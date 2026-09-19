"""Tests for the factual answers: read from the profile, never generated."""

from __future__ import annotations

import pytest

from app.models import DraftAnswer, Facts, FormAnswer, FormQuestion, Identity, Profile
from app.writer import answers


def _question(label: str, *, max_length: int | None = None) -> FormQuestion:
    return FormQuestion(label=label, name="q", max_length=max_length)


def _profile(*, facts: Facts | None = None, identity: Identity | None = None) -> Profile:
    return Profile(
        facts=facts if facts is not None else Facts(),
        identity=identity if identity is not None else Identity(),
    )


# --- classification ------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("First name", "first_name"),
        ("firstname", "first_name"),
        ("Prénom", "first_name"),
        ("Last name", "last_name"),
        ("Surname", "last_name"),
        ("Nom de famille", "last_name"),
        ("Full name", "name"),
        ("Your name", "name"),
        ("Name *", "name"),
        ("Nom", "last_name"),
        ("Email", "email"),
        ("E-mail address", "email"),
        ("Courriel", "email"),
        ("Phone", "phone"),
        ("Téléphone", "phone"),
        ("Mobile number", "phone"),
        ("Are you authorized to work in the EU?", "work_authorization"),
        ("Are you authorised to work in the EU?", "work_authorization"),
        ("Do you have a work authorisation?", "work_authorization"),
        ("Do you require sponsorship?", "work_authorization"),
        ("Do you hold a valid work permit?", "work_authorization"),
        ("Do you have a work permit?", "work_authorization"),
        (
            "If you are working in Canada on the basis of a work permit, what is the "
            "expiry date of your current work permit? (select date)",
            "work_permit_expiry",
        ),
        ("What is the expiry date of your visa?", "work_permit_expiry"),
        ("When does your residence permit expire?", "work_permit_expiry"),
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
        "Name of your current company",
        "Nom de l'entreprise",
        "How did you hear about us?",
        "",
    ],
)
def test_open_questions_are_left_to_the_model(label):
    assert answers.match_field(label) is None


def test_a_programming_language_question_is_not_a_fact():
    # "languages" appears, but the fact is about the languages you speak.
    assert answers.match_field("Which development languages do you know?") is None


# --- the identity answers ------------------------------------------------------


def test_the_name_email_and_phone_come_from_the_identity():
    profile = _profile(
        identity=Identity(
            name="Thomas Simmer",
            first_name="Thomas",
            last_name="Simmer",
            email="thomas.simmer@hotmail.fr",
            phone="+33 6 00 00 00 00",
        )
    )
    for label, expected in [
        ("First name", "Thomas"),
        ("Last name", "Simmer"),
        ("Email", "thomas.simmer@hotmail.fr"),
        ("Phone", "+33 6 00 00 00 00"),
    ]:
        answer = answers.fact_answer(_question(label), profile)
        assert answer is not None
        assert answer.answer == expected
        assert answer.source == "fact"


def test_first_and_last_name_fall_back_to_splitting_the_full_name():
    profile = _profile(identity=Identity(name="Thomas Simmer"))
    first = answers.fact_answer(_question("First name"), profile)
    last = answers.fact_answer(_question("Last name"), profile)
    assert first is not None and first.answer == "Thomas"
    assert last is not None and last.answer == "Simmer"


def test_an_explicit_first_name_wins_over_the_split():
    profile = _profile(identity=Identity(name="Camille Moreau", first_name="Cam"))
    answer = answers.fact_answer(_question("First name"), profile)
    assert answer is not None and answer.answer == "Cam"


def test_a_missing_email_is_marked_missing_not_invented():
    answer = answers.fact_answer(_question("Email"), _profile())
    assert answer is not None
    assert answer.source == "missing"
    assert answer.answer == ""
    assert "never invents" in answer.note


# --- the fact answers ----------------------------------------------------------


def test_a_question_is_answered_from_the_facts():
    profile = _profile(facts=Facts(notice_period="Two months"))
    answer = answers.fact_answer(_question("What is your notice period?"), profile)
    assert answer is not None
    assert answer.answer == "Two months"
    assert answer.source == "fact"
    assert "notice period" in answer.note


def test_a_language_list_is_joined():
    profile = _profile(facts=Facts(languages=["French", "English"]))
    answer = answers.fact_answer(_question("Which languages do you speak?"), profile)
    assert answer is not None
    assert answer.answer == "French, English"


