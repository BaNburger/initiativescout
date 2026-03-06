"""Shared business logic for Scout API and MCP server."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from scout.enricher import (
    _html_cache,
    discover_urls, enrich_extra_links, enrich_github, enrich_team_page, enrich_website,
    enrich_structured_data, enrich_tech_stack, enrich_dns, enrich_sitemap,
    enrich_careers, enrich_git_deep,
)
from scout.models import CustomColumn, Enrichment, Initiative, OutreachScore, Project, ScoringPrompt
from scout.schema import get_schema
from scout.scorer import LLMClient, get_entity_config, score_initiative, score_project
from scout.utils import json_parse

# ---------------------------------------------------------------------------
# Enricher registry — maps name to async callable
# ---------------------------------------------------------------------------

ENRICHER_REGISTRY: dict[str, Any] = {
    "website": enrich_website,
    "team_page": enrich_team_page,
    "github": enrich_github,
    "extra_links": enrich_extra_links,
    "structured_data": enrich_structured_data,
    "tech_stack": enrich_tech_stack,
    "dns": enrich_dns,
    "sitemap": enrich_sitemap,
    "careers": enrich_careers,
    "git_deep": enrich_git_deep,
}

# Enrichers that need a crawler argument
_CRAWLER_ENRICHERS = {"website", "team_page", "extra_links"}

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

# Score fields (universal — not entity-type-specific)
SCORE_LIST_FIELDS = (
    "verdict", "score", "classification",
    "grade_team", "grade_tech", "grade_opportunity",
)
SCORE_DETAIL_FIELDS = (
    "verdict", "score", "classification", "reasoning", "contact_who",
    "contact_channel", "engagement_hook", "grade_team", "grade_team_num",
    "grade_tech", "grade_tech_num", "grade_opportunity", "grade_opportunity_num",
)
PROJECT_SCORE_KEYS = (
    "verdict", "score", "classification",
    "grade_team", "grade_team_num", "grade_tech", "grade_tech_num",
    "grade_opportunity", "grade_opportunity_num",
)


# Schema-driven field accessors (replace old hardcoded tuples)
def get_detail_fields() -> tuple[str, ...]:
    return tuple(get_schema()["detail_fields"])

def get_updatable_fields() -> tuple[str, ...]:
    return tuple(get_schema()["updatable_fields"])

def get_compact_fields() -> set[str]:
    return set(get_schema()["compact_fields"])

# Backward-compat aliases (initiative defaults)
DETAIL_FIELDS = tuple(get_schema("initiative")["detail_fields"])
UPDATABLE_FIELDS = tuple(get_schema("initiative")["updatable_fields"])
COMPACT_FIELDS = set(get_schema("initiative")["compact_fields"])

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def score_response_dict(outreach: OutreachScore, extended: bool = False) -> dict[str, Any]:
    """Build a dict from an OutreachScore object.

    Args:
        outreach: The score object.
        extended: If True, include reasoning, contact info, evidence, and data gaps.
    """
    result = {f: getattr(outreach, f) for f in SCORE_LIST_FIELDS}
    if extended:
        for f in SCORE_DETAIL_FIELDS:
            if f not in result:
                result[f] = getattr(outreach, f)
        result["key_evidence"] = json_parse(outreach.key_evidence_json, [])
        result["data_gaps"] = json_parse(outreach.data_gaps_json, [])
    return result


def _empty_score_fields(detail: bool) -> dict[str, Any]:
    """Return a score dict with all None values (no scores available)."""
    fields = SCORE_DETAIL_FIELDS if detail else SCORE_LIST_FIELDS
    result: dict[str, Any] = {f: None for f in fields}
    if detail:
        result["key_evidence"] = []
        result["data_gaps"] = []
    return result


def latest_score_fields(scores: list[OutreachScore], detail: bool = True) -> dict[str, Any]:
    """Extract score fields from the most recent score in the list."""
    if not scores:
        return _empty_score_fields(detail)
    latest = max(scores, key=lambda s: s.scored_at)
    return score_response_dict(latest, extended=detail)


def _build_entity_dict(
    init: Initiative,
    enriched: bool,
    enriched_at_iso: str | None,
    score_fields: dict[str, Any],
) -> dict:
    """Assemble the standard entity summary dict from pre-computed parts.

    Schema-driven: reads summary_fields and summary_extra from the current
    entity type schema. Uses init.field() for entity-type-agnostic access
    (columns for built-in types, metadata_json for custom types).
    """
    schema = get_schema()
    result: dict[str, Any] = {f: init.field(f) for f in schema["summary_fields"]}
    result["enriched"] = enriched
    result["enriched_at"] = enriched_at_iso
    result.update(score_fields)
    for f in schema.get("summary_extra", []):
        result[f] = init.field(f)
    result["custom_fields"] = json_parse(init.custom_fields_json, {})
    metadata = json_parse(init.metadata_json, {})
    if metadata:
        result["metadata"] = metadata
    return result


def _enrichment_meta(init: Initiative) -> tuple[bool, str | None]:
    """Return (enriched, enriched_at_iso) from an initiative's enrichments."""
    if not init.enrichments:
        return False, None
    latest = max(e.fetched_at for e in init.enrichments)
    return True, latest.isoformat()


