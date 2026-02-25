from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import DDEvidenceItem, DDGate, DDLegalFact, DDMarketFact, DDTeamFact, DDTechFact, Initiative
from initiative_tracker.store import get_json_list
from initiative_tracker.utils import clip

TECH_LEAD_KEYWORDS = ["cto", "technical lead", "engineering lead", "lead engineer", "research lead"]
EXTERNAL_SOURCE_CLASSES = {"github_api", "openalex", "semantic_scholar", "huggingface"}
MANUAL_SOURCE_CLASSES = {"manual_dd", "people_markdown", "linkedin_safe", "researchgate_safe"}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _latest_gates(rows: list[DDGate]) -> dict[tuple[int, str], DDGate]:
    ordered = sorted(rows, key=lambda row: row.updated_at or row.id, reverse=True)
    out: dict[tuple[int, str], DDGate] = {}
    for row in ordered:
        out.setdefault((row.initiative_id, row.gate_name), row)
    return out


def _gate_map_for_initiative(gate_map: dict[tuple[int, str], DDGate], initiative_id: int) -> dict[str, str]:
    return {
        gate: row.status
        for (row_id, gate), row in gate_map.items()
        if row_id == initiative_id
    }


def _has_tech_lead(team: DDTeamFact | None, snippets: list[str]) -> bool:
    roles = [item.casefold() for item in (get_json_list(team.key_roles_json) if team else [])]
    if any(any(keyword in role for keyword in TECH_LEAD_KEYWORDS) for role in roles):
        return True
    lower_snippets = "\n".join(snippets).casefold()
    return any(keyword in lower_snippets for keyword in TECH_LEAD_KEYWORDS)


def _recommended_next_steps(missing: list[str]) -> list[str]:
    mapping = {
        "no_named_operators": "Manually add named founders/operators via `import-dd-manual`.",
        "no_technical_lead": "Confirm at least one technical lead and role continuity.",
        "no_tech_proof_artifact": "Attach benchmark/prototype artifact links (GitHub, report, demo).",
        "no_market_validation": "Import customer interview and LOI/pilot evidence.",
        "no_external_sources": "Run `collect-dd-public --sources github,openalex,semantic_scholar,huggingface`.",
        "no_manual_sources": "Add high-trust manual DD evidence for team/market/legal facts.",
        "entity_unknown": "Add legal evidence confirming entity status.",
        "ip_unknown": "Add IP ownership evidence and assignment status.",
        "low_qualifying_evidence": "Increase high-specificity evidence items above quality threshold.",
        "low_source_diversity": "Add at least one additional independent source class.",
    }
    out: list[str] = []
    for key in missing:
        if key in mapping:
            out.append(mapping[key])
    return out


