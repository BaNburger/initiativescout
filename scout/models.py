from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Initiative(Base):
    __tablename__ = "initiatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    uni: Mapped[str] = mapped_column(String(50), default="")
    faculty: Mapped[str] = mapped_column(String(200), default="")
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
    custom_fields_json: Mapped[str] = mapped_column(Text, default="{}")
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

    # Flexible metadata for arbitrary entity types
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (
        Index("ix_initiative_uni", "uni"),
    )

    enrichments: Mapped[list[Enrichment]] = relationship("Enrichment", back_populates="initiative", cascade="all, delete-orphan")
    scores: Mapped[list[OutreachScore]] = relationship("OutreachScore", back_populates="initiative", cascade="all, delete-orphan")
    projects: Mapped[list[Project]] = relationship("Project", back_populates="initiative", cascade="all, delete-orphan")

    # --- Field accessors for entity-type-agnostic access ---

    _SKIP_FIELDS = frozenset({
        "metadata_json", "custom_fields_json", "extra_links_json",
        "enrichments", "scores", "projects",
    })

    def field(self, key: str, default=""):
        """Read a field — checks column first, falls back to metadata_json.

        Works for both hardcoded initiative columns AND arbitrary entity types
        that store their data in metadata_json.
        """
        if key not in self._SKIP_FIELDS and key in self.__table__.columns:
            val = getattr(self, key, None)
            if val is not None and val != "" and val != 0 and val is not False:
                return val
        meta = json.loads(self.metadata_json or "{}")
        val = meta.get(key)
        if val is not None:
            return val
        custom = json.loads(self.custom_fields_json or "{}")
        return custom.get(key, default)

    def set_field(self, key: str, value) -> None:
        """Set a field — direct column if it exists, else metadata_json."""
        if key not in self._SKIP_FIELDS and key in self.__table__.columns:
            setattr(self, key, value)
        else:
            meta = json.loads(self.metadata_json or "{}")
            meta[key] = value
            self.metadata_json = json.dumps(meta)

    def all_fields(self) -> dict:
        """Return all non-empty fields from columns + metadata_json + custom_fields_json."""
        result = {}
        for col in self.__table__.columns:
            if col.name in self._SKIP_FIELDS or col.name == "id":
                continue
            val = getattr(self, col.name, None)
            if val is not None and val != "" and val != 0 and val is not False:
                result[col.name] = val
        for src in (self.metadata_json, self.custom_fields_json):
            for k, v in json.loads(src or "{}").items():
                if v and k not in result:
                    result[k] = v
        return result


class Enrichment(Base):
    __tablename__ = "enrichments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(Integer, ForeignKey("initiatives.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "website" | "github" | "team_page"
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_enrichment_initiative", "initiative_id"),
    )

    initiative: Mapped[Initiative] = relationship("Initiative", back_populates="enrichments")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(Integer, ForeignKey("initiatives.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(500), default="")
    github_url: Mapped[str] = mapped_column(String(500), default="")
    team: Mapped[str] = mapped_column(Text, default="")
    extra_links_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    initiative: Mapped[Initiative] = relationship("Initiative", back_populates="projects")
    scores: Mapped[list[OutreachScore]] = relationship("OutreachScore", back_populates="project", cascade="all, delete-orphan")


class OutreachScore(Base):
    __tablename__ = "outreach_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(Integer, ForeignKey("initiatives.id"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)  # reach_out_now | reach_out_soon | monitor | skip
    score: Mapped[float] = mapped_column(Float, default=3.0)
    classification: Mapped[str] = mapped_column(String(50), default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    contact_who: Mapped[str] = mapped_column(Text, default="")
    contact_channel: Mapped[str] = mapped_column(String(50), default="")
    engagement_hook: Mapped[str] = mapped_column(Text, default="")
    key_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    data_gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    # Dimension grades (school grades A+ through D)
    grade_team: Mapped[str] = mapped_column(String(3), default="")
    grade_team_num: Mapped[float] = mapped_column(Float, default=5.0)
    grade_tech: Mapped[str] = mapped_column(String(3), default="")
    grade_tech_num: Mapped[float] = mapped_column(Float, default=5.0)
    grade_opportunity: Mapped[str] = mapped_column(String(3), default="")
    grade_opportunity_num: Mapped[float] = mapped_column(Float, default=5.0)
    # Flexible dimension grades for custom scoring dimensions
    dimension_grades_json: Mapped[str] = mapped_column(Text, default="{}")
    llm_model: Mapped[str] = mapped_column(String(100), default="")
    scored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_score_initiative_scored", "initiative_id", "scored_at"),
        Index("ix_score_project_id", "project_id"),
    )

    initiative: Mapped[Initiative] = relationship("Initiative", back_populates="scores")
    project: Mapped[Project | None] = relationship("Project", back_populates="scores")


class CustomColumn(Base):
    __tablename__ = "custom_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    col_type: Mapped[str] = mapped_column(String(20), default="text")
    show_in_list: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    database: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScoringPrompt(Base):
    __tablename__ = "scoring_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
