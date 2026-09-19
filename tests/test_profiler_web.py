from __future__ import annotations

from app.config import Settings, save_settings
from app.llm import LLMError
from app.models import Achievement, Experience, Facts, Identity, Profile, ProfileDraft
from app.profiler import store

CV_TEXT = (
    "Camille Moreau — Senior Backend Engineer.\n"
    "2022 - 2024: Acme Analytics, Lead Backend Engineer.\n"
    "Rust, Kafka, PostgreSQL, Kubernetes.\n"
)


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
        "skills": "Languages: Rust, Python",
        "exp_count": "1",
        "exp.0.id": "",
        "exp.0.company": "Acme",
        "exp.0.title": "Lead Backend Engineer",
        "exp.0.period": "2022-03 / 2024-06",
        "exp.0.context": "",
        "exp.0.team_size": "",
        "exp.0.stack": "Rust, Kafka",
        "exp.0.difficulties": "",
        "ach_count": "1",
        "ach.0.id": "",
        "ach.0.exp_index": "0",
        "ach.0.text": "Cut ingestion latency by 60%",
        "ach.0.metrics": "",
        "ach.0.skills": "",
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


def _loaded() -> Profile:
    profile = store.load_profile()
    assert profile is not None
    return profile


def test_without_a_profile_the_profile_page_points_to_import(client):
    response = client.get("/profiler", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/import"


def test_import_pasted_text_without_a_model_creates_an_empty_profile(client):
    response = client.post(
        "/profiler/import", data={"text": CV_TEXT}, follow_redirects=False
    )
    assert response.status_code == 303
    # Nothing to interview about yet: go and add experiences.
    assert response.headers["location"] == "/profiler/edit"
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
    response = client.post(
        "/profiler/import", data={"text": CV_TEXT, "replace": "1"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/interview"
    profile = _loaded()
    assert profile.experiences[0].id == "exp-acme-2022"
    assert profile.facts.work_authorization == "EU citizen"


def test_import_reports_a_model_failure_and_keeps_the_cv_text(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))

    async def failing_draft(settings, cv_text):
        raise LLMError("model exploded")

    monkeypatch.setattr("app.profiler.service.draft_profile", failing_draft)
    response = client.post(
        "/profiler/import", data={"text": CV_TEXT}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/profiler/edit"
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


def test_interview_answers_are_merged(client):
    profile = _seed_profile(client)
    experience_id = profile.experiences[0].id

    response = client.get("/profiler/interview")
    assert response.status_code == 200
    assert "Acme" in response.text

    response = client.post(
        "/profiler/interview",
        data={"key": f"exp:{experience_id}:context", "answer": "Analytics platform"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _loaded().experiences[0].context == "Analytics platform"


def test_interview_skipping_moves_to_the_next_question(client):
    profile = _seed_profile(client)
    first_key = f"exp:{profile.experiences[0].id}:context"

    response = client.post(
        "/profiler/interview/skip", data={"key": first_key}, follow_redirects=False
    )
    assert response.status_code == 303
    assert first_key in store.skipped_keys()

    response = client.post("/profiler/interview/restart", follow_redirects=False)
    assert response.status_code == 303
    assert store.skipped_keys() == set()


def test_interview_needs_an_experience_first(client):
    client.post("/profiler/import", data={"text": CV_TEXT}, follow_redirects=False)
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


def test_interview_answer_is_structured_when_a_model_is_configured(client, monkeypatch):
    profile = _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    key = f"exp:{profile.experiences[0].id}:achievements"

    async def fake(settings, *, answer, experience):
        return [Achievement(text="Cut ingestion latency by 60%", metrics=["60%"], skills=["Rust"])]

    monkeypatch.setattr("app.profiler.structure.structure_achievements", fake)
    response = client.post(
        "/profiler/interview",
        data={"key": key, "answer": "Cut latency by 60% using Rust"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    achievements = _loaded().experiences[0].achievements
    assert [a.text for a in achievements] == ["Cut ingestion latency by 60%"]
    assert achievements[0].metrics == ["60%"]


def test_interview_answer_falls_back_and_warns_when_nothing_is_verifiable(client, monkeypatch):
    profile = _seed_profile(client)
    save_settings(Settings(model="openai/gpt-4o"))
    key = f"exp:{profile.experiences[0].id}:achievements"

    async def unverifiable(settings, *, answer, experience):
        return None

    monkeypatch.setattr("app.profiler.structure.structure_achievements", unverifiable)
    response = client.post(
        "/profiler/interview",
        data={"key": key, "answer": "First result\nSecond result"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    # The candidate's own lines are kept, and the fallback is explained.
    assert len(_loaded().experiences[0].achievements) == 2
    assert "could not be verified" in response.text
