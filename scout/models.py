from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from pydantic import BaseModel
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
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
