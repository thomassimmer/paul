"""Rankings in SQLite.

One row per ranked offer. Kept apart from ``offers`` because a ranking is our
judgement of an offer, not what the offer says — and because a manual override
has to survive a re-run of the rules.

Only one connection and one statement per write: the ranker touches this table
once per offer, and there is no reason to read a row back just to return it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import db
from app.models import Elimination, RankingRecord, Score

OVERRIDES = ("", "kept", "eliminated")


def _now() -> str:
    """A timestamp in the same shape SQLite's ``datetime('now')`` produces."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _record(row) -> RankingRecord:
    raw_score = row["score_json"]
    return RankingRecord(
        offer_id=row["offer_id"],
        elimination=Elimination(
            eliminated=bool(row["eliminated"]),
            rule=row["rule"],
            excerpt=row["excerpt"],
        ),
        override=row["override"],
        score=Score.model_validate_json(raw_score) if raw_score else None,
        fingerprint=row["fingerprint"],
        scored_at=row["scored_at"],
    )


def save_ranking(
    offer_id: int,
    *,
    elimination: Elimination,
    override: str,
    score: Score | None,
    fingerprint: str = "",
) -> RankingRecord:
    db.init_db()
    scored_at = _now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO rankings
                (offer_id, eliminated, rule, excerpt, override, score_json, fingerprint, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                eliminated  = excluded.eliminated,
                rule        = excluded.rule,
                excerpt     = excluded.excerpt,
                override    = excluded.override,
                score_json  = excluded.score_json,
                fingerprint = excluded.fingerprint,
                scored_at   = excluded.scored_at
            """,
            (
                offer_id,
                int(elimination.eliminated),
                elimination.rule,
                elimination.excerpt,
                override,
                score.model_dump_json() if score is not None else None,
                fingerprint,
                scored_at,
            ),
        )
    return RankingRecord(
        offer_id=offer_id,
        elimination=elimination,
        override=override,
        score=score,
        fingerprint=fingerprint,
        scored_at=scored_at,
    )


def set_override(offer_id: int, override: str) -> RankingRecord:
    """Record a manual decision. Eliminating drops the score: it no longer applies."""
    if override not in OVERRIDES:
        raise ValueError(f"Unknown override: {override!r}")
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO rankings (offer_id, override, score_json, scored_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                override   = excluded.override,
                score_json = CASE WHEN excluded.override = 'eliminated'
                                  THEN NULL ELSE rankings.score_json END
            """,
            (offer_id, override, _now()),
        )
    record = load_ranking(offer_id)
    assert record is not None  # just written
    return record


def load_ranking(offer_id: int) -> RankingRecord | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM rankings WHERE offer_id = ?", (offer_id,)).fetchone()
    return _record(row) if row is not None else None


def list_rankings() -> dict[int, RankingRecord]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM rankings").fetchall()
    return {row["offer_id"]: _record(row) for row in rows}


def delete_ranking(offer_id: int) -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM rankings WHERE offer_id = ?", (offer_id,))