def test_a_permit_expiry_question_is_not_answered_with_the_work_authorization():
    profile = _profile(facts=Facts(work_authorization="EU citizen, no sponsorship needed"))
    answer = answers.fact_answer(
        _question("What is the expiry date of your current work permit?"), profile
    )
    assert answer is not None
    assert answer.source == "missing"
    assert answer.answer == ""


def test_a_permit_expiry_question_is_answered_from_its_own_fact():
    profile = _profile(
        facts=Facts(
            work_authorization="EU citizen, no sponsorship needed",
            work_permit_expiry="2027-06-30",
        )
    )
    answer = answers.fact_answer(
        _question("What is the expiry date of your current work permit?"), profile
    )
    assert answer is not None
    assert answer.source == "fact"
    assert answer.answer == "2027-06-30"


def test_an_empty_fact_is_marked_missing_and_never_invented():
    answer = answers.fact_answer(_question("What are your salary expectations?"), _profile())
    assert answer is not None
    assert answer.source == "missing"
    assert answer.answer == ""
    assert "never invents" in answer.note


def test_a_fact_longer_than_the_field_is_flagged_not_truncated():
    profile = _profile(
        facts=Facts(notice_period="Six months, negotiable around the end of the quarter")
    )
    answer = answers.fact_answer(_question("What is your notice period?", max_length=20), profile)
    assert answer is not None
    assert answer.answer == "Six months, negotiable around the end of the quarter"
    assert "longer than this field" in answer.note


def test_an_open_question_has_no_fact_answer():
    assert answers.fact_answer(_question("Why us?"), _profile()) is None


def test_resolve_keeps_the_question_order():
    questions = [
        _question("What is your notice period?"),
        _question("Why us?"),
    ]
    slots = answers.resolve(questions, _profile(facts=Facts(notice_period="One month")))
    assert slots[0] is not None and slots[0].source == "fact"
    assert slots[1] is None
    assert answers.open_questions(slots) == [1]


def test_resolve_never_sends_an_identity_question_to_the_model():
    questions = [_question("First name"), _question("Email"), _question("Why us?")]
    slots = answers.resolve(
        questions, _profile(identity=Identity(name="Thomas Simmer", email="t@example.com"))
    )
    assert answers.open_questions(slots) == [2]


# --- merging the drafted answers ----------------------------------------------


def test_merge_matches_a_drafted_answer_by_question_text():
    questions = [_question("Why us?"), _question("Anything else?")]
    slots = answers.resolve(questions, _profile())
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
    slots = answers.resolve(questions, _profile())
    generated = [DraftAnswer(question="", answer="First"), DraftAnswer(question="", answer="Second")]

    merged = answers.merge(questions, slots, generated)

    assert [answer.answer for answer in merged] == ["First", "Second"]


def test_merge_keeps_the_fact_and_ignores_a_drafted_answer_for_it():
    questions = [_question("What is your notice period?"), _question("Why us?")]
    slots = answers.resolve(questions, _profile(facts=Facts(notice_period="One month")))
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
    merged = answers.apply(questions, _profile(facts=Facts(notice_period="One month")), stored)
    assert merged[0].source == "fact"
    assert merged[0].answer == "Three months"


def test_apply_restores_the_fact_when_the_answer_was_cleared():
    questions = [_question("What is your notice period?")]
    stored = [FormAnswer(question="What is your notice period?", answer="")]
    merged = answers.apply(questions, _profile(facts=Facts(notice_period="One month")), stored)
    assert merged[0].answer == "One month"


def test_apply_keeps_the_users_wording_for_an_identity_answer():
    questions = [_question("Email")]
    stored = [FormAnswer(question="Email", answer="other@example.com")]
    merged = answers.apply(
        questions, _profile(identity=Identity(email="thomas@example.com")), stored
    )
    assert merged[0].source == "fact"
    assert merged[0].answer == "other@example.com"


def test_apply_keeps_a_question_the_user_added_by_hand():
    stored = [FormAnswer(question="A question Paul never asked", answer="An answer")]
    merged = answers.apply([], _profile(), stored)
    assert len(merged) == 1
    assert merged[0].question == "A question Paul never asked"


def test_apply_rebuilds_the_source_of_a_generated_answer():
    questions = [_question("Why us?")]
    stored = [FormAnswer(question="Why us?", answer="Edited by hand")]
    merged = answers.apply(questions, _profile(), stored)
    assert merged[0].source == "generated"
    assert merged[0].answer == "Edited by hand"
