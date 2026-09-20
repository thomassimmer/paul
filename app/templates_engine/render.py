"""Rendering a document from a blueprint.

The user's file stays the base, so page size, margins, styles, theme, headers and
footers are preserved exactly. Only the body is rebuilt: for each line, the XML of
the block the line was modelled on is deep-copied and its text replaced, which
keeps fonts, colours, numbering, borders and hyperlinks without approximating
anything.
"""

from __future__ import annotations

from copy import deepcopy
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
    # A hyperlink is re-emitted with the rId the prototype already used, so its
    # target is the one of the base document. Checking the id here means a stale
    # blueprint degrades to plain text instead of a broken link.
    link_ids = {relation.rId for relation in document.part.rels.values()}

    for line in lines:
        xml = prototype_for(blueprint, line.role)
        if xml is None:
            paragraph = document.add_paragraph(line.text)
            element = paragraph._p
        else:
            element = parse_xml(xml)
            _fill(element, line.text, line.role, link_ids)
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


def _fill(paragraph, text: str, role: str, link_ids: set[str]) -> None:
    """Put ``text`` in a copied paragraph, re-using the runs' formatting.

    A paragraph collapses into a single run wearing the formatting of its own
    text: the other runs, tabs and images go. The paragraph properties stay,
    which is what keeps indentation, numbering, borders and shading.

    Hyperlinks are kept. An anchor the template linked — the e-mail of a contact
    line, a portfolio — is linked again when its text is still there, so the
    document keeps its clickable links without the writer ever knowing a URL. An
    anchor the new line no longer contains is left as text, and the rest of the
    line, separators included, is written plain.

    A skills line is the other exception. The template may style its category
    apart from its values ("Security:" in bold, the rest plain), and that
    contrast is worth keeping: the text is split where the template splits it, at
    the colon.
    """
    runs = list(paragraph.iter(qn("w:r")))
    plain = _plain_properties(paragraph, runs)
    links = _anchors(paragraph, link_ids) if role != "skill_line" else []
    category, values = _skill_styles(runs) if role == "skill_line" else (None, None)
    _clear(paragraph)

    if links:
        _append_linked(paragraph, text, plain, links)
        return
    if category is None and values is None:
        _append_run(paragraph, text, plain)
        return
    head, colon, tail = text.partition(":")
    if not colon:  # no category in the text: it is all values
        _append_run(paragraph, text, values)
        return
    _append_run(paragraph, head + colon, category)
    _append_run(paragraph, tail, values)


def _clear(paragraph) -> None:
    """Keep the paragraph properties, which carry the layout; drop the content."""
    for child in list(paragraph):
        if child.tag != qn("w:pPr"):
            paragraph.remove(child)


def _skill_styles(runs) -> tuple[object, object]:
    """The (category, values) formatting a skills line shows, when it shows both.

    The template's own text says which run holds the category: the one carrying
    the colon. The run that follows is the values' style. A line whose category
    and values share a single run has no contrast to keep, and is rendered plain.
    """
    colon = next((index for index, run in enumerate(runs) if ":" in _run_text(run)), None)
    if colon is None or colon + 1 >= len(runs):
        return None, None
    return _properties(runs[colon]), _properties(runs[colon + 1])


def _plain_properties(paragraph, runs):
    """The formatting of the paragraph's own text, not of a link inside it.

    A contact line often starts with a hyperlink; wearing that link's colour and
    underline for the separators would make the whole line look like one.
    """
    own = [child for child in paragraph if child.tag == qn("w:r")]
    if own:
        return _properties(own[0])
    return _properties(runs[0]) if runs else None


def _anchors(paragraph, link_ids: set[str]) -> list[tuple[str, str, object]]:
    """The prototype's hyperlinks, in order: rId, anchor text and formatting."""
    anchors: list[tuple[str, str, object]] = []
    for link in paragraph.iter(qn("w:hyperlink")):
        rid = link.get(qn("r:id"))
        text = _run_text(link)
        if not rid or not text or rid not in link_ids:
            continue
        runs = list(link.iter(qn("w:r")))
        anchors.append((rid, text, _properties(runs[0]) if runs else None))
    return anchors


def _append_linked(paragraph, text: str, plain, links: list[tuple[str, str, object]]) -> None:
    """Write the text, linking each anchor again where it still appears."""
    cursor = 0
    for rid, anchor, properties in links:
        index = text.find(anchor, cursor)
        if index < 0:  # the new line dropped or reworded that link
            continue
        if index > cursor:
            _append_run(paragraph, text[cursor:index], plain)
        _append_hyperlink(paragraph, anchor, rid, properties)
        cursor = index + len(anchor)
    if cursor < len(text):
        _append_run(paragraph, text[cursor:], plain)


def _properties(run):
    """A copy of a run's formatting, ready for a new run; ``None`` if it has none."""
    properties = run.find(qn("w:rPr"))
    return deepcopy(properties) if properties is not None else None


def _run_text(run) -> str:
    return "".join(node.text or "" for node in run.iter(qn("w:t")))


def _append_run(paragraph, text: str, properties) -> None:
    paragraph.append(_run(text, properties))


def _append_hyperlink(paragraph, text: str, rid: str, properties) -> None:
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    link.append(_run(text, properties))
    paragraph.append(link)


def _run(text: str, properties):
    """A run wearing a copy of ``properties``.

    The copy matters: a contact line writes several runs — separators and links —
    that share one set of properties, and an element cannot have two parents.
    """
    run = OxmlElement("w:r")
    if properties is not None:
        run.append(deepcopy(properties))
    text_element = OxmlElement("w:t")
    # Without this, Word collapses leading and trailing spaces.
    text_element.set(qn("xml:space"), "preserve")
    text_element.text = text
    run.append(text_element)
    return run
