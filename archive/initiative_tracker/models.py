from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from initiative_tracker.utils import utc_now


class Base(DeclarativeBase):
    pass


class Initiative(Base):
    __tablename__ = "initiatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    university: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    primary_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description_summary_en: Mapped[str] = mapped_column(Text, default="", nullable=False)
    categories_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    technologies_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    markets_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    team_signals_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    sources: Mapped[list[InitiativeSource]] = relationship(back_populates="initiative")
    observations: Mapped[list[RawObservation]] = relationship(back_populates="initiative")
    signals: Mapped[list[Signal]] = relationship(back_populates="initiative")
    scores: Mapped[list[Score]] = relationship(back_populates="initiative")
    score_components: Mapped[list[ScoreComponent]] = relationship(back_populates="initiative")
    people_links: Mapped[list[InitiativePerson]] = relationship(back_populates="initiative")
    actions: Mapped[list[InitiativeAction]] = relationship(back_populates="initiative")
    statuses: Mapped[list[InitiativeStatus]] = relationship(back_populates="initiative")
    dd_team_facts: Mapped[list[DDTeamFact]] = relationship(back_populates="initiative")
    dd_tech_facts: Mapped[list[DDTechFact]] = relationship(back_populates="initiative")
    dd_market_facts: Mapped[list[DDMarketFact]] = relationship(back_populates="initiative")
    dd_legal_facts: Mapped[list[DDLegalFact]] = relationship(back_populates="initiative")
    dd_finance_facts: Mapped[list[DDFinanceFact]] = relationship(back_populates="initiative")
    dd_gates: Mapped[list[DDGate]] = relationship(back_populates="initiative")
    dd_scores: Mapped[list[DDScore]] = relationship(back_populates="initiative")
    dd_score_components: Mapped[list[DDScoreComponent]] = relationship(back_populates="initiative")
    dd_evidence_items: Mapped[list[DDEvidenceItem]] = relationship(back_populates="initiative")
    dd_claims: Mapped[list[DDClaim]] = relationship(back_populates="initiative")
    dd_ai_assists: Mapped[list[DDAIAssist]] = relationship(back_populates="initiative")
    dd_memos: Mapped[list[DDMemo]] = relationship(back_populates="initiative")


class InitiativeSource(Base):
    __tablename__ = "initiative_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    external_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="sources")


class RawObservation(Base):
    __tablename__ = "raw_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="observations")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="signals")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    tech_depth: Mapped[float] = mapped_column(Float, nullable=False)
    market_opportunity: Mapped[float] = mapped_column(Float, nullable=False)
    team_strength: Mapped[float] = mapped_column(Float, nullable=False)
    maturity: Mapped[float] = mapped_column(Float, nullable=False)
    composite_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_tech: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_market: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_team: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_maturity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actionability_0_6m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    support_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    outreach_now_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    venture_upside_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_actionability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_support_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="scores")
    components: Mapped[list[ScoreComponent]] = relationship(back_populates="score")


class Ranking(Base):
    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ranking_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_key: Mapped[str] = mapped_column(String(255), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    rank_position: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    supporting_initiatives_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_meta_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    person_type: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    headline: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    contact_channels_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_urls_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative_links: Mapped[list[InitiativePerson]] = relationship(back_populates="person")
    talent_scores: Mapped[list[TalentScore]] = relationship(back_populates="person")


class InitiativePerson(Base):
    __tablename__ = "initiative_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    is_primary_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="people_links")
    person: Mapped[Person] = relationship(back_populates="initiative_links")


class ScoreComponent(Base):
    __tablename__ = "score_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    score_id: Mapped[int | None] = mapped_column(ForeignKey("scores.id"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    component_key: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    normalized_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weighted_contribution: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_mix_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), default="derived", nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="score_components")
    score: Mapped[Score | None] = relationship(back_populates="components")
    evidences: Mapped[list[ScoreEvidence]] = relationship(back_populates="component")


class ScoreEvidence(Base):
    __tablename__ = "score_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    score_component_id: Mapped[int] = mapped_column(ForeignKey("score_components.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    signal_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    component: Mapped[ScoreComponent] = relationship(back_populates="evidences")


class TalentScore(Base):
    __tablename__ = "talent_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), nullable=False)
    talent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reachability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    operator_strength: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    investor_relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    network_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    person: Mapped[Person] = relationship(back_populates="talent_scores")


class InitiativeAction(Base):
    __tablename__ = "initiative_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    lens: Mapped[str] = mapped_column(String(32), default="outreach", nullable=False)
    why_now: Mapped[str] = mapped_column(Text, default="", nullable=False)
    primary_contact_person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    recommended_support_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    first_meeting_goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_30_days_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="actions")


