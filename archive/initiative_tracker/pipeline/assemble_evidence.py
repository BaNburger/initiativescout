from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import (
    DDEvidenceItem,
    DDMarketFact,
    DDTeamFact,
    DDTechFact,
    EvidenceDossier,
    Initiative,
    InitiativePerson,
    InitiativeSource,
    Person,
    RawObservation,
    Signal,
)
from initiative_tracker.utils import from_json, text_hash, to_json, utc_now

log = logging.getLogger(__name__)

_MAX_OBSERVATION_CHARS = 3000
_MAX_SIGNAL_ITEMS = 30
_MAX_DD_EVIDENCE_ITEMS = 20


def _format_observation_payload(payload_json: str) -> str:
    payload = from_json(payload_json, {})
    if isinstance(payload, dict):
        parts: list[str] = []
        for key in ("title", "meta_description", "headings", "body_text"):
            val = payload.get(key, "")
            if val:
                parts.append(f"{key}: {val}")
        text = "\n".join(parts) if parts else to_json(payload)
    elif isinstance(payload, str):
        text = payload
    else:
        text = to_json(payload)
    return text[:_MAX_OBSERVATION_CHARS]


def _build_dossier_text(
    initiative: Initiative,
    sources: list[InitiativeSource],
    observations: list[RawObservation],
    signals: list[Signal],
    people: list[dict[str, Any]],
    tech_facts: list[DDTechFact],
    team_facts: list[DDTeamFact],
    market_facts: list[DDMarketFact],
    dd_evidence: list[DDEvidenceItem],
) -> str:
    sections: list[str] = []

    sections.append(f"INITIATIVE: {initiative.canonical_name}")
    sections.append(f"University: {initiative.university or 'Unknown'}")
    sections.append(f"URL: {initiative.primary_url or 'None'}")
    sections.append("")

    if initiative.description_raw or initiative.description_summary_en:
        sections.append("=== DESCRIPTION ===")
        if initiative.description_summary_en:
            sections.append(initiative.description_summary_en)
        if initiative.description_raw and initiative.description_raw != initiative.description_summary_en:
            sections.append(initiative.description_raw[:2000])
        sections.append("")

    website_obs = [o for o in observations if o.source_type == "website_enrichment"]
    if website_obs:
        sections.append("=== WEBSITE CONTENT ===")
        for obs in website_obs[:3]:
            sections.append(_format_observation_payload(obs.payload_json))
        sections.append("")

    tech_signals = [s for s in signals if s.signal_type in ("technology_domain", "technology_keyword_density")]
    market_signals = [s for s in signals if s.signal_type in ("market_domain", "market_metric")]
    team_signals = [s for s in signals if s.signal_type in ("team_metric", "seed_rating")]
    other_signals = [s for s in signals if s.signal_type not in (
        "technology_domain", "technology_keyword_density", "market_domain",
        "market_metric", "team_metric", "seed_rating",
    )]

    if tech_signals:
        sections.append("=== TECHNOLOGY SIGNALS ===")
        for s in tech_signals[:_MAX_SIGNAL_ITEMS]:
            line = f"- {s.signal_key}: {s.value}"
            if s.evidence_text:
                line += f" | {s.evidence_text[:200]}"
            sections.append(line)
        sections.append("")

    if market_signals:
        sections.append("=== MARKET SIGNALS ===")
        for s in market_signals[:_MAX_SIGNAL_ITEMS]:
            line = f"- {s.signal_key}: {s.value}"
            if s.evidence_text:
                line += f" | {s.evidence_text[:200]}"
            sections.append(line)
        sections.append("")

    if people:
        sections.append("=== TEAM ===")
        sections.append(f"Named people: {len(people)}")
        for p in people[:15]:
            line = f"- {p['name']} ({p['role']})"
            if p.get("person_type"):
                line += f" [{p['person_type']}]"
            if p.get("headline"):
                line += f" — {p['headline']}"
            sections.append(line)
        sections.append("")

    if team_signals:
        sections.append("=== TEAM SIGNALS ===")
        for s in team_signals[:_MAX_SIGNAL_ITEMS]:
            line = f"- {s.signal_key}: {s.value}"
            if s.evidence_text:
                line += f" | {s.evidence_text[:200]}"
            sections.append(line)
        sections.append("")

    team_signal_list = from_json(initiative.team_signals_json, [])
    if team_signal_list:
        sections.append("=== TEAM SIGNAL TAGS ===")
        sections.append(", ".join(str(t) for t in team_signal_list[:20]))
        sections.append("")

    if tech_facts:
        sections.append("=== GITHUB DATA ===")
        for tf in tech_facts:
            sections.append(f"Org: {tf.github_org}, Repo: {tf.github_repo}")
            sections.append(f"Repos: {tf.repo_count}, Contributors: {tf.contributor_count}")
            sections.append(f"Commit velocity (90d): {tf.commit_velocity_90d}")
            sections.append(f"CI present: {tf.ci_present}, Test signal: {tf.test_signal}")
            sections.append(f"Benchmark artifacts: {tf.benchmark_artifacts}")
            sections.append(f"Prototype stage: {tf.prototype_stage}")
            ip_indicators = from_json(tf.ip_indicators_json, [])
            if ip_indicators:
                sections.append(f"IP indicators: {', '.join(str(i) for i in ip_indicators)}")
        sections.append("")

    if team_facts:
        sections.append("=== DD TEAM FACTS ===")
        for fact in team_facts:
            sections.append(f"Commitment level: {fact.commitment_level}")
            roles = from_json(fact.key_roles_json, [])
            if roles:
                sections.append(f"Key roles: {', '.join(str(r) for r in roles)}")
            sections.append(f"References count: {fact.references_count}")
            sections.append(f"Investable segment: {fact.investable_segment}")
            sections.append(f"Is investable: {fact.is_investable}")
            risk_flags = from_json(fact.founder_risk_flags_json, [])
            if risk_flags:
                sections.append(f"Risk flags: {', '.join(str(r) for r in risk_flags)}")
        sections.append("")

    if market_facts:
        sections.append("=== DD MARKET FACTS ===")
        for fact in market_facts:
            sections.append(f"Customer interviews: {fact.customer_interviews}")
            sections.append(f"LOIs: {fact.lois}, Pilots: {fact.pilots}, Paid pilots: {fact.paid_pilots}")
            sections.append(f"Pricing evidence: {fact.pricing_evidence}")
            sections.append(f"Buyer persona clarity: {fact.buyer_persona_clarity}")
        sections.append("")

    if dd_evidence:
        sections.append("=== ADDITIONAL EVIDENCE ===")
        for item in dd_evidence[:_MAX_DD_EVIDENCE_ITEMS]:
            sections.append(f"[{item.source_type}] {item.snippet[:300]}")
        sections.append("")

    if other_signals:
        sections.append("=== OTHER SIGNALS ===")
        for s in other_signals[:_MAX_SIGNAL_ITEMS]:
            line = f"- {s.signal_type}/{s.signal_key}: {s.value}"
            if s.evidence_text:
                line += f" | {s.evidence_text[:200]}"
            sections.append(line)
        sections.append("")

    source_types = sorted({s.source_type for s in sources})
    if source_types:
        sections.append(f"=== SOURCES: {', '.join(source_types)} ===")

    technologies = from_json(initiative.technologies_json, [])
    markets = from_json(initiative.markets_json, [])
    if technologies:
        sections.append(f"Technologies: {', '.join(str(t) for t in technologies)}")
    if markets:
        sections.append(f"Markets: {', '.join(str(m) for m in markets)}")

    return "\n".join(sections)


