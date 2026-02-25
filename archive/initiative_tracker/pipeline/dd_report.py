from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import DDGate, DDMemo, DDScore, Initiative
from initiative_tracker.store import add_dd_memo
from initiative_tracker.utils import from_json


def _latest_dd_scores(rows: list[DDScore]) -> dict[int, DDScore]:
    ordered = sorted(rows, key=lambda row: _ts(row.scored_at), reverse=True)
    latest: dict[int, DDScore] = {}
    for row in ordered:
        latest.setdefault(row.initiative_id, row)
    return latest


def _latest_memos(rows: list[DDMemo]) -> dict[int, DDMemo]:
    ordered = sorted(rows, key=lambda row: _ts(row.created_at), reverse=True)
    latest: dict[int, DDMemo] = {}
    for row in ordered:
        latest.setdefault(row.initiative_id, row)
    return latest


def _ts(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).timestamp()
    return value.timestamp()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _passes_all(gates: list[DDGate]) -> bool:
    status = {row.gate_name: row.status for row in gates}
    return all(status.get(name) == "pass" for name in ["A", "B", "C", "D"])


def _team_fit_labels(score: DDScore) -> tuple[list[str], list[str], str]:
    values = {
        "product": float(score.team_product_fit),
        "tech": float(score.team_tech_fit),
        "sales": float(score.team_sales_fit),
    }
    strong_in = [name for name, value in values.items() if value >= 4.0]
    need_help_in = [name for name, value in values.items() if value < 3.0]
    if values["tech"] < 3.0:
        support_priority = "tech"
    elif need_help_in:
        support_priority = need_help_in[0]
    else:
        support_priority = "none"
    return strong_in, need_help_in, support_priority


def _recommendation(
    *,
    score: DDScore,
    gate_pass: bool,
    blocking_gates: list[str],
) -> dict[str, Any]:
    strong_in, need_help_in, support_priority = _team_fit_labels(score)

    if gate_pass and score.conviction_score >= 4.2 and score.team_tech_fit >= 3.8 and score.market_validation_stage in {"pilot", "paid_pilot", "repeat_revenue"}:
        decision = "invest"
        check_size = "100k-250k"
        next_actions = [
            "Run founder diligence focused on technical execution depth",
            "Confirm one paid customer path with clear decision owner",
            "Complete legal/IP verification checklist",
            "Agree a 30-day support sprint with specific outcomes",
        ]
    elif gate_pass and score.conviction_score >= 3.2:
        decision = "monitor"
        check_size = "optionality_only"
        next_actions = [
            "Define two milestone proofs (one tech, one market)",
            "Support conversion from LOI to pilot",
            "Re-score after milestone evidence is added",
        ]
    else:
        decision = "pass"
        check_size = "none"
        next_actions = [
            "Document blockers and required evidence",
            "Revisit only after explicit gate resolution",
        ]

    rationale = (
        f"Conviction {score.conviction_score:.2f}/5, confidence {score.conviction_confidence:.2f}, "
        f"team-fit (P/T/S) {score.team_product_fit:.2f}/{score.team_tech_fit:.2f}/{score.team_sales_fit:.2f}, "
        f"market stage {score.market_validation_stage}. Gate pass={gate_pass}."
    )
    if blocking_gates:
        rationale += f" Blocking gates: {', '.join(blocking_gates)}."

    return {
        "decision": decision,
        "check_size_band": check_size,
        "rationale": rationale,
        "next_actions": next_actions,
        "strong_in": strong_in,
        "need_help_in": need_help_in,
        "support_priority": support_priority,
    }


