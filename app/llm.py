"""LiteLLM wrapper.

For now this only holds the settings page's "test connection" check. The
structured-output and retry helpers described in the README land with the
profiler and offer analyzer.
"""

from __future__ import annotations

import time

from app.config import Settings

TEST_PROMPT = "Reply with the single word: ok"


async def test_connection(settings: Settings, *, timeout: float = 30.0) -> tuple[bool, str]:
    """Send a minimal completion to the configured model.

    Returns ``(ok, message)``; ``message`` is shown as-is to the user.
    """
    model = settings.model.strip()
    if not model:
        return False, "No model configured. Set one such as `anthropic/claude-sonnet-4-5`."

    # Imported lazily: LiteLLM is heavy and only needed when a call is made.
    try:
        import litellm  # type: ignore[import-not-found]  # pragma: no cover
    except ImportError as exc:  # pragma: no cover - depends on the install
        return False, f"LiteLLM is not installed: {exc}"

    litellm.suppress_debug_info = True
    litellm.drop_params = True

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
        "max_tokens": 5,
        "temperature": 0,
        "timeout": timeout,
    }
    if settings.api_key.strip():
        kwargs["api_key"] = settings.api_key.strip()
    if settings.api_base.strip():
        kwargs["api_base"] = settings.api_base.strip()

    started = time.perf_counter()
    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as exc:  # provider errors are shown verbatim, they are the point
        return False, f"{type(exc).__name__}: {exc}"

    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        reply = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        reply = ""
    return True, f"Connected to {model} in {elapsed_ms:.0f} ms. Reply: {reply or '(empty)'}"
