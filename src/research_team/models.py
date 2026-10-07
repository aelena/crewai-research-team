"""Structured outputs for the tasks whose results other code reads (the plan and the claim ledgers)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchTopic(BaseModel):
    title: str
    why_it_matters: str
    key_questions: list[str] = Field(min_length=1)
    search_queries: list[str] = Field(default_factory=list, description="Concrete queries a researcher can run")


class ResearchPlan(BaseModel):
    thesis_hypothesis: str = Field(description="The working thesis the piece will test, stated so it can be wrong")
    counter_thesis: str = Field(description="The strongest position against the working thesis")
    main_topics: list[ResearchTopic] = Field(min_length=2)
    secondary_topics: list[ResearchTopic] = Field(min_length=1)
    counter_evidence_targets: list[str] = Field(
        min_length=2, description="Failure cases, critiques, regulatory or economic risks to look for"
    )
    success_criteria: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


ClaimStatus = Literal["verified", "contested", "unverified", "refuted"]


class Claim(BaseModel):
    statement: str
    status: ClaimStatus
    confidence: Literal["high", "medium", "low"]
    sources: list[str] = Field(default_factory=list, description="URLs that support (or refute) the claim")
    source_quality: Literal["primary", "reputable-secondary", "vendor-or-advocacy", "opinion", "none"] = "none"
    as_of: str | None = Field(None, description="Date or year the figure refers to, if any")
    note: str = ""


class DataSeries(BaseModel):
    """Numbers fit to chart, each traceable to a verified claim."""

    title: str
    unit: str = ""
    labels: list[str]
    values: list[float]
    source: str


class ClaimLedger(BaseModel):
    track: Literal["main", "secondary"]
    claims: list[Claim] = Field(min_length=1)
    chartable: list[DataSeries] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="Questions the evidence could not answer")
    needs_human_review: list[str] = Field(default_factory=list)
