from __future__ import annotations

from app import background
from app.config import Settings, save_settings
from app.llm import LLMError
from app.models import (
    Experience,
    Facts,
    Identity,
    Preferences,
    Profile,
    ProfileDraft,
)
from app.profiler import interview, store

CV_TEXT = (
    "Camille Moreau — Senior Backend Engineer.\n"
    "2022 - 2024: Acme Analytics, Lead Backend Engineer.\n"
    "Rust, Kafka, PostgreSQL, Kubernetes.\n"
)


def _run_inline(monkeypatch) -> None:
    """Draft the profile at once instead of in the background, so tests are deterministic."""

    async def inline(kind, label, work, *, context=None):
        run = background.remember(background.build(kind, label, context=context))
        await background.execute(run, work)
        return run

    monkeypatch.setattr("app.background.start", inline)


def _import(client, monkeypatch, *, follow_redirects: bool = False, **data):
    """Import, running the draft inline, and return the post response."""
    _run_inline(monkeypatch)
    return client.post("/profiler/import", data=data, follow_redirects=follow_redirects)


def _landing(client) -> str:
    """Where the import run sends the page once it is done."""
    response = client.get("/profiler/import/status", follow_redirects=False)
    assert response.status_code == 200
    return response.headers.get("HX-Redirect", "")


def _edit_form(**overrides: str) -> dict[str, str]:
    form = {
        "identity.name": "Camille Moreau",
        "identity.headline": "Senior Backend Engineer",
        "identity.location": "Lyon",
        "identity.email": "",
        "identity.phone": "",
        "identity.links": "",
        "facts.work_authorization": "EU citizen",
        "facts.notice_period": "",
        "facts.salary_expectation": "",
        "facts.relocation": "",
        "facts.languages": "",
        "preferences.target_roles": "",
        "preferences.locations": "",
        "preferences.remote": "",
        "preferences.contract_types": "",
        "preferences.more_of": "",
        "preferences.less_of": "",
        "exp_count": "1",
        "exp.0.id": "",
        "exp.0.company": "Acme",
        "exp.0.title": "Lead Backend Engineer",
        "exp.0.period": "2022-03 / 2024-06",
        "exp.0.context": "",
        "exp.0.team_size": "",
        "exp.0.stack": "Rust, Kafka",
        "exp.0.highlights": "Cut ingestion latency by 60%",
        "edu_count": "1",
        "edu.0.id": "",
        "edu.0.school": "",
        "edu.0.degree": "",
        "edu.0.period": "",
        "edu.0.details": "",
        "proj_count": "1",
        "proj.0.id": "",
        "proj.0.name": "",
        "proj.0.description": "",
        "proj.0.links": "",
        "proj.0.skills": "",
    }
    form.update(overrides)
    return form


def _seed_profile(client) -> Profile:
    client.post("/profiler/edit", data=_edit_form(), follow_redirects=False)
    return _loaded()


def _full_profile() -> Profile:
    """A profile with an experience and its facts already filled in."""
    return Profile(
        experiences=[
            Experience(
                company="Acme",
                title="Engineer",
                period="2022",
                context="Analytics platform",
                stack=["Rust"],
                highlights=["Cut latency"],
            )
        ],
        facts=Facts(work_authorization="EU citizen", languages=["English"]),
        preferences=Preferences(more_of=["Rust"]),
    )


def _turn(*edits: interview.DraftEdit, question: str = "", **fields) -> interview.Turn:
    """A model turn: what it writes, and the question that follows."""
    return interview.Turn(
        edits=list(edits),
        question=interview.DraftQuestion(prompt=question, **fields),
    )


def _model_replies(monkeypatch, *turns: interview.Turn) -> list[dict]:
    """Make the interview's model answer with these turns, in order."""
    queue = list(turns)
    calls: list[dict] = []

    async def fake(settings, profile, *, asked, question="", answer=""):
        calls.append({"asked": asked, "question": question, "answer": answer})
        return queue.pop(0) if queue else interview.Turn()

    monkeypatch.setattr("app.profiler.interview.ask", fake)
    return calls


def _loaded() -> Profile:
    profile = store.load_profile()
    assert profile is not None
    return profile


