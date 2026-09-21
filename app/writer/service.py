"""Writer orchestration: from one offer and the profile to an application folder.

The division of labour is the one the README promises. The model writes prose; code
decides everything else: which form questions are factual (``answers.py``), whether
each generated line is supported by the profile (``grounding.py``), whether the
document fits its target length (``templates_engine.pdf``), and what the ATS
coverage is (``ats.py``). Nothing that can be computed is asked of the model, and a
model failure never leaves a half-written folder: the documents on disk are only
replaced once the whole step succeeded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date

from app.ats import AtsReport, coverage, extract_keywords, format_issues, to_ats_keywords
from app.config import Settings
from app.llm import LLMError
from app.models import (
    Application,
    DraftAnswer,
    DraftLine,
    FormAnswer,
    FormQuestion,
    GroundingReport,
    OfferRecord,
    Profile,
)
from app.offers import store as offers_store
from app.templates_engine import pdf, render
from app.templates_engine import store as templates_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
from app.writer import answers, draft, grounding, markdown, store

# A document that overflows is condensed at most this many times: after that, the
# content has been asked twice and the review screen is a better place to fix it.
MAX_CONDENSE_PASSES = 2


class WriterError(RuntimeError):
    """The application cannot be prepared, for a reason we can explain."""


# How a background job is told what is happening: ``on_step(key, status, detail)``.
# ``service`` only emits the keys; the labels and the job itself live in ``jobs.py``.
OnStep = Callable[[str, str, str], None]


def _report(on_step: OnStep | None, key: str, status: str, detail: str = "") -> None:
    if on_step is not None:
        on_step(key, status, detail)


def _document_detail(document: Document) -> str:
    parts = [f"{document.pages} page(s)"]
    if not document.exact:
        parts.append("estimated")
    if document.grounding.issues:
        parts.append(f"{len(document.grounding.issues)} unverified line(s)")
    return " · ".join(parts)


@dataclass
class Document:
    """One generated document, its layout and its verification."""

    kind: str  # "cv" | "letter"
    lines: list[DraftLine]
    docx: bytes
    grounding: GroundingReport
    pages: int
    exact: bool
    target: int

    @property
    def over_target(self) -> bool:
        return self.pages > self.target


@dataclass
class Prepared:
    """Everything ``prepare`` produced, for the routes and the tests.

    A document is ``None`` when the preparation was not asked to write it (the
    user only checked the other one), which is also how a reader can tell an
    absent letter from an empty one.
    """

    folder: str
    cv: Document | None
    letter: Document | None
    answers: list[FormAnswer]
    ats: AtsReport | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ReviewView:
    """What the review screen shows, read back from the folder."""

    folder: str
    cv_text: str
    letter_text: str
    answers_text: str
    cv_html: str
    letter_html: str
    answers: list[FormAnswer]
    cv_grounding: GroundingReport
    letter_grounding: GroundingReport
    ats: AtsReport | None
    files: list[str]
    # Which documents the folder actually holds: an application only needs the
    # ones the user asked for, and the review screen shows those and no others.
    cv_present: bool = True
    letter_present: bool = True
    answers_present: bool = True
    # Tokens that change when the DOCX is written again, used to cache-bust the
    # framed PDF preview below.
    cv_revision: str = ""
    letter_revision: str = ""

    @property
    def has_documents(self) -> bool:
        return self.cv_present or self.letter_present or self.answers_present


# The sections a preparation can write. A preparation that was not asked for one
# leaves whatever is already on disk alone rather than erasing it.
SECTIONS = ("cv", "letter", "answers")


# --- Rendering and the fit check ----------------------------------------------


def cv_text(lines: list[DraftLine]) -> str:
    """The CV as one text, for the ATS match. Formatting is not a keyword."""
    return "\n".join(line.text for line in lines)


def _render(kind: str, lines: list[DraftLine]) -> bytes:
    return render.render(templates_store.base_document(kind), templates_store.load_blueprint(kind), lines)


def _render_and_fit(kind: str, lines: list[DraftLine], target: int) -> Document:
    docx = _render(kind, lines)
    pages, exact = pdf.page_count(docx, lines)
    return Document(
        kind=kind,
        lines=lines,
        docx=docx,
        grounding=GroundingReport(),
        pages=pages,
        exact=exact,
        target=target,
    )


def condense_instruction(kind: str, pages: int, target: int) -> str:
    return (
        f"The {kind} is currently {pages} page(s) long and must fit in {target}. "
        "Remove the least relevant lines entirely and shorten the wording of the rest. "
        "Keep every citation valid and never add a fact that is not in the profile."
    )


async def _fit(
    *,
    kind: str,
    lines: list[DraftLine],
    target: int,
    redraft: Callable[[str], Awaitable[list[DraftLine]]],
    warnings: list[str],
) -> Document:
    """Render, measure, and condense until the document fits its target length."""
    document = _render_and_fit(kind, lines, target)
    passes = 0
    while document.over_target and passes < MAX_CONDENSE_PASSES:
        instruction = condense_instruction(kind, document.pages, target)
        try:
            lines = await redraft(instruction)
        except LLMError as exc:
            warnings.append(f"Could not shorten the {kind}: {exc}")
            break
        document = _render_and_fit(kind, lines, target)
        passes += 1

    if document.over_target:
        measured = "" if document.exact else " (estimated, without LibreOffice)"
        warnings.append(
            f"The {kind} is {document.pages} page(s){measured}, over the {target}-page "
            "target: trim it in the review screen."
        )
    return document


# --- Preparation ---------------------------------------------------------------


def _offer_source(record: OfferRecord) -> str:
    """The text the offer was analyzed from: the cleaned one when we kept it."""
    source = offers_store.load_source(record.id)
    if source is None:
        return ""
    raw, cleaned = source
    return cleaned.strip() or raw


def form_text(record: OfferRecord, application: Application | None) -> str:
    """The form the preparation modal shows in its box.

    What the user pasted last time when there is something, the questions read
    from the offer's own fragment otherwise, one per line. The box is therefore
    never empty when the offer already carries a form, and the reader can see and
    edit what will be answered instead of typing it again.
    """
    source = (application.form_source if application is not None else "").strip()
    if source:
        return source
    return "\n".join(answers.question_title(question) for question in record.offer.form)


def _present(folder: str, filename: str) -> bool:
    """True when the folder exists and already holds that file."""
    return bool(folder) and store.folder_exists(folder) and store.exists(folder, filename)


def sections_to_write(
    record: OfferRecord,
    folder: str,
    *,
    want_cv: bool,
    want_letter: bool,
    form_changed: bool,
) -> tuple[str, ...]:
    """The sections a preparation still has to write, in their canonical order.

    A document already in the folder is never rewritten: preparing again after new
    form questions arrived must not throw away the CV and the cover letter that are
    already there. The answers are written the first time the form asks anything,
    and rewritten only when that form changed — the Regenerate button is the way to
    redraft them otherwise.
    """
    todo: list[str] = []
    if want_cv and not _present(folder, store.CV_MD):
        todo.append("cv")
    if want_letter and not _present(folder, store.LETTER_MD):
        todo.append("letter")
    if record.offer.form and (form_changed or not _present(folder, store.ANSWERS_MD)):
        todo.append("answers")
    return tuple(todo)


async def prepare(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    *,
    sections: tuple[str, ...] = SECTIONS,
    today: date | None = None,
    on_step: OnStep | None = None,
) -> Prepared:
    """Write the sections of one application that ``sections`` asks for.

    A section the caller did not ask for is left exactly as it is on disk, never
    erased: reopening the modal to answer new form questions must not throw away
    the CV and the cover letter that are already there.

    Raises ``LLMError`` when the model cannot produce a document at all; the
    folder is only written once every requested section exists. ``on_step`` is how
    the background job follows along; it is optional, so the tests can call this
    directly.
    """
    _report(on_step, "offer", "running")
    when = today or tracker_service.today_utc()
    folder = store.resolve_folder(record, when)
    source = _offer_source(record)
    _report(on_step, "offer", "done", folder)

    warnings: list[str] = []
    cv: Document | None = None
    letter: Document | None = None
    report: AtsReport | None = None
    form_answers: list[FormAnswer] | None = None

    if "cv" in sections:
        cv_blueprint = templates_store.load_blueprint("cv")

        async def redraft_cv(instruction: str) -> list[DraftLine]:
            return await draft.tailor_cv(
                settings,
                offer=record.offer,
                profile=profile,
                blueprint=cv_blueprint,
                target_pages=settings.target_pages.cv,
                instruction=instruction,
            )

        _report(on_step, "cv", "running")
        cv_lines = await redraft_cv("")
        cv = await _fit(
            kind="cv",
            lines=cv_lines,
            target=settings.target_pages.cv,
            redraft=redraft_cv,
            warnings=warnings,
        )
        cv.grounding = grounding.check(cv.lines, profile)
        _grounding_warning(cv, warnings)
        _report(on_step, "cv", "done", _document_detail(cv))
    else:
        _report(on_step, "cv", "skipped")

    if "letter" in sections:
        letter_blueprint = templates_store.load_blueprint("letter")

        async def redraft_letter(instruction: str) -> list[DraftLine]:
            return await draft.write_letter(
                settings,
                offer=record.offer,
                profile=profile,
                blueprint=letter_blueprint,
                target_pages=settings.target_pages.letter,
                instruction=instruction,
            )

        _report(on_step, "letter", "running")
        letter_lines = await redraft_letter("")
        letter = await _fit(
            kind="letter",
            lines=letter_lines,
            target=settings.target_pages.letter,
            redraft=redraft_letter,
            warnings=warnings,
        )
        letter.grounding = grounding.check(letter.lines, profile)
        _grounding_warning(letter, warnings)
        _report(on_step, "letter", "done", _document_detail(letter))
    else:
        _report(on_step, "letter", "skipped")

    if "answers" in sections:
        _report(on_step, "answers", "running")
        form_answers = await _draft_answers(
            settings, record, profile, instruction="", warnings=warnings
        )
        _report(on_step, "answers", "done", _answers_detail(form_answers))
    else:
        _report(on_step, "answers", "skipped")

    if cv is not None:
        _report(on_step, "ats", "running")
        report = await _ats_report(settings, record, profile, cv, source)
        _report(on_step, "ats", "done", f"{report.coverage_percent}% coverage")
    else:
        # No keyword report is written without a CV to check: an untouched CV
        # keeps the report it already has.
        _report(on_step, "ats", "skipped")

    _report(on_step, "save", "running")
    _write_application(folder, record, source, cv, letter, form_answers, report)
    tracker_store.set_folder(record.id, folder)
    _report(on_step, "save", "done", folder)

    return Prepared(
        folder=folder,
        cv=cv,
        letter=letter,
        answers=form_answers if form_answers is not None else [],
        ats=report if report is not None else store.load_ats(folder),
        warnings=warnings,
    )


def _answers_detail(form_answers: list[FormAnswer]) -> str:
    from_facts = sum(1 for item in form_answers if item.source == "fact")
    missing = sum(1 for item in form_answers if item.source == "missing")
    parts = [f"{len(form_answers)} question(s)", f"{from_facts} from your profile"]
    if missing:
        parts.append(f"{missing} missing")
    return " · ".join(parts)


def _grounding_warning(document: Document, warnings: list[str]) -> None:
    if document.grounding.ok:
        return
    warnings.append(
        f"The {document.kind.upper()} has {len(document.grounding.issues)} unverified "
        "line(s): they are highlighted below."
    )


def _write_application(
    folder: str,
    record: OfferRecord,
    source: str,
    cv: Document | None,
    letter: Document | None,
    form_answers: list[FormAnswer] | None,
    report: AtsReport | None,
) -> None:
    """Write the folder. A section that is ``None`` was not asked for and is kept."""
    store.save_offer(folder, record, source)
    store.ensure_notes(folder)
    if cv is not None:
        store.write_text(folder, store.CV_MD, markdown.render_lines(cv.lines))
        store.write_bytes(folder, store.CV_DOCX, cv.docx)
    if letter is not None:
        store.write_text(folder, store.LETTER_MD, markdown.render_lines(letter.lines))
        store.write_bytes(folder, store.LETTER_DOCX, letter.docx)
    if form_answers is not None:
        store.write_text(folder, store.ANSWERS_MD, markdown.render_answers(form_answers))
    if report is not None:
        store.save_ats(folder, report)


# --- Form answers --------------------------------------------------------------


async def _draft_answers(
    settings: Settings,
    record: OfferRecord,
    profile: Profile,
    *,
    instruction: str,
    warnings: list[str],
    folder: str = "",
    from_current: bool = False,
) -> list[FormAnswer]:
    questions = record.offer.form
    slots = answers.resolve(questions, profile)
    open_list = [
        (answers.question_title(question), question.max_length)
        for question, slot in zip(questions, slots, strict=True)
        if slot is None
    ]
    current = (
        _current_answers(folder, record, profile, questions, slots)
        if from_current and folder
        else ""
    )

    generated: list[DraftAnswer] = []
    if open_list:
        try:
            generated = await draft.answer_questions(
                settings,
                offer=record.offer,
                profile=profile,
                questions=open_list,
                instruction=instruction,
                current=current,
            )
        except LLMError as exc:
            warnings.append(f"Could not draft the open form questions: {exc}")

    merged = answers.merge(questions, slots, generated)
    missing = [item.question for item in merged if item.source == "missing"]
    if missing:
        warnings.append(
            f"{len(missing)} factual answer(s) are missing from your profile: "
            + "; ".join(missing)
            + "."
        )
    return merged


def _current_answers(
    folder: str,
    record: OfferRecord,
    profile: Profile,
    questions: list[FormQuestion],
    slots: list[FormAnswer | None],
) -> str:
    """The stored answers to the open questions, as the user last left them.

    Only the open ones: a factual answer is read from the profile, and never goes to
    the model, so it has no business in the request either.
    """
    stored = _load_answers(folder, record, profile)
    blocks: list[str] = []
    for index, question in enumerate(questions):
        if slots[index] is not None or index >= len(stored):
            continue
        text = stored[index].answer.strip()
        if text:
            blocks.append(f"### {answers.question_title(question)}\n{text}")
    return "\n\n".join(blocks)


def _load_answers(folder: str, record: OfferRecord, profile: Profile) -> list[FormAnswer]:
    """Rebuild the answers for display, keeping the user's edits."""
    stored = markdown.parse_answers(store.read_text(folder, store.ANSWERS_MD) or "")
    return answers.apply(record.offer.form, profile, stored)


