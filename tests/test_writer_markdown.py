"""Tests for the editable Markdown form of a generated document."""

from __future__ import annotations

from app.models import DraftLine, FormAnswer
from app.writer import markdown


def test_lines_round_trip_with_roles_and_citations():
    lines = [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="section_title", text="Experience"),
        DraftLine(role="bullet", text="Cut latency by 60%", achievement_ids=["exp-acme-a1"]),
        DraftLine(
            role="bullet",
            text="Migrated the platform",
            achievement_ids=["exp-acme-a1", "exp-acme-a2"],
        ),
    ]

    assert markdown.parse_lines(markdown.render_lines(lines)) == lines


def test_render_lines_starts_with_a_hint_that_is_not_a_line():
    rendered = markdown.render_lines([DraftLine(role="name", text="Camille")])
    assert rendered.startswith("<!--")
    assert markdown.parse_lines(rendered) == [DraftLine(role="name", text="Camille")]


def test_a_line_without_a_role_is_read_as_a_paragraph():
    parsed = markdown.parse_lines("Just a sentence.")
    assert parsed == [DraftLine(role="body_text", text="Just a sentence.")]


def test_parse_lines_skips_blank_lines_and_keeps_unknown_roles():
    parsed = markdown.parse_lines("  \n[x_unknown] Something\n")
    assert parsed == [DraftLine(role="x_unknown", text="Something")]


def test_parse_lines_reads_a_citation_at_the_end_only():
    parsed = markdown.parse_lines("[bullet] Use {braces} carefully")
    assert parsed[0].text == "Use {braces} carefully"
    assert parsed[0].achievement_ids == []


def test_to_html_escapes_the_text():
    html = markdown.to_html("[bullet] <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_to_html_shows_the_citation_as_a_marker():
    html = markdown.to_html("[bullet] A result {exp-acme-a1}")
    assert "exp-acme-a1" in html
    assert 'class="cite"' in html


def test_answers_round_trip():
    answers = [
        FormAnswer(question="Why us?", answer="Because of the mission."),
        FormAnswer(question="Anything else?", answer=""),
    ]

    rendered = markdown.render_answers(answers)
    parsed = markdown.parse_answers(rendered)

    assert [answer.question for answer in parsed] == ["Why us?", "Anything else?"]
    assert parsed[0].answer == "Because of the mission."
    assert parsed[1].answer == ""


def test_an_empty_answer_is_written_as_a_placeholder_and_read_back_empty():
    rendered = markdown.render_answers([FormAnswer(question="Q", answer="")])
    assert "_To fill in._" in rendered
    assert markdown.parse_answers(rendered)[0].answer == ""


def test_answers_with_several_lines_are_kept_together():
    rendered = "## Q\nFirst line.\nSecond line.\n"
    assert markdown.parse_answers(rendered)[0].answer == "First line.\nSecond line."


def test_render_answers_without_any_answer_is_empty():
    assert markdown.render_answers([]) == ""
    assert markdown.parse_answers("") == []
