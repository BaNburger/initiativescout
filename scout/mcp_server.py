from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from scout import services
from scout.db import (
    create_database, current_db_name, init_db, list_databases,
    session_scope, switch_db, validate_db_name,
)
from sqlalchemy import func, select

from scout.models import Initiative, Project

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def scout_lifespan(server: FastMCP) -> AsyncIterator[None]:
    init_db()
    yield


mcp = FastMCP(
    "Scout",
    instructions=(
        "Scout is an outreach intelligence tool for Munich student initiatives. "
        "QUICK START: get_stats() → get_work_queue() → follow recommended_action for each item. "
        "AUTONOMOUS: get_work_queue() → enrich_initiative(id) → score_initiative_tool(id) → repeat until queue empty. "
        "NEW DATA: create_initiative(name, uni, website) → enrich_initiative(id) → score_initiative_tool(id). "
        "ANALYTICS: get_stats() → get_aggregations() for score distributions and top-N by verdict. "
        "SIMILARITY: embed_all_tool() → find_similar_initiatives(query='...') for semantic search. "
        "COMPACT: list_initiatives(fields='id,name,verdict,score') to reduce token usage for large lists. "
        "SEARCH: list_initiatives(search='...') uses FTS5 ranked search across name, description, sector, domains, faculty. "
        "ERRORS: All errors return {error, error_code, retryable}. Retry if retryable=true."
    ),
    lifespan=scout_lifespan,
    json_response=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_error(session, model, entity_id, label="Entity"):
    obj = services.get_entity(session, model, entity_id)
    if not obj:
        return None, _error(f"{label} {entity_id} not found", "NOT_FOUND")
    return obj, None


def _error(message: str, error_code: str, retryable: bool = False) -> dict:
    return {"error": message, "error_code": error_code, "retryable": retryable}


def _llm_error(exc: Exception) -> dict:
    """Convert an LLM-related exception into a standard error dict."""
    retryable = getattr(exc, "retryable", False)
    return _error(f"Scoring failed: {exc}", "LLM_ERROR", retryable=retryable)


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


@mcp.resource("scout://overview")
def scout_overview() -> str:
    """Overview of Scout: data model, workflow, and available verdicts."""
    return json.dumps({
        "system": "Scout — Outreach Intelligence for Munich Student Initiatives",
        "description": (
            "Scout discovers, enriches, and scores Munich university student initiatives "
            "for venture outreach. Contains initiative profiles with web/GitHub enrichment "
            "data and LLM-powered outreach verdicts."
        ),
        "data_model": {
            "initiative": "Student initiative at a Munich university (TUM, LMU, HM). Has profile, enrichments, scores, and projects.",
            "enrichment": "Web-scraped data from the initiative's website, team page, or GitHub org.",
            "project": "A sub-project within an initiative. Can be scored independently.",
            "outreach_score": "LLM-generated verdict, score (1-5), classification, reasoning, and engagement recommendations.",
            "custom_column": "User-defined field for tracking additional per-initiative data.",
        },
        "scoring_architecture": {
            "description": "Each initiative is scored on 3 dimensions in parallel via LLM.",
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
            "3. get_work_queue() — get next initiatives needing enrichment or scoring.",
            "4. create_initiative(name, uni, ...) — add new initiatives to track.",
            "5. enrich_initiative(id) — fetch fresh web/GitHub data.",
            "6. score_initiative_tool(id) — score 3 dimensions in parallel.",
            "7. list_initiatives(verdict, ..., fields='id,name,verdict,score') — browse/filter/compact mode.",
            "8. get_initiative(id) — full details with enrichments and scores.",
            "9. embed_all_tool() — build dense embeddings for similarity search.",
            "10. find_similar_initiatives(query='...') — semantic similarity search.",
            "11. update_initiative(id, ...) — correct or add information.",
            "12. list_scoring_prompts() / update_scoring_prompt() — customize dimension prompts.",
            "13. delete_initiative(id) — remove duplicates or irrelevant entries.",
        ],
        "autonomous_workflow": {
            "description": "For AI agents processing initiatives autonomously.",
            "steps": [
                "1. get_stats() — understand database state.",
                "2. get_work_queue(limit=10) — get prioritized items needing work.",
                "3. For each item, follow recommended_action: 'enrich' → enrich_initiative(id), 'score' → score_initiative_tool(id).",
                "4. Repeat get_work_queue() until queue is empty.",
                "5. list_initiatives(verdict='reach_out_now') — review top results.",
            ],
            "new_data_flow": "create_initiative(name, uni, website) → enrich_initiative(id) → score_initiative_tool(id)",
        },
        "search_modes": {
            "keyword": "list_initiatives(search='...') — FTS5-ranked full-text search across name, description, sector, domains, faculty.",
            "semantic": "find_similar_initiatives(query='...') — Dense embedding similarity via model2vec. Run embed_all_tool() first.",
            "similar": "find_similar_initiatives(initiative_id=N) — Find initiatives most similar to a given one.",
            "hybrid": "find_similar_initiatives(query='...', uni='TUM', verdict='reach_out_now') — SQL pre-filter + semantic ranking.",
            "compact": "list_initiatives(fields='id,name,verdict,score') — Return only requested fields to save tokens.",
        },
        "performance_expectations": {
            "enrichment": "2-5 seconds per initiative (web scraping).",
            "scoring": "5-15 seconds per initiative (3 parallel LLM calls).",
            "listing": "Instant (SQL query with FTS5).",
            "embedding": "~1 second for 200 initiatives (model2vec, local).",
            "similarity": "Instant (numpy dot product on pre-computed vectors).",
        },
        "error_handling": {
            "format": "Errors return {error, error_code, retryable}.",
            "codes": {
                "NOT_FOUND": "Entity does not exist.",
                "LLM_ERROR": "LLM API call failed or returned bad output. Check retryable flag.",
                "ALREADY_EXISTS": "Duplicate entity (database or custom column key).",
                "VALIDATION_ERROR": "Invalid input (e.g. bad database name format).",
                "DEPENDENCY_MISSING": "Optional dependency not installed (e.g. model2vec for embeddings).",
            },
        },
        "verdicts": {
            "reach_out_now": "Strong signals, worth a cold email this week.",
            "reach_out_soon": "Promising but needs a trigger event. Queue for next month.",
            "monitor": "Interesting but insufficient evidence. Check back in 3 months.",
            "skip": "Social club, dormant, or out of scope.",
        },
        "classifications": ["deep_tech", "student_venture", "applied_research", "student_club", "dormant"],
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
    sort_by: str = "score", sort_dir: str = "desc", limit: int = 50,
    fields: str | None = None,
) -> list[dict]:
    """List and filter student initiatives.

    WHAT: Returns initiative summaries with scores, classifications, and verdicts.
    WHEN: Use to browse, search, or filter the database. For autonomous processing, use get_work_queue() instead.
    RESPONSE: Each item includes id, name, uni, faculty, verdict, score, classification, enriched status, and grade breakdown.
    COMPACT: Use fields="id,name,verdict,score" to return only those keys (saves tokens for large lists).

    Args:
        verdict: Filter by outreach verdict. Comma-separated from:
                 reach_out_now, reach_out_soon, monitor, skip, unscored.
        classification: Filter by type. Comma-separated from:
                        deep_tech, student_venture, applied_research, student_club, dormant.
        uni: Filter by university. Comma-separated, e.g. "TUM,LMU".
        faculty: Filter by faculty/department. Comma-separated.
        search: Free-text search across name, description, sector, and more (FTS5-ranked).
        sort_by: Sort field: score, name, uni, faculty, verdict, grade_team, grade_tech, grade_opportunity.
        sort_dir: Sort direction: asc or desc.
        limit: Max results (default 50, max 500).
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
def get_initiative(initiative_id: int) -> dict:
    """Get full details for a single initiative including enrichments, projects, and scores.

    WHAT: Returns complete profile, all enrichments, projects, scores, and computed data gaps.
    WHEN: Use after list_initiatives() to inspect a specific initiative before enriching or scoring.
    RESPONSE: verdict=null means unscored. enriched=false means no web data fetched yet.
        data_gaps lists what's missing (e.g. "No GitHub data"). enrichments array shows fetched sources.
    NEXT: If enriched=false, call enrich_initiative(id). If verdict=null, call score_initiative_tool(id).
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        return err if err else services.initiative_detail(init)


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
    """Create a new initiative in the database.

    WHAT: Creates a new initiative record with the given fields.
    WHEN: Use when you discover a new student initiative to track.
    NEXT: Call enrich_initiative(id) to fetch web/GitHub data, then score_initiative_tool(id).

    Args:
        name: Initiative name (required).
        uni: University — typically TUM, LMU, or HM (required).
        sector: Industry sector, e.g. "AI", "FinTech", "BioTech".
        website: Initiative website URL — needed for enrichment.
        github_org: GitHub org or username — needed for tech enrichment.
        email: Contact email address.
        linkedin: LinkedIn URL for the initiative or founder.
        description: Short description of what the initiative does.
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
        detail = services.initiative_detail(init)
        detail["hint"] = "Call enrich_initiative(id) next to fetch web/GitHub data."
        return detail


@mcp.tool()
def delete_initiative(initiative_id: int) -> dict:
    """Delete an initiative and all its enrichments, scores, and projects.

    WHAT: Permanently removes an initiative and all associated data (cascading delete).
    WHEN: Use when an initiative is duplicate, out of scope, or no longer relevant.
    """
    with session_scope() as session:
        if not services.delete_initiative(session, initiative_id):
            return _error(f"Initiative {initiative_id} not found", "NOT_FOUND")
        session.commit()
        return {"ok": True, "deleted_initiative_id": initiative_id}


@mcp.tool()
def get_work_queue(limit: int = 10) -> dict:
    """Get the next initiatives that need enrichment or scoring.

    WHAT: Returns a prioritized queue of initiatives needing work, with recommended actions.
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
) -> dict:
    """Update fields on an initiative. Only provided (non-null) arguments are applied.

    WHAT: Modifies initiative profile data. Returns the full updated detail.
    WHEN: Use to correct data, add missing URLs (website, github_org, linkedin) before enrichment,
        or fill in context (description, sector) before scoring.
    NEXT: If you added website/github_org, call enrich_initiative(id) to fetch fresh data.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        if err:
            return err
        updates = {k: v for k, v in {
            "name": name, "uni": uni, "faculty": faculty, "sector": sector, "mode": mode,
            "description": description, "website": website, "email": email,
            "relevance": relevance, "team_page": team_page, "team_size": team_size,
            "linkedin": linkedin, "github_org": github_org, "key_repos": key_repos,
            "sponsors": sponsors, "competitions": competitions,
        }.items() if v is not None}
        services.apply_updates(init, updates, services.UPDATABLE_FIELDS)
        session.flush()
        try:
            services.sync_fts_update(session, init)
        except Exception:
            log.warning("FTS sync failed for initiative %s", initiative_id, exc_info=True)
        session.commit()
        return services.initiative_detail(init)


# ---------------------------------------------------------------------------
# Tools: Enrichment & Scoring
# ---------------------------------------------------------------------------


@mcp.tool()
async def enrich_initiative(initiative_id: int) -> dict:
    """Fetch fresh enrichment data from the initiative's website, team page, and GitHub.

    WHAT: Scrapes the initiative's website, team page, and GitHub org for text content.
        Takes 2-5 seconds per source. Replaces old enrichments if at least one succeeds.
    WHEN: Call BEFORE score_initiative_tool(). Enrichment data is what the scorer reads.
    RESPONSE: sources_succeeded lists which sources returned data. sources_not_configured
        lists sources that couldn't run (e.g. no website URL set).
    NEXT: Call score_initiative_tool(id) to score using the enrichment data.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        if err:
            return err
        new = await services.run_enrichment(session, init)
        session.commit()

        succeeded = [e.source_type for e in new]
        possible = {"website", "team_page", "github"}
        not_configured = []
        if not (init.website or "").strip():
            not_configured.append("website")
        if not (init.team_page or "").strip():
            not_configured.append("team_page")
        if not (init.github_org or "").strip():
            not_configured.append("github")
        failed = list(possible - set(succeeded) - set(not_configured))

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
                "to enable more enrichment sources."
            )
        return result


