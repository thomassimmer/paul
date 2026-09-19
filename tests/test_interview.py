from __future__ import annotations

from app.models import Experience, Profile
from app.profiler import interview
from app.profiler.ids import assign_ids
from app.profiler.text import extract_metrics


def _profile(*experiences: Experience) -> Profile:
    return assign_ids(Profile(experiences=list(experiences)))


def test_questions_cover_each_experience_then_facts_then_preferences():
    profile = _profile(
        Experience(company="Acme", title="Engineer", period="2022"),
        Experience(company="Globex", title="Analyst", period="2019"),
    )
    questions = interview.all_questions(profile)
    # 5 questions per experience, 5 facts, 2 preferences.
    assert len(questions) == 2 * 5 + 5 + 2
    assert questions[0].key == "exp:exp-acme-2022:context"
    assert questions[5].key == "exp:exp-globex-2019:context"
    assert questions[-1].key == "prefs:less_of"


def test_answered_questions_are_not_asked_again():
    profile = _profile(
        Experience(company="Acme", title="Engineer", period="2022", context="Analytics", team_size="7")
    )
    pending = {question.key for question in interview.pending_questions(profile)}
    assert "exp:exp-acme-2022:context" not in pending
    assert "exp:exp-acme-2022:team_size" not in pending
    assert "exp:exp-acme-2022:achievements" in pending


def test_skipped_questions_are_not_pending():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))
    first = interview.next_step(profile)
    assert first.question is not None
    after_skip = interview.next_step(profile, {first.question.key})
    assert after_skip.question is not None
    assert after_skip.question.key != first.question.key


def test_next_step_reports_progress():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))
    step = interview.next_step(profile)
    assert step.position == 1
    assert step.total == 12  # 5 experience + 5 facts + 2 preferences
    assert step.remaining == 12
    assert not step.done


def test_done_when_everything_is_answered_or_skipped():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))
    skipped = {question.key for question in interview.all_questions(profile)}
    assert interview.next_step(profile, skipped).done


def test_apply_answer_sets_text_and_lists():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))

    profile = interview.apply_answer(profile, "exp:exp-acme-2022:context", "  Analytics platform  ")
    assert profile.experiences[0].context == "Analytics platform"

    profile = interview.apply_answer(profile, "exp:exp-acme-2022:stack", "Rust, Kafka; PostgreSQL")
    assert profile.experiences[0].stack == ["Rust", "Kafka", "PostgreSQL"]

    profile = interview.apply_answer(profile, "facts:languages", "French (native), English (C1)")
    assert profile.facts.languages == ["French (native)", "English (C1)"]

    profile = interview.apply_answer(profile, "prefs:more_of", "System design, Mentoring")
    assert profile.preferences.more_of == ["System design", "Mentoring"]


def test_apply_answer_turns_lines_into_achievements_with_metrics():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))

    profile = interview.apply_answer(
        profile,
        "exp:exp-acme-2022:achievements",
        "- Cut ingestion latency by 60%\n- Led the migration of 40 services\n",
    )

    achievements = profile.experiences[0].achievements
    assert [a.text for a in achievements] == [
        "Cut ingestion latency by 60%",
        "Led the migration of 40 services",
    ]
    assert achievements[0].metrics == ["60%"]
    assert achievements[0].id == "exp-acme-2022-a1"
    assert achievements[1].id == "exp-acme-2022-a2"


def test_apply_answer_ignores_a_key_whose_experience_is_gone():
    profile = _profile(Experience(company="Acme", title="Engineer", period="2022"))
    assert interview.apply_answer(profile, "exp:exp-gone:context", "x") == profile


def test_extract_metrics():
    assert extract_metrics("Cut latency by 60% for 1200 users") == ["60%", "1200 users"]
    assert extract_metrics("Reduced costs by 1.2M €") == ["1.2M €"]
    assert extract_metrics("No numbers here") == []
    assert extract_metrics("Grew 3x in 2 months") == ["3x", "2 months"]
