"""Tests for the grounding check: every claim must come from the profile."""

from __future__ import annotations

from app.models import Achievement, DraftLine, Education, Experience, Identity, Profile, Project
from app.writer import grounding


def _profile() -> Profile:
    return Profile(
        identity=Identity(name="Camille Moreau"),
        skills={"Languages": ["Rust", "Python"]},
        experiences=[
            Experience(
                id="exp-acme-2022",
                company="Acme",
                title="Lead Backend Engineer",
                stack=["Kafka"],
                achievements=[
                    Achievement(
                        id="exp-acme-2022-a1",
                        text="Cut ingestion latency by 60%",
                        metrics=["60%"],
                        skills=["Kafka"],
                    )
                ],
            )
        ],
        education=[Education(id="edu-insa", school="INSA Lyon", degree="MSc in Computer Science")],
        projects=[Project(id="proj-board", name="Job-board scraper")],
    )


def _issues(lines: list[DraftLine]) -> list[str]:
    return [issue.reason for issue in grounding.check(lines, _profile()).issues]


def test_a_grounded_bullet_passes():
    lines = [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="entry_title", text="Lead Backend Engineer — Acme"),
        DraftLine(role="bullet", text="Cut ingestion latency by 60%", achievement_ids=["exp-acme-2022-a1"]),
        DraftLine(role="skill_line", text="Rust; Kafka"),
    ]
    assert grounding.check(lines, _profile()).ok


def test_a_bullet_without_a_citation_is_flagged():
    reasons = _issues([DraftLine(role="bullet", text="Led the platform team")])
    assert any("without citing" in reason for reason in reasons)


def test_a_bullet_citing_an_unknown_achievement_is_flagged():
    reasons = _issues(
        [DraftLine(role="bullet", text="Did a thing", achievement_ids=["exp-nope-a9"])]
    )
    assert any("absent from your profile" in reason for reason in reasons)


def test_a_number_absent_from_the_profile_is_flagged():
    reasons = _issues(
        [DraftLine(role="bullet", text="Cut latency by 80%", achievement_ids=["exp-acme-2022-a1"])]
    )
    assert any("80" in reason for reason in reasons)


def test_the_ids_digits_are_not_treated_as_supporting_material():
    # "2022" only appears in the ids; a line that states it must be flagged.
    reasons = _issues(
        [DraftLine(role="bullet", text="Led the 2022 migration", achievement_ids=["exp-acme-2022-a1"])]
    )
    assert any("2022" in reason for reason in reasons)


def test_a_skill_absent_from_the_profile_is_flagged():
    reasons = _issues([DraftLine(role="skill_line", text="Rust; Kubernetes")])
    assert any("Kubernetes" in reason for reason in reasons)


def test_the_category_label_of_a_skill_line_is_not_a_claim():
    assert grounding.check([DraftLine(role="skill_line", text="Languages: Rust, Python")], _profile()).ok


def test_a_skill_line_with_a_category_still_checks_its_values():
    reasons = _issues([DraftLine(role="skill_line", text="Languages: Rust, COBOL")])
    assert any("COBOL" in reason for reason in reasons)
    assert not any("Languages" in reason for reason in reasons)


def test_a_school_or_a_project_is_a_valid_entry_title():
    lines = [
        DraftLine(role="entry_title", text="MSc in Computer Science — INSA Lyon"),
        DraftLine(role="entry_title", text="Job-board scraper"),
    ]
    assert grounding.check(lines, _profile()).ok


def test_an_entry_title_that_matches_nothing_is_flagged():
    reasons = _issues([DraftLine(role="entry_title", text="Staff Engineer — Globex")])
    assert any("does not match" in reason for reason in reasons)


def test_a_wrong_name_is_flagged():
    reasons = _issues([DraftLine(role="name", text="Someone Else")])
    assert any("not the name" in reason for reason in reasons)


def test_roles_that_state_no_result_are_not_number_checked():
    lines = [
        DraftLine(role="entry_dates", text="2020 / 2021"),
        DraftLine(role="contact", text="Paris · +33 6 00 00 00 00"),
        DraftLine(role="headline", text="Backend engineer with 12 years"),
    ]
    assert grounding.check(lines, _profile()).ok


def test_the_report_counts_what_it_checked():
    report = grounding.check([DraftLine(role="bullet", text="A")], _profile())
    assert report.checked == 1
    assert not report.ok


def test_an_empty_profile_flags_nothing_it_cannot_judge():
    empty = Profile()
    lines = [
        DraftLine(role="entry_title", text="Anything"),
        DraftLine(role="name", text="Anything"),
        DraftLine(role="bullet", text="A result", achievement_ids=["x"]),
    ]
    reasons = [issue.reason for issue in grounding.check(lines, empty).issues]
    # The citation is still checked; the entry title and the name are not, since
    # there is nothing in the profile to compare them to.
    assert len(reasons) == 1
    assert "absent from your profile" in reasons[0]
