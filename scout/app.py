from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from scout import services
from scout.db import (
    backup_database, create_database, current_db_name, delete_backup,
    delete_database, get_entity_type, get_revision,
    get_session, init_db, list_backups, list_databases, restore_database,
    session_generator, switch_db, validate_db_name,
)
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
from scout.scorer import LLMCallError, LLMClient

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from scout.utils import load_llm_env
    load_llm_env()
    init_db()
    yield


app = FastAPI(
    title="Scout",
    version="1.0.0",
    description=(
        "Outreach intelligence API. "
        "Discover, enrich, and score entities for outreach. "
        "All endpoints return JSON. No authentication required."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Initiatives", "description": "Browse, search, and update initiatives."},
        {"name": "Enrichment", "description": "Fetch live web and GitHub data."},
        {"name": "Scoring", "description": "LLM-powered outreach scoring. Requires LLM API key (auto-loaded from .mcp.json)."},
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
    yield from session_generator()


def _get_or_404(session: Session, model, entity_id: int):
    obj = services.get_entity(session, model, entity_id)
    if not obj:
        raise HTTPException(404, f"{model.__name__} not found")
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


@app.get("/api/export", tags=["Import"], summary="Export initiatives to XLSX")
async def export_file(
    verdict: str | None = Query(None, description="Comma-separated verdict filter"),
    uni: str | None = Query(None, description="Comma-separated uni filter"),
    include_enrichments: bool = Query(True, description="Include enrichment summary column"),
    include_scores: bool = Query(True, description="Include score columns"),
    include_extras: bool = Query(False, description="Include extra profile fields"),
    session: Session = Depends(db_session),
):
    from scout.exporter import export_xlsx
    buf = export_xlsx(
        session, verdict=verdict, uni=uni,
        include_enrichments=include_enrichments,
        include_scores=include_scores, include_extras=include_extras,
    )
    db_name = current_db_name()
    filename = f"scout-{db_name}-{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/scrape/tum-professors", tags=["Import"],
          summary="Scrape TUM professor directory and import")
async def scrape_tum_professors_route(body: dict[str, Any] | None = None):
    from scout.scrapers import scrape_tum_professors as _scrape
    from scout.services import import_scraped_entities
    params = body or {}
    professors = await _scrape()
    school = params.get("school")
    if school:
        professors = [p for p in professors if p.get("faculty", "").upper() == school.upper()]
    limit = min(int(params.get("limit", 50)), 1000)
    professors = professors[:limit]

    with next(session_generator()) as session:
        result = import_scraped_entities(session, professors)
        session.commit()
    return {**result, "total_found": len(professors)}


@app.get("/api/entity-type", tags=["Stats"], summary="Get entity type for current database")
async def get_entity_type_route():
    return {"entity_type": get_entity_type()}


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
    classification: str | None = Query(None, description="Comma-separated classification filter (values depend on entity type)"),
    uni: str | None = Query(None, description="Comma-separated: TUM, LMU, HM"),
    faculty: str | None = Query(None, description="Comma-separated faculty/department filter"),
    search: str | None = Query(None, description="Free-text search across name, description, and sector"),
    sort_by: str = Query("score", description="Sort field: score, name, uni, verdict, grade_team, grade_tech, grade_opportunity"),
    sort_dir: str = Query("desc", description="asc or desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(200, ge=1, le=500),
    fields: str | None = Query(None, description="Comma-separated field names for compact mode (e.g. 'id,name,verdict,score')"),
    session: Session = Depends(db_session),
):
    fields_set = {f.strip() for f in fields.split(",") if f.strip()} if fields else None
    items, total = services.query_initiatives(
        session, verdict=verdict, classification=classification,
        uni=uni, faculty=faculty, search=search, sort_by=sort_by, sort_dir=sort_dir,
        page=page, per_page=per_page, fields=fields_set,
    )
    if fields_set:
        return JSONResponse({"items": items, "total": total})
    return {"items": items, "total": total}


@app.get("/api/initiatives/{initiative_id}", response_model=InitiativeDetail,
         tags=["Initiatives"], summary="Get full initiative detail with enrichments, projects, and scores")
async def get_initiative(initiative_id: int, session: Session = Depends(db_session)):
    return services.initiative_detail(_get_or_404(session, Initiative, initiative_id))


@app.put("/api/initiatives/{initiative_id}", response_model=InitiativeDetail,
         tags=["Initiatives"], summary="Update initiative fields (partial update, null fields ignored)")
async def update_initiative(initiative_id: int, body: InitiativeUpdate, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id)
    services.apply_updates(init, body.model_dump(), services.UPDATABLE_FIELDS)
    if body.custom_fields is not None:
        existing = services.json_parse(init.custom_fields_json, {})
        existing.update(body.custom_fields)
        existing = {k: v for k, v in existing.items() if v is not None}
        init.custom_fields_json = json.dumps(existing)
    session.flush()  # triggers after_update → FTS sync automatically
    session.commit()
    return services.initiative_detail(init)


# ---------------------------------------------------------------------------
# Routes: Enrichment (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


def _batch_stream(initiative_ids, process_fn, stat_key, *,
                   exclude_scored=False, delay=0.1, context_manager=None):
    """SSE streaming wrapper for batch enrich/score operations.

    Args:
        context_manager: Optional async context manager (e.g. open_crawler())
            whose result is passed as the third argument to process_fn.
    """
    async def stream():
        session = None
        try:
            session = get_session()
            query = select(Initiative.id, Initiative.name)
            if initiative_ids:
                query = query.where(Initiative.id.in_(initiative_ids))
            if exclude_scored:
                scored_ids = (
                    select(func.distinct(OutreachScore.initiative_id))
                    .where(OutreachScore.project_id.is_(None))
                )
                query = query.where(Initiative.id.notin_(scored_ids))
            rows = session.execute(query).all()
            total = len(rows)
            ok = failed = 0

            async def _run_loop(ctx=None):
                nonlocal ok, failed
                for idx, (init_id, init_name) in enumerate(rows):
                    yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'name': init_name})}\n\n"
                    try:
                        init = session.execute(select(Initiative).where(Initiative.id == init_id)).scalars().first()
                        if init is None:
                            failed += 1
                            continue
                        if ctx is not None:
                            await process_fn(session, init, ctx)
                        else:
                            await process_fn(session, init)
                        session.commit()
                        ok += 1
                    except Exception as exc:
                        log.warning("Batch %s failed for %s: %s", stat_key, init_name, exc)
                        failed += 1
                        session.rollback()
                    await asyncio.sleep(delay)

            if context_manager is not None:
                async with context_manager as ctx:
                    async for msg in _run_loop(ctx):
                        yield msg
            else:
                async for msg in _run_loop():
                    yield msg

            yield f"data: {json.dumps({'type': 'complete', 'stats': {stat_key: ok, 'failed': failed}})}\n\n"
        except Exception:
            log.exception("Batch %s stream error", stat_key)
            if session is not None:
                session.rollback()
            raise
        finally:
            if session is not None:
                session.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/enrich/batch", tags=["Enrichment"], summary="Enrich multiple initiatives (SSE progress stream)")