@mcp.tool()
async def score_initiative_tool(initiative_id: int) -> dict:
    """Score an initiative across 3 dimensions (team, tech, opportunity) in parallel.

    WHAT: Makes 3 parallel LLM calls (team, tech, opportunity). Verdict and score are
        computed deterministically from the average grade. Takes 5-15 seconds.
        Requires ANTHROPIC_API_KEY environment variable.
    WHEN: Call AFTER enrich_initiative(). Scoring without enrichment data produces weaker results.
    RESPONSE: Returns verdict (reach_out_now/reach_out_soon/monitor/skip), score (1-5),
        classification, per-dimension grades, reasoning, contact recommendation, and data_gaps.
    ERRORS: Returns {error, error_code: "LLM_ERROR", retryable} on failure.
        If retryable=true, the API call failed transiently — wait and retry.

    Args:
        initiative_id: The numeric ID of the initiative to score.
    """
    with session_scope() as session:
        try:
            init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
            if err:
                return err
            outreach = await services.run_scoring(session, init)
            session.commit()
            result = services.score_response_dict(outreach, extended=True)
            result["initiative_id"] = init.id
            result["initiative_name"] = init.name
            return result
        except Exception as exc:
            return _llm_error(exc)


# ---------------------------------------------------------------------------
# Tools: Similarity & Embeddings
# ---------------------------------------------------------------------------


