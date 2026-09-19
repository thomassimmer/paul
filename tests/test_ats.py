"""Tests for the ATS checks and the keyword extraction step."""

from __future__ import annotations

import asyncio
import io

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from app import ats
from app.config import Settings
from app.models import Achievement, Experience, Keyword, Offer, Profile


def _docx_bytes(document: DocumentType) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- normalize -----------------------------------------------------------------


def test_normalize_ignores_case_accents_and_spacing():
    assert ats.normalize("  Éléphant   GRIS\t") == "elephant gris"
    assert ats.normalize("Kubernetes") == ats.normalize("KUBERNETES")
    assert ats.normalize("multi   colonne") == "multi colonne"


def test_normalize_handles_none_and_empty():
    assert ats.normalize("") == ""
    assert ats.normalize("   \n\t ") == ""


# --- keyword_present -----------------------------------------------------------


def test_keyword_present_matches_term_and_variants():
    keyword = ats.AtsKeyword(term="Kubernetes", variants=["K8s"])
    assert ats.keyword_present("Nous déployons sur Kubernetes en production.", keyword)
    assert ats.keyword_present("Notre cluster K8s tourne bien.", keyword)
    assert not ats.keyword_present("Nous utilisons Docker.", keyword)


def test_keyword_present_ignores_case_and_accents():
    keyword = ats.AtsKeyword(term="Gestion de projet")
    assert ats.keyword_present("GESTION DE PROJET pluridisciplinaire", keyword)
    assert ats.keyword_present("expérience en gestion de projets", keyword)


def test_keyword_present_ignores_empty_variants():
    keyword = ats.AtsKeyword(term="Rust", variants=["", "   "])
    assert not ats.keyword_present("Nous codons en Go.", keyword)
    assert ats.keyword_present("Nous codons en Rust.", keyword)


# --- coverage ------------------------------------------------------------------


def test_coverage_percentage_and_missing():
    keywords = [
        ats.AtsKeyword(term="Python"),
        ats.AtsKeyword(term="Kubernetes", variants=["K8s"]),
        ats.AtsKeyword(term="Terraform"),
    ]

    report = ats.coverage("Développeur Python, cluster K8s.", keywords)

    assert report.coverage_percent == 67
    assert [keyword.present for keyword in report.keywords] == [True, True, False]
    assert [keyword.term for keyword in report.missing] == ["Terraform"]


def test_coverage_without_keywords_is_zero_and_does_not_raise():
    report = ats.coverage("n'importe quel texte", [])

    assert report.coverage_percent == 0
    assert report.keywords == []
    assert report.missing == []


def test_coverage_marks_keywords_found_in_the_profile():
    profile = Profile(
        skills={"Backend": ["Terraform"]},
        experiences=[
            Experience(
                stack=["Kubernetes"],
                achievements=[Achievement(text="A migré la plateforme vers Rust.")],
            )
        ],
    )
    keywords = [
        ats.AtsKeyword(term="Terraform"),
        ats.AtsKeyword(term="Rust"),
        ats.AtsKeyword(term="COBOL"),
    ]

    report = ats.coverage("Développeur Python.", keywords, profile)
    by_term = {keyword.term: keyword for keyword in report.keywords}

    assert by_term["Terraform"].present is False
    assert by_term["Terraform"].in_profile is True
    assert "profile" in by_term["Terraform"].hint.lower()
    assert by_term["Rust"].in_profile is True
    assert by_term["COBOL"].in_profile is False
    assert by_term["COBOL"].hint != by_term["Terraform"].hint


def test_coverage_leaves_a_present_keyword_without_hint():
    report = ats.coverage("Nous faisons du Python.", [ats.AtsKeyword(term="Python")])

    assert report.keywords[0].present is True
    assert report.keywords[0].hint == ""


# --- format_issues -------------------------------------------------------------


def test_format_issues_reports_a_table():
    document = Document()
    document.add_paragraph("Camille Moreau")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Compétence"
    table.cell(0, 1).text = "Python"

    issues = ats.format_issues(_docx_bytes(document))

    assert any("table" in issue.lower() for issue in issues)


def test_format_issues_is_quiet_on_a_plain_document():
    document = Document()
    document.add_paragraph("Camille Moreau, ingénieure backend.")

    issues = ats.format_issues(_docx_bytes(document))

    assert not any("table" in issue.lower() for issue in issues)


def test_format_issues_reports_a_multi_column_section():
    document = Document()
    document.add_paragraph("Camille Moreau")
    sect_pr = document.sections[0]._sectPr
    cols = sect_pr.find(qn("w:cols"))
    if cols is None:
        cols = sect_pr.makeelement(qn("w:cols"), {})
        sect_pr.append(cols)
    cols.set(qn("w:num"), "2")

    issues = ats.format_issues(_docx_bytes(document))

    assert any("column" in issue.lower() for issue in issues)


def test_format_issues_reports_a_header():
    document = Document()
    document.add_paragraph("Camille Moreau")
    document.sections[0].header.paragraphs[0].text = "Camille Moreau — page 1"

    issues = ats.format_issues(_docx_bytes(document))

    assert any("header" in issue.lower() for issue in issues)


def test_format_issues_reports_text_boxes_and_drawings():
    document = Document()
    paragraph = document.add_paragraph()
    paragraph._p.append(parse_xml(f"<w:r {nsdecls('w')}><w:drawing/></w:r>"))
    paragraph._p.append(parse_xml(f"<w:r {nsdecls('w')}><w:txbxContent><w:p/></w:txbxContent></w:r>"))

    issues = ats.format_issues(_docx_bytes(document))

    assert any("text box" in issue.lower() for issue in issues)
    assert any("image" in issue.lower() for issue in issues)


# --- extract_keywords ----------------------------------------------------------


def test_extract_keywords_reuses_offer_keywords_without_calling_the_model(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the model must not be called when the offer has keywords")

    monkeypatch.setattr(ats, "complete_structured", boom)

    offer = Offer(
        keywords=[Keyword(term="Kubernetes", variants=["K8s"]), Keyword(term="Python")]
    )
    result = asyncio.run(ats.extract_keywords(Settings(), offer, "texte de l'offre"))

    assert [(keyword.term, keyword.variants) for keyword in result] == [
        ("Kubernetes", ["K8s"]),
        ("Python", []),
    ]
    assert all(not keyword.present and not keyword.in_profile for keyword in result)


def test_extract_keywords_asks_the_model_when_the_offer_has_none(monkeypatch):
    calls: dict = {}

    async def fake(settings, *, schema, content, system=None, **kwargs):
        calls["schema"] = schema
        calls["content"] = content
        calls["system"] = system
        return schema(keywords=[Keyword(term="Rust", variants=["rustlang", "Rust", ""])])

    monkeypatch.setattr(ats, "complete_structured", fake)

    result = asyncio.run(ats.extract_keywords(Settings(), Offer(), "Une offre Rust."))

    assert calls["content"] == "Une offre Rust."
    assert calls["system"]  # the prompt file was loaded
    assert [keyword.term for keyword in result] == ["Rust"]
    assert result[0].variants == ["rustlang"]  # blank and self-repeating dropped