class InitiativeStatus(Base):
    __tablename__ = "initiative_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)
    owner: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_step_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="statuses")


class DDTeamFact(Base):
    __tablename__ = "dd_team_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    commitment_level: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    key_roles_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    references_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    founder_risk_flags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    investable_segment: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    is_investable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_team_facts")


class DDTechFact(Base):
    __tablename__ = "dd_tech_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    github_org: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    github_repo: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    repo_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contributor_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commit_velocity_90d: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ci_present: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_signal: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    benchmark_artifacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prototype_stage: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    ip_indicators_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_tech_facts")


class DDMarketFact(Base):
    __tablename__ = "dd_market_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    customer_interviews: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lois: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pilots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paid_pilots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pricing_evidence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    buyer_persona_clarity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sam_som_quality: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_market_facts")


class DDLegalFact(Base):
    __tablename__ = "dd_legal_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    entity_status: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    ip_ownership_status: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    founder_agreements: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    licensing_constraints: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    compliance_flags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    legal_risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_legal_facts")


class DDFinanceFact(Base):
    __tablename__ = "dd_finance_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    burn_monthly: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    runway_months: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    funding_dependence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cap_table_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dilution_risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_finance_facts")


class DDGate(Base):
    __tablename__ = "dd_gates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    gate_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="fail", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_gates")


class DDScore(Base):
    __tablename__ = "dd_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    team_dd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tech_dd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    market_dd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    execution_dd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    legal_dd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    team_product_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    team_tech_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    team_sales_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    market_validation_stage: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    conviction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    conviction_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_scores")
    components: Mapped[list[DDScoreComponent]] = relationship(back_populates="dd_score")


class DDScoreComponent(Base):
    __tablename__ = "dd_score_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    dd_score_id: Mapped[int | None] = mapped_column(ForeignKey("dd_scores.id"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    component_key: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    normalized_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weighted_contribution: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rule_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ai_suggested_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    final_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_review_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    audit_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_mix_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_score_components")
    dd_score: Mapped[DDScore | None] = relationship(back_populates="components")


class DDEvidenceItem(Base):
    __tablename__ = "dd_evidence_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    quality: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_evidence_items")


class DDClaim(Base):
    __tablename__ = "dd_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    claim_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    claim_value_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    extractor: Mapped[str] = mapped_column(String(32), default="rule", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_item_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_claims")


class DDAIAssist(Base):
    __tablename__ = "dd_ai_assists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    component_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="heuristic-fallback", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    ai_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cited_claim_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_ai_assists")


class DDMemo(Base):
    __tablename__ = "dd_memos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), default="monitor", nullable=False)
    check_size_band: Mapped[str] = mapped_column(String(64), default="n/a", nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    top_risks_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    next_actions_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    recommendation_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship(back_populates="dd_memos")


class EvidenceDossier(Base):
    __tablename__ = "evidence_dossiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    dossier_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dossier_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship("Initiative", backref="evidence_dossiers")


class LLMScore(Base):
    __tablename__ = "llm_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)

    technical_substance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    team_capability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    problem_market_clarity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    traction_momentum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reachability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    investability_signal: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    confidence_technical: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_team: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_market: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_traction: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_reachability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_investability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    composite_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    composite_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    classification: Mapped[str] = mapped_column(String(64), default="unclear", nullable=False)
    initiative_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    overall_assessment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(32), default="monitor_quarterly", nullable=False)
    engagement_hook: Mapped[str] = mapped_column(Text, default="", nullable=False)

    llm_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    evidence_dossier_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    dimension_details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    data_gaps_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship("Initiative", backref="llm_scores")


