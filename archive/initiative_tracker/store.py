from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from initiative_tracker.models import (
    DDAIAssist,
    DDClaim,
    DDEvidenceItem,
    DDGate,
    DDLegalFact,
    DDMemo,
    DDMarketFact,
    DDScore,
    DDScoreComponent,
    DDTeamFact,
    DDTechFact,
    DDFinanceFact,
    Initiative,
    InitiativeAction,
    InitiativePerson,
    InitiativeSource,
    InitiativeStatus,
    Person,
    RawObservation,
    ScoreComponent,
    ScoreEvidence,
    Signal,
    TalentScore,
)
from initiative_tracker.utils import (
    canonicalize_url,
    from_json,
    normalize_name,
    text_hash,
    to_json,
    unique_list,
    utc_now,
)


def _merge_list_json(existing_json: str, incoming: list[str] | None) -> str:
    existing = from_json(existing_json, [])
    merged = unique_list([*(existing or []), *((incoming or []))])
    return to_json(merged)


def _find_candidate(session: Session, normalized_name: str, canonical_url: str, university: str) -> Initiative | None:
    conditions = [Initiative.normalized_name == normalized_name]
    if canonical_url:
        conditions.append(Initiative.primary_url == canonical_url)
    candidate = session.execute(select(Initiative).where(or_(*conditions))).scalars().first()
    if candidate:
        return candidate

    scoped = session.execute(
        select(Initiative).where(Initiative.university == (university or ""))
    ).scalars().all()
    best_score = 0.0
    best_item: Initiative | None = None
    for item in scoped:
        score = fuzz.ratio(normalized_name, item.normalized_name)
        if score > best_score:
            best_score = score
            best_item = item
    if best_item and best_score >= 95:
        return best_item

    all_items = session.execute(select(Initiative)).scalars().all()
    for item in all_items:
        if fuzz.ratio(normalized_name, item.normalized_name) >= 97:
            return item
    return None


def upsert_initiative(
    session: Session,
    *,
    name: str,
    university: str | None = None,
    primary_url: str | None = None,
    description_raw: str | None = None,
    description_summary_en: str | None = None,
    categories: list[str] | None = None,
    technologies: list[str] | None = None,
    markets: list[str] | None = None,
    team_signals: list[str] | None = None,
    confidence: float | None = None,
) -> Initiative:
    normalized_name = normalize_name(name)
    canonical_url = canonicalize_url(primary_url)
    normalized_university = (university or "").strip().upper()

    initiative = _find_candidate(session, normalized_name, canonical_url, normalized_university)
    created = False
    if initiative is None:
        initiative = Initiative(
            canonical_name=name.strip(),
            normalized_name=normalized_name,
            university=normalized_university,
            primary_url=canonical_url,
            description_raw=description_raw or "",
            description_summary_en=description_summary_en or "",
            categories_json=to_json(unique_list(categories or [])),
            technologies_json=to_json(unique_list(technologies or [])),
            markets_json=to_json(unique_list(markets or [])),
            team_signals_json=to_json(unique_list(team_signals or [])),
            confidence=confidence or 0.0,
            last_seen_at=utc_now(),
        )
        session.add(initiative)
        session.flush()
        created = True

    if not created:
        initiative.canonical_name = initiative.canonical_name or name.strip()
        if canonical_url and (not initiative.primary_url):
            initiative.primary_url = canonical_url
        if normalized_university and (not initiative.university):
            initiative.university = normalized_university

        if description_raw and len(description_raw) > len(initiative.description_raw):
            initiative.description_raw = description_raw
        if description_summary_en and len(description_summary_en) > len(initiative.description_summary_en):
            initiative.description_summary_en = description_summary_en

        initiative.categories_json = _merge_list_json(initiative.categories_json, categories)
        initiative.technologies_json = _merge_list_json(initiative.technologies_json, technologies)
        initiative.markets_json = _merge_list_json(initiative.markets_json, markets)
        initiative.team_signals_json = _merge_list_json(initiative.team_signals_json, team_signals)
        if confidence is not None:
            initiative.confidence = max(initiative.confidence, confidence)
        initiative.last_seen_at = utc_now()

    session.add(initiative)
    session.flush()
    return initiative


