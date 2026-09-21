from __future__ import annotations

import pytest

from app.offers import clean

FRAGMENT = """
<html><head><title>Senior Backend Engineer - Acme</title><style>.x{}</style></head>
<body>
  <script>track()</script>
  <div id="job">
    <h1>Senior <strong>Backend</strong> Engineer</h1>
    <div class="meta">Acme &middot; Lyon &middot; Remote friendly</div>
    <p>We are looking for a backend engineer.<br>You will own the ingestion path.</p>
    <h2>What you will do</h2>
    <ul>
      <li>Design data pipelines</li>
      <li>Mentor <a href="https://acme.test/team?utm_source=job&id=1">the team</a></li>
      <li>Own reliability<ul><li>on-call</li></ul></li>
    </ul>
    <table><tr><td>Contract</td><td>Permanent</td></tr></table>
  </div>
  <img src="track.gif">
  <span style="display:none">secret</span>
</body></html>
"""

FORM_FRAGMENT = """
<div>
  <p>We are looking for a backend engineer to own the ingestion path.</p>
  <form>
    <label for="q1">Why do you want to join us?</label>
    <textarea id="q1" name="why" maxlength="500" required></textarea>
    <label for="country">Country</label>
    <select id="country" name="country"><option>France</option><option>Germany</option></select>
    <fieldset>
      <legend>Work authorization</legend>
      <label><input type="radio" name="permit" value="eu"> EU citizen</label>
      <label><input type="radio" name="permit" value="other"> Need sponsorship</label>
    </fieldset>
    <input type="hidden" name="csrf" value="zzz">
    <input type="text" name="phone" placeholder="+33 ...">
    <input type="submit" value="Apply">
  </form>
</div>
"""

FORM_ONLY_FRAGMENT = """
<form>
  <label for="why">Why do you want to join us?</label>
  <textarea id="why" name="why"></textarea>
</form>
"""


def test_reads_headings_lists_paragraphs_and_tables():
    text = clean.clean_fragment(FRAGMENT).text
    assert text.startswith("Page title: Senior Backend Engineer - Acme")
    assert "# Senior Backend Engineer" in text
    assert "## What you will do" in text
    assert "- Design data pipelines" in text
    # The nested list keeps its own item, and the parent does not absorb it.
    assert "- Own reliability" in text
    assert "- on-call" in text
    assert "Contract | Permanent" in text


def test_keeps_line_breaks_inside_a_paragraph():
    text = clean.clean_fragment(FRAGMENT).text
    assert "We are looking for a backend engineer.\nYou will own the ingestion path." in text


def test_drops_scripts_styles_images_and_hidden_content():
    text = clean.clean_fragment(FRAGMENT).text
    assert "track()" not in text
    assert ".x{}" not in text
    assert "track.gif" not in text
    assert "secret" not in text


def test_renders_links_and_strips_tracking_parameters():
    cleaned = clean.clean_fragment(FRAGMENT)
    assert "[the team](https://acme.test/team?id=1)" in cleaned.text
    assert cleaned.links == 1


def test_form_fragments_do_not_pollute_the_description():
    text = clean.clean_fragment(FORM_FRAGMENT).text
    assert "Why do you want to join us?" not in text
    assert "Work authorization" not in text


def test_parses_the_application_form():
    form = clean.clean_fragment(FORM_FRAGMENT).form
    # Hidden plumbing and the submit button are not questions; the phone input is.
    assert [q.name for q in form] == ["why", "country", "permit", "phone"]

    textarea = form[0]
    assert textarea.label == "Why do you want to join us?"
    assert textarea.type == "textarea"
    assert textarea.required is True
    assert textarea.max_length == 500

    select = form[1]
    assert select.type == "select"
    assert select.options == ["France", "Germany"]

    radios = form[2]
    assert radios.type == "radio"
    # The fieldset legend names the question; the labels are its options.
    assert radios.label == "Work authorization"
    assert radios.options == ["EU citizen", "Need sponsorship"]


