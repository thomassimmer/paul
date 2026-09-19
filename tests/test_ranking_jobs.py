from __future__ import annotations

import asyncio

from app.config import Settings
from app.models import OfferDraft, Profile
from app.offers import store as offers_store
from app.ranking import jobs, service

SETTINGS = Settings(model="openai/gpt-4o")
PROFILE = Profile()


def _records(count: int):
    for index in range(count):
        offers_store.save_offer(
            OfferDraft(title=f"Offer {index}", company="Acme").to_offer([]),
            raw="",
            cleaned="",
            source="text",
        )
    return offers_store.list_offers()


def _job(records, *, concurrency: int = 4, scope: str = "all") -> jobs.Job:
    return jobs.build_job(records, scope=scope, concurrency=concurrency)


def test_run_updates_every_line_of_the_report(monkeypatch):
    records = _records(3)

    async def fake_rank_one(settings, profile, record, *, previous=None):
        return service.RankOutcome("scored", total=60 + record.id)

    monkeypatch.setattr("app.ranking.service.rank_one", fake_rank_one)
    job = _job(records)

    asyncio.run(jobs.run(job, SETTINGS, PROFILE, records, {}))

    assert job.running is False
    assert job.finished_at is not None
    assert job.done == job.total == 3
    assert job.scored == 3
    assert {line.total for line in job.offers} == {61, 62, 63}
    assert job.percent == 100
    assert "3 offer(s)" in job.summary


def test_concurrency_is_bounded(monkeypatch):
    records = _records(6)
    in_flight = 0
    peak = 0

    async def fake_rank_one(settings, profile, record, *, previous=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return service.RankOutcome("scored", total=50)

    monkeypatch.setattr("app.ranking.service.rank_one", fake_rank_one)
    job = _job(records, concurrency=2)

    asyncio.run(jobs.run(job, SETTINGS, PROFILE, records, {}))

    assert peak == 2
    assert job.done == 6


def test_a_job_can_be_stopped(monkeypatch):
    records = _records(5)
    job = jobs.remember(_job(records, concurrency=1))
    assert job is not None
    calls: list[int] = []

    async def fake_rank_one(settings, profile, record, *, previous=None):
        calls.append(record.id)
        if len(calls) == 2:
            assert jobs.cancel() is True
        return service.RankOutcome("scored", total=50)

    monkeypatch.setattr("app.ranking.service.rank_one", fake_rank_one)

    asyncio.run(jobs.run(job, SETTINGS, PROFILE, records, {}))

    assert job.cancelled is True
    assert len(calls) == 2  # the calls already in flight finish, no new one starts
    statuses = [line.status for line in job.offers]
    assert statuses.count("scored") == 2
    assert statuses.count("cancelled") == 3
    assert job.done == 5
    assert "stopped by you" in job.summary


def test_errors_are_reported_per_offer_without_stopping_the_run(monkeypatch):
    records = _records(3)
    failing = records[1].id

    async def fake_rank_one(settings, profile, record, *, previous=None):
        if record.id == failing:
            return service.RankOutcome("error", error="provider exploded")
        return service.RankOutcome("eliminated")

    monkeypatch.setattr("app.ranking.service.rank_one", fake_rank_one)
    job = _job(records)

    asyncio.run(jobs.run(job, SETTINGS, PROFILE, records, {}))

    assert job.done == 3
    assert job.errors == 1
    assert job.eliminated == 2
    line = next(line for line in job.offers if line.offer_id == failing)
    assert line.status == "error"
    assert "provider exploded" in line.error
    assert "failed" in job.summary


def test_start_job_registers_the_job_and_runs_it_in_the_background(monkeypatch):
    records = _records(2)

    async def fake_rank_one(settings, profile, record, *, previous=None):
        return service.RankOutcome("scored", total=10)

    monkeypatch.setattr("app.ranking.service.rank_one", fake_rank_one)

    async def scenario():
        job = await jobs.start_job(
            SETTINGS, PROFILE, records, {}, scope="pending", concurrency=2
        )
        assert jobs.current() is job
        assert job.running is True
        for _ in range(200):  # let the scheduled task make progress
            if not job.running:
                break
            await asyncio.sleep(0)
        return job

    job = asyncio.run(scenario())
    assert job.running is False
    assert job.done == 2


def test_cancel_and_reset_are_harmless_without_a_job():
    jobs.reset()
    assert jobs.current() is None
    assert jobs.cancel() is False


def test_eta_is_only_offered_while_running():
    job = jobs.Job(scope="all", concurrency=1, offers=[jobs.JobOffer(1, "A", "B")])
    assert job.eta_seconds is None  # nothing done yet
    job.offers[0].status = "scored"
    assert job.eta_seconds is not None
    job.finished_at = job.started_at + 1
    assert job.eta_seconds is None
