from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import DDTechFact, Initiative, InitiativePerson, InitiativeSource, Person, Signal
from initiative_tracker.pipeline.collect_github import collect_github
from initiative_tracker.pipeline.dd_common import (
    classify_investability,
    DEFAULT_SOURCE_RELIABILITY,
    enrich_evidence_quality,
    extract_numeric_hints,
    has_keyword,
    make_evidence,
    stage_from_text,
)
from initiative_tracker.sources.dd_external import (
    collect_linkedin_safe_signals,
    collect_researchgate_safe_signals,
    fetch_huggingface_signals,
    fetch_openalex_signals,
    fetch_semantic_scholar_signals,
    parse_source_keys,
)
from initiative_tracker.store import (
    get_json_list,
    upsert_dd_finance_fact,
    upsert_dd_evidence_item,
    upsert_dd_legal_fact,
    upsert_dd_market_fact,
    upsert_dd_team_fact,
    upsert_dd_tech_fact,
)
from initiative_tracker.utils import clip, unique_list


def _extract_text_blob(initiative: Initiative, signals: list[Signal]) -> str:
    chunks = [initiative.description_raw or "", initiative.description_summary_en or ""]
    chunks.extend(get_json_list(initiative.team_signals_json))
    chunks.extend(signal.evidence_text or "" for signal in signals)
    return "\n".join(chunk for chunk in chunks if chunk)


def _capability_snippets(text_blob: str) -> dict[str, str]:
    lower = text_blob.casefold()
    snippets: dict[str, str] = {}
    if has_keyword(lower, ["cto", "engineering", "algorithm", "architecture", "technical lead", "developer", "research"]):
        snippets["tech"] = "Technical capability signals detected (engineering/architecture/research)."
    if has_keyword(lower, ["product", "roadmap", "user", "design partner", "problem-solution", "prototype"]):
        snippets["product"] = "Product capability signals detected (roadmap/problem-solution/user focus)."
    if has_keyword(lower, ["sales", "commercial", "pricing", "pipeline", "bizdev", "partner", "contract", "pilot"]):
        snippets["sales"] = "Sales/commercial capability signals detected (pricing/pipeline/pilot/partner)."
    return snippets


def _outcome_snippets(text_blob: str) -> dict[str, str]:
    lower = text_blob.casefold()
    out: dict[str, str] = {}
    if has_keyword(lower, ["prototype validated", "benchmark", "field test", "latency", "accuracy", "throughput"]):
        out["tech_outcome"] = "Technical outcome evidence detected (prototype/benchmark/field test)."
    if has_keyword(lower, ["interview", "loi", "pilot", "paid pilot", "customer signed", "contract"]):
        out["market_outcome"] = "Market outcome evidence detected (interviews/LOIs/pilots/contracts)."
    return out


def _evidence_reliability(source_type: str) -> float:
    return clip(float(DEFAULT_SOURCE_RELIABILITY.get(source_type.casefold(), 0.5)), 0.0, 1.0)


