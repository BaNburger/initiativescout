from __future__ import annotations

import logging
from typing import Any

from initiative_tracker.llm.client import LLMClient, get_llm_client
from initiative_tracker.llm.prompts import SCORING_SYSTEM_PROMPT, build_scoring_prompt
from initiative_tracker.utils import clip

log = logging.getLogger(__name__)

DIMENSION_KEYS = [
    "technical_substance",
    "team_capability",
    "problem_market_clarity",
    "traction_momentum",
    "reachability",
    "investability_signal",
]

VALID_CLASSIFICATIONS = {
    "deep_tech_team",
    "applied_research",
    "student_venture",
    "student_club",
    "dormant",
    "unclear",
}

VALID_ACTIONS = {
    "engage_now",
    "monitor_closely",
    "monitor_quarterly",
    "archive",
}

DIMENSION_WEIGHTS = {
    "technical_substance": 0.25,
    "team_capability": 0.25,
    "problem_market_clarity": 0.20,
    "traction_momentum": 0.15,
    "reachability": 0.10,
    "investability_signal": 0.05,
}


def _validate_dimension(dim: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": clip(float(dim.get("score", 1.0)), 1.0, 5.0),
        "confidence": clip(float(dim.get("confidence", 0.0)), 0.0, 1.0),
        "reasoning": str(dim.get("reasoning", "")),
        "key_evidence": dim.get("key_evidence", []),
        "data_gaps": dim.get("data_gaps", []),
    }


def _validate_response(raw: dict[str, Any]) -> dict[str, Any]:
    dimensions = raw.get("dimensions", {})
    validated_dims: dict[str, Any] = {}
    for key in DIMENSION_KEYS:
        dim_data = dimensions.get(key, {})
        validated_dims[key] = _validate_dimension(dim_data)

    classification = raw.get("classification", "unclear")
    if classification not in VALID_CLASSIFICATIONS:
        classification = "unclear"

    action = raw.get("recommended_action", "monitor_quarterly")
    if action not in VALID_ACTIONS:
        action = "monitor_quarterly"

    composite = 0.0
    composite_conf = 0.0
    total_weight = 0.0
    for key, weight in DIMENSION_WEIGHTS.items():
        dim = validated_dims[key]
        composite += weight * dim["score"] * dim["confidence"]
        composite_conf += weight * dim["confidence"]
        total_weight += weight

    return {
        "initiative_summary": str(raw.get("initiative_summary", "")),
        "classification": classification,
        "dimensions": validated_dims,
        "overall_assessment": str(raw.get("overall_assessment", "")),
        "recommended_action": action,
        "engagement_hook": str(raw.get("engagement_hook", "")),
        "composite_score": round(composite, 4),
        "composite_confidence": round(composite_conf / total_weight if total_weight else 0.0, 4),
    }


def score_dossier(
    dossier_text: str,
    *,
    client: LLMClient | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if client is None:
        client = get_llm_client(config)

    user_prompt = build_scoring_prompt(dossier_text)

    raw = client.score_dossier(SCORING_SYSTEM_PROMPT, user_prompt)
    return _validate_response(raw)
