"""The structured offer editor.

Used to complete by hand what the model could not read from the offer. Same
conventions as the profile editor: one line per list item, and the form's
questions rendered with a trailing blank row so rows can be added and removed
without JavaScript.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models import CompanyInfo, Constraints, FormQuestion, Keyword, Offer, Requirements
from app.profiler.text import split_list

_SCALARS = (
    "title",
    "company",
    "location",
    "remote_policy",
    "contract_type",
    "seniority",
    "salary",
    "language",
)


def _text(form: Mapping[str, object], key: str) -> str:
    value = form.get(key)
    return "" if value is None else str(value).strip()


def _number(form: Mapping[str, object], key: str, default: int = 0) -> int:
    try:
        return int(str(form.get(key, "")).strip())
    except (TypeError, ValueError):
        return default


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_keywords(text: str) -> list[Keyword]:
    """Parse ``Term: variant, variant`` lines, one term per line."""
    keywords: list[Keyword] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        term, separator, rest = line.partition(":")
        if separator and rest.strip():
            keywords.append(Keyword(term=term.strip(), variants=split_list(rest)))
        else:
            keywords.append(Keyword(term=line))
    return [keyword for keyword in keywords if keyword.term]


def format_keywords(keywords: list[Keyword]) -> str:
    lines = []
    for keyword in keywords:
        if not keyword.term:
            continue
        lines.append(
            f"{keyword.term}: {', '.join(keyword.variants)}" if keyword.variants else keyword.term
        )
    return "\n".join(lines)


def editor_view(offer: Offer) -> dict:
    """Rows for the template, with one blank form question at the end."""
    rows = [{"index": i, "question": question} for i, question in enumerate(offer.form)]
    rows.append({"index": len(offer.form), "question": FormQuestion()})
    return {
        "form_rows": rows,
        "form_count": len(rows),
        "keywords_text": format_keywords(offer.keywords),
    }


def offer_from_form(form: Mapping[str, object], current: Offer) -> Offer:
    """Build the offer from the editor, keeping what the form does not show."""
    offer = current.model_copy(deep=True)

    for field in _SCALARS:
        setattr(offer, field, _text(form, field))

    offer.responsibilities = split_list(_text(form, "responsibilities"))
    offer.requirements = Requirements(
        must_have=split_list(_text(form, "must_have")),
        nice_to_have=split_list(_text(form, "nice_to_have")),
    )
    offer.keywords = parse_keywords(_text(form, "keywords"))
    offer.constraints = Constraints(
        work_authorization=_text(form, "constraints.work_authorization"),
        citizenship=_text(form, "constraints.citizenship"),
        on_site=_text(form, "constraints.on_site"),
        language_level=_text(form, "constraints.language_level"),
        clearance=_text(form, "constraints.clearance"),
    )
    offer.company_info = CompanyInfo(
        size=_text(form, "company.size"),
        funding=_text(form, "company.funding"),
        domain=_text(form, "company.domain"),
        mission=_text(form, "company.mission"),
    )
    offer.form = _form_questions(form)
    return offer


def _form_questions(form: Mapping[str, object]) -> list[FormQuestion]:
    questions: list[FormQuestion] = []
    for index in range(_number(form, "form_count")):
        prefix = f"form.{index}."
        label = _text(form, prefix + "label")
        name = _text(form, prefix + "name")
        type_ = _text(form, prefix + "type")
        options = split_list(_text(form, prefix + "options"))
        placeholder = _text(form, prefix + "placeholder")
        if not any((label, name, type_, options, placeholder)):
            continue  # blank trailing row
        questions.append(
            FormQuestion(
                label=label,
                name=name,
                type=type_,
                options=options,
                required=_text(form, prefix + "required") == "1",
                max_length=_int_or_none(_text(form, prefix + "max_length")),
                placeholder=placeholder,
            )
        )
    return questions