def build_source_audit_payload(
    *,
    initiatives: list[Initiative],
    evidence_by_initiative: dict[int, list[DDEvidenceItem]],
    team_map: dict[int, DDTeamFact],
    tech_map: dict[int, DDTechFact],
    market_map: dict[int, DDMarketFact],
    legal_map: dict[int, DDLegalFact],
    gate_map: dict[tuple[int, str], DDGate],
    quality_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    coverage_payload: list[dict[str, Any]] = []
    gaps_payload: list[dict[str, Any]] = []

    for initiative in initiatives:
        evidence_rows = evidence_by_initiative.get(initiative.id, [])
        source_types = sorted({row.source_type for row in evidence_rows if row.source_type})
        snippets = [row.snippet for row in evidence_rows if row.snippet]
        avg_quality = (
            sum(float(row.quality) for row in evidence_rows) / len(evidence_rows)
            if evidence_rows
            else 0.0
        )
        avg_reliability = (
            sum(float(row.reliability) for row in evidence_rows) / len(evidence_rows)
            if evidence_rows
            else 0.0
        )
        qualifying_rows = [row for row in evidence_rows if float(row.quality) >= quality_threshold]

        team = team_map.get(initiative.id)
        tech = tech_map.get(initiative.id)
        market = market_map.get(initiative.id)
        legal = legal_map.get(initiative.id)

        gate_status = _gate_map_for_initiative(gate_map, initiative.id)
        gate_blockers = [gate for gate in ["A", "B", "C", "D"] if gate_status.get(gate) != "pass"]

        missing: list[str] = []
        if not team or _safe_int(team.references_count, 0) < 2:
            missing.append("no_named_operators")
        if not _has_tech_lead(team, snippets):
            missing.append("no_technical_lead")
        if not tech or _safe_int(tech.benchmark_artifacts, 0) <= 0:
            missing.append("no_tech_proof_artifact")
        if not market or (_safe_int(market.lois, 0) + _safe_int(market.pilots, 0) + _safe_int(market.paid_pilots, 0)) <= 0:
            missing.append("no_market_validation")
        if not legal or (legal.entity_status or "").casefold() in {"", "unknown"}:
            missing.append("entity_unknown")
        if not legal or (legal.ip_ownership_status or "").casefold() in {"", "unknown"}:
            missing.append("ip_unknown")
        if len(qualifying_rows) < 3:
            missing.append("low_qualifying_evidence")
        if len(source_types) < 2:
            missing.append("low_source_diversity")
        if not any(source in EXTERNAL_SOURCE_CLASSES for source in source_types):
            missing.append("no_external_sources")
        if not any(source in MANUAL_SOURCE_CLASSES for source in source_types):
            missing.append("no_manual_sources")

        missing = sorted(set(missing))

        coverage_payload.append(
            {
                "initiative_id": initiative.id,
                "initiative_name": initiative.canonical_name,
                "evidence_items": len(evidence_rows),
                "qualifying_evidence_items": len(qualifying_rows),
                "quality_threshold": round(quality_threshold, 4),
                "avg_quality": round(clip(avg_quality, 0.0, 1.0), 4),
                "avg_reliability": round(clip(avg_reliability, 0.0, 1.0), 4),
                "source_types": source_types,
                "source_type_count": len(source_types),
                "has_external_sources": any(source in EXTERNAL_SOURCE_CLASSES for source in source_types),
                "has_manual_sources": any(source in MANUAL_SOURCE_CLASSES for source in source_types),
                "gate_status": gate_status,
                "gate_blockers": gate_blockers,
            }
        )

        gaps_payload.append(
            {
                "initiative_id": initiative.id,
                "initiative_name": initiative.canonical_name,
                "missing_critical_facts": missing,
                "gate_blockers": gate_blockers,
                "recommended_next_steps": _recommended_next_steps(missing),
            }
        )

    coverage_payload.sort(key=lambda row: (row["qualifying_evidence_items"], row["source_type_count"]), reverse=True)
    gaps_payload.sort(key=lambda row: len(row.get("missing_critical_facts") or []), reverse=True)
    return coverage_payload, gaps_payload


def source_audit(
    *,
    all_initiatives: bool = True,
    settings: Settings | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    rubric = cfg.load_dd_rubric()
    quality_threshold = clip(_safe_float(rubric.get("quality_threshold"), 0.55), 0.0, 1.0)

    details: dict[str, Any] = {
        "evaluated": 0,
        "quality_threshold": round(quality_threshold, 4),
        "coverage_rows": 0,
        "gap_rows": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "dd_source_audit")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            if not all_initiatives:
                initiatives = initiatives[:30]

            evidence_rows = session.execute(select(DDEvidenceItem)).scalars().all()
            team_rows = session.execute(select(DDTeamFact)).scalars().all()
            tech_rows = session.execute(select(DDTechFact)).scalars().all()
            market_rows = session.execute(select(DDMarketFact)).scalars().all()
            legal_rows = session.execute(select(DDLegalFact)).scalars().all()
            gate_rows = session.execute(select(DDGate)).scalars().all()

            evidence_by_initiative: dict[int, list[DDEvidenceItem]] = {}
            for row in evidence_rows:
                evidence_by_initiative.setdefault(row.initiative_id, []).append(row)

            coverage_payload, gaps_payload = build_source_audit_payload(
                initiatives=initiatives,
                evidence_by_initiative=evidence_by_initiative,
                team_map={row.initiative_id: row for row in team_rows},
                tech_map={row.initiative_id: row for row in tech_rows},
                market_map={row.initiative_id: row for row in market_rows},
                legal_map={row.initiative_id: row for row in legal_rows},
                gate_map=_latest_gates(gate_rows),
                quality_threshold=quality_threshold,
            )

            _write_json(cfg.exports_dir / "dd_source_coverage.json", coverage_payload)
            _write_json(cfg.exports_dir / "dd_evidence_gaps.json", gaps_payload)

            details["evaluated"] = len(initiatives)
            details["coverage_rows"] = len(coverage_payload)
            details["gap_rows"] = len(gaps_payload)
            details["high_gap_initiatives"] = sum(
                1
                for row in gaps_payload
                if len(row.get("missing_critical_facts") or []) >= 4
            )

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
