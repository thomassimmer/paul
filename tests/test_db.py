from __future__ import annotations

from app import db


def test_init_db_creates_the_tables():
    db.init_db()
    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"meta", "interview_skipped", "offers", "rankings", "applications"} <= tables


def test_init_db_adds_a_column_a_previous_version_did_not_have():
    # A database created before ``fingerprint`` existed: CREATE TABLE IF NOT
    # EXISTS would leave it as it is, so init_db has to migrate it.
    with db.connect() as conn:
        conn.executescript(
            "CREATE TABLE rankings ("
            "  offer_id INTEGER PRIMARY KEY,"
            "  eliminated INTEGER NOT NULL DEFAULT 0,"
            "  rule TEXT NOT NULL DEFAULT '',"
            "  excerpt TEXT NOT NULL DEFAULT '',"
            "  override TEXT NOT NULL DEFAULT '',"
            "  score_json TEXT,"
            "  scored_at TEXT NOT NULL DEFAULT ''"
            ");"
        )

    db.init_db()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(rankings)")}
    assert {"offer_id", "score_json", "fingerprint"} <= columns


def test_init_db_is_idempotent():
    db.init_db()
    db.init_db()  # a second start must not fail on the migration
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row is not None
    assert int(row["value"]) == db.SCHEMA_VERSION
