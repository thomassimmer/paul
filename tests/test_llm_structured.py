from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app import llm
from app.config import Settings
from app.models import ProfileDraft


def _fake_litellm(monkeypatch, *answers: str) -> list[dict]:
    """Install a fake litellm returning ``answers`` in order; record the calls."""
    queued = list(answers)
    calls: list[dict] = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        content = queued.pop(0) if queued else ""
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("litellm")
    monkeypatch.setattr(module, "acompletion", acompletion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


VALID = '{"identity": {"name": "Camille"}, "experiences": [{"company": "Acme"}]}'


def test_returns_a_validated_model(monkeypatch):
    _fake_litellm(monkeypatch, VALID)
    draft = asyncio.run(
        llm.complete_structured(Settings(model="openai/gpt-4o"), schema=ProfileDraft, content="cv")
    )
    assert isinstance(draft, ProfileDraft)
    assert draft.identity.name == "Camille"
    assert draft.experiences[0].company == "Acme"


def test_retries_once_with_the_validation_error(monkeypatch):
    calls = _fake_litellm(monkeypatch, "not json at all", VALID)

    draft = asyncio.run(
        llm.complete_structured(Settings(model="openai/gpt-4o"), schema=ProfileDraft, content="cv")
    )

    assert draft.identity.name == "Camille"
    assert len(calls) == 2
    # The retry tells the model what was wrong.
    retry_messages = calls[1]["messages"]
    assert "did not validate" in retry_messages[-1]["content"]


def test_parses_answers_wrapped_in_a_code_fence(monkeypatch):
    _fake_litellm(monkeypatch, f"Sure!\n```json\n{VALID}\n```\n")
    draft = asyncio.run(
        llm.complete_structured(Settings(model="openai/gpt-4o"), schema=ProfileDraft, content="cv")
    )
    assert draft.identity.name == "Camille"


def test_gives_up_after_the_last_retry(monkeypatch):
    _fake_litellm(monkeypatch, "nope", "still nope")
    with pytest.raises(llm.LLMError, match="did not return a valid ProfileDraft"):
        asyncio.run(
            llm.complete_structured(Settings(model="openai/gpt-4o"), schema=ProfileDraft, content="cv")
        )


def test_provider_errors_become_llm_errors(monkeypatch):
    async def acompletion(**kwargs):
        raise RuntimeError("no such model")

    module = types.ModuleType("litellm")
    monkeypatch.setattr(module, "acompletion", acompletion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", module)

    with pytest.raises(llm.LLMError, match="RuntimeError: no such model"):
        asyncio.run(
            llm.complete_structured(Settings(model="openai/gpt-4o"), schema=ProfileDraft, content="cv")
        )


def test_without_a_model_it_fails_fast(monkeypatch):
    _fake_litellm(monkeypatch, VALID)
    with pytest.raises(llm.LLMError, match="No model configured"):
        asyncio.run(llm.complete_structured(Settings(), schema=ProfileDraft, content="cv"))
