from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from app import llm
from app.config import Settings
from app.models import Experience, Profile
from app.profiler import service, structure
from app.profiler.ids import assign_ids
from app.profiler.structure import StructuredAchievement

ANSWER = (
    "J'ai réduit la latence d'ingestion de 60% avec un consumer group Kafka.\n"
    "J'ai aussi migré 40 services vers un pipeline partagé."
)


def _item(**overrides: str | list[str]) -> StructuredAchievement:
    base: dict = {
        "text": "Réduit la latence d'ingestion de 60% avec un consumer group Kafka.",
        "source": "J'ai réduit la latence d'ingestion de 60% avec un consumer group Kafka.",
        "metrics": ["60%"],
        "skills": ["Kafka"],
    }
    base.update(overrides)
    return StructuredAchievement(**base)


# --- the guard, on its own ---------------------------------------------------


def test_accepts_a_grounded_breakdown():
    accepted = structure.accept_achievements([_item()], ANSWER)
    assert accepted is not None
    assert accepted[0].text.startswith("Réduit la latence")
    assert accepted[0].metrics == ["60%"]
    assert accepted[0].skills == ["Kafka"]


def test_rejects_a_source_that_is_not_in_the_answer():
    assert structure.accept_achievements([_item(source="Something Else entirely")], ANSWER) is None


def test_rejects_a_missing_source():
    assert structure.accept_achievements([_item(source="")], ANSWER) is None


def test_rejects_a_number_the_answer_does_not_state():
    # "in half" was turned into "50%": that is an invented fact.
    item = _item(
        text="Divisé la latence par deux, soit 50% de moins.",
        metrics=["50%"],
        skills=[],
    )
    assert structure.accept_achievements([item], ANSWER) is None


def test_the_whole_batch_is_rejected_when_one_item_is_ungrounded():
    items = [
        _item(),
        _item(text="Migré 40 services.", source="A sentence that is not there", metrics=[], skills=[]),
    ]
    # Losing one item would silently drop part of what the candidate wrote.
    assert structure.accept_achievements(items, ANSWER) is None


def test_drops_a_metric_the_answer_does_not_contain_but_keeps_the_achievement():
    accepted = structure.accept_achievements([_item(metrics=["60%", "80%"])], ANSWER)
    assert accepted is not None
    assert accepted[0].metrics == ["60%"]


def test_drops_an_inferred_skill_but_keeps_the_achievement():
    # The answer never mentions Kubernetes: adding it would be an invention.
    accepted = structure.accept_achievements([_item(skills=["Kafka", "Kubernetes"])], ANSWER)
    assert accepted is not None
    assert accepted[0].skills == ["Kafka"]


def test_matching_ignores_case_accents_and_thousand_separators():
    answer = "Réduit la latence pour 1 200 utilisateurs actifs."
    item = StructuredAchievement(
        text="Réduit la latence pour 1 200 utilisateurs actifs",
        source="Réduit la latence pour 1 200 utilisateurs actifs",
        metrics=["1200 utilisateurs"],
        skills=[],
    )
    accepted = structure.accept_achievements([item], answer)
    assert accepted is not None
    assert accepted[0].metrics == ["1200 utilisateurs"]


def test_normalises_a_decimal_comma_when_comparing_numbers():
    answer = "Réduit les coûts de 1,2 M€."
    item = StructuredAchievement(
        text="Réduit les coûts de 1,2 M€",
        source="Réduit les coûts de 1,2 M€",
        metrics=[],
        skills=[],
    )
    assert structure.accept_achievements([item], answer) is not None


def test_an_empty_breakdown_falls_back():
    assert structure.accept_achievements([], ANSWER) is None


# --- the LLM call ------------------------------------------------------------


def _fake_litellm(monkeypatch, payload: str) -> list[dict]:
    calls: list[dict] = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        message = types.SimpleNamespace(content=payload)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("litellm")
    setattr(module, "acompletion", acompletion)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


def _payload(*items: dict) -> str:
    return json.dumps({"achievements": list(items)})