@mcp.tool()
def find_similar_initiatives(
    query: str | None = None, initiative_id: int | None = None,
    uni: str | None = None, verdict: str | None = None,
    limit: int = 10,
) -> dict:
    """Find initiatives similar to a query or another initiative using semantic embeddings.

    WHAT: Semantic similarity search using dense embeddings (model2vec). Returns ranked results
        with similarity scores. Supports hybrid mode: SQL pre-filters + semantic ranking.
    WHEN: Use to discover related initiatives, find thematic clusters, or answer
        "show me initiatives similar to X".
    PREREQ: Run embed_all() first to build embeddings. Returns empty if no embeddings exist.
    NEXT: get_initiative(id) to inspect top results.

    Args:
        query: Free-text search query (e.g. "robotics research lab"). Either query or initiative_id required.
        initiative_id: Find initiatives similar to this one. Either query or initiative_id required.
        uni: Pre-filter by university before ranking (comma-separated).
        verdict: Pre-filter by verdict before ranking (comma-separated).
        limit: Max results (default 10, max 100).
    """
    try:
        from scout.embedder import find_similar
    except ImportError:
        return _error("model2vec not installed. Run: pip install 'scout[embeddings]'", "DEPENDENCY_MISSING")

    with session_scope() as session:
        # Build optional ID mask from SQL filters
        id_mask = None
        if uni or verdict:
            from sqlalchemy import and_
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
                return {"results": [], "hint": "No initiatives match the pre-filters."}

        try:
            results = find_similar(
                query_text=query, initiative_id=initiative_id,
                top_k=max(1, min(limit, 100)), id_mask=id_mask,
            )
        except ImportError:
            return _error("model2vec not installed. Run: pip install 'scout[embeddings]'", "DEPENDENCY_MISSING")

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
    """Build or rebuild dense embeddings for all initiatives.

    WHAT: Encodes all initiatives into dense vectors using model2vec (local, ~15MB model).
        Embeddings are stored as .npy files alongside the database. Takes ~1 second for 200 initiatives.
    WHEN: Run once after importing data, or after significant enrichment changes.
        Re-run is safe (overwrites previous embeddings).
    NEXT: Use find_similar_initiatives() for semantic search.
    """
    try:
        from scout.embedder import embed_all
    except ImportError:
        return _error("model2vec not installed. Run: pip install 'scout[embeddings]'", "DEPENDENCY_MISSING")

    with session_scope() as session:
        try:
            count = embed_all(session)
        except ImportError:
            return _error("model2vec not installed. Run: pip install 'scout[embeddings]'", "DEPENDENCY_MISSING")
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
    """Get summary statistics about all initiatives in the database.

    WHAT: Returns counts (total, enriched, scored) and breakdowns by verdict, classification, and uni.
    WHEN: Use as the first call to understand the database state. If scored < total, use get_work_queue()
        to find initiatives needing work.
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
    """Create a new project under an initiative.

    WHAT: Creates a sub-project linked to a parent initiative.
    WHEN: Use when an initiative has distinct sub-projects that should be scored separately.
    NEXT: Call score_project_tool(project_id) to score the project.
    """
    with session_scope() as session:
        _, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
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
        proj, err = _get_or_error(session, Project, project_id, "Project")
        if err:
            return err
        updates = {k: v for k, v in {"name": name, "description": description,
                   "website": website, "github_url": github_url, "team": team}.items()
                   if v is not None}
        services.apply_updates(proj, updates, ("name", "description", "website", "github_url", "team"))
        session.commit()
        return services.project_summary(proj)


@mcp.tool()
def delete_project(project_id: int) -> dict:
    """Delete a project and its associated scores.

    WHAT: Permanently removes a project and its scores. Does not affect the parent initiative.
    """
    with session_scope() as session:
        proj, err = _get_or_error(session, Project, project_id, "Project")
        if err:
            return err
        session.delete(proj)
        session.commit()
        return {"ok": True, "deleted_project_id": project_id}


@mcp.tool()
async def score_project_tool(project_id: int) -> dict:
    """Run LLM-based outreach scoring for a project in context of its parent initiative.

    WHAT: Single LLM call scoring the project using parent initiative context. Takes 5-15 seconds.
    WHEN: Call after creating or updating a project. Requires ANTHROPIC_API_KEY.
    ERRORS: Returns {error, error_code: "LLM_ERROR", retryable} on failure.
    """
    with session_scope() as session:
        try:
            proj, err = _get_or_error(session, Project, project_id, "Project")
            if err:
                return err
            init, err = _get_or_error(session, Initiative, proj.initiative_id, "Initiative")
            if err:
                return err
            outreach = await services.run_project_scoring(session, proj, init)
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
def list_scoring_prompts() -> list[dict]:
    """List the 3 scoring prompt definitions (team, tech, opportunity).

    WHAT: Returns each prompt's key, label, content (system prompt text), and updated_at.
    WHEN: Use to inspect or audit how the LLM evaluates each dimension before scoring.
    NEXT: Use update_scoring_prompt(key, content) to customize a dimension's evaluation criteria.
    """
    with session_scope() as session:
        return services.get_scoring_prompts(session)


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
        return result


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
    WHEN: Use to switch between different initiative datasets.
    """
    try:
        name = validate_db_name(name)
    except ValueError as exc:
        return _error(str(exc), "VALIDATION_ERROR")
    switch_db(name)
    return {"current": current_db_name(), "message": f"Switched to database '{name}'"}


@mcp.tool()
def create_scout_database(name: str) -> dict:
    """Create a new empty Scout database and switch to it.

    WHAT: Creates a fresh database file and switches to it.
    WHEN: Use when starting a new dataset or separating initiative groups.

    Args:
        name: Database name (letters, numbers, hyphens, underscores only).
    """
    try:
        name = validate_db_name(name)
    except ValueError as exc:
        return _error(str(exc), "VALIDATION_ERROR")
    try:
        create_database(name)
    except ValueError as exc:
        return _error(str(exc), "ALREADY_EXISTS")
    return {"current": current_db_name(), "message": f"Created and switched to database '{name}'"}


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

    WHAT: Adds a user-defined field that can store per-initiative data.
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
    return {"ok": True, "deleted_column_id": column_id}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Scout MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
