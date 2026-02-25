from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import (
    DDGate,
    DDMemo,
    DDScore,
    DDScoreComponent,
    Initiative,
    InitiativeSource,
    Ranking,
    Score,
    ScoreComponent,
    ScoreEvidence,
)
from initiative_tracker.pipeline.dossiers import build_initiative_dossiers
from initiative_tracker.store import get_json_list


def _latest_scores(scores: list[Score]) -> dict[int, Score]:
    ordered = sorted(scores, key=lambda row: _ts(row.scored_at), reverse=True)
    latest: dict[int, Score] = {}
    for score in ordered:
        latest.setdefault(score.initiative_id, score)
    return latest


def _ts(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).timestamp()
    return value.timestamp()


def _latest_rankings(session, ranking_type: str) -> list[Ranking]:
    latest_timestamp = session.execute(
        select(func.max(Ranking.generated_at)).where(Ranking.ranking_type == ranking_type)
    ).scalar()
    if latest_timestamp is None:
        return []
    return (
        session.execute(
            select(Ranking)
            .where(Ranking.ranking_type == ranking_type, Ranking.generated_at == latest_timestamp)
            .order_by(Ranking.rank_position.asc())
        )
        .scalars()
        .all()
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_support_list(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:  # noqa: BLE001
        pass
    return [segment.strip() for segment in raw.strip("[]").split(",") if segment.strip()]


def _parse_meta(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _redact_channel(channel: str) -> str:
    if "@" in channel:
        user, _, domain = channel.partition("@")
        return f"{user[:2]}***@{domain}"
    return channel


def _redact_channels(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in payload:
        copied = dict(row)
        channels = copied.get("contact_channels")
        if isinstance(channels, list):
            copied["contact_channels"] = [_redact_channel(str(channel)) for channel in channels]
        out.append(copied)
    return out


def _redact_dossiers(dossiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dossier in dossiers:
        copied = dict(dossier)
        talent = copied.get("top_talent")
        if isinstance(talent, list):
            redacted_people = []
            for person in talent:
                if not isinstance(person, dict):
                    continue
                p = dict(person)
                channels = p.get("contact_channels")
                if isinstance(channels, list):
                    p["contact_channels"] = [_redact_channel(str(channel)) for channel in channels]
                redacted_people.append(p)
            copied["top_talent"] = redacted_people
        out.append(copied)
    return out


def _team_fit_labels(dd_score: DDScore) -> tuple[list[str], list[str], str]:
    fit_scores = {
        "product": float(dd_score.team_product_fit),
        "tech": float(dd_score.team_tech_fit),
        "sales": float(dd_score.team_sales_fit),
    }
    strong_in = [name for name, value in fit_scores.items() if value >= 4.0]
    need_help_in = [name for name, value in fit_scores.items() if value < 3.0]
    if fit_scores["tech"] < 3.0:
        support_priority = "tech"
    elif need_help_in:
        support_priority = need_help_in[0]
    else:
        support_priority = "none"
    return strong_in, need_help_in, support_priority


def _ranking_payload(rows: list[Ranking], *, key_name: str) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        maybe_initiative_id = int(row.item_key) if str(row.item_key).isdigit() else None
        entry = {
            key_name: row.item_name,
            "item_key": row.item_key,
            "initiative_id": maybe_initiative_id,
            "rank": row.rank_position,
            "score": round(row.score, 4),
            "supporting": _parse_support_list(row.supporting_initiatives_json),
            "evidence_count": row.evidence_count,
        }
        entry.update(_parse_meta(row.item_meta_json))
        payload.append(entry)
    return payload


def export_outputs(top_n: int = 15, settings: Settings | None = None, db_url: str | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "initiatives_exported": 0,
        "technology_rankings": 0,
        "market_rankings": 0,
        "team_rankings": 0,
        "outreach_rankings": 0,
        "upside_rankings": 0,
        "talent_operator_rankings": 0,
        "talent_alumni_rankings": 0,
        "dossiers_exported": 0,
        "score_explanations": 0,
        "dd_gates_exported": 0,
        "dd_scores_exported": 0,
        "dd_memos_exported": 0,
        "dd_score_components_exported": 0,
        "team_capability_matrix_exported": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "export")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            scores = session.execute(select(Score)).scalars().all()
            sources = session.execute(select(InitiativeSource)).scalars().all()
            components = session.execute(select(ScoreComponent)).scalars().all()
            evidences = session.execute(select(ScoreEvidence)).scalars().all()
            dd_gates = session.execute(select(DDGate)).scalars().all()
            dd_scores = session.execute(select(DDScore)).scalars().all()
            dd_score_components = session.execute(select(DDScoreComponent)).scalars().all()
            dd_memos = session.execute(select(DDMemo)).scalars().all()
            latest_score = _latest_scores(scores)

            source_map: dict[int, list[str]] = {}
            for source in sources:
                source_map.setdefault(source.initiative_id, [])
                if source.source_url:
                    source_map[source.initiative_id].append(source.source_url)
                if source.external_url:
                    source_map[source.initiative_id].append(source.external_url)

            initiatives_master: list[dict[str, Any]] = []
            for initiative in initiatives:
                score = latest_score.get(initiative.id)
                confidence = initiative.confidence
                if score:
                    confidence = (
                        score.confidence_tech
                        + score.confidence_market
                        + score.confidence_team
                        + score.confidence_maturity
                        + score.confidence_actionability
                        + score.confidence_support_fit
                    ) / 6.0

                initiatives_master.append(
                    {
                        "initiative_id": initiative.id,
                        "name": initiative.canonical_name,
                        "university": initiative.university or None,
                        "source_urls": sorted(set(source_map.get(initiative.id, []))),
                        "description_raw": initiative.description_raw,
                        "description_summary_en": initiative.description_summary_en,
                        "categories": get_json_list(initiative.categories_json),
                        "technologies": get_json_list(initiative.technologies_json),
                        "markets": get_json_list(initiative.markets_json),
                        "team_signals": get_json_list(initiative.team_signals_json),
                        "last_seen_at": initiative.last_seen_at.isoformat() if initiative.last_seen_at else None,
                        "confidence": round(float(confidence), 4),
                        "scores": {
                            "tech_depth": round(score.tech_depth, 4) if score else None,
                            "market_opportunity": round(score.market_opportunity, 4) if score else None,
                            "team_strength": round(score.team_strength, 4) if score else None,
                            "maturity": round(score.maturity, 4) if score else None,
                            "actionability_0_6m": round(score.actionability_0_6m, 4) if score else None,
                            "support_fit": round(score.support_fit, 4) if score else None,
                            "legacy_composite": round(score.composite_score, 4) if score else None,
                            "outreach_now_score": round(score.outreach_now_score, 4) if score else None,
                            "venture_upside_score": round(score.venture_upside_score, 4) if score else None,
                        },
                    }
                )

            initiatives_master.sort(
                key=lambda row: row["scores"]["outreach_now_score"] if row["scores"]["outreach_now_score"] is not None else -1,
                reverse=True,
            )

            _write_json(cfg.exports_dir / "initiatives_master.json", initiatives_master)
            details["initiatives_exported"] = len(initiatives_master)

            evidence_by_component: dict[int, list[ScoreEvidence]] = {}
            for evidence in evidences:
                evidence_by_component.setdefault(evidence.score_component_id, []).append(evidence)

            explanations_by_initiative: dict[int, list[dict[str, Any]]] = {}
            for component in components:
                explanations_by_initiative.setdefault(component.initiative_id, []).append(
                    {
                        "initiative_id": component.initiative_id,
                        "dimension": component.dimension,
                        "component_key": component.component_key,
                        "raw_value": round(component.raw_value, 4),
                        "normalized_value": round(component.normalized_value, 4),
                        "weight": round(component.weight, 4),
                        "weighted_contribution": round(component.weighted_contribution, 4),
                        "source_mix": get_json_list(component.source_mix_json),
                        "confidence": round(component.confidence, 4),
                        "provenance": component.provenance,
                        "evidence_refs": [
                            {
                                "source_url": evidence.source_url,
                                "snippet": evidence.snippet,
                                "signal_type": evidence.signal_type,
                                "signal_key": evidence.signal_key,
                                "value": round(evidence.value, 4),
                            }
                            for evidence in evidence_by_component.get(component.id, [])
                        ],
                    }
                )

            score_explanations = [
                {
                    "initiative_id": initiative.id,
                    "initiative_name": initiative.canonical_name,
                    "components": sorted(
                        explanations_by_initiative.get(initiative.id, []),
                        key=lambda row: (row["dimension"], row["component_key"]),
                    ),
                }
                for initiative in initiatives
            ]
            _write_json(cfg.exports_dir / "score_explanations.json", score_explanations)
            details["score_explanations"] = len(score_explanations)

            latest_dd_scores: dict[int, DDScore] = {}
            for row in sorted(dd_scores, key=lambda item: item.scored_at, reverse=True):
                latest_dd_scores.setdefault(row.initiative_id, row)

            latest_dd_memos: dict[int, DDMemo] = {}
            for row in sorted(dd_memos, key=lambda item: item.created_at, reverse=True):
                latest_dd_memos.setdefault(row.initiative_id, row)

            dd_gate_payload = [
                {
                    "initiative_id": row.initiative_id,
                    "gate_name": row.gate_name,
                    "status": row.status,
                    "reason": row.reason,
                    "evidence_refs": get_json_list(row.evidence_json),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in dd_gates
            ]
            dd_score_payload = [
                {
                    "initiative_id": initiative.id,
                    "initiative_name": initiative.canonical_name,
                    "team_dd": round(latest_dd_scores[initiative.id].team_dd, 4),
                    "tech_dd": round(latest_dd_scores[initiative.id].tech_dd, 4),
                    "market_dd": round(latest_dd_scores[initiative.id].market_dd, 4),
                    "execution_dd": round(latest_dd_scores[initiative.id].execution_dd, 4),
                    "legal_dd": round(latest_dd_scores[initiative.id].legal_dd, 4),
                    "team_product_fit": round(latest_dd_scores[initiative.id].team_product_fit, 4),
                    "team_tech_fit": round(latest_dd_scores[initiative.id].team_tech_fit, 4),
                    "team_sales_fit": round(latest_dd_scores[initiative.id].team_sales_fit, 4),
                    "market_validation_stage": latest_dd_scores[initiative.id].market_validation_stage,
                    "conviction_confidence": round(latest_dd_scores[initiative.id].conviction_confidence, 4),
                    "conviction_score": round(latest_dd_scores[initiative.id].conviction_score, 4),
                    "scored_at": latest_dd_scores[initiative.id].scored_at.isoformat() if latest_dd_scores[initiative.id].scored_at else None,
                }
                for initiative in initiatives
                if initiative.id in latest_dd_scores
            ]
            dd_component_payload = [
                {
                    "initiative_id": row.initiative_id,
                    "dimension": row.dimension,
                    "component_key": row.component_key,
                    "raw_value": round(row.raw_value, 4),
                    "normalized_value": round(row.normalized_value, 4),
                    "weight": round(row.weight, 4),
                    "weighted_contribution": round(row.weighted_contribution, 4),
                    "confidence": round(row.confidence, 4),
                    "evidence_refs": get_json_list(row.evidence_json),
                    "source_mix": get_json_list(row.source_mix_json),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in dd_score_components
            ]
            team_capability_payload = []
            for initiative in initiatives:
                dd = latest_dd_scores.get(initiative.id)
                if not dd:
                    continue
                strong_in, need_help_in, support_priority = _team_fit_labels(dd)
                critical_gap = "tech" if dd.team_tech_fit < 3.0 else (need_help_in[0] if need_help_in else None)
                team_capability_payload.append(
                    {
                        "initiative_id": initiative.id,
                        "initiative_name": initiative.canonical_name,
                        "product_fit": round(dd.team_product_fit, 4),
                        "tech_fit": round(dd.team_tech_fit, 4),
                        "sales_fit": round(dd.team_sales_fit, 4),
                        "strong_in": strong_in,
                        "need_help_in": need_help_in,
                        "critical_gap": critical_gap,
                        "support_priority": support_priority,
                    }
                )
            dd_memo_payload = []
            for initiative in initiatives:
                if initiative.id not in latest_dd_memos:
                    continue
                memo = latest_dd_memos[initiative.id]
                recommendation = _parse_meta(memo.recommendation_json)
                strong_in = recommendation.get("strong_in")
                need_help_in = recommendation.get("need_help_in")
                dd_memo_payload.append(
                    {
                        "initiative_id": initiative.id,
                        "initiative_name": initiative.canonical_name,
                        "decision": memo.decision,
                        "check_size_band": memo.check_size_band,
                        "rationale": memo.rationale,
                        "top_risks": get_json_list(memo.top_risks_json),
                        "next_actions": get_json_list(memo.next_actions_json),
                        "strong_in": [str(item) for item in strong_in] if isinstance(strong_in, list) else [],
                        "need_help_in": [str(item) for item in need_help_in] if isinstance(need_help_in, list) else [],
                        "support_priority": str(recommendation.get("support_priority") or ""),
                    }
                )

            _write_json(cfg.exports_dir / "dd_gates.json", dd_gate_payload)
            _write_json(cfg.exports_dir / "dd_scores.json", dd_score_payload)
            _write_json(cfg.exports_dir / "dd_score_components.json", dd_component_payload)
            _write_json(cfg.exports_dir / "team_capability_matrix.json", team_capability_payload)
            _write_json(cfg.exports_dir / "investment_memos.json", dd_memo_payload)
            details["dd_gates_exported"] = len(dd_gate_payload)
            details["dd_scores_exported"] = len(dd_score_payload)
            details["dd_memos_exported"] = len(dd_memo_payload)
            details["dd_score_components_exported"] = len(dd_component_payload)
            details["team_capability_matrix_exported"] = len(team_capability_payload)

            technology_rows = _latest_rankings(session, "technologies")[:top_n]
            market_rows = _latest_rankings(session, "market_opportunities")[:top_n]
            team_rows = _latest_rankings(session, "teams")[:top_n]

            technology_payload = [
                {
                    "technology_domain": row.item_name,
                    "opportunity_score": round(row.score, 4),
                    "supporting_initiatives": _parse_support_list(row.supporting_initiatives_json),
                    "evidence_count": row.evidence_count,
                }
                for row in technology_rows
            ]
            market_payload = [
                {
                    "market_domain": row.item_name,
                    "opportunity_score": round(row.score, 4),
                    "supporting_initiatives": _parse_support_list(row.supporting_initiatives_json),
                    "evidence_count": row.evidence_count,
                }
                for row in market_rows
            ]
            team_payload = []
            for row in team_rows:
                initiative_id = int(row.item_key) if str(row.item_key).isdigit() else None
                linked_score = latest_score.get(initiative_id or -1)
                team_payload.append(
                    {
                        "initiative_id": initiative_id,
                        "initiative_name": row.item_name,
                        "team_strength": round(row.score, 4),
                        "supporting_signals": _parse_support_list(row.supporting_initiatives_json),
                        "composite_score": round(linked_score.composite_score, 4) if linked_score else None,
                        "outreach_now_score": round(linked_score.outreach_now_score, 4) if linked_score else None,
                        "venture_upside_score": round(linked_score.venture_upside_score, 4) if linked_score else None,
                    }
                )

            outreach_payload = _ranking_payload(_latest_rankings(session, "outreach_targets")[:top_n], key_name="initiative_name")
            upside_payload = _ranking_payload(_latest_rankings(session, "venture_upside")[:top_n], key_name="initiative_name")
            talent_operator_payload = _redact_channels(
                _ranking_payload(_latest_rankings(session, "talent_operators")[:top_n], key_name="person_name")
            )
            talent_alumni_payload = _redact_channels(
                _ranking_payload(_latest_rankings(session, "talent_alumni_angels")[:top_n], key_name="person_name")
            )
            dd_investable_payload = _ranking_payload(_latest_rankings(session, "dd_investable")[:top_n], key_name="initiative_name")
            dd_watchlist_payload = _ranking_payload(_latest_rankings(session, "dd_watchlist")[:top_n], key_name="initiative_name")

            _write_json(cfg.exports_dir / "top_technologies.json", technology_payload)
            _write_json(cfg.exports_dir / "top_market_opportunities.json", market_payload)
            _write_json(cfg.exports_dir / "top_teams.json", team_payload)
            _write_json(cfg.exports_dir / "top_outreach_targets.json", outreach_payload)
            _write_json(cfg.exports_dir / "top_venture_upside.json", upside_payload)
            _write_json(cfg.exports_dir / "top_talent_operators.json", talent_operator_payload)
            _write_json(cfg.exports_dir / "top_talent_alumni_angels.json", talent_alumni_payload)
            _write_json(cfg.exports_dir / "investable_rankings.json", dd_investable_payload)
            _write_json(cfg.exports_dir / "watchlist_rankings.json", dd_watchlist_payload)

            _write_csv(
                cfg.exports_dir / "top_technologies.csv",
                [
                    {
                        "technology_domain": row.get("technology_domain"),
                        "opportunity_score": row.get("opportunity_score"),
                        "evidence_count": row.get("evidence_count"),
                        "supporting_initiatives": "; ".join(row.get("supporting_initiatives", [])),
                    }
                    for row in technology_payload
                ],
                ["technology_domain", "opportunity_score", "evidence_count", "supporting_initiatives"],
            )
            _write_csv(
                cfg.exports_dir / "top_market_opportunities.csv",
                [
                    {
                        "market_domain": row.get("market_domain"),
                        "opportunity_score": row.get("opportunity_score"),
                        "evidence_count": row.get("evidence_count"),
                        "supporting_initiatives": "; ".join(row.get("supporting_initiatives", [])),
                    }
                    for row in market_payload
                ],
                ["market_domain", "opportunity_score", "evidence_count", "supporting_initiatives"],
            )
            _write_csv(
                cfg.exports_dir / "top_teams.csv",
                [
                    {
                        "initiative_id": row.get("initiative_id"),
                        "initiative_name": row.get("initiative_name"),
                        "team_strength": row.get("team_strength"),
                        "composite_score": row.get("composite_score"),
                        "outreach_now_score": row.get("outreach_now_score"),
                        "venture_upside_score": row.get("venture_upside_score"),
                        "supporting_signals": "; ".join(row.get("supporting_signals", [])),
                    }
                    for row in team_payload
                ],
                [
                    "initiative_id",
                    "initiative_name",
                    "team_strength",
                    "composite_score",
                    "outreach_now_score",
                    "venture_upside_score",
                    "supporting_signals",
                ],
            )

            _write_csv(
                cfg.exports_dir / "top_outreach_targets.csv",
                [
                    {
                        "initiative_name": row.get("initiative_name"),
                        "rank": row.get("rank"),
                        "outreach_now_score": row.get("score"),
                        "team_strength": row.get("team_strength"),
                        "market_opportunity": row.get("market_opportunity"),
                        "support_fit": row.get("support_fit"),
                    }
                    for row in outreach_payload
                ],
                ["initiative_name", "rank", "outreach_now_score", "team_strength", "market_opportunity", "support_fit"],
            )
            _write_csv(
                cfg.exports_dir / "top_venture_upside.csv",
                [
                    {
                        "initiative_name": row.get("initiative_name"),
                        "rank": row.get("rank"),
                        "venture_upside_score": row.get("score"),
                        "tech_depth": row.get("tech_depth"),
                        "market_opportunity": row.get("market_opportunity"),
                        "team_strength": row.get("team_strength"),
                    }
                    for row in upside_payload
                ],
                ["initiative_name", "rank", "venture_upside_score", "tech_depth", "market_opportunity", "team_strength"],
            )
            _write_csv(
                cfg.exports_dir / "top_talent_operators.csv",
                [
                    {
                        "person_name": row.get("person_name"),
                        "rank": row.get("rank"),
                        "talent_score": row.get("score"),
                        "evidence_count": row.get("evidence_count"),
                        "reasons": "; ".join(row.get("reasons", [])),
                    }
                    for row in talent_operator_payload
                ],
                ["person_name", "rank", "talent_score", "evidence_count", "reasons"],
            )
            _write_csv(
                cfg.exports_dir / "top_talent_alumni_angels.csv",
                [
                    {
                        "person_name": row.get("person_name"),
                        "rank": row.get("rank"),
                        "talent_score": row.get("score"),
                        "evidence_count": row.get("evidence_count"),
                        "reasons": "; ".join(row.get("reasons", [])),
                    }
                    for row in talent_alumni_payload
                ],
                ["person_name", "rank", "talent_score", "evidence_count", "reasons"],
            )

            details["technology_rankings"] = len(technology_payload)
            details["market_rankings"] = len(market_payload)
            details["team_rankings"] = len(team_payload)
            details["outreach_rankings"] = len(outreach_payload)
            details["upside_rankings"] = len(upside_payload)
            details["talent_operator_rankings"] = len(talent_operator_payload)
            details["talent_alumni_rankings"] = len(talent_alumni_payload)
            details["dd_investable_rankings"] = len(dd_investable_payload)
            details["dd_watchlist_rankings"] = len(dd_watchlist_payload)

            dossiers = build_initiative_dossiers(session=session)
            redacted_dossiers = _redact_dossiers(dossiers)
            _write_json(cfg.exports_dir / "initiative_dossiers.json", redacted_dossiers)
            details["dossiers_exported"] = len(dossiers)

            summary_lines = [
                "# Venture Scout Brief",
                "",
                "## Snapshot",
                f"- Total initiatives: {len(initiatives_master)}",
                f"- Top outreach targets exported: {len(outreach_payload)}",
                f"- Top venture upside exported: {len(upside_payload)}",
                f"- Top operators exported: {len(talent_operator_payload)}",
                f"- Top alumni angels exported: {len(talent_alumni_payload)}",
                f"- DD investable rankings exported: {len(dd_investable_payload)}",
                f"- DD watchlist rankings exported: {len(dd_watchlist_payload)}",
                f"- Investment memos exported: {len(dd_memo_payload)}",
                "",
                "## Top Outreach Targets",
            ]
            for item in outreach_payload[:15]:
                summary_lines.append(
                    f"{item['rank']}. {item['initiative_name']} (outreach {item['score']}, team {item.get('team_strength')}, market {item.get('market_opportunity')})"
                )

            summary_lines.extend(["", "## Top Venture Upside"])
            for item in upside_payload[:15]:
                summary_lines.append(
                    f"{item['rank']}. {item['initiative_name']} (upside {item['score']}, tech {item.get('tech_depth')})"
                )

            summary_lines.extend(["", "## Top Talent Operators"])
            for item in talent_operator_payload[:15]:
                summary_lines.append(
                    f"{item['rank']}. {item['person_name']} (score {item['score']}, evidence {item['evidence_count']})"
                )

            summary_lines.extend(["", "## Top Talent Alumni Angels"])
            for item in talent_alumni_payload[:15]:
                summary_lines.append(
                    f"{item['rank']}. {item['person_name']} (score {item['score']}, evidence {item['evidence_count']})"
                )

            summary_lines.extend(
                [
                    "",
                    "## DD Recommendations",
                ]
            )
            for item in dd_memo_payload[:15]:
                summary_lines.append(
                    f"- {item['initiative_name']}: {item['decision']} ({item['check_size_band']})"
                )

            summary_lines.extend(
                [
                    "",
                    "## Methodology Notes",
                    "- Deterministic heuristic scoring with strict evidence traceability",
                    "- Non-seed components without evidence contribute zero",
                    "- Seed ratings capped to 40% influence per core dimension",
                    "- Dual lens outputs: outreach_now and venture_upside",
                ]
            )

            report_path = cfg.reports_dir / "venture_scout_brief.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n".join(summary_lines), encoding="utf-8")

            dd_report_path = cfg.reports_dir / "due_diligence_brief.md"
            if not dd_report_path.exists() and dd_memo_payload:
                dd_lines = [
                    "# Due Diligence Brief",
                    "",
                    "## Recommendations",
                ]
                for item in dd_memo_payload[:15]:
                    dd_lines.append(f"- {item['initiative_name']}: {item['decision']} ({item['check_size_band']})")
                    dd_lines.append(f"  - Rationale: {item['rationale']}")
                    dd_lines.append(f"  - Risks: {', '.join(item['top_risks']) if item['top_risks'] else 'none'}")
                dd_report_path.write_text("\n".join(dd_lines), encoding="utf-8")

            # Keep legacy summary for backward compatibility.
            legacy_path = cfg.reports_dir / "phase1_summary.md"
            if not legacy_path.exists():
                legacy_path.write_text("\n".join(summary_lines), encoding="utf-8")

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
