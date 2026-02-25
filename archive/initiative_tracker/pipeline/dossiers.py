from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from initiative_tracker.db import session_scope
from initiative_tracker.models import (
    Initiative,
    InitiativePerson,
    InitiativeStatus,
    Person,
    Score,
    ScoreComponent,
    ScoreEvidence,
    TalentScore,
)
from initiative_tracker.store import get_json_list, upsert_initiative_action
from initiative_tracker.utils import clip, unique_list


def _latest_by_key(rows: list[Any], key: str, time_attr: str) -> dict[Any, Any]:
    ordered = sorted(rows, key=lambda row: getattr(row, time_attr), reverse=True)
    latest: dict[Any, Any] = {}
    for row in ordered:
        latest.setdefault(getattr(row, key), row)
    return latest


def _technology_stage(technology: str, team_signals: list[str]) -> str:
    text = (technology + " " + " ".join(team_signals)).casefold()
    if any(token in text for token in ["winner", "record", "competition", "preis", "sieger"]):
        return "competition_validated"
    if any(token in text for token in ["pilot", "customer", "commercial", "startup", "spinout"]):
        return "pilot_or_commercial"
    if any(token in text for token in ["prototype", "system", "stack", "machine"]):
        return "prototype"
    return "research"


def _build_playbook(
    score: Score | None,
    people: list[dict[str, Any]],
    risk_flags: list[str],
) -> dict[str, Any]:
    outreach_score = float(score.outreach_now_score) if score else 0.0
    upside_score = float(score.venture_upside_score) if score else 0.0

    primary_contact = people[0]["name"] if people else None
    why_now = (
        f"Outreach now score {outreach_score:.2f}/5 with upside {upside_score:.2f}/5. "
        "Team shows momentum and near-term support potential."
    )
    if "no_named_contact" in risk_flags:
        why_now = "High potential but contact discovery needed before engagement."

    support = [
        "Compute credits for prototyping/training",
        "Customer introductions to pilot partners",
        "Angel syndicate preparation",
    ]
    if score and score.tech_depth > 4.2:
        support.append("Technical diligence and IP positioning")

    return {
        "why_now": why_now,
        "primary_contact": primary_contact,
        "recommended_support": unique_list(support),
        "first_meeting_goal": "Confirm spinout intent, timing, and highest-value support gap.",
        "next_30_days": [
            "Send warm outreach with specific achievement reference",
            "Run 20-minute discovery call",
            "Map blocker to support package",
            "Set follow-up milestone in 30 days",
        ],
    }


