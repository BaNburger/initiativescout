from __future__ import annotations

from collections import defaultdict
from typing import Any

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.sources.hm import fetch_hm_directory
from initiative_tracker.sources.lmu import fetch_lmu_directory
from initiative_tracker.sources.tum import fetch_tum_directory
from initiative_tracker.store import add_initiative_source, add_raw_observation, add_signal, upsert_initiative
from initiative_tracker.types import SourceInitiative


def _source_batches(settings: Settings) -> dict[str, list[SourceInitiative]]:
    return {
        "tum": fetch_tum_directory(settings),
        "lmu": fetch_lmu_directory(settings),
        "hm": fetch_hm_directory(settings),
    }


def _category_market_hints(categories: list[str]) -> list[str]:
    hints: list[str] = []
    for category in categories:
        lower = category.casefold()
        if "entrepreneur" in lower or "career" in lower:
            hints.append("career_and_venture")
        if "technology" in lower or "research" in lower:
            hints.append("deep_tech")
        if "sustainability" in lower or "health" in lower:
            hints.append("sustainability_and_health")
    return sorted(set(hints))


def scrape_directories(settings: Settings | None = None, db_url: str | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "sources": defaultdict(int),
        "records_processed": 0,
        "records_upserted": 0,
        "signals_written": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "scrape_directories")
        try:
            source_batches = _source_batches(cfg)
            for source_key, initiatives in source_batches.items():
                details["sources"][source_key] = len(initiatives)
                for item in initiatives:
                    initiative = upsert_initiative(
                        session,
                        name=item.name,
                        university=item.university,
                        primary_url=item.external_url,
                        description_raw=item.description_raw,
                        categories=item.categories,
                        technologies=item.technologies,
                        markets=item.markets,
                        team_signals=item.team_signals,
                        confidence=0.75,
                    )

                    payload = item.model_dump()
                    add_initiative_source(
                        session,
                        initiative_id=initiative.id,
                        source_type="directory_scrape",
                        source_name=item.source_name,
                        source_url=item.source_url,
                        external_url=item.external_url,
                        payload=payload,
                    )
                    add_raw_observation(
                        session,
                        initiative_id=initiative.id,
                        source_type="directory_scrape",
                        source_name=item.source_name,
                        source_url=item.source_url,
                        payload=payload,
                    )

                    for category in item.categories:
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="category",
                            signal_key=category,
                            value=1.0,
                            evidence_text=item.description_raw or "",
                            source_type="directory_scrape",
                            source_url=item.source_url,
                        )
                        details["signals_written"] += 1

                    for hint in _category_market_hints(item.categories):
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="market_domain",
                            signal_key=hint,
                            value=0.8,
                            evidence_text="category hint",
                            source_type="directory_scrape",
                            source_url=item.source_url,
                        )
                        details["signals_written"] += 1

                    details["records_processed"] += 1
                    details["records_upserted"] += 1

            details["sources"] = dict(details["sources"])
            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            details["sources"] = dict(details["sources"])
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
