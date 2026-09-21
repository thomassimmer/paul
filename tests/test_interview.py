"""The interview: what the model may write, and how an answer lands in the profile.

The generation is a model step, so it is tested the way the other ones are: the
guard around the model's answer is checked on its own, then the call itself
against a fake provider.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types

from app.config import Settings
from app.models import Experience, Profile
from app.profiler import interview
from app.profiler.ids import assign_ids
from app.profiler.interview import DraftEdit, DraftQuestion, Edit, Turn

ANSWER = "I cut the latency by 60% and led a team of 4 engineers."


def _profile() -> Profile:
    return assign_ids(
        Profile(
            experiences=[
                Experience(
                    company="Acme",
                    title="Engineer",
                    period="2022",
                    context="Analytics platform",
                    stack=["Rust"],
                    highlights=["Cut latency"],
                )
            ]
        )
    )


def _edit(target: str, field_name: str, value: str, source: str = ANSWER) -> Edit:
    return Edit(target=target, field=field_name, value=value, source=source)


# --- the guard -----------------------------------------------------------------


def _draft(target: str, field_name: str, value: str, source: str = ANSWER) -> DraftEdit:
    return DraftEdit(target=target, field=field_name, value=value, source=source)


def test_accept_edits_keeps_what_the_answer_supports():
    profile = _profile()
    items = [
        _draft("exp:exp-acme-2022", "highlights", "Cut latency by 60%", "Cut the latency by 60%"),
        # A number the answer never gave: invented, so refused.
        _draft("exp:exp-acme-2022", "highlights", "Cut latency by 80%", "Cut the latency by 60%"),
        # A quote that is not in the answer: refused.
        _draft("exp:exp-acme-2022", "highlights", "Led 4 engineers", "I managed the platform"),
        # An experience that does not exist: nowhere to write it.
        _draft("exp:exp-gone-2019", "highlights", "Led 4 engineers", "led a team of 4 engineers"),
        # No target at all: same.
        _draft("", "highlights", "Led 4 engineers", "led a team of 4 engineers"),
    ]
    kept, dropped = interview.accept_edits(items, profile, ANSWER)
    assert [edit.value for edit in kept] == ["Cut latency by 60%"]
    assert len(dropped) == 4


def test_accept_edits_says_why_each_change_was_dropped():
    profile = _profile()
    items = [
        _draft("exp:exp-acme-2022", "highlights", "Cut latency by 80%", "Cut the latency by 60%"),
        _draft("exp:exp-gone-2019", "highlights", "Led 4 engineers", "led a team of 4 engineers"),
    ]
    _, dropped = interview.accept_edits(items, profile, ANSWER)
    assert "states a number your answer does not" in dropped[0]
    # What the model aimed at is named: “nowhere” alone is not something to act on.
    assert "nowhere to write it" in dropped[1]
    assert "exp:exp-gone-2019" in dropped[1]


# --- the target a model names, however loosely --------------------------------


def test_a_target_named_by_company_finds_the_experience():
    kept, dropped = interview.accept_edits(
        [_draft("acme", "highlights", "Led 4 engineers", "led a team of 4 engineers")],
        _profile(),
        ANSWER,
    )
    assert dropped == []
    assert kept[0].target == "exp:exp-acme-2022"


def test_a_highlight_addition_is_written_when_the_field_is_unclear():
    # An experience accumulates highlights, so a field the profile does not know — or
    # an old name for it — must not cost the candidate their sentence.
    for field_name in ("", "achievements", "description"):
        kept, dropped = interview.accept_edits(
            [_draft("exp:exp-acme-2022", field_name, "Led 4 engineers", "led a team of 4 engineers")],
            _profile(),
            ANSWER,
        )
        assert dropped == []
        assert (kept[0].target, kept[0].field) == ("exp:exp-acme-2022", "highlights")


def test_a_target_that_matches_two_experiences_is_refused():
    profile = assign_ids(
        Profile(
            experiences=[
                Experience(company="Acme", title="Engineer", period="2022"),
                Experience(company="Acme", title="Engineer", period="2023"),
            ]
        )
    )
    kept, dropped = interview.accept_edits(
        [_draft("acme", "highlights", "Led 4 engineers", "led a team of 4 engineers")],
        profile,
        ANSWER,
    )
    assert kept == [] and len(dropped) == 1


def test_a_fact_field_the_profile_does_not_have_is_refused():
    # There is no highlight to fall back on for a fact: an unknown field is a mistake.
    kept, dropped = interview.accept_edits(
        [_draft("facts", "salary", "2 months", "2 months")], _profile(), ANSWER
    )
    assert kept == [] and len(dropped) == 1


def test_accept_edits_ignores_a_blank_value_without_reporting_it():
    profile = _profile()
    kept, dropped = interview.accept_edits([_draft("exp:exp-acme-2022", "highlights", "   ")], profile, ANSWER)
    assert kept == [] and dropped == []


def test_accept_edits_matches_a_quote_across_accents_and_spacing():
    profile = _profile()
    items = [_draft("exp:exp-acme-2022", "highlights", "Réduit la latence", "réduit  la    latence")]
    kept, _ = interview.accept_edits(items, profile, "J'ai réduit la latence.")
    assert len(kept) == 1


# --- writing -------------------------------------------------------------------


def test_apply_edits_adds_highlights_without_repeating_them():
    result = interview.apply_edits(
        _profile(),
        [
            _edit("exp:exp-acme-2022", "highlights", "Cut latency"),  # already there
            _edit("exp:exp-acme-2022", "highlights", "Led 4 engineers"),
        ],
    )
    assert result.profile.experiences[0].highlights == ["Cut latency", "Led 4 engineers"]
    assert result.written[0].lines == ["Led 4 engineers"]


def test_a_highlight_with_a_comma_stays_one_line():
    result = interview.apply_edits(
        _profile(),
        [_edit("exp:exp-acme-2022", "highlights", "Cut latency, from 900ms to 120ms")],
    )
    assert result.profile.experiences[0].highlights[-1] == "Cut latency, from 900ms to 120ms"


def test_a_stack_line_is_split_on_commas():
    result = interview.apply_edits(
        _profile(), [_edit("exp:exp-acme-2022", "stack", "Rust, Kafka, PostgreSQL")]
    )
    assert result.profile.experiences[0].stack == ["Rust", "Kafka", "PostgreSQL"]


def test_apply_edits_appends_to_the_context_instead_of_replacing_it():
    result = interview.apply_edits(
        _profile(), [_edit("exp:exp-acme-2022", "context", "Used by 100k analysts")]
    )
    assert result.profile.experiences[0].context == "Analytics platform\nUsed by 100k analysts"


def test_a_fact_is_corrected_rather_than_accumulated():
    profile = _profile()
    profile.facts.notice_period = "1 month"

    result = interview.apply_edits(profile, [_edit("facts", "notice_period", "2 months")])
    assert result.profile.facts.notice_period == "2 months"

    # Writing the same value again is not a change.
    again = interview.apply_edits(result.profile, [_edit("facts", "notice_period", "2 months")])
    assert again.written == []


def test_a_preference_list_gains_the_item_it_lacked():
    result = interview.apply_edits(
        _profile(), [_edit("prefs", "more_of", "Mentoring")]
    )
    assert result.profile.preferences.more_of == ["Mentoring"]
    assert result.written[0].label == "What you want next · more of"


def test_a_missing_experience_is_created_from_the_answer():
    result = interview.apply_edits(
        _profile(),
        [
            _edit("exp:new", "company", "Globex"),
            _edit("exp:new", "title", "Data Engineer"),
            _edit("exp:new", "period", "2019"),
            _edit("exp:new", "highlights", "Built the ingestion pipeline"),
        ],
    )
    created = result.profile.experiences[-1]
    assert (created.company, created.title, created.period) == ("Globex", "Data Engineer", "2019")
    assert created.highlights == ["Built the ingestion pipeline"]
    assert created.id == "exp-globex-2019"
    labels = {item.label: item.lines for item in result.written}
    assert labels["Globex — Data Engineer · what you did"] == ["Built the ingestion pipeline"]
    assert labels["Globex — Data Engineer · the company"] == ["Globex"]


def test_a_new_experience_nobody_described_is_not_added():
    result = interview.apply_edits(_profile(), [_edit("exp:new", "period", "  ")])
    assert len(result.profile.experiences) == 1


def test_apply_edits_ignores_an_experience_that_is_gone():
    result = interview.apply_edits(
        _profile(), [_edit("exp:exp-gone-2019", "highlights", "Something")]
    )
    assert len(result.profile.experiences) == 1
    assert result.written == []


def test_apply_turn_drops_an_ungrounded_edit_and_keeps_the_others():
    profile = _profile()
    turn = Turn(
        edits=[
            _draft("exp:exp-acme-2022", "highlights", "Cut latency by 60%", "Cut the latency by 60%"),
            _draft("exp:exp-acme-2022", "highlights", "Doubled revenue", "Doubled the revenue"),
        ],
        question=DraftQuestion(prompt="What next?"),
    )
    result = interview.apply_turn(profile, turn, ANSWER)

    assert result.profile.experiences[0].highlights == ["Cut latency", "Cut latency by 60%"]
    assert len(result.dropped) == 1
    assert "Doubled revenue" in result.dropped[0]


# --- merging a line into the one it belongs to --------------------------------


def _merge(value: str, replaces: str, source: str = ANSWER) -> Edit:
    return Edit(
        target="exp:exp-acme-2022",
        field="highlights",
        value=value,
        source=source,
        replaces=replaces,
    )


def test_a_highlight_is_merged_into_when_the_line_it_supersedes_is_named():
    result = interview.apply_edits(
        _profile(),
        [_merge("Cut latency from 900ms to 120ms, for 12k daily users", "Cut latency")],
    )

    assert result.profile.experiences[0].highlights == [
        "Cut latency from 900ms to 120ms, for 12k daily users"
    ]
    assert result.written[0].rewritten is True


def test_a_merge_leaves_the_line_where_it_was():
    profile = _profile()
    profile.experiences[0].highlights = ["First", "Cut latency", "Last"]

    result = interview.apply_edits(profile, [_merge("Cut latency by 60%", "Cut latency")])

    assert result.profile.experiences[0].highlights == ["First", "Cut latency by 60%", "Last"]


def test_a_shortened_quote_still_finds_the_line_to_merge_into():
    profile = _profile()
    profile.experiences[0].highlights = [
        "Led a complete overhaul of invoice allocation: pgvector and an LLM fallback"
    ]

    result = interview.apply_edits(
        profile,
        [
            _merge(
                "Led the invoice allocation overhaul: pgvector, an LLM fallback, and a "
                "zero-downtime migration",
                "Led a complete overhaul of invoice allocation",
            )
        ],
    )

    assert result.profile.experiences[0].highlights == [
        "Led the invoice allocation overhaul: pgvector, an LLM fallback, and a zero-downtime migration"
    ]
    assert result.written[0].rewritten is True


def test_a_merge_settles_a_line_without_saying_the_same_thing_twice():
    result = interview.apply_edits(
        _profile(), [_merge("cut latency", "Cut latency")]  # same line, reworded case only
    )
    assert result.written == []
    assert result.profile.experiences[0].highlights == ["Cut latency"]


def test_a_merge_that_names_no_line_adds_one_instead():
    profile = _profile()
    result = interview.apply_edits(
        profile, [_merge("Cut latency by 60%", "a line that is not there")]
    )
    # Nothing is lost: the line is added, and the page does not claim a rewrite.
    assert result.profile.experiences[0].highlights == ["Cut latency", "Cut latency by 60%"]
    assert result.written[0].rewritten is False


def test_an_ambiguous_merge_names_no_line():
    profile = _profile()
    profile.experiences[0].highlights = ["Cut latency on the API", "Cut latency on the batch job"]

    result = interview.apply_edits(profile, [_merge("Cut latency by 60%", "Cut latency")])

    # Two candidates: adding is safer than rewriting a line the candidate never meant.
    assert result.profile.experiences[0].highlights == [
        "Cut latency on the API",
        "Cut latency on the batch job",
        "Cut latency by 60%",
    ]
    assert result.written[0].rewritten is False


def test_a_merge_may_keep_the_numbers_of_the_line_it_supersedes():
    profile = _profile()
    profile.experiences[0].highlights = ["Cut latency by 60% for 13M invoices"]
    answer = "we halved the batch size"

    kept, dropped = interview.accept_edits(
        [
            DraftEdit(
                target="exp:exp-acme-2022",
                field="highlights",
                value="Cut latency by 60% for 13M invoices, with half the batch size",
                source=answer,
                replaces="Cut latency by 60% for 13M invoices",
            )
        ],
        profile,
        answer,
    )

    # 60% and 13M are not in the answer, but they are in the line being merged: the
    # merged line is allowed to keep what the profile already stated.
    assert dropped == [] and len(kept) == 1


def test_a_merge_that_drops_a_figure_the_profile_stated_is_refused():
    profile = _profile()
    profile.experiences[0].highlights = ["Cut latency by 60% in 2 weeks"]
    answer = "we also made the allocation configurable per account"

    kept, dropped = interview.accept_edits(
        [
            DraftEdit(
                target="exp:exp-acme-2022",
                field="highlights",
                value="Cut latency and made the allocation configurable per account",
                source=answer,
                replaces="Cut latency by 60% in 2 weeks",
            )
        ],
        profile,
        answer,
    )

    # A merge adds to a line; losing what it measured is a loss, and it is named.
    assert kept == [] and len(dropped) == 1
    assert "drop 60, 2" in dropped[0]


def test_the_numbers_of_another_line_are_not_a_licence_to_invent():
    profile = _profile()
    profile.experiences[0].highlights = ["Cut latency by 60%"]

    kept, dropped = interview.accept_edits(
        [
            DraftEdit(
                target="exp:exp-acme-2022",
                field="highlights",
                value="Cut latency by 60% and doubled revenue",
                source="doubled revenue",
            )
        ],
        profile,
        "doubled revenue",
    )

    # Without naming a line, only the answer can support a number.
    assert kept == [] and len(dropped) == 1


# --- the model call ------------------------------------------------------------


def _fake_litellm(monkeypatch, payload: str) -> list[dict]:
    calls: list[dict] = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        message = types.SimpleNamespace(content=payload)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("litellm")
    monkeypatch.setattr(module, "acompletion", acompletion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


def test_ask_returns_the_edits_and_the_next_question(monkeypatch):
    payload = json.dumps(
        {
            "edits": [
                {
                    "target": "exp:exp-acme-2022",
                    "field": "highlights",
                    "value": "Led a team of 4 engineers",
                    "source": "led a team of 4 engineers",
                }
            ],
            "question": {
                "prompt": "How big was the team?",
                "topic": "Team",
                "hint": "A number and a reporting line.",
                "why": "Gives the CV the scale of the role.",
            },
        }
    )
    _fake_litellm(monkeypatch, payload)
    turn = asyncio.run(
        interview.ask(
            Settings(model="openai/gpt-4o"),
            _profile(),
            asked=["What was genuinely hard at Acme?"],
            question="What did you achieve?",
            answer=ANSWER,
        )
    )
    assert turn.question.prompt == "How big was the team?"
    assert turn.question.why == "Gives the CV the scale of the role."
    assert turn.edits[0].value == "Led a team of 4 engineers"


def test_the_request_carries_the_profile_the_asked_questions_and_the_targets(monkeypatch):
    calls = _fake_litellm(monkeypatch, json.dumps({"question": {"prompt": "?"}}))
    asyncio.run(
        interview.ask(
            Settings(model="openai/gpt-4o"),
            _profile(),
            asked=["What was genuinely hard at Acme?"],
            question="What did you achieve?",
            answer=ANSWER,
        )
    )
    content = calls[0]["messages"][1]["content"]
    # The model must see the experience ids it may target, and the gaps: an empty
    # field appears in the profile view.
    assert "exp-acme-2022" in content
    assert "team_size:" in content
    assert "What was genuinely hard at Acme?" in content
    assert 'target "facts"' in content
    assert ANSWER in content


def test_the_request_marks_a_skipped_question(monkeypatch):
    calls = _fake_litellm(monkeypatch, json.dumps({"question": {"prompt": "?"}}))
    asyncio.run(
        interview.ask(
            Settings(model="openai/gpt-4o"),
            _profile(),
            asked=[],
            question="What was genuinely hard at Acme?",
        )
    )
    content = calls[0]["messages"][1]["content"]
    assert "skipped" in content


def test_the_request_says_it_is_the_first_question_of_a_visit(monkeypatch):
    calls = _fake_litellm(monkeypatch, json.dumps({"question": {"prompt": "?"}}))
    asyncio.run(interview.ask(Settings(model="openai/gpt-4o"), _profile(), asked=[]))
    assert "first question for this visit" in calls[0]["messages"][1]["content"]


# --- the turn itself -----------------------------------------------------------


def test_turn_normalises_the_question_it_remembers():
    turn = Turn(question=DraftQuestion(prompt="  How   big was the team?  "))
    assert turn.has_question
    assert turn.asked() == "How big was the team?"


def test_an_empty_turn_has_no_question():
    assert not Turn().has_question
