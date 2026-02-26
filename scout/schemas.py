"""Pydantic request/response schemas for the Scout API."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, field_validator


class _GradesMixin(BaseModel):
    grade_team: str | None = None
    grade_team_num: float | None = None
    grade_tech: str | None = None
    grade_tech_num: float | None = None
    grade_opportunity: str | None = None
    grade_opportunity_num: float | None = None


class InitiativeOut(_GradesMixin):
    id: int
    name: str
    uni: str
    sector: str
    mode: str
    description: str
    website: str
    email: str
    relevance: str
    sheet_source: str
    enriched: bool
    enriched_at: str | None = None
    verdict: str | None = None
    score: float | None = None
    classification: str | None = None
    reasoning: str | None = None
    contact_who: str | None = None
    contact_channel: str | None = None
    engagement_hook: str | None = None
    key_evidence: list[str] = []
    data_gaps: list[str] = []
    # Lightweight overview fields for list view
    technology_domains: str = ""
    categories: str = ""
    member_count: int = 0
    outreach_now_score: float | None = None
    venture_upside_score: float | None = None
    custom_fields: dict[str, Any] = {}


class EnrichmentOut(BaseModel):
    id: int
    source_type: str
    summary: str
    fetched_at: str


class ProjectOut(_GradesMixin):
    id: int
    initiative_id: int
    name: str
    description: str
    website: str
    github_url: str
    team: str
    extra_links: dict[str, str] = {}
    verdict: str | None = None
    score: float | None = None
    classification: str | None = None


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    website: str = ""
    github_url: str = ""
    team: str = ""
    extra_links: dict[str, str] = {}


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    website: str | None = None
    github_url: str | None = None
    team: str | None = None
    extra_links: dict[str, str] | None = None


class InitiativeUpdate(BaseModel):
    name: str | None = None
    uni: str | None = None
    sector: str | None = None
    mode: str | None = None
    description: str | None = None
    website: str | None = None
    email: str | None = None
    relevance: str | None = None
    team_page: str | None = None
    team_size: str | None = None
    linkedin: str | None = None
    github_org: str | None = None
    key_repos: str | None = None
    sponsors: str | None = None
    competitions: str | None = None
    custom_fields: dict[str, Any] | None = None


class InitiativeDetail(InitiativeOut):
    team_page: str = ""
    team_size: str = ""
    linkedin: str = ""
    github_org: str = ""
    key_repos: str = ""
    sponsors: str = ""
    competitions: str = ""
    extra_links: dict[str, str] = {}
    enrichments: list[EnrichmentOut] = []
    projects: list[ProjectOut] = []
    # Full overview fields
    market_domains: str = ""
    member_examples: str = ""
    member_roles: str = ""
    github_repo_count: int = 0
    github_contributors: int = 0
    github_commits_90d: int = 0
    github_ci_present: bool = False
    huggingface_model_hits: int = 0
    openalex_hits: int = 0
    semantic_scholar_hits: int = 0
    dd_key_roles: str = ""
    dd_references_count: int = 0
    dd_is_investable: bool = False
    profile_coverage_score: int = 0
    known_url_count: int = 0
    linkedin_hits: int = 0
    researchgate_hits: int = 0


class ImportResult(BaseModel):
    total_imported: int
    spin_off_count: int
    all_initiatives_count: int
    duplicates_updated: int


class CustomColumnCreate(BaseModel):
    key: str
    label: str
    col_type: str = "text"
    show_in_list: bool = True
    sort_order: int = 0

    @field_validator("key")
    @classmethod
    def key_must_be_safe(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("key must contain only letters, numbers, hyphens, and underscores")
        return v


class CustomColumnUpdate(BaseModel):
    label: str | None = None
    col_type: str | None = None
    show_in_list: bool | None = None
    sort_order: int | None = None


class StatsOut(BaseModel):
    total: int
    enriched: int
    scored: int
    by_verdict: dict[str, int]
    by_classification: dict[str, int]
    by_uni: dict[str, int]


class ScoringPromptUpdate(BaseModel):
    content: str
