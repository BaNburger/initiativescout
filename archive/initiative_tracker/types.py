from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class SourceInitiative(BaseModel):
    name: str
    university: str | None = None
    source_name: str
    source_url: str
    external_url: str | None = None
    description_raw: str | None = None
    categories: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    team_signals: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InitiativeRecord(BaseModel):
    initiative_id: int
    name: str
    university: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    description_raw: str | None = None
    description_summary_en: str | None = None
    categories: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    team_signals: list[str] = Field(default_factory=list)
    last_seen_at: datetime | None = None
    confidence: float = 0.0


class InitiativeScore(BaseModel):
    initiative_id: int
    tech_depth: float
    market_opportunity: float
    team_strength: float
    maturity: float
    composite_score: float
    confidence_tech: float
    confidence_market: float
    confidence_team: float
    confidence_maturity: float


class TechnologyRankingItem(BaseModel):
    technology_domain: str
    opportunity_score: float
    supporting_initiatives: list[str] = Field(default_factory=list)
    evidence_count: int


class MarketRankingItem(BaseModel):
    market_domain: str
    opportunity_score: float
    supporting_initiatives: list[str] = Field(default_factory=list)
    evidence_count: int


class TeamRankingItem(BaseModel):
    initiative_id: int
    initiative_name: str
    team_strength: float
    supporting_signals: list[str] = Field(default_factory=list)
    composite_score: float | None = None


class EvidenceRef(BaseModel):
    source_url: str
    snippet: str
    signal_type: str
    signal_key: str
    value: float


class ScoreComponentBreakdown(BaseModel):
    initiative_id: int
    dimension: str
    component_key: str
    raw_value: float
    normalized_value: float
    weight: float
    weighted_contribution: float
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_mix: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class TalentRecord(BaseModel):
    person_id: int
    name: str
    person_type: str
    roles: list[str] = Field(default_factory=list)
    initiative_ids: list[int] = Field(default_factory=list)
    contact_channels: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    confidence: float = 0.0
    why_ranked: list[str] = Field(default_factory=list)


class ActionPlaybook(BaseModel):
    why_now: str
    primary_contact: str | None = None
    recommended_support: list[str] = Field(default_factory=list)
    first_meeting_goal: str = ""
    next_30_days: list[str] = Field(default_factory=list)


class InitiativeDossier(BaseModel):
    initiative_id: int
    lens_scores: dict[str, float | None] = Field(default_factory=dict)
    score_breakdown: list[ScoreComponentBreakdown] = Field(default_factory=list)
    technology_profile: list[dict[str, Any]] = Field(default_factory=list)
    market_profile: list[dict[str, Any]] = Field(default_factory=list)
    top_talent: list[TalentRecord] = Field(default_factory=list)
    action_playbook: ActionPlaybook
    risk_flags: list[str] = Field(default_factory=list)
    pipeline_status: dict[str, Any] = Field(default_factory=dict)


class InitiativeStatusRecord(BaseModel):
    initiative_id: int
    status: str
    owner: str = ""
    last_contact_at: datetime | None = None
    next_step_date: date | None = None
    notes: str = ""


class DDEvidenceRef(BaseModel):
    source_type: str
    source_url: str
    snippet: str
    doc_id: str | None = None
    confidence: float = 0.0


class DDGateResult(BaseModel):
    initiative_id: int
    gate_name: str
    status: str
    reason: str
    evidence_refs: list[DDEvidenceRef] = Field(default_factory=list)
    updated_at: datetime | None = None


class DDScorecard(BaseModel):
    initiative_id: int
    team_dd: float
    tech_dd: float
    market_dd: float
    execution_dd: float
    legal_dd: float
    team_product_fit: float = 0.0
    team_tech_fit: float = 0.0
    team_sales_fit: float = 0.0
    market_validation_stage: str = "none"
    conviction_confidence: float = 0.0
    conviction_score: float


class InvestmentRecommendation(BaseModel):
    initiative_id: int
    decision: str
    check_size_band: str
    rationale: str
    top_risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    strong_in: list[str] = Field(default_factory=list)
    need_help_in: list[str] = Field(default_factory=list)
    support_priority: str = ""


class TeamCapabilityAssessment(BaseModel):
    initiative_id: int
    product_fit: float
    tech_fit: float
    sales_fit: float
    strong_in: list[str] = Field(default_factory=list)
    need_help_in: list[str] = Field(default_factory=list)
    critical_gap: str | None = None