# --- ATS -----------------------------------------------------------------------


async def _ats_report(
    settings: Settings, record: OfferRecord, profile: Profile, cv: Document, source: str
) -> AtsReport:
    keywords = await extract_keywords(settings, record.offer, source)
    return _coverage(record, profile, cv, keywords)


def _coverage(
    record: OfferRecord, profile: Profile, cv: Document, keywords: list
) -> AtsReport:
    report = coverage(cv_text(cv.lines), keywords, profile)
    return report.model_copy(update={"format_issues": format_issues(cv.docx)})


def _rewrite_ats(profile: Profile, record: OfferRecord, folder: str) -> None:
    """Refresh ``ats.json`` from the folder as it stands now. No model call.

    The offer's own keywords are reused exactly as ``extract_keywords`` would: the
    report only has to be recomputed because the CV text or its layout changed.
    """
    cv_lines = markdown.parse_lines(store.read_text(folder, store.CV_MD) or "")
    cv_docx = store.read_bytes(folder, store.CV_DOCX)
    if cv_docx is None:  # pragma: no cover - a folder without a DOCX is hand-made
        cv_docx = render.render(
            templates_store.base_document("cv"),
            templates_store.load_blueprint("cv"),
            cv_lines,
        )
        store.write_bytes(folder, store.CV_DOCX, cv_docx)
    document = Document(
        kind="cv",
        lines=cv_lines,
        docx=cv_docx,
        grounding=GroundingReport(),
        pages=0,
        exact=True,
        target=0,
    )
    report = _coverage(record, profile, document, to_ats_keywords(record.offer.keywords))
    store.save_ats(folder, report)


