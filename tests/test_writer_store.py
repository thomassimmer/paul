"""Tests for the application folders on disk."""

from __future__ import annotations

from datetime import date

import pytest

from app.ats import AtsReport, AtsKeyword
from app.models import Keyword, OfferDraft, OfferRecord
from app.writer import store

WHEN = date(2026, 9, 19)


def _record(offer_id: int = 1, *, title: str = "Senior Backend Engineer", company: str = "Acme") -> OfferRecord:
    offer = OfferDraft(title=title, company=company, keywords=[Keyword(term="Rust")]).to_offer([])
    return OfferRecord(id=offer_id, analyzed_at="2026-09-19", source="text", offer=offer)


# --- naming --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Acme Corp", "acme-corp"),
        ("École Nationale", "ecole-nationale"),
        ("  C++ / Rust  ", "c-rust"),
        ("", ""),
    ],
)
def test_slugify(value, expected):
    assert store.slugify(value) == expected


def test_base_name_is_the_month_then_the_offer():
    assert store.base_name(WHEN, _record()) == "2026-09-acme-senior-backend-engineer"


def test_base_name_without_a_company_or_a_title():
    assert store.base_name(WHEN, _record(title="", company="")) == "2026-09-offer"


def test_a_folder_is_reused_for_the_same_offer():
    record = _record(1)
    name = store.resolve_folder(record, WHEN)
    store.save_offer(name, record, "<p>raw</p>")
    assert store.resolve_folder(record, WHEN) == name


def test_a_colliding_offer_gets_its_own_folder():
    first, second = _record(1), _record(2)
    name = store.resolve_folder(first, WHEN)
    store.save_offer(name, first, "")
    other = store.resolve_folder(second, WHEN)
    assert other == f"{name}-2"
    assert other != name


def test_a_folder_without_a_readable_offer_is_left_alone():
    name = store.resolve_folder(_record(1), WHEN)
    store.create(name)  # an empty folder is not provably ours
    assert store.resolve_folder(_record(1), WHEN) == f"{name}-2"


@pytest.mark.parametrize("name", ["..", ".", "", "a/b", "../etc", ".hidden"])
def test_a_dangerous_folder_name_is_refused(name):
    with pytest.raises(store.FolderError):
        store.folder_path(name)


@pytest.mark.parametrize("filename", ["..", "", "a/b", ".env"])
def test_a_dangerous_file_name_is_refused(filename):
    with pytest.raises(store.FolderError):
        store.file_path("ok", filename)


# --- the offer the folder was made for ----------------------------------------


def test_the_offer_and_the_raw_fragment_are_stored_and_read_back():
    record = _record()
    name = store.resolve_folder(record, WHEN)
    store.save_offer(name, record, "<html>offer</html>")

    stored = store.load_offer(name)
    assert stored is not None
    assert stored.id == record.id
    assert stored.offer.title == "Senior Backend Engineer"
    assert store.read_text(name, store.OFFER_HTML) == "<html>offer</html>"


def test_loading_a_folder_without_an_offer_returns_none():
    assert store.load_offer("unknown") is None


# --- reports and files ---------------------------------------------------------


def test_the_ats_report_round_trips():
    report = AtsReport(
        coverage_percent=50,
        keywords=[AtsKeyword(term="Rust", present=True)],
        format_issues=["A table is present."],
    )
    store.save_ats("app", report)
    stored = store.load_ats("app")
    assert stored is not None
    assert stored.coverage_percent == 50
    assert stored.keywords[0].term == "Rust"
    assert stored.format_issues == ["A table is present."]


def test_a_corrupt_ats_report_reads_as_absent():
    store.write_text("app", store.ATS_JSON, "{ not json")
    assert store.load_ats("app") is None


def test_notes_are_created_once_and_never_overwritten():
    store.ensure_notes("app")
    written = store.read_text("app", store.NOTES_MD)
    assert written and "Your notes" in written

    store.write_text("app", store.NOTES_MD, "# Notes\n\nMy own note\n")
    store.ensure_notes("app")
    assert store.read_text("app", store.NOTES_MD) == "# Notes\n\nMy own note\n"


def test_written_files_lists_what_exists():
    assert store.written_files("app") == []
    store.write_text("app", store.CV_MD, "x")
    store.write_text("app", store.ATS_JSON, "{}")
    assert store.written_files("app") == [store.ATS_JSON, store.CV_MD]


def test_the_download_whitelist_covers_the_documents():
    for name in (store.CV_DOCX, store.CV_MD, store.LETTER_DOCX, store.LETTER_MD, store.ANSWERS_MD):
        assert name in store.DOWNLOADABLE
