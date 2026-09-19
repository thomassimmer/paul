"""SQLite access: one process, one database file under ``data/``.

The profile itself lives in ``data/profile/profile.yaml``; the database holds
what does not belong in a readable file, starting with the interview's skipped
questions. Domain tables (offers, applications) arrive with the next modules.
"""

from __future__ import annotations

import sqlite3

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "paul.sqlite3"

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Questions the user chose to skip during the profiler interview. Skipping is
-- UI state, not profile data, so it lives here and never touches the YAML.
CREATE TABLE IF NOT EXISTS interview_skipped (
    key        TEXT PRIMARY KEY,
    skipped_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    """Open the database, creating the data directory if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the schema. Safe to call on every start and from tests."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