def add_initiative_source(
    session: Session,
    *,
    initiative_id: int,
    source_type: str,
    source_name: str,
    source_url: str,
    external_url: str | None,
    payload: dict[str, Any],
) -> InitiativeSource:
    canonical_source_url = canonicalize_url(source_url)
    canonical_external_url = canonicalize_url(external_url)
    payload_hash = text_hash(payload)

    existing = session.execute(
        select(InitiativeSource).where(
            InitiativeSource.initiative_id == initiative_id,
            InitiativeSource.source_type == source_type,
            InitiativeSource.source_url == canonical_source_url,
            InitiativeSource.external_url == canonical_external_url,
        )
    ).scalars().first()

    if existing:
        existing.last_seen_at = utc_now()
        existing.raw_hash = payload_hash
        session.add(existing)
        return existing

    source = InitiativeSource(
        initiative_id=initiative_id,
        source_type=source_type,
        source_name=source_name,
        source_url=canonical_source_url,
        external_url=canonical_external_url,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        raw_hash=payload_hash,
    )
    session.add(source)
    session.flush()
    return source


def add_raw_observation(
    session: Session,
    *,
    initiative_id: int,
    source_type: str,
    source_name: str,
    source_url: str,
    payload: dict[str, Any],
) -> RawObservation:
    payload_json = to_json(payload)
    observation = RawObservation(
        initiative_id=initiative_id,
        source_type=source_type,
        source_name=source_name,
        source_url=canonicalize_url(source_url),
        payload_json=payload_json,
        payload_hash=text_hash(payload),
        observed_at=utc_now(),
    )
    session.add(observation)
    session.flush()
    return observation


def add_signal(
    session: Session,
    *,
    initiative_id: int,
    signal_type: str,
    signal_key: str,
    value: float,
    evidence_text: str,
    source_type: str,
    source_url: str,
) -> Signal:
    canonical_source_url = canonicalize_url(source_url)
    existing = session.execute(
        select(Signal).where(
            Signal.initiative_id == initiative_id,
            Signal.signal_type == signal_type,
            Signal.signal_key == signal_key,
            Signal.source_type == source_type,
            Signal.source_url == canonical_source_url,
        )
    ).scalars().first()

    if existing:
        existing.value = value
        existing.evidence_text = evidence_text
        existing.created_at = utc_now()
        session.add(existing)
        return existing

    signal = Signal(
        initiative_id=initiative_id,
        signal_type=signal_type,
        signal_key=signal_key,
        value=value,
        evidence_text=evidence_text,
        source_type=source_type,
        source_url=canonical_source_url,
        created_at=utc_now(),
    )
    session.add(signal)
    session.flush()
    return signal


def get_json_list(value: str) -> list[str]:
    return from_json(value, [])


def _merge_json_values(existing_json: str, incoming: list[str] | None) -> str:
    existing = [str(v) for v in from_json(existing_json, []) or []]
    return to_json(unique_list([*existing, *((incoming or []))]))


def upsert_person(
    session: Session,
    *,
    name: str,
    person_type: str,
    headline: str = "",
    contact_channels: list[str] | None = None,
    source_urls: list[str] | None = None,
    confidence: float = 0.0,
) -> Person:
    normalized_name = normalize_name(name)
    canonical_sources = [canonicalize_url(url) for url in (source_urls or []) if canonicalize_url(url)]

    existing = session.execute(
        select(Person).where(
            Person.normalized_name == normalized_name,
            Person.person_type == person_type,
        )
    ).scalars().first()
    if existing is None:
        existing = session.execute(
            select(Person).where(Person.normalized_name == normalized_name)
        ).scalars().first()

    if existing is None:
        person = Person(
            canonical_name=name.strip(),
            normalized_name=normalized_name,
            person_type=person_type,
            headline=headline.strip(),
            contact_channels_json=to_json(unique_list(contact_channels or [])),
            source_urls_json=to_json(unique_list(canonical_sources)),
            confidence=confidence,
        )
        session.add(person)
        session.flush()
        return person

    existing.canonical_name = existing.canonical_name or name.strip()
    if person_type and existing.person_type == "unknown":
        existing.person_type = person_type
    if headline and len(headline) > len(existing.headline):
        existing.headline = headline.strip()
    existing.contact_channels_json = _merge_json_values(existing.contact_channels_json, contact_channels)
    existing.source_urls_json = _merge_json_values(existing.source_urls_json, canonical_sources)
    existing.confidence = max(existing.confidence, confidence)
    session.add(existing)
    session.flush()
    return existing


