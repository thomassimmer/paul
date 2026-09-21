"""Lifecycle of a preparation or a regeneration.

Three or four model calls plus a LibreOffice page measurement: far too long to hold
an HTTP request open, so the work runs as a background task and the page polls a
small state every two seconds. One process, one user: a single in-memory job is
enough, and losing it on a restart costs nothing, because the application folder on
disk is the source of truth.

A job is a list of named steps rather than a list of offers, unlike the ranker:
preparing one application is one long chain, and the interesting thing to show is
where in that chain the work currently stands.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.llm import LLMError
from app.models import OfferRecord, Profile
from app.templates_engine.extract import TemplateError
from app.writer import service, store

FINISHED_STATUSES = {"done", "error", "cancelled", "skipped"}

STATUS_LABELS = {
    "pending": "waiting",
    "running": "running",
    "done": "done",
    "error": "failed",
    "cancelled": "stopped",
    "skipped": "skipped",
}

KIND_LABELS = {"prepare": "Preparation", "regenerate": "Regeneration"}

# The steps of each kind of job, in order. ``service`` emits exactly these keys.
STEP_LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    "prepare": (
        ("offer", "Reading the offer and your profile"),
        ("cv", "Writing the CV, and fitting it to the page target"),
        ("letter", "Writing the cover letter, and fitting it"),
        ("answers", "Answering the form questions"),
        ("ats", "Checking the keyword coverage"),
        ("save", "Writing the application folder"),
    ),
    "regenerate": (
        ("draft", "Rewriting the section, and fitting it to the page target"),
        ("ats", "Refreshing the keyword coverage"),
    ),
}


@dataclass
class Step:
    key: str
    label: str
    status: str = "pending"
    detail: str = ""


@dataclass
class Job:
    kind: str  # "prepare" | "regenerate"
    offer_id: int
    title: str
    company: str
    steps: list[Step]
    section: str = ""
    instruction: str = ""
    from_current: bool = False
    folder: str = ""
    # Which sections a preparation was asked to write. A ``regenerate`` job ignores
    # it: it rewrites the one section it names.
    sections: tuple[str, ...] = service.SECTIONS
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    status: str = "running"  # running | done | error | cancelled
    cancelled: bool = False  # set by ``cancel()`` before the task observes it
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    result_folder: str = ""

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def done(self) -> int:
        return sum(1 for step in self.steps if step.status in FINISHED_STATUSES)

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def percent(self) -> int:
        return round(self.done / self.total * 100) if self.total else 100

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at

    @property
    def heading(self) -> str:
        label = KIND_LABELS.get(self.kind, self.kind)
        return f"{label} in progress…" if self.running else f"Last {label.lower()}"

    @property
    def subject(self) -> str:
        parts = [self.title or "Untitled offer"]
        if self.company:
            parts.append(self.company)
        if self.section:
            parts.append(service.SECTION_LABELS.get(self.section, self.section))
        return " · ".join(parts)

    @property
    def summary(self) -> str:
        if self.status == "error":
            return "failed"
        if self.status == "cancelled":
            return "stopped by you"
        return f"{self.done}/{self.total} steps"


_current: Job | None = None
_task: asyncio.Task | None = None


def current() -> Job | None:
    return _current


def remember(job: Job | None) -> Job | None:
    global _current
    _current = job
    return job


def reset() -> None:
    """Forget the last job. Used by the tests, and by the dismiss button."""
    global _task
    remember(None)
    _task = None


def cancel() -> bool:
    """Stop the running job. Nothing half-written: the folder is written last."""
    global _task
    if _current is None or not _current.running:
        return False
    _current.cancelled = True
    if _task is not None:
        _task.cancel()
    return True


def build_job(
    record: OfferRecord,
    *,
    kind: str,
    section: str = "",
    instruction: str = "",
    from_current: bool = False,
    folder: str = "",
    sections: tuple[str, ...] = service.SECTIONS,
) -> Job:
    """The step list for a job, before anything has happened."""
    labels = STEP_LABELS.get(kind, STEP_LABELS["prepare"])
    return Job(
        kind=kind,
        offer_id=record.id,
        title=record.offer.title or "Untitled offer",
        company=record.offer.company,
        steps=[Step(key=key, label=label) for key, label in labels],
        section=section,
        instruction=instruction,
        from_current=from_current,
        folder=folder,
        sections=sections,
    )


def _report_to(job: Job):
    def report(key: str, status: str, detail: str = "") -> None:
        for step in job.steps:
            if step.key == key:
                step.status = status
                if detail:
                    step.detail = detail
                return

    return report


def _close_steps(job: Job, status: str) -> None:
    """The step in flight becomes ``status``, the ones never reached are skipped."""
    for step in job.steps:
        if step.status == "running":
            step.status = status
        elif step.status == "pending":
            step.status = "skipped"


async def run(job: Job, settings: Settings, profile: Profile, record: OfferRecord) -> None:
    """Do the work, marking each step, and never leave the job unfinished."""
    try:
        if job.kind == "prepare":
            prepared = await service.prepare(
                settings, profile, record, sections=job.sections, on_step=_report_to(job)
            )
            job.result_folder = prepared.folder
            job.warnings = prepared.warnings
        else:
            await service.regenerate(
                settings,
                profile,
                record,
                folder=job.folder,
                section=job.section,
                instruction=job.instruction,
                warnings=job.warnings,
                from_current=job.from_current,
                on_step=_report_to(job),
            )
            job.result_folder = job.folder
        job.status = "done"
    except asyncio.CancelledError:
        job.status = "cancelled"
        _close_steps(job, "cancelled")
        raise
    except (LLMError, TemplateError, service.WriterError, store.FolderError) as exc:
        job.status = "error"
        job.error = str(exc)
        _close_steps(job, "error")
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensive
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        _close_steps(job, "error")
    finally:
        if job.finished_at is None:
            job.finished_at = time.monotonic()


async def start_job(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    *,
    kind: str,
    section: str = "",
    instruction: str = "",
    from_current: bool = False,
    folder: str = "",
    sections: tuple[str, ...] = service.SECTIONS,
) -> Job:
    """Register a job and run it in the background, so the request can return."""
    global _task
    job = remember(
        build_job(
            record,
            kind=kind,
            section=section,
            instruction=instruction,
            from_current=from_current,
            folder=folder,
            sections=sections,
        )
    )
    assert job is not None
    _task = asyncio.create_task(run(job, settings, profile, record))
    return job