def compute_missing_fields(init: Initiative) -> list[dict]:
    """Return enrichable fields that are empty/default on this entity."""
    enrichable = get_schema().get("enrichable_fields", {})
    missing = []
    for key, meta in enrichable.items():
        val = init.field(key)
        if val is None or val == "" or val == 0 or val is False:
            missing.append({"key": key, "label": meta["label"], "type": meta["type"]})
    return missing


def apply_enrichment_fields(init: Initiative, fields: dict) -> dict:
    """Validate and apply structured fields to an entity.

    Returns dict with 'applied' (list of keys set) and 'skipped' (list of
    {key, reason} for invalid keys).
    """
    enrichable = get_schema().get("enrichable_fields", {})
    applied, skipped = [], []
    for key, value in fields.items():
        if key not in enrichable:
            skipped.append({"key": key, "reason": "not in enrichable_fields"})
            continue
        meta = enrichable[key]
        # Type coercion
        if meta["type"] == "int":
            try:
                value = int(value)
            except (ValueError, TypeError):
                skipped.append({"key": key, "reason": f"expected int, got {type(value).__name__}"})
                continue
        elif meta["type"] == "bool":
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            else:
                value = bool(value)
        init.set_field(key, value)
        applied.append(key)
    return {"applied": applied, "skipped": skipped}


def entity_summary(init: Initiative) -> dict:
    enriched, enriched_at_iso = _enrichment_meta(init)
    return _build_entity_dict(
        init, enriched=enriched, enriched_at_iso=enriched_at_iso,
        score_fields=latest_score_fields(init.scores, detail=False),
    )


def entity_detail(init: Initiative, *, sources: set[str] | None = None) -> dict:
    enriched, enriched_at_iso = _enrichment_meta(init)
    base = _build_entity_dict(
        init, enriched=enriched, enriched_at_iso=enriched_at_iso,
        score_fields=latest_score_fields(init.scores, detail=True),
    )
    # Add detail fields from schema, skipping empty but keeping 0 and False
    for f in get_detail_fields():
        val = init.field(f)
        if val is not None and val != "":
            base[f] = val
    extra = json_parse(init.extra_links_json)
    if extra:
        base["extra_links"] = extra
    base["enrichments"] = [
        {"id": e.id, "source_type": e.source_type, "source_url": e.source_url,
         "summary": e.summary, "fetched_at": e.fetched_at.isoformat()}
        for e in init.enrichments
        if sources is None or e.source_type in sources
    ]
    base["projects"] = [project_summary(p) for p in init.projects]
    return base


