from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scout import services
from scout.db import DB_NAME_RE, create_database, current_db_name, get_session, init_db, list_databases, switch_db
from scout.importer import import_xlsx
from scout.models import Enrichment, Initiative, OutreachScore, Project
from scout.schemas import (
    CustomColumnCreate,
    CustomColumnUpdate,
    ImportResult,
    InitiativeDetail,
    InitiativeOut,
    InitiativeUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ScoringPromptUpdate,
    StatsOut,
)
from scout.scorer import LLMClient

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Scout",
    version="0.1.0",
    description=(
        "Outreach intelligence API for Munich student initiatives. "
        "Discover, enrich, and score initiatives for venture outreach. "
        "All endpoints return JSON. No authentication required."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Initiatives", "description": "Browse, search, and update student initiatives."},
        {"name": "Enrichment", "description": "Fetch live web and GitHub data for initiatives."},
        {"name": "Scoring", "description": "LLM-powered outreach scoring. Requires ANTHROPIC_API_KEY."},
        {"name": "Projects", "description": "Manage sub-projects within initiatives."},
        {"name": "Import", "description": "Bulk import initiatives from XLSX spreadsheets."},
        {"name": "Stats", "description": "Aggregate statistics and breakdowns."},
        {"name": "Databases", "description": "Manage multiple Scout databases and custom columns."},
        {"name": "Admin", "description": "Administrative operations."},
    ],
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Dependencies & Helpers
# ---------------------------------------------------------------------------


def db_session() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_or_404(session: Session, model, entity_id: int, label: str = "Entity"):
    obj = session.execute(select(model).where(model.id == entity_id)).scalars().first()
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


# ---------------------------------------------------------------------------
# Routes: Static
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Scout</h1><p>index.html not found</p>", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Routes: Import
# ---------------------------------------------------------------------------


@app.post("/api/import", response_model=ImportResult,
         tags=["Import"], summary="Import initiatives from XLSX spreadsheet")
async def import_file(file: UploadFile = File(...), session: Session = Depends(db_session)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx files are supported")
    content = await file.read()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = Path(f.name)
            f.write(content)
        return import_xlsx(tmp_path, session)
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes: Initiatives
# ---------------------------------------------------------------------------


class InitiativeListResponse(BaseModel):
    items: list[InitiativeOut]
    total: int


@app.get("/api/initiatives", response_model=InitiativeListResponse,
         tags=["Initiatives"], summary="List initiatives with filtering, sorting, and pagination")
async def list_initiatives(
    verdict: str | None = Query(None, description="Comma-separated: reach_out_now, reach_out_soon, monitor, skip, unscored"),
    classification: str | None = Query(None, description="Comma-separated: deep_tech, student_venture, applied_research, student_club, dormant"),
    uni: str | None = Query(None, description="Comma-separated: TUM, LMU, HM"),
    search: str | None = Query(None, description="Free-text search across name, description, and sector"),
    sort_by: str = Query("score", description="Sort field: score, name, uni, verdict, grade_team, grade_tech, grade_opportunity"),
    sort_dir: str = Query("desc", description="asc or desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(200, ge=1, le=500),
    session: Session = Depends(db_session),
):
    items, total = services.query_initiatives(
        session, verdict=verdict, classification=classification,
        uni=uni, search=search, sort_by=sort_by, sort_dir=sort_dir,
        page=page, per_page=per_page,
    )
    return {"items": items, "total": total}


@app.get("/api/initiatives/{initiative_id}", response_model=InitiativeDetail,
         tags=["Initiatives"], summary="Get full initiative detail with enrichments, projects, and scores")
async def get_initiative(initiative_id: int, session: Session = Depends(db_session)):
    return services.initiative_detail(_get_or_404(session, Initiative, initiative_id, "Initiative"))


@app.put("/api/initiatives/{initiative_id}", response_model=InitiativeDetail,
         tags=["Initiatives"], summary="Update initiative fields (partial update, null fields ignored)")
async def update_initiative(initiative_id: int, body: InitiativeUpdate, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    services.apply_updates(init, body.model_dump(), services.UPDATABLE_FIELDS)
    if body.custom_fields is not None:
        existing = services.json_parse(init.custom_fields_json, {})
        existing.update(body.custom_fields)
        existing = {k: v for k, v in existing.items() if v is not None}
        init.custom_fields_json = json.dumps(existing)
    session.commit()
    return services.initiative_detail(init)


# ---------------------------------------------------------------------------
# Routes: Enrichment (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


def _batch_stream(initiative_ids, process_fn, stat_key, delay=0.1):
    """SSE streaming wrapper for batch enrich/score operations."""
    async def stream():
        session = None
        try:
            session = get_session()
            # Load IDs + names upfront so a rollback doesn't expire ORM objects
            query = select(Initiative.id, Initiative.name)
            if initiative_ids:
                query = query.where(Initiative.id.in_(initiative_ids))
            rows = session.execute(query).all()
            total = len(rows)
            ok = failed = 0

            for idx, (init_id, init_name) in enumerate(rows):
                yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'name': init_name})}\n\n"
                try:
                    # Re-fetch a fresh ORM object each iteration
                    init = session.execute(select(Initiative).where(Initiative.id == init_id)).scalars().first()
                    if init is None:
                        failed += 1
                        continue
                    await process_fn(session, init)
                    session.commit()
                    ok += 1
                except Exception as exc:
                    log.warning("Batch %s failed for %s: %s", stat_key, init_name, exc)
                    failed += 1
                    session.rollback()
                await asyncio.sleep(delay)

            yield f"data: {json.dumps({'type': 'complete', 'stats': {stat_key: ok, 'failed': failed}})}\n\n"
        except Exception:
            if session is not None:
                session.rollback()
            raise
        finally:
            if session is not None:
                session.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/enrich/batch", tags=["Enrichment"], summary="Enrich multiple initiatives (SSE progress stream)")
