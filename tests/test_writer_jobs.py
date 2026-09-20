"""Tests for the background writer jobs: steps, progress, failure, cancellation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import LLMError
from app.models import OfferDraft, OfferRecord, Profile
from app.writer import jobs, service


def _record() -> OfferRecord:
    offer = OfferDraft(title="Senior Backend Engineer", company="Acme").to_offer([])
    return OfferRecord(id=1, analyzed_at="2026-09-19", source="text", offer=offer)


# --- the job skeleton ----------------------------------------------------------


def test_a_prepare_job_starts_with_all_its_steps_waiting():
    job = jobs.build_job(_record(), kind="prepare")
    assert [step.key for step in job.steps] == ["offer", "cv", "letter", "answers", "ats", "save"]
    assert all(step.status == "pending" for step in job.steps)
    assert job.done == 0
    assert job.percent == 0
    assert job.running is True


def test_a_regenerate_job_has_the_shorter_step_list():
    job = jobs.build_job(_record(), kind="regenerate", section="cv")
    assert [step.key for step in job.steps] == ["draft", "ats"]
    assert job.subject.endswith("CV")
    assert "Regeneration" in job.heading


def test_the_subject_names_the_offer():
    job = jobs.build_job(_record(), kind="prepare")
    assert job.subject == "Senior Backend Engineer · Acme"


# --- running -------------------------------------------------------------------


def _fake_prepare(monkeypatch, *, warnings=()):
    async def fake(
        settings: Settings,
        profile: Profile,
        record: OfferRecord,
        *,
        on_step: service.OnStep | None = None,
        today=None,
    ):
        assert on_step is not None, "the job must be told what is happening"
        for key in ("offer", "cv", "letter", "answers", "ats", "save"):
            on_step(key, "running", "")
            on_step(key, "done", f"{key} done")
        return SimpleNamespace(folder="2026-09-acme", warnings=list(warnings))

    monkeypatch.setattr("app.writer.jobs.service.prepare", fake)


def test_a_successful_run_marks_every_step_done(monkeypatch):
    _fake_prepare(monkeypatch, warnings=["the CV has 1 unverified line"])
    job = jobs.build_job(_record(), kind="prepare")

    asyncio.run(jobs.run(job, Settings(), Profile(), _record()))

    assert job.status == "done"
    assert job.running is False
    assert job.done == job.total
    assert job.percent == 100
    assert job.result_folder == "2026-09-acme"
    assert job.warnings == ["the CV has 1 unverified line"]
    assert job.steps[1].detail == "cv done"
    assert job.finished_at is not None


def test_a_failing_run_is_reported_and_the_rest_is_skipped(monkeypatch):
    async def failing(
        settings: Settings,
        profile: Profile,
        record: OfferRecord,
        *,
        on_step: service.OnStep | None = None,
        today=None,
    ):
        assert on_step is not None
        on_step("offer", "running", "")
        raise LLMError("provider is down")

    monkeypatch.setattr("app.writer.jobs.service.prepare", failing)
    job = jobs.build_job(_record(), kind="prepare")

    asyncio.run(jobs.run(job, Settings(), Profile(), _record()))

    assert job.status == "error"
    assert job.error == "provider is down"
    assert job.steps[0].status == "error"
    assert all(step.status == "skipped" for step in job.steps[1:])
    assert job.summary == "failed"


def test_an_unexpected_error_keeps_its_type_name(monkeypatch):
    async def broken(
        settings: Settings,
        profile: Profile,
        record: OfferRecord,
        *,
        on_step: service.OnStep | None = None,
        today=None,
    ):
        raise ZeroDivisionError("boom")

    monkeypatch.setattr("app.writer.jobs.service.prepare", broken)
    job = jobs.build_job(_record(), kind="prepare")

    asyncio.run(jobs.run(job, Settings(), Profile(), _record()))

    assert job.status == "error"
    assert job.error == "ZeroDivisionError: boom"


def test_a_cancelled_run_is_marked_cancelled(monkeypatch):
    async def cancelling(
        settings: Settings,
        profile: Profile,
        record: OfferRecord,
        *,
        on_step: service.OnStep | None = None,
        today=None,
    ):
        assert on_step is not None
        on_step("offer", "running", "")
        raise asyncio.CancelledError()

    monkeypatch.setattr("app.writer.jobs.service.prepare", cancelling)
    job = jobs.build_job(_record(), kind="prepare")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(jobs.run(job, Settings(), Profile(), _record()))

    assert job.status == "cancelled"
    assert job.steps[0].status == "cancelled"
    assert job.summary == "stopped by you"


def test_a_regeneration_runs_the_service_with_the_stored_instruction(monkeypatch):
    seen: dict = {}

    async def fake_regenerate(settings, profile, record, **kwargs):
        seen.update(kwargs)
        kwargs["on_step"]("draft", "running", "")
        kwargs["on_step"]("draft", "done", "1 page(s)")
        kwargs["on_step"]("ats", "running", "")
        kwargs["on_step"]("ats", "done", "80% coverage")

    monkeypatch.setattr("app.writer.jobs.service.regenerate", fake_regenerate)
    job = jobs.build_job(
        _record(),
        kind="regenerate",
        section="letter",
        instruction="shorter",
        from_current=True,
        folder="2026-09-acme",
    )

    asyncio.run(jobs.run(job, Settings(), Profile(), _record()))

    assert job.status == "done"
    assert seen["folder"] == "2026-09-acme"
    assert seen["section"] == "letter"
    assert seen["instruction"] == "shorter"
    assert seen["from_current"] is True  # the job carries the choice to the service
    assert job.result_folder == "2026-09-acme"
    assert job.done == job.total


# --- the single-job state ------------------------------------------------------


def test_cancel_does_nothing_without_a_running_job():
    jobs.reset()
    assert jobs.cancel() is False


def test_cancel_marks_the_job():
    job = jobs.remember(jobs.build_job(_record(), kind="prepare"))
    assert job is not None

    assert jobs.cancel() is True
    assert job.cancelled is True

    job.status = "done"
    assert jobs.cancel() is False


def test_reset_forgets_the_job():
    jobs.remember(jobs.build_job(_record(), kind="prepare"))
    assert jobs.current() is not None
    jobs.reset()
    assert jobs.current() is None
