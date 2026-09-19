"""Applications in SQLite.

One row per offer the user has touched. A missing row is not missing data: it
means the default status, so an analyzed offer is already on the board.
"""

from __future__ import annotations

from datetime import date

from app import db
from app.models import Application
from app.tracker import service


def _record(row) -> Application:
    return Application(
        offer_id=row["offer_id"],
        status=row["status"],
        applied_on=row["applied_on"],
        last_contact=row["last_contact"],
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


def load_application(offer_id: int) -> Application | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE offer_id = ?", (offer_id,)
        ).fetchone()
    return _record(row) if row is not None else None


def list_applications() -> dict[int, Application]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM applications").fetchall()
    return {row["offer_id"]: _record(row) for row in rows}


def save_application(
    offer_id: int,
    *,
    status: str,
    applied_on: str,
    last_contact: str,
    notes: str,
) -> Application:
    db.init_db()
    updated_at = service.today_utc().isoformat()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO applications (offer_id, status, applied_on, last_contact, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                status       = excluded.status,
                applied_on   = excluded.applied_on,
                last_contact = excluded.last_contact,
                notes        = excluded.notes,
                updated_at   = excluded.updated_at
            """,
            (offer_id, status, applied_on, last_contact, notes, updated_at),
        )
    return Application(
        offer_id=offer_id,
        status=status,
        applied_on=applied_on,
        last_contact=last_contact,
        notes=notes,
        updated_at=updated_at,
    )


def set_status(offer_id: int, status: str, *, today: date) -> Application:
    """Change the status only, keeping dates and notes.

    Moving to an applied status records today's date, so the follow-up clock
    starts by itself.
    """
    current = load_application(offer_id) or Application(offer_id=offer_id)
    status = service.normalize_status(status)
    applied_on, last_contact = service.next_dates(status, current, today)
    return save_application(
        offer_id,
        status=status,
        applied_on=applied_on,
        last_contact=last_contact,
        notes=current.notes,
    )


def applied_offer_ids() -> set[int]:
    """The offers an application was sent for: used by the ranker's scopes."""
    return {
        offer_id
        for offer_id, application in list_applications().items()
        if service.has_applied(application.status)
    }


def delete_application(offer_id: int) -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM applications WHERE offer_id = ?", (offer_id,))
