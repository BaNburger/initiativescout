from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from initiative_tracker.utils import clip

BOILERPLATE_TERMS = {
    "about us",
    "privacy policy",
    "terms of use",
    "cookie policy",
    "all rights reserved",
    "impressum",
    "legal notice",
    "contact us",
    "newsletter",
    "follow us",
}

NON_INVESTABLE_TERMS = {
    "chapter",
    "club",
    "association",
    "network",
    "society",
    "committee",
    "sports",
    "choir",
    "debate",
    "consulting",
    "festival",
}

INVESTABLE_TERMS = {
    "ai",
    "autonomous",
    "robot",
    "rocket",
    "aerospace",
    "drone",
    "biotech",
    "medtech",
    "quantum",
    "prototype",
    "engineering",
    "hardware",
    "software",
    "satellite",
    "battery",
    "fusion",
    "spinout",
    "startup",
}

DEFAULT_SOURCE_RELIABILITY = {
    "manual_dd": 1.0,
    "github_api": 0.9,
    "people_markdown": 0.8,
    "public_signals": 0.6,
    "website_enrichment": 0.5,
    "seed_markdown": 0.4,
}

DEFAULT_QUALITY_WEIGHTS = {
    "specificity": 0.50,
    "recency": 0.20,
    "source_reliability": 0.20,
    "independence": 0.10,
}


def _specificity_score(snippet: str) -> float:
    text = (snippet or "").strip().casefold()
    if not text:
        return 0.0

    score = 0.35
    if len(text) > 40:
        score += 0.15
    if len(text) > 120:
        score += 0.15
    if re.search(r"\b\d{1,4}\b", text):
        score += 0.1

    hard_tokens = [
        "benchmark",
        "latency",
        "accuracy",
        "prototype",
        "field test",
        "pilot",
        "paid",
        "loi",
        "contract",
        "customer",
        "founded",
        "cto",
        "product lead",
        "sales lead",
        "ci",
        "test",
    ]
    if any(token in text for token in hard_tokens):
        score += 0.25

    if any(term in text for term in BOILERPLATE_TERMS):
        score -= 0.35

    return clip(score, 0.0, 1.0)


def _recency_score(snippet: str) -> float:
    text = (snippet or "").strip().casefold()
    if not text:
        return 0.0

    if any(token in text for token in ["today", "this year", "current", "recent", "latest", "2026", "2025"]):
        return 0.9

    current_year = datetime.now(tz=UTC).year
    years = [int(match.group(0)) for match in re.finditer(r"\b20\d{2}\b", text)]
    if years:
        newest = max(years)
        delta = max(0, current_year - newest)
        if delta <= 1:
            return 0.9
        if delta <= 2:
            return 0.75
        if delta <= 4:
            return 0.55
        return 0.35

    return 0.5


def _source_reliability(source_type: str, reliability: dict[str, float] | None = None) -> float:
    table = reliability or DEFAULT_SOURCE_RELIABILITY
    return clip(float(table.get((source_type or "").strip().casefold(), 0.5)), 0.0, 1.0)


def score_evidence_quality(
    evidence: dict[str, Any],
    *,
    source_reliability_map: dict[str, float] | None = None,
    quality_weights: dict[str, float] | None = None,
    independence: float = 0.5,
) -> float:
    weights = quality_weights or DEFAULT_QUALITY_WEIGHTS
    snippet = str(evidence.get("snippet") or "")
    source_type = str(evidence.get("source_type") or "")

    specificity = _specificity_score(snippet)
    recency = _recency_score(snippet)
    source_rel = _source_reliability(source_type, source_reliability_map)
    independence_score = clip(independence, 0.0, 1.0)

    value = (
        float(weights.get("specificity", 0.5)) * specificity
        + float(weights.get("recency", 0.2)) * recency
        + float(weights.get("source_reliability", 0.2)) * source_rel
        + float(weights.get("independence", 0.1)) * independence_score
    )
    return clip(value, 0.0, 1.0)


