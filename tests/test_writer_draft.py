"""Tests for the LLM steps of the writer and the guards around them."""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.models import CV_ROLES, LETTER_ROLES, DraftAnswer, DraftLine, OfferDraft, Profile
from app.prompt_context import language_code, output_language_text
from app.templates_engine import store as templates_store
from app.writer import draft


def _offer(language: str = "fr"):
    return OfferDraft(title="Backend Engineer", company="Acme", language=language).to_offer([])


# --- role repair ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("bullet", "bullet"),
        ("BULLET", "bullet"),
        ("skill_line", "skill_line"),
        ("skills", "skill_line"),
        ("Skills_Line", "skill_line"),
        ("section title", "section_title"),
        ("Title", "section_title"),
        ("paragraph", "body_text"),
        ("something_else", "body_text"),
        ("", "body_text"),
    ],
)
def test_role_repair(role, expected):
    assert draft.normalize_role(role, CV_ROLES) == expected


def test_role_repair_uses_a_letter_role_for_a_letter():
    assert draft.normalize_role("greeting", LETTER_ROLES) == "salutation"


def test_clean_ids_drops_blanks_and_duplicates():
    assert draft.clean_ids([" a ", "", "a", "b", "  "]) == ["a", "b"]


def test_normalize_lines_drops_empty_lines_and_keeps_the_text():
    lines = [
        DraftLine(role="bullet", text="  A result  ", source_ids=["x"]),
        DraftLine(role="bullet", text="   "),
    ]
    normalized = draft.normalize_lines(lines, CV_ROLES)
    assert normalized == [DraftLine(role="bullet", text="A result", source_ids=["x"])]


def test_normalize_lines_lifts_a_citation_the_model_left_in_the_text():
    lines = [
        DraftLine(role="bullet", text="Cut latency by 60% {exp-a1}", source_ids=["exp-a1"])
    ]
    assert draft.normalize_lines(lines, CV_ROLES) == [
        DraftLine(role="bullet", text="Cut latency by 60%", source_ids=["exp-a1"])
    ]


def test_normalize_lines_keeps_the_ids_of_a_citation_only_written_in_the_text():
    lines = [DraftLine(role="bullet", text="Cut latency by 60% {exp-a1}")]
    normalized = draft.normalize_lines(lines, CV_ROLES)
    assert normalized == [
        DraftLine(role="bullet", text="Cut latency by 60%", source_ids=["exp-a1"])
    ]


# --- the calls -----------------------------------------------------------------


def _fake_complete(monkeypatch, result):
    calls: dict = {}

    async def fake(settings, *, schema, content, system=None, **kwargs):
        calls["schema"] = schema
        calls["content"] = content
        calls["system"] = system
        return result(schema)

    monkeypatch.setattr(draft, "complete_structured", fake)
    return calls


def test_tailor_cv_returns_repaired_lines(monkeypatch):
    def result(schema):
        return schema(
            lines=[
                DraftLine(role="skills", text="Rust; Kafka"),
                DraftLine(role="bullet", text="Cut latency by 60%", source_ids=["exp-a1", "exp-a1"]),
                DraftLine(role="bullet", text=""),
            ]
        )

    calls = _fake_complete(monkeypatch, result)
    profile = Profile()

    lines = asyncio.run(
        draft.tailor_cv(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=profile,
            blueprint=templates_store.load_blueprint("cv"),
            target_pages=2,
        )
    )

    assert [line.role for line in lines] == ["skill_line", "bullet"]
    assert lines[1].source_ids == ["exp-a1"]
    assert "Candidate profile" in calls["content"]
    assert "Write in French" in calls["content"]
    assert "2 page(s)" in calls["content"]
    assert calls["system"]


