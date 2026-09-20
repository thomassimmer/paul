"""What the anonymizer produces must not be traceable to the profile it removed.

The verification here is deliberately independent of the script's own check: the
package is reopened and read for itself, so a bug in the script's ``_residues``
cannot make the test pass.
"""

from __future__ import annotations

import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "anonymize_template.py"
ROOT = Path(__file__).resolve().parent.parent

# Nothing here resembles the fictional profile, so a leftover is unmistakable.
REAL = {
    "identity": {
        "name": "Georges Anonyme",
        "first_name": "Georges",
        "last_name": "Anonyme",
        "headline": "Platform Engineer",
        "location": "Brest",
        "email": "georges.anonyme@corp.invalid",
        "phone": "+33 6 47 11 22 33",
        "links": ["https://github.com/georges-anonyme", "linkedin.com/in/georges-anonyme"],
    },
    "experiences": [
        {"company": "Zephyr Systèmes", "title": "Lead"},
        {"company": "Basalte SAS", "title": "Engineer"},
    ],
    "education": [{"school": "Verdun Institute"}],
}

NEEDLES = [
    "Georges Anonyme",
    "Georges",
    "Anonyme",
    "georges.anonyme@corp.invalid",
    "+33 6 47 11 22 33",
    "334711223",
    "georges-anonyme",
    "Zephyr Systèmes",
    "Basalte SAS",
    "Verdun Institute",
    "Brest",
]

_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
_TARGET = re.compile(r'Target="([^"]*)"')


def _module():
    """``scripts/`` is not a package, so the module is loaded by path."""
    spec = importlib.util.spec_from_file_location("anonymize_template", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _readable(docx: bytes) -> str:
    """Every string a reader could see: the text runs and the link targets."""
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        raw = "\n".join(
            archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
            if name.endswith((".xml", ".rels"))
        )
    return "\n".join(_TEXT.findall(raw) + _TARGET.findall(raw)).casefold()


def _inputs():
    from app.models import Profile
    from app.templates_engine import default

    module = _module()
    real = Profile.model_validate(REAL)
    demo = Profile.model_validate(
        yaml.safe_load((ROOT / "app" / "examples" / "profile.example.yaml").read_text("utf-8"))
    )
    pools = module._pools(ROOT / "app" / "examples" / "demo" / "template_text.yaml")
    docx, blueprint = default.build("cv")
    return module, real, demo, pools, docx, blueprint


def test_nothing_of_the_real_profile_survives():
    module, real, demo, pools, docx, blueprint = _inputs()

    rewritten, out_blueprint, _warnings = module.anonymise(docx, blueprint, real, demo, pools)

    readable = _readable(rewritten)
    for needle in NEEDLES:
        assert needle.casefold() not in readable, f"{needle!r} survived in the document"

    # And the blueprint that goes with it is clean too: it carries the block XML.
    dumped = out_blueprint.model_dump_json().casefold()
    for needle in NEEDLES:
        assert needle.casefold() not in dumped, f"{needle!r} survived in the blueprint"


def test_the_fictional_text_replaces_it_and_the_roles_are_kept():
    module, real, demo, pools, docx, blueprint = _inputs()

    rewritten, out_blueprint, _warnings = module.anonymise(docx, blueprint, real, demo, pools)

    readable = _readable(rewritten)
    assert "camille moreau" in readable
    assert "acme analytics" in readable

    assert [block.role for block in out_blueprint.blocks] == [
        block.role for block in blueprint.blocks
    ]
    assert len(out_blueprint.blocks) == len(blueprint.blocks)


def test_an_unquoted_fixture_line_is_refused_rather_than_rendered(tmp_path: Path):
    """A plain scalar holding ": " is read as a mapping by YAML; that must not pass."""
    module = _module()
    fixture = tmp_path / "text.yaml"
    fixture.write_text("skill_line:\n  - Languages: Rust, Python\n", encoding="utf-8")

    with pytest.raises(module.AnonymiseError, match="is not a string"):
        module._pools(fixture)


def test_only_personal_targets_are_rewritten():
    """A namespace URL is not a personal link, and must be left exactly as it is."""
    module, real, demo, _pools, _docx, _blueprint = _inputs()
    replacements = module.replacement_map(real, demo)

    schema = "https://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    assert module._target_after(schema, replacements, demo) is None

    mail = module._target_after("mailto:georges.anonyme@corp.invalid", replacements, demo)
    assert mail == "mailto:" + demo.identity.email

    profile = module._target_after("https://github.com/georges-anonyme", replacements, demo)
    assert profile == demo.identity.links[0]