# --- Review, save and regenerate ----------------------------------------------


def load_review(profile: Profile, record: OfferRecord, folder: str) -> ReviewView:
    """Read a prepared folder back for the review screen."""
    files = store.written_files(folder)
    cv_source = store.read_text(folder, store.CV_MD) or ""
    letter_source = store.read_text(folder, store.LETTER_MD) or ""
    answers_source = store.read_text(folder, store.ANSWERS_MD) or ""

    return ReviewView(
        folder=folder,
        cv_text=cv_source,
        letter_text=letter_source,
        answers_text=answers_source,
        cv_html=markdown.to_html(cv_source),
        letter_html=markdown.to_html(letter_source),
        answers=_load_answers(folder, record, profile),
        cv_grounding=grounding.check(markdown.parse_lines(cv_source), profile),
        letter_grounding=grounding.check(markdown.parse_lines(letter_source), profile),
        ats=store.load_ats(folder),
        files=files,
        cv_present=store.CV_MD in files,
        letter_present=store.LETTER_MD in files,
        answers_present=store.ANSWERS_MD in files,
        cv_revision=store.revision(folder, store.CV_DOCX),
        letter_revision=store.revision(folder, store.LETTER_DOCX),
    )


@dataclass
class Saved:
    """What a save rewrote, so the route can say so.

    A section is only touched when its text was posted: each section has its own
    Save button, and re-rendering a document the user did not edit would only risk
    changing it.
    """

    sections: list[str] = field(default_factory=list)
    cv: Document | None = None
    letter: Document | None = None
    answers: list[FormAnswer] | None = None
    ats: AtsReport | None = None


