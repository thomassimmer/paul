"""LiteLLM wrapper.

Two entry points:

- ``test_connection`` for the settings page;
- ``complete_structured``: every LLM call in the app goes through it and returns
  a validated Pydantic object, retried once with the validation error when the
  model answers something invalid (README design rule: "every LLM call returns a
  validated Pydantic object").

Importing LiteLLM is deferred until a call is actually made: it is a heavy
dependency and the app must start (and its pages must render) without it.
"""

from __future__ import annotations

import json
import re
import time

from pydantic import BaseModel, ValidationError

from app.config import Settings


class LLMError(RuntimeError):
    """No usable answer could be obtained from the configured model."""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

TEST_PROMPT = "Reply with the single word: ok"

# A rejected credential is the one provider failure the user can fix in Settings,
# so it gets a message that says where. Every other provider error is shown
# verbatim: the raw text is usually the point (unknown model, quota, outage).
_AUTH_HINTS = (
    "authenticationerror",
    "authentication_error",
    "authentication fails",
    "invalid api key",
    "incorrect api key",
    "invalid x-api-key",
    "unauthorized",
    "401",
)

AUTH_ERROR = (
    "The model refused to sign in: check the model, the API key and the API base "
    "in Settings, then try again."
)


def _describe(exc: Exception) -> str:
    """Provider error text, with an unreachable model turned into an actionable one."""
    text = f"{type(exc).__name__}: {exc}"
    if any(hint in text.lower() for hint in _AUTH_HINTS):
        return AUTH_ERROR
    return text


def _import_litellm():
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise LLMError(f"LiteLLM is not installed: {exc}") from exc
    litellm.suppress_debug_info = True
    litellm.drop_params = True
    return litellm


def _completion_kwargs(settings: Settings, *, timeout: float) -> dict:
    model = settings.model.strip()
    if not model:
        raise LLMError("No model configured. Set one in Settings, for example `anthropic/claude-sonnet-4-5`.")
    kwargs: dict = {"model": model, "timeout": timeout}
    if settings.api_key.strip():
        kwargs["api_key"] = settings.api_key.strip()
    if settings.api_base.strip():
        kwargs["api_base"] = settings.api_base.strip()
    return kwargs


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a model answer, code fences and prose aside."""
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object found in the answer")
    return text[start : end + 1]


async def _complete(litellm, settings: Settings, messages: list[dict], *, timeout: float) -> str:
    kwargs = _completion_kwargs(settings, timeout=timeout)
    kwargs.update(messages=messages, temperature=0)
    try:
        response = await litellm.acompletion(**kwargs)
    except LLMError:
        raise
    except Exception as exc:  # provider errors are shown verbatim, they are the point
        raise LLMError(_describe(exc)) from exc
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError) as exc:
        raise LLMError("The model returned no message.") from exc


async def complete_structured[T: BaseModel](
    settings: Settings,
    *,
    schema: type[T],
    content: str,
    system: str | None = None,
    retries: int = 1,
    timeout: float = 180.0,
) -> T:
    """Ask the model for a JSON object validating against ``schema``.

    On an invalid answer, retry once by default, telling the model what was
    wrong. Raises ``LLMError`` when no attempt produced a valid object.
    """
    litellm = _import_litellm()
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(
        {
            "role": "user",
            "content": (
                f"{content}\n\n---\n"
                "Answer with a single JSON object that validates against this "
                f"JSON Schema. No prose, no code fence.\n{schema_json}"
            ),
        }
    )

    last_error: Exception | None = None
    for _ in range(retries + 1):
        answer = await _complete(litellm, settings, messages, timeout=timeout)
        try:
            return schema.model_validate_json(_extract_json(answer))
        except (ValidationError, ValueError) as exc:
            last_error = exc
            messages = [
                *messages,
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": f"That did not validate: {exc}. Answer again with corrected JSON only.",
                },
            ]

    raise LLMError(f"The model did not return a valid {schema.__name__}: {last_error}")


async def test_connection(settings: Settings, *, timeout: float = 30.0) -> tuple[bool, str]:
    """Send a minimal completion to the configured model.

    Returns ``(ok, message)``; ``message`` is shown as-is to the user.
    """
    model = settings.model.strip()
    if not model:
        return False, "No model configured. Set one such as `anthropic/claude-sonnet-4-5`."

    try:
        litellm = _import_litellm()
    except LLMError as exc:
        return False, str(exc)

    started = time.perf_counter()
    try:
        reply = await _complete(
            litellm,
            settings,
            [{"role": "user", "content": TEST_PROMPT}],
            timeout=timeout,
        )
    except LLMError as exc:
        return False, str(exc)

    elapsed_ms = (time.perf_counter() - started) * 1000
    return True, f"Connected to {model} in {elapsed_ms:.0f} ms. Reply: {reply.strip() or '(empty)'}"
