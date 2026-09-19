"""User settings: load and save ``data/settings.json``.

The file is readable on purpose (except the API key, which is stored there too
and must never be committed): the README promises that everything can be read,
backed up and versioned without the app.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

# Overridden in Docker (PAUL_DATA_DIR=/app/data, mounted from ./data).
DATA_DIR = Path(os.environ.get("PAUL_DATA_DIR", "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"

# "auto" follows the language of the offer; the rest force an output language.
LANGUAGES = ["auto", "en", "fr", "de", "es", "it", "nl", "pt"]


class TargetPages(BaseModel):
    """Target document lengths, used by the fit check."""

    cv: int = 2
    letter: int = 1


class Wish(BaseModel):
    """A weighted criterion the ranker scores offers against."""

    label: str
    weight: float = 1.0


class Settings(BaseModel):
    model: str = ""
    api_key: str = ""
    api_base: str = ""
    output_language: str = "auto"
    followup_days: int = 7
    # How many offers the ranker works on at once. 1 is the polite setting for a
    # provider with a strict rate limit; 4 keeps a twenty-offer run short.
    ranking_concurrency: int = 4
    target_pages: TargetPages = Field(default_factory=TargetPages)
    filter_rules: str = ""
    wishes: list[Wish] = Field(default_factory=list)


def load_settings() -> Settings:
    """Return the stored settings, or the defaults on a first run."""
    if not SETTINGS_PATH.exists():
        return Settings()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read {SETTINGS_PATH}: {exc}") from exc
    return Settings.model_validate(raw)


def save_settings(settings: Settings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json")
    SETTINGS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_wishes(text: str) -> list[Wish]:
    """Parse the wishes textarea: one wish per line, ``label`` or ``label: weight``."""
    wishes: list[Wish] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, weight = line.rpartition(":")
        if sep:
            try:
                wishes.append(Wish(label=label.strip(), weight=float(weight.strip())))
                continue
            except ValueError:
                pass  # no numeric weight: the whole line is the label
        wishes.append(Wish(label=line))
    return [wish for wish in wishes if wish.label]


def format_wishes(wishes: list[Wish]) -> str:
    """Render wishes back into the textarea, dropping the default weight."""
    return "\n".join(
        wish.label if wish.weight == 1.0 else f"{wish.label}: {wish.weight:g}"
        for wish in wishes
    )
