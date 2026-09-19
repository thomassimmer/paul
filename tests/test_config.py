from __future__ import annotations

import json

from app.config import (
    SETTINGS_PATH,
    Settings,
    Wish,
    format_wishes,
    load_settings,
    parse_wishes,
    save_settings,
)


def test_defaults_when_no_file():
    settings = load_settings()
    assert settings.model == ""
    assert settings.output_language == "auto"
    assert settings.followup_days == 7
    assert settings.target_pages.cv == 2
    assert settings.target_pages.letter == 1
    assert settings.wishes == []


def test_save_then_load_roundtrip():
    original = Settings(
        model="anthropic/claude-sonnet-4-5",
        api_key="sk-secret",
        output_language="fr",
        followup_days=10,
        wishes=[Wish(label="startup", weight=3.0)],
    )
    save_settings(original)

    reloaded = load_settings()
    assert reloaded == original
    # Written as readable JSON, as the README promises.
    on_disk = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    assert on_disk["model"] == "anthropic/claude-sonnet-4-5"


def test_parse_wishes():
    text = """
    # comment
    startup: 3
    funded
    scientific domain: 2.5
    remote: not-a-number
    """
    wishes = parse_wishes(text)
    assert wishes == [
        Wish(label="startup", weight=3.0),
        Wish(label="funded", weight=1.0),
        Wish(label="scientific domain", weight=2.5),
        Wish(label="remote: not-a-number", weight=1.0),
    ]


def test_format_wishes_omits_default_weight():
    wishes = [Wish(label="startup", weight=3.0), Wish(label="funded")]
    assert format_wishes(wishes) == "startup: 3\nfunded"


def test_wishes_roundtrip():
    text = "startup: 3\nfunded\nremote: 2"
    assert format_wishes(parse_wishes(text)) == text
