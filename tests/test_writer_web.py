"""Tests for the applications pages: list, prepare, review, save, download."""

from __future__ import annotations

from app.config import Settings, save_settings
from app.models import (
    Achievement,
    DraftAnswer,
    DraftLine,
    Experience,
    Facts,
    FormQuestion,
    Identity,
    Keyword,
    OfferDraft,
    Profile,
)
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.writer import store

MODEL = "openai/gpt-4o"


def _profile() -> Profile:
    return Profile(
        identity=Identity(name="Camille Moreau", email="camille@example.com"),
        facts=Facts(notice_period="One month"),
        skills={"Languages": ["Rust"]},
        experiences=[
            Experience(
                id="exp-acme-2022",
                company="Acme",
                title="Lead Backend Engineer",
                achievements=[
                    Achievement(
                        id="exp-acme-2022-a1",
                        text="Cut ingestion latency by 60%",
                        metrics=["60%"],
                    )
                ],
            )
        ],
    )


def _cv_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="contact", text="camille@example.com"),
        DraftLine(role="section_title", text="Experience"),
        DraftLine(role="entry_title", text="Lead Backend Engineer — Acme"),
        DraftLine(role="bullet", text="Cut ingestion latency by 60%", achievement_ids=["exp-acme-2022-a1"]),
        DraftLine(role="section_title", text="Skills"),
        DraftLine(role="skill_line", text="Rust"),
    ]


def _letter_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="salutation", text="Dear hiring team,"),
        DraftLine(role="body_text", text="I cut ingestion latency by 60%.", achievement_ids=["exp-acme-2022-a1"]),
        DraftLine(role="closing", text="Sincerely,"),
    ]


def _seed_offer() -> int:
    offer = OfferDraft(
        title="Senior Backend Engineer",
        company="Acme",
        language="en",
        keywords=[Keyword(term="Rust")],
    ).to_offer(
        [
            FormQuestion(label="What is your notice period?", name="notice", max_length=200),
            FormQuestion(label="Why us?", name="why", max_length=400),
        ]
    )
    return offers_store.save_offer(offer, raw="<p>raw</p>", cleaned="A Rust role", source="html").id


def _seed_profile() -> None:
    profile_store.save_profile(_profile())


def _configure() -> None:
    save_settings(Settings(model=MODEL))


def _patch(monkeypatch) -> None:
    async def tailor_cv(settings, *, offer, profile, blueprint, target_pages, instruction=""):
        return _cv_lines()

    async def write_letter(settings, *, offer, profile, blueprint, target_pages, instruction=""):
        return _letter_lines()

    async def answer_questions(settings, *, offer, profile, questions, instruction=""):
        return [DraftAnswer(question=question, answer="Because of the mission.") for question, _ in questions]

    monkeypatch.setattr("app.writer.draft.tailor_cv", tailor_cv)
    monkeypatch.setattr("app.writer.draft.write_letter", write_letter)
    monkeypatch.setattr("app.writer.draft.answer_questions", answer_questions)
    monkeypatch.setattr("app.templates_engine.pdf.count_pages", lambda *args, **kwargs: 1)


def _prepare(client, offer_id: int) -> None:
    client.post(f"/applications/{offer_id}/prepare")


# --- the list page -------------------------------------------------------------


def test_the_page_renders_without_a_profile(client):
    _seed_offer()
    response = client.get("/applications")
    assert response.status_code == 200
    assert "Import your CV" in response.text
    assert "Not prepared" in response.text


def test_the_page_warns_without_a_model(client):
    _seed_profile()
    _seed_offer()
    assert "No model configured" in client.get("/applications").text


def test_the_nav_links_to_the_applications(client):
    assert 'href="/applications"' in client.get("/").text


# --- preparing -----------------------------------------------------------------


def test_preparing_needs_a_profile(client):
    offer_id = _seed_offer()
    _configure()
    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)
    assert "Import your CV first" in response.text
    assert store.list_folders() == []


def test_preparing_needs_a_model(client):
    _seed_profile()
    offer_id = _seed_offer()
    save_settings(Settings())
    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)
    assert "No model configured" in response.text


def test_a_missing_offer_is_reported(client):
    response = client.post("/applications/999/prepare", follow_redirects=True)
    assert "no longer exists" in response.text


def test_prepare_then_review(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert response.status_code == 200
    assert "Application prepared in" in response.text
    assert "Grounding of the CV" in response.text
    assert "100%" in response.text
    assert 'name="cv"' in response.text
    assert 'name="letter"' in response.text
    assert 'name="answers"' in response.text
    # The fact came from the profile; the open question was drafted.
    assert "from your profile" in response.text
    assert "Because of the mission." in response.text
    assert "supported by your profile" in response.text


def test_preparing_again_reuses_the_folder(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)
    first = store.list_folders()

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert "Application prepared in" in response.text
    assert store.list_folders() == first


def test_the_review_page_needs_a_prepared_offer(client):
    _seed_profile()
    _configure()
    offer_id = _seed_offer()
    response = client.get(f"/applications/{offer_id}", follow_redirects=True)
    assert "not prepared yet" in response.text


# --- saving and regenerating ---------------------------------------------------


def test_saving_the_cv(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/save",
        data={"cv": "[name] Camille Moreau\n[bullet] Cut ingestion latency by 60% {exp-acme-2022-a1}\n"},
        follow_redirects=True,
    )

    assert "Saved: CV" in response.text
    folder = store.list_folders()[0]
    written = store.read_text(folder, store.CV_MD) or ""
    assert "[section_title]" not in written
    assert "[name] Camille Moreau" in written
    # The letter was not posted, so it is untouched.
    assert "[salutation]" in (store.read_text(folder, store.LETTER_MD) or "")


def test_saving_nothing_is_reported(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)

    response = client.post(f"/applications/{offer_id}/save", data={}, follow_redirects=True)

    assert "Nothing to save" in response.text


def test_regenerating_a_section(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": "shorter"},
        follow_redirects=True,
    )

    assert "CV regenerated" in response.text


def test_regenerating_needs_a_model(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)
    save_settings(Settings())

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": ""},
        follow_redirects=True,
    )

    assert "No model configured" in response.text


# --- downloads -----------------------------------------------------------------


def test_downloading_a_markdown_document(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}/download/cv.md")

    assert response.status_code == 200
    assert "Camille Moreau" in response.text


def test_downloading_an_unknown_file_is_refused(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}/download/secrets.txt", follow_redirects=True)

    assert "No such document" in response.text


def test_a_pdf_export_without_libreoffice_says_so(client, monkeypatch):
    _seed_profile()
    _configure()
    _patch(monkeypatch)
    offer_id = _seed_offer()
    _prepare(client, offer_id)
    monkeypatch.setattr("app.writer.router.pdf.to_pdf", lambda data: None)

    response = client.get(f"/applications/{offer_id}/download/cv.pdf", follow_redirects=True)

    assert "needs LibreOffice" in response.text
