"""ATS readiness: keyword coverage and format red flags.

A CV that reads well to a human can still be mangled by the parsers behind
applicant tracking systems, and an offer's keywords are what those parsers score
against. This module answers two questions with deterministic code — how much of
the offer's vocabulary the CV already contains, and which parts of the document
layout a parser is likely to drop — because a model would only make a check that
must stay reproducible and explainable less trustworthy.

The model is used for one thing only: extracting the keywords when the offer was
not analyzed yet. Anything about the candidate's own profile stays rule-based.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from xml.etree import ElementTree

from pydantic import BaseModel, Field

from app.config import Settings
from app.llm import complete_structured
from app.models import Keyword, Offer, Profile
from app.prompts import load_prompt

_WHITESPACE = re.compile(r"\s+")

# WordprocessingML: the namespace every ``w:`` element below belongs to.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"

_HEADER_FOOTER = re.compile(r"word/(?:header|footer)\d+\.xml")

# The candidate can act on these two hints: they are the only difference between
# a keyword they may add and one they must not invent.
_HINT_IN_PROFILE = "In your profile: you can add it to the CV without inventing anything."
_HINT_NOT_IN_PROFILE = "Not in your profile: add it only if you really have it."


class AtsKeyword(BaseModel):
    """One offer keyword, plus what the CV and the profile do with it."""

    term: str = ""
    variants: list[str] = Field(default_factory=list)
    present: bool = False
    in_profile: bool = False
    hint: str = ""


class AtsReport(BaseModel):
    """The result of checking a CV against an offer's keywords."""

    coverage_percent: int = 0
    keywords: list[AtsKeyword] = Field(default_factory=list)
    format_issues: list[str] = Field(default_factory=list)

    @property
    def missing(self) -> list[AtsKeyword]:
        """The keywords the CV does not contain yet."""
        return [keyword for keyword in self.keywords if not keyword.present]


def normalize(text: str) -> str:
    """Lower-case, accent-free, whitespace-collapsed text, for every match here.

    ATS matching ignores case, accents and runs of spaces, so the comparison must
    do the same or a perfectly good CV would be reported as empty-handed.
    """
    decomposed = unicodedata.normalize("NFD", text or "")
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", without_accents.casefold()).strip()


def keyword_present(cv_text: str, keyword: AtsKeyword) -> bool:
    """True when the term or one of its variants appears in the CV text.

    The variants an offer uses are how the same skill hides under two names
    ("K8s" / "Kubernetes"), so finding either one counts.
    """
    haystack = normalize(cv_text)
    for needle in (keyword.term, *keyword.variants):
        folded = normalize(needle)
        if folded and folded in haystack:
            return True
    return False


# The ids are dropped: their digits ("exp-acme-2022") would otherwise make almost
# every number look supported, and an id is a label, not something the profile
# claims.
_ID_KEY = "id"


