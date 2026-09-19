"""SQLite access: one process, one database file under ``data/``.

Settings live in ``data/settings.json``; the database is for the domain data
(offers, applications) that the next modules add. For now it only records its
own schema version, which is enough to initialise the Docker volume on first run.
"""

from __future__ import annotations

import sqlite3

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "paul.sqlite3"

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    """Create the schema. Safe to call on every start."""
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
