"""Where templates live: ``data/templates/``.

A template is two files: the DOCX itself (the base the renderer keeps) and the
blueprint read from it. Dropping the DOCX from the folder is all it takes to go
back to the default.
"""

from __future__ import annotations

from pathlib import Path

from app.config import DATA_DIR
from app.models import TemplateBlock, TemplateBlueprint
from app.templates_engine import default
from app.templates_engine.extract import ExtractedBlock, TemplateError, extract_blocks
from app.templates_engine.roles import ROLES, guess_roles

TEMPLATES_DIR = DATA_DIR / "templates"


def custom_path(kind: str) -> Path:
    return TEMPLATES_DIR / f"{kind}.docx"


def blueprint_path(kind: str) -> Path:
    return TEMPLATES_DIR / f"{kind}.json"


def has_custom(kind: str) -> bool:
    return custom_path(kind).exists()


def save_custom(kind: str, docx_bytes: bytes, blueprint: TemplateBlueprint) -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    custom_path(kind).write_bytes(docx_bytes)
    blueprint_path(kind).write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")


def delete_custom(kind: str) -> None:
    custom_path(kind).unlink(missing_ok=True)
    blueprint_path(kind).unlink(missing_ok=True)


def base_document(kind: str) -> bytes:
    """The user's file when there is one, the generated default otherwise."""
    path = custom_path(kind)
    if path.exists():
        return path.read_bytes()
    return default.build(kind)[0]


def load_blueprint(kind: str) -> TemplateBlueprint:
    """The stored blueprint, rebuilt from the DOCX if the JSON is missing."""
    path = blueprint_path(kind)
    if path.exists() and custom_path(kind).exists():
        try:
            return TemplateBlueprint.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TemplateError(f"The stored blueprint of the {kind} is unreadable: {exc}") from exc
    if not custom_path(kind).exists():
        return default.build(kind)[1]
    return blueprint_from_blocks(kind, extract_blocks(custom_path(kind).read_bytes()))


def custom_blocks(kind: str) -> list[ExtractedBlock]:
    """The blocks of the user's own file, for the role-correction page."""
    path = custom_path(kind)
    if not path.exists():
        raise TemplateError(f"No {kind} template was imported.")
    return extract_blocks(path.read_bytes())


def blueprint_from_blocks(
    kind: str, blocks: list[ExtractedBlock], roles: list[str] | None = None
) -> TemplateBlueprint:
    if roles is None:
        roles = guess_roles([block.text for block in blocks], [block.hint for block in blocks], kind)
    return TemplateBlueprint(
        kind=kind,
        blocks=[
            TemplateBlock(role=role, text=block.text, xml=block.xml)
            for block, role in zip(blocks, roles, strict=False)
        ],
    )


def save_roles(kind: str, roles: list[str]) -> TemplateBlueprint:
    """Store corrected roles, re-deriving the blocks so the XML stays exact."""
    blocks = custom_blocks(kind)
    allowed = ROLES.get(kind, ())
    cleaned = [role if role in allowed else "body_text" for role in roles]
    while len(cleaned) < len(blocks):
        cleaned.append("body_text")
    blueprint = blueprint_from_blocks(kind, blocks, cleaned[: len(blocks)])
    blueprint_path(kind).parent.mkdir(parents=True, exist_ok=True)
    blueprint_path(kind).write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
    return blueprint