class InitiativeTier(Base):
    __tablename__ = "initiative_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initiative_id: Mapped[int] = mapped_column(ForeignKey("initiatives.id"), nullable=False)
    llm_score_id: Mapped[int] = mapped_column(ForeignKey("llm_scores.id"), nullable=False)

    tier: Mapped[str] = mapped_column(String(2), default="X", nullable=False)
    tier_rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)

    composite_percentile: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    dimension_percentiles_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    previous_tier: Mapped[str | None] = mapped_column(String(2), nullable=True)
    tier_change: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tier_change_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)

    cohort_stats_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    initiative: Mapped[Initiative] = relationship("Initiative", backref="tiers")
    llm_score: Mapped[LLMScore] = relationship("LLMScore", backref="tier")


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


Index("ix_initiatives_name", Initiative.normalized_name)
Index("ix_initiatives_url", Initiative.primary_url)
Index("ix_initiatives_name_url_unique", Initiative.normalized_name, Initiative.primary_url, unique=True)
Index("ix_sources_initiative", InitiativeSource.initiative_id)
Index("ix_signals_initiative", Signal.initiative_id)
Index("ix_scores_initiative_scored_at", Score.initiative_id, Score.scored_at)
Index("ix_rankings_type_generated_at", Ranking.ranking_type, Ranking.generated_at)
Index("ix_pipeline_stage_started", PipelineRun.stage, PipelineRun.started_at)
Index("ix_people_name", Person.normalized_name)
Index("ix_people_name_type", Person.normalized_name, Person.person_type)
Index("ix_initiative_people_unique", InitiativePerson.initiative_id, InitiativePerson.person_id, InitiativePerson.role, unique=True)
Index("ix_score_components_initiative", ScoreComponent.initiative_id)
Index("ix_score_components_dim_key", ScoreComponent.initiative_id, ScoreComponent.dimension, ScoreComponent.component_key)
Index("ix_score_evidence_component", ScoreEvidence.score_component_id)
Index("ix_talent_scores_person_time", TalentScore.person_id, TalentScore.scored_at)
Index("ix_initiative_actions_initiative_lens", InitiativeAction.initiative_id, InitiativeAction.lens)
Index("ix_initiative_status_initiative", InitiativeStatus.initiative_id, unique=True)
Index("ix_dd_team_facts_initiative", DDTeamFact.initiative_id)
Index("ix_dd_tech_facts_initiative", DDTechFact.initiative_id)
Index("ix_dd_market_facts_initiative", DDMarketFact.initiative_id)
Index("ix_dd_legal_facts_initiative", DDLegalFact.initiative_id)
Index("ix_dd_finance_facts_initiative", DDFinanceFact.initiative_id)
Index("ix_dd_gates_initiative_gate", DDGate.initiative_id, DDGate.gate_name)
Index("ix_dd_scores_initiative_scored_at", DDScore.initiative_id, DDScore.scored_at)
Index("ix_dd_score_components_initiative", DDScoreComponent.initiative_id)
Index("ix_dd_score_components_dim_key", DDScoreComponent.initiative_id, DDScoreComponent.dimension, DDScoreComponent.component_key)
Index("ix_dd_evidence_items_initiative_fetched", DDEvidenceItem.initiative_id, DDEvidenceItem.fetched_at)
Index("ix_dd_claims_initiative_created", DDClaim.initiative_id, DDClaim.created_at)
Index("ix_dd_ai_assists_initiative_created", DDAIAssist.initiative_id, DDAIAssist.created_at)
Index("ix_dd_memos_initiative_created_at", DDMemo.initiative_id, DDMemo.created_at)
Index("ix_schema_meta_key", SchemaMeta.key, unique=True)
Index("ix_evidence_dossiers_initiative", EvidenceDossier.initiative_id)
Index("ix_llm_scores_initiative_scored_at", LLMScore.initiative_id, LLMScore.scored_at)
Index("ix_initiative_tiers_initiative", InitiativeTier.initiative_id)
Index("ix_initiative_tiers_tier", InitiativeTier.tier)