def test_ignores_hidden_and_submit_controls_and_falls_back_to_the_name():
    form = clean.clean_fragment(FORM_FRAGMENT).form
    assert "csrf" not in [q.name for q in form]
    assert "Apply" not in [q.label for q in form]
    # The phone input has no label: its name is humanised instead.
    assert "Phone" in [q.label for q in form]


def test_a_description_and_a_form_paste_together():
    # The description and the application form can be copied from different pages,
    # then pasted one after the other in the same box.
    description = "<div><p>Acme is hiring a backend engineer for its ingestion team.</p></div>"
    cleaned = clean.clean_fragment(description + FORM_ONLY_FRAGMENT)
    assert [q.name for q in cleaned.form] == ["why"]
    assert "Acme is hiring" in cleaned.text


def test_plain_text_fragments_are_accepted():
    cleaned = clean.clean_fragment("Senior Backend Engineer\nAcme, Lyon\nRust and Kafka required.")
    assert cleaned.html is False
    assert cleaned.form == []
    assert "Rust and Kafka required." in cleaned.text


def test_looks_like_html():
    assert clean.looks_like_html("<div>x</div>") is True
    assert clean.looks_like_html("just text") is False
    assert clean.looks_like_html("2 < 3 and 5 > 4") is False


@pytest.mark.parametrize(
    "href,expected",
    [
        ("https://x.test/a?utm_source=y&id=2", "https://x.test/a?id=2"),
        ("https://x.test/a?gclid=1", "https://x.test/a"),
        ("#section", ""),
        ("javascript:void(0)", ""),
        ("mailto:a@b.test", ""),
        ("", ""),
    ],
)
def test_clean_url(href, expected):
    assert clean.clean_url(href) == expected


def test_refuses_a_form_only_fragment():
    with pytest.raises(clean.CleanError, match="Only an application form"):
        clean.clean_fragment(FORM_ONLY_FRAGMENT)


def test_refuses_empty_content():
    with pytest.raises(clean.CleanError, match="No text could be read"):
        clean.clean_fragment("   ")


def test_refuses_a_fragment_with_almost_no_text():
    with pytest.raises(clean.CleanError, match="almost no text"):
        clean.clean_fragment("<div><p>Hi</p></div>")


def test_truncates_a_huge_fragment():
    huge = "<div>" + "".join(
        f"<p>line {index} of text that is long enough to matter</p>" for index in range(4000)
    ) + "</div>"
    cleaned = clean.clean_fragment(huge)
    assert cleaned.truncated is True
    assert len(cleaned.text) <= clean.MAX_CHARS
    assert len(huge) > clean.MAX_CHARS


# --- the form pasted later, in the preparation modal ---------------------------


def test_parse_questions_reads_a_markup_form_like_an_analysis():
    questions = clean.parse_questions(FORM_FRAGMENT)

    assert [question.name for question in questions] == ["why", "country", "permit", "phone"]
    assert questions[0].max_length == 500
    assert questions[0].required is True


def test_parse_questions_reads_a_form_typed_by_hand():
    questions = clean.parse_questions(
        "1. Why do you want to join us?\n"
        "- What is your notice period?\n"
        "\n"
        "What are your salary expectations?\n"
    )

    assert [question.label for question in questions] == [
        "Why do you want to join us?",
        "What is your notice period?",
        "What are your salary expectations?",
    ]
    assert all(question.type == "textarea" for question in questions)


def test_parse_questions_falls_back_to_the_text_of_markup_without_controls():
    questions = clean.parse_questions("<div><p>Why us?</p><p>Notice period?</p></div>")

    assert [question.label for question in questions] == ["Why us?", "Notice period?"]


def test_parse_questions_returns_nothing_for_an_empty_paste():
    assert clean.parse_questions("") == []
    assert clean.parse_questions("   \n  ") == []
