"""Shared Pydantic domain models.

Every LLM call in the app returns one of these validated objects (README design
rule). ``Profile`` is owned by the profiler, ``Offer`` by the offer analyzer;
``Score`` and ``Application`` arrive with the ranker and the writer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Identity(BaseModel):
    """Who you are. A fact like any other: never drafted for an application form.

    ``first_name`` / ``last_name`` exist because forms ask for them separately;
    when they are empty the writer falls back to splitting ``name``.
    """

    name: str = ""
    first_name: str = ""
    last_name: str = ""
    headline: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    links: list[str] = Field(default_factory=list)


class Facts(BaseModel):
    """Answers only you can give. Never filled by the LLM.

    Hand-added keys are preserved (``extra="allow"``), because the profile is a
    plain file you are invited to edit.
    """

    model_config = ConfigDict(extra="allow")

    work_authorization: str = ""
    # Only meaningful when you hold a permit or a visa; left empty otherwise.
    work_permit_expiry: str = ""
    notice_period: str = ""
    salary_expectation: str = ""
    relocation: str = ""
    languages: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    """One job or placement.

    ``context`` is the setting: the company, the product, the scale, your remit.
    ``highlights`` is what you did and got out of it, one line per thing — the
    shape a CV bullet wants, and the material the ranker and the writer work from.

    This replaced a structured ``achievements`` list (ids, metrics, skills) and a
    separate ``difficulties`` field. The ceremony cost more than it told the
    writer, and it was the app's taxonomy rather than the candidate's; a few plain
    lines say more, and the interview fills them without a second thought.
    """

    id: str = ""
    company: str = ""
    title: str = ""
    period: str = ""
    context: str = ""
    team_size: str = ""
    stack: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fold_legacy(cls, data: object) -> object:
        """Read a file written before ``highlights`` without losing anything.

        Older profiles stored a structured ``achievements`` list and a
        ``difficulties`` field. Everything they held is kept: a result becomes a
        highlight, with the figures it measured in parentheses after it; the
        technologies it named join the stack of the job; and what was genuinely hard
        becomes a line of the context, where it belongs — a constraint is not a
        bullet a CV should carry. Those extra fields carried information the sentence
        alone does not: "Led the migration" with a ``metrics`` of "13M invoices" says
        something the text never repeats.
        """
        if not isinstance(data, dict):
            return data
        achievements = data.get("achievements") or []
        difficulties = data.get("difficulties")
        if not achievements and not difficulties:
            return data

        folded = [
            str(item).strip() for item in (data.get("highlights") or []) if str(item).strip()
        ]
        stack = [str(item).strip() for item in (data.get("stack") or []) if str(item).strip()]
        known = {skill.casefold() for skill in stack}

        def add_highlight(line: str) -> None:
            if line and line not in folded:
                folded.append(line)

        for item in achievements:
            if not isinstance(item, dict):
                add_highlight(str(item).strip())
                continue
            text = str(item.get("text", "")).strip()
            measures = [
                str(measure).strip()
                for measure in (item.get("metrics") or [])
                if str(measure).strip()
            ]
            add_highlight(f"{text} ({', '.join(measures)})" if text and measures else text)
            for skill in item.get("skills") or []:
                name = str(skill).strip()
                if name and name.casefold() not in known:
                    known.add(name.casefold())
                    stack.append(name)

        if isinstance(difficulties, str) and difficulties.strip():
            context = str(data.get("context", "")).strip()
            legacy_context = f"{context}\n{difficulties.strip()}" if context else difficulties.strip()
        else:
            legacy_context = data.get("context", "")

        legacy = {
            key: value for key, value in data.items() if key not in ("achievements", "difficulties")
        }
        legacy["highlights"] = folded
        legacy["stack"] = stack
        legacy["context"] = legacy_context
        return legacy


class Education(BaseModel):
    id: str = ""
    school: str = ""
    degree: str = ""
    period: str = ""
    details: str = ""


class Project(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    links: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Preferences(BaseModel):
    """What you want next. ``more_of`` / ``less_of`` are asked by the interview."""

    model_config = ConfigDict(extra="allow")

    target_roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote: str = ""
    contract_types: list[str] = Field(default_factory=list)
    more_of: list[str] = Field(default_factory=list)
    less_of: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    identity: Identity = Field(default_factory=Identity)
    facts: Facts = Field(default_factory=Facts)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)


class ProfileDraft(BaseModel):
    """What the LLM may produce from a CV.

    Deliberately has no ``facts`` and no ``preferences``: those are answered by
    you, never generated, and the type makes that impossible to get wrong.
    """

    identity: Identity = Field(default_factory=Identity)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)

    def to_profile(self) -> Profile:
        return Profile(**self.model_dump())


# --- Offers -------------------------------------------------------------------


class Keyword(BaseModel):
    """A term the offer insists on, with accepted variants (K8s / Kubernetes)."""

    term: str = ""
    variants: list[str] = Field(default_factory=list)


class Requirements(BaseModel):
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)


class Constraints(BaseModel):
    """Conditions that can eliminate an offer outright."""

    work_authorization: str = ""
    citizenship: str = ""
    on_site: str = ""
    language_level: str = ""
    clearance: str = ""

    @property
    def stated(self) -> bool:
        """True when the offer states at least one constraint.

        One property, so the card and the quick navigation agree on whether the
        section exists instead of each repeating the same list of fields.
        """
        return any(
            (
                self.work_authorization,
                self.citizenship,
                self.on_site,
                self.language_level,
                self.clearance,
            )
        )


class CompanyInfo(BaseModel):
    """Only what the pasted content states. Never guessed."""

    size: str = ""
    funding: str = ""
    domain: str = ""
    mission: str = ""

    @property
    def stated(self) -> bool:
        """True when the offer states at least one company fact."""
        return any((self.size, self.funding, self.domain, self.mission))


class FormQuestion(BaseModel):
    """One application-form field, parsed from the HTML by code, not by the LLM."""

    label: str = ""
    name: str = ""
    type: str = ""
    options: list[str] = Field(default_factory=list)
    required: bool = False
    max_length: int | None = None
    placeholder: str = ""


class OfferDraft(BaseModel):
    """What the LLM extracts from the cleaned offer text.

    Deliberately has no ``form`` field: the application form is parsed from the
    HTML by code, which is both more faithful (labels, ``required``,
    ``maxlength``, options) and cheaper.
    """

    title: str = ""
    company: str = ""
    location: str = ""
    remote_policy: str = ""
    contract_type: str = ""
    seniority: str = ""
    salary: str = ""
    language: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    requirements: Requirements = Field(default_factory=Requirements)
    keywords: list[Keyword] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    company_info: CompanyInfo = Field(default_factory=CompanyInfo)

    def to_offer(self, form: list[FormQuestion]) -> Offer:
        return Offer(**self.model_dump(), form=form)


class Offer(OfferDraft):
    """A draft plus the application form we parsed ourselves."""

    form: list[FormQuestion] = Field(default_factory=list)


class OfferRecord(BaseModel):
    """An offer as stored: the row's metadata plus the extracted offer."""

    id: int
    analyzed_at: str = ""
    source: str = ""
    # Where the posting lives, so it can be found again later. Given at import
    # (the analyzer never sees the address bar) and editable by hand afterwards.
    url: str = ""
    offer: Offer