def test_structure_achievements_parses_and_guards(monkeypatch):
    _fake_litellm(
        monkeypatch,
        _payload(
            {
                "text": "Réduit la latence d'ingestion de 60% avec un consumer group Kafka.",
                "source": "J'ai réduit la latence d'ingestion de 60% avec un consumer group Kafka.",
                "metrics": ["60%"],
                "skills": ["Kafka", "Kubernetes"],
            }
        ),
    )
    experience = Experience(id="exp-acme-2022", company="Acme", title="Engineer")
    result = asyncio.run(
        structure.structure_achievements(
            Settings(model="openai/gpt-4o"), answer=ANSWER, experience=experience
        )
    )
    assert result is not None
    assert result[0].skills == ["Kafka"]  # Kubernetes was not in the answer


def test_structure_achievements_returns_none_on_an_ungrounded_answer(monkeypatch):
    _fake_litellm(
        monkeypatch,
        _payload({"text": "Invented", "source": "not in the answer", "metrics": [], "skills": []}),
    )
    result = asyncio.run(
        structure.structure_achievements(
            Settings(model="openai/gpt-4o"),
            answer=ANSWER,
            experience=Experience(id="exp-acme-2022"),
        )
    )
    assert result is None


def test_structure_achievements_without_a_model_does_not_call_out(monkeypatch):
    calls = _fake_litellm(monkeypatch, _payload())
    result = asyncio.run(
        structure.structure_achievements(Settings(), answer=ANSWER, experience=Experience())
    )
    assert result is None
    assert calls == []


# --- orchestration -----------------------------------------------------------


def _profile() -> Profile:
    return assign_ids(Profile(experiences=[Experience(company="Acme", period="2022")]))


def test_apply_interview_answer_uses_the_structured_result(monkeypatch):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:achievements"

    async def fake(settings, *, answer, experience):
        return [structure.Achievement(text="From the model", metrics=["60%"], skills=["Kafka"])]

    monkeypatch.setattr("app.profiler.structure.structure_achievements", fake)
    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(model="openai/gpt-4o"), profile, key, ANSWER)
    )

    assert notice is None
    achievements = updated.experiences[0].achievements
    assert [a.text for a in achievements] == ["From the model"]
    assert achievements[0].id == "exp-acme-2022-a1"


def test_apply_interview_answer_falls_back_when_nothing_is_grounded(monkeypatch):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:achievements"

    async def fake(settings, *, answer, experience):
        return None

    monkeypatch.setattr("app.profiler.structure.structure_achievements", fake)
    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(model="openai/gpt-4o"), profile, key, ANSWER)
    )

    assert notice is not None and "verified" in notice
    # The candidate's own lines are kept, verbatim.
    assert len(updated.experiences[0].achievements) == 2
    assert updated.experiences[0].achievements[0].metrics == ["60%"]


def test_apply_interview_answer_falls_back_when_the_model_fails(monkeypatch):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:achievements"

    async def failing(settings, *, answer, experience):
        raise llm.LLMError("provider is down")

    monkeypatch.setattr("app.profiler.structure.structure_achievements", failing)
    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(model="openai/gpt-4o"), profile, key, ANSWER)
    )

    assert notice is not None and "provider is down" in notice
    assert len(updated.experiences[0].achievements) == 2


def test_apply_interview_answer_without_a_model_stays_plain(monkeypatch):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:achievements"

    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(), profile, key, ANSWER)
    )

    assert notice is None
    assert len(updated.experiences[0].achievements) == 2


def test_apply_interview_answer_leaves_other_fields_alone(monkeypatch):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:context"

    async def fake(settings, *, answer, experience):  # pragma: no cover - must not run
        raise AssertionError("structuring must only run for achievements")

    monkeypatch.setattr("app.profiler.structure.structure_achievements", fake)
    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(model="openai/gpt-4o"), profile, key, "Analytics")
    )

    assert notice is None
    assert updated.experiences[0].context == "Analytics"


@pytest.mark.parametrize("answer", ["", "   "])
def test_apply_interview_answer_ignores_a_blank_answer(monkeypatch, answer):
    profile = _profile()
    key = f"exp:{profile.experiences[0].id}:achievements"
    updated, notice = asyncio.run(
        service.apply_interview_answer(Settings(model="openai/gpt-4o"), profile, key, answer)
    )
    assert notice is None
    assert updated.experiences[0].achievements == []
