"""Build a fictional Paul workspace, for screenshots, videos and manual checks.

The workspace goes through the app's own stores and services, so what the board
shows is what the code produces rather than a hand-written imitation of it:

* the profile is ``app/examples/profile.example.yaml``, the file the tests
  already validate;
* every offer is read by the real HTML cleaner, which is also what parses its
  application form — that part is code, and needs no model;
* the verdicts are built by ``ranking.score.build_score``, so the weights and the
  total are computed, never typed in, and the fingerprint is the real one: the
  board shows scored offers, not "out of date" ones;
* the prepared folder goes through ``writer.service.save``, which renders the
  DOCX, runs the grounding check and computes the ATS coverage in code.

The two model steps — extracting an offer, writing the documents — are the only
things it cannot replay. The verdicts are therefore fixtures, and the documents
are written for the fictional profile. Everything around them is real.

Run it against a throwaway directory, never against ``./data``::

    python scripts/seed_demo.py --data-dir ./demo-data --model openai/gpt-4o

Set ``--model`` to the model you will actually use. A stored score carries a
fingerprint of what produced it, model included, so typing a different model in
the settings later would flag every seeded offer as "out of date".

Then point the app at the same directory (``PAUL_DATA_DIR=./demo-data``), or mount
it as ``/app/data`` in Docker.

Re-seeding rebuilds the offers and the documents but keeps the API key, the API
base and the model already stored in the workspace, and it keeps ``templates/``:
a template imported from a real CV is not demo data, and re-importing it after
every take would be absurd. ``scripts/anonymize_template.py`` writes that folder.

The directory has to be empty, or to carry the marker this script writes: a
directory that already holds a profile, offers or settings is refused rather than
emptied.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "app" / "examples" / "demo"
EXAMPLE_PROFILE = ROOT / "app" / "examples" / "profile.example.yaml"
MARKER = "demo-workspace.json"
MARKER_VERSION = 1
# Kept across a re-seed: an imported template belongs to the user, not to the demo.
TEMPLATES = "templates"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a fictional Paul workspace, for screenshots and videos."
    )
    parser.add_argument(
        "--data-dir",
        default="demo-data",
        help="where to write the workspace (default: ./demo-data)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="LiteLLM model string to store in the demo settings",
    )
    args = parser.parse_args(argv)

    # ``DATA_DIR`` is read when ``app.config`` is imported, so the environment has
    # to hold the target before the first app import. ``tests/conftest.py`` does
    # the same thing, for the same reason.
    os.environ["PAUL_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    report = seed(model=args.model)
    print(f"Demo workspace written to {report['data_dir']}")
    for line in report["offers"]:
        print(f"  · {line}")
    print(f"Prepared folder: {report['prepared']}")
    print(f"Model stored: {report['model'] or '(none — set it before recording)'}")
    if report["kept_key"]:
        print("The API key already in the workspace was kept.")
    print()
    print("The profile is the fictional Camille Moreau. If the board shows another")
    print("name, you are looking at your own data, not at the demo.")
    return 0


def seed(*, model: str = "", today: date | None = None) -> dict:
    """Write the whole demo workspace into the configured data directory."""
    from app.config import DATA_DIR, Settings, Wish, load_settings, save_settings
    from app.models import Elimination, Offer, Profile, ScoringGrid
    from app.offers import clean as offers_clean
    from app.offers import store as offers_store
    from app.profiler import store as profile_store
    from app.ranking import score as ranking_score
    from app.ranking import service as ranking_service
    from app.ranking import store as ranking_store
    from app.tracker import service as tracker_service
    from app.tracker import store as tracker_store

    when = today or tracker_service.today_utc()
    fixture = yaml.safe_load((DEMO / "offers.yaml").read_text(encoding="utf-8"))

    data_dir = Path(DATA_DIR)
    # Read before emptying: a second take keeps the provider settings the user has
    # already pasted, and only the demo data is rebuilt.
    previous = load_settings() if (data_dir / MARKER).exists() else Settings()
    _reset(data_dir)

    settings = Settings(
        model=model or previous.model,
        api_key=previous.api_key,
        api_base=previous.api_base,
        filter_rules=fixture["settings"]["filter_rules"].strip(),
        wishes=[Wish(**wish) for wish in fixture["settings"]["wishes"]],
    )
    save_settings(settings)

    profile = profile_store.save_profile(
        Profile.model_validate(yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8")))
    )
    profile_store.save_cv_text((DEMO / "cv.txt").read_text(encoding="utf-8"))

    written: list[str] = []
    prepared: tuple[dict, object] | None = None

    # Reversed: the board lists offers by descending id, so the last one written
    # is the first one shown, and the fixture reads best offer first.
    for entry in reversed(fixture["offers"]):
        html = _fragment(entry)
        cleaned = offers_clean.clean_fragment(html)
        offer = Offer(**entry["offer"], form=cleaned.form)
        record = offers_store.save_offer(
            offer, raw=html, cleaned=cleaned.text, source="html", url=entry.get("url", "")
        )
        written.append(f"{offer.company} — {offer.title}")

        if "elimination" in entry:
            elimination = Elimination(eliminated=True, **entry["elimination"])
            score = None
        else:
            grid = ScoringGrid(**entry["score"])
            elimination, score = Elimination(), ranking_score.build_score(grid)

        ranking_store.save_ranking(
            record.id,
            elimination=elimination,
            override="",
            score=score,
            fingerprint=ranking_service.fingerprint(settings, profile, record.offer),
        )

        if entry.get("status"):
            tracker_store.save_application(
                record.id,
                status=entry["status"],
                applied_on=_days_ago(when, entry.get("applied_days_ago")),
                last_contact=_days_ago(when, entry.get("last_contact_days_ago")),
                notes="",
            )

        if entry.get("prepared"):
            prepared = (entry, record)

    folder = _prepare(prepared, profile, settings, when) if prepared else ""
    _mark(data_dir)

    return {
        "data_dir": str(data_dir),
        "offers": written,
        "prepared": folder,
        "model": settings.model,
        "kept_key": bool(previous.api_key),
    }


def _prepare(prepared, profile, settings, when: date) -> str:
    """Write the documents of the prepared offer, through the writer's own save."""
    from app.tracker import store as tracker_store
    from app.writer import service as writer_service
    from app.writer import store as writer_store

    entry, record = prepared
    folder = writer_store.resolve_folder(record, when)
    writer_store.create(folder)
    writer_store.save_offer(folder, record, _fragment(entry))
    writer_store.ensure_notes(folder)
    if entry.get("notes"):
        writer_store.write_text(folder, writer_store.NOTES_MD, entry["notes"])

    documents = DEMO / "documents" / entry["slug"]
    writer_service.save(
        settings,
        profile,
        record,
        folder=folder,
        cv_source=(documents / "cv.md").read_text(encoding="utf-8"),
        letter_source=(documents / "letter.md").read_text(encoding="utf-8"),
        answers_source=_answers_markdown(record.offer, profile, entry.get("open_answers") or []),
    )
    tracker_store.set_folder(record.id, folder)
    return folder


