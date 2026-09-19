"""Reading the roles of a template.

Two passes, cheapest first: the layout already says a lot (a bullet style is a
bullet, a short bold line is a section title), and the model settles the rest. If
no model is configured, or the answer does not line up with the blocks, the
guesses stand: a wrong role costs one click on the preview page.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import Settings
from app.llm import LLMError, complete_structured
from app.models import TemplateBlueprint
from app.prompts import load_prompt
from app.templates_engine import store
from app.templates_engine.extract import ExtractedBlock, extract_blocks
from app.templates_engine.roles import ROLES, guess_roles


class _BlockRole(BaseModel):
    index: int = 0
    role: str = ""


class _RoleList(BaseModel):
    blocks: list[_BlockRole] = Field(default_factory=list)


async def analyze(settings: Settings, docx_bytes: bytes, kind: str) -> TemplateBlueprint:
    """Read a template into a blueprint, with or without a model."""
    blocks = extract_blocks(docx_bytes)
    guesses = guess_roles([block.text for block in blocks], [block.hint for block in blocks], kind)
    roles = list(guesses)
    notes: list[str] = []

    if settings.model.strip():
        try:
            roles = await _ask_for_roles(settings, blocks, kind, guesses)
        except LLMError as exc:
            notes.append(f"The roles were guessed from the layout ({exc}).")
        else:
            kept = sum(1 for guess, role in zip(guesses, roles, strict=False) if guess != role)
            if kept:
                notes.append(f"The model corrected {kept} of the {len(roles)} blocks.")
    else:
        notes.append("No model configured: the roles were guessed from the layout only.")

    blueprint = store.blueprint_from_blocks(kind, blocks, roles)
    blueprint.notes = notes
    return blueprint


async def _ask_for_roles(
    settings: Settings, blocks: list[ExtractedBlock], kind: str, guesses: list[str]
) -> list[str]:
    answer = await complete_structured(
        settings,
        schema=_RoleList,
        content=_content(blocks, kind),
        system=load_prompt("templates/assign_roles.md"),
    )
    allowed = set(ROLES.get(kind, ()))
    roles = list(guesses)
    for item in answer.blocks:
        if 0 <= item.index < len(roles) and item.role in allowed:
            roles[item.index] = item.role
    return roles


def _content(blocks: list[ExtractedBlock], kind: str) -> str:
    document = "cover letter" if kind == "letter" else "CV"
    lines = [
        f"{index}. [{block.hint or 'no style'}] {block.text}"
        for index, block in enumerate(blocks)
    ]
    return (
        f"This is a {document} template with {len(blocks)} blocks.\n\n"
        + "\n".join(lines)
        + f"\n\nAllowed roles: {', '.join(ROLES.get(kind, ()))}\n"
    )
