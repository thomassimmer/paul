"""Tests for the applications pages: list, prepare, review, save, download.

Preparing and regenerating run in the background, so most tests replace the
scheduling with an inline run, exactly like the ranker's tests do.
"""

from __future__ import annotations

import re

from app.config import Settings, save_settings
from app.llm import LLMError
from app.models import (
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
from app.writer import jobs, store

MODEL = "openai/gpt-4o"


def _record(offer_id: int):
    record = offers_store.load_offer(offer_id)
    assert record is not None
    return record


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
                highlights=["Cut ingestion latency by 60%"],
            )
        ],
    )


def _cv_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="contact", text="camille@example.com"),
        DraftLine(role="section_title", text="Experience"),
        DraftLine(role="entry_title", text="Lead Backend Engineer — Acme"),
        DraftLine(
            role="bullet",
            text="Cut ingestion latency by 60%",
            source_ids=["exp-acme-2022"],
        ),
        DraftLine(role="section_title", text="Skills"),
        DraftLine(role="skill_line", text="Rust"),
    ]


def _letter_lines() -> list[DraftLine]:
    return [
        DraftLine(role="name", text="Camille Moreau"),
        DraftLine(role="salutation", text="Dear hiring team,"),
        DraftLine(
            role="body_text",
            text="I cut ingestion latency by 60%.",
            source_ids=["exp-acme-2022"],
        ),
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


def _patch_drafts(monkeypatch) -> None:
    async def tailor_cv(
        settings, *, offer, profile, blueprint, target_pages, instruction="", current=""
    ):
        return _cv_lines()

    async def write_letter(
        settings, *, offer, profile, blueprint, target_pages, instruction="", current=""
    ):
        return _letter_lines()

    async def answer_questions(
        settings, *, offer, profile, questions, instruction="", current=""
    ):
        return [
            DraftAnswer(question=question, answer="Because of the mission.")
            for question, _ in questions
        ]

    monkeypatch.setattr("app.writer.draft.tailor_cv", tailor_cv)
    monkeypatch.setattr("app.writer.draft.write_letter", write_letter)
    monkeypatch.setattr("app.writer.draft.answer_questions", answer_questions)
    monkeypatch.setattr("app.templates_engine.pdf.count_pages", lambda *args, **kwargs: 1)


def _run_inline(monkeypatch) -> None:
    """Replace the background scheduling with an inline run, so tests are deterministic."""

    async def inline(
        settings,
        profile,
        record,
        *,
        kind,
        section="",
        instruction="",
        from_current=False,
        folder="",
    ):
        job = jobs.remember(
            jobs.build_job(
                record,
                kind=kind,
                section=section,
                instruction=instruction,
                from_current=from_current,
                folder=folder,
            )
        )
        assert job is not None
        await jobs.run(job, settings, profile, record)
        return job

    monkeypatch.setattr("app.writer.jobs.start_job", inline)


def _setup(monkeypatch) -> int:
    _seed_profile()
    _configure()
    _patch_drafts(monkeypatch)
    return _seed_offer()


def _prepare(client, offer_id: int) -> None:
    client.post(f"/applications/{offer_id}/prepare")


# --- preparing -----------------------------------------------------------------


def test_preparing_needs_a_profile(client):
    offer_id = _seed_offer()
    _configure()
    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)
    assert "Import your CV first" in response.text
    assert store.list_folders() == []
    assert jobs.current() is None


def test_preparing_needs_a_model(client):
    _seed_profile()
    offer_id = _seed_offer()
    save_settings(Settings())
    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)
    assert "No model configured" in response.text
    assert jobs.current() is None


def test_a_missing_offer_is_reported(client):
    response = client.post("/applications/999/prepare", follow_redirects=True)
    assert "no longer exists" in response.text


def test_prepare_starts_a_background_job(client, monkeypatch):
    offer_id = _setup(monkeypatch)

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    job = jobs.current()
    assert job is not None and job.kind == "prepare"


