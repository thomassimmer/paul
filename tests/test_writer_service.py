"""Tests for the writer orchestration: prepare, save and regenerate."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.config import Settings
from app.models import (
    Application,
    DraftAnswer,
    DraftLine,
    Experience,
    Facts,
    FormQuestion,
    Identity,
    Keyword,
    OfferDraft,
    Profile,
)
from app.offers import store as offers_store
from app.writer import service, store

MODEL = "openai/gpt-4o"


def _profile(*, facts: Facts | None = None) -> Profile:
    return Profile(
        identity=Identity(name="Camille Moreau", email="camille@example.com"),
        facts=facts if facts is not None else Facts(notice_period="One month"),
        experiences=[
            Experience(
                id="exp-acme-2022",
                company="Acme",
                title="Lead Backend Engineer",
                stack=["Rust"],
                highlights=["Cut ingestion latency by 60%"],
            )
        ],
    )


def _form() -> list[FormQuestion]:
    return [
        FormQuestion(label="What is your notice period?", name="notice", max_length=200),
        FormQuestion(label="Why us?", name="why", max_length=400),
    ]


def _seed(form: list[FormQuestion] | None = None):
    offer = OfferDraft(
        title="Senior Backend Engineer",
        company="Acme",
        language="en",
        keywords=[Keyword(term="Rust")],
    ).to_offer(_form() if form is None else form)
    return offers_store.save_offer(offer, raw="<p>raw offer</p>", cleaned="A Rust role", source="html")


def _cv_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="contact", text="camille@example.com"),
        DraftLine(role="section_title", text="Experience"),
        DraftLine(role="entry_title", text="Lead Backend Engineer — Acme"),
        DraftLine(role="entry_dates", text="2022 / 2024"),
        DraftLine(
            role="bullet",
            text="Cut ingestion latency by 60%",
            source_ids=["exp-acme-2022"],
        ),
        DraftLine(role="section_title", text="Skills"),
        DraftLine(role="skill_line", text="Rust"),
    ]


def _letter_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="date", text="19 September 2026"),
        DraftLine(role="salutation", text="Dear hiring team,"),
        DraftLine(
            role="body_text",
            text="I cut ingestion latency by 60%.",
            source_ids=["exp-acme-2022"],
        ),
        DraftLine(role="closing", text="Sincerely,"),
    ]


def _pages(*values: int):
    """A ``count_pages`` that returns the given counts in order, then the last."""
    queue = list(values)

    def fake(docx_bytes, *, timeout=120.0):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return fake


def _patch(monkeypatch, *, cv=None, letter=None, answers=None, pages: int | list[int] = 1):
    calls: dict = {
        "cv": [],
        "letter": [],
        "answers": [],
        "base": {"cv": [], "letter": [], "answers": []},
    }
    cv_fn = cv or (lambda instruction, index: _cv_lines())
    letter_fn = letter or (lambda instruction, index: _letter_lines())

    async def tailor_cv(
        settings, *, offer, profile, blueprint, target_pages, instruction="", current=""
    ):
        calls["cv"].append(instruction)
        calls["base"]["cv"].append(current)
        return cv_fn(instruction, len(calls["cv"]))

    async def write_letter(
        settings, *, offer, profile, blueprint, target_pages, instruction="", current=""
    ):
        calls["letter"].append(instruction)
        calls["base"]["letter"].append(current)
        return letter_fn(instruction, len(calls["letter"]))

    async def answer_questions(
        settings, *, offer, profile, questions, instruction="", current=""
    ):
        calls["answers"].append((instruction, list(questions)))
        calls["base"]["answers"].append(current)
        return answers(questions) if answers else []

    monkeypatch.setattr(service.draft, "tailor_cv", tailor_cv)
    monkeypatch.setattr(service.draft, "write_letter", write_letter)
    monkeypatch.setattr(service.draft, "answer_questions", answer_questions)
    fake_pages = _pages(*pages) if isinstance(pages, (list, tuple)) else _pages(pages)
    monkeypatch.setattr(service.pdf, "count_pages", fake_pages)
    return calls


def _settings() -> Settings:
    return Settings(model=MODEL)


def _prepare(record, profile=None, *, monkeypatch=None, **kwargs):
    return asyncio.run(
        service.prepare(_settings(), profile or _profile(), record, **kwargs)
    )


# --- preparing -----------------------------------------------------------------


def test_prepare_writes_the_whole_folder(monkeypatch):
    record = _seed()
    _patch(monkeypatch)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert prepared.warnings == []
    assert prepared.folder.endswith("acme-senior-backend-engineer")
    for name in (
        store.OFFER_JSON,
        store.OFFER_HTML,
        store.CV_MD,
        store.CV_DOCX,
        store.LETTER_MD,
        store.LETTER_DOCX,
        store.ANSWERS_MD,
        store.ATS_JSON,
        store.NOTES_MD,
    ):
        assert store.exists(prepared.folder, name), name

    assert (store.read_text(prepared.folder, store.CV_MD) or "").startswith("<!--")
    assert "[name] Camille Moreau" in (store.read_text(prepared.folder, store.CV_MD) or "")
    stored = store.load_offer(prepared.folder)
    assert stored is not None and stored.id == record.id


def test_prepare_records_the_folder_on_the_application(monkeypatch):
    from app.tracker import store as tracker_store

    record = _seed()
    _patch(monkeypatch)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    application = tracker_store.load_application(record.id)
    assert application is not None
    assert application.folder == prepared.folder


def test_prepare_computes_the_ats_coverage_from_the_offer_keywords(monkeypatch):
    record = _seed()
    _patch(monkeypatch)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert prepared.ats is not None
    assert prepared.ats.coverage_percent == 100  # the only keyword is Rust, present
    assert prepared.ats.keywords[0].present is True
    stored = store.load_ats(prepared.folder)
    assert stored is not None and stored.coverage_percent == 100


def test_prepare_separates_facts_from_drafted_answers(monkeypatch):
    record = _seed()
    _patch(monkeypatch, answers=lambda questions: [DraftAnswer(question="Why us?", answer="Because.")])

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert [answer.source for answer in prepared.answers] == ["fact", "generated"]
    assert prepared.answers[0].answer == "One month"
    assert prepared.answers[1].answer == "Because."
    answers_md = store.read_text(prepared.folder, store.ANSWERS_MD) or ""
    assert "One month" in answers_md
    assert "Because." in answers_md


def test_only_the_open_questions_are_sent_to_the_model(monkeypatch):
    record = _seed()
    calls = _patch(monkeypatch)

    _prepare(record, monkeypatch=monkeypatch)

    assert calls["answers"] == [("", [("Why us?", 400)])]


def test_prepare_warns_about_a_missing_fact(monkeypatch):
    record = _seed()
    _patch(monkeypatch)

    prepared = _prepare(record, profile=_profile(facts=Facts()), monkeypatch=monkeypatch)

    assert any("missing from your profile" in warning for warning in prepared.warnings)
    assert prepared.answers[0].source == "missing"


def test_prepare_without_a_form_leaves_the_answers_empty(monkeypatch):
    record = _seed(form=[])
    calls = _patch(monkeypatch)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert prepared.answers == []
    assert store.read_text(prepared.folder, store.ANSWERS_MD) == ""
    assert calls["answers"] == []


# --- what a preparation is asked to write --------------------------------------

TODAY = date(2026, 9, 19)


def test_sections_to_write_asks_for_everything_the_first_time():
    record = _seed()

    assert service.sections_to_write(
        record, "", want_cv=True, want_letter=True, form_changed=False
    ) == ("cv", "letter", "answers")


def test_sections_to_write_leaves_out_the_documents_not_asked_for():
    record = _seed()

    assert service.sections_to_write(
        record, "", want_cv=True, want_letter=False, form_changed=False
    ) == ("cv", "answers")


def test_sections_to_write_keeps_the_documents_already_written():
    record = _seed()
    folder = store.resolve_folder(record, TODAY)
    store.write_text(folder, store.CV_MD, "[name] Camille Moreau\n")

    assert service.sections_to_write(
        record, folder, want_cv=True, want_letter=True, form_changed=False
    ) == ("letter", "answers")


def test_sections_to_write_only_rewrites_the_answers_when_the_form_changed():
    record = _seed()
    folder = store.resolve_folder(record, TODAY)
    for name in (store.CV_MD, store.LETTER_MD, store.ANSWERS_MD):
        store.write_text(folder, name, "x")

    assert service.sections_to_write(
        record, folder, want_cv=True, want_letter=True, form_changed=False
    ) == ()
    assert service.sections_to_write(
        record, folder, want_cv=True, want_letter=True, form_changed=True
    ) == ("answers",)


def test_sections_to_write_ignores_the_answers_without_a_form():
    record = _seed(form=[])

    assert service.sections_to_write(
        record, "", want_cv=True, want_letter=True, form_changed=True
    ) == ("cv", "letter")


def test_form_text_shows_the_pasted_form_when_there_is_one():
    record = _seed()
    application = Application(offer_id=record.id, form_source="<form><input name='x'></form>")

    assert service.form_text(record, application) == "<form><input name='x'></form>"


def test_form_text_falls_back_to_the_form_read_from_the_offer():
    record = _seed()

    text = service.form_text(record, None)

    assert "What is your notice period?" in text
    assert "Why us?" in text


def test_prepare_writes_only_the_sections_it_was_asked_for(monkeypatch):
    record = _seed()
    _patch(monkeypatch)

    prepared = _prepare(record, monkeypatch=monkeypatch, sections=("cv",))

    assert store.exists(prepared.folder, store.CV_MD)
    assert not store.exists(prepared.folder, store.LETTER_MD)
    assert not store.exists(prepared.folder, store.ANSWERS_MD)
    assert prepared.letter is None
    assert prepared.answers == []
    assert prepared.ats is not None  # computed from the CV that was written


def test_prepare_does_not_rewrite_the_documents_it_was_not_asked_for(monkeypatch):
    record = _seed()
    _patch(monkeypatch)
    first = _prepare(record, monkeypatch=monkeypatch, sections=("cv", "letter"))
    cv_before = store.read_bytes(first.folder, store.CV_DOCX)

    calls = _patch(monkeypatch)  # fresh recorders
    second = _prepare(record, monkeypatch=monkeypatch, sections=("answers",))

    assert second.folder == first.folder
    assert store.read_bytes(second.folder, store.CV_DOCX) == cv_before
    assert calls["cv"] == []
    assert calls["letter"] == []
    assert calls["answers"]  # the answers were drafted
    assert store.exists(second.folder, store.ANSWERS_MD)


def test_a_section_that_is_not_written_is_reported_as_skipped(monkeypatch):
    record = _seed()
    _patch(monkeypatch)
    events: list[tuple[str, str]] = []

    asyncio.run(
        service.prepare(
            _settings(),
            _profile(),
            record,
            sections=("cv",),
            on_step=lambda key, status, detail="": events.append((key, status)),
        )
    )

    assert ("cv", "done") in events
    assert ("letter", "skipped") in events
    assert ("answers", "skipped") in events
    assert ("ats", "done") in events


# --- the fit check -------------------------------------------------------------


def test_an_overflowing_document_is_condensed_once(monkeypatch):
    record = _seed()

    def cv(instruction, index):
        if index == 1:
            return _cv_lines()
        return _cv_lines()[:1]  # the condense pass keeps only the identity

    calls = _patch(monkeypatch, cv=cv, pages=[5, 1, 1])

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert calls["cv"][0] == ""
    assert "must fit in" in calls["cv"][1]
    assert "[contact]" not in (store.read_text(prepared.folder, store.CV_MD) or "")
    assert not any("over the" in warning for warning in prepared.warnings)


def test_a_document_that_stays_too_long_is_reported(monkeypatch):
    record = _seed()
    calls = _patch(monkeypatch, pages=5)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    # The initial draft, then two condense attempts, and then we stop.
    assert len(calls["cv"]) == 3
    assert any("over the 2-page target" in warning for warning in prepared.warnings)


def test_ungrounded_lines_are_reported(monkeypatch):
    record = _seed()

    def cv(instruction, index):
        return [*_cv_lines(), DraftLine(role="bullet", text="Invented a platform")]

    _patch(monkeypatch, cv=cv, pages=1)

    prepared = _prepare(record, monkeypatch=monkeypatch)

    assert any("unverified" in warning for warning in prepared.warnings)
    assert prepared.cv is not None
    assert prepared.cv.grounding.issues


# --- saving --------------------------------------------------------------------


def _prepared(monkeypatch):
    record = _seed()
    _patch(monkeypatch, answers=lambda questions: [DraftAnswer(question="Why us?", answer="Because.")])
    return record, _prepare(record, monkeypatch=monkeypatch)


def test_save_only_touches_the_posted_section(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    letter_before = store.read_text(prepared.folder, store.LETTER_MD)

    saved = service.save(
        _settings(),
        _profile(),
        record,
        folder=prepared.folder,
        cv_source="[name] Camille Moreau\n[bullet] Cut latency by 60% {exp-acme-2022}\n",
    )

    assert saved.sections == ["CV"]
    assert "[section_title]" not in (store.read_text(prepared.folder, store.CV_MD) or "")
    assert store.read_text(prepared.folder, store.LETTER_MD) == letter_before
    assert saved.ats is not None


def test_save_recomputes_the_grounding(monkeypatch):
    record, prepared = _prepared(monkeypatch)

    saved = service.save(
        _settings(),
        _profile(),
        record,
        folder=prepared.folder,
        cv_source="[name] Camille Moreau\n[bullet] Led the platform team\n",
    )

    assert saved.cv is not None
    assert not saved.cv.grounding.ok


def test_save_without_a_folder_is_refused():
    record = _seed()
    with pytest.raises(service.WriterError):
        service.save(_settings(), _profile(), record, folder="nope", cv_source="[name] X")


def test_save_with_nothing_posted_is_refused(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    with pytest.raises(service.WriterError):
        service.save(_settings(), _profile(), record, folder=prepared.folder)


# --- regenerating --------------------------------------------------------------


def test_regenerate_redrafts_with_the_instruction(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    calls = _patch(monkeypatch, pages=1)  # a fresh set of call recorders

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="cv",
            instruction="focus on Rust",
            warnings=[],
        )
    )

    assert calls["cv"] == ["focus on Rust"]
    assert calls["letter"] == []


def test_regenerate_without_the_flag_rewrites_from_scratch(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    calls = _patch(monkeypatch, pages=1)

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="cv",
            instruction="focus on Rust",
            warnings=[],
        )
    )

    assert calls["base"]["cv"] == [""]


def test_regenerate_from_the_current_version_hands_back_the_stored_document(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    calls = _patch(monkeypatch, pages=1)

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="cv",
            instruction="focus on Rust",
            warnings=[],
            from_current=True,
        )
    )

    base = calls["base"]["cv"][0]
    assert "[name] Camille Moreau" in base
    assert "{exp-acme-2022}" in base  # the citations travel with the lines
    assert "<!--" not in base  # the header documents the format, it is not content


def test_regenerate_answers_from_the_current_version_hands_back_the_open_answers(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    calls = _patch(monkeypatch, pages=1)

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="answers",
            instruction="shorter",
            warnings=[],
            from_current=True,
        )
    )

    base = calls["base"]["answers"][0]
    assert "Why us?" in base
    assert "Because." in base
    # A factual answer is never sent to the model.
    assert "One month" not in base


def test_the_condense_pass_builds_on_the_draft_it_just_produced(monkeypatch):
    record, prepared = _prepared(monkeypatch)

    def cv(instruction, index):
        return _cv_lines() if index == 1 else _cv_lines()[:1]

    # Two condense passes, so the third call is told what the second one produced.
    calls = _patch(monkeypatch, cv=cv, pages=[5, 5, 1])

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="cv",
            instruction="focus on Rust",
            warnings=[],
            from_current=True,
        )
    )

    assert len(calls["base"]["cv"]) == 3
    assert "[contact]" in calls["base"]["cv"][0]  # the stored document
    assert "[contact]" in calls["base"]["cv"][1]  # the draft the first call returned
    # The last condense pass builds on the shortened draft, not on what was on disk.
    assert "[contact]" not in calls["base"]["cv"][2]
    assert "[name] Camille Moreau" in calls["base"]["cv"][2]


def test_regenerate_answers_rewrites_only_the_answers(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    letter_before = store.read_text(prepared.folder, store.LETTER_MD)

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="answers",
            instruction="shorter",
            warnings=[],
        )
    )

    assert store.read_text(prepared.folder, store.LETTER_MD) == letter_before


def test_regenerate_refuses_an_unknown_section(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    with pytest.raises(service.WriterError):
        asyncio.run(
            service.regenerate(
                _settings(),
                _profile(),
                record,
                folder=prepared.folder,
                section="poster",
                instruction="",
                warnings=[],
            )
        )


# --- the review view and the list ---------------------------------------------


def test_load_review_reads_the_documents_back(monkeypatch):
    record, prepared = _prepared(monkeypatch)

    view = service.load_review(_profile(), record, prepared.folder)

    assert "[name] Camille Moreau" in view.cv_text
    assert "Camille Moreau" in view.cv_html
    assert view.cv_grounding.ok
    assert view.ats is not None
    assert store.CV_MD in view.files
    assert [answer.source for answer in view.answers] == ["fact", "generated"]


# --- progress ------------------------------------------------------------------


def test_prepare_reports_each_step(monkeypatch):
    record = _seed()
    _patch(monkeypatch)
    events: list[tuple[str, str]] = []

    asyncio.run(
        service.prepare(
            _settings(),
            _profile(),
            record,
            on_step=lambda key, status, detail="": events.append((key, status)),
        )
    )

    running = [key for key, status in events if status == "running"]
    done = [key for key, status in events if status == "done"]
    assert running == ["offer", "cv", "letter", "answers", "ats", "save"]
    assert done == running


def test_the_steps_carry_a_detail(monkeypatch):
    record = _seed()
    _patch(monkeypatch)
    details: dict[str, str] = {}

    asyncio.run(
        service.prepare(
            _settings(),
            _profile(),
            record,
            on_step=lambda key, status, detail="": details.update(
                {key: detail} if status == "done" else {}
            ),
        )
    )

    assert details["cv"] == "1 page(s)"
    assert details["answers"] == "2 question(s) · 1 from your profile"
    assert details["ats"].endswith("% coverage")


def test_regenerate_reports_its_steps(monkeypatch):
    record, prepared = _prepared(monkeypatch)
    events: list[tuple[str, str]] = []

    asyncio.run(
        service.regenerate(
            _settings(),
            _profile(),
            record,
            folder=prepared.folder,
            section="cv",
            instruction="shorter",
            warnings=[],
            on_step=lambda key, status, detail="": events.append((key, status)),
        )
    )

    running = [key for key, status in events if status == "running"]
    assert running == ["draft", "ats"]