def entity_detail_compact(init: Initiative) -> dict:
    """Lighter detail view: skips enrichment summaries, extra_links, projects, reasoning."""
    enriched, enriched_at_iso = _enrichment_meta(init)
    base = _build_entity_dict(
        init, enriched=enriched, enriched_at_iso=enriched_at_iso,
        score_fields=latest_score_fields(init.scores, detail=False),
    )
    base.update({f: init.field(f) for f in get_detail_fields()})
    base["enrichment_sources"] = [e.source_type for e in init.enrichments]
    base["project_count"] = len(init.projects)
    # Strip empty/default values to reduce context, but keep id, name, enriched
    # and preserve legitimate 0 and False values (e.g. github_commits_90d=0)
    _keep = {"id", "name", "enriched"}
    _empty = ("", None, [], {})
    return {k: v for k, v in base.items() if k in _keep or v not in _empty}


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


def rebuild_fts(session: Session) -> None:
    """Full rebuild of the FTS index from the initiatives table."""
    from scout.db import _FTS_TABLE
    session.execute(text(f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}) VALUES('rebuild')"))


def _fts_search(session: Session, query: str) -> list[int] | None:
    """Run FTS5 MATCH search, return ordered IDs by BM25 rank. None on error."""
    from scout.db import _FTS_TABLE
    try:
        # Strip control characters and escape FTS5 special chars
        safe_q = "".join(c for c in query if c >= " " or c == "\t")
        safe_q = safe_q.replace('"', '""')
        fts_q = f'"{safe_q}"'
        rows = session.execute(text(
            f'SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH :q '
            f'ORDER BY rank LIMIT 500'
        ), {"q": fts_q}).all()
        # Lazy FTS rebuild: if empty result, check if FTS index needs populating
        if not rows:
            has_rows = session.execute(text(
                f"SELECT 1 FROM {_FTS_TABLE} LIMIT 1"
            )).first()
            if not has_rows:
                log.info("FTS index empty — rebuilding lazily")
                rebuild_fts(session)
                rows = session.execute(text(
                    f'SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH :q '
                    f'ORDER BY rank LIMIT 500'
                ), {"q": fts_q}).all()
        return [r[0] for r in rows]
    except (OperationalError, ProgrammingError):
        log.warning("FTS5 search failed for query %r, falling back to LIKE", query, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# SQL-based list query
# ---------------------------------------------------------------------------


def _latest_score_subquery():
    """Subquery returning the latest initiative-level score per initiative (lightweight fields)."""
    columns = [
        OutreachScore.initiative_id,
        OutreachScore.verdict,
        OutreachScore.score,
        OutreachScore.classification,
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
    ]
    return (
        select(*columns)
        .where(OutreachScore.project_id.is_(None))
        .subquery()
    )


def query_entities(
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

    # Base query: Initiative LEFT JOIN latest score (light) + enrichment aggregates
    base = (
        select(
            Initiative,
            ls.c.verdict.label("ls_verdict"),
            ls.c.score.label("ls_score"),
            ls.c.classification.label("ls_classification"),
            ls.c.grade_team.label("ls_grade_team"),
            ls.c.grade_tech.label("ls_grade_tech"),
            ls.c.grade_opportunity.label("ls_grade_opportunity"),
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
        # Build score fields from the SQL row (light fields only)
        score_fields: dict[str, Any] = {}
        for f in SCORE_LIST_FIELDS:
            score_fields[f] = getattr(row, f"ls_{f}", None)

        items.append(_build_entity_dict(
            init,
            enriched=row.enrich_count > 0,
            enriched_at_iso=row.enrich_latest.isoformat() if row.enrich_latest else None,
            score_fields=score_fields,
        ))

    if fields:
        allowed = fields & get_compact_fields()
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


def merge_custom_fields(obj, updates: dict) -> None:
    """Merge custom field updates into an initiative's custom_fields_json.

    Sets keys from *updates*; keys with None values are removed.
    """
    existing = json_parse(obj.custom_fields_json, {})
    existing.update(updates)
    obj.custom_fields_json = json.dumps({k: v for k, v in existing.items() if v is not None})


_PROJECT_FIELDS = ("name", "description", "website", "github_url", "team")


def create_entity(session: Session, **kwargs: Any) -> Initiative:
    """Create a new entity. Accepts fields from the entity type schema.

    Column fields are set directly; other fields go into metadata_json.
    FTS index is updated automatically via SQLAlchemy event listeners (db.py).
    """
    allowed = set(get_updatable_fields())
    col_names = Initiative._columns()
    col_data = {}
    meta_data = {}
    for k, v in kwargs.items():
        if k not in allowed or v is None:
            continue
        if k in col_names:
            col_data[k] = v
        else:
            meta_data[k] = v
    init = Initiative(**col_data)
    if meta_data:
        import json as _json
        init.metadata_json = _json.dumps(meta_data)
    session.add(init)
    session.flush()
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


def delete_entity(session: Session, entity_id: int) -> bool:
    """Delete an entity (cascade handles enrichments, scores, projects).

    FTS index is updated automatically via SQLAlchemy event listeners (db.py).
    Returns True if found.
    """
    init = get_entity(session, Initiative, entity_id)
    if not init:
        return False
    session.delete(init)  # triggers after_delete → FTS sync
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
        needs_enrich = p in (1, 3)
        needs_score = p in (1, 2)
        if needs_enrich:
            action = "enrich"
        elif needs_score:
            action = "score"
        else:
            action = "re-enrich"
        missing = compute_missing_fields(init)
        item: dict = {
            "id": init.id, "name": init.name,
            "needs_enrichment": needs_enrich, "needs_scoring": needs_score,
            "recommended_action": action,
            "missing_fields_count": len(missing),
        }
        # Include entity-type-relevant context
        if init.uni:
            item["uni"] = init.uni
        has_web = bool((init.field("website") or "").strip())
        has_gh = bool((init.field("github_org") or "").strip())
        if has_web:
            item["has_website"] = True
        if has_gh:
            item["has_github"] = True
        queue.append(item)
    return queue


# ---------------------------------------------------------------------------
# Custom column CRUD
# ---------------------------------------------------------------------------


def _column_dict(col: CustomColumn) -> dict:
    return {
        "id": col.id, "key": col.key, "label": col.label,
        "col_type": col.col_type, "show_in_list": col.show_in_list,
        "sort_order": col.sort_order, "database": col.database,
    }


def create_custom_column(
    session: Session, key: str, label: str,
    col_type: str = "text", show_in_list: bool = True, sort_order: int = 0,
    database: str | None = None,
) -> dict | None:
    """Create a custom column. Returns None if key already exists in this database."""
    stmt = select(CustomColumn).where(CustomColumn.key == key)
    if database is not None:
        stmt = stmt.where(
            (CustomColumn.database == database) | (CustomColumn.database.is_(None))
        )
    existing = session.execute(stmt).scalars().first()
    if existing:
        return None
    col = CustomColumn(
        key=key, label=label, col_type=col_type,
        show_in_list=show_in_list, sort_order=sort_order,
        database=database,
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
# Import helpers
# ---------------------------------------------------------------------------


def import_scraped_entities(
    session: Session, entities: list[dict[str, str]],
) -> dict[str, int]:
    """Import a list of scraped entity dicts, deduplicating by name.

    Each dict should have at least 'name', plus optional 'uni', 'faculty', 'website'.
    Returns {"created": N, "skipped_duplicates": N}.
    """
    existing_names = {
        name.lower()
        for (name,) in session.execute(select(Initiative.name)).all()
    }
    created = skipped = 0
    for ent in entities:
        if ent["name"].lower() in existing_names:
            skipped += 1
            continue
        session.add(Initiative(
            name=ent["name"], uni=ent.get("uni", ""),
            faculty=ent.get("faculty", ""), website=ent.get("website", ""),
        ))
        existing_names.add(ent["name"].lower())
        created += 1
    session.flush()
    return {"created": created, "skipped_duplicates": skipped}


def build_similarity_id_mask(
    session: Session,
    uni: str | None = None,
    verdict: str | None = None,
) -> set[int] | None:
    """Build an ID mask for similarity search pre-filtering.

    Returns None if no filters are applied, or a set of matching initiative IDs.
    """
    if not uni and not verdict:
        return None
    q_filter = select(Initiative.id)
    if uni:
        us = {u.strip().upper() for u in uni.split(",")}
        q_filter = q_filter.where(func.upper(Initiative.uni).in_(us))
    if verdict:
        ls = _latest_score_subquery()
        vs = {v.strip().lower() for v in verdict.split(",")}
        q_filter = q_filter.join(
            ls, and_(Initiative.id == ls.c.initiative_id, ls.c.rn == 1)
        ).where(ls.c.verdict.in_(vs))
    rows = session.execute(q_filter).scalars().all()
    return set(rows)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def run_enrichment(
    session: Session, init: Initiative, crawler: object | None = None,
    *, incremental: bool = True,
) -> list[Enrichment]:
    """Run entity-type-aware enrichers in parallel; only delete old enrichments if at least one succeeds.

    Uses ENRICHER_REGISTRY + entity type config to determine which enrichers
    to run. Enrichers that need a crawler get one; others are called directly.

    When incremental=True (default), skips enrichers whose target fields are
    already filled on the entity. Set incremental=False to force re-run all.

    Returns new enrichments (caller must commit).
    """
    from scout.db import get_entity_type
    entity_type = get_entity_type()
    cfg = get_entity_config(entity_type)
    configured = set(cfg.get("enrichers", list(ENRICHER_REGISTRY.keys())))
    enricher_targets = cfg.get("enricher_targets", {})

    # Build tasks from registry, respecting entity type config
    tasks: list[tuple[str, Any]] = []
    for name in ENRICHER_REGISTRY:
        if name not in configured:
            continue
        # Skip enrichers whose target fields are all filled
        if incremental and name in enricher_targets:
            targets = enricher_targets[name]
            if targets and all(
                init.field(f) not in (None, "", 0, False) for f in targets
            ):
                log.debug("Skipping enricher %s — all targets filled for %s", name, init.name)
                continue
        fn = ENRICHER_REGISTRY[name]
        if name in _CRAWLER_ENRICHERS:
            tasks.append((name, fn(init, crawler)))
        else:
            tasks.append((name, fn(init)))

    if not tasks:
        return []

    labels, coros = zip(*tasks)
    # Enable per-entity URL cache so enrichers sharing the same URL don't re-fetch
    async with _html_cache():
        results = await asyncio.gather(*coros, return_exceptions=True)

    new_enrichments: list[Enrichment] = []
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            log.warning("Enrichment failed (%s) for %s: %s", label, init.name, result)
        elif isinstance(result, list):
            new_enrichments.extend(result)
        elif result:
            new_enrichments.append(result)

    if new_enrichments:
        # Only delete enrichments from automated enrichers that ran — preserve
        # LLM-submitted enrichments (source_type not in the enricher registry)
        automated_types = set(labels)
        session.execute(delete(Enrichment).where(
            Enrichment.initiative_id == init.id,
            Enrichment.source_type.in_(automated_types),
        ))
        for e in new_enrichments:
            session.add(e)
        # Apply structured fields from enrichments to entity
        for e in new_enrichments:
            sf = json_parse(e.structured_fields_json)
            if sf:
                apply_enrichment_fields(init, sf)
        # Re-embed with updated enrichment data
        session.flush()
        try:
            from scout.embedder import re_embed_one
            re_embed_one(session, init)
        except Exception:
            log.warning("Re-embed failed for %s (non-fatal)", init.name, exc_info=True)
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


async def enrich_with_diagnostics(
    session: Session, init: Initiative, *, discover: bool = False,
    incremental: bool = True,
) -> dict:
    """Full enrichment pipeline: optional discovery → enrich → source diagnostics.

    This is the single entry point for both MCP and the web API. It handles:
    - Auto-discovery when no URLs are configured
    - Opening/closing the shared crawler
    - Classifying sources into succeeded/failed/not_configured

    Caller must commit the session after this returns.
    """
    from scout.enricher import open_crawler

    # Smart default: auto-discover when entity has no URLs at all
    auto_discover = False
    if not discover:
        has_urls = bool(
            (init.field("website") or "").strip()
            or (init.field("github_org") or "").strip()
            or json_parse(init.extra_links_json)
        )
        if not has_urls:
            discover = True
            auto_discover = True

    discover_result = None
    if discover:
        try:
            disc = await run_discovery(session, init)
            session.commit()
            discover_result = {"urls_found": disc["urls_found"]}
            if auto_discover:
                discover_result["auto_triggered"] = True
        except ImportError:
            discover_result = {"skipped": True, "reason": "ddgs not installed — pip install 'scout[crawl]'"}
        except Exception as exc:
            discover_result = {"skipped": True, "reason": str(exc)[:100]}

    async with open_crawler() as crawler:
        new = await run_enrichment(session, init, crawler=crawler, incremental=incremental)

    # Classify sources
    succeeded = [e.source_type for e in new]
    possible = {"website", "team_page", "github",
                "structured_data", "tech_stack", "dns", "sitemap", "careers", "git_deep"}
    extra = json_parse(init.extra_links_json)
    if extra:
        possible.update(k.removesuffix("_urls").removesuffix("_url") for k in extra if extra[k])
    not_configured = []
    has_website = bool((init.field("website") or "").strip())
    has_github = bool((init.field("github_org") or "").strip())
    if not has_website:
        not_configured.extend(["website", "structured_data", "tech_stack", "dns", "sitemap", "careers"])
    if not (init.field("team_page") or "").strip():
        not_configured.append("team_page")
    if not has_github:
        not_configured.extend(["github", "git_deep"])
    failed = sorted(possible - set(succeeded) - set(not_configured))

    result = {
        "entity_id": init.id, "entity_name": init.name,
        "enrichments_added": len(new),
        "sources_succeeded": succeeded,
        "sources_failed": failed,
        "sources_not_configured": not_configured,
    }
    if discover_result:
        result["discovery"] = discover_result
    return result


def _ensure_client(client: LLMClient | None) -> LLMClient:
    """Return the given client or create a default one."""
    return client if client is not None else LLMClient()


async def run_scoring(
    session: Session, init: Initiative, client: LLMClient | None = None,
    entity_type: str | None = None,
) -> OutreachScore:
    """Score an entity, replacing existing entity-level scores (caller must commit)."""
    client = _ensure_client(client)
    if entity_type is None:
        from scout.db import get_entity_type
        entity_type = get_entity_type()
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == init.id)
    ).scalars().all()
    prompts = load_scoring_prompts(session)
    outreach = await score_initiative(init, list(enrichments), client, prompts, entity_type=entity_type)
    session.execute(delete(OutreachScore).where(
        OutreachScore.initiative_id == init.id,
        OutreachScore.project_id.is_(None),
    ))
    session.add(outreach)
    return outreach


async def run_project_scoring(
    session: Session, proj: Project, init: Initiative, client: LLMClient | None = None,
    entity_type: str = "initiative",
) -> OutreachScore:
    """Score a project, replacing existing project scores (caller must commit)."""
    client = _ensure_client(client)
    outreach = await score_project(proj, init, client, entity_type=entity_type)
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


def get_custom_columns(session: Session, database: str | None = None) -> list[dict]:
    """Fetch custom column definitions for a specific database (or all if None).

    Returns columns where database matches OR database is NULL (global).
    """
    stmt = select(CustomColumn).order_by(CustomColumn.sort_order)
    if database is not None:
        stmt = stmt.where(
            (CustomColumn.database == database) | (CustomColumn.database.is_(None))
        )
    cols = session.execute(stmt).scalars().all()
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
