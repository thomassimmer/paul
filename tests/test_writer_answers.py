"""Tests for the factual answers: read from the profile, never generated."""

from __future__ import annotations

import pytest

from app.models import DraftAnswer, Facts, FormAnswer, FormQuestion
from app.writer import answers


def _question(label: str, *, max_length: int | None = None) -> FormQuestion:
    return FormQuestion(label=label, name="q", max_length=max_length)


# --- classification ------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("Are you authorized to work in the EU?", "work_authorization"),
        ("Are you authorised to work in the EU?", "work_authorization"),
        ("Do you have a work authorisation?", "work_authorization"),
        ("Do you require sponsorship?", "work_authorization"),
        ("Do you hold a valid work permit?", "work_authorization"),
        ("What is your notice period?", "notice_period"),
        ("When can you start?", "notice_period"),
        ("What are your salary expectations?", "salary_expectation"),
        ("What is your expected compensation?", "salary_expectation"),
        ("Are you willing to relocate?", "relocation"),
        ("Which languages do you speak?", "languages"),
        ("Do you speak fluent German?", "languages"),
        ("Quel est votre préavis ?", "notice_period"),
        ("Quelles langues parlez-vous ?", "languages"),
    ],
)
def test_factual_questions_are_recognized(label, field):
    assert answers.match_field(label) == field


@pytest.mark.parametrize(
    "label",
    [
        "Why do you want to join us?",
        "Describe a project you are proud of.",
        "Which programming languages do you use?",
        "What coding languages are you comfortable with?",
        "Tell us about a difficult decision.",
        "",
    ],
)
def test_open_questions_are_left_to_the_model(label):
    assert answers.match_field(label) is None


def test_a_programming_language_question_is_not_a_fact():
    # "languages" appears, but the fact is about the languages you speak.
    assert answers.match_field("Which development languages do you know?") is None


# --- the fact answers ----------------------------------------------------------


def test_a_question_is_answered_from_the_facts():
    facts = Facts(notice_period="Two months")
    answer = answers.fact_answer(_question("What is your notice period?"), facts)
    assert answer is not None
    assert answer.answer == "Two months"
    assert answer.source == "fact"
    assert "notice period" in answer.note


def test_a_language_list_is_joined():
    facts = Facts(languages=["French", "English"])
    answer = answers.fact_answer(_question("Which languages do you speak?"), facts)
    assert answer is not None
    assert answer.answer == "French, English"


def test_an_empty_fact_is_marked_missing_and_never_invented():
    answer = answers.fact_answer(_question("What are your salary expectations?"), Facts())
    assert answer is not None
    assert answer.source == "missing"
    assert answer.answer == ""
    assert "never invents" in answer.note


def test_a_fact_longer_than_the_field_is_flagged_not_truncated():
    facts = Facts(notice_period="Six months, negotiable around the end of the quarter")
    answer = answers.fact_answer(
        _question("What is your notice period?", max_length=20), facts
    )
    assert answer is not None
    assert answer.answer == "Six months, negotiable around the end of the quarter"
    assert "longer than this field" in answer.note


def test_an_open_question_has_no_fact_answer():
    assert answers.fact_answer(_question("Why us?"), Facts()) is None


def test_resolve_keeps_the_question_order():
    questions = [
        _question("What is your notice period?"),
        _question("Why us?"),
    ]
    slots = answers.resolve(questions, Facts(notice_period="One month"))
    assert slots[0] is not None and slots[0].source == "fact"
    assert slots[1] is None
    assert answers.open_questions(slots) == [1]


# --- merging the drafted answers ----------------------------------------------


def test_merge_matches_a_drafted_answer_by_question_text():
    questions = [_question("Why us?"), _question("Anything else?")]
    slots = answers.resolve(questions, Facts())
    generated = [
        DraftAnswer(question="Anything else?", answer="I like the mission."),
        DraftAnswer(question="Why us?", answer="Your product."),
    ]

    merged = answers.merge(questions, slots, generated)

    assert [answer.answer for answer in merged] == ["Your product.", "I like the mission."]
    assert all(answer.source == "generated" for answer in merged)


def test_merge_matches_a_reformulated_question():
    questions = [_question("Why do you want to join us?")]
    generated = [DraftAnswer(question="Why do you want to join Acme?", answer="Because.")]
    merged = answers.merge(questions, [None], generated)
    assert merged[0].answer == "Because."


def test_same_question_tolerates_wording_and_refuses_a_different_question():
    assert answers.same_question("Why do you want to join us?", "why do you want to join Acme?")
    assert not answers.same_question("Why do you want to join us?", "What is your notice period?")
    assert not answers.same_question("Why us?", "")


def test_merge_falls_back_to_position_when_the_question_is_not_echoed():
    questions = [_question("Why us?"), _question("Anything else?")]
    slots = answers.resolve(questions, Facts())
    generated = [DraftAnswer(question="", answer="First"), DraftAnswer(question="", answer="Second")]

    merged = answers.merge(questions, slots, generated)

    assert [answer.answer for answer in merged] == ["First", "Second"]


def test_merge_keeps_the_fact_and_ignores_a_drafted_answer_for_it():
    questions = [_question("What is your notice period?"), _question("Why us?")]
    slots = answers.resolve(questions, Facts(notice_period="One month"))
    generated = [DraftAnswer(question="What is your notice period?", answer="Invented!")]

    merged = answers.merge(questions, slots, generated)

    assert merged[0].source == "fact"
    assert merged[0].answer == "One month"
    assert merged[1].answer == ""


def test_merge_marks_an_empty_drafted_answer():
    questions = [_question("Why us?")]
    merged = answers.merge(questions, [None], [])
    assert merged[0].source == "generated"
    assert merged[0].answer == ""
    assert merged[0].note


def test_merge_shortens_a_drafted_answer_to_the_field_limit():
    questions = [_question("Why us?", max_length=12)]
    generated = [DraftAnswer(question="Why us?", answer="Because you build good tools.")]
    merged = answers.merge(questions, [None], generated)
    assert merged[0].answer == "Because you"
    assert len(merged[0].answer) <= 12


# --- reading the edited file back ---------------------------------------------


def test_apply_keeps_the_users_wording_for_a_fact():
    questions = [_question("What is your notice period?")]
    stored = [FormAnswer(question="What is your notice period?", answer="Three months")]
    merged = answers.apply(questions, Facts(notice_period="One month"), stored)
    assert merged[0].source == "fact"
    assert merged[0].answer == "Three months"


def test_apply_restores_the_fact_when_the_answer_was_cleared():
    questions = [_question("What is your notice period?")]
    stored = [FormAnswer(question="What is your notice period?", answer="")]
    merged = answers.apply(questions, Facts(notice_period="One month"), stored)
    assert merged[0].answer == "One month"


def test_apply_keeps_a_question_the_user_added_by_hand():
    stored = [FormAnswer(question="A question Paul never asked", answer="An answer")]
    merged = answers.apply([], Facts(), stored)
    assert len(merged) == 1
    assert merged[0].question == "A question Paul never asked"


def test_apply_rebuilds_the_source_of_a_generated_answer():
    questions = [_question("Why us?")]
    stored = [FormAnswer(question="Why us?", answer="Edited by hand")]
    merged = answers.apply(questions, Facts(), stored)
    assert merged[0].source == "generated"
    assert merged[0].answer == "Edited by hand"
