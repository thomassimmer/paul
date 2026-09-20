"""Tests for the editable Markdown form of a generated document."""

from __future__ import annotations

from app.models import DraftLine, FormAnswer
from app.writer import markdown


def test_lines_round_trip_with_roles_and_citations():
    lines = [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="section_title", text="Experience"),
        DraftLine(role="bullet", text="Cut latency by 60%", source_ids=["exp-acme-2022"]),
        DraftLine(
            role="bullet",
            text="Migrated the platform",
            source_ids=["exp-acme-2022", "exp-northwind-2019"],
        ),
    ]

    assert markdown.parse_lines(markdown.render_lines(lines)) == lines


def test_render_lines_starts_with_a_hint_that_is_not_a_line():
    rendered = markdown.render_lines([DraftLine(role="name", text="Camille")])
    assert rendered.startswith("<!--")
    assert markdown.parse_lines(rendered) == [DraftLine(role="name", text="Camille")]


def test_source_only_drops_the_header_and_the_blank_lines_around_it():
    rendered = markdown.render_lines([DraftLine(role="name", text="Camille")])
    assert markdown.source_only(rendered) == "[name] Camille"


def test_a_line_without_a_role_is_read_as_a_paragraph():
    parsed = markdown.parse_lines("Just a sentence.")
    assert parsed == [DraftLine(role="body_text", text="Just a sentence.")]


def test_parse_lines_skips_blank_lines_and_keeps_unknown_roles():
    parsed = markdown.parse_lines("  \n[x_unknown] Something\n")
    assert parsed == [DraftLine(role="x_unknown", text="Something")]


def test_braces_that_do_not_hold_an_id_are_plain_text():
    parsed = markdown.parse_lines("[bullet] Use {braces} carefully {not an id}")
    assert parsed[0].text == "Use {braces} carefully {not an id}"
    assert parsed[0].source_ids == []


def test_a_citation_left_inside_the_text_is_lifted_out():
    parsed = markdown.parse_lines("[entry_subtitle] B2B platform {exp-acme-2022} for Acme")
    assert parsed[0].text == "B2B platform for Acme"
    assert parsed[0].source_ids == ["exp-acme-2022"]


def test_a_citation_written_twice_is_read_once():
    parsed = markdown.parse_lines("[bullet] Cut latency {exp-acme-2022} {exp-acme-2022}")
    assert parsed[0].text == "Cut latency"
    assert parsed[0].source_ids == ["exp-acme-2022"]


def test_rendering_never_leaves_a_citation_in_the_text():
    lines = [DraftLine(role="bullet", text="Cut latency {exp-acme-2022}", source_ids=[])]
    rendered = markdown.render_lines(lines)
    assert markdown.parse_lines(rendered)[0].text == "Cut latency"


def test_to_html_escapes_the_text():
    html = markdown.to_html("[bullet] <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_to_html_shows_the_citation_as_a_marker():
    html = markdown.to_html("[bullet] A result {exp-acme-2022}")
    assert "exp-acme-2022" in html
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
