from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import Initiative, Person, Ranking, Score, Signal, TalentScore
from initiative_tracker.store import get_json_list
from initiative_tracker.utils import to_json, unique_list, utc_now


def _latest_scores(scores: list[Score]) -> dict[int, Score]:
    ordered = sorted(scores, key=lambda row: row.scored_at, reverse=True)
    latest: dict[int, Score] = {}
    for score in ordered:
        latest.setdefault(score.initiative_id, score)
    return latest


def _latest_talent(rows: list[TalentScore]) -> dict[int, TalentScore]:
    ordered = sorted(rows, key=lambda row: row.scored_at, reverse=True)
    latest: dict[int, TalentScore] = {}
    for row in ordered:
        latest.setdefault(row.person_id, row)
    return latest


def _normalize_domain(
    raw: str,
    *,
    canonical_domains: set[str],
    alias_map: dict[str, str],
    keyword_map: dict[str, str],
) -> str | None:
    value = raw.strip()
    if not value:
        return None
    key = value.casefold()
    if key in canonical_domains:
        return key
    alias = alias_map.get(key)
    if alias:
        return alias
    for keyword, domain in keyword_map.items():
        if keyword in key:
            return domain
    return None


def _taxonomy_keyword_map(taxonomy: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for domain, keywords in taxonomy.items():
        out[domain.casefold()] = domain
        for keyword in keywords:
            out[keyword.casefold()] = domain
    return out


def rank_initiatives(top_n: int = 15, settings: Settings | None = None, db_url: str | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    tech_taxonomy = cfg.load_technology_taxonomy()
    market_taxonomy = cfg.load_market_taxonomy()
    tech_aliases = cfg.load_technology_aliases()
    market_aliases = cfg.load_market_aliases()
    tech_keywords = _taxonomy_keyword_map(tech_taxonomy)
    market_keywords = _taxonomy_keyword_map(market_taxonomy)

    details: dict[str, Any] = {
        "top_n": top_n,
        "team_items": 0,
        "technology_items": 0,
        "market_items": 0,
        "outreach_items": 0,
        "upside_items": 0,
        "talent_operator_items": 0,
        "talent_alumni_items": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "rank")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            scores = session.execute(select(Score)).scalars().all()
            signals = session.execute(select(Signal)).scalars().all()
            people = session.execute(select(Person)).scalars().all()
            talent_scores = session.execute(select(TalentScore)).scalars().all()

            latest = _latest_scores(scores)
            if not latest:
                finish_pipeline_run(session, run, status="success", details=details)
                return details

            initiative_by_id = {initiative.id: initiative for initiative in initiatives}

            tech_evidence: dict[tuple[int, str], int] = defaultdict(int)
            market_evidence: dict[tuple[int, str], int] = defaultdict(int)
            tech_domains_by_initiative: dict[int, set[str]] = defaultdict(set)
            market_domains_by_initiative: dict[int, set[str]] = defaultdict(set)
            for signal in signals:
                if signal.signal_type == "technology_domain":
                    domain = _normalize_domain(
                        signal.signal_key,
                        canonical_domains={d.casefold() for d in tech_taxonomy},
                        alias_map=tech_aliases,
                        keyword_map=tech_keywords,
                    )
                    if not domain:
                        continue
                    tech_evidence[(signal.initiative_id, domain)] += 1
                    tech_domains_by_initiative[signal.initiative_id].add(domain)
                elif signal.signal_type == "market_domain":
                    domain = _normalize_domain(
                        signal.signal_key,
                        canonical_domains={d.casefold() for d in market_taxonomy},
                        alias_map=market_aliases,
                        keyword_map=market_keywords,
                    )
                    if not domain:
                        continue
                    market_evidence[(signal.initiative_id, domain)] += 1
                    market_domains_by_initiative[signal.initiative_id].add(domain)

            generated_at = utc_now()

            # Legacy teams ranking (backward compatibility).
            team_rows = sorted(
                latest.values(),
                key=lambda score: (score.team_strength, score.composite_score),
                reverse=True,
            )[:top_n]

            for index, score in enumerate(team_rows, start=1):
                initiative = initiative_by_id.get(score.initiative_id)
                if initiative is None:
                    continue
                ranking = Ranking(
                    ranking_type="teams",
                    item_key=str(initiative.id),
                    item_name=initiative.canonical_name,
                    rank_position=index,
                    score=round(score.team_strength, 4),
                    supporting_initiatives_json=initiative.team_signals_json,
                    evidence_count=len(get_json_list(initiative.team_signals_json)),
                    item_meta_json=to_json(
                        {
                            "legacy_composite": round(score.composite_score, 4),
                            "outreach_now_score": round(score.outreach_now_score, 4),
                            "venture_upside_score": round(score.venture_upside_score, 4),
                        }
                    ),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["team_items"] += 1

            # Outreach lens ranking.
            outreach_rows = sorted(latest.values(), key=lambda score: score.outreach_now_score, reverse=True)[:top_n]
            for index, score in enumerate(outreach_rows, start=1):
                initiative = initiative_by_id.get(score.initiative_id)
                if not initiative:
                    continue
                ranking = Ranking(
                    ranking_type="outreach_targets",
                    item_key=str(initiative.id),
                    item_name=initiative.canonical_name,
                    rank_position=index,
                    score=round(score.outreach_now_score, 4),
                    supporting_initiatives_json=to_json([initiative.canonical_name]),
                    evidence_count=1,
                    item_meta_json=to_json(
                        {
                            "team_strength": round(score.team_strength, 4),
                            "market_opportunity": round(score.market_opportunity, 4),
                            "support_fit": round(score.support_fit, 4),
                        }
                    ),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["outreach_items"] += 1

            # Venture upside lens ranking.
            upside_rows = sorted(latest.values(), key=lambda score: score.venture_upside_score, reverse=True)[:top_n]
            for index, score in enumerate(upside_rows, start=1):
                initiative = initiative_by_id.get(score.initiative_id)
                if not initiative:
                    continue
                ranking = Ranking(
                    ranking_type="venture_upside",
                    item_key=str(initiative.id),
                    item_name=initiative.canonical_name,
                    rank_position=index,
                    score=round(score.venture_upside_score, 4),
                    supporting_initiatives_json=to_json([initiative.canonical_name]),
                    evidence_count=1,
                    item_meta_json=to_json(
                        {
                            "tech_depth": round(score.tech_depth, 4),
                            "market_opportunity": round(score.market_opportunity, 4),
                            "team_strength": round(score.team_strength, 4),
                        }
                    ),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["upside_items"] += 1

            tech_agg: dict[str, dict[str, Any]] = {}
            for initiative_id, score in latest.items():
                initiative = initiative_by_id.get(initiative_id)
                if initiative is None:
                    continue

                technologies = get_json_list(initiative.technologies_json)
                mapped_domains = []
                for item in technologies:
                    domain = _normalize_domain(
                        item,
                        canonical_domains={d.casefold() for d in tech_taxonomy},
                        alias_map=tech_aliases,
                        keyword_map=tech_keywords,
                    )
                    if domain:
                        mapped_domains.append(domain)
                domains = unique_list([*mapped_domains, *sorted(tech_domains_by_initiative.get(initiative.id, set()))])
                if not domains:
                    continue
                for domain in domains:
                    contribution = (
                        (0.5 * score.tech_depth + 0.3 * score.market_opportunity + 0.2 * score.team_strength)
                        * (0.5 + 0.5 * score.confidence_tech)
                    )
                    entry = tech_agg.setdefault(
                        domain,
                        {"total": 0.0, "count": 0, "supports": [], "evidence_count": 0},
                    )
                    entry["total"] += contribution
                    entry["count"] += 1
                    entry["supports"].append((contribution, initiative.canonical_name))
                    entry["evidence_count"] += tech_evidence.get((initiative.id, domain), 0)

            sorted_tech = sorted(
                ((domain, payload) for domain, payload in tech_agg.items() if payload["count"] > 0),
                key=lambda row: row[1]["total"] / row[1]["count"],
                reverse=True,
            )[:top_n]

            for index, (domain, payload) in enumerate(sorted_tech, start=1):
                support_names = [name for _, name in sorted(payload["supports"], reverse=True)[:10]]
                ranking = Ranking(
                    ranking_type="technologies",
                    item_key=domain,
                    item_name=domain,
                    rank_position=index,
                    score=round(payload["total"] / payload["count"], 4),
                    supporting_initiatives_json=to_json(support_names),
                    evidence_count=int(payload["evidence_count"]),
                    item_meta_json=to_json({"canonical": True}),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["technology_items"] += 1

            market_agg: dict[str, dict[str, Any]] = {}
            for initiative_id, score in latest.items():
                initiative = initiative_by_id.get(initiative_id)
                if initiative is None:
                    continue

                markets = get_json_list(initiative.markets_json)
                mapped_markets = []
                for item in markets:
                    domain = _normalize_domain(
                        item,
                        canonical_domains={d.casefold() for d in market_taxonomy},
                        alias_map=market_aliases,
                        keyword_map=market_keywords,
                    )
                    if domain:
                        mapped_markets.append(domain)

                canonical_markets = unique_list([*mapped_markets, *sorted(market_domains_by_initiative.get(initiative.id, set()))])
                if not canonical_markets:
                    continue
                for market in canonical_markets:
                    contribution = (
                        (0.55 * score.market_opportunity + 0.25 * score.tech_depth + 0.2 * score.team_strength)
                        * (0.5 + 0.5 * score.confidence_market)
                    )
                    entry = market_agg.setdefault(
                        market,
                        {"total": 0.0, "count": 0, "supports": [], "evidence_count": 0},
                    )
                    entry["total"] += contribution
                    entry["count"] += 1
                    entry["supports"].append((contribution, initiative.canonical_name))
                    entry["evidence_count"] += market_evidence.get((initiative.id, market), 0)

            sorted_market = sorted(
                ((market, payload) for market, payload in market_agg.items() if payload["count"] > 0),
                key=lambda row: row[1]["total"] / row[1]["count"],
                reverse=True,
            )[:top_n]

            for index, (market, payload) in enumerate(sorted_market, start=1):
                support_names = [name for _, name in sorted(payload["supports"], reverse=True)[:10]]
                ranking = Ranking(
                    ranking_type="market_opportunities",
                    item_key=market,
                    item_name=market,
                    rank_position=index,
                    score=round(payload["total"] / payload["count"], 4),
                    supporting_initiatives_json=to_json(support_names),
                    evidence_count=int(payload["evidence_count"]),
                    item_meta_json=to_json({"canonical": True}),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["market_items"] += 1

            latest_talent = _latest_talent(talent_scores)
            person_by_id = {person.id: person for person in people}

            operators = [
                row
                for row in latest_talent.values()
                if row.talent_type == "operators" and person_by_id.get(row.person_id) is not None
            ]
            operators.sort(key=lambda row: row.composite_score, reverse=True)
            for index, row in enumerate(operators[:top_n], start=1):
                person = person_by_id[row.person_id]
                ranking = Ranking(
                    ranking_type="talent_operators",
                    item_key=str(person.id),
                    item_name=person.canonical_name,
                    rank_position=index,
                    score=round(row.composite_score, 4),
                    supporting_initiatives_json=to_json(get_json_list(person.source_urls_json)[:8]),
                    evidence_count=len(get_json_list(person.source_urls_json)),
                    item_meta_json=to_json({"contact_channels": get_json_list(person.contact_channels_json)[:6], "reasons": get_json_list(row.reasons_json)}),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["talent_operator_items"] += 1

            alumni = [
                row
                for row in latest_talent.values()
                if row.talent_type == "alumni_angels" and person_by_id.get(row.person_id) is not None
            ]
            alumni.sort(key=lambda row: row.composite_score, reverse=True)
            for index, row in enumerate(alumni[:top_n], start=1):
                person = person_by_id[row.person_id]
                ranking = Ranking(
                    ranking_type="talent_alumni_angels",
                    item_key=str(person.id),
                    item_name=person.canonical_name,
                    rank_position=index,
                    score=round(row.composite_score, 4),
                    supporting_initiatives_json=to_json(get_json_list(person.source_urls_json)[:8]),
                    evidence_count=len(get_json_list(person.source_urls_json)),
                    item_meta_json=to_json({"contact_channels": get_json_list(person.contact_channels_json)[:6], "reasons": get_json_list(row.reasons_json)}),
                    top_n=top_n,
                    generated_at=generated_at,
                )
                session.add(ranking)
                details["talent_alumni_items"] += 1

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
