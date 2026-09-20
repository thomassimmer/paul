"""Tests for the shared background runs: state, success, failure, cancellation."""

from __future__ import annotations

import asyncio

import pytest

from app import background
from app.llm import LLMError


def test_a_run_starts_running_and_labelled():
    run = background.build("thing", "Doing the thing…")
    assert run.running is True
    assert run.status == "running"
    assert run.label == "Doing the thing…"
    assert run.elapsed >= 0


def test_remember_current_and_clear():
    run = background.remember(background.build("thing", "Doing the thing…"))
    assert background.current("thing") is run
    background.clear("thing")
    assert background.current("thing") is None


def test_live_hides_a_finished_run():
    run = background.remember(background.build("thing", "Doing the thing…"))
    assert background.live("thing") is run

    run.status = "done"
    # A finished run is not shown again: only what is still working is.
    assert background.live("thing") is None
    assert background.current("thing") is run

    run.status = "error"
    assert background.live("thing") is None


def test_execute_marks_a_successful_run_done():
    run = background.remember(background.build("thing", "Doing the thing…"))

    async def work(state: background.Run) -> None:
        state.message = "All good."
        state.return_url = "/somewhere"

    asyncio.run(background.execute(run, work))

    assert run.status == "done"
    assert run.running is False
    assert run.message == "All good."
    assert run.return_url == "/somewhere"
    assert run.finished_at is not None


def test_execute_reports_a_domain_error_verbatim():
    run = background.remember(background.build("thing", "Doing the thing…"))

    async def work(state: background.Run) -> None:
        raise LLMError("provider is down")

    asyncio.run(background.execute(run, work))

    assert run.status == "error"
    assert run.error == "provider is down"
    assert run.finished_at is not None


def test_execute_falls_back_to_the_type_name_when_there_is_no_message():
    run = background.remember(background.build("thing", "Doing the thing…"))

    async def work(state: background.Run) -> None:
        raise RuntimeError()

    asyncio.run(background.execute(run, work))

    assert run.error == "RuntimeError"


def test_execute_marks_a_cancelled_run_and_re_raises():
    run = background.remember(background.build("thing", "Doing the thing…"))

    async def work(state: background.Run) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(background.execute(run, work))

    assert run.status == "error"
    assert run.error == "Stopped."


def test_start_schedules_the_work_and_returns_the_run():
    async def scenario() -> background.Run:
        async def work(state: background.Run) -> None:
            state.message = "finished"

        run = await background.start("thing", "Doing the thing…", work)
        assert run.running is True  # the request returned before the work ran
        for _ in range(10):  # let the scheduled task run
            if not run.running:
                break
            await asyncio.sleep(0)
        return run

    run = asyncio.run(scenario())

    assert run.status == "done"
    assert run.message == "finished"


def test_reset_forgets_every_run():
    background.remember(background.build("one", "One"))
    background.remember(background.build("two", "Two"))
    background.reset()
    assert background.current("one") is None
    assert background.current("two") is None