async def enrich_batch(body: dict[str, Any] | None = None):
    return _batch_stream((body or {}).get("initiative_ids"), services.run_enrichment, "enriched")


@app.post("/api/enrich/{initiative_id}", tags=["Enrichment"], summary="Enrich a single initiative from web and GitHub")
async def enrich_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    added = await services.run_enrichment(session, init)
    session.commit()
    return {"enrichments_added": len(added)}


# ---------------------------------------------------------------------------
# Routes: Scoring (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


@app.post("/api/score/batch", tags=["Scoring"], summary="Score multiple initiatives via LLM (SSE progress stream)")
async def score_batch(body: dict[str, Any] | None = None):
    client = LLMClient()

    async def _score_one(session, init):
        await services.run_scoring(session, init, client)

    return _batch_stream((body or {}).get("initiative_ids"), _score_one, "scored", delay=0.3)


@app.post("/api/score/{initiative_id}", tags=["Scoring"], summary="Score a single initiative via LLM")
async def score_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    try:
        outreach = await services.run_scoring(session, init)
        session.commit()
    except Exception as exc:
        raise HTTPException(500, f"Scoring failed: {exc}") from exc
    return {f: getattr(outreach, f) for f in services.SCORE_RESPONSE_FIELDS}


# ---------------------------------------------------------------------------
# Routes: Projects
# ---------------------------------------------------------------------------


@app.get("/api/initiatives/{initiative_id}/projects", response_model=list[ProjectOut],
         tags=["Projects"], summary="List projects for an initiative")
