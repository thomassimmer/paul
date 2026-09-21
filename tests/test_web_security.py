"""What the origin guard refuses, and what it lets through.

The decision is a pure function of the method and the headers, so most of it is
tested without HTTP; the last two tests check the guard is actually wired into the
app, since a correct function nobody calls protects nothing.
"""

from __future__ import annotations

from app.config import SETTINGS_PATH, load_settings
from app.web import security

FORM = {
    "model": "ollama/llama3",
    "api_key": "",
    "api_base": "",
    "output_language": "fr",
    "followup_days": "10",
    "target_pages_cv": "2",
    "target_pages_letter": "1",
    "filter_rules": "",
    "wishes": "",
}


# --- The host header ----------------------------------------------------------


def test_a_loopback_host_is_answered():
    for host in ("127.0.0.1", "127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
        assert security.refusal("GET", {"host": host}) is None


def test_another_name_is_refused():
    # The DNS-rebinding case: the browser thinks it is same-origin, and the only
    # thing that says otherwise is the name it asked for.
    refused = security.refusal("GET", {"host": "evil.example"})
    assert refused is not None
    status, message = refused
    assert status == 400
    assert "PAUL_ALLOWED_HOSTS" in message


def test_a_missing_or_broken_host_is_refused():
    assert security.refusal("GET", {}) is not None
    assert security.refusal("GET", {"host": ""}) is not None
    assert security.refusal("GET", {"host": "[::1"}) is not None


def test_the_allow_list_can_be_extended(monkeypatch):
    monkeypatch.setattr(security, "ALLOWED_HOSTS", frozenset({"paul.local"}))
    assert security.refusal("GET", {"host": "paul.local"}) is None
    assert security.refusal("GET", {"host": "127.0.0.1"}) is not None


# --- The origin header --------------------------------------------------------


def test_reading_methods_are_not_checked():
    # A cross-site GET changes nothing, and the fragment the page shows is not a
    # secret the browser withholds.
    assert security.refusal("GET", {"host": "127.0.0.1", "origin": "http://evil.example"}) is None


def test_a_cross_origin_post_is_refused():
    refused = security.refusal(
        "POST", {"host": "127.0.0.1:8000", "origin": "http://evil.example"}
    )
    assert refused is not None
    status, message = refused
    assert status == 403
    assert "Cross-origin" in message


def test_a_post_from_the_app_is_answered():
    for origin in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
        headers = {"host": "127.0.0.1:8000", "origin": origin}
        assert security.refusal("POST", headers) is None, origin


def test_a_missing_origin_is_taken_as_a_tool():
    # curl, the Docker healthcheck and the test suite send no Origin at all.
    assert security.refusal("POST", {"host": "127.0.0.1:8000"}) is None


def test_the_referer_stands_in_for_a_missing_origin():
    headers = {"host": "127.0.0.1:8000", "referer": "http://evil.example/form"}
    assert security.refusal("POST", headers) == (403, security.BAD_ORIGIN)
    assert security.refusal("POST", {**headers, "referer": "http://127.0.0.1:8000/settings"}) is None


# --- The guard is wired in ----------------------------------------------------


def test_a_cross_origin_post_changes_nothing(client):
    response = client.post("/settings", data=FORM, headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
    assert not SETTINGS_PATH.exists()


def test_the_same_post_from_the_app_is_saved(client):
    response = client.post(
        "/settings",
        data=FORM,
        headers={"Origin": "http://127.0.0.1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert load_settings().model == "ollama/llama3"


def test_a_request_under_another_name_is_refused(client):
    response = client.get("/health", headers={"Host": "evil.example"})

    assert response.status_code == 400
