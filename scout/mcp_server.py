from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from scout import services
from scout.db import DB_NAME_RE, current_db_name, get_session, init_db, list_databases, switch_db
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
        "Use these tools to list, inspect, enrich, and score initiatives. "
        "Start with get_stats() for an overview, then list_initiatives() to browse, "
        "then get_initiative(id) for full details."
    ),
    lifespan=scout_lifespan,
    json_response=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _session():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _get_or_error(session, model, entity_id, label="Entity"):
    obj = session.execute(select(model).where(model.id == entity_id)).scalars().first()
    if not obj:
        return None, {"error": f"{label} {entity_id} not found"}
    return obj, None


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
        },
        "workflow": [
            "0. list_scout_databases() — see available databases. select_scout_database(name) to switch.",
            "1. get_stats() — see how many initiatives exist and scoring coverage.",
            "2. list_initiatives() — browse with filters (verdict, uni, classification, search).",
            "3. get_initiative(id) — full details with enrichments and scores.",
            "4. enrich_initiative(id) — fetch fresh web/GitHub data.",
            "5. score_initiative_tool(id) — get LLM-powered outreach recommendation.",
            "6. update_initiative(id, ...) — correct or add information.",
        ],
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
    uni: str | None = None, search: str | None = None,
    sort_by: str = "score", sort_dir: str = "desc", limit: int = 50,
) -> list[dict]:
    """List and filter student initiatives.

    Args:
        verdict: Filter by outreach verdict. Comma-separated from:
                 reach_out_now, reach_out_soon, monitor, skip, unscored.
        classification: Filter by type. Comma-separated from:
                        deep_tech, student_venture, applied_research, student_club, dormant.
        uni: Filter by university. Comma-separated, e.g. "TUM,LMU".
        search: Free-text search across name, description, and sector.
        sort_by: Sort field: score, name, uni, verdict, grade_team, grade_tech, grade_opportunity.
        sort_dir: Sort direction: asc or desc.
        limit: Max results (default 50, max 500).
    """
    with _session() as session:
        items, _ = services.query_initiatives(
            session, verdict=verdict, classification=classification,
            uni=uni, search=search, sort_by=sort_by, sort_dir=sort_dir,
            page=1, per_page=max(1, min(limit, 500)),
        )
        return items


@mcp.tool()
def get_initiative(initiative_id: int) -> dict:
    """Get full details for a single initiative including enrichments, projects, and scores."""
    with _session() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        return err if err else services.initiative_detail(init)


@mcp.tool()
def update_initiative(
    initiative_id: int,
    name: str | None = None, uni: str | None = None, sector: str | None = None,
    mode: str | None = None, description: str | None = None, website: str | None = None,
    email: str | None = None, relevance: str | None = None, team_page: str | None = None,
    team_size: str | None = None, linkedin: str | None = None, github_org: str | None = None,
    key_repos: str | None = None, sponsors: str | None = None, competitions: str | None = None,
) -> dict:
    """Update fields on an initiative. Only provided (non-null) arguments are applied."""
    with _session() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        if err:
            return err
        updates = {k: v for k, v in {
            "name": name, "uni": uni, "sector": sector, "mode": mode,
            "description": description, "website": website, "email": email,
            "relevance": relevance, "team_page": team_page, "team_size": team_size,
            "linkedin": linkedin, "github_org": github_org, "key_repos": key_repos,
            "sponsors": sponsors, "competitions": competitions,
        }.items() if v is not None}
        services.apply_updates(init, updates, services.UPDATABLE_FIELDS)
        session.commit()
        return services.initiative_detail(init)


# ---------------------------------------------------------------------------
# Tools: Enrichment & Scoring
# ---------------------------------------------------------------------------


@mcp.tool()
async def enrich_initiative(initiative_id: int) -> dict:
    """Fetch fresh enrichment data from the initiative's website, team page, and GitHub."""
    with _session() as session:
        init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        if err:
            return err
        new = await services.run_enrichment(session, init)
        session.commit()
        return {
            "initiative_id": init.id, "initiative_name": init.name,
            "enrichments_added": len(new),
            "sources": [e.source_type for e in new],
        }


