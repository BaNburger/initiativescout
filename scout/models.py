from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Initiative(Base):
    __tablename__ = "initiatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    uni: Mapped[str] = mapped_column(String(50), default="")
    sector: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(50), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(500), default="")
    email: Mapped[str] = mapped_column(String(300), default="")
    team_page: Mapped[str] = mapped_column(String(500), default="")
    team_size: Mapped[str] = mapped_column(String(50), default="")
    linkedin: Mapped[str] = mapped_column(String(500), default="")
    github_org: Mapped[str] = mapped_column(String(200), default="")
    key_repos: Mapped[str] = mapped_column(Text, default="")
    sponsors: Mapped[str] = mapped_column(Text, default="")
    competitions: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[str] = mapped_column(String(50), default="")
    sheet_source: Mapped[str] = mapped_column(String(50), default="")
    extra_links_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # --- Fields from overview spreadsheet ---
    # Classification
    technology_domains: Mapped[str] = mapped_column(Text, default="")
    market_domains: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(Text, default="")
    # Team signals
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    member_examples: Mapped[str] = mapped_column(Text, default="")
    member_roles: Mapped[str] = mapped_column(Text, default="")
    # GitHub signals
    github_repo_count: Mapped[int] = mapped_column(Integer, default=0)
    github_contributors: Mapped[int] = mapped_column(Integer, default=0)
    github_commits_90d: Mapped[int] = mapped_column(Integer, default=0)
    github_ci_present: Mapped[bool] = mapped_column(Boolean, default=False)
    # Research signals
    huggingface_model_hits: Mapped[int] = mapped_column(Integer, default=0)
    openalex_hits: Mapped[int] = mapped_column(Integer, default=0)
    semantic_scholar_hits: Mapped[int] = mapped_column(Integer, default=0)
    # Due diligence
    dd_key_roles: Mapped[str] = mapped_column(Text, default="")
    dd_references_count: Mapped[int] = mapped_column(Integer, default=0)
    dd_is_investable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Pre-computed scores
    outreach_now_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    venture_upside_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # Coverage
    profile_coverage_score: Mapped[int] = mapped_column(Integer, default=0)
    known_url_count: Mapped[int] = mapped_column(Integer, default=0)
    linkedin_hits: Mapped[int] = mapped_column(Integer, default=0)
    researchgate_hits: Mapped[int] = mapped_column(Integer, default=0)

    enrichments: Mapped[list[Enrichment]] = relationship("Enrichment", back_populates="initiative", cascade="all, delete-orphan")
    scores: Mapped[list[OutreachScore]] = relationship("OutreachScore", back_populates="initiative", cascade="all, delete-orphan")


class Enrichment(Base):
    __tablename__ = "enrichments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(Integer, ForeignKey("initiatives.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "website" | "github" | "team_page"
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    initiative: Mapped[Initiative] = relationship("Initiative", back_populates="enrichments")


class OutreachScore(Base):
    __tablename__ = "outreach_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(Integer, ForeignKey("initiatives.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)  # reach_out_now | reach_out_soon | monitor | skip
    score: Mapped[float] = mapped_column(Float, default=3.0)
    classification: Mapped[str] = mapped_column(String(50), default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    contact_who: Mapped[str] = mapped_column(Text, default="")
    contact_channel: Mapped[str] = mapped_column(String(50), default="")
    engagement_hook: Mapped[str] = mapped_column(Text, default="")
    key_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    data_gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    llm_model: Mapped[str] = mapped_column(String(100), default="")
    scored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    initiative: Mapped[Initiative] = relationship("Initiative", back_populates="scores")


# ---------------------------------------------------------------------------
# Pydantic schemas for API responses
# ---------------------------------------------------------------------------


class InitiativeOut(BaseModel):
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


class EnrichmentOut(BaseModel):
    id: int
    source_type: str
    summary: str
    fetched_at: str


class ScoreOut(BaseModel):
    verdict: str
    score: float
    classification: str
    reasoning: str
    contact_who: str
    contact_channel: str
    engagement_hook: str
    key_evidence: list[str]
    data_gaps: list[str]


class ImportResult(BaseModel):
    total_imported: int
    spin_off_count: int
    all_initiatives_count: int
    duplicates_updated: int


class StatsOut(BaseModel):
    total: int
    enriched: int
    scored: int
    by_verdict: dict[str, int]
    by_classification: dict[str, int]
    by_uni: dict[str, int]
