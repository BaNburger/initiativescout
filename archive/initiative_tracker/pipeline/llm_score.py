from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.llm.client import LLMClient, get_llm_client
from initiative_tracker.llm.scorer import DIMENSION_KEYS, score_dossier
from initiative_tracker.models import EvidenceDossier, LLMScore
from initiative_tracker.utils import to_json, utc_now

log = logging.getLogger(__name__)

_CONFIDENCE_FIELD_MAP = {
    "technical_substance": "confidence_technical",
    "team_capability": "confidence_team",
    "problem_market_clarity": "confidence_market",
    "traction_momentum": "confidence_traction",
    "reachability": "confidence_reachability",
    "investability_signal": "confidence_investability",
}

_SCORE_FIELD_MAP = {
    "technical_substance": "technical_substance",
    "team_capability": "team_capability",
    "problem_market_clarity": "problem_market_clarity",
    "traction_momentum": "traction_momentum",
    "reachability": "reachability",
    "investability_signal": "investability_signal",
}


def _store_llm_score(
    session: Session,
    initiative_id: int,
    result: dict[str, Any],
    *,
    dossier_hash: str,
    llm_model: str,
    prompt_version: str,
) -> LLMScore:
    dims = result["dimensions"]

    all_gaps: list[str] = []
    for key in DIMENSION_KEYS:
        all_gaps.extend(dims[key].get("data_gaps", []))

    score = LLMScore(
        initiative_id=initiative_id,
        technical_substance=dims["technical_substance"]["score"],
        team_capability=dims["team_capability"]["score"],
        problem_market_clarity=dims["problem_market_clarity"]["score"],
        traction_momentum=dims["traction_momentum"]["score"],
        reachability=dims["reachability"]["score"],
        investability_signal=dims["investability_signal"]["score"],
        confidence_technical=dims["technical_substance"]["confidence"],
        confidence_team=dims["team_capability"]["confidence"],
        confidence_market=dims["problem_market_clarity"]["confidence"],
        confidence_traction=dims["traction_momentum"]["confidence"],
        confidence_reachability=dims["reachability"]["confidence"],
        confidence_investability=dims["investability_signal"]["confidence"],
        composite_score=result["composite_score"],
        composite_confidence=result["composite_confidence"],
        classification=result["classification"],
        initiative_summary=result["initiative_summary"],
        overall_assessment=result["overall_assessment"],
        recommended_action=result["recommended_action"],
        engagement_hook=result["engagement_hook"],
        llm_model=llm_model,
        prompt_version=prompt_version,
        evidence_dossier_hash=dossier_hash,
        dimension_details_json=to_json(dims),
        data_gaps_json=to_json(all_gaps),
    )
    session.add(score)
    session.flush()
    return score


def _score_in_session(
    session: Session,
    *,
    client: LLMClient,
    llm_model: str,
    prompt_version: str,
    initiative_ids: list[int] | None = None,
    force: bool = False,
    delay_seconds: float = 0.5,
) -> int:
    query = select(EvidenceDossier)
    if initiative_ids:
        query = query.where(EvidenceDossier.initiative_id.in_(initiative_ids))
    dossiers = session.execute(query).scalars().all()

    if not dossiers:
        log.info("No evidence dossiers found. Run 'assemble-evidence' first.")
        return 0

    # Deduplicate: keep latest dossier per initiative
    latest: dict[int, EvidenceDossier] = {}
    for d in sorted(dossiers, key=lambda x: x.assembled_at):
        latest[d.initiative_id] = d
    dossiers = list(latest.values())

    if not force:
        scored_hashes = set()
        existing = session.execute(select(LLMScore)).scalars().all()
        for s in existing:
            scored_hashes.add(s.evidence_dossier_hash)
        dossiers = [d for d in dossiers if d.dossier_hash not in scored_hashes]

    if not dossiers:
        log.info("All dossiers already scored. Use --force to re-score.")
        return 0

    log.info("Scoring %d initiatives via LLM (%s)", len(dossiers), llm_model)
    count = 0
    errors = 0

    for i, dossier in enumerate(dossiers):
        try:
            result = score_dossier(dossier.dossier_text, client=client)
            _store_llm_score(
                session,
                dossier.initiative_id,
                result,
                dossier_hash=dossier.dossier_hash,
                llm_model=llm_model,
                prompt_version=prompt_version,
            )
            count += 1
            log.info(
                "[%d/%d] Scored initiative %d: %s (composite=%.2f, tier_class=%s)",
                i + 1, len(dossiers),
                dossier.initiative_id,
                result["classification"],
                result["composite_score"],
                result["recommended_action"],
            )
        except Exception:
            errors += 1
            log.exception("Failed to score initiative %d", dossier.initiative_id)

        if delay_seconds > 0 and i < len(dossiers) - 1:
            time.sleep(delay_seconds)

    session.flush()
    log.info("LLM scoring complete: %d scored, %d errors", count, errors)
    return count


def score_with_llm(
    *,
    db_url: str | None = None,
    config: dict[str, Any] | None = None,
    initiative_ids: list[int] | None = None,
    force: bool = False,
    delay_seconds: float = 0.5,
) -> int:
    config = config or {}
    init_db(db_url)
    client = get_llm_client(config)
    llm_model = config.get("model") or getattr(client, "model", "unknown")
    prompt_version = config.get("prompt_version", "v1")

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "llm_score")
        try:
            count = _score_in_session(
                session,
                client=client,
                llm_model=llm_model,
                prompt_version=prompt_version,
                initiative_ids=initiative_ids,
                force=force,
                delay_seconds=delay_seconds,
            )
            finish_pipeline_run(session, run, status="success", details={"scored": count})
            return count
        except Exception as exc:
            finish_pipeline_run(session, run, status="error", error_message=str(exc))
            raise
