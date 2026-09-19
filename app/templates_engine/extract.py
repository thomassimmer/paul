"""Reading a DOCX into blocks.

A block is one non-empty paragraph, whether it sits in the body or inside a table
cell, and it comes with the XML that produced it. Keeping that XML is what makes
rendering faithful: the renderer never rebuilds a style, it re-uses the element
the user's own document already had.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# Enough to describe any real CV; past that the role prompt would be wasteful.
MAX_BLOCKS = 120
MAX_TEXT = 200


class TemplateError(Exception):
    """The file cannot be read as a DOCX, for a reason we can explain."""


@dataclass(frozen=True)
class ExtractedBlock:
    """One paragraph, ready to be shown in the preview and re-used for rendering."""

    text: str
    xml: str
    hint: str


def extract_blocks(docx_bytes: bytes) -> list[ExtractedBlock]:
    try:
        document = Document(BytesIO(docx_bytes))
    except Exception as exc:
        raise TemplateError(
            f"Could not read this file as a .docx ({type(exc).__name__}). "
            "A Word .doc has to be saved as .docx first."
        ) from exc

    blocks: list[ExtractedBlock] = []
    for paragraph in _iter_paragraphs(document):
        text = " ".join(paragraph.text.split())
        if not text:
            continue  # an empty paragraph is spacing, and spacing is rebuilt
        blocks.append(
            ExtractedBlock(text=text[:MAX_TEXT], xml=paragraph._p.xml, hint=_hint(paragraph))
        )
        if len(blocks) >= MAX_BLOCKS:
            break
    if not blocks:
        raise TemplateError("This document has no text to analyse.")
    return blocks


def _iter_paragraphs(document):
    """Paragraphs in document order, table cells included."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            seen: set[int] = set()
            for row in Table(child, document).rows:
                for cell in row.cells:
                    # A merged cell is yielded once per grid position: read it once.
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    for paragraph in cell.paragraphs:
                        yield paragraph


def _hint(paragraph: Paragraph) -> str:
    """A short description of how the paragraph looks, for the role prompt."""
    parts: list[str] = []
    style = paragraph.style.name if paragraph.style is not None else ""
    if style and style.lower() != "normal":
        parts.append(style)

    for run in paragraph.runs:
        if not run.text.strip():
            continue
        if run.bold:
            parts.append("bold")
        if run.italic:
            parts.append("italic")
        size = run.font.size.pt if run.font.size is not None else None
        if size:
            parts.append(f"{size:g}pt")
        break

    if _is_list(paragraph):
        parts.append("list")
    if paragraph.paragraph_format.alignment is not None:
        parts.append(str(paragraph.paragraph_format.alignment).rsplit(" ", 1)[-1].lower())
    return ", ".join(parts)


def _is_list(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr
    if properties is not None and properties.numPr is not None:
        return True
    name = paragraph.style.name if paragraph.style is not None else ""
    return "list" in (name or "").lower()
