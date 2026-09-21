"""Rewrite a .docx template so it no longer carries the CV it was built from.

An imported template *is* the candidate's own document: its paragraphs are the CV
they wrote, and its hyperlinks point at their e-mail and their profiles. Rendering
a document into it replaces the content, but not what the file itself holds, so a
template shown in the settings page — or handled by anyone else — still tells the
story of its author.

This rewrites it with the app's own renderer: the base document and the blueprint
stay the ones that were imported, and the lines are the fictional profile's.
Styles, fonts, margins and the shape of the document are therefore preserved by
construction, because nothing here re-implements the renderer.

What a render does not touch is the package around the body. A hyperlink's target
lives in ``word/_rels/document.xml.rels`` and survives whether or not the link is
still used, so targets are rewritten too. The result is then verified: if any
string taken from the real profile is still readable in a text node or a link
target, nothing is written and the script says what it found.

Usage::

    python scripts/anonymize_template.py --report
    python scripts/anonymize_template.py --data-dir ./demo-data

``--report`` changes nothing: it lists the roles, the links, and what each block
would become, so the result can be judged before any file is produced.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# The strings stay in the repository; the file they are read from does not.
# Both default to the running app's own data directory.
DEFAULT_SOURCE = "data/templates/cv.docx"
DEFAULT_BLUEPRINT = "data/templates/cv.json"
DEFAULT_PROFILE = "data/profile/profile.yaml"
DEMO_PROFILE = ROOT / "app" / "examples" / "profile.example.yaml"
DEMO_TEXT = ROOT / "app" / "examples" / "demo" / "template_text.yaml"

# Only the text runs, and only the link targets: everything else in an OOXML part
# is namespaces, style ids and geometry, which a substitution must never touch.
_TEXT_NODE = re.compile(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")
_TARGET_ATTR = re.compile(r'(Target=")([^"]*)(")')
# A variant shorter than this is too likely to be a word rather than a name.
_MIN_TOKEN = 3


class AnonymiseError(Exception):
    """The result could not be guaranteed: nothing should be written."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite a .docx template with the fictional profile's text."
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="the template to rewrite")
    parser.add_argument("--blueprint", default=DEFAULT_BLUEPRINT, help="its analysed blueprint")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="the real profile to remove")
    parser.add_argument("--demo-profile", default=str(DEMO_PROFILE))
    parser.add_argument("--text", default=str(DEMO_TEXT))
    parser.add_argument("--kind", default="cv", choices=["cv", "letter"])
    parser.add_argument("--data-dir", default="demo-data", help="workspace to write into")
    parser.add_argument("--report", action="store_true", help="show what would change, then stop")
    args = parser.parse_args(argv)

    # ``store.TEMPLATES_DIR`` is read when ``app.config`` is imported.
    os.environ["PAUL_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    from app.templates_engine import store

    source = Path(args.source)
    profile = _load_profile(args.profile)
    demo = _load_profile(args.demo_profile)
    pools = _pools(Path(args.text))
    blueprint = _load_blueprint(Path(args.blueprint), source, args.kind)

    if args.report:
        _report(source, blueprint, profile, demo, pools)
        return 0

    docx, rewritten_blueprint, warnings = anonymise(
        source.read_bytes(), blueprint, profile, demo, pools, kind=args.kind
    )
    store.save_custom(args.kind, docx, rewritten_blueprint)

    for warning in warnings:
        print(f"warning: {warning}")
    print(f"Written to {store.custom_path(args.kind)}")
    print(f"          {store.blueprint_path(args.kind)}")
    print()
    print(f"Your own files are untouched: {source} and {args.blueprint}.")
    return 0


def anonymise(docx, blueprint, profile, demo, pools, *, kind="cv"):
    """Rewrite the package and its blueprint.

    Returns ``(docx_bytes, blueprint, warnings)``. Raises ``AnonymiseError`` when a
    string from the real profile is still readable in the result, because a partial
    anonymization is worse than none: it looks finished.
    """
    from app.models import DraftLine
    from app.templates_engine import render, store
    from app.templates_engine.extract import extract_blocks

    replacements = replacement_map(profile, demo)
    lines, warnings = _lines(blueprint, pools, replacements)

    rewritten = render.render(docx, blueprint, [DraftLine(role=r, text=t) for r, t in lines])
    rewritten = _scrub(rewritten, replacements, demo)

    residues = _residues(rewritten, replacements)
    if residues:
        raise AnonymiseError(
            "These strings from the real profile are still readable in the result: "
            + ", ".join(sorted(residues))
            + ". Nothing was written."
        )

    blocks = extract_blocks(rewritten)
    if len(blocks) != len(blueprint.blocks):
        raise AnonymiseError(
            f"The rewritten document has {len(blocks)} blocks against "
            f"{len(blueprint.blocks)} in the blueprint: the roles cannot be matched "
            "back position by position. Nothing was written."
        )
    rewritten_blueprint = store.blueprint_from_blocks(
        kind, blocks, [block.role for block in blueprint.blocks]
    )
    return rewritten, rewritten_blueprint, warnings


def _lines(blueprint, pools: dict[str, list[str]], replacements) -> tuple[list, list[str]]:
    """One line per block: the pool's text, or the file's own when it is neutral."""
    cursors: Counter[str] = Counter()
    warnings: list[str] = []
    lines: list[tuple[str, str]] = []

    for block in blueprint.blocks:
        pool = pools.get(block.role) or []
        if pool:
            text = pool[cursors[block.role] % len(pool)]
            cursors[block.role] += 1
        elif _mentions(block.text, replacements):
            # A section title is worth keeping; one that names the candidate is not.
            text = ""
            warnings.append(f"a {block.role} block named the real candidate and was emptied")
        else:
            text = block.text
        lines.append((block.role, text))
    return lines, warnings


def replacement_map(profile, demo) -> dict[str, str]:
    """Every shape of every personal string, and what it becomes.

    Built from the two profiles rather than written here: a real name, address and
    link are nobody's business to hard-code into a script.
    """
    mapping: dict[str, str] = {}

    def add(value: str, target: str) -> None:
        if not value or not target:
            return
        for variant in _variants(value):
            if len(variant) >= _MIN_TOKEN:
                mapping[variant] = target

    add(profile.identity.name, demo.identity.name)
    add(profile.identity.first_name, demo.identity.first_name)
    add(profile.identity.last_name, demo.identity.last_name)
    add(profile.identity.email, demo.identity.email)
    add(profile.identity.phone, demo.identity.phone)
    add(profile.identity.headline, demo.identity.headline)
    add(profile.identity.location, demo.identity.location)

    for index, link in enumerate(profile.identity.links):
        add(link, _cycle(demo.identity.links, index) or "")

    for index, experience in enumerate(profile.experiences):
        target = _cycle(demo.experiences, index)
        add(experience.company, target.company if target else "")
    for index, education in enumerate(profile.education):
        target = _cycle(demo.education, index)
        add(education.school, target.school if target else "")
    return mapping


def _variants(value: str) -> set[str]:
    """The shapes one string takes in a document: raw, bare, and digits only."""
    text = (value or "").strip()
    if not text:
        return set()
    found = {text}
    bare = re.sub(r"^(?:mailto:|https?://|www\.)", "", text, flags=re.IGNORECASE).rstrip("/")
    if bare:
        found.add(bare)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        found.add(digits)
    return found


def _cycle(items: list, index: int):
    """The demo value to use for the index-th real one.

    The demo profile is smaller than a real career: five companies have to map onto
    two, so the pool is cycled rather than truncated. Dropping the extra ones would
    leave the last companies of the real profile in place, and the verification
    would be looking at a map that cannot match them.
    """
    if not items:
        return None
    return items[index % len(items)]


def _mentions(text: str, replacements: dict[str, str]) -> bool:
    folded = (text or "").casefold()
    return any(needle.casefold() in folded for needle in replacements)


def _substitute(text: str, replacements: dict[str, str]) -> str:
    """Replace every personal string, longest first so a name is not eaten by its part."""
    for needle, target in sorted(replacements.items(), key=lambda item: -len(item[0])):
        text = re.sub(re.escape(needle), lambda _match, to=target: to, text, flags=re.IGNORECASE)
    return text


def _scrub(docx: bytes, replacements: dict[str, str], demo) -> bytes:
    """Rewrite the whole package: text runs, and the link targets beside them.

    Only ``<w:t>`` contents and ``Target`` attributes are touched. Everything else
    in these parts is namespaces, style ids and geometry: a blind substitution over
    the XML would corrupt the document.
    """
    source = zipfile.ZipFile(io.BytesIO(docx))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith(".rels"):
                data = _scrub_targets(data.decode("utf-8"), replacements, demo).encode("utf-8")
            elif item.filename.endswith(".xml"):
                data = _TEXT_NODE.sub(
                    lambda match: match.group(1)
                    + _substitute(match.group(2), replacements)
                    + match.group(3),
                    data.decode("utf-8"),
                ).encode("utf-8")
            target.writestr(item, data)
    return buffer.getvalue()


def _scrub_targets(xml: str, replacements: dict[str, str], demo) -> str:
    """Point a link that named the candidate at the demo equivalent instead."""

    def replace(match: re.Match[str]) -> str:
        after = _target_after(match.group(2), replacements, demo)
        if after is None:
            return match.group(0)
        return match.group(1) + after + match.group(3)

    return _TARGET_ATTR.sub(replace, xml)


def _target_after(value: str, replacements: dict[str, str], demo) -> str | None:
    """What a link target becomes, or ``None`` when it never named the candidate."""
    if not _mentions(value, replacements):
        return None
    if value.lower().startswith("mailto:"):
        return "mailto:" + demo.identity.email
    return _cycle(demo.identity.links, 0) or ""


def _residues(docx: bytes, replacements: dict[str, str]) -> list[str]:
    """What is still readable of the real profile, in text or in a link target."""
    readable: list[str] = []
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        for name in archive.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            raw = archive.read(name).decode("utf-8", "replace")
            readable.extend(_texts(raw))
            readable.extend(match.group(2) for match in _TARGET_ATTR.finditer(raw))
    haystack = "\n".join(readable).casefold()
    return [needle for needle in replacements if needle.casefold() in haystack]


def _texts(xml: str) -> list[str]:
    return [match.group(2) for match in _TEXT_NODE.finditer(xml)]


# --- Reading the inputs --------------------------------------------------------


def _load_profile(path: str):
    from app.models import Profile

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Profile.model_validate(data or {})


def _load_blueprint(path: Path, source: Path, kind: str):
    """The analysed blueprint, or a layout-only guess when there is none.

    The roles the analyzer produced are worth keeping: they were read from this
    very file, so the rewritten document can be given the same ones back.
    """
    from app.models import TemplateBlueprint
    from app.templates_engine.extract import extract_blocks
    from app.templates_engine.store import blueprint_from_blocks

    if path.exists():
        return TemplateBlueprint.model_validate_json(path.read_text(encoding="utf-8"))
    return blueprint_from_blocks(kind, extract_blocks(source.read_bytes()))


def _pools(path: Path) -> dict[str, list[str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pools: dict[str, list[str]] = {}
    for role, lines in raw.items():
        if not isinstance(lines, list):
            continue
        for line in lines:
            # A plain scalar containing ": " is read as a mapping by YAML, which
            # would silently hand a dict to the renderer. Quoting the line is the
            # fix; this is here so the next one is caught rather than shipped.
            if not isinstance(line, str):
                raise AnonymiseError(
                    f"The {role} line {line!r} of {path.name} is not a string: "
                    'quote it in the fixture (a value containing ": " needs quotes).'
                )
            pools.setdefault(role, []).append(line)
    return pools


# --- The report ----------------------------------------------------------------


def _report(source: Path, blueprint, profile, demo, pools: dict) -> None:
    """Say what would be replaced, without writing anything."""
    replacements = replacement_map(profile, demo)
    lines, warnings = _lines(blueprint, pools, replacements)

    print(f"template   {source}")
    print(f"blocks     {len(blueprint.blocks)}")
    counts = Counter(b.role for b in blueprint.blocks).most_common()
    tally = ", ".join(f"{role}×{count}" for role, count in counts)  # noqa: RUF001 - on purpose
    print(f"roles      {tally}")
    print()
    print(f"strings to remove   {len(replacements)} shape(s) of {_shapes(profile)}")
    print()
    print("what each role becomes:")
    seen: Counter[str] = Counter()
    for role, text in lines:
        seen[role] += 1
        if seen[role] > 2:
            continue
        shown = text if len(text) <= 76 else text[:73] + "…"
        print(f"  {role:<15} {shown or '(emptied)'}")

    links = _link_targets(source.read_bytes())
    print()
    print(f"link targets in the package: {len(links)}")
    for link in links:
        after = _target_after(link, replacements, demo)
        shown = link if len(link) <= 52 else link[:49] + "…"
        print(f"  {shown:<52} {('→ ' + after) if after is not None else 'left as is'}")

    for warning in warnings:
        print()
        print(f"warning: {warning}")


def _shapes(profile) -> str:
    counts = [
        ("name", 1 if profile.identity.name else 0),
        ("e-mail", 1 if profile.identity.email else 0),
        ("phone", 1 if profile.identity.phone else 0),
        ("links", len(profile.identity.links)),
        ("companies", len(profile.experiences)),
        ("schools", len(profile.education)),
    ]
    return ", ".join(f"{count} {label}" for label, count in counts if count)


def _link_targets(docx: bytes) -> list[str]:
    found: list[str] = []
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        for name in archive.namelist():
            if not name.endswith(".rels"):
                continue
            raw = archive.read(name).decode("utf-8", "replace")
            found.extend(match.group(2) for match in _TARGET_ATTR.finditer(raw))
    return [target for target in found if target.startswith(("mailto:", "http://", "https://", "www."))]


if __name__ == "__main__":
    sys.exit(main())
