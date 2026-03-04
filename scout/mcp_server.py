from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP
from sqlalchemy import and_, delete, func, select

from scout import services
from scout.db import (
    create_database, current_db_name, get_entity_type, get_session, init_db,
    list_databases, session_scope, switch_db, validate_db_name,
)
from scout.enricher import open_crawler
from scout.models import Enrichment, Initiative, OutreachScore, Project
from scout.scorer import (
    ENTITY_CONFIG, GRADE_MAP, VALID_GRADES, Grade,
    LLMClient, build_full_dossier, build_team_dossier, build_tech_dossier,
    compute_data_gaps, compute_score, compute_verdict,
    create_score_from_grades, default_prompts_for, valid_classifications,
)
from scout.utils import json_parse

log = logging.getLogger(__name__)


def _entity_cfg() -> dict[str, str]:
    """Return ENTITY_CONFIG for the current database's entity type."""
    return ENTITY_CONFIG.get(get_entity_type(), ENTITY_CONFIG["initiative"])


def _build_instructions(entity_type: str) -> str:
    cfg = ENTITY_CONFIG.get(entity_type, ENTITY_CONFIG["initiative"])
    lp = cfg["label_plural"]
    l = cfg["label"]
    return (
        f"Scout is an outreach intelligence tool for {cfg['context']}. "
        "QUICK START: get_stats() → get_work_queue() → follow recommended_action for each item. "
        "BULK (RECOMMENDED): process_queue(limit=20) enriches AND scores in one call. Repeat until remaining_in_queue=0. "
        f"BULK SELECTIVE: batch_enrich(initiative_ids='1,2,3') → batch_score(initiative_ids='1,2,3') for specific {lp}. "
        f"SINGLE ITEM: enrich_initiative(id) → score_initiative_tool(id) for one-off {l} processing with full detail. "
        f"DEEP MODE: discover_initiative(id) → enrich_initiative(id) → score_initiative_tool(id). "
        "Discovery uses DuckDuckGo to find LinkedIn, GitHub, HuggingFace URLs not in the spreadsheet. Rate-limited (~12s/call). "
        f"NEW DATA: create_initiative(name, uni, website) → discover_initiative(id) → enrich_initiative(id) → score_initiative_tool(id). "
        "ANALYTICS: get_stats() → get_aggregations() for score distributions and top-N by verdict. "
        "SIMILARITY: find_similar_initiatives(query='...') for semantic search (embeddings auto-update on enrichment). "
        f"COMPACT: list_initiatives(fields='id,name,verdict,score') to reduce token usage for large lists. "
        "SEARCH: list_initiatives(search='...') uses FTS5 ranked search across name, description, sector, domains, faculty. "
        "ERRORS: All errors return {error, error_code, retryable}. Retry if retryable=true. "
        f"DATA SAFETY: This database contains real {l} data — treat it as production. "
        f"NEVER rename, delete, or overwrite {lp} for testing or debugging. "
        "For experiments, create a test database first: create_scout_database('test') → select_scout_database('test'). "
        "delete_initiative() requires confirm=True to prevent accidental deletion. "
        "update_initiative() will warn you when changing the name field — only do so with verified data."
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def scout_lifespan(server: FastMCP) -> AsyncIterator[None]:
    init_db()
    et = get_entity_type()
    server._mcp_server.instructions = _build_instructions(et)
    yield


mcp = FastMCP(
    "Scout",
    instructions=_build_instructions("initiative"),
    lifespan=scout_lifespan,
    json_response=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_error(session, model, entity_id):
    obj = services.get_entity(session, model, entity_id)
    if not obj:
        return None, _error(f"{model.__name__} {entity_id} not found", "NOT_FOUND")
    return obj, None


def _error(message: str, error_code: str, retryable: bool = False) -> dict:
    return {"error": message, "error_code": error_code, "retryable": retryable}


def _llm_error(exc: Exception) -> dict:
    """Convert an LLM-related exception into a standard error dict."""
    retryable = getattr(exc, "retryable", False)
    return _error(f"Scoring failed: {exc}", "LLM_ERROR", retryable=retryable)


def _check_api_key() -> dict | None:
    """Return an error dict if the LLM API key is not configured, else None."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return _error(
            "ANTHROPIC_API_KEY not set in MCP server environment. "
            "Run 'scout-setup claude-code' to configure, or add "
            "env.ANTHROPIC_API_KEY to your .mcp.json / Claude Desktop config. "
            "Alternatively, use get_scoring_dossier() + submit_score() for LLM-free scoring.",
            "CONFIG_ERROR",
        )
    if provider in ("openai", "openai_compatible") and not os.environ.get("OPENAI_API_KEY"):
        return _error(
            "OPENAI_API_KEY not set in MCP server environment. "
            "Alternatively, use get_scoring_dossier() + submit_score() for LLM-free scoring.",
            "CONFIG_ERROR",
        )
    return None


def _parse_ids(raw: str | None) -> list[int] | None:
    """Parse a comma-separated string of IDs into a list of ints, or None."""
    if not raw:
        return None
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


VALID_CHANNELS = {"email", "linkedin", "event", "website_form"}


# ---------------------------------------------------------------------------
# Batch runner — eliminates per-item session boilerplate
# ---------------------------------------------------------------------------


async def _run_for_item(init_id: int, operation, **kwargs) -> dict:
    """Run an async operation on a single initiative with full session lifecycle.

    Returns {"id", "ok": True, "name", ...result} on success,
    or {"id", "ok": False, "error"} on failure. Never raises.
    """
    s = get_session()
    try:
        init = s.execute(
            select(Initiative).where(Initiative.id == init_id)
        ).scalars().first()
        if not init:
            return {"id": init_id, "ok": False, "error": "Not found"}
        result = await operation(s, init, **kwargs)
        s.commit()
        return {"id": init_id, "ok": True, "name": init.name, **(result or {})}
    except Exception as exc:
        s.rollback()
        log.warning("Batch op failed for id=%s: %s", init_id, exc, exc_info=True)
        return {"id": init_id, "ok": False, "error": str(exc)[:120]}
    finally:
        s.close()


async def _run_batch(ids: list[int], operation, concurrency: int = 1, **kwargs) -> list[dict]:
    """Run an operation on multiple initiative IDs with controlled concurrency."""
    if concurrency > 1:
        sem = asyncio.Semaphore(concurrency)

        async def _limited(init_id):
            async with sem:
                return await _run_for_item(init_id, operation, **kwargs)

        results = list(await asyncio.gather(*[_limited(i) for i in ids]))
    else:
        results = [await _run_for_item(i, operation, **kwargs) for i in ids]
    return results


def _batch_summary(results: list[dict]) -> tuple[int, int]:
    """Return (succeeded, failed) counts from batch results."""
    ok = sum(1 for r in results if r.get("ok"))
    return ok, len(results) - ok


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


@mcp.resource("scout://overview")
def scout_overview() -> str:
    """Overview of Scout: data model, workflow, and available verdicts."""
    cfg = _entity_cfg()
    lp = cfg["label_plural"]
    et = get_entity_type()
    cls_list = sorted(valid_classifications(et))
    return json.dumps({
        "system": f"Scout — Outreach Intelligence for {cfg['context'].title()}",
        "entity_type": et,
        "description": (
            f"Scout discovers, enriches, and scores {lp} "
            "for outreach. Contains profiles with web/GitHub enrichment "
            "data and LLM-powered outreach verdicts."
        ),
        "data_model": {
            cfg["label"]: f"A {cfg['label']} record. Has profile, enrichments, scores, and projects.",
            "enrichment": "Web-scraped data from website, team page, GitHub, and extra links (LinkedIn, HuggingFace, etc.).",
            "project": f"A sub-project within a {cfg['label']}. Can be scored independently.",
            "outreach_score": "LLM-generated verdict, score (1-5), classification, reasoning, and engagement recommendations.",
            "custom_column": f"User-defined field for tracking additional per-{cfg['label']} data.",
        },
        "scoring_architecture": {
            "description": f"Each {cfg['label']} is scored on 3 dimensions in parallel via LLM.",
            "dimensions": {
                "team": "Team quality from team page, LinkedIn, member roles, team size.",
                "tech": "Technical depth from GitHub activity, research output, key repos.",
                "opportunity": "Market opportunity — pure LLM judgment on the full dossier.",
            },
            "aggregation": "Verdict and score computed deterministically from average of 3 grade numerics.",
        },
        "workflow": [
            "0. list_scout_databases() — see available databases. select_scout_database(name) to switch.",
            "1. get_stats() — see total, enriched, scored counts.",
            "2. get_aggregations() — score distributions by uni/faculty, top-N per verdict, grade breakdowns.",
            f"3. get_work_queue() — get next {lp} needing enrichment or scoring.",
            f"4. create_initiative(name, uni, ...) — add new {lp} to track.",
            f"5. discover_initiative(id) — find new URLs via DuckDuckGo (rate-limited, run once per {cfg['label']}).",
            "6. enrich_initiative(id) — fetch fresh data from all known URLs + GitHub.",
            "7. score_initiative_tool(id) — score 3 dimensions in parallel.",
            f"8. list_initiatives(verdict, ..., fields='id,name,verdict,score') — browse/filter {lp}.",
            "9. get_initiative(id) — full details with enrichments and scores.",
            "10. embed_all_tool() — build dense embeddings for similarity search.",
            "11. find_similar_initiatives(query='...') — semantic similarity search.",
            "12. update_initiative(id, ...) — correct or add information.",
            "13. list_scoring_prompts() / update_scoring_prompt() — customize dimension prompts.",
            "14. delete_initiative(id) — remove duplicates or irrelevant entries.",
        ],
        "bulk_workflow": {
            "description": "Efficient batch processing (recommended for >5 items).",
            "steps": [
                "1. get_stats() — understand database state.",
                "2. process_queue(limit=20) — enriches AND scores in one call.",
                "3. Repeat process_queue() until remaining_in_queue=0.",
                "4. list_initiatives(verdict='reach_out_now') — review top results.",
            ],
            "selective": f"batch_enrich(initiative_ids='1,2,3') → batch_score(initiative_ids='1,2,3') for specific {lp}.",
        },
        "single_item_workflow": {
            "description": "For detailed single-item processing with full response data.",
            "steps": [
                "1. get_work_queue(limit=10) — get prioritized items.",
                "2. enrich_initiative(id) → score_initiative_tool(id) per item.",
                "3. get_initiative(id) — inspect full details.",
            ],
            "new_data_flow": "create_initiative(name, uni, website) → discover_initiative(id) → enrich_initiative(id) → score_initiative_tool(id)",
        },
        "search_modes": {
            "keyword": "list_initiatives(search='...') — FTS5-ranked full-text search across name, description, sector, domains, faculty.",
            "semantic": "find_similar_initiatives(query='...') — Dense embedding similarity via model2vec. Embeddings auto-update on enrichment.",
            "similar": "find_similar_initiatives(initiative_id=N) — Find initiatives most similar to a given one.",
            "hybrid": "find_similar_initiatives(query='...', uni='TUM', verdict='reach_out_now') — SQL pre-filter + semantic ranking.",
            "compact": "list_initiatives(fields='id,name,verdict,score') — Return only requested fields to save tokens.",
        },
        "performance_expectations": {
            "enrichment": f"2-10 seconds per {cfg['label']} (web scraping + extra links; faster without Crawl4AI).",
            "discovery": f"12+ seconds per {cfg['label']} (DuckDuckGo rate limit). Run once per {cfg['label']}.",
            "scoring": f"5-15 seconds per {cfg['label']} (3 parallel LLM calls).",
            "listing": "Instant (SQL query with FTS5).",
            "embedding": f"~1 second for 200 {lp} (model2vec, local).",
            "similarity": "Instant (numpy dot product on pre-computed vectors).",
        },
        "error_handling": {
            "format": "Errors return {error, error_code, retryable}.",
            "codes": {
                "NOT_FOUND": "Entity does not exist.",
                "LLM_ERROR": "LLM API call failed or returned bad output. Check retryable flag.",
                "ALREADY_EXISTS": "Duplicate entity (database or custom column key).",
                "VALIDATION_ERROR": "Invalid input (e.g. bad database name format).",
                "DEPENDENCY_MISSING": "Optional dependency not installed (e.g. duckduckgo-search for discovery).",
            },
        },
        "verdicts": {
            "reach_out_now": "Strong signals, worth a cold email this week.",
            "reach_out_soon": "Promising but needs a trigger event. Queue for next month.",
            "monitor": "Interesting but insufficient evidence. Check back in 3 months.",
            "skip": "Social club, dormant, or out of scope.",
        },
        "classifications": cls_list,
        "grades": "School grades A+ through D on three dimensions: team, tech, opportunity. Lower numeric = better (A+=1.0, D=4.0).",
    }, indent=2)


# ---------------------------------------------------------------------------
# Tools: Initiatives
# ---------------------------------------------------------------------------


@mcp.tool()
def list_initiatives(
    verdict: str | None = None, classification: str | None = None,
    uni: str | None = None, faculty: str | None = None,
    search: str | None = None,
    sort_by: str = "score", sort_dir: str = "desc", limit: int = 20,
    fields: str | None = None,
) -> list[dict]:
    """List and filter entities in the current database.

    WHAT: Returns summaries with scores, classifications, and verdicts.
    WHEN: Use to browse, search, or filter the database. For autonomous processing, use get_work_queue() instead.
    RESPONSE: Each item includes id, name, uni, faculty, verdict, score, classification, enriched status, and grade breakdown.
    COMPACT: Use fields="id,name,verdict,score" to return only those keys (saves tokens for large lists).

    Args:
        verdict: Filter by outreach verdict. Comma-separated from:
                 reach_out_now, reach_out_soon, monitor, skip, unscored.
        classification: Filter by classification type (comma-separated). Values depend on entity type.
        uni: Filter by university. Comma-separated, e.g. "TUM,LMU".
        faculty: Filter by faculty/department. Comma-separated.
        search: Free-text search across name, description, sector, and more (FTS5-ranked).
        sort_by: Sort field: score, name, uni, faculty, verdict, grade_team, grade_tech, grade_opportunity.
        sort_dir: Sort direction: asc or desc.
        limit: Max results (default 20, max 500).
        fields: Comma-separated field names for compact mode, e.g. "id,name,verdict,score".
                Only returns the requested fields from each item (must be valid summary fields).
    """
    fields_set = {f.strip() for f in fields.split(",") if f.strip()} if fields else None
    with session_scope() as session:
        items, _ = services.query_initiatives(
            session, verdict=verdict, classification=classification,
            uni=uni, faculty=faculty, search=search, sort_by=sort_by, sort_dir=sort_dir,
            page=1, per_page=max(1, min(limit, 500)), fields=fields_set,
        )
        return items


@mcp.tool()
def get_initiative(initiative_id: int, compact: bool = False) -> dict:
    """Get full details for a single entity.

    WHAT: Returns profile, enrichments, projects, scores, and data gaps.
    WHEN: Use after list_initiatives() to inspect a specific entity before enriching or scoring.
    RESPONSE: verdict=null means unscored. enriched=false means no web data fetched yet.
        data_gaps lists what's missing (e.g. "No GitHub data"). enrichments array shows fetched sources.
    NEXT: If enriched=false, call enrich_initiative(id). If verdict=null, call score_initiative_tool(id).

    Args:
        initiative_id: The numeric ID of the entity.
        compact: If true, returns lighter payload — skips enrichment summaries, extra_links,
                 projects, and full reasoning. Use for quick lookups. Default false (full detail).
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        if compact:
            return services.initiative_detail_compact(init)
        return services.initiative_detail(init)


@mcp.tool()
def create_initiative(
    name: str, uni: str,
    faculty: str | None = None, sector: str | None = None, mode: str | None = None,
    description: str | None = None, website: str | None = None,
    email: str | None = None, relevance: str | None = None,
    team_page: str | None = None, team_size: str | None = None,
    linkedin: str | None = None, github_org: str | None = None,
    key_repos: str | None = None, sponsors: str | None = None,
    competitions: str | None = None,
) -> dict:
    """Create a new record in the database.

    WHAT: Creates a new record with the given fields. Works for any entity type
        (initiative, professor, etc.) depending on the current database.
    WHEN: Use when you discover a new entity to track.
    NEXT: Call enrich_initiative(id) to fetch web/GitHub data, then score_initiative_tool(id).

    Args:
        name: Entity name (required). For professors, use the person's name.
        uni: University (required).
        faculty: Faculty or school code (e.g. "CIT", "ED" for TUM schools).
        sector: Domain or research area, e.g. "AI", "Robotics", "Database Systems".
        website: Website URL — needed for enrichment. For professors, the chair/group page.
        github_org: GitHub org or username — needed for tech enrichment.
        email: Contact email address.
        linkedin: LinkedIn URL.
        description: Short description.
    """
    with session_scope() as session:
        init = services.create_initiative(
            session, name=name, uni=uni, faculty=faculty, sector=sector, mode=mode,
            description=description, website=website, email=email,
            relevance=relevance, team_page=team_page, team_size=team_size,
            linkedin=linkedin, github_org=github_org, key_repos=key_repos,
            sponsors=sponsors, competitions=competitions,
        )
        session.commit()
        return {
            "id": init.id, "name": init.name, "uni": init.uni,
            "website": init.website or None,
            "github_org": init.github_org or None,
            "hint": "Call enrich_initiative(id) next to fetch web/GitHub data.",
        }


@mcp.tool()
def delete_initiative(initiative_id: int, confirm: bool = False) -> dict:
    """Delete an entity and all its enrichments, scores, and projects.

    WHAT: Permanently removes the entity and all associated data (cascading delete).
    WHEN: Use when an entity is duplicate, out of scope, or no longer relevant.
    SAFETY: You must pass confirm=True to execute. This prevents accidental deletion.

    Args:
        initiative_id: The numeric ID of the entity to delete.
        confirm: Must be True to confirm deletion. Defaults to False (dry run).
    """
    if not confirm:
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, initiative_id)
            if err:
                return err
            return {
                "ok": False,
                "action": "delete_initiative",
                "initiative_id": init.id,
                "initiative_name": init.name,
                "warning": f"This will permanently delete '{init.name}' and all its enrichments, scores, and projects. "
                           "Call again with confirm=True to proceed.",
            }
    with session_scope() as session:
        if not services.delete_initiative(session, initiative_id):
            return _error(f"Initiative {initiative_id} not found", "NOT_FOUND")
        session.commit()
        return {"ok": True, "deleted_initiative_id": initiative_id}


@mcp.tool()
def get_work_queue(limit: int = 10) -> dict:
    """Get the next entities that need enrichment or scoring.

    WHAT: Returns a prioritized queue of entities needing work, with recommended actions.
    WHEN: Use this to drive autonomous workflows — call it, then follow each item's recommended_action.
    NEXT: For each item, call enrich_initiative(id) or score_initiative_tool(id) as recommended.

    Priority order:
    1. Not enriched AND not scored → recommended_action: "enrich"
    2. Enriched but not scored → recommended_action: "score"
    3. Scored but not enriched (stale) → recommended_action: "re-enrich"

    Args:
        limit: Max items to return (1-100, default 10).
    """
    with session_scope() as session:
        queue = services.get_work_queue(session, limit)
        stats = services.compute_stats(session)
        return {"queue": queue, "database_stats": stats}


@mcp.tool()
def update_initiative(
    initiative_id: int,
    name: str | None = None, uni: str | None = None, faculty: str | None = None,
    sector: str | None = None,
    mode: str | None = None, description: str | None = None, website: str | None = None,
    email: str | None = None, relevance: str | None = None, team_page: str | None = None,
    team_size: str | None = None, linkedin: str | None = None, github_org: str | None = None,
    key_repos: str | None = None, sponsors: str | None = None, competitions: str | None = None,
    custom_fields: dict[str, str | None] | None = None,
) -> dict:
    """Update fields on an entity. Only provided (non-null) arguments are applied.

    WHAT: Modifies entity profile data. Returns the full updated detail.
    WHEN: Use to correct data, add missing URLs (website, github_org, linkedin) before enrichment,
        or fill in context (description, sector) before scoring.
    SAFETY: Changing the name field triggers a warning with old→new values. Only rename
        if you have verified the correct name from a primary source.
    NEXT: If you added website/github_org, call enrich_initiative(id) to fetch fresh data.

    Args:
        custom_fields: Dict of custom column key→value pairs to set. Use null value to remove a key.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        old_name = init.name
        updates = {k: v for k, v in {
            "name": name, "uni": uni, "faculty": faculty, "sector": sector, "mode": mode,
            "description": description, "website": website, "email": email,
            "relevance": relevance, "team_page": team_page, "team_size": team_size,
            "linkedin": linkedin, "github_org": github_org, "key_repos": key_repos,
            "sponsors": sponsors, "competitions": competitions,
        }.items() if v is not None}
        services.apply_updates(init, updates, services.UPDATABLE_FIELDS)
        if custom_fields is not None:
            existing = json_parse(init.custom_fields_json)
            existing.update(custom_fields)
            existing = {k: v for k, v in existing.items() if v is not None}
            init.custom_fields_json = json.dumps(existing)
        session.flush()  # triggers after_update → FTS sync automatically
        session.commit()
        detail = services.initiative_detail(init)
        if name is not None and name != old_name:
            detail["warning"] = (
                f"Initiative renamed: '{old_name}' → '{name}'. "
                "Verify this is correct — renaming changes the identity of the record."
            )
        return detail


# ---------------------------------------------------------------------------
# Tools: Enrichment & Scoring
# ---------------------------------------------------------------------------


@mcp.tool()
async def enrich_initiative(initiative_id: int) -> dict:
    """Fetch fresh enrichment data from website, team page, GitHub, and all extra links.

    WHAT: Scrapes the entity's website, team page, GitHub org, and any extra URLs
        stored in extra_links (LinkedIn, HuggingFace, Instagram, etc.).
        Uses Crawl4AI for JS rendering when installed, otherwise falls back to httpx.
        Takes 2-10 seconds. Replaces old enrichments if at least one succeeds.
    WHEN: Call BEFORE score_initiative_tool(). Enrichment data is what the scorer reads.
        For best results, call discover_initiative(id) first to find extra URLs.
    RESPONSE: sources_succeeded lists which sources returned data. sources_not_configured
        lists sources that couldn't run (e.g. no website URL set).
    NEXT: Call score_initiative_tool(id) to score using the enrichment data.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        async with open_crawler() as crawler:
            new = await services.run_enrichment(session, init, crawler=crawler)
        session.commit()

        succeeded = [e.source_type for e in new]
        # Build the set of expected sources
        possible = {"website", "team_page", "github"}
        extra = json_parse(init.extra_links_json)
        if extra:
            possible.update(
                k.removesuffix("_urls").removesuffix("_url")
                for k in extra if extra[k]
            )
        not_configured = []
        if not (init.website or "").strip():
            not_configured.append("website")
        if not (init.team_page or "").strip():
            not_configured.append("team_page")
        if not (init.github_org or "").strip():
            not_configured.append("github")
        failed = sorted(possible - set(succeeded) - set(not_configured))

        result = {
            "initiative_id": init.id, "initiative_name": init.name,
            "enrichments_added": len(new),
            "sources_succeeded": succeeded,
            "sources_failed": failed,
            "sources_not_configured": not_configured,
        }
        if not_configured:
            result["hint"] = (
                f"Set {', '.join(not_configured)} on the initiative via update_initiative() "
                "to enable more enrichment sources. Or run discover_initiative(id) to find URLs."
            )
        return result


@mcp.tool()
async def discover_initiative(initiative_id: int) -> dict:
    """Discover new URLs for an entity via DuckDuckGo search.

    WHAT: Searches DuckDuckGo for the entity name + university, discovers
        platform URLs (LinkedIn, GitHub, HuggingFace, Crunchbase, etc.) not already
        in the profile. Stores discovered URLs in extra_links.
        Rate-limited at ~12 seconds between calls to avoid DuckDuckGo blocks.
    WHEN: Call BEFORE enrich_initiative() when extra_links is empty or sparse.
        Only needs to run once per entity — discovered URLs persist.
    NEXT: Call enrich_initiative(id) to crawl the newly discovered URLs.
    ERRORS: Returns DEPENDENCY_MISSING if duckduckgo-search is not installed.

    Args:
        initiative_id: The numeric ID of the entity.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        try:
            result = await services.run_discovery(session, init)
            session.commit()
            result["initiative_id"] = init.id
            result["initiative_name"] = init.name
            if result["urls_found"] > 0:
                result["hint"] = "Call enrich_initiative(id) to crawl the discovered URLs."
            else:
                result["hint"] = "No new URLs discovered. Try enriching with existing data."
            return result
        except ImportError:
            return _error(
                "duckduckgo-search not installed. Install: pip install 'scout[crawl]'",
                "DEPENDENCY_MISSING",
            )


@mcp.tool()
async def score_initiative_tool(initiative_id: int) -> dict:
    """Score an entity across 3 dimensions (team, tech, opportunity) in parallel.

    WHAT: Makes 3 parallel LLM calls (team, tech, opportunity). Verdict and score are
        computed deterministically from the average grade. Takes 5-15 seconds.
        Requires ANTHROPIC_API_KEY environment variable.
    WHEN: Call AFTER enrich_initiative(). Scoring without enrichment data produces weaker results.
    RESPONSE: Returns verdict (reach_out_now/reach_out_soon/monitor/skip), score (1-5),
        classification, per-dimension grades, reasoning, contact recommendation, and data_gaps.
    ERRORS: Returns {error, error_code: "LLM_ERROR", retryable} on failure.
        If retryable=true, the API call failed transiently — wait and retry.

    Args:
        initiative_id: The numeric ID of the entity to score.
    """
    key_err = _check_api_key()
    if key_err:
        return key_err
    with session_scope() as session:
        try:
            init, err = _get_or_error(session, Initiative, initiative_id)
            if err:
                return err
            outreach = await services.run_scoring(
                session, init, entity_type=get_entity_type(),
            )
            session.commit()
            result = services.score_response_dict(outreach, extended=True)
            result["initiative_id"] = init.id
            result["initiative_name"] = init.name
            return result
        except Exception as exc:
            return _llm_error(exc)


@mcp.tool()
def get_scoring_dossier(initiative_id: int) -> dict:
    """Build scoring dossiers and prompts for an entity WITHOUT making LLM calls.

    WHAT: Returns the 3 dimension dossiers (team, tech, opportunity) and their
        system prompts so the calling LLM can evaluate them directly.
        No API key required — all data is assembled locally.
    WHEN: Use when no LLM API key is configured, or when you want the calling
        LLM (e.g. Claude Code) to perform the scoring itself.
    NEXT: Evaluate each dimension, then call submit_score() with the results.

    Args:
        initiative_id: The numeric ID of the entity.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        enrichments = session.execute(
            select(Enrichment).where(Enrichment.initiative_id == init.id)
        ).scalars().all()
        prompts = services.load_scoring_prompts(session)
        et = get_entity_type()
        defaults = default_prompts_for(et)

        team_prompt = prompts.get("team", defaults["team"][1])
        tech_prompt = prompts.get("tech", defaults["tech"][1])
        opp_prompt = prompts.get("opportunity", defaults["opportunity"][1])
        return {
            "initiative_id": init.id,
            "initiative_name": init.name,
            "entity_type": et,
            "enriched": len(enrichments) > 0,
            "dimensions": {
                "team": {"prompt": team_prompt, "dossier": build_team_dossier(init, enrichments, et)},
                "tech": {"prompt": tech_prompt, "dossier": build_tech_dossier(init, enrichments, et)},
                "opportunity": {"prompt": opp_prompt, "dossier": build_full_dossier(init, enrichments, et)},
            },
            "hint": (
                "Evaluate each dimension per its prompt, then call submit_score() with "
                "grade_team, grade_tech, grade_opportunity, classification, "
                "contact_who, contact_channel, engagement_hook."
            ),
        }


@mcp.tool()
def submit_score(
    initiative_id: int,
    grade_team: str, grade_tech: str, grade_opportunity: str,
    classification: str,
    contact_who: str = "", contact_channel: str = "website_form",
    engagement_hook: str = "", reasoning: str = "",
) -> dict:
    """Submit externally-evaluated scores for an entity. No LLM call needed.

    WHAT: Validates grades, computes verdict/score deterministically, and saves
        the OutreachScore. Use after get_scoring_dossier() when the calling LLM
        has evaluated the dossiers.
    WHEN: Use as the second step of LLM-free scoring (after get_scoring_dossier).

    Args:
        initiative_id: The numeric ID of the entity.
        grade_team: Team grade (A+, A, A-, B+, B, B-, C+, C, C-, D).
        grade_tech: Tech grade (same scale).
        grade_opportunity: Opportunity grade (same scale).
        classification: Entity classification (varies by entity type). Use get_scoring_dossier() to see valid values.
        contact_who: Recommended contact person/role.
        contact_channel: One of: email, linkedin, event, website_form.
        engagement_hook: Suggested opening line for outreach.
        reasoning: Brief reasoning for the opportunity assessment.
    """
    # Parse grades (Grade.parse never fails — falls back to "C")
    grades = {
        "team": Grade.parse(grade_team),
        "tech": Grade.parse(grade_tech),
        "opportunity": Grade.parse(grade_opportunity),
    }
    # Reject truly invalid input (typos, garbage) — Grade.parse defaults to C
    for label, raw in [("grade_team", grade_team), ("grade_tech", grade_tech),
                       ("grade_opportunity", grade_opportunity)]:
        normalized = raw.strip().upper().replace(" ", "")
        if normalized not in VALID_GRADES:
            return _error(f"Invalid {label}: {raw!r}. Valid: {', '.join(sorted(VALID_GRADES))}",
                          "VALIDATION_ERROR")

    classification = classification.strip().lower()
    valid_cls = valid_classifications(get_entity_type())
    if classification not in valid_cls:
        return _error(f"Invalid classification: {classification!r}. Valid: {', '.join(sorted(valid_cls))}",
                      "VALIDATION_ERROR")

    contact_channel = contact_channel.strip().lower()
    if contact_channel and contact_channel not in VALID_CHANNELS:
        return _error(f"Invalid contact_channel: {contact_channel!r}. Valid: {', '.join(sorted(VALID_CHANNELS))}",
                      "VALIDATION_ERROR")

    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err

        enrichments = list(session.execute(
            select(Enrichment).where(Enrichment.initiative_id == init.id)
        ).scalars().all())

        outreach = create_score_from_grades(
            init, enrichments, grades,
            classification=classification, contact_who=contact_who,
            contact_channel=contact_channel, engagement_hook=engagement_hook,
            reasoning=reasoning, entity_type=get_entity_type(),
        )

        # Delete existing initiative-level scores, then save
        session.execute(delete(OutreachScore).where(
            OutreachScore.initiative_id == init.id,
            OutreachScore.project_id.is_(None),
        ))
        session.add(outreach)
        session.commit()

        return {
            "initiative_id": init.id, "initiative_name": init.name,
            "verdict": outreach.verdict, "score": outreach.score,
            "classification": outreach.classification,
            "grade_team": outreach.grade_team, "grade_tech": outreach.grade_tech,
            "grade_opportunity": outreach.grade_opportunity,
        }


# ---------------------------------------------------------------------------
# Tools: Batch Operations
# ---------------------------------------------------------------------------


@mcp.tool()
async def batch_enrich(initiative_ids: str | None = None, limit: int = 20) -> dict:
    """Enrich multiple entities in one call, sharing a single web crawler.

    WHAT: Runs web/GitHub enrichment for a batch of entities (3 concurrent).
        Shares one Crawl4AI browser instance for efficiency. Returns compact
        status per item — no full enrichment details.
    WHEN: Use instead of calling enrich_initiative() in a loop. Much faster, fewer tokens.
    AUTO: If no initiative_ids given, auto-selects from work queue (items needing enrichment).

    Args:
        initiative_ids: Comma-separated entity IDs, e.g. "1,2,3". If omitted, picks from work queue.
        limit: Max items to process (1-50, default 20). Applies when auto-selecting from queue.
    """
    limit = max(1, min(limit, 50))
    ids = _parse_ids(initiative_ids)

    with session_scope() as session:
        if ids is None:
            queue = services.get_work_queue(session, limit)
            ids = [item["id"] for item in queue if item["needs_enrichment"]]
        if not ids:
            return {"processed": 0, "succeeded": 0, "failed": 0, "results": [],
                    "hint": f"No {_entity_cfg()['label_plural']} need enrichment. Try batch_score() instead."}
        ids = ids[:limit]

    async def _do_enrich(s, init, *, crawler=None):
        new = await services.run_enrichment(s, init, crawler=crawler)
        if new:
            return {"sources": len(new)}
        return {"ok": False, "sources": 0,
                "warning": "No data fetched — add website/github URLs or run discover_initiative() first"}

    async with open_crawler() as crawler:
        results = await _run_batch(ids, _do_enrich, concurrency=3, crawler=crawler)

    ok, failed = _batch_summary(results)
    result = {"processed": len(ids), "succeeded": ok, "failed": failed, "results": results}
    if ok > 0:
        result["hint"] = "Call batch_score() next to score the enriched initiatives."
    return result


@mcp.tool()
async def batch_score(initiative_ids: str | None = None, limit: int = 20) -> dict:
    """Score multiple entities in one call, sharing a single LLM client.

    WHAT: Runs LLM scoring for a batch of entities sequentially (3 parallel dimension
        calls per entity). Returns compact verdict+score per item — no reasoning or evidence.
    WHEN: Use instead of calling score_initiative_tool() in a loop. Saves tokens significantly.
    AUTO: If no initiative_ids given, auto-selects from work queue (enriched but unscored).
    PREREQ: Entities should be enriched first. Use batch_enrich() or process_queue().

    Args:
        initiative_ids: Comma-separated entity IDs, e.g. "1,2,3". If omitted, picks from work queue.
        limit: Max items to process (1-50, default 20). Applies when auto-selecting from queue.
    """
    key_err = _check_api_key()
    if key_err:
        return key_err

    limit = max(1, min(limit, 50))
    ids = _parse_ids(initiative_ids)

    with session_scope() as session:
        if ids is None:
            queue = services.get_work_queue(session, limit)
            ids = [item["id"] for item in queue if item["needs_scoring"]]
        if not ids:
            return {"processed": 0, "succeeded": 0, "failed": 0,
                    "results": [], "summary": {},
                    "hint": f"No {_entity_cfg()['label_plural']} need scoring."}
        ids = ids[:limit]

    client = LLMClient()
    et = get_entity_type()

    async def _do_score(s, init, *, client=None, entity_type="initiative"):
        outreach = await services.run_scoring(s, init, client, entity_type=entity_type)
        return {"verdict": outreach.verdict, "score": outreach.score,
                "classification": outreach.classification}

    results = await _run_batch(ids, _do_score, concurrency=1, client=client, entity_type=et)
    ok, failed = _batch_summary(results)
    verdict_counts: dict[str, int] = {}
    for r in results:
        if r.get("ok") and "verdict" in r:
            v = r["verdict"]
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

    return {"processed": len(ids), "succeeded": ok, "failed": failed,
            "results": results, "summary": verdict_counts}


@mcp.tool()
async def process_queue(limit: int = 20, discover: bool = False, enrich: bool = True, score: bool = True) -> dict:
    """Autonomous pipeline: fetch work queue, optionally discover URLs, enrich, then score.

    WHAT: Fetches the work queue, enriches items that need it, then scores items that
        need it (including freshly enriched ones). Returns compact results per step.
        This is the recommended tool for autonomous bulk processing.
    WHEN: Use as the primary autonomous workflow tool. One call processes a full batch.
        Call repeatedly until remaining_in_queue reaches 0.
    RESPONSE: Enrichment counts + per-item scoring verdicts (compact, no reasoning).

    Args:
        limit: Max items to process (1-50, default 20).
        discover: Whether to run DuckDuckGo URL discovery before enrichment (default false).
            Useful for professors or new entities with sparse extra_links.
            Rate-limited (~12s per item), runs serially. Only discovers for items needing enrichment.
        enrich: Whether to run enrichment step (default true).
        score: Whether to run scoring step (default true). Requires ANTHROPIC_API_KEY.
    """
    if score:
        key_err = _check_api_key()
        if key_err:
            return key_err

    limit = max(1, min(limit, 50))
    et = get_entity_type()

    with session_scope() as session:
        queue = services.get_work_queue(session, limit)
        stats = services.compute_stats(session)

    if not queue:
        return {"enrichment": None, "scoring": None, "remaining_in_queue": 0,
                "hint": f"Work queue is empty. All {_entity_cfg()['label_plural']} are processed."}

    enrich_ids = [item["id"] for item in queue if item["needs_enrichment"]]
    score_only_ids = [item["id"] for item in queue if item["needs_scoring"] and not item["needs_enrichment"]]

    discover_result = None
    enrich_result = None
    score_result = None

    # Step 0: Discovery (serial, rate-limited)
    if discover and enrich_ids:
        async def _do_discover(s, init):
            result = await services.run_discovery(s, init)
            return {"urls_found": result["urls_found"]}

        try:
            disc_results = await _run_batch(enrich_ids, _do_discover, concurrency=1)
            disc_ok = sum(1 for r in disc_results if r.get("ok") and r.get("urls_found", 0) > 0)
            discover_result = {"processed": len(enrich_ids), "urls_found": disc_ok,
                               "no_new_urls": len(enrich_ids) - disc_ok}
        except ImportError:
            discover_result = {"skipped": True, "reason": "duckduckgo-search not installed"}

    # Step 1: Enrich
    if enrich and enrich_ids:
        async def _do_enrich(s, init, *, crawler=None):
            new = await services.run_enrichment(s, init, crawler=crawler)
            if new:
                return {"sources": len(new)}
            return {"ok": False, "reason": "all sources returned empty"}

        async with open_crawler() as crawler:
            enrich_results = await _run_batch(enrich_ids, _do_enrich, concurrency=3, crawler=crawler)

        enrich_ok, enrich_failed = _batch_summary(enrich_results)
        enrich_result = {"processed": len(enrich_ids), "succeeded": enrich_ok, "failed": enrich_failed}
        enrich_failures = [r for r in enrich_results if not r.get("ok")]
        if enrich_failures:
            enrich_result["failed_items"] = enrich_failures
    else:
        enrich_failures = []

    # After enrichment, freshly-enriched items now need scoring (skip failed enrichments)
    failed_ids = {f["id"] for f in enrich_failures}
    score_ids = score_only_ids + [i for i in enrich_ids if i not in failed_ids] if enrich else score_only_ids

    # Step 2: Score
    if score and score_ids:
        client = LLMClient()

        async def _do_score(s, init, *, client=None, entity_type="initiative"):
            outreach = await services.run_scoring(s, init, client, entity_type=entity_type)
            return {"verdict": outreach.verdict, "score": outreach.score,
                    "classification": outreach.classification}

        score_results = await _run_batch(score_ids, _do_score, concurrency=1,
                                         client=client, entity_type=et)
        score_ok, score_failed = _batch_summary(score_results)
        verdict_counts: dict[str, int] = {}
        for r in score_results:
            if r.get("ok") and "verdict" in r:
                v = r["verdict"]
                verdict_counts[v] = verdict_counts.get(v, 0) + 1

        score_result = {"processed": len(score_ids), "succeeded": score_ok,
                        "failed": score_failed, "results": score_results,
                        "summary": verdict_counts}

    remaining = max(0, (stats["total"] - stats["scored"])
                    - (score_result["succeeded"] if score_result else 0))
    result: dict = {"discovery": discover_result, "enrichment": enrich_result,
                    "scoring": score_result, "remaining_in_queue": remaining}
    if not score:
        result["hint"] = "Enrichment done. Scoring was skipped (score=False)."
    elif remaining > 0:
        result["hint"] = "Call process_queue() again to process the next batch."
    else:
        result["hint"] = f"All {_entity_cfg()['label_plural']} processed. Use list_initiatives(verdict='reach_out_now') to review top results."
    return result


# ---------------------------------------------------------------------------
# Tools: Similarity & Embeddings
# ---------------------------------------------------------------------------


@mcp.tool()
def find_similar_initiatives(
    query: str | None = None, initiative_id: int | None = None,
    uni: str | None = None, verdict: str | None = None,
    limit: int = 10,
) -> dict:
    """Find entities similar to a query or another entity using semantic embeddings.

    WHAT: Semantic similarity search using dense embeddings (model2vec). Returns ranked results
        with similarity scores. Supports hybrid mode: SQL pre-filters + semantic ranking.
    WHEN: Use to discover related entities, find thematic clusters, or answer
        "show me entities similar to X".
    PREREQ: Embeddings are auto-built during enrichment. Run embed_all() to rebuild all at once.
    NEXT: get_initiative(id) to inspect top results.

    Args:
        query: Free-text search query (e.g. "robotics research lab"). Either query or initiative_id required.
        initiative_id: Find entities similar to this one. Either query or initiative_id required.
        uni: Pre-filter by university before ranking (comma-separated).
        verdict: Pre-filter by verdict before ranking (comma-separated).
        limit: Max results (default 10, max 100).
    """
    from scout.embedder import find_similar

    with session_scope() as session:
        # Build optional ID mask from SQL filters
        id_mask = None
        if uni or verdict:
            q_filter = select(Initiative.id)
            if uni:
                us = {u.strip().upper() for u in uni.split(",")}
                q_filter = q_filter.where(func.upper(Initiative.uni).in_(us))
            if verdict:
                ls = services._latest_score_subquery()
                vs = {v.strip().lower() for v in verdict.split(",")}
                q_filter = q_filter.join(
                    ls, and_(Initiative.id == ls.c.initiative_id, ls.c.rn == 1)
                ).where(ls.c.verdict.in_(vs))
            rows = session.execute(q_filter).scalars().all()
            id_mask = set(rows)
            if not id_mask:
                return {"results": [], "hint": f"No {_entity_cfg()['label_plural']} match the pre-filters."}

        results = find_similar(
            query_text=query, initiative_id=initiative_id,
            top_k=max(1, min(limit, 100)), id_mask=id_mask,
        )

        if not results:
            return {"results": [], "hint": "No embeddings found. Run embed_all() first."}

        # Enrich with names
        ids = [r[0] for r in results]
        inits = session.execute(
            select(Initiative.id, Initiative.name, Initiative.uni)
            .where(Initiative.id.in_(ids))
        ).all()
        name_map = {r.id: (r.name, r.uni) for r in inits}

        return {"results": [
            {"id": rid, "name": name_map.get(rid, ("?", "?"))[0],
             "uni": name_map.get(rid, ("?", "?"))[1], "similarity": score}
            for rid, score in results
        ]}


@mcp.tool()
def embed_all_tool() -> dict:
    """Build or rebuild dense embeddings for all entities.

    WHAT: Encodes all entities into dense vectors using model2vec (local, ~15MB model).
        Embeddings are stored as .npy files alongside the database. Takes ~1 second for 200 entities.
    WHEN: Embeddings auto-update on each enrichment. Use this tool to rebuild all at once
        (e.g. after bulk import or if sidecar files are deleted). Re-run is safe (overwrites).
    NEXT: Use find_similar_initiatives() for semantic search.
    """
    from scout.embedder import embed_all
    with session_scope() as session:
        try:
            count = embed_all(session)
        except Exception as exc:
            return _error(f"Embedding failed: {exc}", "EMBEDDING_ERROR")
        return {"ok": True, "embedded": count, "hint": "Use find_similar_initiatives() for semantic search."}


# ---------------------------------------------------------------------------
# Tools: Stats
# ---------------------------------------------------------------------------


@mcp.tool()
def get_aggregations() -> dict:
    """Get analytical aggregations for zoom-out analysis.

    WHAT: Score distributions by uni/faculty, top-10 per verdict, grade distributions, unprocessed counts.
    WHEN: Use to zoom out before drilling in. Call after get_stats() for a deeper analytical overview.
    NEXT: list_initiatives() with filters to investigate interesting segments.
    """
    with session_scope() as session:
        return services.compute_aggregations(session)


@mcp.tool()
def get_stats() -> dict:
    """Get summary statistics about all entities in the database.

    WHAT: Returns counts (total, enriched, scored) and breakdowns by verdict, classification, and uni.
    WHEN: Use as the first call to understand the database state. If scored < total, use get_work_queue()
        to find entities needing work.
    """
    with session_scope() as session:
        return services.compute_stats(session)


# ---------------------------------------------------------------------------
# Tools: Projects
# ---------------------------------------------------------------------------


@mcp.tool()
def create_project(
    initiative_id: int, name: str,
    description: str | None = None, website: str | None = None,
    github_url: str | None = None, team: str | None = None,
) -> dict:
    """Create a new project under an entity.

    WHAT: Creates a sub-project linked to a parent entity.
    WHEN: Use when an entity has distinct sub-projects that should be scored separately.
    NEXT: Call score_project_tool(project_id) to score the project.
    """
    with session_scope() as session:
        _, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        proj = services.create_project(
            session, initiative_id,
            name=name, description=description,
            website=website, github_url=github_url, team=team,
        )
        session.commit()
        return services.project_summary(proj)


@mcp.tool()
def update_project(
    project_id: int,
    name: str | None = None, description: str | None = None,
    website: str | None = None, github_url: str | None = None, team: str | None = None,
) -> dict:
    """Update fields on a project. Only provided (non-null) arguments are applied.

    WHAT: Modifies project profile data. Returns the updated project summary.
    WHEN: Use to add missing info (website, github_url, team) before scoring.
    """
    with session_scope() as session:
        proj, err = _get_or_error(session, Project, project_id)
        if err:
            return err
        updates = {k: v for k, v in {"name": name, "description": description,
                   "website": website, "github_url": github_url, "team": team}.items()
                   if v is not None}
        services.apply_updates(proj, updates, ("name", "description", "website", "github_url", "team"))
        session.commit()
        return services.project_summary(proj)


@mcp.tool()
def delete_project(project_id: int, confirm: bool = False) -> dict:
    """Delete a project and its associated scores.

    WHAT: Permanently removes a project and its scores. Does not affect the parent initiative.
    SAFETY: You must pass confirm=True to execute. This prevents accidental deletion.

    Args:
        project_id: The numeric ID of the project to delete.
        confirm: Must be True to confirm deletion. Defaults to False (dry run).
    """
    with session_scope() as session:
        proj, err = _get_or_error(session, Project, project_id)
        if err:
            return err
        if not confirm:
            return {
                "ok": False,
                "action": "delete_project",
                "project_id": proj.id,
                "project_name": proj.name,
                "initiative_id": proj.initiative_id,
                "warning": f"This will permanently delete project '{proj.name}' and its scores. "
                           "Call again with confirm=True to proceed.",
            }
        session.delete(proj)
        session.commit()
        return {"ok": True, "deleted_project_id": project_id}


@mcp.tool()
async def score_project_tool(project_id: int) -> dict:
    """Run LLM-based outreach scoring for a project in context of its parent entity.

    WHAT: Single LLM call scoring the project using parent entity context. Takes 5-15 seconds.
    WHEN: Call after creating or updating a project. Requires ANTHROPIC_API_KEY.
    ERRORS: Returns {error, error_code: "LLM_ERROR", retryable} on failure.
    """
    key_err = _check_api_key()
    if key_err:
        return key_err
    with session_scope() as session:
        try:
            proj, err = _get_or_error(session, Project, project_id)
            if err:
                return err
            init, err = _get_or_error(session, Initiative, proj.initiative_id)
            if err:
                return err
            outreach = await services.run_project_scoring(
                session, proj, init, entity_type=get_entity_type(),
            )
            session.commit()
            result = services.score_response_dict(outreach, extended=True)
            result["project_id"] = proj.id
            result["project_name"] = proj.name
            result["initiative_id"] = init.id
            result["initiative_name"] = init.name
            return result
        except Exception as exc:
            return _llm_error(exc)


# ---------------------------------------------------------------------------
# Tools: Scoring Prompts
# ---------------------------------------------------------------------------


@mcp.tool()
def list_scoring_prompts(compact: bool = False) -> list[dict]:
    """List the 3 scoring prompt definitions (team, tech, opportunity).

    WHAT: Returns each prompt's key, label, content (system prompt text), and updated_at.
    WHEN: Use to inspect or audit how the LLM evaluates each dimension before scoring.
    NEXT: Use update_scoring_prompt(key, content) to customize a dimension's evaluation criteria.

    Args:
        compact: If true, returns only key, label, and updated_at (no prompt content). Default false.
    """
    with session_scope() as session:
        prompts = services.get_scoring_prompts(session)
        if compact:
            return [{"key": p["key"], "label": p["label"], "updated_at": p["updated_at"]} for p in prompts]
        return prompts


@mcp.tool()
def update_scoring_prompt(key: str, content: str) -> dict:
    """Update the system prompt for a scoring dimension.

    WHAT: Replaces the system prompt used by the LLM when evaluating this dimension.
    WHEN: Use to customize evaluation criteria, change grading emphasis, or add context.
    NEXT: Re-score initiatives with score_initiative_tool() to apply the new prompt.

    Args:
        key: Dimension key — one of "team", "tech", or "opportunity".
        content: New system prompt text. Must include JSON response format instructions.
    """
    with session_scope() as session:
        result = services.update_scoring_prompt(session, key, content)
        if result is None:
            return _error(f"Scoring prompt '{key}' not found", "NOT_FOUND")
        session.commit()
        return result


# ---------------------------------------------------------------------------
# Tools: Export
# ---------------------------------------------------------------------------


@mcp.tool()
def export_initiatives(
    verdict: str | None = None,
    uni: str | None = None,
    include_enrichments: bool = True,
    include_scores: bool = True,
    include_extras: bool = False,
) -> dict:
    """Export entities to an XLSX file saved in the data directory.

    WHAT: Generates a spreadsheet with entity profiles, scores, and enrichment summaries.
        Saves the file to the Scout data directory and returns the file path.
    WHEN: Use to export data for sharing, reporting, or offline analysis.

    Args:
        verdict: Comma-separated verdict filter (e.g. "reach_out_now,reach_out_soon"). None = all.
        uni: Comma-separated uni filter (e.g. "TUM,LMU"). None = all.
        include_enrichments: Include enrichment summary column. Default true.
        include_scores: Include score columns (verdict, grades, reasoning). Default true.
        include_extras: Include extra profile fields (domains, member count). Default false.
    """
    from scout.db import DATA_DIR, current_db_name
    from scout.exporter import export_xlsx

    with session_scope() as session:
        buf = export_xlsx(
            session, verdict=verdict, uni=uni,
            include_enrichments=include_enrichments,
            include_scores=include_scores, include_extras=include_extras,
        )
    db_name = current_db_name()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"scout-{db_name}-{ts}.xlsx"
    out_path = DATA_DIR / filename
    out_path.write_bytes(buf.getvalue())
    return {
        "ok": True,
        "file": str(out_path),
        "filename": filename,
        "hint": f"File saved to {out_path}. Open in Excel or Google Sheets.",
    }


# ---------------------------------------------------------------------------
# Tools: Databases
# ---------------------------------------------------------------------------


@mcp.tool()
def list_scout_databases() -> dict:
    """List all available Scout databases and show which one is currently active.

    WHAT: Returns database names and highlights the active one.
    WHEN: Use at start of session to see available datasets.
    """
    return {"databases": list_databases(), "current": current_db_name()}


@mcp.tool()
def select_scout_database(name: str) -> dict:
    """Switch to a different Scout database. Creates it if it doesn't exist.

    WHAT: Changes the active database. All subsequent tool calls operate on this database.
    WHEN: Use to switch between different datasets (initiatives, professors, etc.).
    """
    try:
        name = validate_db_name(name)
    except ValueError as exc:
        return _error(str(exc), "VALIDATION_ERROR")
    switch_db(name)
    et = get_entity_type()
    mcp._mcp_server.instructions = _build_instructions(et)
    return {"current": current_db_name(), "entity_type": et, "message": f"Switched to database '{name}'"}


@mcp.tool()
def create_scout_database(name: str, entity_type: str = "initiative") -> dict:
    """Create a new empty Scout database and switch to it.

    WHAT: Creates a fresh database file and switches to it. The entity_type
        determines default scoring prompts and classification categories.
    WHEN: Use when starting a new dataset or a different entity type (e.g. professors).

    Args:
        name: Database name (letters, numbers, hyphens, underscores only).
        entity_type: Entity type for this database. 'initiative' (default) or 'professor'.
    """
    try:
        name = validate_db_name(name)
    except ValueError as exc:
        return _error(str(exc), "VALIDATION_ERROR")
    if entity_type not in ENTITY_CONFIG:
        return _error(
            f"Unknown entity_type: {entity_type!r}. Valid: {', '.join(sorted(ENTITY_CONFIG))}",
            "VALIDATION_ERROR",
        )
    try:
        create_database(name, entity_type=entity_type)
    except ValueError as exc:
        return _error(str(exc), "ALREADY_EXISTS")
    # Update MCP instructions for new entity type
    mcp._mcp_server.instructions = _build_instructions(entity_type)
    return {
        "current": current_db_name(),
        "entity_type": entity_type,
        "message": f"Created and switched to '{name}' database (entity type: {entity_type})",
    }


@mcp.tool()
async def scrape_tum_professors(school: str | None = None, limit: int = 50) -> dict:
    """Scrape TUM professor directory and import professors into the database.

    WHAT: Fetches professor names and schools from professoren.tum.de,
        creates records for each professor.
    WHEN: Use to bootstrap a professor database. Run once, then enrich and score.
    PREREQ: Current database should have entity_type='professor'. If not, create one
        first with create_scout_database('professors', entity_type='professor').

    Args:
        school: Filter by TUM school abbreviation: CIT, ED, LS, MGT, MED, NAT. None = all schools.
        limit: Max professors to import (default 50).
    """
    try:
        from scout.scrapers import scrape_tum_professors as _scrape
    except ImportError as exc:
        return _error(f"Scraper dependency missing: {exc}", "DEPENDENCY_MISSING")

    try:
        professors = await _scrape()
    except Exception as exc:
        return _error(f"Scrape failed: {exc}", "SCRAPE_ERROR", retryable=True)

    if school:
        professors = [p for p in professors if p.get("faculty", "").upper() == school.upper()]
    professors = professors[:max(1, min(limit, 500))]

    created = 0
    skipped = 0
    with session_scope() as session:
        for prof in professors:
            existing = session.execute(
                select(Initiative).where(Initiative.name == prof["name"])
            ).scalars().first()
            if existing:
                skipped += 1
                continue
            init = Initiative(
                name=prof["name"],
                uni=prof.get("uni", "TUM"),
                faculty=prof.get("faculty", ""),
                website=prof.get("website", ""),
            )
            session.add(init)
            created += 1
        session.commit()

    return {
        "created": created,
        "skipped_duplicates": skipped,
        "total_found": len(professors),
        "hint": "Call process_queue() to enrich and score the imported professors.",
    }


@mcp.tool()
def get_custom_columns() -> list[dict]:
    """List custom column definitions for the current database.

    WHAT: Returns all user-defined columns with their types and display settings.
    WHEN: Use before create/update to see what columns already exist.
    """
    with session_scope() as session:
        return services.get_custom_columns(session)


@mcp.tool()
def create_custom_column(
    key: str, label: str,
    col_type: str = "text", show_in_list: bool = True, sort_order: int = 0,
) -> dict:
    """Create a new custom column definition.

    WHAT: Adds a user-defined field that can store per-entity data.
    WHEN: Use when you need to track additional attributes not covered by built-in fields.

    Args:
        key: Unique machine-readable key (lowercase, no spaces, e.g. "funding_stage").
        label: Human-readable display label (e.g. "Funding Stage").
        col_type: Column type — "text", "number", "boolean", or "url". Default "text".
        show_in_list: Whether to show in the initiative list view. Default true.
        sort_order: Display order (lower = first). Default 0.
    """
    with session_scope() as session:
        result = services.create_custom_column(
            session, key=key, label=label, col_type=col_type,
            show_in_list=show_in_list, sort_order=sort_order,
        )
        if result is None:
            return _error(f"Column key '{key}' already exists", "ALREADY_EXISTS")
        session.commit()
        return result


@mcp.tool()
def update_custom_column(
    column_id: int,
    label: str | None = None, col_type: str | None = None,
    show_in_list: bool | None = None, sort_order: int | None = None,
) -> dict:
    """Update a custom column definition. Only provided (non-null) arguments are applied.

    WHAT: Modifies an existing custom column's display settings.
    WHEN: Use to rename, change type, or reorder columns.

    Args:
        column_id: The numeric ID of the custom column.
        label: New display label.
        col_type: New column type — "text", "number", "boolean", or "url".
        show_in_list: Whether to show in the list view.
        sort_order: Display order (lower = first).
    """
    with session_scope() as session:
        result = services.update_custom_column(
            session, column_id,
            label=label, col_type=col_type,
            show_in_list=show_in_list, sort_order=sort_order,
        )
        if result is None:
            return _error(f"Custom column {column_id} not found", "NOT_FOUND")
        session.commit()
        return result


@mcp.tool()
def delete_custom_column(column_id: int) -> dict:
    """Delete a custom column definition.

    WHAT: Removes a custom column definition. Note: stored values on initiatives are kept.
    WHEN: Use when a custom column is no longer needed.

    Args:
        column_id: The numeric ID of the custom column to delete.
    """
    with session_scope() as session:
        if not services.delete_custom_column(session, column_id):
            return _error(f"Custom column {column_id} not found", "NOT_FOUND")
        session.commit()
        return {"ok": True, "deleted_column_id": column_id}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Scout MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
