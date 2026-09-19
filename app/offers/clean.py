"""Deterministic cleaning of a pasted offer fragment.

The model never sees raw page HTML. This module turns a fragment into a compact
Markdown-ish document, and parses the application form itself: labels, ``name``,
``type``, ``required``, ``maxlength``, ``placeholder`` and ``<option>`` values are
facts read from the markup, so they are read by code rather than asked for from
the LLM (README design rule). That also keeps the extraction prompt small and
focused on what only language understanding can do.

"Cleaning" means: drop scripts, styles, SVG, images and other non-content noise,
strip tracking parameters from links, and keep headings, lists, links and text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from app.models import FormQuestion

# Never content: scripts, styling, vectors, media and third-party embeds.
SKIP_TAGS = {
    "script",
    "style",
    "svg",
    "noscript",
    "template",
    "iframe",
    "canvas",
    "video",
    "audio",
    "img",
    "picture",
    "source",
    "track",
    "map",
    "link",
    "meta",
    "object",
    "embed",
    "base",
    "head",
    "title",
}

HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Anything that is not inline is a container to walk through. Listing the inline
# tags (rather than the blocks) means unknown wrappers such as <html>, <body> or
# a custom <job-description> are traversed instead of being flattened.
INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "br",
    "cite",
    "code",
    "data",
    "del",
    "dfn",
    "em",
    "i",
    "ins",
    "kbd",
    "mark",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
    "wbr",
}

CONTROL_TAGS = ("input", "select", "textarea")
# Not questions to answer: hidden plumbing and the submit buttons themselves.
SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}

_TRACKING_PREFIXES = ("utm_", "mc_")
_TRACKING_KEYS = {"gclid", "fbclid", "igshid", "yclid", "_ga"}
_TAG_RE = re.compile(
    r"<\s*(?:!doctype|html|body|div|p|span|a|ul|ol|li|h[1-6]|form|input|select"
    r"|textarea|label|button|table|section|article|br)\b",
    re.IGNORECASE,
)

# Past this, the extraction prompt would be wasteful and unreliable.
MAX_CHARS = 80_000
MIN_USEFUL_CHARS = 40
# Short lines can legitimately repeat (bullets, labels); longer ones are duplicates.
DEDUPE_MIN_LENGTH = 20


class CleanError(Exception):
    """The pasted content holds no usable text, for a reason we can explain."""


@dataclass
class CleanedOffer:
    """What the cleaner produced, ready for the extraction prompt."""

    text: str = ""
    fragments: int = 0
    html: bool = False
    links: int = 0
    truncated: bool = False
    form: list[FormQuestion] = field(default_factory=list)

    @property
    def form_fields(self) -> int:
        return len(self.form)


@dataclass
class _Html:
    title: str
    lines: list[str]
    questions: list[FormQuestion]
    links: int


def looks_like_html(content: str) -> bool:
    """A cheap test, so a fragment is not silently treated as plain text."""
    return bool(_TAG_RE.search(content or ""))


def clean_url(href: str | None) -> str:
    """Drop tracking parameters and unusable schemes from a link."""
    if not href:
        return ""
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return ""
    parsed = urlparse(href)
    if parsed.query:
        kept = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_KEYS
            and not key.lower().startswith(_TRACKING_PREFIXES)
        ]
        parsed = parsed._replace(query=urlencode(kept))
    return urlunparse(parsed)


def _squash(text: str) -> str:
    """Collapse runs of spaces; keep intentional line breaks."""
    text = (text or "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _attr(tag: Tag, name: str) -> str:
    """Read an attribute as text; BeautifulSoup allows lists for some of them."""
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return ""


def _inline(node, skip: tuple = ()) -> str:
    """Render inline content, keeping links as Markdown."""
    if any(node is skipped for skipped in skip):
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name in SKIP_TAGS:
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "a":
        text = _squash("".join(_inline(child, skip) for child in node.children))
        href = clean_url(_attr(node, "href"))
        if text and href:
            return f"[{text}]({href})"
        return text or href
    return "".join(_inline(child, skip) for child in node.children)


def _has_block_children(tag: Tag) -> bool:
    return any(isinstance(child, Tag) and _is_block(child) for child in tag.children)


def _render(node: Tag, lines: list[str], depth: int = 0) -> None:
    """Walk the tree in document order, one line (or list item) at a time."""
    pending: list = []

    def flush() -> None:
        if pending:
            text = _squash("".join(_inline(item) for item in pending))
            pending.clear()
            if text:
                lines.extend(text.split("\n"))

    for child in node.children:
        if isinstance(child, NavigableString):
            if str(child).strip():
                pending.append(child)
            continue
        if not isinstance(child, Tag) or child.name in SKIP_TAGS:
            continue
        if not _is_block(child):
            pending.append(child)
            continue
        flush()
        _emit_block(child, lines, depth)
    flush()


def _inline_without_lists(tag: Tag) -> str:
    """Inline content of a list item, leaving nested lists to their own walk."""
    return "".join(
        _inline(child)
        for child in tag.children
        if not (isinstance(child, Tag) and child.name in ("ul", "ol"))
    )


def _is_block(tag: Tag) -> bool:
    return tag.name not in INLINE_TAGS


def _is_hidden(tag: Tag) -> bool:
    """Elements a browser would not show: layout noise, not content."""
    if tag.has_attr("hidden"):
        return True
    if _attr(tag, "aria-hidden").strip().lower() == "true":
        return True
    style = _attr(tag, "style").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _emit_block(tag: Tag, lines: list[str], depth: int) -> None:
    name = tag.name
    if name == "hr":
        return  # pure layout
    if name in HEADING_LEVELS:
        text = _squash(_inline(tag))
        if text:
            lines.append("#" * HEADING_LEVELS[name] + " " + text)
        return
    if name == "li":
        text = _squash(_inline_without_lists(tag))
        if text:
            lines.append("  " * depth + "- " + text)
        for nested in tag.find_all(["ul", "ol"], recursive=False):
            _render(nested, lines, depth + 1)
        return
    if name == "tr":
        cells = [_squash(_inline(cell)) for cell in tag.find_all(["td", "th"], recursive=False)]
        row = " | ".join(cell for cell in cells if cell)
        if row:
            lines.append(row)
        return
    if name in ("ul", "ol"):
        _render(tag, lines, depth + 1)
        return
    if not _has_block_children(tag):
        # A container that only holds inline content is one paragraph.
        text = _squash(_inline(tag))
        if text:
            lines.extend(text.split("\n"))
        return
    _render(tag, lines, depth)


def _label_for(control: Tag, soup: BeautifulSoup) -> str:
    control_id = _attr(control, "id").strip()
    if control_id:
        label = soup.find("label", attrs={"for": control_id})
        if isinstance(label, Tag):
            text = _squash(_inline(label))
            if text:
                return text
    parent = control.find_parent("label")
    if isinstance(parent, Tag):
        text = _squash(_inline(parent, skip=(control,)))
        if text:
            return text
    for attribute in ("aria-label", "title"):
        value = _attr(control, attribute).strip()
        if value:
            return value
    return ""


def _group_label(control: Tag) -> str:
    fieldset = control.find_parent("fieldset")
    if isinstance(fieldset, Tag):
        legend = fieldset.find("legend", recursive=False)
        if isinstance(legend, Tag):
            return _squash(_inline(legend))
    return ""


def _humanize(name: str) -> str:
    name = re.split(r"[\[\]]", name or "")[0].strip()
    name = name.replace("_", " ").replace("-", " ").strip()
    return name[:1].upper() + name[1:] if name else ""


def _int_or_none(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _form_questions(soup: BeautifulSoup) -> list[FormQuestion]:
    """Parse the application form, grouping radios and checkboxes by name."""
    questions: list[FormQuestion] = []
    grouped: dict[str, FormQuestion] = {}

    for control in soup.find_all(CONTROL_TAGS):
        if control.name == "input":
            control_type = (_attr(control, "type") or "text").strip().lower()
            if control_type in SKIP_INPUT_TYPES:
                continue
            type_ = control_type
        else:
            type_ = "select" if control.name == "select" else "textarea"

        name = _attr(control, "name").strip()
        label = _label_for(control, soup)
        required = control.has_attr("required") or _attr(control, "aria-required") == "true"

        if type_ in {"radio", "checkbox"} and name:
            question = grouped.get(name)
            if question is None:
                question = FormQuestion(
                    label=_group_label(control) or _humanize(name),
                    name=name,
                    type=type_,
                )
                grouped[name] = question
                questions.append(question)
            option = label or _attr(control, "value").strip()
            if option and option not in question.options:
                question.options.append(option)
            question.required = question.required or required
            continue

        options: list[str] = []
        if control.name == "select":
            for option in control.find_all("option"):
                text = _squash(_inline(option))
                if text and text not in options:
                    options.append(text)

        questions.append(
            FormQuestion(
                label=label or _humanize(name),
                name=name,
                type=type_,
                options=options,
                required=required,
                max_length=_int_or_none(_attr(control, "maxlength")),
                placeholder=_attr(control, "placeholder").strip(),
            )
        )

    return [question for question in questions if question.label or question.name]


def _clean_html(html: str) -> _Html:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = _squash(title_tag.get_text(" ", strip=True)) if isinstance(title_tag, Tag) else ""

    # Read the form before removing anything: labels point into the tree.
    questions = _form_questions(soup)

    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    for tag in soup.find_all(_is_hidden):
        if tag.parent is not None:
            tag.decompose()
    # The controls and their labels are captured as structured questions already;
    # leaving them in the text would only duplicate and pad the prompt.
    for tag in soup.find_all([*CONTROL_TAGS, "label", "legend", "button", "option", "optgroup"]):
        tag.decompose()

    links = sum(1 for anchor in soup.find_all("a") if clean_url(_attr(anchor, "href")))

    lines: list[str] = []
    _render(soup, lines)
    return _Html(title=title, lines=lines, questions=questions, links=links)


def _clean_text(content: str) -> list[str]:
    return [line for line in _squash(content).split("\n") if line]


def clean_fragments(parts) -> CleanedOffer:
    """Clean one or several fragments of the same offer and merge them.

    Several fragments are the normal case: the description and the application
    form often live on different pages, and the user pastes them separately.
    """
    result = CleanedOffer()
    lines: list[str] = []
    seen: set[str] = set()

    for part in parts:
        content = (part or "").strip()
        if not content:
            continue
        result.fragments += 1

        if looks_like_html(content):
            result.html = True
            cleaned = _clean_html(content)
            fragment_lines = list(cleaned.lines)
            if cleaned.title:
                fragment_lines.insert(0, f"Page title: {cleaned.title}")
            result.links += cleaned.links
        else:
            cleaned = _Html(title="", lines=_clean_text(content), questions=[], links=0)
            fragment_lines = cleaned.lines

        for question in cleaned.questions:
            if question not in result.form:
                result.form.append(question)

        for line in fragment_lines:
            line = line.strip()
            if not line:
                continue
            # Pasting two fragments of the same page repeats the header: keep it once.
            if len(line) >= DEDUPE_MIN_LENGTH and line in seen:
                continue
            seen.add(line)
            lines.append(line)

    if not lines:
        if result.form:
            raise CleanError(
                "Only an application form was found in this fragment. Paste the "
                "offer's description too, as another fragment if it is on another page."
            )
        raise CleanError(
            "No text could be read from this fragment. Paste the offer's content, "
            "not just a link."
        )

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        cut = text.rfind("\n", 0, MAX_CHARS)
        text = text[: cut if cut > 0 else MAX_CHARS]
        result.truncated = True

    if len(text) < MIN_USEFUL_CHARS:
        raise CleanError(
            "This fragment holds almost no text. If the offer is rendered by "
            "JavaScript, copy the element's outerHTML from the inspector instead."
        )

    result.text = text
    return result
