"""Profiler storage.

The profile is a single readable YAML file under ``data/profile/``; the raw CV
text is kept next to it so the draft can be re-run with a better model. Skipped
interview questions are UI state and live in SQLite, never in the YAML.
"""

from __future__ import annotations

import yaml

from app import db
from app.config import DATA_DIR
from app.models import Profile
from app.profiler.ids import assign_ids

PROFILE_DIR = DATA_DIR / "profile"
PROFILE_PATH = PROFILE_DIR / "profile.yaml"
CV_TEXT_PATH = PROFILE_DIR / "cv.txt"

_HEADER = (
    "# Paul profile — a plain file you own. Edit it by hand if you like.\n"
    "# Ids are stable and cited by the writer: never renumber an existing one.\n"
)


class ProfileError(Exception):
    """The profile file exists but cannot be read back."""


def load_profile() -> Profile | None:
    """Return the stored profile, or ``None`` before the first import.

    Ids are filled in memory when the file lacks them; nothing is written back
    here, so a read never surprises you with a modification.
    """
    if not PROFILE_PATH.exists():
        return None
    try:
        raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"Cannot read {PROFILE_PATH}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"{PROFILE_PATH} must contain a YAML mapping.")
    try:
        profile = Profile.model_validate(raw)
    except Exception as exc:
        raise ProfileError(f"{PROFILE_PATH} is not a valid profile: {exc}") from exc
    return assign_ids(profile)


def save_profile(profile: Profile) -> Profile:
    """Write the profile, filling in any missing ids first."""
    profile = assign_ids(profile)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        profile.model_dump(mode="json", exclude_defaults=True),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    PROFILE_PATH.write_text(_HEADER + body, encoding="utf-8")
    return profile


def delete_profile() -> None:
    PROFILE_PATH.unlink(missing_ok=True)


def save_cv_text(text: str) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    CV_TEXT_PATH.write_text(text, encoding="utf-8")


def load_cv_text() -> str | None:
    if not CV_TEXT_PATH.exists():
        return None
    return CV_TEXT_PATH.read_text(encoding="utf-8")


# --- Interview state (SQLite) -------------------------------------------------


def skipped_keys() -> set[str]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT key FROM interview_skipped").fetchall()
    return {row["key"] for row in rows}


def skip_key(key: str) -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO interview_skipped (key) VALUES (?)", (key,))


def clear_skips() -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM interview_skipped")
