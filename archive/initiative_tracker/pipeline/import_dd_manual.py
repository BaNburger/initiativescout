from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import Initiative
from initiative_tracker.pipeline.dd_common import make_evidence
from initiative_tracker.store import (
    upsert_dd_finance_fact,
    upsert_dd_legal_fact,
    upsert_dd_market_fact,
    upsert_dd_team_fact,
)
from initiative_tracker.utils import clip


def _as_bool(value: Any) -> bool:
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, *, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:  # noqa: B904
        raise ValueError(f"Invalid float value '{value}'") from exc


def _as_int(value: Any, *, default: int = 0) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError as exc:  # noqa: B904
        raise ValueError(f"Invalid integer value '{value}'") from exc


def _split_values(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def _normalize_stage(value: Any) -> str:
    stage = str(value or "").strip().casefold().replace(" ", "_")
    aliases = {
        "none": "none",
        "interview": "interviews",
        "interviews": "interviews",
        "loi": "loi",
        "letter_of_intent": "loi",
        "pilot": "pilot",
        "paid_pilot": "paid_pilot",
        "repeat_revenue": "repeat_revenue",
        "revenue": "repeat_revenue",
    }
    return aliases.get(stage, "none")


def _stage_floor_counts(stage: str) -> tuple[int, int, int, int]:
    if stage == "interviews":
        return 5, 0, 0, 0
    if stage == "loi":
        return 5, 1, 0, 0
    if stage == "pilot":
        return 8, 1, 1, 0
    if stage == "paid_pilot":
        return 10, 1, 1, 1
    if stage == "repeat_revenue":
        return 12, 2, 2, 2
    return 0, 0, 0, 0


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"Manual DD file not found: {path}")

    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON manual DD file must contain a top-level array of rows")
        rows = [row for row in payload if isinstance(row, dict)]
        if len(rows) != len(payload):
            raise ValueError("JSON manual DD file contains non-object rows")
        return rows

    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]

    raise ValueError("Manual DD import supports only .csv or .json files")


