"""Shared business logic for Scout API and MCP server."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, text
from sqlalchemy.orm import Session

from scout.enricher import (
    discover_urls, enrich_extra_links, enrich_github, enrich_team_page, enrich_website,
)
from scout.models import CustomColumn, Enrichment, Initiative, OutreachScore, Project, ScoringPrompt
from scout.scorer import LLMClient, score_initiative, score_project
from scout.utils import json_parse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared entity lookup
# ---------------------------------------------------------------------------


def get_entity(session: Session, model, entity_id: int):
    """Fetch an entity by primary key. Returns the object or None."""
    return session.execute(
        select(model).where(model.id == entity_id)
    ).scalars().first()


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
    "name", "uni", "faculty", "sector", "mode", "description", "website", "email",
    "relevance", "team_page", "team_size", "linkedin", "github_org",
    "key_repos", "sponsors", "competitions",
)

PROJECT_SCORE_KEYS = (
    "verdict", "score", "classification",
    "grade_team", "grade_team_num", "grade_tech", "grade_tech_num",
    "grade_opportunity", "grade_opportunity_num",
)

# Fields allowed in compact list mode (the `fields` parameter).
COMPACT_FIELDS = {
    "id", "name", "uni", "faculty", "sector", "mode", "description",
    "website", "email", "relevance", "sheet_source",
    "enriched", "enriched_at",
    "verdict", "score", "classification",
    "grade_team", "grade_tech", "grade_opportunity",
    "technology_domains", "categories", "member_count",
    "outreach_now_score", "venture_upside_score",
    "custom_fields",
}

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def score_response_dict(outreach: OutreachScore, extended: bool = False) -> dict[str, Any]:
    """Build a dict from an OutreachScore object.

    Args:
        outreach: The score object.
        extended: If True, include reasoning, contact info, evidence, and data gaps.
    """
    result = {f: getattr(outreach, f) for f in SCORE_RESPONSE_FIELDS}
    if extended:
        result.update({
            "reasoning": outreach.reasoning,
            "contact_who": outreach.contact_who,
            "contact_channel": outreach.contact_channel,
            "engagement_hook": outreach.engagement_hook,
            "key_evidence": json_parse(outreach.key_evidence_json, []),
            "data_gaps": json_parse(outreach.data_gaps_json, []),
        })
    return result


def latest_score_fields(scores: list[OutreachScore]) -> dict[str, Any]:
    if not scores:
        return {**{f: None for f in SCORE_FIELDS}, "key_evidence": [], "data_gaps": []}
    latest = max(scores, key=lambda s: s.scored_at)
    result = {f: getattr(latest, f) for f in SCORE_FIELDS}
    result["key_evidence"] = json_parse(latest.key_evidence_json, [])
    result["data_gaps"] = json_parse(latest.data_gaps_json, [])
    return result


# Base initiative fields read directly from the Initiative ORM object.
_SUMMARY_BASE_FIELDS = (
    "id", "name", "uni", "faculty", "sector", "mode", "description",
    "website", "email", "relevance", "sheet_source",
)
_SUMMARY_EXTRA_FIELDS = (
    "technology_domains", "categories", "member_count",
    "outreach_now_score", "venture_upside_score",
)


def _build_initiative_dict(
    init: Initiative,
    enriched: bool,
    enriched_at_iso: str | None,
    score_fields: dict[str, Any],
) -> dict:
    """Assemble the standard initiative summary dict from pre-computed parts.

    This is the single source of truth for the initiative list-view shape,
    used by both ``initiative_summary`` (ORM-based) and ``query_initiatives``
    (SQL-based).
    """
    result: dict[str, Any] = {f: getattr(init, f) for f in _SUMMARY_BASE_FIELDS}
    result["enriched"] = enriched
    result["enriched_at"] = enriched_at_iso
    result.update(score_fields)
    for f in _SUMMARY_EXTRA_FIELDS:
        result[f] = getattr(init, f)
    result["custom_fields"] = json_parse(init.custom_fields_json, {})
    return result


def initiative_summary(init: Initiative) -> dict:
    enriched = bool(init.enrichments)
    enriched_at = max((e.fetched_at for e in init.enrichments), default=None) if enriched else None
    return _build_initiative_dict(
        init,
        enriched=enriched,
        enriched_at_iso=enriched_at.isoformat() if enriched_at else None,
        score_fields=latest_score_fields(init.scores),
    )


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
# FTS5 full-text search helpers
# ---------------------------------------------------------------------------


def sync_fts_insert(session: Session, init: Initiative) -> None:
    """Insert a single initiative into the FTS index."""
    session.execute(text("""
        INSERT INTO initiative_fts(rowid, name, description, sector,
            technology_domains, categories, market_domains, faculty)
        VALUES (:id, :name, :description, :sector,
            :technology_domains, :categories, :market_domains, :faculty)
    """), {
        "id": init.id, "name": init.name or "", "description": init.description or "",
        "sector": init.sector or "", "technology_domains": init.technology_domains or "",
        "categories": init.categories or "", "market_domains": init.market_domains or "",
        "faculty": init.faculty or "",
    })


def sync_fts_delete(session: Session, initiative_id: int) -> None:
    """Remove a single initiative from the FTS index."""
    session.execute(text(
        "INSERT INTO initiative_fts(initiative_fts, rowid, name, description, sector, "
        "technology_domains, categories, market_domains, faculty) "
        "SELECT 'delete', id, COALESCE(name,''), COALESCE(description,''), "
        "COALESCE(sector,''), COALESCE(technology_domains,''), "
        "COALESCE(categories,''), COALESCE(market_domains,''), "
        "COALESCE(faculty,'') FROM initiatives WHERE id = :id"
    ), {"id": initiative_id})


def sync_fts_update(session: Session, init: Initiative) -> None:
    """Update FTS index for a single initiative (delete + re-insert).

    Uses a savepoint so that a failed re-insert rolls back the delete,
    preventing a half-deleted FTS entry from being committed.
    """
    with session.begin_nested():
        sync_fts_delete(session, init.id)
        sync_fts_insert(session, init)


def rebuild_fts(session: Session) -> None:
    """Full rebuild of the FTS index from the initiatives table."""
    session.execute(text("INSERT INTO initiative_fts(initiative_fts) VALUES('rebuild')"))


def _fts_search(session: Session, query: str) -> list[int] | None:
    """Run FTS5 MATCH search, return ordered IDs by BM25 rank. None on error."""
    try:
        # Strip control characters and escape FTS5 special chars
        safe_q = "".join(c for c in query if c >= " " or c == "\t")
        safe_q = safe_q.replace('"', '""')
        rows = session.execute(text(
            'SELECT rowid FROM initiative_fts WHERE initiative_fts MATCH :q '
            'ORDER BY rank LIMIT 500'
        ), {"q": f'"{safe_q}"'}).all()
        return [r[0] for r in rows]
    except Exception:
        log.debug("FTS5 search failed for query %r, falling back to LIKE", query)
        return None


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
    faculty: str | None = None,
    search: str | None = None,
    sort_by: str = "score",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 200,
    fields: set[str] | None = None,
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

    if faculty:
        fs = {f.strip().lower() for f in faculty.split(",")}
        base = base.where(func.lower(Initiative.faculty).in_(fs))

    if search:
        fts_ids = _fts_search(session, search)
        if fts_ids is not None:
            if not fts_ids:
                return [], 0  # FTS found nothing
            base = base.where(Initiative.id.in_(fts_ids))
        else:
            # LIKE fallback if FTS5 table missing or query fails
            escaped = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = f"%{escaped}%"
            base = base.where(or_(
                func.lower(Initiative.name).like(q, escape="\\"),
                func.lower(Initiative.description).like(q, escape="\\"),
                func.lower(Initiative.sector).like(q, escape="\\"),
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
        "faculty": func.lower(Initiative.faculty),
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
        # Build score fields from the SQL row (ls_ prefix columns)
        score_fields: dict[str, Any] = {}
        for f in SCORE_FIELDS:
            score_fields[f] = getattr(row, f"ls_{f}", None)
        score_fields["key_evidence"] = json_parse(row.ls_key_evidence_json, [])
        score_fields["data_gaps"] = json_parse(row.ls_data_gaps_json, [])

        items.append(_build_initiative_dict(
            init,
            enriched=row.enrich_count > 0,
            enriched_at_iso=row.enrich_latest.isoformat() if row.enrich_latest else None,
            score_fields=score_fields,
        ))

    if fields:
        allowed = fields & COMPACT_FIELDS
        items = [{k: v for k, v in item.items() if k in allowed} for item in items]

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


_PROJECT_FIELDS = ("name", "description", "website", "github_url", "team")


def create_initiative(session: Session, **kwargs: Any) -> Initiative:
    """Create a new initiative. Accepts any UPDATABLE_FIELDS as keyword args."""
    data = {k: v for k, v in kwargs.items() if k in UPDATABLE_FIELDS and v is not None}
    init = Initiative(**data)
    session.add(init)
    session.flush()  # assign ID
    try:
        sync_fts_insert(session, init)
    except Exception:
        log.debug("FTS sync failed on create for %s", init.name)
    return init


def create_project(session: Session, initiative_id: int, extra_links: dict | None = None, **kwargs: Any) -> Project:
    """Create a new project under an initiative."""
    data = {k: (v or "") for k, v in kwargs.items() if k in _PROJECT_FIELDS}
    data["initiative_id"] = initiative_id
    if extra_links is not None:
        data["extra_links_json"] = json.dumps(extra_links)
    proj = Project(**data)
    session.add(proj)
    session.flush()
    return proj


def delete_initiative(session: Session, initiative_id: int) -> bool:
    """Delete an initiative (cascade handles enrichments, scores, projects). Returns True if found."""
    init = get_entity(session, Initiative, initiative_id)
    if not init:
        return False
    try:
        sync_fts_delete(session, initiative_id)
    except Exception:
        log.debug("FTS sync failed on delete for id %d", initiative_id)
    session.delete(init)
    return True


def get_work_queue(session: Session, limit: int = 10) -> list[dict]:
    """Return initiatives needing work, prioritized by what's missing.

    Priority 1: Not enriched AND not scored
    Priority 2: Enriched but not scored
    Priority 3: Scored but not enriched (stale data)
    """
    # Subquery: enriched initiative IDs
    enriched_ids = (
        select(func.distinct(Enrichment.initiative_id)).subquery()
    )
    # Subquery: scored initiative IDs (latest initiative-level score)
    scored_ids = (
        select(func.distinct(OutreachScore.initiative_id))
        .where(OutreachScore.project_id.is_(None))
        .subquery()
    )

    has_enrichment = Initiative.id.in_(select(enriched_ids))
    has_score = Initiative.id.in_(select(scored_ids))

    priority = case(
        (~has_enrichment & ~has_score, 1),
        (has_enrichment & ~has_score, 2),
        (has_score & ~has_enrichment, 3),
        else_=99,
    )

    query = (
        select(Initiative, priority.label("priority"))
        .where(priority < 99)
        .order_by(priority, Initiative.id)
        .limit(max(1, min(limit, 100)))
    )

    rows = session.execute(query).all()
    queue = []
    for row in rows:
        init = row[0]
        p = row[1]
        has_web = bool((init.website or "").strip())
        has_gh = bool((init.github_org or "").strip())
        needs_enrich = p in (1, 3)
        needs_score = p in (1, 2)
        if needs_enrich:
            action = "enrich"
        elif needs_score:
            action = "score"
        else:
            action = "re-enrich"
        queue.append({
            "id": init.id, "name": init.name, "uni": init.uni,
            "has_website": has_web, "has_github": has_gh,
            "needs_enrichment": needs_enrich, "needs_scoring": needs_score,
            "recommended_action": action,
        })
    return queue


# ---------------------------------------------------------------------------
# Custom column CRUD
# ---------------------------------------------------------------------------


def _column_dict(col: CustomColumn) -> dict:
    return {
        "id": col.id, "key": col.key, "label": col.label,
        "col_type": col.col_type, "show_in_list": col.show_in_list,
        "sort_order": col.sort_order,
    }


def create_custom_column(
    session: Session, key: str, label: str,
    col_type: str = "text", show_in_list: bool = True, sort_order: int = 0,
) -> dict | None:
    """Create a custom column. Returns None if key already exists."""
    existing = session.execute(
        select(CustomColumn).where(CustomColumn.key == key)
    ).scalars().first()
    if existing:
        return None
    col = CustomColumn(
        key=key, label=label, col_type=col_type,
        show_in_list=show_in_list, sort_order=sort_order,
    )
    session.add(col)
    session.flush()
    return _column_dict(col)


_CUSTOM_COLUMN_FIELDS = ("label", "col_type", "show_in_list", "sort_order")


def update_custom_column(session: Session, column_id: int, **kwargs: Any) -> dict | None:
    """Update a custom column. Returns None if not found."""
    col = get_entity(session, CustomColumn, column_id)
    if not col:
        return None
    apply_updates(col, kwargs, _CUSTOM_COLUMN_FIELDS)
    session.flush()
    return _column_dict(col)


def delete_custom_column(session: Session, column_id: int) -> bool:
    """Delete a custom column. Returns False if not found."""
    col = get_entity(session, CustomColumn, column_id)
    if not col:
        return False
    session.delete(col)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def run_enrichment(
    session: Session, init: Initiative, crawler: object | None = None,
) -> list[Enrichment]:
    """Run all enrichers in parallel; only delete old enrichments if at least one succeeds.

    Args:
        crawler: Optional AsyncWebCrawler instance for Crawl4AI.
                 Pass ``None`` to use the httpx+lxml fallback.

    Returns new enrichments (caller must commit).
    """
    # Standard enrichers (website, team_page use crawler; github uses REST API)
    results = await asyncio.gather(
        enrich_website(init, crawler),
        enrich_team_page(init, crawler),
        enrich_github(init),
        return_exceptions=True,
    )
    new_enrichments: list[Enrichment] = []
    labels = ("enrich_website", "enrich_team_page", "enrich_github")
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            log.warning("Enrichment failed (%s) for %s: %s", label, init.name, result)
        elif result:
            new_enrichments.append(result)

    # Extra links enrichment (crawl all URLs in extra_links_json)
    try:
        extras = await enrich_extra_links(init, crawler)
        new_enrichments.extend(extras)
    except Exception as exc:
        log.warning("Extra links enrichment failed for %s: %s", init.name, exc)

    if new_enrichments:
        session.execute(delete(Enrichment).where(Enrichment.initiative_id == init.id))
        for e in new_enrichments:
            session.add(e)
        # Re-embed if embeddings exist (optional dependency)
        session.flush()  # make new enrichments visible for re-embedding
        try:
            from scout.embedder import re_embed_one
            re_embed_one(session, init)
        except ImportError:
            pass
        except Exception:
            log.debug("Re-embed failed for %s (non-fatal)", init.name)
    return new_enrichments


async def run_discovery(session: Session, init: Initiative) -> dict:
    """Run DuckDuckGo URL discovery and merge into extra_links_json.

    Does NOT trigger enrichment — caller should call run_enrichment() after.
    Caller must commit.
    """
    discovered = await discover_urls(init)

    if discovered:
        existing = json_parse(init.extra_links_json)
        existing.update(discovered)
        init.extra_links_json = json.dumps(existing)
        session.flush()

    return {
        "discovered_urls": discovered,
        "urls_found": len(discovered),
    }


def _ensure_client(client: LLMClient | None) -> LLMClient:
    """Return the given client or create a default one."""
    return client if client is not None else LLMClient()


async def run_scoring(
    session: Session, init: Initiative, client: LLMClient | None = None,
) -> OutreachScore:
    """Score an initiative, replacing existing initiative-level scores (caller must commit)."""
    client = _ensure_client(client)
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == init.id)
    ).scalars().all()
    prompts = load_scoring_prompts(session)
    outreach = await score_initiative(init, list(enrichments), client, prompts)
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
    client = _ensure_client(client)
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


def compute_aggregations(session: Session) -> dict:
    """Analytical aggregations: score distributions, top-N per verdict, grade breakdowns."""
    ls = _latest_score_subquery()
    latest = select(
        ls.c.initiative_id, ls.c.verdict, ls.c.score, ls.c.classification,
        ls.c.grade_team, ls.c.grade_team_num,
        ls.c.grade_tech, ls.c.grade_tech_num,
        ls.c.grade_opportunity, ls.c.grade_opportunity_num,
    ).where(ls.c.rn == 1).subquery()

    # Average score by uni
    score_by_uni = dict(session.execute(
        select(Initiative.uni, func.round(func.avg(latest.c.score), 2))
        .join(latest, Initiative.id == latest.c.initiative_id)
        .where(Initiative.uni != "")
        .group_by(Initiative.uni)
    ).all())

    # Average score by faculty
    score_by_faculty = dict(session.execute(
        select(Initiative.faculty, func.round(func.avg(latest.c.score), 2))
        .join(latest, Initiative.id == latest.c.initiative_id)
        .where(Initiative.faculty != "")
        .group_by(Initiative.faculty)
    ).all())

    # Top 10 per verdict
    top_by_verdict = {}
    for v in ("reach_out_now", "reach_out_soon", "monitor"):
        rows = session.execute(
            select(Initiative.id, Initiative.name, Initiative.uni, latest.c.score)
            .join(latest, Initiative.id == latest.c.initiative_id)
            .where(latest.c.verdict == v)
            .order_by(latest.c.score.desc())
            .limit(10)
        ).all()
        top_by_verdict[v] = [
            {"id": r[0], "name": r[1], "uni": r[2], "score": r[3]} for r in rows
        ]

    # Grade distributions
    grade_dist = {}
    for dim in ("team", "tech", "opportunity"):
        col = getattr(latest.c, f"grade_{dim}")
        rows = session.execute(
            select(col, func.count()).where(col.isnot(None)).group_by(col)
        ).all()
        grade_dist[dim] = dict(rows)

    # Unprocessed counts
    total = session.execute(select(func.count(Initiative.id))).scalar() or 0
    enriched = session.execute(
        select(func.count(func.distinct(Enrichment.initiative_id)))
    ).scalar() or 0
    scored = session.execute(select(func.count()).select_from(latest)).scalar() or 0

    return {
        "score_by_uni": score_by_uni,
        "score_by_faculty": score_by_faculty,
        "top_by_verdict": top_by_verdict,
        "grade_distributions": grade_dist,
        "unprocessed": {
            "not_enriched": total - enriched,
            "not_scored": total - scored,
        },
    }


def get_custom_columns(session: Session) -> list[dict]:
    """Fetch custom column definitions for the current database."""
    cols = session.execute(
        select(CustomColumn).order_by(CustomColumn.sort_order)
    ).scalars().all()
    return [_column_dict(c) for c in cols]


# ---------------------------------------------------------------------------
# Scoring prompts
# ---------------------------------------------------------------------------


def load_scoring_prompts(session: Session) -> dict[str, str]:
    """Return {key: content} dict of scoring prompts for use by the scorer."""
    rows = session.execute(select(ScoringPrompt)).scalars().all()
    return {r.key: r.content for r in rows}


def get_scoring_prompts(session: Session) -> list[dict]:
    """Return full scoring prompt objects for the API."""
    rows = session.execute(
        select(ScoringPrompt).order_by(ScoringPrompt.key)
    ).scalars().all()
    return [
        {"key": r.key, "label": r.label, "content": r.content,
         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in rows
    ]


def update_scoring_prompt(session: Session, key: str, content: str) -> dict | None:
    """Update a scoring prompt's content. Returns the updated prompt or None."""
    prompt = session.execute(
        select(ScoringPrompt).where(ScoringPrompt.key == key)
    ).scalars().first()
    if not prompt:
        return None
    prompt.content = content
    session.flush()
    return {"key": prompt.key, "label": prompt.label, "content": prompt.content,
            "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None}
