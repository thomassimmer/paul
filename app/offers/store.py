"""Offers in SQLite.

The extracted offer is stored as JSON next to the raw fragment and the cleaned
text, so an analysis can be re-run later with a better model or a tighter prompt
without asking the user to paste the page again. The database is not the source
of truth for the *input*, which is why both are kept verbatim.
"""

from __future__ import annotations

from app import db
from app.models import Offer, OfferRecord


def _record(row) -> OfferRecord:
    return OfferRecord(
        id=row["id"],
        analyzed_at=row["analyzed_at"],
        source=row["source"],
        url=row["url"],
        offer=Offer.model_validate_json(row["offer_json"]),
    )


def save_offer(
    offer: Offer, *, raw: str, cleaned: str, source: str, url: str = ""
) -> OfferRecord:
    db.init_db()
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO offers (source, url, raw, cleaned, offer_json) VALUES (?, ?, ?, ?, ?)",
            (source, url, raw, cleaned, offer.model_dump_json()),
        )
        offer_id = int(cursor.lastrowid or 0)
    record = load_offer(offer_id)
    assert record is not None  # just inserted
    return record


def list_offers() -> list[OfferRecord]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM offers ORDER BY id DESC").fetchall()
    return [_record(row) for row in rows]


def load_offer(offer_id: int) -> OfferRecord | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    return _record(row) if row is not None else None


def count_offers() -> int:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM offers").fetchone()
    return int(row["n"]) if row is not None else 0


def load_source(offer_id: int) -> tuple[str, str] | None:
    """Return ``(raw, cleaned)`` as pasted and as cleaned."""
    db.init_db()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT raw, cleaned FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()
    return (row["raw"], row["cleaned"]) if row is not None else None


def update_offer(offer_id: int, offer: Offer, *, url: str | None = None) -> None:
    """Replace the extracted offer. ``url`` is only touched when it is given.

    A re-analysis replaces the offer but knows nothing about the posting link, so
    ``None`` means "keep whatever is there" rather than "erase it": losing the
    link would be invisible and would defeat the point of saving it.
    """
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "UPDATE offers SET offer_json = ? WHERE id = ?",
            (offer.model_dump_json(), offer_id),
        )
        if url is not None:
            conn.execute("UPDATE offers SET url = ? WHERE id = ?", (url, offer_id))


def delete_offer(offer_id: int) -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
