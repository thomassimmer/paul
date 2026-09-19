from __future__ import annotations

from app.config import SETTINGS_PATH, load_settings

FORM = {
    "model": "ollama/llama3",
    "api_key": "",
    "api_base": "http://localhost:11434",
    "output_language": "fr",
    "followup_days": "10",
    "target_pages_cv": "2",
    "target_pages_letter": "1",
    "filter_rules": "Eliminate clearance requirements.",
    "wishes": "startup: 3",
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Paul" in response.text


def test_the_dashboard_lists_the_writer_step(client):
    assert "Write the tailored documents" in client.get("/").text


def test_settings_page_renders(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert 'name="model"' in response.text
    assert 'name="filter_rules"' in response.text
    assert 'name="wishes"' in response.text


def test_save_settings_redirects_and_persists(client):
    response = client.post("/settings", data=FORM, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"

    settings = load_settings()
    assert settings.model == "ollama/llama3"
    assert settings.output_language == "fr"
    assert settings.followup_days == 10
    assert [w.label for w in settings.wishes] == ["startup"]


def test_saved_banner_shown(client):
    response = client.get("/settings?saved=1")
    assert response.status_code == 200
    assert "Settings saved." in response.text


def test_invalid_values_are_reported_and_kept(client):
    response = client.post(
        "/settings",
        data={**FORM, "followup_days": "oops", "output_language": "zz"},
    )
    assert response.status_code == 200
    assert "Follow-up delay must be a number of days" in response.text
    assert "Unknown output language" in response.text
    # Nothing was written.
    assert not SETTINGS_PATH.exists()


def test_api_key_is_kept_when_field_left_blank(client):
    client.post("/settings", data={**FORM, "api_key": "sk-secret"})
    assert load_settings().api_key == "sk-secret"

    client.post("/settings", data={**FORM, "api_key": ""})
    assert load_settings().api_key == "sk-secret"


def test_api_key_can_be_removed(client):
    client.post("/settings", data={**FORM, "api_key": "sk-secret"})
    client.post("/settings", data={**FORM, "api_key": "", "clear_api_key": "1"})
    assert load_settings().api_key == ""


def test_connection_without_model(client):
    response = client.post("/settings/test", data={**FORM, "model": ""})
    assert response.status_code == 200
    assert "No model configured" in response.text


def test_connection_htmx_returns_fragment(client):
    response = client.post(
        "/settings/test",
        data=FORM,
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # A fragment, not a full page.
    assert "<html" not in response.text.lower()
    assert "banner" in response.text