def _build_initiative_dossiers_in_session(session: Session, *, top_n: int | None = None) -> list[dict[str, Any]]:
    initiatives = session.execute(select(Initiative)).scalars().all()
    scores = session.execute(select(Score)).scalars().all()
    components = session.execute(select(ScoreComponent)).scalars().all()
    evidences = session.execute(select(ScoreEvidence)).scalars().all()
    people = session.execute(select(Person)).scalars().all()
    links = session.execute(select(InitiativePerson)).scalars().all()
    talent_scores = session.execute(select(TalentScore)).scalars().all()
    statuses = session.execute(select(InitiativeStatus)).scalars().all()

    latest_scores = _latest_by_key(scores, "initiative_id", "scored_at")
    latest_talent = _latest_by_key(talent_scores, "person_id", "scored_at")
    status_by_initiative = _latest_by_key(statuses, "initiative_id", "updated_at")

    components_by_initiative: dict[int, list[ScoreComponent]] = defaultdict(list)
    for component in components:
        components_by_initiative[component.initiative_id].append(component)

    evidence_by_component: dict[int, list[ScoreEvidence]] = defaultdict(list)
    for evidence in evidences:
        evidence_by_component[evidence.score_component_id].append(evidence)

    person_by_id = {person.id: person for person in people}
    links_by_initiative: dict[int, list[InitiativePerson]] = defaultdict(list)
    for link in links:
        links_by_initiative[link.initiative_id].append(link)

    rows = sorted(
        initiatives,
        key=lambda item: (latest_scores.get(item.id).outreach_now_score if latest_scores.get(item.id) else 0.0),
        reverse=True,
    )
    if top_n:
        rows = rows[:top_n]

    dossiers: list[dict[str, Any]] = []
    for initiative in rows:
        score = latest_scores.get(initiative.id)
        initiative_components = components_by_initiative.get(initiative.id, [])

        component_payload: list[dict[str, Any]] = []
        evidence_total = 0
        for component in sorted(initiative_components, key=lambda row: (row.dimension, row.component_key)):
            component_evidence = evidence_by_component.get(component.id, [])
            evidence_total += len(component_evidence)
            component_payload.append(
                {
                    "initiative_id": initiative.id,
                    "dimension": component.dimension,
                    "component_key": component.component_key,
                    "raw_value": round(component.raw_value, 4),
                    "normalized_value": round(component.normalized_value, 4),
                    "weight": round(component.weight, 4),
                    "weighted_contribution": round(component.weighted_contribution, 4),
                    "evidence_refs": [
                        {
                            "source_url": row.source_url,
                            "snippet": row.snippet,
                            "signal_type": row.signal_type,
                            "signal_key": row.signal_key,
                            "value": round(row.value, 4),
                        }
                        for row in component_evidence
                    ],
                    "source_mix": get_json_list(component.source_mix_json),
                    "confidence": round(component.confidence, 4),
                }
            )

        linked_people: list[dict[str, Any]] = []
        for link in sorted(links_by_initiative.get(initiative.id, []), key=lambda row: (not row.is_primary_contact, row.role)):
            person = person_by_id.get(link.person_id)
            if not person:
                continue
            talent = latest_talent.get(person.id)
            linked_people.append(
                {
                    "person_id": person.id,
                    "name": person.canonical_name,
                    "person_type": person.person_type,
                    "roles": [link.role],
                    "initiative_ids": [initiative.id],
                    "contact_channels": get_json_list(person.contact_channels_json),
                    "evidence_count": len(get_json_list(person.source_urls_json)),
                    "confidence": round(person.confidence, 4),
                    "why_ranked": get_json_list(talent.reasons_json) if talent else [],
                    "talent_score": round(talent.composite_score, 4) if talent else None,
                }
            )

        technologies = get_json_list(initiative.technologies_json)
        team_signals = get_json_list(initiative.team_signals_json)
        tech_profile = [
            {
                "technology_domain": tech,
                "stage": _technology_stage(tech, team_signals),
                "confidence": round(clip((len(tech) / 24.0), 0.2, 1.0), 4),
            }
            for tech in technologies
        ]

        markets = get_json_list(initiative.markets_json)
        market_profile = [{"market_domain": market, "near_term_applicability": "medium"} for market in markets]

        risk_flags: list[str] = []
        if evidence_total < 6:
            risk_flags.append("low_evidence")
        if not linked_people:
            risk_flags.append("no_named_contact")
        if score and (score.confidence_tech + score.confidence_market + score.confidence_team + score.confidence_maturity) / 4 < 0.4:
            risk_flags.append("seed_dependency_high")
        if score and score.outreach_now_score < 2.2:
            risk_flags.append("stale_activity")

        playbook = _build_playbook(score, linked_people, risk_flags)
        primary_contact_id = linked_people[0]["person_id"] if linked_people else None
        upsert_initiative_action(
            session,
            initiative_id=initiative.id,
            lens="outreach",
            why_now=playbook["why_now"],
            primary_contact_person_id=primary_contact_id,
            recommended_support=playbook["recommended_support"],
            first_meeting_goal=playbook["first_meeting_goal"],
            next_30_days=playbook["next_30_days"],
            risk_flags=risk_flags,
        )

        status_row = status_by_initiative.get(initiative.id)
        status_payload = {
            "status": status_row.status if status_row else "new",
            "owner": status_row.owner if status_row else "",
            "last_contact_at": status_row.last_contact_at.isoformat() if status_row and status_row.last_contact_at else None,
            "next_step_date": status_row.next_step_date.isoformat() if status_row and status_row.next_step_date else None,
            "notes": status_row.notes if status_row else "",
        }

        dossiers.append(
            {
                "initiative_id": initiative.id,
                "initiative_name": initiative.canonical_name,
                "university": initiative.university,
                "lens_scores": {
                    "outreach_now_score": round(score.outreach_now_score, 4) if score else None,
                    "venture_upside_score": round(score.venture_upside_score, 4) if score else None,
                    "legacy_composite": round(score.composite_score, 4) if score else None,
                },
                "score_breakdown": component_payload,
                "technology_profile": tech_profile,
                "market_profile": market_profile,
                "top_talent": linked_people,
                "action_playbook": playbook,
                "risk_flags": risk_flags,
                "pipeline_status": status_payload,
                "description_summary_en": initiative.description_summary_en,
                "source_urls": [],
            }
        )

    return dossiers


def build_initiative_dossiers(
    *,
    db_url: str | None = None,
    top_n: int | None = None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    if session is not None:
        return _build_initiative_dossiers_in_session(session, top_n=top_n)

    with session_scope(db_url) as managed_session:
        return _build_initiative_dossiers_in_session(managed_session, top_n=top_n)