def save(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    *,
    folder: str,
    cv_source: str | None = None,
    letter_source: str | None = None,
    answers_source: str | None = None,
) -> Saved:
    """Save what the user edited, and recompute everything that is code.

    No model call: re-parsing the text, re-rendering the DOCX, re-checking the
    grounding and the ATS coverage are all deterministic, so saving is instant and
    an edited document never needs the provider to be reachable.
    """
    if not store.folder_exists(folder):
        raise WriterError("This application folder no longer exists.")

    saved = Saved()
    if cv_source is not None:
        cv = _render_and_fit("cv", markdown.parse_lines(cv_source), settings.target_pages.cv)
        cv.grounding = grounding.check(cv.lines, profile)
        store.write_text(folder, store.CV_MD, markdown.render_lines(cv.lines))
        store.write_bytes(folder, store.CV_DOCX, cv.docx)
        report = _coverage(record, profile, cv, to_ats_keywords(record.offer.keywords))
        store.save_ats(folder, report)
        saved.cv, saved.ats, saved.sections = cv, report, [*saved.sections, "CV"]

    if letter_source is not None:
        letter = _render_and_fit(
            "letter", markdown.parse_lines(letter_source), settings.target_pages.letter
        )
        letter.grounding = grounding.check(letter.lines, profile)
        store.write_text(folder, store.LETTER_MD, markdown.render_lines(letter.lines))
        store.write_bytes(folder, store.LETTER_DOCX, letter.docx)
        saved.letter, saved.sections = letter, [*saved.sections, "letter"]

    if answers_source is not None:
        form_answers = answers.apply(
            record.offer.form, profile, markdown.parse_answers(answers_source)
        )
        store.write_text(folder, store.ANSWERS_MD, markdown.render_answers(form_answers))
        saved.answers, saved.sections = form_answers, [*saved.sections, "form answers"]

    if not saved.sections:
        raise WriterError("Nothing to save.")
    return saved


