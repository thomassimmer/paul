"""Shared Pydantic domain models.

Every LLM call in the app returns one of these validated objects (README design
rule). ``Profile`` is owned by the profiler; the offer analyzer and the writer
will add ``Offer``, ``Score`` and ``Application`` here.
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
