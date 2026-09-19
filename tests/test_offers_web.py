from __future__ import annotations

from app.config import Settings, save_settings
from app.models import OfferDraft, Requirements
from app.offers import store

FRAGMENT = """
<div>
  <h1>Senior Backend Engineer</h1>
  <p>Acme, Lyon. We are looking for a backend engineer to own the ingestion path.</p>
  <form>
    <label for="why">Why do you want to join us?</label>
    <textarea id="why" name="why" maxlength="500" required></textarea>
  </form>
</div>
"""

DRAFT = OfferDraft(
    title="Senior Backend Engineer",
    company="Acme",
    location="Lyon",
    contract_type="Permanent",
    seniority="senior",
    language="en",
    responsibilities=["Own the ingestion path"],
    requirements=Requirements(must_have=["Rust"], nice_to_have=["Kafka"]),
)


def _patch_extract(monkeypatch, draft: OfferDraft | None = None):
    async def fake(settings, cleaned_text):
        return draft if draft is not None else DRAFT

    monkeypatch.setattr("app.offers.extract.extract_offer", fake)


def _analyze(client) -> int:
    """Analyze FRAGMENT and return the new offer id."""
    response = client.post(
        "/offers/new",
        data={"fragment_count": "1", "fragment.0": FRAGMENT},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _stored(offer_id: int):
    record = store.load_offer(offer_id)
    assert record is not None
    return record.offer


def test_offers_list_starts_empty(client):
    response = client.get("/offers")
    assert response.status_code == 200
    assert "No offer analyzed yet" in response.text


def test_new_offer_page_explains_how_to_copy_a_fragment(client):
    response = client.get("/offers/new")
    assert response.status_code == 200
    assert "Copy outerHTML" in response.text


def test_analyze_redirects_to_the_offer(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    _patch_extract(monkeypatch)

    offer_id = _analyze(client)

    response = client.get(f"/offers/{offer_id}")
    assert response.status_code == 200
    assert "Senior Backend Engineer" in response.text
    assert "Rust" in response.text
    assert "Why do you want to join us?" in response.text
    assert "name=why" in response.text


def test_analyze_needs_a_model_and_keeps_the_pasted_fragment(client):
    # No model is configured, so the extraction never runs and the error is
    # reported on the page rather than after a redirect.
    response = client.post(
        "/offers/new",
        data={"fragment_count": "1", "fragment.0": FRAGMENT},
    )

    assert response.status_code == 400
    assert "No model configured" in response.text
    # The pasted fragment is handed back rather than lost.
    assert "Senior Backend Engineer" in response.text
    assert store.count_offers() == 0


def test_analyze_with_empty_input_explains_what_to_paste(client):
    save_settings(Settings(model="openai/gpt-4o"))
    response = client.post("/offers/new", data={"fragment_count": "1", "fragment.0": "  "})
    assert response.status_code == 400
    assert "Paste the offer" in response.text


def test_offer_can_be_completed_by_hand(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    _patch_extract(monkeypatch, OfferDraft(title="Senior Backend Engineer"))
    offer_id = _analyze(client)

    response = client.get(f"/offers/{offer_id}")
    assert "did not state the company" in response.text

    edit_page = client.get(f"/offers/{offer_id}/edit")
    assert edit_page.status_code == 200
    assert 'name="company"' in edit_page.text

    response = client.post(
        f"/offers/{offer_id}/edit",
        data={
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "location": "Lyon",
            "remote_policy": "hybrid",
            "contract_type": "Permanent",
            "seniority": "senior",
            "salary": "70-80k",
            "language": "en",
            "responsibilities": "Own the ingestion path",
            "must_have": "Rust",
            "nice_to_have": "",
            "keywords": "Kubernetes: K8s",
            "constraints.work_authorization": "EU only",
            "company.size": "120",
            "form_count": "2",
            "form.0.label": "Why us?",
            "form.0.name": "why",
            "form.0.type": "textarea",
            "form.0.max_length": "500",
            "form.0.required": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    offer = _stored(offer_id)
    assert offer.company == "Acme"
    assert offer.salary == "70-80k"
    assert offer.keywords[0].variants == ["K8s"]
    assert offer.constraints.work_authorization == "EU only"
    assert offer.company_info.size == "120"
    assert len(offer.form) == 1 and offer.form[0].required is True


def test_offer_can_be_analyzed_again(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    _patch_extract(monkeypatch)
    offer_id = _analyze(client)

    _patch_extract(monkeypatch, OfferDraft(title="Backend Engineer", company="Acme"))
    response = client.post(f"/offers/{offer_id}/reanalyze", follow_redirects=False)

    assert response.status_code == 303
    assert _stored(offer_id).title == "Backend Engineer"
    # The form was parsed from the markup, so it is still there.
    assert [q.name for q in _stored(offer_id).form] == ["why"]


def test_offer_source_shows_the_raw_fragment(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    _patch_extract(monkeypatch)
    offer_id = _analyze(client)

    response = client.get(f"/offers/{offer_id}/source")
    assert response.status_code == 200
    assert "Senior Backend Engineer" in response.text
    assert "As cleaned" in response.text


def test_offer_can_be_deleted(client, monkeypatch):
    save_settings(Settings(model="openai/gpt-4o"))
    _patch_extract(monkeypatch)
    offer_id = _analyze(client)

    response = client.post(f"/offers/{offer_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert store.load_offer(offer_id) is None
    assert "No offer analyzed yet" in client.get("/offers").text


def test_missing_offer_redirects_to_the_list(client):
    response = client.get("/offers/9999", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/offers"


def test_dashboard_links_to_the_offers(client):
    assert "/offers/new" in client.get("/").text
