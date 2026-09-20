"""What the settings page shows about templates, and what they accept.

Templates stopped being a page of their own: importing one, reading its roles and
resetting it are settings, so this module holds the two kinds that can be
imported and the small overview the settings page renders.
"""

from __future__ import annotations

from app.models import TemplateBlueprint
from app.templates_engine import store
from app.templates_engine.extract import TemplateError

# The two kinds of template, and the largest file we will read.
KINDS = {"cv": "CV", "letter": "Cover letter"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def kinds() -> list[dict]:
    """One entry per template kind, saying what is currently in use."""
    overview: list[dict] = []
    for kind, label in KINDS.items():
        blueprint: TemplateBlueprint | None = None
        error = ""
        if store.has_custom(kind):
            try:
                blueprint = store.load_blueprint(kind)
            except TemplateError as exc:
                error = str(exc)
        overview.append(
            {
                "kind": kind,
                "label": label,
                "custom": store.has_custom(kind),
                "count": len(blueprint.blocks) if blueprint else 0,
                "error": error,
            }
        )
    return overview
