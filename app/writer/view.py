"""The review context: everything the document sections of a page need.

Kept out of the routes because two places render the same sections: the offer page
and its polling endpoint. Building the context in one function is what keeps the
first render and the refreshed one identical, so a running job cannot make the
page lose a button.
"""

from __future__ import annotations

from app.config import load_settings
from app.models import OfferRecord, Profile
from app.templates_engine import pdf
from app.templates_engine.roles import ROLE_LABELS, ROLES
from app.writer import jobs, service

# The roles a line can be added under by hand: everything the renderer knows, save
# ``fixed``, which is a decorative block kept untouched rather than typed.
INSERTABLE_ROLES = {
    kind: tuple(role for role in roles if role != "fixed") for kind, roles in ROLES.items()
}


def context(
    profile: Profile, record: OfferRecord, folder: str, *, poll_url: str, next_url: str
) -> dict:
    """The template variables the review body reads, for one prepared offer."""
    return {
        "record": record,
        "offer": record.offer,
        "folder": folder,
        "job": jobs.current(),
        "status_labels": jobs.STATUS_LABELS,
        "poll_url": poll_url,
        "next_url": next_url,
        "target": load_settings().target_pages,
        "review": service.load_review(profile, record, folder),
        # Without LibreOffice the review screen keeps the plain HTML preview.
        "pdf_preview": pdf.converter() is not None,
        # The buttons that add a line to each editor without typing its role.
        "cv_roles": INSERTABLE_ROLES["cv"],
        "letter_roles": INSERTABLE_ROLES["letter"],
        "role_labels": ROLE_LABELS,
    }