def test_a_second_preparation_is_refused_while_one_runs(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert "already running" in response.text


def test_prepare_writes_the_folder_and_the_review_reads_it(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert response.status_code == 200
    assert "Last preparation" in response.text
    assert "Open the offer" in response.text
    assert store.list_folders() != []

    page = client.get(f"/offers/{offer_id}")
    assert page.status_code == 200
    assert "Grounding of the CV" in page.text
    assert "100%" in page.text
    assert 'name="cv"' in page.text
    assert 'name="letter"' in page.text
    assert 'name="answers"' in page.text
    assert "from your profile" in page.text
    assert "Because of the mission." in page.text
    assert "supported by your profile" in page.text


def test_preparing_again_reuses_the_folder(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    first = store.list_folders()

    client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert store.list_folders() == first


def test_a_failing_preparation_is_reported_in_the_panel(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)

    async def failing(
        settings, *, offer, profile, blueprint, target_pages, instruction="", current=""
    ):
        raise LLMError("provider is down")

    monkeypatch.setattr("app.writer.draft.tailor_cv", failing)

    response = client.post(f"/applications/{offer_id}/prepare", follow_redirects=True)

    assert "provider is down" in response.text
    assert "Last preparation" in response.text
    assert "failed" in response.text


def test_stopping_and_dismissing_a_job(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    response = client.post("/applications/cancel", follow_redirects=True)
    assert "Stopping" in response.text

    response = client.post("/applications/dismiss", follow_redirects=True)
    assert jobs.current() is None
    assert "Preparation in progress" not in response.text

    response = client.post("/applications/cancel", follow_redirects=True)
    assert "Nothing is running" in response.text


def test_the_offer_page_shows_the_documents_of_a_prepared_offer(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/offers/{offer_id}"

    page = client.get(f"/offers/{offer_id}")
    assert "Grounding of the CV" in page.text


# --- saving and regenerating ---------------------------------------------------


def test_saving_the_cv(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/save",
        data={
            "cv": "[name] Camille Moreau\n[bullet] Cut ingestion latency by 60% {exp-acme-2022}\n"
        },
        follow_redirects=True,
    )

    assert "Saved: CV" in response.text
    folder = store.list_folders()[0]
    written = store.read_text(folder, store.CV_MD) or ""
    assert "[section_title]" not in written
    assert "[name] Camille Moreau" in written
    # The letter was not posted, so it is untouched.
    assert "[salutation]" in (store.read_text(folder, store.LETTER_MD) or "")


def test_saving_with_htmx_swaps_the_documents_in_place(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/save",
        data={"letter": "[name] Camille Moreau\n[salutation] Dear hiring team,\n"},
        headers={"HX-Request": "true"},
    )

    # No redirect: the documents come back for an in-place swap, so the page keeps
    # its scroll position instead of jumping back to the top.
    assert response.status_code == 200
    assert '<div id="documents">' in response.text
    assert "Saved: letter" in response.text
    assert "Cover letter" in response.text

    folder = store.list_folders()[0]
    assert "[name] Camille Moreau" in (store.read_text(folder, store.LETTER_MD) or "")


def test_a_failed_save_with_htmx_stays_on_the_page(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/save",
        data={},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert '<div id="documents">' in response.text
    assert "Nothing to save" in response.text


def test_saving_nothing_is_reported(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(f"/applications/{offer_id}/save", data={}, follow_redirects=True)

    assert "Nothing to save" in response.text


def test_saving_is_refused_while_a_job_runs(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    jobs.remember(
        jobs.build_job(_record(offer_id), kind="regenerate", section="cv")
    )

    response = client.post(
        f"/applications/{offer_id}/save", data={"cv": "[name] X"}, follow_redirects=True
    )

    assert "already running" in response.text


def test_regenerating_a_section_starts_a_job(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": "shorter"},
        follow_redirects=True,
    )

    assert "Regenerating the CV in the background" in response.text
    job = jobs.current()
    assert job is not None and job.kind == "regenerate"
    assert job.section == "cv" and job.instruction == "shorter"
    assert job.from_current is False  # the checkbox is off unless it is posted


def test_regenerating_from_the_current_version_is_opt_in(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": "shorter", "from_current": "1"},
        follow_redirects=True,
    )

    job = jobs.current()
    assert job is not None and job.from_current is True


def test_regenerating_needs_a_model(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    save_settings(Settings())

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": ""},
        follow_redirects=True,
    )

    assert "No model configured" in response.text


def test_regenerating_an_unknown_section_is_refused(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "poster"},
        follow_redirects=True,
    )

    assert "Unknown section" in response.text


def test_the_job_poll_only_redraws_the_documents_when_they_changed(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    jobs.remember(
        jobs.build_job(_record(offer_id), kind="regenerate", section="cv")
    )

    page = client.get(f"/offers/{offer_id}")
    assert f'hx-get="/offers/{offer_id}/progress"' in page.text
    assert "Regeneration in progress" in page.text

    # Every two seconds the answer is the panel alone. Redrawing the framed previews
    # would reload them for nothing, and htmx shuttling the preserved frames through
    # its pantry is what dragged the page to the bottom.
    polled = client.get(f"/offers/{offer_id}/progress")
    assert polled.status_code == 200
    assert 'id="job"' in polled.text
    assert "hx-swap-oob" not in polled.text

    # The fetch that follows a job starting asks for the documents, so the section
    # being rewritten is locked and covered.
    pushed = client.get(
        f"/offers/{offer_id}/progress", headers={"X-Push-Documents": "true"}
    )
    assert 'id="documents" hx-swap-oob="outerHTML"' in pushed.text
    assert 'id="quick-nav"' in pushed.text
    assert 'href="#cv"' in pushed.text


def test_a_finished_job_pushes_the_documents_unasked(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)  # leaves a finished job behind

    response = client.get(f"/offers/{offer_id}/progress")

    # The rewrite landed: the documents come back unlocked, without being asked for.
    assert 'id="documents" hx-swap-oob="outerHTML"' in response.text


# --- downloads -----------------------------------------------------------------


def test_downloading_a_markdown_document(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}/download/cv.md")

    assert response.status_code == 200
    assert "Camille Moreau" in response.text


def test_downloading_an_unknown_file_is_refused(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}/download/secrets.txt", follow_redirects=True)

    assert "No such document" in response.text


def test_a_pdf_export_without_libreoffice_says_so(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    monkeypatch.setattr("app.writer.router.pdf.to_pdf", lambda data: None)

    response = client.get(f"/applications/{offer_id}/download/cv.pdf", follow_redirects=True)

    assert "needs LibreOffice" in response.text


# --- the framed preview ---------------------------------------------------------


def _framing(monkeypatch) -> None:
    """Pretend LibreOffice is installed, so the review screen frames the PDF."""
    monkeypatch.setattr("app.writer.router.pdf.converter", lambda: "soffice")


def test_the_review_frames_the_saved_documents_when_libreoffice_is_there(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    _framing(monkeypatch)

    page = client.get(f"/offers/{offer_id}")

    assert "doc-pdf" in page.text
    assert f'src="/applications/{offer_id}/preview/cv?v=' in page.text
    assert f'src="/applications/{offer_id}/preview/letter?v=' in page.text


def test_the_review_falls_back_to_html_without_libreoffice(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    monkeypatch.setattr("app.writer.router.pdf.converter", lambda: None)

    page = client.get(f"/offers/{offer_id}")

    assert 'class="doc-preview"' in page.text
    assert "doc-pdf" not in page.text
    assert "Install LibreOffice" in page.text


def test_the_preview_serves_the_converted_pdf_inline(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    monkeypatch.setattr("app.writer.router.pdf.to_pdf", lambda data: b"%PDF-1.4 fake")

    response = client.get(f"/applications/{offer_id}/preview/cv")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="cv.pdf"'
    assert response.content == b"%PDF-1.4 fake"


def test_the_preview_explains_a_failed_conversion(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    monkeypatch.setattr("app.writer.router.pdf.to_pdf", lambda data: None)

    response = client.get(f"/applications/{offer_id}/preview/cv")

    assert response.status_code == 503
    assert "needs LibreOffice" in response.text


def test_the_preview_refuses_an_unknown_document(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.get(f"/applications/{offer_id}/preview/secrets")

    assert response.status_code == 404


# --- navigating a long review page ---------------------------------------------


def test_the_page_links_to_its_sections(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}")

    assert 'class="quick-nav"' in page.text
    for anchor in ("overview", "ranking", "checks", "cv", "letter", "answers", "tracking"):
        assert f'href="#{anchor}"' in page.text
        assert f'id="{anchor}"' in page.text


def test_the_documents_and_the_tracking_are_grouped_under_application(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}").text
    group = page.index('class="quick-nav-group">Application<')

    for anchor in ("checks", "cv", "letter", "answers", "tracking"):
        assert group < page.index(f'href="#{anchor}"')


def test_the_quick_navigation_skips_the_sections_that_do_not_exist_yet(client, monkeypatch):
    offer_id = _setup(monkeypatch)

    page = client.get(f"/offers/{offer_id}").text

    # Nothing is prepared, so there is nothing to point at.
    assert 'href="#tracking"' in page
    assert 'href="#cv"' not in page
    assert 'id="cv"' not in page


def test_the_checks_can_be_folded_away(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}")

    assert '<details class="collapse">' in page.text
    assert "<summary>" in page.text


def test_the_pdf_frame_hides_the_viewer_toolbar(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    _framing(monkeypatch)

    page = client.get(f"/offers/{offer_id}")

    assert "#toolbar=0" in page.text
    assert "&amp;navpanes=0" in page.text


# --- adding a line by hand ------------------------------------------------------


def test_the_editors_offer_a_checkbox_to_build_on_the_current_version(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}").text

    assert 'name="from_current"' in page
    assert "Improve the current version" in page


def test_the_editors_offer_a_button_per_role(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}")

    assert 'data-insert-role="bullet"' in page.text  # CV
    assert 'data-insert-role="salutation"' in page.text  # letter
    assert 'data-insert-role="fixed"' not in page.text  # decorative, not typed


def test_the_editors_have_the_ids_the_insert_needs(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}")

    assert 'id="cv-source"' in page.text
    assert 'id="letter-source"' in page.text


# --- starting a job without leaving the page ------------------------------------


def test_starting_a_preparation_with_htmx_swaps_the_job_panel(client, monkeypatch):
    offer_id = _setup(monkeypatch)

    response = client.post(
        f"/applications/{offer_id}/prepare",
        data={"next": f"/offers/{offer_id}"},
        headers={"HX-Request": "true"},
    )

    # Not a redirect: a placeholder pulls the real panel in place.
    assert response.status_code == 200
    assert 'id="job"' in response.text
    assert f'hx-get="/offers/{offer_id}/progress"' in response.text
    job = jobs.current()
    assert job is not None and job.kind == "prepare"


def test_starting_a_regeneration_with_htmx_swaps_the_job_panel(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    response = client.post(
        f"/applications/{offer_id}/regenerate",
        data={"section": "cv", "instruction": "shorter"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert f'hx-get="/offers/{offer_id}/progress"' in response.text


def _locked(page: str, element_id: str) -> bool:
    match = re.search(rf'<textarea id="{element_id}"[^>]*>', page)
    assert match is not None, f"no textarea {element_id}"
    return "readonly" in match.group(0)


def test_a_regeneration_only_locks_the_section_it_rewrites(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    jobs.remember(jobs.build_job(_record(offer_id), kind="regenerate", section="cv"))

    page = client.get(f"/offers/{offer_id}").text

    # The CV is on the bench: covered by the loader, and its editor is locked.
    assert "Rewriting the CV…" in page
    assert 'id="cv-loading"' in page
    assert _locked(page, "cv-source")
    # The letter is not being touched, so it stays editable and uncovered.
    assert "Rewriting the cover letter…" not in page
    assert 'id="letter-loading"' not in page
    assert not _locked(page, "letter-source")


def test_a_preparation_locks_every_editor(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    page = client.get(f"/offers/{offer_id}").text

    assert "Writing the CV…" in page
    assert "Writing the cover letter…" in page
    assert _locked(page, "cv-source")
    assert _locked(page, "letter-source")


def test_nothing_is_locked_when_no_job_runs(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)

    page = client.get(f"/offers/{offer_id}").text

    assert not _locked(page, "cv-source")
    assert not _locked(page, "letter-source")
    assert "doc-loading" not in page


# --- stopping and dismissing without leaving the page ---------------------------


def test_the_job_panel_stops_and_dismisses_in_place(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    page = client.get(f"/offers/{offer_id}").text

    assert 'hx-target="#job"' in page
    assert f'name="poll_url" value="/offers/{offer_id}/progress"' in page


def test_the_job_panel_floats_on_the_offer_page(client, monkeypatch):
    offer_id = _setup(monkeypatch)

    # With no job there is nothing to show, so the page keeps its full height.
    assert 'class="job-toast"' not in client.get(f"/offers/{offer_id}").text

    # A job makes the panel a fixed toast, out of the page flow: the status stays
    # readable from the documents, and the page never shifts under the reader.
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))
    assert 'class="job-toast"' in client.get(f"/offers/{offer_id}").text


def test_the_finished_toast_does_not_link_to_the_page_it_is_on(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)  # the job is done

    page = client.get(f"/offers/{offer_id}").text

    assert "Last preparation" in page
    # The reader is already on the offer page; the link would only reload it.
    assert "Open the offer" not in page


def test_stopping_with_htmx_refreshes_the_panel(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    response = client.post(
        "/applications/cancel",
        data={
            "next": f"/offers/{offer_id}",
            "panel_id": "job",
            "poll_url": f"/offers/{offer_id}/progress",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="job"' in response.text
    assert f'hx-get="/offers/{offer_id}/progress"' in response.text


def test_dismissing_with_htmx_empties_the_panel(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    response = client.post(
        "/applications/dismiss",
        data={"next": f"/offers/{offer_id}", "panel_id": "job"},
        headers={"HX-Request": "true"},
    )

    assert jobs.current() is None
    assert response.status_code == 200
    # Nothing to fetch back: the panel just takes itself off the page.
    assert response.text.strip() == '<div id="job"></div>'


def test_dismissing_leaves_a_job_that_was_already_replaced(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    job = jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    # A toast that decides to close itself a second late must not wipe the run that
    # took its place in the meantime.
    response = client.post(
        "/applications/dismiss",
        data={"next": f"/offers/{offer_id}", "panel_id": "job", "job_id": "not-this-one"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    assert jobs.current() is job


def test_the_done_toast_closes_itself(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    _run_inline(monkeypatch)
    _prepare(client, offer_id)  # the job is done
    job = jobs.current()
    assert job is not None

    page = client.get(f"/offers/{offer_id}").text

    assert 'hx-post="/applications/dismiss"' in page
    assert 'hx-trigger="load delay:1s"' in page
    assert f'"job_id": "{job.id}"' in page


def test_a_posted_panel_id_cannot_inject_markup(client, monkeypatch):
    offer_id = _setup(monkeypatch)
    jobs.remember(jobs.build_job(_record(offer_id), kind="prepare"))

    response = client.post(
        "/applications/cancel",
        data={
            "next": f"/offers/{offer_id}",
            "panel_id": 'job"><script>alert(1)</script>',
            "poll_url": f"/offers/{offer_id}/progress",
        },
        headers={"HX-Request": "true"},
    )

    assert "<script>" not in response.text
    assert 'id="job"' in response.text