def _assemble_in_session(
    session: Session,
    *,
    initiative_ids: list[int] | None = None,
    force: bool = False,
) -> int:
    query = select(Initiative)
    if initiative_ids:
        query = query.where(Initiative.id.in_(initiative_ids))
    initiatives = session.execute(query).scalars().all()

    if not initiatives:
        log.info("No initiatives to assemble dossiers for")
        return 0

    all_ids = [i.id for i in initiatives]

    sources_q = session.execute(
        select(InitiativeSource).where(InitiativeSource.initiative_id.in_(all_ids))
    ).scalars().all()
    observations_q = session.execute(
        select(RawObservation).where(RawObservation.initiative_id.in_(all_ids))
    ).scalars().all()
    signals_q = session.execute(
        select(Signal).where(Signal.initiative_id.in_(all_ids))
    ).scalars().all()
    links_q = session.execute(
        select(InitiativePerson).where(InitiativePerson.initiative_id.in_(all_ids))
    ).scalars().all()
    person_ids = {link.person_id for link in links_q}
    people_q = session.execute(
        select(Person).where(Person.id.in_(person_ids))
    ).scalars().all() if person_ids else []
    tech_facts_q = session.execute(
        select(DDTechFact).where(DDTechFact.initiative_id.in_(all_ids))
    ).scalars().all()
    team_facts_q = session.execute(
        select(DDTeamFact).where(DDTeamFact.initiative_id.in_(all_ids))
    ).scalars().all()
    market_facts_q = session.execute(
        select(DDMarketFact).where(DDMarketFact.initiative_id.in_(all_ids))
    ).scalars().all()
    dd_evidence_q = session.execute(
        select(DDEvidenceItem).where(DDEvidenceItem.initiative_id.in_(all_ids))
    ).scalars().all()

    sources_by_init: dict[int, list[InitiativeSource]] = defaultdict(list)
    for s in sources_q:
        sources_by_init[s.initiative_id].append(s)
    obs_by_init: dict[int, list[RawObservation]] = defaultdict(list)
    for o in observations_q:
        obs_by_init[o.initiative_id].append(o)
    signals_by_init: dict[int, list[Signal]] = defaultdict(list)
    for s in signals_q:
        signals_by_init[s.initiative_id].append(s)
    links_by_init: dict[int, list[InitiativePerson]] = defaultdict(list)
    for link in links_q:
        links_by_init[link.initiative_id].append(link)
    person_by_id = {p.id: p for p in people_q}
    tech_facts_by_init: dict[int, list[DDTechFact]] = defaultdict(list)
    for tf in tech_facts_q:
        tech_facts_by_init[tf.initiative_id].append(tf)
    team_facts_by_init: dict[int, list[DDTeamFact]] = defaultdict(list)
    for tf in team_facts_q:
        team_facts_by_init[tf.initiative_id].append(tf)
    market_facts_by_init: dict[int, list[DDMarketFact]] = defaultdict(list)
    for mf in market_facts_q:
        market_facts_by_init[mf.initiative_id].append(mf)
    dd_evidence_by_init: dict[int, list[DDEvidenceItem]] = defaultdict(list)
    for ev in dd_evidence_q:
        dd_evidence_by_init[ev.initiative_id].append(ev)

    existing_dossiers = session.execute(
        select(EvidenceDossier).where(EvidenceDossier.initiative_id.in_(all_ids))
    ).scalars().all()
    existing_by_init = {d.initiative_id: d for d in existing_dossiers}

    count = 0
    for initiative in initiatives:
        iid = initiative.id

        people_list: list[dict[str, Any]] = []
        for link in sorted(links_by_init.get(iid, []), key=lambda r: (not r.is_primary_contact, r.role)):
            person = person_by_id.get(link.person_id)
            if person:
                people_list.append({
                    "name": person.canonical_name,
                    "role": link.role,
                    "person_type": person.person_type,
                    "headline": person.headline,
                })

        dossier_text = _build_dossier_text(
            initiative=initiative,
            sources=sources_by_init.get(iid, []),
            observations=obs_by_init.get(iid, []),
            signals=signals_by_init.get(iid, []),
            people=people_list,
            tech_facts=tech_facts_by_init.get(iid, []),
            team_facts=team_facts_by_init.get(iid, []),
            market_facts=market_facts_by_init.get(iid, []),
            dd_evidence=dd_evidence_by_init.get(iid, []),
        )

        dossier_h = text_hash(dossier_text)
        existing = existing_by_init.get(iid)

        if existing and existing.dossier_hash == dossier_h and not force:
            continue

        if existing:
            existing.dossier_text = dossier_text
            existing.dossier_hash = dossier_h
            existing.assembled_at = utc_now()
        else:
            session.add(EvidenceDossier(
                initiative_id=iid,
                dossier_text=dossier_text,
                dossier_hash=dossier_h,
            ))
        count += 1

    session.flush()
    log.info("Assembled %d evidence dossiers (of %d initiatives)", count, len(initiatives))
    return count


def assemble_evidence(
    *,
    db_url: str | None = None,
    initiative_ids: list[int] | None = None,
    force: bool = False,
) -> int:
    init_db(db_url)
    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "assemble_evidence")
        try:
            count = _assemble_in_session(session, initiative_ids=initiative_ids, force=force)
            finish_pipeline_run(session, run, status="success", details={"assembled": count})
            return count
        except Exception as exc:
            finish_pipeline_run(session, run, status="error", error_message=str(exc))
            raise