def test_without_a_profile_the_profile_page_points_to_import(client):
    response = client.get("/profiler", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/import"


def test_import_pasted_text_without_a_model_creates_an_empty_profile(client, monkeypatch):
    response = _import(client, monkeypatch, text=CV_TEXT)
    assert response.status_code == 200
    # Nothing to interview about yet: go and add experiences.
    assert _landing(client) == "/profiler/edit"
    cv_text = store.load_cv_text()
    assert cv_text is not None
    assert cv_text.startswith("Camille Moreau")


def test_import_requires_an_explicit_replace_when_a_profile_exists(client):
    _seed_profile(client)
    response = client.post(
        "/profiler/import", data={"text": CV_TEXT}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/import"
    # The existing profile is untouched.
    assert _loaded().identity.name == "Camille Moreau"


def test_import_with_a_model_uses_the_draft_and_keeps_facts(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    store.save_profile(Profile(facts=Facts(work_authorization="EU citizen")))

    async def fake_draft(settings, cv_text):
        assert "Camille" in cv_text
        return ProfileDraft(
            identity=Identity(name="Camille Moreau"),
            experiences=[Experience(company="Acme", title="Engineer", period="2022")],
        )

    monkeypatch.setattr("app.profiler.service.draft_profile", fake_draft)
    response = _import(client, monkeypatch, text=CV_TEXT, replace="1")

    assert response.status_code == 200
    assert _landing(client) == "/profiler/interview"
    profile = _loaded()
    assert profile.experiences[0].id == "exp-acme-2022"
    assert profile.facts.work_authorization == "EU citizen"


def test_import_reports_a_model_failure_and_keeps_the_cv_text(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))

    async def failing_draft(settings, cv_text):
        raise LLMError("model exploded")

    monkeypatch.setattr("app.profiler.service.draft_profile", failing_draft)
    response = _import(client, monkeypatch, text=CV_TEXT)

    assert response.status_code == 200
    assert _landing(client) == "/profiler/edit"
    assert store.load_cv_text() is not None


def test_import_rejects_an_unsupported_file(client):
    response = client.post(
        "/profiler/import",
        data={},
        files={"file": ("cv.doc", b"whatever", "application/msword")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/import"
    assert store.load_profile() is None


def test_edit_saves_the_profile_and_shows_it(client):
    _seed_profile(client)

    response = client.get("/profiler")
    assert response.status_code == 200
    assert "Acme" in response.text
    assert "Cut ingestion latency by 60%" in response.text

    # The trailing blank experience row is not stored as an experience.
    assert len(_loaded().experiences) == 1


def test_edit_page_renders_the_blank_rows(client):
    response = client.get("/profiler/edit")
    assert response.status_code == 200
    assert 'name="exp_count" value="1"' in response.text


def test_the_interview_page_asks_for_its_question_itself(client):
    """The page is instant; the question lands once the model has read the profile."""
    _seed_profile(client)

    page = client.get("/profiler/interview").text

    assert 'hx-get="/profiler/interview/next"' in page
    assert 'hx-trigger="load"' in page


def test_the_interview_reports_a_missing_model(client):
    _seed_profile(client)

    response = client.get("/profiler/interview/next")

    assert response.status_code == 200
    assert "No model configured" in response.text
    assert "/settings" in response.text


def test_an_answer_is_written_into_the_profile_and_the_next_question_is_asked(client, monkeypatch):
    profile = _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    calls = _model_replies(
        monkeypatch,
        _turn(
            interview.DraftEdit(
                target=f"exp:{profile.experiences[0].id}",
                field="highlights",
                value="Cut p99 latency by 60%",
                source="we cut the p99 latency by 60%",
            ),
            question="How big was the team?",
            topic="Team",
            why="Gives the scale of the role.",
        ),
    )

    response = client.post(
        "/profiler/interview/answer",
        data={"question": "What did you achieve?", "answer": "we cut the p99 latency by 60%"},
    )

    assert response.status_code == 200
    # What was written, then the question that follows it.
    assert "Cut p99 latency by 60%" in response.text
    assert "How big was the team?" in response.text
    assert "Gives the scale of the role." in response.text
    assert _loaded().experiences[0].highlights[-1] == "Cut p99 latency by 60%"
    # Everything was written, so the exchange is remembered for good, and the model
    # was told what had been asked before.
    assert store.asked_questions() == ["What did you achieve?"]
    assert calls[0]["asked"] == []


def test_an_invented_detail_is_refused_and_reported(client, monkeypatch):
    profile = _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    _model_replies(
        monkeypatch,
        _turn(
            interview.DraftEdit(
                target=f"exp:{profile.experiences[0].id}",
                field="highlights",
                value="Doubled revenue in one quarter",
                source="not a word of this is in the answer",
            ),
            question="What next?",
        ),
    )

    response = client.post(
        "/profiler/interview/answer",
        data={"question": "What did you achieve?", "answer": "I worked on the ingestion path"},
    )

    assert response.status_code == 200
    assert "could not be written" in response.text
    assert "Doubled revenue in one quarter" in response.text
    # Nothing the answer does not support reached the profile.
    assert _loaded().experiences[0].highlights == ["Cut ingestion latency by 60%"]
    # Part of the answer was lost, so the question may come back to it.
    assert store.asked_questions() == []


def test_a_merged_line_is_shown_as_a_rewrite(client, monkeypatch):
    profile = _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    _model_replies(
        monkeypatch,
        _turn(
            interview.DraftEdit(
                target=f"exp:{profile.experiences[0].id}",
                field="highlights",
                value="Cut ingestion latency by 60% for 12k daily users",
                source="cut it by 60% for 12k daily users",
                replaces="Cut ingestion latency by 60%",
            ),
            question="What next?",
        ),
    )

    response = client.post(
        "/profiler/interview/answer",
        data={"question": "How much traffic?", "answer": "we cut it by 60% for 12k daily users"},
    )

    assert response.status_code == 200
    assert "rewritten" in response.text
    # The line was merged into, not added beside.
    assert _loaded().experiences[0].highlights == [
        "Cut ingestion latency by 60% for 12k daily users"
    ]


def test_an_htmx_answer_returns_only_the_card(client, monkeypatch):
    _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    _model_replies(monkeypatch, _turn(question="How big was the team?"))

    response = client.post(
        "/profiler/interview/answer",
        data={"question": "What did you achieve?", "answer": "Something"},
        headers={"HX-Request": "true"},
    )

    assert 'id="interview"' in response.text
    assert "<!doctype html>" not in response.text.lower()


def test_skipping_a_question_remembers_it_and_asks_another(client, monkeypatch):
    _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    calls = _model_replies(monkeypatch, _turn(question="What else did you do?"))

    response = client.post(
        "/profiler/interview/skip", data={"question": "What was the hardest part?"}
    )

    assert response.status_code == 200
    assert "What else did you do?" in response.text
    # A skipped question is remembered like any other, so it never comes back.
    assert store.asked_questions() == ["What was the hardest part?"]
    assert calls[0]["answer"] == ""


def test_the_interview_says_when_it_has_nothing_left_to_ask(client, monkeypatch):
    _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    _model_replies(monkeypatch, interview.Turn(finished=True))

    response = client.get("/profiler/interview/next")

    assert response.status_code == 200
    assert "That is everything" in response.text


def test_a_model_failure_keeps_the_question_and_hands_the_answer_back(client, monkeypatch):
    _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))

    async def failing(settings, profile, *, asked, question="", answer=""):
        raise LLMError("provider is down")

    monkeypatch.setattr("app.profiler.interview.ask", failing)
    response = client.post(
        "/profiler/interview/answer",
        data={"question": "What did you achieve?", "answer": "We cut the latency"},
    )

    assert response.status_code == 200
    assert "provider is down" in response.text
    # The question and the typed answer are still there, and nothing was marked asked.
    assert "What did you achieve?" in response.text
    assert "We cut the latency" in response.text
    assert store.asked_questions() == []


def test_forgetting_starts_the_interview_over(client):
    store.remember_question("What was the hardest part?")

    response = client.post("/profiler/interview/forget", follow_redirects=False)

    assert response.status_code == 303
    assert store.asked_questions() == []


def test_interview_needs_an_experience_first(client, monkeypatch):
    _import(client, monkeypatch, text=CV_TEXT)
    response = client.get("/profiler/interview", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/edit"


def test_yaml_editor_saves_and_reports_errors(client):
    valid = "identity:\n  name: Camille\n"
    response = client.post("/profiler/yaml", data={"yaml_text": valid}, follow_redirects=False)
    assert response.status_code == 303
    assert _loaded().identity.name == "Camille"

    response = client.post("/profiler/yaml", data={"yaml_text": "identity: [oops"})
    assert response.status_code == 400
    assert "not a valid profile" in response.text
    # The submitted text is handed back so nothing is lost.
    assert "identity: [oops" in response.text


def test_profile_yaml_can_be_downloaded(client):
    assert client.get("/profiler/profile.yaml").status_code == 404
    _seed_profile(client)
    response = client.get("/profiler/profile.yaml")
    assert response.status_code == 200
    assert "application/yaml" in response.headers["content-type"]
    assert response.text.startswith("# Paul profile")


def test_dashboard_links_to_the_profiler(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "/profiler/import" in response.text


def test_the_profile_page_has_a_quick_navigation_over_its_sections(client):
    client.post(
        "/profiler/edit",
        data=_edit_form(**{"edu.0.school": "INSA Lyon", "edu.0.degree": "MSc"}),
        follow_redirects=False,
    )

    page = client.get("/profiler").text

    assert 'class="quick-nav"' in page
    for anchor in ("identity", "facts", "experience", "education", "preferences"):
        assert f'href="#{anchor}"' in page
        assert f'id="{anchor}"' in page


def test_the_quick_navigation_skips_a_card_that_is_not_rendered(client):
    store.save_profile(Profile(identity=Identity(name="Camille Moreau")))

    page = client.get("/profiler").text

    assert 'href="#identity"' in page
    assert 'href="#preferences"' in page
    # No education and no project: the card is not rendered, so neither is the
    # link that would go nowhere.
    assert 'href="#education"' not in page
    assert 'id="education"' not in page


# --- The profile page and the import ------------------------------------------


def test_the_profile_page_shows_what_was_written(client):
    _seed_profile(client)

    page = client.get("/profiler").text

    assert "Acme" in page
    assert "Cut ingestion latency by 60%" in page


def test_a_new_import_forgets_the_interview_questions(client, monkeypatch):
    store.save_profile(_full_profile())
    store.remember_question("What was the hardest part?")

    _import(client, monkeypatch, text=CV_TEXT, replace="1")

    # The interview was built from the profile the import replaced.
    assert store.asked_questions() == []