# --- Ranking ------------------------------------------------------------------


class Elimination(BaseModel):
    """The verdict of stage 1, with the evidence that justifies it.

    ``rule`` is the candidate's rule that applied and ``excerpt`` the offer text
    that triggered it. An elimination without both is not an elimination: the
    caller downgrades it (nothing disappears silently).
    """

    eliminated: bool = False
    rule: str = ""
    excerpt: str = ""


def effective_eliminated(override: str, elimination: Elimination) -> bool:
    """A manual override wins over the rules; otherwise the rules decide."""
    if override == "kept":
        return False
    if override == "eliminated":
        return True
    return elimination.eliminated


class AxisVerdict(BaseModel):
    """What the model returns for one axis of the grid."""

    score: int = Field(default=0, ge=0, le=5)
    justification: str = ""


class ScoringGrid(BaseModel):
    """The fixed grid. Deliberately has no ``total``: it is computed by code."""

    technical_match: AxisVerdict = Field(default_factory=AxisVerdict)
    seniority_scope: AxisVerdict = Field(default_factory=AxisVerdict)
    wishes: AxisVerdict = Field(default_factory=AxisVerdict)
    red_flags: AxisVerdict = Field(default_factory=AxisVerdict)


class AxisScore(AxisVerdict):
    """A verdict plus the weight the application gives that axis."""

    axis: str = ""
    label: str = ""
    weight: float = 0.0


class Score(BaseModel):
    axes: list[AxisScore] = Field(default_factory=list)
    total: int = 0  # 0-100, computed by code from the axes and their weights


