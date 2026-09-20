"""The interview: the model asks a question, you answer, the profile grows.

Which question comes next is not a script. The model reads the profile as it
stands and asks the one thing it needs: what a job was really about, what a result
amounted to, whether it understood a point correctly, and whether an experience the
CV forgets exists at all. The candidate answers for as long as they like and comes
back later — nothing about the interview is stored except the questions already
put, so reopening it starts from the profile as it is now and carries on.

The model writes the answers too, but it is never allowed to invent. Every change
it proposes quotes the answer it came from, and this module checks that quote — and
every number in the value — against the answer before anything is written. A
proposal that fails is dropped and reported, never stored.

What an answer does to a field depends on the field, and the code decides:

- a list (``highlights``, ``stack``, a preference) gains the items it lacked;
- the free text of an experience (``context``, ``team_size``) gains a line;
- a fact or a preference holds one value, so it is *corrected* rather than
  accumulated: "1 month" then "2 months" means two months.

Nothing is dropped. The one thing an answer may overwrite is a highlight the model
named as superseded (``replaces``): merging a detail into the line it belongs to is
what keeps a profile from saying the same thing twice, and the page shows the
rewrite rather than hiding it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import yaml
from pydantic import BaseModel, Field

from app.config import Settings
from app.llm import complete_structured
from app.models import Experience, Facts, Preferences, Profile
from app.profiler.ids import assign_ids, slugify
from app.profiler.text import split_list
from app.prompts import load_prompt

# The fields an answer may write, per target. Kept here as well as in the prompt:
# a target the profile does not have can then never be written, whatever the model
# answers. The fact and preference fields are read from their models, so adding one
# is enough to let the interview ask about it.
EXPERIENCE_FIELDS = ("highlights", "context", "team_size", "stack")
NEW_EXPERIENCE_FIELDS = ("company", "title", "period", *EXPERIENCE_FIELDS)
FACT_FIELDS = tuple(Facts.model_fields)
PREFERENCE_FIELDS = tuple(Preferences.model_fields)

# The identity of an experience the CV is missing: one value each, written once.
_IDENTITY_FIELDS = ("company", "title", "period")

# The lists where a comma separates values — unlike ``highlights``, where a comma
# is part of the sentence ("Cut latency, from 900ms to 120ms").
COMMA_LISTS = (
    "stack",
    "languages",
    "target_roles",
    "locations",
    "contract_types",
    "more_of",
    "less_of",
)

# Spellings a model falls back to for the field of a target. Repairing them in code
# rather than paying for a retry is what ``writer/draft.py`` does with document
# roles, and for the same reason: a spelling must not cost the candidate the
# sentence they just gave.
FIELD_ALIASES = {
    "highlight": "highlights",
    "achievement": "highlights",
    "achievements": "highlights",
    "accomplishment": "highlights",
    "accomplishments": "highlights",
    "result": "highlights",
    "results": "highlights",
    "description": "highlights",
    "responsibilities": "highlights",
    "team": "team_size",
    "teamsize": "team_size",
    "tech": "stack",
    "technology": "stack",
    "technologies": "stack",
    "tools": "stack",
    "dates": "period",
    "when": "period",
    "role": "title",
    "job_title": "title",
    "employer": "company",
}

FIELD_LABELS = {
    "highlights": "what you did",
    "context": "the context",
    "team_size": "the team",
    "stack": "the stack",
    "company": "the company",
    "title": "the role",
    "period": "the dates",
    "more_of": "more of",
    "less_of": "less of",
    "target_roles": "target roles",
    "contract_types": "contract types",
    "remote": "remote",
    "locations": "locations",
    "work_authorization": "work authorization",
    "work_permit_expiry": "work permit expiry",
    "notice_period": "notice period",
    "salary_expectation": "salary expectation",
    "relocation": "relocation",
    "languages": "languages",
}

_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


# --- what the model returns ----------------------------------------------------


class DraftEdit(BaseModel):
    """One change the model proposes, with the answer excerpt it came from."""

    target: str = ""  # "exp:<id>", "exp:new", "facts", "prefs"
    field: str = ""  # the field inside that target
    value: str = ""  # what to add (or, for a fact, its new value)
    source: str = ""  # a verbatim excerpt of the answer, verified by code
    # The existing highlight this one supersedes, quoted from the profile. Named,
    # the line is merged into rather than gaining a near-duplicate next to it.
    replaces: str = ""


class DraftQuestion(BaseModel):
    """The question to show next, and why it is worth answering."""

    prompt: str = ""
    topic: str = ""
    hint: str = ""
    why: str = ""


class Turn(BaseModel):
    """One exchange: what to write, and the question that follows it."""

    edits: list[DraftEdit] = Field(default_factory=list)
    question: DraftQuestion = Field(default_factory=DraftQuestion)
    # The model sets this when nothing worth asking is left in the profile.
    finished: bool = False

    @property
    def has_question(self) -> bool:
        return bool(self.question.prompt.strip())

    def asked(self) -> str:
        """The identity of the question, for the list of what has been asked."""
        return " ".join(self.question.prompt.split())


# --- text helpers --------------------------------------------------------------


def _fold(text: str) -> str:
    """Case- and accent-insensitive, whitespace collapsed."""
    decomposed = unicodedata.normalize("NFD", text or "")
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", without_accents.casefold()).strip()


def _compact(text: str) -> str:
    return _fold(text).replace(" ", "")


def _canonical(text: str) -> str:
    """Compact, with a decimal comma normalised, for comparing numbers."""
    return _compact(text).replace(",", ".")


def _quotes(source: str, answer: str) -> bool:
    """True when the excerpt really appears in the answer."""
    return bool(source.strip()) and _fold(source) in _fold(answer)


def _numbers_present(value: str, answer: str) -> bool:
    """True when every number the value states is one the answer gave."""
    canonical = _canonical(answer)
    return all(number.replace(",", ".") in canonical for number in _NUMBER.findall(value or ""))


def _experience_label(experience: Experience) -> str:
    parts = [part.strip() for part in (experience.company, experience.title) if part.strip()]
    return " — ".join(parts) if parts else "a new experience"


def _find(profile: Profile, experience_id: str) -> Experience | None:
    for experience in profile.experiences:
        if experience.id == experience_id:
            return experience
    return None


# --- the guard -----------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """One change that survived the guard, ready to be written."""

    target: str
    field: str
    value: str
    source: str = ""
    replaces: str = ""


def _normalize_field(field_name: str, allowed: tuple[str, ...]) -> str:
    """The allowed field a model named, spelling aside, or "" when it names none."""
    cleaned = (field_name or "").strip().casefold().replace(" ", "_").replace("-", "_")
    if cleaned in allowed:
        return cleaned
    alias = FIELD_ALIASES.get(cleaned, "")
    return alias if alias in allowed else ""


def _find_experience_id(profile: Profile, target: str) -> str | None:
    """The id of the experience a target names, however loosely it is spelled."""
    name = target.strip()
    if name.casefold().startswith("exp:"):
        name = name[4:].strip()
    if not name:
        return None
    for experience in profile.experiences:
        if experience.id == name:
            return experience.id
    folded = _fold(name)
    matches = {
        experience.id
        for experience in profile.experiences
        if folded
        in {
            _fold(experience.id),
            _fold(experience.id.removeprefix("exp-")),
            _fold(experience.company),
            _fold(slugify(experience.company)),
        }
    }
    # One match or none: an ambiguous target would quietly write to the wrong job.
    return matches.pop() if len(matches) == 1 else None


def _resolve(profile: Profile, target: str, field_name: str) -> tuple[str, str]:
    """The place a change goes, as the code names it; ``("", "")`` when there is none.

    Models spell a target loosely — the ``exp-`` prefix dropped, the company used
    instead of the id, ``achievements`` for the field — and a spelling should not
    cost the candidate the sentence they just gave. An ambiguous target still
    resolves to nothing rather than to the wrong experience.
    """
    cleaned = (target or "").strip().casefold()
    if cleaned in ("facts", "prefs"):
        allowed = FACT_FIELDS if cleaned == "facts" else PREFERENCE_FIELDS
        field_name = _normalize_field(field_name, allowed)
        return (cleaned, field_name) if field_name else ("", "")

    if cleaned in ("exp:new", "new", "new_experience", "exp:new_experience"):
        return "exp:new", _normalize_field(field_name, NEW_EXPERIENCE_FIELDS) or "highlights"

    experience_id = _find_experience_id(profile, target)
    if experience_id is None:
        return "", ""
    # An experience accumulates highlights, so a missing or unrecognised field
    # writes one there rather than losing the sentence.
    return f"exp:{experience_id}", _normalize_field(field_name, EXPERIENCE_FIELDS) or "highlights"


def _replaced_highlight(profile: Profile, target: str, field_name: str, replaces: str) -> str:
    """The existing highlight a ``replaces`` names, or "" when it names none."""
    if not replaces.strip() or field_name != "highlights" or not target.startswith("exp:"):
        return ""
    experience = _find(profile, target[4:])
    if experience is None:
        return ""
    index = _match_highlight(experience.highlights, replaces)
    return experience.highlights[index] if index is not None else ""


def _match_highlight(lines: list[str], old: str) -> int | None:
    """The line a ``replaces`` names: exact, or the only one it can be a piece of.

    A model quoting a long line from the profile sometimes shortens it, so an exact
    match is tried first, then a unique prefix, then a unique containment. Two
    candidates mean no match: merging into the wrong result would rewrite a line the
    candidate never asked about.
    """
    folded = _fold(old)
    if not folded:
        return None
    for index, line in enumerate(lines):
        if _fold(line) == folded:
            return index
    starts = [index for index, line in enumerate(lines) if _fold(line).startswith(folded)]
    if len(starts) == 1:
        return starts[0]
    contains = [index for index, line in enumerate(lines) if folded in _fold(line)]
    return contains[0] if len(contains) == 1 else None


def _drops_figures(value: str, replaced: str) -> str:
    """A merge may add to a line; it may not quietly drop what the line measured."""
    canonical = _canonical(value)
    lost = [number for number in _NUMBER.findall(replaced) if number.replace(",", ".") not in canonical]
    return (
        f"“{value}” — merging into that line would drop {', '.join(lost)}, "
        "which your profile already states."
    )


def _nowhere(value: str, target: str, field_name: str) -> str:
    """Say what the model aimed at: a bare “nowhere” is not something to act on."""
    named = [
        part
        for part in (
            f"the target “{target.strip()}”" if (target or "").strip() else "",
            f"the field “{field_name.strip()}”" if (field_name or "").strip() else "",
        )
        if part
    ]
    return f"“{value}” — nowhere to write it: the model named {' and '.join(named) or 'no target'}."


def accept_edits(
    edits: list[DraftEdit], profile: Profile, answer: str
) -> tuple[list[Edit], list[str]]:
    """Keep the changes the answer supports, and say what the others were.

    A change is kept when it quotes the answer, states no number the answer does
    not, and names a place the profile has — spelled loosely is fine, see
    ``_resolve``. Unlike the writer's grounding check, this rejects one change at a
    time rather than the whole batch: the candidate still has their answer in front
    of them and is told what could not be placed, so nothing is lost in silence.
    """
    kept: list[Edit] = []
    dropped: list[str] = []
    for item in edits:
        value = " ".join((item.value or "").split())
        if not value:
            continue
        target, field_name = _resolve(profile, item.target, item.field)
        if not target:
            dropped.append(_nowhere(value, item.target, item.field))
            continue
        # A line that supersedes an existing one may keep the figures that line
        # already stated: they are in the profile, not invented.
        replaced = _replaced_highlight(profile, target, field_name, item.replaces)
        corpus = f"{answer}\n{replaced}" if replaced else answer
        if not _quotes(item.source, answer):
            dropped.append(f"“{value}” — I could not find it in your answer.")
        elif not _numbers_present(value, corpus):
            dropped.append(f"“{value}” — it states a number your answer does not.")
        elif replaced and not _numbers_present(replaced, value):
            dropped.append(_drops_figures(value, replaced))
        else:
            kept.append(
                Edit(
                    target=target,
                    field=field_name,
                    value=value,
                    source=item.source.strip(),
                    replaces=item.replaces.strip(),
                )
            )
    return kept, dropped


# --- writing -------------------------------------------------------------------


@dataclass
class Written:
    """One field an answer wrote to, ready to be shown back to the candidate."""

    label: str
    lines: list[str] = field(default_factory=list)
    # True when a line the profile already had was superseded rather than added to:
    # the page says so, because that is the one change that overwrites something.
    rewritten: bool = False


@dataclass
class DraftResult:
    """What applying an answer produced."""

    profile: Profile
    written: list[Written] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _append_items(current: list[str], items: list[str]) -> list[str]:
    added: list[str] = []
    seen = {_fold(item) for item in current}
    for item in items:
        item = item.strip()
        if item and _fold(item) not in seen:
            seen.add(_fold(item))
            current.append(item)
            added.append(item)
    return added


def _append_text(container: Experience, field_name: str, value: str) -> list[str]:
    text = " ".join(value.split())
    current = str(getattr(container, field_name) or "").strip()
    if not current:
        setattr(container, field_name, text)
        return [text]
    if _fold(text) in _fold(current):
        return []
    setattr(container, field_name, f"{current}\n{text}")
    return [text]


def _write(
    container: object, field_name: str, value: str, replaces: str = ""
) -> tuple[list[str], bool]:
    """Write the value, and return the lines it produced and whether it replaced one."""
    value = value.strip()
    if not value:
        return [], False

    # Merging a detail into the line it belongs to, when the model named that line.
    if (
        replaces
        and field_name == "highlights"
        and isinstance(container, Experience)
    ):
        index = _match_highlight(container.highlights, replaces)
        if index is not None:
            if _fold(container.highlights[index]) == _fold(value):
                return [], False  # the line already says it
            container.highlights[index] = value
            return [value], True

    current = getattr(container, field_name, None)

    if isinstance(current, list):
        items = split_list(value) if field_name in COMMA_LISTS else [value]
        return _append_items(current, items), False

    if isinstance(container, Experience):
        if field_name in _IDENTITY_FIELDS:
            if str(current or "").strip():
                return [], False
            setattr(container, field_name, value)
            return [value], False
        return _append_text(container, field_name, value), False

    # A fact or a preference holds one value: the answer corrects it.
    if str(current or "").strip() == value:
        return [], False
    setattr(container, field_name, value)
    return [value], False


def _container(
    profile: Profile, new_experience: Experience, target: str
) -> object | None:
    if target == "exp:new":
        return new_experience
    if target.startswith("exp:"):
        return _find(profile, target[4:])
    if target == "facts":
        return profile.facts
    if target == "prefs":
        return profile.preferences
    return None


def _label(profile: Profile, new_experience: Experience, target: str, field_name: str) -> str:
    """How a change is announced to the candidate."""
    name = FIELD_LABELS.get(field_name, field_name.replace("_", " "))
    if target == "facts":
        return f"About you · {name}"
    if target == "prefs":
        return f"What you want next · {name}"
    experience = new_experience if target == "exp:new" else _find(profile, target[4:])
    where = _experience_label(experience) if experience is not None else "The experience"
    return f"{where} · {name}"


def apply_edits(profile: Profile, edits: list[Edit]) -> DraftResult:
    """Write the accepted changes into a copy of the profile.

    The copy comes back with stable ids, and with the changes grouped by field so
    the page can show the candidate exactly what was written — and which of those
    lines replaced one they already had.
    """
    profile = profile.model_copy(deep=True)
    new_experience = Experience()
    lines_by: dict[tuple[str, str], list[str]] = {}
    rewritten_by: dict[tuple[str, str], bool] = {}
    order: list[tuple[str, str]] = []

    for edit in edits:
        container = _container(profile, new_experience, edit.target)
        if container is None:
            continue
        added, rewritten = _write(container, edit.field, edit.value, edit.replaces)
        if not added:
            continue
        key = (edit.target, edit.field)
        if key not in lines_by:
            lines_by[key] = []
            order.append(key)
        lines_by[key].extend(item for item in added if item not in lines_by[key])
        rewritten_by[key] = rewritten_by.get(key, False) or rewritten

    if new_experience.company or new_experience.title or new_experience.highlights:
        profile.experiences.append(new_experience)

    written = [
        Written(
            label=_label(profile, new_experience, target, name),
            lines=lines_by[(target, name)],
            rewritten=rewritten_by.get((target, name), False),
        )
        for target, name in order
    ]
    return DraftResult(profile=assign_ids(profile), written=written)


def apply_turn(profile: Profile, turn: Turn, answer: str) -> DraftResult:
    """Guard the turn's changes against the answer, then write them."""
    edits, dropped = accept_edits(turn.edits, profile, answer)
    result = apply_edits(profile, edits)
    result.dropped = dropped
    return result


