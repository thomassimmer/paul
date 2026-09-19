"""The gap-targeted interview.

The README asks the agent to interview you "one experience at a time, targeting
what a CV usually omits". Which question comes next is computable from the
profile itself, so it is computed by code and not left to the LLM: the same
profile always yields the same questions, and answers merge deterministically.
That makes this step free, explainable and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Achievement, Experience, Profile
from app.profiler.ids import assign_ids
from app.profiler.text import extract_metrics, split_list

KIND_TEXT = "text"
KIND_LIST = "list"


@dataclass(frozen=True)
class Question:
    """One interview question, addressed by a stable ``key``.

    The key is what gets stored when a question is skipped, so it must not
    depend on the current position in the list.
    """

    key: str
    topic: str
    prompt: str
    hint: str = ""
    kind: str = KIND_TEXT


@dataclass(frozen=True)
class InterviewStep:
    question: Question | None
    position: int
    total: int
    remaining: int

    @property
    def done(self) -> bool:
        return self.question is None


def _experience_label(experience: Experience) -> str:
    company = experience.company or experience.id
    return f"{company} — {experience.title}" if experience.title else company


def _experience_key(experience_id: str, field: str) -> str:
    return f"exp:{experience_id}:{field}"


def _experience_questions(experience: Experience) -> list[Question]:
    label = _experience_label(experience)
    return [
        Question(
            _experience_key(experience.id, "context"),
            label,
            f"What was {label} about, and what exactly was your remit?",
            "The company, the product, your role — what a CV usually leaves out.",
        ),
        Question(
            _experience_key(experience.id, "team_size"),
            label,
            f"How big was the team at {label}, and who did you report to?",
            "For example: “8 engineers, reporting to the CTO”.",
        ),
        Question(
            _experience_key(experience.id, "stack"),
            label,
            f"Which technologies did you actually use day to day at {label}?",
            "Comma-separated. Only what you really touched.",
            KIND_LIST,
        ),
        Question(
            _experience_key(experience.id, "achievements"),
            label,
            f"What results are you most proud of at {label}?",
            "One result per line. Numbers and units help a lot.",
        ),
        Question(
            _experience_key(experience.id, "difficulties"),
            label,
            f"What was genuinely hard at {label}, and how did you solve it?",
            "The difficulty and what you did about it.",
        ),
    ]


_FACT_QUESTIONS = [
    Question(
        "facts:work_authorization",
        "You",
        "What is your work authorization situation?",
        "For example: “EU citizen”, “needs sponsorship”, “permanent resident”.",
    ),
    Question(
        "facts:notice_period",
        "You",
        "What is your notice period?",
        "For example: “2 months”, “immediately available”.",
    ),
    Question(
        "facts:salary_expectation",
        "You",
        "What are your salary expectations?",
        "Kept in your profile and reused for application forms.",
    ),
    Question(
        "facts:relocation",
        "You",
        "Are you willing to relocate, and where?",
        "",
    ),
    Question(
        "facts:languages",
        "You",
        "Which languages do you speak, and at what level?",
        "Comma-separated, for example: “French (native), English (C1)”.",
        KIND_LIST,
    ),
]

_PREFERENCE_QUESTIONS = [
    Question(
        "prefs:more_of",
        "What you want next",
        "What would you like to do more of in your next role?",
        "Comma-separated.",
        KIND_LIST,
    ),
    Question(
        "prefs:less_of",
        "What you want next",
        "What would you like to do less of?",
        "Comma-separated.",
        KIND_LIST,
    ),
]


def all_questions(profile: Profile) -> list[Question]:
    """Every question the interview could ask, in order."""
    questions: list[Question] = []
    for experience in profile.experiences:
        questions.extend(_experience_questions(experience))
    return [*questions, *_FACT_QUESTIONS, *_PREFERENCE_QUESTIONS]


def _target(profile: Profile, key: str) -> tuple[object | None, str]:
    """Return ``(container, field_name)`` addressed by a question key."""
    parts = key.split(":", 2)
    scope = parts[0]
    if scope == "exp" and len(parts) == 3:
        _, experience_id, field = parts
        for experience in profile.experiences:
            if experience.id == experience_id:
                return experience, field
        return None, ""
    if len(parts) >= 2 and scope == "facts":
        return profile.facts, parts[1]
    if len(parts) >= 2 and scope == "prefs":
        return profile.preferences, parts[1]
    return None, ""


def is_answered(profile: Profile, question: Question) -> bool:
    """A question is answered once the field it targets has content."""
    container, field = _target(profile, question.key)
    if container is None:
        # The experience it referred to is gone: nothing left to ask.
        return True
    value = getattr(container, field, None)
    if isinstance(value, list):
        return len(value) > 0
    return bool(str(value or "").strip())


def pending_questions(profile: Profile, skipped: set[str] | None = None) -> list[Question]:
    skipped = skipped or set()
    return [
        question
        for question in all_questions(profile)
        if question.key not in skipped and not is_answered(profile, question)
    ]


def next_step(profile: Profile, skipped: set[str] | None = None) -> InterviewStep:
    """The question to show now, and where it sits in the whole interview."""
    questions = all_questions(profile)
    pending = pending_questions(profile, skipped)
    if not pending:
        return InterviewStep(None, len(questions), len(questions), 0)
    current = pending[0]
    return InterviewStep(current, questions.index(current) + 1, len(questions), len(pending))


def _achievements_from_text(text: str) -> list[Achievement]:
    """The deterministic split: one line of the answer, one achievement."""
    lines = [line.strip(" -\t") for line in (text or "").splitlines()]
    return [Achievement(text=line, metrics=extract_metrics(line)) for line in lines if line]


def is_achievements_target(profile: Profile, key: str) -> Experience | None:
    """Return the experience whose achievements this key targets, if any."""
    container, field = _target(profile, key)
    if field == "achievements" and isinstance(container, Experience):
        return container
    return None


def apply_answer(
    profile: Profile,
    key: str,
    answer: str,
    *,
    achievements: list[Achievement] | None = None,
) -> Profile:
    """Merge one interview answer into a copy of the profile.

    The type of the target field decides how the answer is parsed, so the
    question's ``kind`` is only a UI hint. ``achievements`` lets the caller
    supply an already-structured result (see ``structure.py``); otherwise the
    answer is split line by line.
    """
    profile = profile.model_copy(deep=True)
    container, field = _target(profile, key)
    if container is None:
        return profile

    answer = (answer or "").strip()
    current = getattr(container, field, None)

    if field == "achievements" and isinstance(container, Experience):
        container.achievements = (
            achievements if achievements is not None else _achievements_from_text(answer)
        )
    elif isinstance(current, list):
        setattr(container, field, split_list(answer))
    else:
        setattr(container, field, answer)

    return assign_ids(profile)
