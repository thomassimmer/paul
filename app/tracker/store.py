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
        folder=row["folder"],
        want_cv=bool(row["want_cv"]),
        want_letter=bool(row["want_letter"]),
        form_source=row["form_source"],
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
    folder: str | None = None,
    want_cv: bool | None = None,
    want_letter: bool | None = None,
    form_source: str | None = None,
) -> Application:
    """Write the row. A field left out is kept as it is.

    The tracker page saves the status and the notes without knowing anything about
    the writer's folder or the preparation plan, so ``None`` means "keep whatever
    is there" rather than "erase it": losing the link to the generated documents,
    or the form the user pasted, on a status change would be invisible and
    infuriating.
    """
    db.init_db()
    current = load_application(offer_id)
    if folder is None:
        folder = current.folder if current is not None else ""
    if want_cv is None:
        want_cv = current.want_cv if current is not None else True
    if want_letter is None:
        want_letter = current.want_letter if current is not None else True
    if form_source is None:
        form_source = current.form_source if current is not None else ""
    updated_at = service.today_utc().isoformat()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO applications
                (offer_id, status, applied_on, last_contact, notes, folder,
                 want_cv, want_letter, form_source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                status       = excluded.status,
                applied_on   = excluded.applied_on,
                last_contact = excluded.last_contact,
                notes        = excluded.notes,
                folder       = excluded.folder,
                want_cv      = excluded.want_cv,
                want_letter  = excluded.want_letter,
                form_source  = excluded.form_source,
                updated_at   = excluded.updated_at
            """,
            (
                offer_id,
                status,
                applied_on,
                last_contact,
                notes,
                folder,
                int(want_cv),
                int(want_letter),
                form_source,
                updated_at,
            ),
        )
    return Application(
        offer_id=offer_id,
        status=status,
        applied_on=applied_on,
        last_contact=last_contact,
        notes=notes,
        folder=folder,
        want_cv=want_cv,
        want_letter=want_letter,
        form_source=form_source,
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


def set_folder(offer_id: int, folder: str) -> Application:
    """Remember where the writer put the documents for this offer."""
    current = load_application(offer_id) or Application(offer_id=offer_id)
    return save_application(
        offer_id,
        status=current.status,
        applied_on=current.applied_on,
        last_contact=current.last_contact,
        notes=current.notes,
        folder=folder,
    )


def set_plan(
    offer_id: int, *, want_cv: bool, want_letter: bool, form_source: str
) -> Application:
    """Remember what applying to this offer requires.

    Written when the preparation modal is saved, so reopening it shows the choices
    made last time rather than the defaults.
    """
    current = load_application(offer_id) or Application(offer_id=offer_id)
    return save_application(
        offer_id,
        status=current.status,
        applied_on=current.applied_on,
        last_contact=current.last_contact,
        notes=current.notes,
        want_cv=want_cv,
        want_letter=want_letter,
        form_source=form_source,
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