# --- the model call ------------------------------------------------------------


def _profile_view(profile: Profile) -> str:
    """The profile with its empty fields shown: the interview targets the gaps.

    ``prompt_context.profile_text`` leaves the empty fields out, which is right
    for the writer and wrong here: what is missing is exactly what to ask about.
    """
    return yaml.safe_dump(
        profile.model_dump(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    ).strip()


def targets_text() -> str:
    """The targets the model may write to, generated from the code that checks them."""
    return "\n".join(
        [
            (
                f'- target "exp:<id>", any id in the profile: fields '
                f"{', '.join(EXPERIENCE_FIELDS)}"
            ),
            (
                f'- target "exp:new", for an experience the CV is missing: fields '
                f"{', '.join(NEW_EXPERIENCE_FIELDS)}"
            ),
            f'- target "facts": fields {", ".join(FACT_FIELDS)}',
            f'- target "prefs": fields {", ".join(PREFERENCE_FIELDS)}',
        ]
    )


def _request(profile: Profile, *, asked: list[str], question: str, answer: str) -> str:
    parts = [
        "## Profile",
        _profile_view(profile),
        "## Fields you may write (the only valid targets and fields)",
        targets_text(),
        "## Questions already asked (never ask one of these again)",
        "\n".join(f"- {item}" for item in asked) if asked else "(none yet)",
    ]
    question = " ".join((question or "").split())
    if not question:
        parts += [
            "## The last exchange",
            "(none: this is your first question for this visit. Ask it.)",
        ]
    elif answer.strip():
        parts += [
            "## The last exchange",
            f"Question: {question}",
            f"Answer: {answer.strip()}",
        ]
    else:
        parts += [
            "## The last exchange",
            f"Question: {question}",
            (
                "Answer: the candidate skipped this question. Write nothing for it, and "
                "ask something else."
            ),
        ]
    return "\n\n".join(parts) + "\n"


async def ask(
    settings: Settings,
    profile: Profile,
    *,
    asked: list[str],
    question: str = "",
    answer: str = "",
) -> Turn:
    """One exchange: write what the last answer said, and ask the next question.

    ``asked`` is what has already been put to the candidate, so the model does not
    loop. An empty ``answer`` with a question means the candidate skipped it.
    """
    return await complete_structured(
        settings,
        schema=Turn,
        content=_request(profile, asked=asked, question=question, answer=answer),
        system=load_prompt("profiler/interview.md"),
    )