async def list_projects(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    return [services.project_summary(p) for p in init.projects]


@app.post("/api/initiatives/{initiative_id}/projects", response_model=ProjectOut, status_code=201,
          tags=["Projects"], summary="Create a new project under an initiative")
async def create_project(initiative_id: int, body: ProjectCreate, session: Session = Depends(db_session)):
    _get_or_404(session, Initiative, initiative_id, "Initiative")
    proj = Project(
        initiative_id=initiative_id, name=body.name, description=body.description,
        website=body.website, github_url=body.github_url, team=body.team,
        extra_links_json=json.dumps(body.extra_links),
    )
    session.add(proj)
    session.commit()
    session.refresh(proj)
    return services.project_summary(proj)


@app.put("/api/projects/{project_id}", response_model=ProjectOut,
         tags=["Projects"], summary="Update project fields (partial update)")
async def update_project(project_id: int, body: ProjectUpdate, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    services.apply_updates(proj, body.model_dump(), ("name", "description", "website", "github_url", "team"))
    if body.extra_links is not None:
        proj.extra_links_json = json.dumps(body.extra_links)
    session.commit()
    return services.project_summary(proj)


@app.delete("/api/projects/{project_id}", tags=["Projects"], summary="Delete a project and its scores")
async def delete_project(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    session.delete(proj)
    session.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/score", tags=["Scoring", "Projects"],
          summary="Score a project via LLM in context of its parent initiative")
async def score_project_endpoint(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    init = _get_or_404(session, Initiative, proj.initiative_id, "Initiative")
    try:
        outreach = await services.run_project_scoring(session, proj, init)
        session.commit()
    except Exception as exc:
        raise HTTPException(500, f"Scoring failed: {exc}") from exc
    return {f: getattr(outreach, f) for f in services.SCORE_RESPONSE_FIELDS}


# ---------------------------------------------------------------------------
# Routes: Databases
# ---------------------------------------------------------------------------

@app.get("/api/databases", tags=["Databases"], summary="List available databases")
async def list_databases_route():
    return {"databases": list_databases(), "current": current_db_name()}


@app.post("/api/databases/select", tags=["Databases"], summary="Switch to a different database")
async def select_database(body: dict[str, Any]):
    name = (body.get("name") or "").strip()
    if not name or not DB_NAME_RE.match(name):
        raise HTTPException(400, "Invalid database name (letters, numbers, hyphens, underscores)")
    switch_db(name)
    return {"current": current_db_name()}


@app.post("/api/databases/create", tags=["Databases"], summary="Create a new empty database")
async def create_database_route(body: dict[str, Any]):
    name = (body.get("name") or "").strip()
    if not name or not DB_NAME_RE.match(name):
        raise HTTPException(400, "Invalid database name (letters, numbers, hyphens, underscores)")
    try:
        create_database(name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"current": current_db_name()}


# ---------------------------------------------------------------------------
# Routes: Custom Columns
# ---------------------------------------------------------------------------


@app.get("/api/custom-columns", tags=["Databases"], summary="List custom column definitions")
async def list_custom_columns(session: Session = Depends(db_session)):
    return services.get_custom_columns(session)


@app.post("/api/custom-columns", tags=["Databases"], status_code=201,
          summary="Add a custom column definition")
async def create_custom_column(body: CustomColumnCreate, session: Session = Depends(db_session)):
    result = services.create_custom_column(
        session, key=body.key, label=body.label, col_type=body.col_type,
        show_in_list=body.show_in_list, sort_order=body.sort_order,
    )
    if result is None:
        raise HTTPException(409, f"Column key '{body.key}' already exists")
    return result


@app.put("/api/custom-columns/{column_id}", tags=["Databases"],
         summary="Update a custom column definition")
async def update_custom_column(column_id: int, body: CustomColumnUpdate,
                               session: Session = Depends(db_session)):
    result = services.update_custom_column(
        session, column_id,
        label=body.label, col_type=body.col_type,
        show_in_list=body.show_in_list, sort_order=body.sort_order,
    )
    if result is None:
        raise HTTPException(404, "Custom column not found")
    return result


@app.delete("/api/custom-columns/{column_id}", tags=["Databases"],
            summary="Remove a custom column definition")
async def delete_custom_column(column_id: int, session: Session = Depends(db_session)):
    if not services.delete_custom_column(session, column_id):
        raise HTTPException(404, "Custom column not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routes: Scoring Prompts
# ---------------------------------------------------------------------------


@app.get("/api/scoring-prompts", tags=["Scoring"],
         summary="List scoring prompt definitions (team, tech, opportunity)")
async def list_scoring_prompts(session: Session = Depends(db_session)):
    return services.get_scoring_prompts(session)


@app.put("/api/scoring-prompts/{key}", tags=["Scoring"],
         summary="Update a scoring prompt's content")
async def update_scoring_prompt(key: str, body: ScoringPromptUpdate,
                                session: Session = Depends(db_session)):
    result = services.update_scoring_prompt(session, key, body.content)
    if result is None:
        raise HTTPException(404, f"Scoring prompt '{key}' not found")
    return result


# ---------------------------------------------------------------------------
# Routes: Stats
# ---------------------------------------------------------------------------


@app.get("/api/stats", response_model=StatsOut,
         tags=["Stats"], summary="Get aggregate statistics and breakdowns")
async def get_stats(session: Session = Depends(db_session)):
    return services.compute_stats(session)


# ---------------------------------------------------------------------------
# Routes: Reset
# ---------------------------------------------------------------------------


@app.delete("/api/reset", tags=["Admin"], summary="Delete all data (initiatives, enrichments, scores, projects)")
async def reset_db(session: Session = Depends(db_session)):
    session.execute(delete(OutreachScore))
    session.execute(delete(Enrichment))
    session.execute(delete(Project))
    session.execute(delete(Initiative))
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def main():
    import uvicorn
    uvicorn.run("scout.app:app", host="127.0.0.1", port=8001, reload=True)


if __name__ == "__main__":
    main()
