"""Shared business logic for Scout API and MCP server."""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scout.enricher import enrich_github, enrich_team_page, enrich_website
from scout.models import CustomColumn, Enrichment, Initiative, OutreachScore, Project
from scout.scorer import LLMClient, score_initiative, score_project

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared field tuples
# ---------------------------------------------------------------------------

SCORE_FIELDS = (
    "verdict", "score", "classification", "reasoning", "contact_who",
    "contact_channel", "engagement_hook", "grade_team", "grade_team_num",
    "grade_tech", "grade_tech_num", "grade_opportunity", "grade_opportunity_num",
)

SCORE_RESPONSE_FIELDS = (
    "verdict", "score", "classification", "grade_team", "grade_tech", "grade_opportunity",
)

DETAIL_FIELDS = (
    "team_page", "team_size", "linkedin", "github_org", "key_repos",
    "sponsors", "competitions", "market_domains", "member_examples",
    "member_roles", "github_repo_count", "github_contributors",
    "github_commits_90d", "github_ci_present", "huggingface_model_hits",
    "openalex_hits", "semantic_scholar_hits", "dd_key_roles",
    "dd_references_count", "dd_is_investable", "profile_coverage_score",
    "known_url_count", "linkedin_hits", "researchgate_hits",
)

UPDATABLE_FIELDS = (
    "name", "uni", "sector", "mode", "description", "website", "email",
    "relevance", "team_page", "team_size", "linkedin", "github_org",
    "key_repos", "sponsors", "competitions",
)

PROJECT_SCORE_KEYS = (
    "verdict", "score", "classification",
    "grade_team", "grade_team_num", "grade_tech", "grade_tech_num",
    "grade_opportunity", "grade_opportunity_num",
)

_VERDICT_ORDER = {"reach_out_now": 3, "reach_out_soon": 2, "monitor": 1, "skip": 0}

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


_MISSING = object()


def json_parse(value: str | None, default: Any = _MISSING) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return {} if default is _MISSING else default


def latest_score_fields(scores: list[OutreachScore]) -> dict[str, Any]:
    if not scores:
        return {**{f: None for f in SCORE_FIELDS}, "key_evidence": [], "data_gaps": []}
    latest = max(scores, key=lambda s: s.scored_at)
    result = {f: getattr(latest, f) for f in SCORE_FIELDS}
    result["key_evidence"] = json_parse(latest.key_evidence_json, [])
    result["data_gaps"] = json_parse(latest.data_gaps_json, [])
    return result


def initiative_summary(init: Initiative) -> dict:
    enriched = bool(init.enrichments)
    enriched_at = max((e.fetched_at for e in init.enrichments), default=None) if enriched else None
    return {
        "id": init.id, "name": init.name, "uni": init.uni, "sector": init.sector,
        "mode": init.mode, "description": init.description, "website": init.website,
        "email": init.email, "relevance": init.relevance, "sheet_source": init.sheet_source,
        "enriched": enriched,
        "enriched_at": enriched_at.isoformat() if enriched_at else None,
        **latest_score_fields(init.scores),
        "technology_domains": init.technology_domains,
        "categories": init.categories,
        "member_count": init.member_count,
        "outreach_now_score": init.outreach_now_score,
        "venture_upside_score": init.venture_upside_score,
        "custom_fields": json_parse(init.custom_fields_json, {}),
    }


def initiative_detail(init: Initiative) -> dict:
    base = initiative_summary(init)
    base.update({f: getattr(init, f) for f in DETAIL_FIELDS})
    base["extra_links"] = json_parse(init.extra_links_json)
    base["enrichments"] = [
        {"id": e.id, "source_type": e.source_type, "summary": e.summary,
         "fetched_at": e.fetched_at.isoformat()}
        for e in init.enrichments
    ]
    base["projects"] = [project_summary(p) for p in init.projects]
    return base


def project_summary(proj: Project) -> dict:
    sf = latest_score_fields(proj.scores)
    return {
        "id": proj.id, "initiative_id": proj.initiative_id,
        "name": proj.name, "description": proj.description,
        "website": proj.website, "github_url": proj.github_url,
        "team": proj.team, "extra_links": json_parse(proj.extra_links_json),
        **{k: sf[k] for k in PROJECT_SCORE_KEYS},
    }


# ---------------------------------------------------------------------------
# Filtering and sorting
# ---------------------------------------------------------------------------