SECTION_LABELS = {"cv": "CV", "letter": "Letter", "answers": "Form answers"}


async def regenerate(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    *,
    folder: str,
    section: str,
    instruction: str,
    warnings: list[str],
    from_current: bool = False,
    on_step: OnStep | None = None,
) -> None:
    """Redraft one section with an instruction, leaving the others untouched.

    ``from_current`` hands the section as it stands on disk back to the model, so it
    improves what is there instead of writing it again: the stored file carries the
    user's own edits, which is exactly the version to build on.
    """
    if section not in SECTIONS:
        raise WriterError(f"Unknown section: {section!r}")
    if not store.folder_exists(folder):
        raise WriterError("This application folder no longer exists.")

    _report(on_step, "draft", "running")
    detail = ""
    if section == "cv":
        document = await _regenerate_document(
            settings, profile, record, folder, "cv", instruction, warnings, from_current
        )
        detail = _document_detail(document)
    elif section == "letter":
        document = await _regenerate_document(
            settings, profile, record, folder, "letter", instruction, warnings, from_current
        )
        detail = _document_detail(document)
    else:
        merged = await _draft_answers(
            settings,
            record,
            profile,
            instruction=instruction,
            warnings=warnings,
            folder=folder,
            from_current=from_current,
        )
        store.write_text(folder, store.ANSWERS_MD, markdown.render_answers(merged))
        detail = _answers_detail(merged)
    _report(on_step, "draft", "done", detail)

    _report(on_step, "ats", "running")
    _rewrite_ats(profile, record, folder)
    report = store.load_ats(folder)
    _report(
        on_step,
        "ats",
        "done",
        f"{report.coverage_percent}% coverage" if report is not None else "",
    )


