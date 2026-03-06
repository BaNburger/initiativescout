"""Pydantic request/response schemas for the Scout API.

Entity list/detail endpoints return schema-driven dicts (no Pydantic response model).
These models are kept for request body validation and specific response shapes.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


class ProjectOut(BaseModel):
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
    grade_team: str | None = None
    grade_team_num: float | None = None
    grade_tech: str | None = None
    grade_tech_num: float | None = None
    grade_opportunity: str | None = None
    grade_opportunity_num: float | None = None


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