def _flatten(value: object) -> Iterator[str]:
    """Every string and number the profile states, minus the ids."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == _ID_KEY:
                continue
            yield from _flatten(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)


def profile_material(profile: Profile) -> str:
    """Everything the profile states, ids aside, as one normalized blob.

    The ATS hint and the grounding check ask the same question — does the profile
    mention this term? — so they read the same text, built here once. It covers
    the whole profile rather than a hand-kept skills list: a technology is stated
    by the job or the project that used it, not by a bare keyword.
    """
    return normalize(" \n ".join(_flatten(profile.model_dump(exclude_defaults=True))))


def _hint(present: bool, in_profile: bool) -> str:
    if present:
        return ""
    return _HINT_IN_PROFILE if in_profile else _HINT_NOT_IN_PROFILE


def coverage(
    cv_text: str, keywords: list[AtsKeyword], profile: Profile | None = None
) -> AtsReport:
    """Check a CV against the offer's keywords.

    ``coverage_percent`` stays 0 when there is no keyword: an offer we could not
    read a single term from must not look like a perfect match.
    """
    profile_text = profile_material(profile) if profile is not None else ""
    checked: list[AtsKeyword] = []
    for keyword in keywords:
        present = keyword_present(cv_text, keyword)
        in_profile = bool(profile_text) and keyword_present(profile_text, keyword)
        checked.append(
            keyword.model_copy(
                update={
                    "present": present,
                    "in_profile": in_profile,
                    "hint": _hint(present, in_profile),
                }
            )
        )

    total = len(checked)
    percent = round(100 * sum(keyword.present for keyword in checked) / total) if total else 0
    return AtsReport(coverage_percent=percent, keywords=checked)


def _count(root: ElementTree.Element, local: str) -> int:
    return sum(1 for _ in root.iter(f"{_W}{local}"))


def _counted(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _text_box_issues(root: ElementTree.Element) -> list[str]:
    count = _count(root, "txbxContent")
    if not count:
        return []
    return [
        f"{_counted(count, 'text box', 'text boxes')} detected. Applicant tracking systems "
        "often skip text boxes, so anything written there can disappear from the parsed "
        "CV. Rewrite it as ordinary paragraphs."
    ]


def _drawing_issues(root: ElementTree.Element) -> list[str]:
    count = _count(root, "drawing")
    if not count:
        return []
    return [
        f"{_counted(count, 'image', 'images')} detected. An ATS reads text, not pixels: "
        "your logo is ignored and a picture of text is invisible to it. Keep every piece "
        "of information as real text."
    ]


def _column_count(cols: ElementTree.Element) -> int:
    """Columns declared by a ``w:cols`` element; absent ``w:num`` means one."""
    raw = cols.get(f"{_W}num")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            return 1
    return max(1, sum(1 for _ in cols.iter(f"{_W}col")))


def _column_issues(root: ElementTree.Element) -> list[str]:
    if not any(_column_count(cols) > 1 for cols in root.iter(f"{_W}cols")):
        return []
    return [
        "A multi-column layout is used. An ATS reads the page as a single column, so "
        "your columns can be interleaved and reordered. Lay the CV out in one column."
    ]


def _table_issues(root: ElementTree.Element) -> list[str]:
    count = _count(root, "tbl")
    if not count:
        return []
    return [
        f"{_counted(count, 'table', 'tables')} detected. Parsers read a table cell by cell "
        "and often merge or reorder it, and a table is also how a two-column layout is "
        "built. Use plain paragraphs unless the content is really tabular."
    ]


def _header_footer_issues(archive: zipfile.ZipFile) -> list[str]:
    for name in archive.namelist():
        if not _HEADER_FOOTER.fullmatch(name):
            continue
        root = ElementTree.fromstring(archive.read(name))
        if any((node.text or "").strip() for node in root.iter(f"{_W}t")):
            return [
                "Text is present in a page header or footer. Some ATS parsers ignore "
                "headers and footers, so keep your name and contact details in the main "
                "body too."
            ]
    return []


def format_issues(docx_bytes: bytes) -> list[str]:
    """List what an ATS parser is likely to get wrong in this DOCX.

    Every message names the element and the consequence, because the person
    reading the report edits a CV, not a parser.
    """
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        return [
            *_text_box_issues(document),
            *_drawing_issues(document),
            *_column_issues(document),
            *_table_issues(document),
            *_header_footer_issues(archive),
        ]


class _KeywordList(BaseModel):
    """What the model returns when the offer has no keywords yet."""

    keywords: list[Keyword] = Field(default_factory=list)


def to_ats_keywords(keywords: list[Keyword]) -> list[AtsKeyword]:
    """The offer's keywords as ATS keywords, with no model call."""
    result: list[AtsKeyword] = []
    for keyword in keywords:
        term = keyword.term.strip()
        if not term:
            continue
        variants = [
            variant.strip()
            for variant in keyword.variants
            if variant.strip() and variant.strip().casefold() != term.casefold()
        ]
        result.append(AtsKeyword(term=term, variants=variants))
    return result


async def extract_keywords(settings: Settings, offer: Offer, offer_text: str) -> list[AtsKeyword]:
    """Return the keywords to check the CV against.

    When the offer already carries keywords they are reused as-is: the offer
    analysis already paid for that extraction, and a second model pass could only
    add variants the offer never used.
    """
    if offer.keywords:
        return to_ats_keywords(offer.keywords)

    result = await complete_structured(
        settings,
        schema=_KeywordList,
        content=offer_text,
        system=load_prompt("writer/keywords.md"),
    )
    return to_ats_keywords(result.keywords)
