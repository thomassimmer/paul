"""SQLite access: one process, one database file under ``data/``.

The profile itself lives in ``data/profile/profile.yaml``; the database holds
what does not belong in a readable file, starting with the interview's skipped
questions. Domain tables (offers, applications) arrive with the next modules.
"""

from __future__ import annotations

import sqlite3

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "paul.sqlite3"

SCHEMA_VERSION = 7

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

-- Analyzed offers. The raw fragment and the cleaned text are kept so the
-- analysis can be re-run later with a better model or updated rules.
CREATE TABLE IF NOT EXISTS offers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
    source      TEXT NOT NULL DEFAULT '',
    raw         TEXT NOT NULL DEFAULT '',
    cleaned     TEXT NOT NULL DEFAULT '',
    offer_json  TEXT NOT NULL
);

-- Filter and ranker: one row per ranked offer. Kept apart from ``offers`` because
-- it is our judgement of an offer, not what the offer says, and because a manual
-- override has to survive a re-run of the rules.
CREATE TABLE IF NOT EXISTS rankings (
    offer_id    INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    eliminated  INTEGER NOT NULL DEFAULT 0,
    rule        TEXT NOT NULL DEFAULT '',
    excerpt     TEXT NOT NULL DEFAULT '',
    override    TEXT NOT NULL DEFAULT '',
    score_json  TEXT,
    fingerprint TEXT NOT NULL DEFAULT '',
    scored_at   TEXT NOT NULL DEFAULT ''
);

-- Tracker: one row per application the user has touched. No row means the
-- default status, so analyzing an offer already puts it on the board.
CREATE TABLE IF NOT EXISTS applications (
    offer_id     INTEGER PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'analyzed',
    applied_on   TEXT NOT NULL DEFAULT '',
    last_contact TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    folder       TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT ''
);
"""

# Added after the first release of the table: ``CREATE TABLE IF NOT EXISTS``
# cannot add a column to a database that already has the table.
_COLUMN_MIGRATIONS = {
    "rankings": {"fingerprint": "TEXT NOT NULL DEFAULT ''"},
    "applications": {"folder": "TEXT NOT NULL DEFAULT ''"},
}


def connect() -> sqlite3.Connection:
    """Open the database, creating the data directory if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the schema and apply the column migrations. Safe on every start."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # the table was just created, it is already up to date
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
