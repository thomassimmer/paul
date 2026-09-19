"""Shared Pydantic domain models.

Every LLM call in the app returns one of these validated objects (README design
rule). ``Profile`` is owned by the profiler, ``Offer`` by the offer analyzer;
``Score`` and ``Application`` arrive with the ranker and the writer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Identity(BaseModel):
    name: str = ""
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
    notice_period: str = ""
    salary_expectation: str = ""
    relocation: str = ""
    languages: list[str] = Field(default_factory=list)


class Achievement(BaseModel):
    """One result. The writer cites ``id``, which is what makes the grounding
    check possible, and ``metrics`` is what makes it concrete."""

    id: str = ""
    text: str = ""
    metrics: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    id: str = ""
    company: str = ""
    title: str = ""
    period: str = ""
    # The interview targets exactly what a CV usually omits:
    context: str = ""
    team_size: str = ""
    stack: list[str] = Field(default_factory=list)
    difficulties: str = ""
    achievements: list[Achievement] = Field(default_factory=list)


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
    skills: dict[str, list[str]] = Field(default_factory=dict)
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
    skills: dict[str, list[str]] = Field(default_factory=dict)

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


class CompanyInfo(BaseModel):
    """Only what the pasted content states. Never guessed."""

    size: str = ""
    funding: str = ""
    domain: str = ""
    mission: str = ""


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
    updated_at: str = ""
