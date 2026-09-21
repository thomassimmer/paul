"""Background runs for the single-call flows.

A model call takes seconds, sometimes minutes: holding an HTTP request open for it
makes the page hang with no sign of life. The writer and the ranker already run in
the background with their own job modules, because they have several steps and a
report line per offer. This is the small shared piece for the flows that are one
call and a save: the route starts a run and returns at once, and the page it came
from polls a tiny state until the work is done.

The local, synchronous half of each flow (cleaning the pasted text, reading the
uploaded file) stays in the request, so an input the user can fix is still
reported on the page immediately; only the model call and what follows it are
dispatched here.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class Run:
    """The state of one background run, small enough to render as a card."""

    kind: str
    label: str
    # What the run is about, so a page can tell whether it is its own: e.g.
    # ``{"offer_id": 12}``. Set by the caller when it starts the run.
    context: dict = field(default_factory=dict)
    status: str = "running"  # running | done | error
    # The success message, shown as a flash once the page navigates to the result.
    message: str = ""
    level: str = "ok"
    error: str = ""
    # Where the page should go once the work succeeded. Empty means "stay here".
    return_url: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at


# One run per kind: one process, one user, and two analyses of the same kind
# never make sense at once. Starting a run replaces the previous one of that kind.
_runs: dict[str, Run] = {}
_tasks: set[asyncio.Task] = set()


def current(kind: str) -> Run | None:
    return _runs.get(kind)


def live(kind: str) -> Run | None:
    """The run to show, if there is one worth showing.

    A finished run is not shown again: a success navigates to its result and is
    then forgotten, and an error is shown by the poll that sees it end. Keeping
    only the running one means no stale card survives a reload.
    """
    run = _runs.get(kind)
    return run if run is not None and run.running else None


def build(kind: str, label: str, *, context: dict | None = None) -> Run:
    return Run(kind=kind, label=label, context=dict(context or {}))


def remember(run: Run) -> Run:
    _runs[run.kind] = run
    return run


def clear(kind: str) -> None:
    _runs.pop(kind, None)


def reset() -> None:
    """Forget every run. Used by the tests."""
    _runs.clear()


async def execute(run: Run, work: Callable[[Run], Awaitable[None]]) -> None:
    """Run ``work``, recording how it ended. Never raises for a failed run."""
    try:
        await work(run)
        run.status = "done"
    except asyncio.CancelledError:
        run.status = "error"
        run.error = "Stopped."
        raise
    except Exception as exc:  # noqa: BLE001 - provider and domain errors are shown verbatim
        run.status = "error"
        run.error = str(exc) or type(exc).__name__
    finally:
        if run.finished_at is None:
            run.finished_at = time.monotonic()


async def start(
    kind: str,
    label: str,
    work: Callable[[Run], Awaitable[None]],
    *,
    context: dict | None = None,
) -> Run:
    """Register a run and start it in the background, so the request can return.

    Awaited rather than merely called so a test can replace it with an inline run
    (the writer's ``start_job`` follows the same shape for the same reason).
    """
    run = remember(build(kind, label, context=context))
    task = asyncio.create_task(execute(run, work))
    _tasks.add(task)  # keep a reference: a bare task can be garbage-collected
    task.add_done_callback(_tasks.discard)
    return run