class RankingRecord(BaseModel):
    """What we decided about an offer, as stored."""

    offer_id: int
    elimination: Elimination = Field(default_factory=Elimination)
    # Manual override: "kept", "eliminated", or "" to trust the rules again.
    override: str = ""
    score: Score | None = None
    # Snapshot of what produced this ranking (criteria, profile, offer, model),
    # so an unchanged offer is never scored twice and an outdated one is visible.
    fingerprint: str = ""
    scored_at: str = ""

    @property
    def eliminated(self) -> bool:
        return effective_eliminated(self.override, self.elimination)


# --- Tracker ------------------------------------------------------------------


class Application(BaseModel):
    """Where an offer stands in your process, as stored.

    Kept apart from the offer and the ranking: this is what *you* did about it,
    not what the offer says nor what we scored it. A missing row means the
    default status, ``analyzed``.
    """

    offer_id: int
    status: str = "analyzed"
    applied_on: str = ""  # YYYY-MM-DD
    last_contact: str = ""  # YYYY-MM-DD, the date follow-ups count from
    notes: str = ""
    # Where the writer put the generated documents, once it has run.
    folder: str = ""
    updated_at: str = ""


# --- Templates ----------------------------------------------------------------

# The roles a template block can play. The two tuples are the contract between
# the blueprint, the prompts and the renderer. A role is checked when a model
# answer is read (``writer/draft.py``) and again when the renderer looks for its
# prototype, so an unknown role can never reach a document.
CV_ROLES = (
    "name",
    "headline",
    "contact",
    "section_title",
    "entry_title",
    "entry_subtitle",
    "entry_dates",
    "bullet",
    "body_text",
    "skill_line",
    "fixed",
)
LETTER_ROLES = (
    "name",
    "contact",
    "date",
    "recipient",
    "salutation",
    "body_text",
    "closing",
    "signature",
    "fixed",
)
# Roles that state a fact about the candidate, and therefore need a citation.
FACTUAL_ROLES = ("bullet", "body_text", "entry_subtitle")
# Roles whose numbers must already exist in the profile. Wider than FACTUAL_ROLES: a
# headline cites nothing — it is not a result — but it is the line a recruiter reads
# first, and "10 years of Rust" is exactly the claim this application refuses.
NUMBER_CHECKED_ROLES = (*FACTUAL_ROLES, "headline")


class TemplateBlock(BaseModel):
    """One paragraph of a template, with its role and the XML behind it."""

    role: str = ""
    text: str = ""  # the sample text, shown in the preview
    xml: str = ""  # the serialized paragraph: the renderer's prototype


class TemplateBlueprint(BaseModel):
    """A template, read once and reused for every rendering."""

    kind: str = "cv"  # "cv" | "letter"
    blocks: list[TemplateBlock] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def prototype(self, role: str) -> str | None:
        for block in self.blocks:
            if block.role == role and block.xml:
                return block.xml
        return None

    def roles(self) -> list[str]:
        return [block.role for block in self.blocks]


# --- Writer -------------------------------------------------------------------


class DraftLine(BaseModel):
    """One line of a generated document, and where it comes from.

    ``role`` stays a plain string here: this is what the model answers, and a
    misspelled role must not cost a whole retry. ``writer/draft.py`` maps it back
    to the roles the renderer knows before anything is stored.

    ``source_ids`` names the profile entries the line is based on — an
    experience id like ``exp-acme-2022``. The writer cites an experience rather
    than an individual result now that a result is just a line of its
    ``highlights``: the citation says which job the claim comes from, and the
    grounding check still refuses any number the profile does not contain.
    """

    role: str = "body_text"
    text: str = ""
    source_ids: list[str] = Field(default_factory=list)


class CvDraft(BaseModel):
    """What the model returns for a CV, roles unchecked."""

    lines: list[DraftLine] = Field(default_factory=list)


class LetterDraft(BaseModel):
    """What the model returns for a cover letter, roles unchecked."""

    lines: list[DraftLine] = Field(default_factory=list)


class GroundingIssue(BaseModel):
    index: int = 0
    role: str = ""
    text: str = ""
    reason: str = ""


class GroundingReport(BaseModel):
    checked: int = 0
    issues: list[GroundingIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


class DraftAnswer(BaseModel):
    """One drafted answer to an open form question, before the facts are merged in."""

    question: str = ""
    answer: str = ""


class AnswersDraft(BaseModel):
    """What the model returns for the application form's open questions."""

    answers: list[DraftAnswer] = Field(default_factory=list)


class FormAnswer(BaseModel):
    question: str = ""
    answer: str = ""
    # "fact": taken from the facts section; "generated": drafted by the model;
    # "missing": a fact question whose fact is not filled in yet.
    source: str = "generated"
    max_length: int | None = None
    note: str = ""
