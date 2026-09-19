"""Lifecycle of a ranking run.

Twenty offers mean up to forty model calls: too long to hold an HTTP request
open. A run is therefore a background task with a small state the page can poll.
One process, one user: a single in-memory job is enough, and losing it on a
restart costs nothing, because every offer already ranked is stored in SQLite.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.models import OfferRecord, Profile, RankingRecord
from app.ranking import service

FINISHED_STATUSES = {"scored", "eliminated", "error", "cancelled"}
STATUS_LABELS = {
    "pending": "waiting",
    "running": "running",
    "scored": "scored",
    "eliminated": "eliminated",
    "error": "failed",
    "cancelled": "stopped",
}


@dataclass
class JobOffer:
    """One line of the report the page shows as the run progresses."""

    offer_id: int
    title: str
    company: str
    status: str = "pending"
    total: int | None = None
    error: str = ""


@dataclass
class Job:
    scope: str
    concurrency: int
    offers: list[JobOffer]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    cancelled: bool = False
    error: str = ""

    @property
    def total(self) -> int:
        return len(self.offers)

    @property
    def done(self) -> int:
        return sum(1 for offer in self.offers if offer.status in FINISHED_STATUSES)

    @property
    def running(self) -> bool:
        return self.finished_at is None

    @property
    def scored(self) -> int:
        return sum(1 for offer in self.offers if offer.status == "scored")

    @property
    def eliminated(self) -> int:
        return sum(1 for offer in self.offers if offer.status == "eliminated")

    @property
    def errors(self) -> int:
        return sum(1 for offer in self.offers if offer.status == "error")

    @property
    def percent(self) -> int:
        return round(self.done / self.total * 100) if self.total else 100

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        """A rough estimate from what has already been done."""
        if not self.running or self.done == 0:
            return None
        return self.elapsed / self.done * (self.total - self.done)

    @property
    def summary(self) -> str:
        parts = [f"{self.total} offer(s)", f"{self.scored} scored", f"{self.eliminated} eliminated"]
        if self.errors:
            parts.append(f"{self.errors} failed")
        if self.cancelled:
            parts.append("stopped by you")
        return " — ".join(parts)


_current: Job | None = None
_tasks: set[asyncio.Task] = set()


def current() -> Job | None:
    return _current


def remember(job: Job | None) -> Job | None:
    global _current
    _current = job
    return job


def reset() -> None:
    """Forget the last job. Used by the tests, and by the dismiss button."""
    remember(None)


def cancel() -> bool:
    """Ask the running job to stop: in-flight calls finish, the rest is skipped."""
    if _current is None or not _current.running:
        return False
    _current.cancelled = True
    return True


async def run(
    job: Job,
    settings: Settings,
    profile: Profile,
    records: list[OfferRecord],
    rankings: dict[int, RankingRecord],
) -> None:
    """Work through ``records`` with a bounded number of calls in flight."""
    by_id = {record.id: record for record in records}
    semaphore = asyncio.Semaphore(max(1, job.concurrency))

    async def work(state: JobOffer) -> None:
        async with semaphore:
            if job.cancelled:
                state.status = "cancelled"
                return
            state.status = "running"
            record = by_id[state.offer_id]
            outcome = await service.rank_one(
                settings, profile, record, previous=rankings.get(record.id)
            )
            state.status = outcome.status
            state.total = outcome.total
            state.error = outcome.error

    try:
        await asyncio.gather(*(work(state) for state in job.offers))
    except Exception as exc:  # pragma: no cover - defensive
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        if job.finished_at is None:
            job.finished_at = time.monotonic()


def build_job(
    records: list[OfferRecord], *, scope: str, concurrency: int
) -> Job:
    """The report skeleton for a run, before anything has happened."""
    return Job(
        scope=scope,
        concurrency=max(1, concurrency),
        offers=[
            JobOffer(
                offer_id=record.id,
                title=record.offer.title or "Untitled offer",
                company=record.offer.company,
            )
            for record in records
        ],
    )


async def start_job(
    settings: Settings,
    profile: Profile,
    records: list[OfferRecord],
    rankings: dict[int, RankingRecord],
    *,
    scope: str,
    concurrency: int,
) -> Job:
    """Register a job and run it in the background, so the request can return."""
    job = remember(build_job(records, scope=scope, concurrency=concurrency))
    assert job is not None
    task = asyncio.create_task(run(job, settings, profile, records, rankings))
    _tasks.add(task)  # keep a reference: a bare task can be garbage-collected
    task.add_done_callback(_tasks.discard)
    return job