def link_person_to_initiative(
    session: Session,
    *,
    initiative_id: int,
    person_id: int,
    role: str,
    is_primary_contact: bool,
    source_type: str,
    source_url: str,
) -> InitiativePerson:
    existing = session.execute(
        select(InitiativePerson).where(
            InitiativePerson.initiative_id == initiative_id,
            InitiativePerson.person_id == person_id,
            InitiativePerson.role == role.strip(),
        )
    ).scalars().first()
    if existing:
        existing.is_primary_contact = existing.is_primary_contact or is_primary_contact
        if source_url:
            existing.source_url = canonicalize_url(source_url)
        if source_type:
            existing.source_type = source_type
        session.add(existing)
        session.flush()
        return existing

    link = InitiativePerson(
        initiative_id=initiative_id,
        person_id=person_id,
        role=role.strip(),
        is_primary_contact=is_primary_contact,
        source_type=source_type,
        source_url=canonicalize_url(source_url),
    )
    session.add(link)
    session.flush()
    return link


def add_score_component(
    session: Session,
    *,
    initiative_id: int,
    score_id: int | None,
    dimension: str,
    component_key: str,
    raw_value: float,
    normalized_value: float,
    weight: float,
    weighted_contribution: float,
    confidence: float,
    evidence_count: int,
    source_mix: list[str],
    provenance: str,
) -> ScoreComponent:
    existing = session.execute(
        select(ScoreComponent).where(
            ScoreComponent.initiative_id == initiative_id,
            ScoreComponent.dimension == dimension,
            ScoreComponent.component_key == component_key,
        )
    ).scalars().first()
    if existing:
        existing.score_id = score_id
        existing.raw_value = raw_value
        existing.normalized_value = normalized_value
        existing.weight = weight
        existing.weighted_contribution = weighted_contribution
        existing.confidence = confidence
        existing.evidence_count = evidence_count
        existing.source_mix_json = to_json(unique_list(source_mix))
        existing.provenance = provenance
        existing.scored_at = utc_now()
        session.add(existing)
        session.flush()
        return existing

    row = ScoreComponent(
        initiative_id=initiative_id,
        score_id=score_id,
        dimension=dimension,
        component_key=component_key,
        raw_value=raw_value,
        normalized_value=normalized_value,
        weight=weight,
        weighted_contribution=weighted_contribution,
        confidence=confidence,
        evidence_count=evidence_count,
        source_mix_json=to_json(unique_list(source_mix)),
        provenance=provenance,
    )
    session.add(row)
    session.flush()
    return row


def replace_score_evidence(
    session: Session,
    *,
    component_id: int,
    initiative_id: int,
    evidences: list[dict[str, Any]],
) -> int:
    existing = session.execute(
        select(ScoreEvidence).where(ScoreEvidence.score_component_id == component_id)
    ).scalars().all()
    for row in existing:
        session.delete(row)

    inserted = 0
    for evidence in evidences:
        source_url = canonicalize_url(str(evidence.get("source_url") or ""))
        snippet = str(evidence.get("snippet") or "").strip()
        if not source_url or not snippet:
            continue
        row = ScoreEvidence(
            initiative_id=initiative_id,
            score_component_id=component_id,
            signal_type=str(evidence.get("signal_type") or ""),
            signal_key=str(evidence.get("signal_key") or ""),
            value=float(evidence.get("value") or 0.0),
            source_url=source_url,
            snippet=snippet,
        )
        session.add(row)
        inserted += 1
    session.flush()
    return inserted


def add_talent_score(
    session: Session,
    *,
    person_id: int,
    talent_type: str,
    reachability: float,
    operator_strength: float,
    investor_relevance: float,
    network_score: float,
    composite_score: float,
    confidence: float,
    reasons: list[str],
) -> TalentScore:
    row = TalentScore(
        person_id=person_id,
        talent_type=talent_type,
        reachability=reachability,
        operator_strength=operator_strength,
        investor_relevance=investor_relevance,
        network_score=network_score,
        composite_score=composite_score,
        confidence=confidence,
        reasons_json=to_json(unique_list(reasons)),
    )
    session.add(row)
    session.flush()
    return row


