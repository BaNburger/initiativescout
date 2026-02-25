from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.llm.scorer import DIMENSION_KEYS, DIMENSION_WEIGHTS
from initiative_tracker.models import Initiative, InitiativeTier, LLMScore
from initiative_tracker.utils import to_json, utc_now

log = logging.getLogger(__name__)

TIER_THRESHOLDS = {
    "S": {"min_composite": 3.8, "min_confidence": 0.5},
    "A": {"min_composite": 3.2, "min_confidence": 0.35},
    "B": {"min_composite": 2.5, "min_confidence": 0.25},
    "C": {"min_composite": 0.0, "min_confidence": 0.0},
}

# Classifications that cap the tier at B (soft gate)
TIER_CAPPED_CLASSIFICATIONS = {"student_club", "dormant"}

_SCORE_ATTRS = {
    "technical_substance": "technical_substance",
    "team_capability": "team_capability",
    "problem_market_clarity": "problem_market_clarity",
    "traction_momentum": "traction_momentum",
    "reachability": "reachability",
    "investability_signal": "investability_signal",
}

_CONFIDENCE_ATTRS = {
    "technical_substance": "confidence_technical",
    "team_capability": "confidence_team",
    "problem_market_clarity": "confidence_market",
    "traction_momentum": "confidence_traction",
    "reachability": "confidence_reachability",
    "investability_signal": "confidence_investability",
}


def _percentile_rank(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return round((below + 0.5 * equal) / len(values) * 100.0, 1)


def _assign_tier(
    composite: float,
    confidence: float,
    classification: str,
    low_confidence_dims: int,
) -> tuple[str, str]:
    if low_confidence_dims >= 4:
        return "X", "Insufficient data: low confidence on 4+ dimensions"

    if classification in TIER_CAPPED_CLASSIFICATIONS:
        cap = "B"
    else:
        cap = None

    for tier in ("S", "A", "B", "C"):
        threshold = TIER_THRESHOLDS[tier]
        if composite >= threshold["min_composite"] and confidence >= threshold["min_confidence"]:
            if cap and tier < cap:  # tier S < B alphabetically means S is better
                return cap, f"Capped at {cap} due to {classification} classification"
            rationale = f"Composite {composite:.2f} >= {threshold['min_composite']}, confidence {confidence:.2f} >= {threshold['min_confidence']}"
            if tier == "S":
                rationale += f" — classification: {classification}"
            return tier, rationale

    return "C", f"Composite {composite:.2f} below all thresholds"


def _rank_in_session(session: Session) -> int:
    scores = session.execute(select(LLMScore)).scalars().all()
    if not scores:
        log.info("No LLM scores found. Run 'llm-score' first.")
        return 0

    # Keep latest score per initiative
    latest_by_init: dict[int, LLMScore] = {}
    for s in sorted(scores, key=lambda x: x.scored_at):
        latest_by_init[s.initiative_id] = s
    active_scores = list(latest_by_init.values())

    initiatives = session.execute(
        select(Initiative).where(Initiative.id.in_([s.initiative_id for s in active_scores]))
    ).scalars().all()
    init_by_id = {i.id: i for i in initiatives}

    # Compute pool-wide score distributions per dimension
    dim_values: dict[str, list[float]] = defaultdict(list)
    composite_values: list[float] = []

    for s in active_scores:
        for dim_key, attr in _SCORE_ATTRS.items():
            dim_values[dim_key].append(getattr(s, attr))
        composite_values.append(s.composite_score)

    # Look up previous tiers for delta tracking
    existing_tiers = session.execute(select(InitiativeTier)).scalars().all()
    prev_tier_by_init: dict[int, str] = {}
    for t in existing_tiers:
        prev_tier_by_init[t.initiative_id] = t.tier

    # Delete old tiers (we recompute fully)
    for t in existing_tiers:
        session.delete(t)
    session.flush()

    # Compute per-university/classification stats
    uni_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    class_counts: dict[str, int] = defaultdict(int)

    count = 0
    new_tiers: list[InitiativeTier] = []

    for s in active_scores:
        initiative = init_by_id.get(s.initiative_id)
        if not initiative:
            continue

        # Percentiles
        dim_percentiles: dict[str, float] = {}
        for dim_key, attr in _SCORE_ATTRS.items():
            val = getattr(s, attr)
            dim_percentiles[dim_key] = _percentile_rank(dim_values[dim_key], val)
        composite_pctl = _percentile_rank(composite_values, s.composite_score)

        # Count low-confidence dimensions
        low_conf_count = 0
        for dim_key, attr in _CONFIDENCE_ATTRS.items():
            if getattr(s, attr) < 0.10:
                low_conf_count += 1

        tier, rationale = _assign_tier(
            s.composite_score,
            s.composite_confidence,
            s.classification,
            low_conf_count,
        )

        # Delta tracking
        prev = prev_tier_by_init.get(s.initiative_id)
        if prev is None:
            change = "new"
            change_reason = "First scoring"
        elif prev == tier:
            change = "stable"
            change_reason = ""
        elif tier < prev:  # S < A < B < C alphabetically — lower letter = better tier
            change = "upgraded"
            change_reason = f"Improved from {prev} to {tier}"
        else:
            change = "downgraded"
            change_reason = f"Declined from {prev} to {tier}"

        tier_obj = InitiativeTier(
            initiative_id=s.initiative_id,
            llm_score_id=s.id,
            tier=tier,
            tier_rationale=rationale,
            composite_percentile=composite_pctl,
            dimension_percentiles_json=to_json(dim_percentiles),
            previous_tier=prev,
            tier_change=change,
            tier_change_reason=change_reason,
            cohort_stats_json="{}",
        )
        new_tiers.append(tier_obj)
        session.add(tier_obj)

        uni = initiative.university or "Unknown"
        uni_counts[uni][tier] = uni_counts[uni].get(tier, 0) + 1
        class_counts[s.classification] += 1
        count += 1

    session.flush()

    # Update cohort stats on each tier
    cohort = {
        "pool_size": len(active_scores),
        "tier_distribution": dict(sorted(defaultdict(int, {t.tier: 0 for t in new_tiers}).items())),
        "university_breakdown": {uni: dict(counts) for uni, counts in uni_counts.items()},
        "classification_distribution": dict(class_counts),
    }
    # Count actual tier distribution
    tier_dist: dict[str, int] = defaultdict(int)
    for t in new_tiers:
        tier_dist[t.tier] += 1
    cohort["tier_distribution"] = dict(tier_dist)

    cohort_json = to_json(cohort)
    for t in new_tiers:
        t.cohort_stats_json = cohort_json

    session.flush()
    log.info(
        "Ranked %d initiatives. Tiers: %s",
        count,
        ", ".join(f"{k}={v}" for k, v in sorted(tier_dist.items())),
    )
    return count


def comparative_rank(*, db_url: str | None = None) -> int:
    init_db(db_url)
    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "comparative_rank")
        try:
            count = _rank_in_session(session)
            finish_pipeline_run(session, run, status="success", details={"ranked": count})
            return count
        except Exception as exc:
            finish_pipeline_run(session, run, status="error", error_message=str(exc))
            raise
