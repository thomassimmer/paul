from __future__ import annotations

import asyncio
import sys
import types

from app import llm
from app.config import Settings


def _install_fake_litellm(monkeypatch, *, reply: str = "ok", error: Exception | None = None):
    """Replace the lazily imported ``litellm`` module and record the kwargs."""
    calls: dict = {}

    async def acompletion(**kwargs):
        calls.update(kwargs)
        if error is not None:
            raise error
        message = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("litellm")
    setattr(module, "acompletion", acompletion)
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


def test_missing_model():
    ok, message = asyncio.run(llm.test_connection(Settings()))
    assert ok is False
    assert "No model configured" in message


def test_success_reports_latency_and_reply(monkeypatch):
    calls = _install_fake_litellm(monkeypatch, reply="ok")
    settings = Settings(model="openai/gpt-4o", api_key="sk-1", api_base="http://x")
    ok, message = asyncio.run(llm.test_connection(settings))
    assert ok is True
    assert "Connected to openai/gpt-4o" in message
    assert "Reply: ok" in message
    assert calls["api_key"] == "sk-1"
    assert calls["api_base"] == "http://x"
    assert calls["temperature"] == 0


def test_local_model_omits_credentials(monkeypatch):
    calls = _install_fake_litellm(monkeypatch)
    ok, _ = asyncio.run(llm.test_connection(Settings(model="ollama/llama3")))
    assert ok is True
    assert "api_key" not in calls
    assert "api_base" not in calls


def test_provider_error_is_returned(monkeypatch):
    _install_fake_litellm(monkeypatch, error=RuntimeError("boom"))
    ok, message = asyncio.run(llm.test_connection(Settings(model="openai/gpt-4o")))
    assert ok is False
    assert "RuntimeError: boom" in message