def collect_dd_public(
    *,
    all_initiatives: bool = True,
    sources: str | None = None,
    settings: Settings | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)
    selected_sources = parse_source_keys(sources, default_csv="github,openalex,semantic_scholar,huggingface")

    details: dict[str, Any] = {
        "processed": 0,
        "sources": sorted(selected_sources),
        "team_facts": 0,
        "market_facts": 0,
        "legal_facts": 0,
        "finance_facts": 0,
        "tech_facts_enriched": 0,
        "evidence_items_upserted": 0,
        "source_hits": {key: 0 for key in sorted(selected_sources)},
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "collect_dd_public")
        try:
            if "github" in selected_sources:
                try:
                    collect_github(
                        initiative_id=None,
                        all_initiatives=all_initiatives,
                        settings=cfg,
                        db_url=db_url,
                    )
                    details["source_hits"]["github"] += 1
                except Exception:  # noqa: BLE001
                    # Keep pipeline fail-soft; DD public collection should continue.
                    pass

            initiatives = session.execute(select(Initiative)).scalars().all()
            if not all_initiatives:
                initiatives = initiatives[:30]

            signals = session.execute(select(Signal)).scalars().all()
            sources = session.execute(select(InitiativeSource)).scalars().all()
            people = session.execute(select(Person)).scalars().all()
            links = session.execute(select(InitiativePerson)).scalars().all()
            dd_tech_rows = session.execute(select(DDTechFact)).scalars().all()

            signal_map: dict[int, list[Signal]] = defaultdict(list)
            for row in signals:
                signal_map[row.initiative_id].append(row)

            source_map: dict[int, list[InitiativeSource]] = defaultdict(list)
            for row in sources:
                source_map[row.initiative_id].append(row)

            people_by_id = {row.id: row for row in people}
            links_map: dict[int, list[InitiativePerson]] = defaultdict(list)
            for row in links:
                links_map[row.initiative_id].append(row)

            tech_map = {row.initiative_id: row for row in dd_tech_rows}

            for initiative in initiatives:
                details["processed"] += 1
                initiative_signals = signal_map.get(initiative.id, [])
                initiative_sources = source_map.get(initiative.id, [])
                link_rows = links_map.get(initiative.id, [])
                text_blob = _extract_text_blob(initiative, initiative_signals)

                source_url = initiative.primary_url or (initiative_sources[0].external_url if initiative_sources else "")
                evidence_seed = make_evidence(
                    source_type="public_signals",
                    source_url=source_url,
                    snippet=(initiative.description_summary_en or initiative.description_raw or initiative.canonical_name)[:280],
                    doc_id=f"initiative_{initiative.id}",
                )
                external_evidence: list[dict[str, Any]] = []
                openalex_metrics = {"publication_count": 0, "citation_total": 0, "recent_citations": 0, "venue_quality": 1.0}
                s2_metrics = {"paper_count": 0, "citation_total": 0, "collaboration_depth": 1.0}
                hf_metrics = {
                    "model_count": 0,
                    "like_total": 0,
                    "download_total": 0,
                    "model_card_quality": 1.0,
                    "license_quality": 1.0,
                }
                linkedin_metrics = {"profile_count": 0}
                researchgate_metrics = {"profile_count": 0}

                if "openalex" in selected_sources:
                    try:
                        openalex_result = fetch_openalex_signals(
                            initiative_name=initiative.canonical_name,
                            timeout=cfg.request_timeout_seconds,
                            user_agent=cfg.user_agent,
                        )
                        openalex_metrics.update(
                            {
                                "publication_count": int(openalex_result.get("publication_count") or 0),
                                "citation_total": int(openalex_result.get("citation_total") or 0),
                                "recent_citations": int(openalex_result.get("recent_citations") or 0),
                                "venue_quality": float(openalex_result.get("venue_quality") or 1.0),
                            }
                        )
                        external_evidence.extend([dict(item) for item in openalex_result.get("evidence", []) if isinstance(item, dict)])
                        if openalex_metrics["publication_count"] > 0:
                            details["source_hits"]["openalex"] += 1
                    except Exception:  # noqa: BLE001
                        pass

                if "semantic_scholar" in selected_sources:
                    try:
                        s2_result = fetch_semantic_scholar_signals(
                            initiative_name=initiative.canonical_name,
                            timeout=cfg.request_timeout_seconds,
                            user_agent=cfg.user_agent,
                        )
                        s2_metrics.update(
                            {
                                "paper_count": int(s2_result.get("paper_count") or 0),
                                "citation_total": int(s2_result.get("citation_total") or 0),
                                "collaboration_depth": float(s2_result.get("collaboration_depth") or 1.0),
                            }
                        )
                        external_evidence.extend([dict(item) for item in s2_result.get("evidence", []) if isinstance(item, dict)])
                        if s2_metrics["paper_count"] > 0:
                            details["source_hits"]["semantic_scholar"] += 1
                    except Exception:  # noqa: BLE001
                        pass

                if "huggingface" in selected_sources:
                    try:
                        hf_result = fetch_huggingface_signals(
                            initiative_name=initiative.canonical_name,
                            timeout=cfg.request_timeout_seconds,
                            user_agent=cfg.user_agent,
                        )
                        hf_metrics.update(
                            {
                                "model_count": int(hf_result.get("model_count") or 0),
                                "like_total": int(hf_result.get("like_total") or 0),
                                "download_total": int(hf_result.get("download_total") or 0),
                                "model_card_quality": float(hf_result.get("model_card_quality") or 1.0),
                                "license_quality": float(hf_result.get("license_quality") or 1.0),
                            }
                        )
                        external_evidence.extend([dict(item) for item in hf_result.get("evidence", []) if isinstance(item, dict)])
                        if hf_metrics["model_count"] > 0:
                            details["source_hits"]["huggingface"] += 1
                    except Exception:  # noqa: BLE001
                        pass

                if "linkedin_safe" in selected_sources:
                    linkedin_result = collect_linkedin_safe_signals(
                        initiative=initiative,
                        initiative_sources=initiative_sources,
                        people=list(people_by_id.values()),
                        links=link_rows,
                    )
                    linkedin_metrics["profile_count"] = int(linkedin_result.get("profile_count") or 0)
                    external_evidence.extend([dict(item) for item in linkedin_result.get("evidence", []) if isinstance(item, dict)])
                    if linkedin_metrics["profile_count"] > 0:
                        details["source_hits"]["linkedin_safe"] += 1

                if "researchgate_safe" in selected_sources:
                    rg_result = collect_researchgate_safe_signals(
                        initiative=initiative,
                        initiative_sources=initiative_sources,
                    )
                    researchgate_metrics["profile_count"] = int(rg_result.get("profile_count") or 0)
                    external_evidence.extend([dict(item) for item in rg_result.get("evidence", []) if isinstance(item, dict)])
                    if researchgate_metrics["profile_count"] > 0:
                        details["source_hits"]["researchgate_safe"] += 1

                # TEAM FACTS + investability segment
                roles = [link.role for link in link_rows if link.role]
                contacts = [people_by_id.get(link.person_id) for link in link_rows if people_by_id.get(link.person_id)]
                team_size_signals = [signal.value for signal in initiative_signals if signal.signal_type == "team_metric" and signal.signal_key == "team_size"]
                team_size = int(max(team_size_signals)) if team_size_signals else len(contacts)

                investability = classify_investability(
                    name=initiative.canonical_name,
                    description=text_blob,
                    categories=get_json_list(initiative.categories_json),
                    technologies=get_json_list(initiative.technologies_json),
                )
                commitment_level = clip(1.0 + min(3.0, team_size / 15.0) + (0.7 if len(contacts) >= 2 else 0.0), 1.0, 5.0)
                founder_risk_flags: list[str] = []
                if len(contacts) < 1:
                    founder_risk_flags.append("no_named_founder")
                if team_size < 3:
                    founder_risk_flags.append("small_core_team")
                if not investability["is_investable"]:
                    founder_risk_flags.append("segment_not_investable")

                team_evidence = [dict(evidence_seed)]
                team_evidence.extend(
                    [
                        dict(item)
                        for item in external_evidence
                        if str(item.get("source_type") or "") in {"linkedin_safe", "openalex", "semantic_scholar"}
                    ]
                )
                for link in link_rows[:4]:
                    person = people_by_id.get(link.person_id)
                    if not person:
                        continue
                    team_evidence.append(
                        make_evidence(
                            source_type="people",
                            source_url=link.source_url or source_url,
                            snippet=f"{person.canonical_name} role={link.role or 'member'}",
                            doc_id=f"person_{person.id}",
                        )
                    )
                capability_hints = _capability_snippets(text_blob)
                for capability, snippet in capability_hints.items():
                    team_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=snippet,
                            doc_id=f"capability_{capability}",
                        )
                    )

                references_count = len(contacts) + linkedin_metrics["profile_count"]
                if openalex_metrics["publication_count"] > 0 or s2_metrics["paper_count"] > 0:
                    references_count += 1

                upsert_dd_team_fact(
                    session,
                    initiative_id=initiative.id,
                    commitment_level=commitment_level,
                    key_roles=unique_list(roles),
                    references_count=references_count,
                    founder_risk_flags=founder_risk_flags,
                    investable_segment=str(investability["segment"]),
                    is_investable=bool(investability["is_investable"]),
                    evidence=team_evidence,
                    source_type="public_signals",
                    source_url=source_url,
                    confidence=clip(0.25 + 0.1 * len(team_evidence), 0.0, 1.0),
                )
                details["team_facts"] += 1

                # MARKET FACTS
                lois = extract_numeric_hints(text_blob, "loi") or extract_numeric_hints(text_blob, "lois")
                pilots = extract_numeric_hints(text_blob, "pilot")
                paid_pilots = extract_numeric_hints(text_blob, "paid pilot")
                interviews = extract_numeric_hints(text_blob, "interview")
                pricing_evidence = has_keyword(text_blob, ["pricing", "price", "revenue", "subscription", "license fee"])
                buyer_persona_clarity = 4.0 if has_keyword(text_blob, ["enterprise", "hospital", "oem", "municipality", "factory", "automotive"]) else 2.0
                sam_som_quality = 4.0 if has_keyword(text_blob, ["sam", "som", "tam", "market size"]) else 1.8
                if openalex_metrics["publication_count"] > 0 or s2_metrics["paper_count"] > 0:
                    buyer_persona_clarity = clip(buyer_persona_clarity + 0.5, 1.0, 5.0)
                if hf_metrics["download_total"] > 1000:
                    pricing_evidence = True

                market_evidence = [dict(evidence_seed)]
                market_evidence.extend(
                    [
                        dict(item)
                        for item in external_evidence
                        if str(item.get("source_type") or "") in {"openalex", "semantic_scholar", "huggingface"}
                    ]
                )
                if lois > 0:
                    market_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=f"LOI hints detected: {lois}",
                            doc_id="loi_hint",
                        )
                    )
                if pilots > 0:
                    market_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=f"Pilot hints detected: {pilots}",
                            doc_id="pilot_hint",
                        )
                    )
                outcome_hints = _outcome_snippets(text_blob)
                market_outcome_hint = outcome_hints.get("market_outcome")
                if market_outcome_hint:
                    market_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=market_outcome_hint,
                            doc_id="market_outcome_hint",
                        )
                    )

                upsert_dd_market_fact(
                    session,
                    initiative_id=initiative.id,
                    customer_interviews=max(interviews + int(hf_metrics["model_count"] > 0), 0),
                    lois=max(lois, 0),
                    pilots=max(pilots, 0),
                    paid_pilots=max(paid_pilots, 0),
                    pricing_evidence=pricing_evidence,
                    buyer_persona_clarity=buyer_persona_clarity,
                    sam_som_quality=sam_som_quality,
                    evidence=market_evidence,
                    source_type="public_signals",
                    source_url=source_url,
                    confidence=clip(0.2 + 0.2 * len(market_evidence), 0.0, 1.0),
                )
                details["market_facts"] += 1

                # LEGAL FACTS
                lower = text_blob.casefold()
                entity_status = "incorporated" if has_keyword(lower, ["gmbh", "ug", "inc", "ltd"]) else "unknown"
                if has_keyword(lower, ["verein", "e.v."]):
                    entity_status = "association"

                ip_ownership_status = "team_owned" if has_keyword(lower, ["ip owned", "patent filed", "proprietary"]) else "unknown"
                if has_keyword(lower, ["open source", "mit license", "apache-2"]):
                    ip_ownership_status = "open_source"

                founder_agreements = has_keyword(lower, ["founder agreement", "shareholder agreement", "vesting"])
                licensing_constraints = has_keyword(lower, ["gpl", "creative commons", "university license", "restricted license"])

                compliance_flags: list[str] = []
                if has_keyword(lower, ["medical device", "mdr", "fda", "regulatory"]):
                    compliance_flags.append("regulated_product")
                if has_keyword(lower, ["privacy", "gdpr", "data protection"]):
                    compliance_flags.append("privacy_compliance")
                if has_keyword(lower, ["export control", "dual-use"]):
                    compliance_flags.append("export_control")

                legal_risk_score = clip(1.5 + 0.9 * len(compliance_flags) + (0.7 if licensing_constraints else 0.0), 1.0, 5.0)
                legal_evidence = [dict(evidence_seed)]
                for flag in compliance_flags[:3]:
                    legal_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=f"Compliance flag detected: {flag}",
                            doc_id=flag,
                        )
                    )

                upsert_dd_legal_fact(
                    session,
                    initiative_id=initiative.id,
                    entity_status=entity_status,
                    ip_ownership_status=ip_ownership_status,
                    founder_agreements=founder_agreements,
                    licensing_constraints=licensing_constraints,
                    compliance_flags=compliance_flags,
                    legal_risk_score=legal_risk_score,
                    evidence=legal_evidence,
                    source_type="public_signals",
                    source_url=source_url,
                    confidence=clip(0.15 + 0.2 * len(legal_evidence), 0.0, 1.0),
                )
                details["legal_facts"] += 1

                # FINANCE FACTS
                runway = float(extract_numeric_hints(text_blob, "runway"))
                burn = float(extract_numeric_hints(text_blob, "burn"))
                funding_dependence = clip(3.5 if has_keyword(lower, ["seeking funding", "need funding", "fundraising"]) else 2.0, 1.0, 5.0)
                dilution_risk = clip(2.0 + (1.2 if funding_dependence >= 3.5 else 0.0) + (0.6 if runway and runway < 9 else 0.0), 1.0, 5.0)
                cap_table_summary = "unknown"
                if has_keyword(lower, ["cap table", "equity split", "shareholding"]):
                    cap_table_summary = "mentioned_publicly"

                finance_evidence = [dict(evidence_seed)]
                if runway > 0:
                    finance_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=f"Runway hint detected: {runway} months",
                            doc_id="runway_hint",
                        )
                    )

                upsert_dd_finance_fact(
                    session,
                    initiative_id=initiative.id,
                    burn_monthly=burn,
                    runway_months=runway,
                    funding_dependence=funding_dependence,
                    cap_table_summary=cap_table_summary,
                    dilution_risk=dilution_risk,
                    evidence=finance_evidence,
                    source_type="public_signals",
                    source_url=source_url,
                    confidence=clip(0.1 + 0.2 * len(finance_evidence), 0.0, 1.0),
                )
                details["finance_facts"] += 1

                # TECH FACT ENRICHMENT (patent/publication/news keyword layer)
                existing_tech = tech_map.get(initiative.id)
                tech_evidence = []
                if existing_tech:
                    raw_existing_evidence = get_json_list(existing_tech.evidence_json)
                    tech_evidence.extend([dict(item) for item in raw_existing_evidence if isinstance(item, dict)])
                tech_evidence.append(dict(evidence_seed))
                tech_evidence.extend(
                    [
                        dict(item)
                        for item in external_evidence
                        if str(item.get("source_type") or "") in {"openalex", "semantic_scholar", "huggingface"}
                    ]
                )

                publication_hit = has_keyword(lower, ["publication", "paper", "arxiv", "ieee", "nature"])
                patent_hit = has_keyword(lower, ["patent", "wipo", "epo", "ip"])
                if publication_hit:
                    tech_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet="Publication-like evidence found in public text.",
                            doc_id="publication_hint",
                        )
                    )
                if patent_hit:
                    tech_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet="Patent/IP-like evidence found in public text.",
                            doc_id="patent_hint",
                        )
                    )
                tech_outcome_hint = _outcome_snippets(text_blob).get("tech_outcome")
                if tech_outcome_hint:
                    tech_evidence.append(
                        make_evidence(
                            source_type="public_signals",
                            source_url=source_url,
                            snippet=tech_outcome_hint,
                            doc_id="tech_outcome_hint",
                        )
                    )

                ip_indicators = []
                if existing_tech:
                    ip_indicators.extend(get_json_list(existing_tech.ip_indicators_json))
                if publication_hit:
                    ip_indicators.append("publication_hint")
                if patent_hit:
                    ip_indicators.append("patent_hint")

                upsert_dd_tech_fact(
                    session,
                    initiative_id=initiative.id,
                    github_org=existing_tech.github_org if existing_tech else "",
                    github_repo=existing_tech.github_repo if existing_tech else "",
                    repo_count=existing_tech.repo_count if existing_tech else 0,
                    contributor_count=existing_tech.contributor_count if existing_tech else 0,
                    commit_velocity_90d=existing_tech.commit_velocity_90d if existing_tech else 0.0,
                    ci_present=existing_tech.ci_present if existing_tech else False,
                    test_signal=existing_tech.test_signal if existing_tech else 0.0,
                    benchmark_artifacts=(existing_tech.benchmark_artifacts if existing_tech else 0)
                    + (1 if hf_metrics["model_card_quality"] >= 3.0 else 0)
                    + (1 if has_keyword(lower, ["benchmark", "latency", "accuracy", "throughput"]) else 0),
                    prototype_stage=stage_from_text(text_blob),
                    ip_indicators=unique_list(ip_indicators),
                    evidence=tech_evidence,
                    source_type="public_signals",
                    source_url=source_url,
                    confidence=clip(0.2 + 0.1 * len(tech_evidence), 0.0, 1.0),
                )
                details["tech_facts_enriched"] += 1

                combined_evidence = [*team_evidence, *market_evidence, *legal_evidence, *finance_evidence, *tech_evidence]
                quality_enriched = enrich_evidence_quality(combined_evidence)
                seen_rows: set[tuple[str, str, str]] = set()
                for item in quality_enriched:
                    source_type = str(item.get("source_type") or "").strip()
                    item_source_url = str(item.get("source_url") or "").strip()
                    snippet = str(item.get("snippet") or "").strip()
                    if not source_type or not item_source_url or not snippet:
                        continue
                    row_key = (source_type.casefold(), item_source_url, snippet)
                    if row_key in seen_rows:
                        continue
                    seen_rows.add(row_key)
                    upsert_dd_evidence_item(
                        session,
                        initiative_id=initiative.id,
                        source_type=source_type,
                        source_url=item_source_url,
                        snippet=snippet,
                        quality=clip(float(item.get("quality") or 0.0), 0.0, 1.0),
                        reliability=_evidence_reliability(source_type),
                    )
                    details["evidence_items_upserted"] += 1

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