async def enrich_batch(body: dict[str, Any] | None = None):
    from scout.enricher import open_crawler

    async def _enrich(session, init, crawler):
        await services.run_enrichment(session, init, crawler=crawler)

    return _batch_stream(
        (body or {}).get("initiative_ids"), _enrich, "enriched",
        context_manager=open_crawler(),
    )


@app.post("/api/enrich/{initiative_id}", tags=["Enrichment"], summary="Enrich a single initiative from web and GitHub")
async def enrich_one(initiative_id: int, session: Session = Depends(db_session)):
    from scout.enricher import open_crawler
    init = _get_or_404(session, Initiative, initiative_id)
    async with open_crawler() as crawler:
        added = await services.run_enrichment(session, init, crawler=crawler)
    session.commit()
    return {"enrichments_added": len(added)}


@app.post("/api/discover/{initiative_id}", tags=["Enrichment"], summary="Discover new URLs via DuckDuckGo search")
async def discover_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id)
    try:
        result = await services.run_discovery(session, init)
        session.commit()
    except ImportError:
        raise HTTPException(501, "ddgs not installed — pip install 'scout[crawl]'")
    return result


# ---------------------------------------------------------------------------
# Routes: Scoring (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


@app.post("/api/score/batch", tags=["Scoring"], summary="Score multiple initiatives via LLM (SSE progress stream)")
async def score_batch(body: dict[str, Any] | None = None):
    try:
        client = LLMClient()
    except LLMCallError as exc:
        raise HTTPException(422, str(exc)) from exc
    params = body or {}

    async def _score_one(session, init):
        await services.run_scoring(session, init, client)

    return _batch_stream(
        params.get("initiative_ids"), _score_one, "scored",
        exclude_scored=params.get("only_unscored", False), delay=0.3,
    )