def upsert_dd_team_fact(
    session: Session,
    *,
    initiative_id: int,
    commitment_level: float,
    key_roles: list[str],
    references_count: int,
    founder_risk_flags: list[str],
    investable_segment: str,
    is_investable: bool,
    evidence: list[dict[str, Any]],
    source_type: str,
    source_url: str,
    confidence: float,
) -> DDTeamFact:
    existing = session.execute(
        select(DDTeamFact).where(DDTeamFact.initiative_id == initiative_id)
    ).scalars().first()
    if existing is None:
        existing = DDTeamFact(
            initiative_id=initiative_id,
            commitment_level=commitment_level,
            key_roles_json=to_json(unique_list(key_roles)),
            references_count=references_count,
            founder_risk_flags_json=to_json(unique_list(founder_risk_flags)),
            investable_segment=investable_segment,
            is_investable=is_investable,
            evidence_json=to_json(evidence),
            source_type=source_type,
            source_url=canonicalize_url(source_url),
            confidence=confidence,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.commitment_level = commitment_level
    existing.key_roles_json = to_json(unique_list(key_roles))
    existing.references_count = references_count
    existing.founder_risk_flags_json = to_json(unique_list(founder_risk_flags))
    existing.investable_segment = investable_segment
    existing.is_investable = is_investable
    existing.evidence_json = to_json(evidence)
    existing.source_type = source_type
    existing.source_url = canonicalize_url(source_url)
    existing.confidence = confidence
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_tech_fact(
    session: Session,
    *,
    initiative_id: int,
    github_org: str,
    github_repo: str,
    repo_count: int,
    contributor_count: int,
    commit_velocity_90d: float,
    ci_present: bool,
    test_signal: float,
    benchmark_artifacts: int,
    prototype_stage: str,
    ip_indicators: list[str],
    evidence: list[dict[str, Any]],
    source_type: str,
    source_url: str,
    confidence: float,
) -> DDTechFact:
    existing = session.execute(
        select(DDTechFact).where(DDTechFact.initiative_id == initiative_id)
    ).scalars().first()
    if existing is None:
        existing = DDTechFact(
            initiative_id=initiative_id,
            github_org=github_org,
            github_repo=github_repo,
            repo_count=repo_count,
            contributor_count=contributor_count,
            commit_velocity_90d=commit_velocity_90d,
            ci_present=ci_present,
            test_signal=test_signal,
            benchmark_artifacts=benchmark_artifacts,
            prototype_stage=prototype_stage,
            ip_indicators_json=to_json(unique_list(ip_indicators)),
            evidence_json=to_json(evidence),
            source_type=source_type,
            source_url=canonicalize_url(source_url),
            confidence=confidence,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.github_org = github_org
    existing.github_repo = github_repo
    existing.repo_count = repo_count
    existing.contributor_count = contributor_count
    existing.commit_velocity_90d = commit_velocity_90d
    existing.ci_present = ci_present
    existing.test_signal = test_signal
    existing.benchmark_artifacts = benchmark_artifacts
    existing.prototype_stage = prototype_stage
    existing.ip_indicators_json = to_json(unique_list(ip_indicators))
    existing.evidence_json = to_json(evidence)
    existing.source_type = source_type
    existing.source_url = canonicalize_url(source_url)
    existing.confidence = confidence
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_market_fact(
    session: Session,
    *,
    initiative_id: int,
    customer_interviews: int,
    lois: int,
    pilots: int,
    paid_pilots: int,
    pricing_evidence: bool,
    buyer_persona_clarity: float,
    sam_som_quality: float,
    evidence: list[dict[str, Any]],
    source_type: str,
    source_url: str,
    confidence: float,
) -> DDMarketFact:
    existing = session.execute(
        select(DDMarketFact).where(DDMarketFact.initiative_id == initiative_id)
    ).scalars().first()
    if existing is None:
        existing = DDMarketFact(
            initiative_id=initiative_id,
            customer_interviews=customer_interviews,
            lois=lois,
            pilots=pilots,
            paid_pilots=paid_pilots,
            pricing_evidence=pricing_evidence,
            buyer_persona_clarity=buyer_persona_clarity,
            sam_som_quality=sam_som_quality,
            evidence_json=to_json(evidence),
            source_type=source_type,
            source_url=canonicalize_url(source_url),
            confidence=confidence,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.customer_interviews = customer_interviews
    existing.lois = lois
    existing.pilots = pilots
    existing.paid_pilots = paid_pilots
    existing.pricing_evidence = pricing_evidence
    existing.buyer_persona_clarity = buyer_persona_clarity
    existing.sam_som_quality = sam_som_quality
    existing.evidence_json = to_json(evidence)
    existing.source_type = source_type
    existing.source_url = canonicalize_url(source_url)
    existing.confidence = confidence
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_legal_fact(
    session: Session,
    *,
    initiative_id: int,
    entity_status: str,
    ip_ownership_status: str,
    founder_agreements: bool,
    licensing_constraints: bool,
    compliance_flags: list[str],
    legal_risk_score: float,
    evidence: list[dict[str, Any]],
    source_type: str,
    source_url: str,
    confidence: float,
) -> DDLegalFact:
    existing = session.execute(
        select(DDLegalFact).where(DDLegalFact.initiative_id == initiative_id)
    ).scalars().first()
    if existing is None:
        existing = DDLegalFact(
            initiative_id=initiative_id,
            entity_status=entity_status,
            ip_ownership_status=ip_ownership_status,
            founder_agreements=founder_agreements,
            licensing_constraints=licensing_constraints,
            compliance_flags_json=to_json(unique_list(compliance_flags)),
            legal_risk_score=legal_risk_score,
            evidence_json=to_json(evidence),
            source_type=source_type,
            source_url=canonicalize_url(source_url),
            confidence=confidence,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.entity_status = entity_status
    existing.ip_ownership_status = ip_ownership_status
    existing.founder_agreements = founder_agreements
    existing.licensing_constraints = licensing_constraints
    existing.compliance_flags_json = to_json(unique_list(compliance_flags))
    existing.legal_risk_score = legal_risk_score
    existing.evidence_json = to_json(evidence)
    existing.source_type = source_type
    existing.source_url = canonicalize_url(source_url)
    existing.confidence = confidence
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_finance_fact(
    session: Session,
    *,
    initiative_id: int,
    burn_monthly: float,
    runway_months: float,
    funding_dependence: float,
    cap_table_summary: str,
    dilution_risk: float,
    evidence: list[dict[str, Any]],
    source_type: str,
    source_url: str,
    confidence: float,
) -> DDFinanceFact:
    existing = session.execute(
        select(DDFinanceFact).where(DDFinanceFact.initiative_id == initiative_id)
    ).scalars().first()
    if existing is None:
        existing = DDFinanceFact(
            initiative_id=initiative_id,
            burn_monthly=burn_monthly,
            runway_months=runway_months,
            funding_dependence=funding_dependence,
            cap_table_summary=cap_table_summary,
            dilution_risk=dilution_risk,
            evidence_json=to_json(evidence),
            source_type=source_type,
            source_url=canonicalize_url(source_url),
            confidence=confidence,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.burn_monthly = burn_monthly
    existing.runway_months = runway_months
    existing.funding_dependence = funding_dependence
    existing.cap_table_summary = cap_table_summary
    existing.dilution_risk = dilution_risk
    existing.evidence_json = to_json(evidence)
    existing.source_type = source_type
    existing.source_url = canonicalize_url(source_url)
    existing.confidence = confidence
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_gate(
    session: Session,
    *,
    initiative_id: int,
    gate_name: str,
    status: str,
    reason: str,
    evidence: list[dict[str, Any]],
) -> DDGate:
    existing = session.execute(
        select(DDGate).where(DDGate.initiative_id == initiative_id, DDGate.gate_name == gate_name)
    ).scalars().first()
    if existing is None:
        existing = DDGate(
            initiative_id=initiative_id,
            gate_name=gate_name,
            status=status,
            reason=reason,
            evidence_json=to_json(evidence),
        )
        session.add(existing)
        session.flush()
        return existing

    existing.status = status
    existing.reason = reason
    existing.evidence_json = to_json(evidence)
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def add_dd_score(
    session: Session,
    *,
    initiative_id: int,
    team_dd: float,
    tech_dd: float,
    market_dd: float,
    execution_dd: float,
    legal_dd: float,
    team_product_fit: float = 0.0,
    team_tech_fit: float = 0.0,
    team_sales_fit: float = 0.0,
    market_validation_stage: str = "none",
    conviction_confidence: float = 0.0,
    conviction_score: float,
) -> DDScore:
    row = DDScore(
        initiative_id=initiative_id,
        team_dd=team_dd,
        tech_dd=tech_dd,
        market_dd=market_dd,
        execution_dd=execution_dd,
        legal_dd=legal_dd,
        team_product_fit=team_product_fit,
        team_tech_fit=team_tech_fit,
        team_sales_fit=team_sales_fit,
        market_validation_stage=market_validation_stage,
        conviction_confidence=conviction_confidence,
        conviction_score=conviction_score,
    )
    session.add(row)
    session.flush()
    return row


def upsert_dd_score_component(
    session: Session,
    *,
    initiative_id: int,
    dd_score_id: int | None,
    dimension: str,
    component_key: str,
    raw_value: float,
    normalized_value: float,
    weight: float,
    weighted_contribution: float,
    confidence: float,
    evidence: list[dict[str, Any]],
    source_mix: list[str],
    rule_value: float = 0.0,
    ai_suggested_value: float = 0.0,
    final_value: float = 0.0,
    ai_used: bool = False,
    manual_review_flag: bool = False,
    audit_reason: str = "",
) -> DDScoreComponent:
    existing = session.execute(
        select(DDScoreComponent).where(
            DDScoreComponent.initiative_id == initiative_id,
            DDScoreComponent.dimension == dimension,
            DDScoreComponent.component_key == component_key,
        )
    ).scalars().first()
    if existing is None:
        existing = DDScoreComponent(
            initiative_id=initiative_id,
            dd_score_id=dd_score_id,
            dimension=dimension,
            component_key=component_key,
            raw_value=raw_value,
            normalized_value=normalized_value,
            weight=weight,
            weighted_contribution=weighted_contribution,
            rule_value=rule_value,
            ai_suggested_value=ai_suggested_value,
            final_value=final_value,
            ai_used=ai_used,
            manual_review_flag=manual_review_flag,
            audit_reason=audit_reason,
            confidence=confidence,
            evidence_json=to_json(evidence),
            source_mix_json=to_json(unique_list(source_mix)),
        )
        session.add(existing)
        session.flush()
        return existing

    existing.dd_score_id = dd_score_id
    existing.raw_value = raw_value
    existing.normalized_value = normalized_value
    existing.weight = weight
    existing.weighted_contribution = weighted_contribution
    existing.rule_value = rule_value
    existing.ai_suggested_value = ai_suggested_value
    existing.final_value = final_value
    existing.ai_used = ai_used
    existing.manual_review_flag = manual_review_flag
    existing.audit_reason = audit_reason
    existing.confidence = confidence
    existing.evidence_json = to_json(evidence)
    existing.source_mix_json = to_json(unique_list(source_mix))
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def upsert_dd_evidence_item(
    session: Session,
    *,
    initiative_id: int,
    source_type: str,
    source_url: str,
    snippet: str,
    quality: float,
    reliability: float,
) -> DDEvidenceItem:
    canonical_url = canonicalize_url(source_url)
    normalized_snippet = snippet.strip()[:320]
    existing = session.execute(
        select(DDEvidenceItem).where(
            DDEvidenceItem.initiative_id == initiative_id,
            DDEvidenceItem.source_type == source_type,
            DDEvidenceItem.source_url == canonical_url,
            DDEvidenceItem.snippet == normalized_snippet,
        )
    ).scalars().first()
    if existing is None:
        existing = DDEvidenceItem(
            initiative_id=initiative_id,
            source_type=source_type,
            source_url=canonical_url,
            snippet=normalized_snippet,
            quality=quality,
            reliability=reliability,
            fetched_at=utc_now(),
        )
        session.add(existing)
        session.flush()
        return existing

    existing.quality = quality
    existing.reliability = reliability
    existing.fetched_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def add_dd_claim(
    session: Session,
    *,
    initiative_id: int,
    claim_type: str,
    claim_key: str,
    claim_value: dict[str, Any],
    extractor: str,
    confidence: float,
    evidence_item_ids: list[int],
) -> DDClaim:
    row = DDClaim(
        initiative_id=initiative_id,
        claim_type=claim_type,
        claim_key=claim_key,
        claim_value_json=to_json(claim_value),
        extractor=extractor,
        confidence=confidence,
        evidence_item_ids_json=to_json(sorted({int(item) for item in evidence_item_ids})),
    )
    session.add(row)
    session.flush()
    return row


def add_dd_ai_assist(
    session: Session,
    *,
    initiative_id: int,
    dimension: str,
    component_key: str,
    model: str,
    prompt_version: str,
    ai_score: float,
    rationale: str,
    cited_claim_ids: list[int],
    confidence: float,
) -> DDAIAssist:
    row = DDAIAssist(
        initiative_id=initiative_id,
        dimension=dimension,
        component_key=component_key,
        model=model,
        prompt_version=prompt_version,
        ai_score=ai_score,
        rationale=rationale,
        cited_claim_ids_json=to_json(sorted({int(item) for item in cited_claim_ids})),
        confidence=confidence,
    )
    session.add(row)
    session.flush()
    return row


def add_dd_memo(
    session: Session,
    *,
    initiative_id: int,
    decision: str,
    check_size_band: str,
    rationale: str,
    top_risks: list[str],
    next_actions: list[str],
    recommendation: dict[str, Any],
) -> DDMemo:
    row = DDMemo(
        initiative_id=initiative_id,
        decision=decision,
        check_size_band=check_size_band,
        rationale=rationale,
        top_risks_json=to_json(unique_list(top_risks)),
        next_actions_json=to_json(unique_list(next_actions)),
        recommendation_json=to_json(recommendation),
    )
    session.add(row)
    session.flush()
    return row


def upsert_initiative_action(
    session: Session,
    *,
    initiative_id: int,
    lens: str,
    why_now: str,
    primary_contact_person_id: int | None,
    recommended_support: list[str],
    first_meeting_goal: str,
    next_30_days: list[str],
    risk_flags: list[str],
) -> InitiativeAction:
    existing = session.execute(
        select(InitiativeAction).where(
            InitiativeAction.initiative_id == initiative_id,
            InitiativeAction.lens == lens,
        )
    ).scalars().first()
    if existing is None:
        existing = InitiativeAction(
            initiative_id=initiative_id,
            lens=lens,
            why_now=why_now,
            primary_contact_person_id=primary_contact_person_id,
            recommended_support_json=to_json(unique_list(recommended_support)),
            first_meeting_goal=first_meeting_goal,
            next_30_days_json=to_json(unique_list(next_30_days)),
            risk_flags_json=to_json(unique_list(risk_flags)),
        )
        session.add(existing)
        session.flush()
        return existing

    existing.why_now = why_now
    existing.primary_contact_person_id = primary_contact_person_id
    existing.recommended_support_json = to_json(unique_list(recommended_support))
    existing.first_meeting_goal = first_meeting_goal
    existing.next_30_days_json = to_json(unique_list(next_30_days))
    existing.risk_flags_json = to_json(unique_list(risk_flags))
    existing.generated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing


def set_initiative_status(
    session: Session,
    *,
    initiative_id: int,
    status: str,
    owner: str = "",
    next_step_date: date | None = None,
    note: str = "",
) -> InitiativeStatus:
    existing = session.execute(
        select(InitiativeStatus).where(InitiativeStatus.initiative_id == initiative_id)
    ).scalars().first()
    now = datetime.now(tz=UTC)
    if existing is None:
        existing = InitiativeStatus(
            initiative_id=initiative_id,
            status=status,
            owner=owner.strip(),
            next_step_date=next_step_date,
            notes=note.strip(),
            last_contact_at=now if status in {"contacted", "discovery", "supporting"} else None,
        )
        session.add(existing)
        session.flush()
        return existing

    existing.status = status
    if owner:
        existing.owner = owner.strip()
    if note:
        existing.notes = note.strip()
    existing.next_step_date = next_step_date
    if status in {"contacted", "discovery", "supporting"}:
        existing.last_contact_at = now
    existing.updated_at = utc_now()
    session.add(existing)
    session.flush()
    return existing
