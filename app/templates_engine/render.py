"""Rendering a document from a blueprint.

The user's file stays the base, so page size, margins, styles, theme, headers and
footers are preserved exactly. Only the body is rebuilt: for each line, the XML of
the block the line was modelled on is deep-copied and its text replaced, which
keeps fonts, colours, numbering and borders without approximating anything.
"""

from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from app.models import DraftLine, TemplateBlueprint
from app.templates_engine.roles import prototype_for


def render(base_docx: bytes, blueprint: TemplateBlueprint, lines: list[DraftLine]) -> bytes:
    """Return a DOCX with ``lines`` laid out in the blueprint's style."""
    document = Document(BytesIO(base_docx))
    _clear_body(document)
    section_properties = document.element.body.find(qn("w:sectPr"))

    for line in lines:
        xml = prototype_for(blueprint, line.role)
        if xml is None:
            paragraph = document.add_paragraph(line.text)
            element = paragraph._p
        else:
            element = parse_xml(xml)
            _fill(element, line.text)
        if section_properties is not None:
            section_properties.addprevious(element)
        else:
            document.element.body.append(element)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _clear_body(document: DocumentType) -> None:
    """Drop the sample content, keeping the section properties (page setup)."""
    body = document.element.body
    for child in list(body.iterchildren()):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def _fill(paragraph, text: str) -> None:
    """Put ``text`` in a copied paragraph, re-using the first run's formatting.

    Everything else goes: the other runs, hyperlink wrappers, tabs and images.
    The paragraph properties stay, which is what keeps indentation, numbering,
    borders and shading.
    """
    runs = list(paragraph.iter(qn("w:r")))
    template_run = runs[0] if runs else None
    if template_run is not None:
        template_run.getparent().remove(template_run)

    for child in list(paragraph):
        if child.tag != qn("w:pPr"):
            paragraph.remove(child)

    run = template_run if template_run is not None else OxmlElement("w:r")
    for child in list(run):
        if child.tag != qn("w:rPr"):
            run.remove(child)
    paragraph.append(run)

    text_element = OxmlElement("w:t")
    # Without this, Word collapses leading and trailing spaces.
    text_element.set(qn("xml:space"), "preserve")
    text_element.text = text
    run.append(text_element)
