from __future__ import annotations

from app.models import FormQuestion, Keyword, Offer, OfferDraft, Requirements
from app.offers import editor


def _offer() -> Offer:
    return OfferDraft(
        title="Senior Backend Engineer",
        company="Acme",
        responsibilities=["Own the ingestion path", "Mentor the team"],
        requirements=Requirements(must_have=["Rust"], nice_to_have=["Kafka"]),
        keywords=[Keyword(term="Kubernetes", variants=["K8s"])],
    ).to_offer([FormQuestion(label="Why us?", name="why", type="textarea", required=True)])


def test_parse_and_format_keywords_round_trip():
    text = "Kubernetes: K8s\nRust"
    assert editor.parse_keywords(text) == [
        Keyword(term="Kubernetes", variants=["K8s"]),
        Keyword(term="Rust"),
    ]
    assert editor.format_keywords(editor.parse_keywords(text)) == text


def test_editor_view_adds_one_blank_form_row():
    view = editor.editor_view(_offer())
    assert len(view["form_rows"]) == 2
    assert view["form_count"] == 2
    assert view["form_rows"][1]["question"].label == ""
    assert view["keywords_text"] == "Kubernetes: K8s"


def _form(**overrides: str) -> dict[str, str]:
    form = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Lyon",
        "remote_policy": "hybrid",
        "contract_type": "Permanent",
        "seniority": "senior",
        "salary": "",
        "language": "en",
        "responsibilities": "Own the ingestion path\nMentor the team",
        "must_have": "Rust\nKafka",
        "nice_to_have": "Kubernetes",
        "keywords": "Kubernetes: K8s\nRust",
        "constraints.work_authorization": "EU only",
        "constraints.citizenship": "",
        "constraints.on_site": "3 days a week",
        "constraints.language_level": "English B2",
        "constraints.clearance": "",
        "company.size": "120",
        "company.funding": "Series B",
        "company.domain": "Analytics",
        "company.mission": "",
        "form_count": "2",
        "form.0.label": "Why us?",
        "form.0.name": "why",
        "form.0.type": "textarea",
        "form.0.options": "",
        "form.0.max_length": "500",
        "form.0.placeholder": "",
        "form.0.required": "1",
    }
    form.update(overrides)
    return form


def test_offer_from_form_reads_every_section():
    offer = editor.offer_from_form(_form(), _offer())

    assert offer.title == "Senior Backend Engineer"
    assert offer.location == "Lyon"
    assert offer.responsibilities == ["Own the ingestion path", "Mentor the team"]
    assert offer.requirements.must_have == ["Rust", "Kafka"]
    assert offer.requirements.nice_to_have == ["Kubernetes"]
    assert offer.keywords == [Keyword(term="Kubernetes", variants=["K8s"]), Keyword(term="Rust")]
    assert offer.constraints.work_authorization == "EU only"
    assert offer.constraints.on_site == "3 days a week"
    assert offer.company_info.size == "120"
    assert offer.company_info.mission == ""


def test_offer_from_form_drops_the_blank_trailing_row():
    offer = editor.offer_from_form(_form(), _offer())
    assert len(offer.form) == 1
    assert offer.form[0].label == "Why us?"
    assert offer.form[0].required is True
    assert offer.form[0].max_length == 500


def test_offer_from_form_clears_removed_values():
    form = _form(must_have="", keywords="", **{"form.0.label": "", "form.0.name": "", "form.0.type": ""})
    offer = editor.offer_from_form(form, _offer())
    assert offer.requirements.must_have == []
    assert offer.keywords == []
    assert offer.form == []
