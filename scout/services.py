"""Shared business logic for Scout API and MCP server."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select
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
# SQL-based list query
# ---------------------------------------------------------------------------


def _latest_score_subquery():
    """Subquery returning the latest initiative-level score per initiative."""
    return (
        select(
            OutreachScore.initiative_id,
            OutreachScore.verdict,
            OutreachScore.score,
            OutreachScore.classification,
            OutreachScore.reasoning,
            OutreachScore.contact_who,
            OutreachScore.contact_channel,
            OutreachScore.engagement_hook,
            OutreachScore.key_evidence_json,
            OutreachScore.data_gaps_json,
            OutreachScore.grade_team,
            OutreachScore.grade_team_num,
            OutreachScore.grade_tech,
            OutreachScore.grade_tech_num,
            OutreachScore.grade_opportunity,
            OutreachScore.grade_opportunity_num,
            OutreachScore.scored_at,
            func.row_number()
            .over(
                partition_by=OutreachScore.initiative_id,
                order_by=OutreachScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .where(OutreachScore.project_id.is_(None))
        .subquery()
    )


def query_initiatives(
    session: Session,
    *,
    verdict: str | None = None,
    classification: str | None = None,
    uni: str | None = None,
    search: str | None = None,
    sort_by: str = "score",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 200,
) -> tuple[list[dict], int]:
    """Return (items, total) with filtering, sorting, and pagination in SQL."""
    ls = _latest_score_subquery()

    # Enrichment aggregates as a subquery
    enrich_sub = (
        select(
            Enrichment.initiative_id,
            func.count(Enrichment.id).label("enrich_count"),
            func.max(Enrichment.fetched_at).label("enrich_latest"),
        )
        .group_by(Enrichment.initiative_id)
        .subquery()
    )

    # Base query: Initiative LEFT JOIN latest score + enrichment aggregates
    base = (
        select(
            Initiative,
            ls.c.verdict.label("ls_verdict"),
            ls.c.score.label("ls_score"),
            ls.c.classification.label("ls_classification"),
            ls.c.reasoning.label("ls_reasoning"),
            ls.c.contact_who.label("ls_contact_who"),
            ls.c.contact_channel.label("ls_contact_channel"),
            ls.c.engagement_hook.label("ls_engagement_hook"),
            ls.c.key_evidence_json.label("ls_key_evidence_json"),
            ls.c.data_gaps_json.label("ls_data_gaps_json"),
            ls.c.grade_team.label("ls_grade_team"),
            ls.c.grade_team_num.label("ls_grade_team_num"),
            ls.c.grade_tech.label("ls_grade_tech"),
            ls.c.grade_tech_num.label("ls_grade_tech_num"),
            ls.c.grade_opportunity.label("ls_grade_opportunity"),
            ls.c.grade_opportunity_num.label("ls_grade_opportunity_num"),
            func.coalesce(enrich_sub.c.enrich_count, 0).label("enrich_count"),
            enrich_sub.c.enrich_latest.label("enrich_latest"),
        )
        .outerjoin(ls, and_(Initiative.id == ls.c.initiative_id, ls.c.rn == 1))
        .outerjoin(enrich_sub, Initiative.id == enrich_sub.c.initiative_id)
    )

    # -- Filters --
    if verdict:
        vs = {v.strip().lower() for v in verdict.split(",")}
        conditions = []
        if "unscored" in vs:
            vs.discard("unscored")
            conditions.append(ls.c.verdict.is_(None))
        if vs:
            conditions.append(func.lower(ls.c.verdict).in_(vs))
        if conditions:
            base = base.where(or_(*conditions))

    if classification:
        cs = {c.strip().lower() for c in classification.split(",")}
        base = base.where(func.lower(ls.c.classification).in_(cs))

    if uni:
        us = {u.strip().upper() for u in uni.split(",")}
        base = base.where(func.upper(Initiative.uni).in_(us))

    if search:
        q = f"%{search.lower()}%"
        base = base.where(or_(
            func.lower(Initiative.name).like(q),
            func.lower(Initiative.description).like(q),
            func.lower(Initiative.sector).like(q),
        ))

    # -- Count total before pagination --
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0

    # -- Sort --
    verdict_order = case(
        (ls.c.verdict == "reach_out_now", 3),
        (ls.c.verdict == "reach_out_soon", 2),
        (ls.c.verdict == "monitor", 1),
        (ls.c.verdict == "skip", 0),
        else_=-1,
    )
    sort_map = {
        "score": func.coalesce(ls.c.score, -1),
        "name": func.lower(Initiative.name),
        "uni": func.lower(Initiative.uni),
        "verdict": verdict_order,
        "grade_team": func.coalesce(ls.c.grade_team_num, 99),
        "grade_tech": func.coalesce(ls.c.grade_tech_num, 99),
        "grade_opportunity": func.coalesce(ls.c.grade_opportunity_num, 99),
    }
    sort_col = sort_map.get(sort_by, func.lower(Initiative.name))
    base = base.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    # -- Pagination --
    offset = (page - 1) * per_page
    base = base.limit(per_page).offset(offset)

    # -- Execute and build result dicts --
    rows = session.execute(base).all()
    items = []
    for row in rows:
        init = row[0]
        items.append({
            "id": init.id, "name": init.name, "uni": init.uni, "sector": init.sector,
            "mode": init.mode, "description": init.description, "website": init.website,
            "email": init.email, "relevance": init.relevance, "sheet_source": init.sheet_source,
            "enriched": row.enrich_count > 0,
            "enriched_at": row.enrich_latest.isoformat() if row.enrich_latest else None,
            "verdict": row.ls_verdict, "score": row.ls_score,
            "classification": row.ls_classification,
            "reasoning": row.ls_reasoning,
            "contact_who": row.ls_contact_who,
            "contact_channel": row.ls_contact_channel,
            "engagement_hook": row.ls_engagement_hook,
            "key_evidence": json_parse(row.ls_key_evidence_json, []),
            "data_gaps": json_parse(row.ls_data_gaps_json, []),
            "grade_team": row.ls_grade_team,
            "grade_team_num": row.ls_grade_team_num,
            "grade_tech": row.ls_grade_tech,
            "grade_tech_num": row.ls_grade_tech_num,
            "grade_opportunity": row.ls_grade_opportunity,
            "grade_opportunity_num": row.ls_grade_opportunity_num,
            "technology_domains": init.technology_domains,
            "categories": init.categories,
            "member_count": init.member_count,
            "outreach_now_score": init.outreach_now_score,
            "venture_upside_score": init.venture_upside_score,
            "custom_fields": json_parse(init.custom_fields_json, {}),
        })

    return items, total


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
    """Aggregate statistics computed in SQL (no N+1 queries)."""
    total = session.execute(select(func.count(Initiative.id))).scalar() or 0

    enriched = session.execute(
        select(func.count(func.distinct(Enrichment.initiative_id)))
    ).scalar() or 0

    # Latest initiative-level score per initiative
    ls = _latest_score_subquery()
    latest = select(ls.c.initiative_id, ls.c.verdict, ls.c.classification).where(ls.c.rn == 1).subquery()

    scored = session.execute(select(func.count()).select_from(latest)).scalar() or 0

    by_verdict = dict(session.execute(
        select(latest.c.verdict, func.count()).group_by(latest.c.verdict)
    ).all())

    by_classification = dict(session.execute(
        select(latest.c.classification, func.count()).group_by(latest.c.classification)
    ).all())

    uni_col = case((Initiative.uni == "", "Unknown"), else_=Initiative.uni)
    by_uni = dict(session.execute(
        select(uni_col, func.count()).group_by(uni_col)
    ).all())

    return {
        "total": total, "enriched": enriched, "scored": scored,
        "by_verdict": by_verdict, "by_classification": by_classification,
        "by_uni": by_uni,
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
