"""Factual form questions, answered from the profile and nowhere else.

The rule from the README is blunt: a question about your name, your email, your
work authorization, your notice period, your salary expectation, your willingness
to relocate or the languages you speak is **never** generated. It is read from the
profile — your own words, or nothing. A model asked "are you allowed to work
here?" would answer something confident and wrong.

Which questions are factual is decided here, by pattern, not by asking a model: the
same question must always get the same treatment, and a misclassification must be
readable in the code. Open questions ("Why do you want to join us?") are the only
ones sent to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ats import normalize
from app.models import DraftAnswer, FormAnswer, FormQuestion, Profile
from app.writer.draft import fit_length

# Identity fields are read from ``profile.identity``; every other fact from
# ``profile.facts``. Keeping them apart here is what lets both be "your words".
IDENTITY_FIELDS = ("name", "first_name", "last_name", "email", "phone")

FACT_LABELS: dict[str, str] = {
    "name": "name",
    "first_name": "first name",
    "last_name": "last name",
    "email": "email address",
    "phone": "phone number",
    "work_authorization": "work authorization",
    "work_permit_expiry": "work permit expiry date",
    "notice_period": "notice period",
    "salary_expectation": "salary expectation",
    "relocation": "relocation",
    "languages": "languages",
}

MISSING_NOTE = "Not in your profile yet: Paul never invents this. Fill it in and prepare again."
GENERATED_NOTE = "Drafted from your profile; check the wording before sending."
EMPTY_NOTE = "No answer was generated: write it by hand."


@dataclass(frozen=True)
class FactRule:
    """A ``facts`` field, and the questions that belong to it.

    ``excludes`` wins over ``patterns``: "which programming languages do you use?"
    contains "languages" but asks about skills, not about the languages you speak.
    """

    field: str
    patterns: tuple[re.Pattern[str], ...]
    excludes: tuple[re.Pattern[str], ...] = ()

    def matches(self, text: str) -> bool:
        if any(pattern.search(text) for pattern in self.excludes):
            return False
        return any(pattern.search(text) for pattern in self.patterns)


def _patterns(*expressions: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(expression) for expression in expressions)


# The order decides ties: identity first (a "first name" is not an open question),
# then the facts. A question about sponsorship is about work authorization even
# when it also mentions relocation.
FACT_RULES: tuple[FactRule, ...] = (
    FactRule(
        "first_name",
        _patterns(
            r"\bfirst\s*name\b",
            r"\bgiven\s*name\b",
            r"\bpr[eé]nom\b",
            r"\bvorname\b",
        ),
    ),
    FactRule(
        "last_name",
        _patterns(
            r"\blast\s*name\b",
            r"\bsur\s*name\b",
            r"\bfamily\s*name\b",
            r"\bnom\s+de\s+famille\b",
            r"^nom$",  # the bare French field; "nom de ..." is not a surname
            r"\bnachname\b",
        ),
    ),
    FactRule(
        "name",
        _patterns(
            r"^(?:your\s+|full\s+|legal\s+)*name\s*[*?:]?$",
            r"\b(?:full|legal)\s+name\b",
            r"\bnom\s+complet\b",
        ),
    ),
    FactRule(
        "email",
        _patterns(
            r"\be-?mail\b",
            r"\bcourriel\b",
            r"\badresse\s+[eé]lectronique\b",
        ),
    ),
    FactRule(
        "phone",
        _patterns(
            r"\bphone\b",
            r"\btelephone\b",
            r"\bt[eé]l[eé]phone\b",
            r"\bmobile\b",
            r"\bportable\b",
        ),
    ),
    FactRule(
        "work_permit_expiry",
        # Needs both a permit/visa and an expiry word, in any order: "expiry date of
        # your current work permit" is a date, not a yes/no on work authorization.
        _patterns(
            r"(?=.*\b(?:work\s+permit|residence\s+permit|visa)\b)"
            r"(?=.*\b(?:expir\w*|validit\w*|end\s+date|valid\s+(?:until|through|till))\b)",
        ),
    ),
    FactRule(
        "work_authorization",
        _patterns(
            r"\bwork(?:ing)?\s+(?:authoriz|authoris|permit|visa|status)",
            r"\bauthoriz\w*\s+to\s+work\b",
            r"\bauthoris\w*\s+to\s+work\b",
            r"\b(?:permit\w*|allowed|eligible|legally\s+allowed|entitled)\s+to\s+work\b",
            r"\bright\s+to\s+work\b",
            r"\bwork\s+permit\b",
            r"\bpermit\s+to\s+work\b",
            r"\bvisa\b",
            r"\bsponsor\w*\b",
            r"\bcitizenship\b",
            r"autorisation\s+de\s+travail",
            r"permis\s+de\s+travail",
            r"droit\s+de\s+travail",
            r"parrainage",
            r"arbeitserlaubnis",
            r"visum",
        ),
    ),
    FactRule(
        "notice_period",
        _patterns(
            r"\bnotice\s+period\b",
            r"\bwhen\s+(?:can|could|would|are)\s+you\s+(?:start|begin|available)",
            r"\bstart(?:ing)?\s+date\b",
            r"\bavailable\s+(?:to\s+start|from|as\s+of)\b",
            r"\bearliest\s+(?:start|availability)\b",
            r"\bavailability\b",
            r"pr[eé]avis",
            r"date\s+de\s+disponibilit[eé]",
            r"disponibilit[eé]",
            r"k[uü]ndigungsfrist",
            r"verf[uü]gbarkeit",
        ),
    ),
    FactRule(
        "salary_expectation",
        _patterns(
            r"\bsalary\b",
            r"\bcompensation\b",
            r"\bexpected\s+(?:pay|salary|compensation|remuneration)\b",
            r"\b(?:day|daily)\s+rate\b",
            r"\bpay\s+expectation",
            r"r[eé]mun[eé]ration",
            r"salaire",
            r"pr[eé]tentions",
            r"gehalt",
            r"gehaltsvorstellung",
        ),
    ),
    FactRule(
        "relocation",
        _patterns(
            r"\brelocat\w*\b",
            r"\bwilling\s+to\s+(?:move|relocate)\b",
            r"\bable\s+to\s+(?:move|relocate)\b",
            r"mobilit[eé]",
            r"relocalisation",
            r"d[eé]m[eé]nagement",
        ),
    ),
    FactRule(
        "languages",
        _patterns(
            r"\blanguages?\b",
            r"\blanguage\s+skills?\b",
            r"\bfluen(?:t|cy)\b",
            r"\blangues?\b",
            r"\bsprachen\b",
        ),
        excludes=_patterns(
            r"\bprogramming\b",
            r"\bprogrammation\b",
            r"\bcoding\b",
            r"\bcode\b",
            r"\bscripting\b",
            r"\bdevelopment\s+languages?\b",
        ),
    ),
)


def match_field(question: str) -> str | None:
    """The ``facts`` field a question belongs to, or ``None`` when it is open."""
    text = normalize(question)
    if not text:
        return None
    for rule in FACT_RULES:
        if rule.matches(text):
            return rule.field
    return None


def question_title(question: FormQuestion) -> str:
    """The question as the form shows it, falling back to its field name."""
    return (question.label or question.name or "Question").strip()


def _split_name(name: str) -> tuple[str, str]:
    """A full name split on whitespace: first word, then the rest.

    Only a fallback for a profile that has not filled ``first_name`` /
    ``last_name`` yet; the user can always override the split by setting them.
    """
    parts = name.split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _identity_text(profile: Profile, field_name: str) -> str:
    if field_name == "first_name":
        first, _ = _split_name(profile.identity.name)
        return (profile.identity.first_name or first).strip()
    if field_name == "last_name":
        _, last = _split_name(profile.identity.name)
        return (profile.identity.last_name or last).strip()
    value = getattr(profile.identity, field_name, "")
    return str(value or "").strip()


def fact_text(profile: Profile, field_name: str) -> str:
    """A fact as one answer string. A list (languages) becomes a comma list."""
    if field_name in IDENTITY_FIELDS:
        return _identity_text(profile, field_name)
    value = getattr(profile.facts, field_name, "")
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def fact_answer(question: FormQuestion, profile: Profile) -> FormAnswer | None:
    """The answer to a factual question, or ``None`` when the model should draft it.

    A factual question with an empty fact is still answered here, with an empty
    answer and a ``missing`` source: the shape of the form is known, the content is
    the candidate's to give.
    """
    title = question_title(question)
    field_name = match_field(title)
    if field_name is None:
        return None

    label = FACT_LABELS.get(field_name, field_name.replace("_", " "))
    value = fact_text(profile, field_name)
    if not value:
        return FormAnswer(
            question=title,
            source="missing",
            max_length=question.max_length,
            note=MISSING_NOTE,
        )

    note = f"From your profile ({label})."
    if question.max_length and len(value) > question.max_length:
        note = (
            f"From your profile ({label}) — longer than this field's "
            f"{question.max_length}-character limit."
        )
    return FormAnswer(
        question=title,
        answer=value,
        source="fact",
        max_length=question.max_length,
        note=note,
    )


def resolve(questions: list[FormQuestion], profile: Profile) -> list[FormAnswer | None]:
    """One slot per question, in order: a fact answer, or ``None`` to draft."""
    return [fact_answer(question, profile) for question in questions]


def open_questions(slots: list[FormAnswer | None]) -> list[int]:
    """The indexes of the questions left to the model."""
    return [index for index, slot in enumerate(slots) if slot is None]


def _key(text: str) -> str:
    """A question, reduced to something two phrasings of it share.

    A model that echoes the question back may drop the trailing "?" or a final
    period; that is still the same question, and matching on it must not fail.
    """
    return normalize(text).strip(" ?.!:;,\t\n")


_WORD = re.compile(r"[a-z0-9]+")
_SAME_QUESTION = 0.6


def _words(text: str) -> set[str]:
    return set(_WORD.findall(normalize(text)))


def same_question(asked: str, echoed: str) -> bool:
    """True when ``echoed`` is the question ``asked``, wording aside.

    Models rewrite the questions they echo ("Why do you want to join us?" becomes
    "Why do you want to join Acme?"); comparing the words catches that, while an
    answer to a different question shares almost no word and stays unmatched.
    """
    asked_key, echoed_key = _key(asked), _key(echoed)
    if not asked_key or not echoed_key:
        return False
    if asked_key == echoed_key:
        return True
    left, right = _words(asked), _words(echoed)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= _SAME_QUESTION


def _drafted(question: str, answer: str, form_question: FormQuestion) -> FormAnswer:
    text = fit_length(answer.strip(), form_question.max_length)
    return FormAnswer(
        question=question,
        answer=text,
        source="generated",
        max_length=form_question.max_length,
        note="" if text else EMPTY_NOTE,
    )


def merge(
    questions: list[FormQuestion],
    slots: list[FormAnswer | None],
    generated: list[DraftAnswer],
) -> list[FormAnswer]:
    """Combine the fact answers with the drafted ones, one per question.

    A drafted answer is matched to its question by wording first, because a model
    reorders or rephrases the questions it echoes. Position is only the fallback,
    and only when the model named no question at all: an answer that names a
    question we never asked is dropped rather than attached to the wrong one,
    because a plausible answer under the wrong question is worse than an empty
    field.
    """
    merged: list[FormAnswer | None] = [
        slots[index] if index < len(slots) else None for index in range(len(questions))
    ]
    problems = [index for index, item in enumerate(merged) if item is None]

    used: set[int] = set()
    for index in problems:
        title = question_title(questions[index])
        for position, item in enumerate(generated):
            if position in used or not same_question(title, item.question):
                continue
            used.add(position)
            merged[index] = _drafted(title, item.answer, questions[index])
            break

    # Position, and only position, when the model named no question at all.
    anonymous = [
        position
        for position, item in enumerate(generated)
        if position not in used and not _key(item.question)
    ]
    if len(anonymous) == len(generated) == len(problems):
        for index, position in zip(problems, anonymous, strict=True):
            merged[index] = _drafted(
                question_title(questions[index]), generated[position].answer, questions[index]
            )

    return [
        item if item is not None else _drafted(question_title(question), "", question)
        for item, question in zip(merged, questions, strict=True)
    ]


def apply(
    questions: list[FormQuestion], profile: Profile, stored: list[FormAnswer]
) -> list[FormAnswer]:
    """Rebuild the answers for display: the source is recomputed, the text is yours.

    ``answers.md`` carries only the question and the answer, because that is what a
    human edits. Which answers came from the profile is recomputed here from the
    same rules that produced them, so the review screen can still say so — and an
    answer the user rewrote is kept exactly as they wrote it.
    """
    result: list[FormAnswer] = []
    for index, question in enumerate(questions):
        pasted = stored[index] if index < len(stored) else None
        text = (pasted.answer if pasted else "").strip()
        slot = fact_answer(question, profile)
        if slot is None:
            result.append(
                FormAnswer(
                    question=question_title(question),
                    answer=text,
                    source="generated",
                    max_length=question.max_length,
                    note="" if text else EMPTY_NOTE,
                )
            )
        elif slot.source == "fact":
            result.append(slot.model_copy(update={"answer": text or slot.answer}))
        else:
            result.append(slot)

    if len(stored) > len(questions):
        # Questions the user added by hand: Paul did not ask them, so he keeps them.
        result.extend(stored[len(questions) :])
    return result


def labels_for(questions: list[FormQuestion]) -> list[str]:
    """Debug helper: the fact fields a set of questions refers to."""
    return [field for question in questions if (field := match_field(question_title(question)))]