def import_dd_manual(*, file_path: str, db_url: str | None = None) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    init_db(db_url)

    details: dict[str, Any] = {
        "file": str(path),
        "rows_total": 0,
        "rows_imported": 0,
        "rows_failed": 0,
        "errors": [],
    }

    rows = _load_rows(path)
    details["rows_total"] = len(rows)

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "import_dd_manual")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            initiatives_by_id = {initiative.id: initiative for initiative in initiatives}

            def resolve_initiative(row: dict[str, Any]) -> Initiative:
                raw_id = row.get("initiative_id")
                if str(raw_id or "").strip().isdigit():
                    initiative = initiatives_by_id.get(int(str(raw_id).strip()))
                    if initiative:
                        return initiative
                    raise ValueError(f"initiative_id '{raw_id}' not found")

                needle = str(row.get("initiative_name") or "").strip().casefold()
                if not needle:
                    raise ValueError("Each row requires initiative_id or initiative_name")
                for initiative in initiatives:
                    if initiative.canonical_name.casefold() == needle:
                        return initiative
                for initiative in initiatives:
                    if needle in initiative.canonical_name.casefold():
                        return initiative
                raise ValueError(f"initiative_name '{row.get('initiative_name')}' not found")

            for idx, row in enumerate(rows, start=1):
                try:
                    initiative = resolve_initiative(row)
                    source_url = str(row.get("source_url") or initiative.primary_url or "")
                    source_type = str(row.get("source_type") or "manual_dd")

                    stage = _normalize_stage(row.get("market_validation_stage"))
                    interviews = _as_int(row.get("customer_interviews"), default=0)
                    lois = _as_int(row.get("lois"), default=0)
                    pilots = _as_int(row.get("pilots"), default=0)
                    paid_pilots = _as_int(row.get("paid_pilots"), default=0)
                    floor_interviews, floor_lois, floor_pilots, floor_paid = _stage_floor_counts(stage)
                    interviews = max(interviews, floor_interviews)
                    lois = max(lois, floor_lois)
                    pilots = max(pilots, floor_pilots)
                    paid_pilots = max(paid_pilots, floor_paid)

                    manual_named_operators = _as_int(row.get("named_operators"), default=0)
                    manual_technical_leads = _as_int(row.get("technical_leads"), default=0)
                    references_count = max(_as_int(row.get("references_count"), default=0), manual_named_operators)
                    key_roles = _split_values(row.get("key_roles"))
                    if manual_technical_leads > 0 and not any("technical lead" in role.casefold() for role in key_roles):
                        key_roles.append("Technical Lead")

                    product_fit_hint = _as_float(row.get("team_product_fit"), default=0.0)
                    tech_fit_hint = _as_float(row.get("team_tech_fit"), default=0.0)
                    sales_fit_hint = _as_float(row.get("team_sales_fit"), default=0.0)

                    team_snippet_parts = [
                        str(row.get("evidence_snippet") or "Manual DD row"),
                        f"product_fit_hint={product_fit_hint:.2f}" if product_fit_hint > 0 else "",
                        f"tech_fit_hint={tech_fit_hint:.2f}" if tech_fit_hint > 0 else "",
                        f"sales_fit_hint={sales_fit_hint:.2f}" if sales_fit_hint > 0 else "",
                        f"named_operators={manual_named_operators}" if manual_named_operators > 0 else "",
                        f"technical_leads={manual_technical_leads}" if manual_technical_leads > 0 else "",
                    ]
                    team_snippet = "; ".join([part for part in team_snippet_parts if part])

                    upsert_dd_team_fact(
                        session,
                        initiative_id=initiative.id,
                        commitment_level=clip(_as_float(row.get("commitment_level"), default=0.0), 0.0, 5.0),
                        key_roles=key_roles,
                        references_count=references_count,
                        founder_risk_flags=_split_values(row.get("founder_risk_flags")),
                        investable_segment=str(row.get("investable_segment") or "manual_segment"),
                        is_investable=_as_bool(row.get("is_investable")),
                        evidence=[
                            make_evidence(
                                source_type=source_type,
                                source_url=source_url,
                                snippet=team_snippet,
                                doc_id=f"manual_row_{idx}",
                                confidence=0.9,
                            )
                        ],
                        source_type=source_type,
                        source_url=source_url,
                        confidence=0.9,
                    )

                    upsert_dd_market_fact(
                        session,
                        initiative_id=initiative.id,
                        customer_interviews=interviews,
                        lois=lois,
                        pilots=pilots,
                        paid_pilots=paid_pilots,
                        pricing_evidence=_as_bool(row.get("pricing_evidence")),
                        buyer_persona_clarity=clip(_as_float(row.get("buyer_persona_clarity"), default=0.0), 0.0, 5.0),
                        sam_som_quality=clip(_as_float(row.get("sam_som_quality"), default=0.0), 0.0, 5.0),
                        evidence=[
                            make_evidence(
                                source_type=source_type,
                                source_url=source_url,
                                snippet="; ".join(
                                    [
                                        str(row.get("market_evidence") or "Manual market DD"),
                                        f"market_validation_stage={stage}" if stage != "none" else "",
                                        f"tech_outcomes={str(row.get('tech_outcomes') or '').strip()}"
                                        if str(row.get("tech_outcomes") or "").strip()
                                        else "",
                                        f"market_outcomes={str(row.get('market_outcomes') or '').strip()}"
                                        if str(row.get("market_outcomes") or "").strip()
                                        else "",
                                    ]
                                ),
                                doc_id=f"manual_market_{idx}",
                                confidence=0.9,
                            )
                        ],
                        source_type=source_type,
                        source_url=source_url,
                        confidence=0.9,
                    )

                    upsert_dd_legal_fact(
                        session,
                        initiative_id=initiative.id,
                        entity_status=str(row.get("entity_status") or "unknown"),
                        ip_ownership_status=str(row.get("ip_ownership_status") or "unknown"),
                        founder_agreements=_as_bool(row.get("founder_agreements")),
                        licensing_constraints=_as_bool(row.get("licensing_constraints")),
                        compliance_flags=_split_values(row.get("compliance_flags")),
                        legal_risk_score=clip(_as_float(row.get("legal_risk_score"), default=0.0), 0.0, 5.0),
                        evidence=[
                            make_evidence(
                                source_type=source_type,
                                source_url=source_url,
                                snippet=str(row.get("legal_evidence") or "Manual legal DD"),
                                doc_id=f"manual_legal_{idx}",
                                confidence=0.95,
                            )
                        ],
                        source_type=source_type,
                        source_url=source_url,
                        confidence=0.95,
                    )

                    upsert_dd_finance_fact(
                        session,
                        initiative_id=initiative.id,
                        burn_monthly=_as_float(row.get("burn_monthly"), default=0.0),
                        runway_months=_as_float(row.get("runway_months"), default=0.0),
                        funding_dependence=clip(_as_float(row.get("funding_dependence"), default=0.0), 0.0, 5.0),
                        cap_table_summary=str(row.get("cap_table_summary") or ""),
                        dilution_risk=clip(_as_float(row.get("dilution_risk"), default=0.0), 0.0, 5.0),
                        evidence=[
                            make_evidence(
                                source_type=source_type,
                                source_url=source_url,
                                snippet=str(row.get("finance_evidence") or "Manual finance DD"),
                                doc_id=f"manual_finance_{idx}",
                                confidence=0.95,
                            )
                        ],
                        source_type=source_type,
                        source_url=source_url,
                        confidence=0.95,
                    )

                    details["rows_imported"] += 1
                except Exception as exc:  # noqa: BLE001
                    details["rows_failed"] += 1
                    details["errors"].append({"row": idx, "error": str(exc)})

            if details["rows_failed"] > 0 and details["rows_imported"] == 0:
                raise ValueError(f"Manual DD import failed: {details['errors'][0]['error']}")

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
