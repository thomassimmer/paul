"""The editable form of a generated document.

One line per block, prefixed by its role, with the profile ids it cites in braces::

    [name] Camille Moreau
    [section_title] Experience
    [entry_title] Acme — Lead Backend Engineer
    [bullet] Cut ingestion latency by 60% {exp-acme-2022-a1}

That is readable enough to be an export, and explicit enough to be read back, so
the review screen edits this text and the renderer gets exactly the lines that
produced it. A cover letter uses the same shape with its own roles.

The citation is metadata, never part of the text: it is lifted out here and at
normalisation, so no ``{id}`` can reach the rendered document.
"""

from __future__ import annotations

import html
import re

from app.models import DraftLine, FormAnswer

HEADER = "<!-- Paul: one line per block, in order. Keep the [role] prefixes. -->"
ANSWERS_HEADER = "<!-- Paul: one '# question' block per form field, answers below. -->"
_EMPTY_ANSWER = "_To fill in._"

_LINE = re.compile(r"^\[(?P<role>[a-z_]+)\]\s*(?P<text>.*)$")
# An id always carries a hyphen (``exp-acme-2022-a1``), which is what tells a
# citation from a brace the prose legitimately uses, such as ``{braces}``.
_CITATION = re.compile(r"\{(?P<ids>[^{}]*-[^{}]*)\}")
_SPACES = re.compile(r"[ \t]{2,}")


def split_citations(text: str) -> tuple[str, list[str]]:
    """Separate the ``{id, id}`` citations from the prose they are glued to.

    The format puts a citation at the end of a line, but a model revising a
    document sometimes leaves one inside the text, or writes it twice — once in
    the text and once in the ``achievement_ids`` field. Both mean the same, so
    every citation is read here and only the prose is handed back.
    """
    ids: list[str] = []
    kept: list[str] = []
    cursor = 0
    for match in _CITATION.finditer(text or ""):
        kept.append(text[cursor : match.start()])
        ids.extend(item.strip() for item in match.group("ids").split(",") if item.strip())
        cursor = match.end()
    kept.append((text or "")[cursor:])
    return _SPACES.sub(" ", "".join(kept)).strip(), _unique(ids)


def _unique(ids: list[str]) -> list[str]:
    """The ids in the order they were written, each kept once."""
    seen: set[str] = set()
    kept: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            kept.append(item)
    return kept


def render_lines(lines: list[DraftLine]) -> str:
    body = []
    for line in lines:
        citation = ""
        if line.achievement_ids:
            citation = " {" + ", ".join(line.achievement_ids) + "}"
        body.append(f"[{line.role}] {line.text}{citation}".rstrip())
    return "\n".join([HEADER, "", *body]) + "\n"


def source_only(text: str) -> str:
    """The editable lines of a stored document, without the header comment.

    What is handed back to the model as "the current version": the header only
    documents the format for a human, and would be read as content.
    """
    return "\n".join(
        line for line in (text or "").splitlines() if not line.strip().startswith("<!--")
    ).strip()


def parse_lines(text: str) -> list[DraftLine]:
    """Read the lines back. Text without a role is kept, as a paragraph."""
    lines: list[DraftLine] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("<!--"):
            continue
        match = _LINE.match(raw)
        role = match.group("role") if match else "body_text"
        body = match.group("text").strip() if match else raw
        body, ids = split_citations(body)
        lines.append(DraftLine(role=role, text=body, achievement_ids=ids))
    return lines


def render_answers(answers: list[FormAnswer]) -> str:
    blocks = []
    for answer in answers:
        body = answer.answer.strip() or _EMPTY_ANSWER
        blocks.append(f"## {answer.question}\n{body}")
    if not blocks:
        return ""
    return "\n\n".join([ANSWERS_HEADER, *blocks]) + "\n"


def parse_answers(text: str) -> list[FormAnswer]:
    answers: list[FormAnswer] = []
    question: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if question is None:
            return
        body = "\n".join(buffer).strip()
        answers.append(
            FormAnswer(question=question, answer="" if body == _EMPTY_ANSWER else body)
        )

    for line in (text or "").splitlines():
        if line.startswith("## "):
            flush()
            question = line[3:].strip()
            buffer = []
        elif question is not None:
            buffer.append(line)
    flush()
    return answers


def to_html(text: str) -> str:
    """A readable preview. The text is escaped first: it comes from a model."""
    parts: list[str] = []
    for line in parse_lines(text):
        body = html.escape(line.text)
        citation = ""
        if line.achievement_ids:
            citation = f'<span class="cite">{html.escape(", ".join(line.achievement_ids))}</span>'
        parts.append(_line_html(line.role, body, citation))
    return "\n".join(parts)


def _line_html(role: str, body: str, citation: str) -> str:
    if role == "bullet":
        return f'<p class="doc-bullet">• {body}{citation}</p>'
    if role == "section_title":
        return f'<h3 class="doc-section">{body}</h3>'
    if role == "name":
        return f'<p class="doc-name">{body}</p>'
    if role == "headline":
        return f'<p class="doc-headline">{body}</p>'
    if role == "entry_title":
        return f'<p class="doc-entry">{body}</p>'
    if role == "entry_subtitle":
        return f'<p class="doc-subtitle">{body}</p>'
    if role == "entry_dates":
        return f'<p class="doc-dates">{body}</p>'
    return f"<p>{body}{citation}</p>"