def test_the_request_carries_an_instruction_when_given(monkeypatch):
    def result(schema):
        return schema(lines=[])

    calls = _fake_complete(monkeypatch, result)
    asyncio.run(
        draft.tailor_cv(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            blueprint=templates_store.load_blueprint("cv"),
            target_pages=1,
            instruction="shorter",
        )
    )
    assert "Additional instruction" in calls["content"]
    assert "shorter" in calls["content"]
    assert "Current version" not in calls["content"]


def test_the_request_carries_the_current_version_when_given(monkeypatch):
    def result(schema):
        return schema(lines=[])

    calls = _fake_complete(monkeypatch, result)
    asyncio.run(
        draft.tailor_cv(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            blueprint=templates_store.load_blueprint("cv"),
            target_pages=1,
            instruction="shorter",
            current="[name] Camille Moreau",
        )
    )
    assert "Current version" in calls["content"]
    assert "[name] Camille Moreau" in calls["content"]
    assert calls["content"].index("Current version") < calls["content"].index("Additional instruction")


def test_write_letter_asks_for_a_letter(monkeypatch):
    def result(schema):
        return schema(lines=[DraftLine(role="greeting", text="Dear team,")])

    calls = _fake_complete(monkeypatch, result)
    lines = asyncio.run(
        draft.write_letter(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            blueprint=templates_store.load_blueprint("letter"),
            target_pages=1,
        )
    )
    assert lines[0].role == "salutation"
    assert "Greeting" not in calls["content"]  # the prompt is a file, not in the request


def test_answer_questions_numbers_them_and_states_the_limit(monkeypatch):
    def result(schema):
        return schema(answers=[DraftAnswer(question="Why us?", answer="Because.")])

    calls = _fake_complete(monkeypatch, result)
    answers = asyncio.run(
        draft.answer_questions(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            questions=[("Why us?", 500)],
        )
    )
    assert answers[0].answer == "Because."
    assert "max_length: 500" in calls["content"]


def test_answer_questions_carries_the_current_answers_when_given(monkeypatch):
    def result(schema):
        return schema(answers=[DraftAnswer(question="Why us?", answer="Because.")])

    calls = _fake_complete(monkeypatch, result)
    asyncio.run(
        draft.answer_questions(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            questions=[("Why us?", 500)],
            instruction="shorter",
            current="### Why us?\nA long answer.",
        )
    )
    assert "Current answers" in calls["content"]
    assert "A long answer." in calls["content"]


def test_answer_questions_without_questions_does_not_call_the_model(monkeypatch):
    async def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("no question, no call")

    monkeypatch.setattr(draft, "complete_structured", boom)
    assert asyncio.run(
        draft.answer_questions(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            questions=[],
        )
    ) == []


# --- the length guard ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("Short answer", 500, "Short answer"),
        ("Short answer", None, "Short answer"),
        ("Short answer", 0, "Short answer"),
        ("Because you build good tools.", 12, "Because you"),
        ("OneVeryLongWordThatCannotBeSplit", 5, "OneVe"),
    ],
)
def test_fit_length(text, limit, expected):
    assert draft.fit_length(text, limit) == expected


# --- the template hint ---------------------------------------------------------


def test_the_template_hint_names_the_roles_and_the_sections():
    text = draft.template_text(templates_store.load_blueprint("cv"))
    assert "section_title" in text
    assert "Experience" in text


# --- the output language -------------------------------------------------------


def test_language_follows_the_offer_by_default():
    assert language_code(Settings(), _offer("de")) == "de"
    assert "German" in output_language_text(Settings(), _offer("de"))


def test_a_forced_language_wins_over_the_offer():
    settings = Settings(output_language="en")
    assert language_code(settings, _offer("fr")) == "en"
    assert "whatever the offer" in output_language_text(settings, _offer("fr"))


def test_an_unknown_language_falls_back_to_english():
    assert language_code(Settings(), _offer("klingon")) == "en"
    assert language_code(Settings(), _offer("")) == "en"


def test_a_language_given_as_a_name_is_understood():
    assert language_code(Settings(), _offer("French")) == "fr"