async def _regenerate_document(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    folder: str,
    kind: str,
    instruction: str,
    warnings: list[str],
    from_current: bool = False,
) -> Document:
    blueprint = templates_store.load_blueprint(kind)
    target = settings.target_pages.cv if kind == "cv" else settings.target_pages.letter

    source = store.CV_MD if kind == "cv" else store.LETTER_MD
    base = markdown.source_only(store.read_text(folder, source) or "") if from_current else ""

    async def redraft(next_instruction: str) -> list[DraftLine]:
        # The base follows the drafts: a condense pass must shorten the version it
        # just produced, not the one that was on disk.
        nonlocal base
        if kind == "cv":
            lines = await draft.tailor_cv(
                settings,
                offer=record.offer,
                profile=profile,
                blueprint=blueprint,
                target_pages=target,
                instruction=next_instruction,
                current=base,
            )
        else:
            lines = await draft.write_letter(
                settings,
                offer=record.offer,
                profile=profile,
                blueprint=blueprint,
                target_pages=target,
                instruction=next_instruction,
                current=base,
            )
        base = markdown.source_only(markdown.render_lines(lines))
        return lines

    lines = await redraft(instruction)
    document = await _fit(
        kind=kind, lines=lines, target=target, redraft=redraft, warnings=warnings
    )
    document.grounding = grounding.check(document.lines, profile)
    _grounding_warning(document, warnings)

    if kind == "cv":
        store.write_text(folder, store.CV_MD, markdown.render_lines(document.lines))
        store.write_bytes(folder, store.CV_DOCX, document.docx)
    else:
        store.write_text(folder, store.LETTER_MD, markdown.render_lines(document.lines))
        store.write_bytes(folder, store.LETTER_DOCX, document.docx)
    return document