def _answers_markdown(offer, profile, open_answers: list[str]) -> str:
    """The form's answers: the facts come from the profile, the open ones do not.

    ``writer.service.save`` rewrites the file from this, matching answers to
    questions by position, so the order here is the order of the form.
    """
    from app.models import FormAnswer
    from app.writer import answers, markdown

    slots = answers.resolve(offer.form, profile)
    remaining = list(open_answers)
    filled = []
    for question, slot in zip(offer.form, slots, strict=True):
        if slot is not None:
            filled.append(slot)
            continue
        filled.append(
            FormAnswer(
                question=answers.question_title(question),
                answer=remaining.pop(0) if remaining else "",
                source="generated",
                max_length=question.max_length,
            )
        )
    if remaining:
        raise ValueError(
            f"The form of {offer.company} has fewer open questions than the fixture "
            f"provides answers for ({len(remaining)} left over)."
        )
    return markdown.render_answers(filled)


def _fragment(entry: dict) -> str:
    return (DEMO / "html" / entry["html"]).read_text(encoding="utf-8")


def _days_ago(when: date, days: int | None) -> str:
    if not days:
        return ""
    return (when - timedelta(days=days)).isoformat()


def _mark(data_dir: Path) -> None:
    """Leave the marker that says this directory is ours to empty."""
    payload = {"created_by": "scripts/seed_demo.py", "version": MARKER_VERSION}
    (data_dir / MARKER).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _reset(data_dir: Path) -> None:
    """Empty the workspace, refusing a directory this script did not create.

    A directory holding anything but our own marker is somebody's real workspace:
    it is refused, never emptied. Hidden files are ignored, so a stray
    ``.DS_Store`` does not stand in the way, and ``templates/`` is kept: it holds
    the imported template, which is the user's own file rather than demo data.
    """
    if not data_dir.is_dir():
        data_dir.mkdir(parents=True, exist_ok=True)
        return

    children = [
        child
        for child in data_dir.iterdir()
        if not child.name.startswith(".") and child.name != TEMPLATES
    ]
    if children and not (data_dir / MARKER).exists():
        raise SystemExit(
            f"{data_dir} is not empty and was not written by this script.\n"
            "Point --data-dir at a throwaway directory, or empty it yourself first."
        )
    for child in children:
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