def generate_dd_report(
    *,
    initiative_id: int | None = None,
    top_n: int = 15,
    settings: Settings | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "memos_generated": 0,
        "initiative_id": initiative_id,
        "top_n": top_n,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "dd_report")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            initiative_by_id = {row.id: row for row in initiatives}
            dd_scores = _latest_dd_scores(session.execute(select(DDScore)).scalars().all())

            gates_by_initiative: dict[int, list[DDGate]] = {}
            for gate in session.execute(select(DDGate)).scalars().all():
                gates_by_initiative.setdefault(gate.initiative_id, []).append(gate)

            candidate_ids = list(dd_scores.keys())
            if initiative_id is not None:
                candidate_ids = [initiative_id] if initiative_id in candidate_ids else []
            else:
                candidate_ids = sorted(candidate_ids, key=lambda ident: dd_scores[ident].conviction_score, reverse=True)[:top_n]

            memo_payload: list[dict[str, Any]] = []
            for ident in candidate_ids:
                initiative = initiative_by_id.get(ident)
                dd = dd_scores.get(ident)
                if initiative is None or dd is None:
                    continue

                gates = gates_by_initiative.get(ident, [])
                status_by_gate = {row.gate_name: row.status for row in gates}
                blocking = [name for name in ["A", "B", "C", "D"] if status_by_gate.get(name) != "pass"]
                gate_pass = _passes_all(gates)

                rec = _recommendation(score=dd, gate_pass=gate_pass, blocking_gates=blocking)

                top_risks = []
                if blocking:
                    top_risks.append(f"Gate blockers: {', '.join(blocking)}")
                if dd.team_tech_fit < 3.0:
                    top_risks.append("Critical tech capability gap")
                if dd.market_validation_stage in {"none", "interviews"}:
                    top_risks.append("Insufficient market validation stage")
                if dd.legal_dd < 2.8:
                    top_risks.append("Legal readiness remains shallow")

                recommendation = {
                    "initiative_id": initiative.id,
                    "decision": rec["decision"],
                    "check_size_band": rec["check_size_band"],
                    "rationale": rec["rationale"],
                    "top_risks": top_risks,
                    "next_actions": rec["next_actions"],
                    "strong_in": rec["strong_in"],
                    "need_help_in": rec["need_help_in"],
                    "support_priority": rec["support_priority"],
                }

                add_dd_memo(
                    session,
                    initiative_id=initiative.id,
                    decision=rec["decision"],
                    check_size_band=rec["check_size_band"],
                    rationale=rec["rationale"],
                    top_risks=top_risks,
                    next_actions=rec["next_actions"],
                    recommendation=recommendation,
                )

                memo_payload.append(
                    {
                        "initiative_id": initiative.id,
                        "initiative_name": initiative.canonical_name,
                        "decision": rec["decision"],
                        "check_size_band": rec["check_size_band"],
                        "rationale": rec["rationale"],
                        "top_risks": top_risks,
                        "next_actions": rec["next_actions"],
                        "strong_in": rec["strong_in"],
                        "need_help_in": rec["need_help_in"],
                        "support_priority": rec["support_priority"],
                        "scorecard": {
                            "team_dd": round(dd.team_dd, 4),
                            "tech_dd": round(dd.tech_dd, 4),
                            "market_dd": round(dd.market_dd, 4),
                            "execution_dd": round(dd.execution_dd, 4),
                            "legal_dd": round(dd.legal_dd, 4),
                            "team_product_fit": round(dd.team_product_fit, 4),
                            "team_tech_fit": round(dd.team_tech_fit, 4),
                            "team_sales_fit": round(dd.team_sales_fit, 4),
                            "market_validation_stage": dd.market_validation_stage,
                            "conviction_confidence": round(dd.conviction_confidence, 4),
                            "conviction_score": round(dd.conviction_score, 4),
                        },
                        "gate_status": status_by_gate,
                    }
                )
                details["memos_generated"] += 1

            _write_json(cfg.exports_dir / "investment_memos.json", memo_payload)

            brief_lines = [
                "# Due Diligence Brief",
                "",
                "## Summary",
                f"- Memos generated: {len(memo_payload)}",
                f"- Invest decisions: {sum(1 for row in memo_payload if row['decision'] == 'invest')}",
                f"- Monitor decisions: {sum(1 for row in memo_payload if row['decision'] == 'monitor')}",
                f"- Pass decisions: {sum(1 for row in memo_payload if row['decision'] == 'pass')}",
                "",
                "## Recommendations",
            ]
            for row in memo_payload:
                brief_lines.append(
                    f"- {row['initiative_name']}: {row['decision']} (conviction {row['scorecard']['conviction_score']}, confidence {row['scorecard']['conviction_confidence']})"
                )
                brief_lines.append(f"  - Strong in: {', '.join(row['strong_in']) if row['strong_in'] else 'none'}")
                brief_lines.append(f"  - Need help in: {', '.join(row['need_help_in']) if row['need_help_in'] else 'none'}")
                brief_lines.append(f"  - Support priority: {row['support_priority']}")
                brief_lines.append(f"  - Rationale: {row['rationale']}")
                brief_lines.append(f"  - Top risks: {', '.join(row['top_risks']) if row['top_risks'] else 'none'}")
                brief_lines.append(f"  - Next actions: {', '.join(row['next_actions'])}")

            report_path = cfg.reports_dir / "due_diligence_brief.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n".join(brief_lines), encoding="utf-8")

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise


def latest_investment_memos(*, db_url: str | None = None) -> list[dict[str, Any]]:
    with session_scope(db_url) as session:
        memos = _latest_memos(session.execute(select(DDMemo)).scalars().all())
        out: list[dict[str, Any]] = []
        for row in memos.values():
            out.append(
                {
                    "initiative_id": row.initiative_id,
                    "decision": row.decision,
                    "check_size_band": row.check_size_band,
                    "rationale": row.rationale,
                    "top_risks": from_json(row.top_risks_json, []),
                    "next_actions": from_json(row.next_actions_json, []),
                    "recommendation": from_json(row.recommendation_json, {}),
                }
            )
        return out