@app.post("/api/score/{initiative_id}", tags=["Scoring"], summary="Score a single initiative via LLM")
async def score_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id)
    try:
        outreach = await services.run_scoring(session, init)
        session.commit()
    except LLMCallError as exc:
        code = 503 if exc.retryable else 422
        raise HTTPException(code, str(exc)) from exc
    return services.score_response_dict(outreach)


# ---------------------------------------------------------------------------
# Routes: Projects
# ---------------------------------------------------------------------------


@app.get("/api/initiatives/{initiative_id}/projects", response_model=list[ProjectOut],
         tags=["Projects"], summary="List projects for an initiative")
async def list_projects(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id)
    return [services.project_summary(p) for p in init.projects]


@app.post("/api/initiatives/{initiative_id}/projects", response_model=ProjectOut, status_code=201,
          tags=["Projects"], summary="Create a new project under an initiative")
async def create_project(initiative_id: int, body: ProjectCreate, session: Session = Depends(db_session)):
    _get_or_404(session, Initiative, initiative_id)
    proj = services.create_project(
        session, initiative_id,
        name=body.name, description=body.description,
        website=body.website, github_url=body.github_url, team=body.team,
        extra_links=body.extra_links,
    )
    session.commit()
    return services.project_summary(proj)


@app.put("/api/projects/{project_id}", response_model=ProjectOut,
         tags=["Projects"], summary="Update project fields (partial update)")
async def update_project(project_id: int, body: ProjectUpdate, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id)
    services.apply_updates(proj, body.model_dump(), ("name", "description", "website", "github_url", "team"))
    if body.extra_links is not None:
        proj.extra_links_json = json.dumps(body.extra_links)
    session.commit()
    return services.project_summary(proj)


@app.delete("/api/projects/{project_id}", tags=["Projects"], summary="Delete a project and its scores")
async def delete_project(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id)
    session.delete(proj)
    session.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/score", tags=["Scoring", "Projects"],
          summary="Score a project via LLM in context of its parent initiative")
async def score_project_endpoint(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id)
    init = _get_or_404(session, Initiative, proj.initiative_id)
    try:
        outreach = await services.run_project_scoring(
            session, proj, init, entity_type=get_entity_type(),
        )
        session.commit()
    except LLMCallError as exc:
        code = 503 if exc.retryable else 422
        raise HTTPException(code, str(exc)) from exc
    return services.score_response_dict(outreach)


# ---------------------------------------------------------------------------
# Routes: Revision polling (live UI updates)
# ---------------------------------------------------------------------------

@app.get("/api/revision", tags=["Admin"], summary="Data revision counter for change detection")
async def get_revision_endpoint():
    return {"revision": get_revision()}