@mcp.tool()
async def score_initiative_tool(initiative_id: int) -> dict:
    """Run LLM-based outreach scoring for an initiative. Requires ANTHROPIC_API_KEY."""
    with _session() as session:
        try:
            init, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
            if err:
                return err
            outreach = await services.run_scoring(session, init)
            session.commit()
            return {
                "initiative_id": init.id, "initiative_name": init.name,
                "verdict": outreach.verdict, "score": outreach.score,
                "classification": outreach.classification,
                "reasoning": outreach.reasoning,
                "contact_who": outreach.contact_who,
                "contact_channel": outreach.contact_channel,
                "engagement_hook": outreach.engagement_hook,
                "grade_team": outreach.grade_team, "grade_tech": outreach.grade_tech,
                "grade_opportunity": outreach.grade_opportunity,
                "key_evidence": services.json_parse(outreach.key_evidence_json, []),
                "data_gaps": services.json_parse(outreach.data_gaps_json, []),
            }
        except Exception as exc:
            return {"error": f"Scoring failed: {exc}"}


# ---------------------------------------------------------------------------
# Tools: Stats
# ---------------------------------------------------------------------------


@mcp.tool()
def get_stats() -> dict:
    """Get summary statistics about all initiatives in the database."""
    with _session() as session:
        return services.compute_stats(session)


# ---------------------------------------------------------------------------
# Tools: Projects
# ---------------------------------------------------------------------------


@mcp.tool()
def create_project(
    initiative_id: int, name: str,
    description: str = "", website: str = "", github_url: str = "", team: str = "",
) -> dict:
    """Create a new project under an initiative."""
    with _session() as session:
        _, err = _get_or_error(session, Initiative, initiative_id, "Initiative")
        if err:
            return err
        proj = Project(
            initiative_id=initiative_id, name=name, description=description,
            website=website, github_url=github_url, team=team,
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)
        return services.project_summary(proj)


@mcp.tool()
def update_project(
    project_id: int,
    name: str | None = None, description: str | None = None,
    website: str | None = None, github_url: str | None = None, team: str | None = None,
) -> dict:
    """Update fields on a project. Only provided (non-null) arguments are applied."""
    with _session() as session:
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
    """Delete a project and its associated scores."""
    with _session() as session:
        proj, err = _get_or_error(session, Project, project_id, "Project")
        if err:
            return err
        session.delete(proj)
        session.commit()
        return {"ok": True, "deleted_project_id": project_id}


@mcp.tool()
async def score_project_tool(project_id: int) -> dict:
    """Run LLM-based outreach scoring for a project in context of its parent initiative."""
    with _session() as session:
        try:
            proj, err = _get_or_error(session, Project, project_id, "Project")
            if err:
                return err
            init, err = _get_or_error(session, Initiative, proj.initiative_id, "Initiative")
            if err:
                return err
            outreach = await services.run_project_scoring(session, proj, init)
            session.commit()
            return {
                "project_id": proj.id, "project_name": proj.name,
                "initiative_id": init.id, "initiative_name": init.name,
                "verdict": outreach.verdict, "score": outreach.score,
                "classification": outreach.classification,
                "reasoning": outreach.reasoning,
                "grade_team": outreach.grade_team, "grade_tech": outreach.grade_tech,
                "grade_opportunity": outreach.grade_opportunity,
            }
        except Exception as exc:
            return {"error": f"Scoring failed: {exc}"}


# ---------------------------------------------------------------------------
# Tools: Databases
# ---------------------------------------------------------------------------


@mcp.tool()
def list_scout_databases() -> dict:
    """List all available Scout databases and show which one is currently active."""
    return {"databases": list_databases(), "current": current_db_name()}


@mcp.tool()
def select_scout_database(name: str) -> dict:
    """Switch to a different Scout database. Creates it if it doesn't exist."""
    name = name.strip()
    if not name or not DB_NAME_RE.match(name):
        return {"error": "Invalid database name (letters, numbers, hyphens, underscores only)"}
    switch_db(name)
    return {"current": current_db_name(), "message": f"Switched to database '{name}'"}


@mcp.tool()
def get_custom_columns() -> list[dict]:
    """List custom column definitions for the current database."""
    with _session() as session:
        return services.get_custom_columns(session)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Scout MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