def filter_and_sort(
    items: list[dict], *, verdict=None, classification=None, uni=None, search=None,
    sort_by="score", sort_dir="desc",
) -> list[dict]:
    if verdict:
        vs = {v.strip().lower() for v in verdict.split(",")}
        items = [i for i in items if (i.get("verdict") or "unscored") in vs]
    if classification:
        cs = {c.strip().lower() for c in classification.split(",")}
        items = [i for i in items if (i.get("classification") or "") in cs]
    if uni:
        us = {u.strip().upper() for u in uni.split(",")}
        items = [i for i in items if i["uni"].upper() in us]
    if search:
        q = search.lower()
        items = [i for i in items if q in i["name"].lower()
                 or q in i.get("description", "").lower() or q in i.get("sector", "").lower()]

    def sort_key(item: dict):
        if sort_by == "score":
            return item["score"] if item["score"] is not None else -1
        if sort_by == "name":
            return item["name"].lower()
        if sort_by == "uni":
            return item["uni"].lower()
        if sort_by == "verdict":
            return _VERDICT_ORDER.get(item.get("verdict") or "", -1)
        if sort_by in ("grade_team", "grade_tech", "grade_opportunity"):
            return item.get(f"{sort_by}_num") or 99
        return item["name"].lower()

    items.sort(key=sort_key, reverse=(sort_dir == "desc"))
    return items


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


def apply_updates(obj, updates: dict[str, Any], fields: tuple[str, ...]) -> None:
    """Apply non-None values from updates dict to an ORM object."""
    for field in fields:
        val = updates.get(field)
        if val is not None:
            setattr(obj, field, val)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def run_enrichment(session: Session, init: Initiative) -> list[Enrichment]:
    """Run all enrichers; only delete old enrichments if at least one succeeds.
    Returns new enrichments (caller must commit)."""
    new_enrichments: list[Enrichment] = []
    for enrich_fn in (enrich_website, enrich_team_page, enrich_github):
        try:
            result = await enrich_fn(init)
            if result:
                new_enrichments.append(result)
        except Exception as exc:
            log.warning("Enrichment failed (%s) for %s: %s", enrich_fn.__name__, init.name, exc)
    if new_enrichments:
        session.execute(delete(Enrichment).where(Enrichment.initiative_id == init.id))
        for e in new_enrichments:
            session.add(e)
    return new_enrichments


async def run_scoring(
    session: Session, init: Initiative, client: LLMClient | None = None,
) -> OutreachScore:
    """Score an initiative, replacing existing initiative-level scores (caller must commit)."""
    if client is None:
        client = LLMClient()
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == init.id)
    ).scalars().all()
    outreach = await score_initiative(init, list(enrichments), client)
    session.execute(delete(OutreachScore).where(
        OutreachScore.initiative_id == init.id,
        OutreachScore.project_id.is_(None),
    ))
    session.add(outreach)
    return outreach


async def run_project_scoring(
    session: Session, proj: Project, init: Initiative, client: LLMClient | None = None,
) -> OutreachScore:
    """Score a project, replacing existing project scores (caller must commit)."""
    if client is None:
        client = LLMClient()
    outreach = await score_project(proj, init, client)
    session.execute(delete(OutreachScore).where(OutreachScore.project_id == proj.id))
    session.add(outreach)
    return outreach


def compute_stats(session: Session) -> dict:
    initiatives = session.execute(select(Initiative)).scalars().all()
    by_verdict: Counter[str] = Counter()
    by_classification: Counter[str] = Counter()
    by_uni: Counter[str] = Counter()
    enriched = scored = 0
    for init in initiatives:
        by_uni[init.uni or "Unknown"] += 1
        if init.enrichments:
            enriched += 1
        if init.scores:
            scored += 1
            latest = max(init.scores, key=lambda s: s.scored_at)
            by_verdict[latest.verdict] += 1
            by_classification[latest.classification] += 1
    return {
        "total": len(initiatives), "enriched": enriched, "scored": scored,
        "by_verdict": dict(by_verdict), "by_classification": dict(by_classification),
        "by_uni": dict(by_uni),
    }


def get_custom_columns(session: Session) -> list[dict]:
    """Fetch custom column definitions for the current database."""
    cols = session.execute(
        select(CustomColumn).order_by(CustomColumn.sort_order)
    ).scalars().all()
    return [
        {"id": c.id, "key": c.key, "label": c.label, "col_type": c.col_type,
         "show_in_list": c.show_in_list, "sort_order": c.sort_order}
        for c in cols
    ]