# ---------------------------------------------------------------------------
# Routes: Databases
# ---------------------------------------------------------------------------

@app.get("/api/databases", tags=["Databases"], summary="List available databases")
async def list_databases_route():
    return {"databases": list_databases(), "current": current_db_name()}


@app.post("/api/databases/select", tags=["Databases"], summary="Switch to a different database")
async def select_database(body: dict[str, Any]):
    try:
        name = validate_db_name(body.get("name") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    switch_db(name)
    return {"current": current_db_name()}


@app.post("/api/databases/create", tags=["Databases"], summary="Create a new empty database")
async def create_database_route(body: dict[str, Any]):
    try:
        name = validate_db_name(body.get("name") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    entity_type = body.get("entity_type", "initiative")
    try:
        create_database(name, entity_type=entity_type)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"current": current_db_name(), "entity_type": entity_type}


@app.post("/api/databases/delete", tags=["Databases"], summary="Delete a database")
async def delete_database_route(body: dict[str, Any]):
    try:
        name = validate_db_name(body.get("name") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        delete_database(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "deleted": name, "current": current_db_name()}


@app.post("/api/databases/backup", tags=["Databases"], summary="Backup a database")
async def backup_database_route(body: dict[str, Any]):
    try:
        name = validate_db_name(body.get("name") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        backup_name = backup_database(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "backup": backup_name}


@app.get("/api/databases/backups", tags=["Databases"], summary="List all backups")
async def list_backups_route():
    return {"backups": list_backups()}


@app.post("/api/databases/restore", tags=["Databases"], summary="Restore a database from backup")
async def restore_database_route(body: dict[str, Any]):
    backup_name = (body.get("backup_name") or "").strip()
    if not backup_name:
        raise HTTPException(400, "backup_name is required")
    try:
        restored = restore_database(backup_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "restored": restored}


@app.delete("/api/databases/backups/{backup_name}", tags=["Databases"], summary="Delete a backup")
async def delete_backup_route(backup_name: str):
    try:
        delete_backup(backup_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "deleted": backup_name}


# ---------------------------------------------------------------------------
# Routes: Custom Columns
# ---------------------------------------------------------------------------


@app.get("/api/custom-columns", tags=["Databases"], summary="List custom column definitions")
async def list_custom_columns(session: Session = Depends(db_session)):
    return services.get_custom_columns(session, database=current_db_name())


@app.post("/api/custom-columns", tags=["Databases"], status_code=201,
          summary="Add a custom column definition")
async def create_custom_column(body: CustomColumnCreate, session: Session = Depends(db_session)):
    result = services.create_custom_column(
        session, key=body.key, label=body.label, col_type=body.col_type,
        show_in_list=body.show_in_list, sort_order=body.sort_order,
        database=current_db_name(),
    )
    if result is None:
        raise HTTPException(409, f"Column key '{body.key}' already exists")
    session.commit()
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
    session.commit()
    return result


@app.delete("/api/custom-columns/{column_id}", tags=["Databases"],
            summary="Remove a custom column definition")
async def delete_custom_column(column_id: int, session: Session = Depends(db_session)):
    if not services.delete_custom_column(session, column_id):
        raise HTTPException(404, "Custom column not found")
    session.commit()
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
    session.commit()
    return result


# ---------------------------------------------------------------------------
# Routes: Stats
# ---------------------------------------------------------------------------


@app.get("/api/faculties", tags=["Stats"],
         summary="List all distinct faculty values for filter dropdowns")
async def get_faculties(session: Session = Depends(db_session)):
    rows = session.execute(
        select(func.distinct(Initiative.faculty))
        .where(Initiative.faculty != "")
        .where(Initiative.faculty.isnot(None))
    ).scalars().all()
    return sorted(rows)


@app.get("/api/stats", response_model=StatsOut,
         tags=["Stats"], summary="Get aggregate statistics and breakdowns")
async def get_stats(session: Session = Depends(db_session)):
    return services.compute_stats(session)


@app.get("/api/aggregations", tags=["Stats"],
         summary="Analytical aggregations: score distributions, top-N per verdict, grade breakdowns")
async def get_aggregations(session: Session = Depends(db_session)):
    return services.compute_aggregations(session)


# ---------------------------------------------------------------------------
# Routes: Embeddings & Similarity
# ---------------------------------------------------------------------------


@app.post("/api/embed", tags=["Enrichment"],
          summary="Build/rebuild dense embeddings for all initiatives")
async def embed_all(session: Session = Depends(db_session)):
    from scout.embedder import embed_all as _embed_all
    count = _embed_all(session)
    return {"ok": True, "embedded": count}


@app.get("/api/similar/{initiative_id}", tags=["Initiatives"],
         summary="Find initiatives semantically similar to a given one")
async def find_similar_endpoint(
    initiative_id: int,
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(db_session),
):
    _get_or_404(session, Initiative, initiative_id)
    from scout.embedder import find_similar
    results = find_similar(initiative_id=initiative_id, top_k=limit)
    if not results:
        return {"results": [], "hint": "No embeddings found. Run POST /api/embed first."}
    # Enrich results with names
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


@app.get("/api/search/semantic", tags=["Initiatives"],
         summary="Semantic text search across initiatives using dense embeddings")
async def semantic_search(
    q: str = Query(..., description="Search query text"),
    limit: int = Query(10, ge=1, le=100),
    uni: str | None = Query(None, description="Pre-filter by uni"),
    verdict: str | None = Query(None, description="Pre-filter by verdict"),
    session: Session = Depends(db_session),
):
    from scout.embedder import find_similar
    from scout.services import build_similarity_id_mask

    id_mask = build_similarity_id_mask(session, uni=uni, verdict=verdict)
    if id_mask is not None and not id_mask:
        return {"results": []}

    results = find_similar(query_text=q, top_k=limit, id_mask=id_mask)
    if not results:
        return {"results": [], "hint": "No embeddings found. Run POST /api/embed first."}

    ids = [r[0] for r in results]
    inits = session.execute(
        select(Initiative.id, Initiative.name, Initiative.uni, Initiative.description)
        .where(Initiative.id.in_(ids))
    ).all()
    info_map = {r.id: r for r in inits}
    return {"results": [
        {"id": rid, "name": getattr(info_map.get(rid), "name", "?"),
         "uni": getattr(info_map.get(rid), "uni", "?"),
         "description": (getattr(info_map.get(rid), "description", "") or "")[:200],
         "similarity": score}
        for rid, score in results
    ]}


# ---------------------------------------------------------------------------
# Routes: Reset
# ---------------------------------------------------------------------------


@app.delete("/api/reset", tags=["Admin"], summary="Delete all data (initiatives, enrichments, scores, projects)")
async def reset_db(session: Session = Depends(db_session)):
    session.execute(delete(OutreachScore))
    session.execute(delete(Enrichment))
    session.execute(delete(Project))
    session.execute(delete(Initiative))
    try:
        session.execute(text("INSERT INTO initiative_fts(initiative_fts) VALUES('rebuild')"))
    except Exception:
        log.debug("FTS deleteall skipped (table may not exist)")
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def main():
    import sys
    import argparse
    import socket

    parser = argparse.ArgumentParser(prog="scout")
    parser.add_argument("-V", "--version", action="store_true", help="print version")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8001, help="bind port (default: 8001)")
    args = parser.parse_args()

    if args.version:
        from scout import __version__
        print(f"scout {__version__}")
        return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((args.host, args.port)) == 0:
            print(f"Port {args.port} is already in use. Try: scout --port <number>")
            sys.exit(1)

    import uvicorn
    uvicorn.run("scout.app:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    main()
