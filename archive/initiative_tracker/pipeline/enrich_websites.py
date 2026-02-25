from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import Initiative, InitiativeSource
from initiative_tracker.sources.website import analyze_website
from initiative_tracker.store import add_initiative_source, add_raw_observation, add_signal, get_json_list, upsert_initiative


UNIVERSITY_DOMAINS = {"tum.de", "www.tum.de", "lmu.de", "www.lmu.de", "hm.edu", "www.hm.edu"}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except Exception:  # noqa: BLE001
        return ""


def _is_skippable(url: str, settings: Settings) -> bool:
    domain = _domain(url)
    if not domain:
        return True
    if domain in settings.social_domains_to_skip:
        return True
    return False


def _choose_enrichment_url(settings: Settings, initiative: Initiative, sources: list[InitiativeSource]) -> str:
    candidates: list[str] = []
    if initiative.primary_url:
        candidates.append(initiative.primary_url)
    for source in sources:
        if source.external_url:
            candidates.append(source.external_url)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    for candidate in deduped:
        domain = _domain(candidate)
        if _is_skippable(candidate, settings):
            continue
        if domain in UNIVERSITY_DOMAINS and len(deduped) > 1:
            continue
        return candidate
    return ""


def enrich_websites(settings: Settings | None = None, db_url: str | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    technology_taxonomy = cfg.load_technology_taxonomy()
    market_taxonomy = cfg.load_market_taxonomy()

    details: dict[str, Any] = {
        "initiatives_seen": 0,
        "attempted": 0,
        "successful": 0,
        "failed": 0,
        "signals_written": 0,
        "skipped": 0,
        "errors": [],
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "enrich_websites")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            details["initiatives_seen"] = len(initiatives)
            all_sources = session.execute(select(InitiativeSource)).scalars().all()
            source_map: dict[int, list[InitiativeSource]] = {}
            for source in all_sources:
                source_map.setdefault(source.initiative_id, []).append(source)

            for initiative in initiatives:
                sources = source_map.get(initiative.id, [])
                website_url = _choose_enrichment_url(cfg, initiative, sources)
                if not website_url:
                    details["skipped"] += 1
                    continue

                details["attempted"] += 1
                try:
                    enrichment = analyze_website(
                        website_url,
                        technology_taxonomy=technology_taxonomy,
                        market_taxonomy=market_taxonomy,
                        settings=cfg,
                    )

                    summary = enrichment.get("summary_en", "")
                    summary = summary.replace("Initiative", initiative.canonical_name, 1)

                    updated_technologies = [*get_json_list(initiative.technologies_json), *enrichment.get("technologies", [])]
                    updated_markets = [*get_json_list(initiative.markets_json), *enrichment.get("markets", [])]
                    team_signals = [
                        *get_json_list(initiative.team_signals_json),
                        f"leadership_mentions:{int(enrichment['team_counts'].get('leadership_mentions', 0))}",
                        f"achievement_mentions:{int(enrichment['team_counts'].get('achievement_mentions', 0))}",
                    ]

                    upsert_initiative(
                        session,
                        name=initiative.canonical_name,
                        university=initiative.university,
                        primary_url=website_url,
                        description_raw=initiative.description_raw or enrichment.get("meta_description") or "",
                        description_summary_en=summary,
                        technologies=updated_technologies,
                        markets=updated_markets,
                        team_signals=team_signals,
                        confidence=0.8,
                    )

                    obs_payload = {
                        "title": enrichment.get("title"),
                        "meta_description": enrichment.get("meta_description"),
                        "technology_matches": enrichment.get("technology_matches"),
                        "market_matches": enrichment.get("market_matches"),
                        "team_counts": enrichment.get("team_counts"),
                        "market_counts": enrichment.get("market_counts"),
                        "team_size": enrichment.get("team_size"),
                        "summary_en": summary,
                    }

                    add_initiative_source(
                        session,
                        initiative_id=initiative.id,
                        source_type="website_enrichment",
                        source_name="initiative_website",
                        source_url=website_url,
                        external_url=website_url,
                        payload=obs_payload,
                    )
                    add_raw_observation(
                        session,
                        initiative_id=initiative.id,
                        source_type="website_enrichment",
                        source_name="initiative_website",
                        source_url=website_url,
                        payload=obs_payload,
                    )

                    for domain, count in enrichment.get("technology_matches", {}).items():
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="technology_domain",
                            signal_key=domain,
                            value=float(count),
                            evidence_text=enrichment.get("meta_description") or enrichment.get("title") or "",
                            source_type="website_enrichment",
                            source_url=website_url,
                        )
                        details["signals_written"] += 1

                    for domain, count in enrichment.get("market_matches", {}).items():
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="market_domain",
                            signal_key=domain,
                            value=float(count),
                            evidence_text=enrichment.get("meta_description") or enrichment.get("title") or "",
                            source_type="website_enrichment",
                            source_url=website_url,
                        )
                        details["signals_written"] += 1

                    for key, value in enrichment.get("team_counts", {}).items():
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="team_metric",
                            signal_key=key,
                            value=float(value),
                            evidence_text=enrichment.get("title") or "",
                            source_type="website_enrichment",
                            source_url=website_url,
                        )
                        details["signals_written"] += 1

                    for key, value in enrichment.get("market_counts", {}).items():
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="market_metric",
                            signal_key=key,
                            value=float(value),
                            evidence_text=enrichment.get("title") or "",
                            source_type="website_enrichment",
                            source_url=website_url,
                        )
                        details["signals_written"] += 1

                    team_size = enrichment.get("team_size")
                    if team_size:
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="team_metric",
                            signal_key="team_size",
                            value=float(team_size),
                            evidence_text="website team size",
                            source_type="website_enrichment",
                            source_url=website_url,
                        )
                        details["signals_written"] += 1

                    infrastructure_quality = 1.0
                    if enrichment.get("title"):
                        infrastructure_quality += 1.0
                    if enrichment.get("meta_description"):
                        infrastructure_quality += 1.0
                    if len(enrichment.get("combined_text", "")) > 1500:
                        infrastructure_quality += 1.0

                    add_signal(
                        session,
                        initiative_id=initiative.id,
                        signal_type="maturity_metric",
                        signal_key="infrastructure_quality",
                        value=float(infrastructure_quality),
                        evidence_text="website completeness proxy",
                        source_type="website_enrichment",
                        source_url=website_url,
                    )
                    details["signals_written"] += 1
                    details["successful"] += 1
                except Exception as exc:  # noqa: BLE001
                    details["failed"] += 1
                    details["errors"].append({"initiative_id": initiative.id, "name": initiative.canonical_name, "error": str(exc)})

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
