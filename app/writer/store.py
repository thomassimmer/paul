"""Application folders on disk: ``data/applications/<year>-<month>-<company>-<role>/``.

One folder per prepared application, holding the offer it was made for, the
editable Markdown sources, the rendered DOCX and the ATS report — the layout
promised in the README, and the thing the user can read, back up and version
without the app.

Folder names are derived from the offer, so re-preparing an offer writes back into
its own folder instead of scattering duplicates; two offers that would collide
(for the same company, on the same role, in the same month) are numbered apart.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from app.ats import AtsReport
from app.config import DATA_DIR
from app.models import OfferRecord

APPLICATIONS_DIR = DATA_DIR / "applications"

OFFER_JSON = "offer.json"
OFFER_HTML = "offer.html"
CV_MD = "cv.md"
CV_DOCX = "cv.docx"
LETTER_MD = "letter.md"
LETTER_DOCX = "letter.docx"
ANSWERS_MD = "answers.md"
ATS_JSON = "ats.json"
NOTES_MD = "notes.md"

NOTES_HEADER = "# Notes\n"
NOTES_TEMPLATE = f"{NOTES_HEADER}\nYour notes, interview prep, anything to remember.\n"

# What the download route may serve, by file name. Everything else is refused.
DOWNLOADABLE = (CV_MD, CV_DOCX, LETTER_MD, LETTER_DOCX, ANSWERS_MD, ATS_JSON, NOTES_MD)


class FolderError(Exception):
    """The folder name or file name is not one this application owns."""


def slugify(value: str, *, max_length: int = 40) -> str:
    """A lower-case ASCII slug, empty when the text has nothing usable."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", without_accents.casefold()).strip("-")
    return slug[:max_length].rstrip("-")


def base_name(when: date, record: OfferRecord) -> str:
    """The folder name an offer deserves, before collision handling."""
    parts = [f"{when:%Y-%m}"]
    parts += [part for part in (slugify(record.offer.company), slugify(record.offer.title)) if part]
    if len(parts) == 1:
        parts.append("offer")
    return "-".join(parts)


def folder_path(name: str) -> Path:
    """The absolute path of a folder, refusing anything that could escape it."""
    safe = (name or "").strip()
    if not safe or safe in (".", "..") or "/" in safe or "\\" in safe or safe.startswith("."):
        raise FolderError(f"Invalid application folder: {name!r}")
    return APPLICATIONS_DIR / safe


def file_path(name: str, filename: str) -> Path:
    safe = (filename or "").strip()
    if not safe or "/" in safe or "\\" in safe or safe.startswith("."):
        raise FolderError(f"Invalid file name: {filename!r}")
    return folder_path(name) / safe


def folder_exists(name: str) -> bool:
    try:
        return folder_path(name).is_dir()
    except FolderError:
        return False


def create(name: str) -> Path:
    path = folder_path(name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def written_files(name: str) -> list[str]:
    """The files actually present, so the review page links only what exists."""
    path = folder_path(name)
    if not path.is_dir():
        return []
    return sorted(child.name for child in path.iterdir() if child.is_file())


def list_folders() -> list[str]:
    if not APPLICATIONS_DIR.is_dir():
        return []
    return sorted(child.name for child in APPLICATIONS_DIR.iterdir() if child.is_dir())


def write_text(name: str, filename: str, text: str) -> None:
    create(name)
    file_path(name, filename).write_text(text, encoding="utf-8")


def read_text(name: str, filename: str) -> str | None:
    path = file_path(name, filename)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def write_bytes(name: str, filename: str, data: bytes) -> None:
    create(name)
    file_path(name, filename).write_bytes(data)


def read_bytes(name: str, filename: str) -> bytes | None:
    path = file_path(name, filename)
    return path.read_bytes() if path.is_file() else None


def exists(name: str, filename: str) -> bool:
    return file_path(name, filename).is_file()


# --- The offer the folder was made for ----------------------------------------


def save_offer(name: str, record: OfferRecord, raw: str) -> None:
    """Keep the structured offer and the fragment exactly as it was pasted."""
    write_text(name, OFFER_JSON, record.model_dump_json(indent=2) + "\n")
    write_text(name, OFFER_HTML, raw)


def load_offer(name: str) -> OfferRecord | None:
    raw = read_text(name, OFFER_JSON)
    if raw is None:
        return None
    try:
        return OfferRecord.model_validate_json(raw)
    except ValueError:
        return None


def resolve_folder(record: OfferRecord, when: date) -> str:
    """This offer's folder: its own when it already has one, a free one otherwise."""
    base = base_name(when, record)
    candidate = base
    suffix = 2
    while _taken(candidate, record.id):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _taken(name: str, offer_id: int) -> bool:
    """True when the folder belongs to another offer — or to something unreadable.

    A folder we cannot read back is left alone: guessing that it is ours is how a
    half-written folder would lose its documents.
    """
    if not folder_exists(name):
        return False
    stored = load_offer(name)
    return stored is None or stored.id != offer_id


# --- Reports and notes ---------------------------------------------------------


def save_ats(name: str, report: AtsReport) -> None:
    write_text(name, ATS_JSON, report.model_dump_json(indent=2) + "\n")


def load_ats(name: str) -> AtsReport | None:
    raw = read_text(name, ATS_JSON)
    if raw is None:
        return None
    try:
        return AtsReport.model_validate_json(raw)
    except ValueError:
        return None


def ensure_notes(name: str) -> None:
    """Create the notes file on the first preparation, never overwrite it after."""
    if not exists(name, NOTES_MD):
        write_text(name, NOTES_MD, NOTES_TEMPLATE)