def enrich_evidence_quality(
    evidences: list[dict[str, Any]],
    *,
    source_reliability_map: dict[str, float] | None = None,
    quality_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    if not evidences:
        return []

    source_types = [str(item.get("source_type") or "") for item in evidences if str(item.get("source_type") or "")]
    unique_sources = set(source_types)
    enriched: list[dict[str, Any]] = []

    for item in evidences:
        copied = dict(item)
        source_type = str(item.get("source_type") or "")
        if not source_type:
            independence = 0.35
        elif len(unique_sources) <= 1:
            independence = 0.35
        else:
            # Higher independence when source class is not dominant.
            same_type = source_types.count(source_type)
            independence = clip(1.0 - (same_type / max(1, len(source_types))), 0.35, 1.0)

        quality = score_evidence_quality(
            copied,
            source_reliability_map=source_reliability_map,
            quality_weights=quality_weights,
            independence=independence,
        )
        copied["quality"] = quality
        copied["confidence"] = clip(float(copied.get("confidence") or quality), 0.0, 1.0)
        enriched.append(copied)

    return enriched


def qualifying_evidence(evidences: list[dict[str, Any]], *, threshold: float) -> list[dict[str, Any]]:
    return [item for item in evidences if float(item.get("quality", 0.0)) >= threshold]


def source_diversity(evidences: list[dict[str, Any]], *, threshold: float | None = None) -> int:
    filtered = evidences
    if threshold is not None:
        filtered = [item for item in evidences if float(item.get("quality", 0.0)) >= threshold]
    return len({str(item.get("source_type") or "") for item in filtered if str(item.get("source_type") or "")})


def component_confidence_from_evidence(
    evidences: list[dict[str, Any]],
    *,
    quality_threshold: float,
    penalty_no_evidence: float,
) -> float:
    qualified = qualifying_evidence(evidences, threshold=quality_threshold)
    if not qualified:
        return clip(penalty_no_evidence, 0.0, 1.0)
    avg_quality = sum(float(item.get("quality", 0.0)) for item in qualified) / max(1, len(qualified))
    diversity_bonus = 0.08 * min(3, source_diversity(qualified))
    return clip(avg_quality + diversity_bonus, 0.0, 1.0)


def evidence_confidence(evidences: list[dict[str, Any]]) -> float:
    if not evidences:
        return 0.0
    qualities = [float(item.get("quality", _specificity_score(str(item.get("snippet") or "")))) for item in evidences]
    avg_quality = sum(qualities) / max(1, len(qualities))
    diversity_bonus = 0.1 * min(3, source_diversity(evidences))
    return clip(avg_quality + diversity_bonus, 0.0, 1.0)


def evidence_quality(value: Any) -> float:
    # Backward-compatible helper used by phase-1 explainability.
    if isinstance(value, str):
        return _specificity_score(value)
    if isinstance(value, list):
        return evidence_confidence([item for item in value if isinstance(item, dict)])
    if isinstance(value, dict):
        snippet = str(value.get("snippet") or "")
        return _specificity_score(snippet)
    return 0.0


def make_evidence(
    *,
    source_type: str,
    source_url: str,
    snippet: str,
    doc_id: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    base_conf = _specificity_score(snippet) if confidence is None else confidence
    return {
        "source_type": source_type,
        "source_url": source_url,
        "snippet": snippet.strip()[:320],
        "doc_id": doc_id,
        "confidence": clip(float(base_conf), 0.0, 1.0),
    }


def classify_investability(
    *,
    name: str,
    description: str,
    categories: list[str],
    technologies: list[str],
) -> dict[str, Any]:
    text = " ".join([name, description, *categories, *technologies]).casefold()
    pos = sum(1 for token in INVESTABLE_TERMS if token in text)
    neg = sum(1 for token in NON_INVESTABLE_TERMS if token in text)

    if neg >= 2 and pos <= 1:
        return {
            "segment": "non_investable_club",
            "is_investable": False,
            "reason": "Club/chapter/association signals dominate technical venture signals.",
        }
    if pos >= 2:
        return {
            "segment": "spinout_candidate",
            "is_investable": True,
            "reason": "Technical venture signals indicate spinout potential.",
        }
    if pos == 1 and neg == 0:
        return {
            "segment": "watchlist_emerging",
            "is_investable": False,
            "reason": "Some venture potential but evidence is still too shallow.",
        }
    return {
        "segment": "watchlist_general",
        "is_investable": False,
        "reason": "Insufficient technical venture evidence.",
    }


def stage_from_text(text: str) -> str:
    lower = (text or "").casefold()
    if any(token in lower for token in ["repeat revenue", "renewal", "arr", "mrr"]):
        return "repeat_revenue"
    if any(token in lower for token in ["paid pilot", "commercial", "customer", "contract"]):
        return "paid_pilot"
    if any(token in lower for token in ["pilot", "poc"]):
        return "pilot"
    if any(token in lower for token in ["loi", "letter of intent"]):
        return "loi"
    if any(token in lower for token in ["interview", "discovery call", "customer discovery"]):
        return "interviews"
    return "none"


def stage_from_market_facts(*, interviews: int, lois: int, pilots: int, paid_pilots: int, evidences: list[dict[str, Any]]) -> str:
    snippets = " ".join(str(item.get("snippet") or "") for item in evidences)
    inferred = stage_from_text(snippets)
    if inferred == "repeat_revenue":
        return inferred
    if paid_pilots > 0:
        return "paid_pilot"
    if pilots > 0:
        return "pilot"
    if lois > 0:
        return "loi"
    if interviews > 0:
        return "interviews"
    return inferred if inferred != "none" else "none"


def stage_to_score(stage: str, stage_scores: dict[str, float]) -> float:
    key = (stage or "none").strip().casefold()
    return float(stage_scores.get(key, stage_scores.get("none", 1.0)))


def stage_at_least(stage: str, minimum_stage: str, stage_scores: dict[str, float]) -> bool:
    return stage_to_score(stage, stage_scores) >= stage_to_score(minimum_stage, stage_scores)


def extract_numeric_hints(text: str, keyword: str) -> int:
    lower = (text or "").casefold()
    patterns = [
        rf"(\d{{1,4}})\s+{re.escape(keyword)}",
        rf"{re.escape(keyword)}\s*[:=-]\s*(\d{{1,4}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return int(match.group(1))
    return 0


def has_keyword(text: str, keywords: list[str]) -> bool:
    lower = (text or "").casefold()
    return any(keyword.casefold() in lower for keyword in keywords)
