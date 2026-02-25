from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scout.db import get_session, init_db
from scout.enricher import enrich_github, enrich_team_page, enrich_website
from scout.importer import import_xlsx
from scout.models import (
    Enrichment,
    EnrichmentOut,
    ImportResult,
    Initiative,
    InitiativeDetail,
    InitiativeOut,
    InitiativeUpdate,
    OutreachScore,
    Project,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    StatsOut,
)
from scout.scorer import LLMClient, score_initiative, score_project

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Scout", version="0.1.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Dependencies & Helpers
# ---------------------------------------------------------------------------


def db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a DB session and closes it after the request."""
    session = get_session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _json(value: str | None, default: Any = None) -> Any:
    """Safely parse a JSON string, returning default on failure."""
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _get_or_404(session: Session, model, entity_id: int, label: str = "Entity"):
    """Fetch a model instance by PK or raise 404."""
    obj = session.execute(select(model).where(model.id == entity_id)).scalars().first()
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


# Score field extraction shared by initiative and project helpers
_SCORE_FIELDS = (
    "verdict", "score", "classification", "reasoning", "contact_who",
    "contact_channel", "engagement_hook", "grade_team", "grade_team_num",
    "grade_tech", "grade_tech_num", "grade_opportunity", "grade_opportunity_num",
)

_SCORE_RESPONSE_FIELDS = ("verdict", "score", "classification", "grade_team", "grade_tech", "grade_opportunity")


def _latest_score_fields(scores: list[OutreachScore]) -> dict[str, Any]:
    if not scores:
        return {**{f: None for f in _SCORE_FIELDS}, "key_evidence": [], "data_gaps": []}
    latest = max(scores, key=lambda s: s.scored_at)
    result = {f: getattr(latest, f) for f in _SCORE_FIELDS}
    result["key_evidence"] = _json(latest.key_evidence_json, [])
    result["data_gaps"] = _json(latest.data_gaps_json, [])
    return result


def _apply_patch(obj, body, fields: tuple[str, ...]) -> None:
    """Apply non-None fields from a Pydantic update body to an ORM object."""
    for field in fields:
        val = getattr(body, field)
        if val is not None:
            setattr(obj, field, val)


def _initiative_to_out(init: Initiative) -> InitiativeOut:
    enriched = bool(init.enrichments)
    enriched_at = max((e.fetched_at for e in init.enrichments), default=None) if enriched else None
    return InitiativeOut(
        id=init.id, name=init.name, uni=init.uni, sector=init.sector,
        mode=init.mode, description=init.description, website=init.website,
        email=init.email, relevance=init.relevance, sheet_source=init.sheet_source,
        enriched=enriched,
        enriched_at=enriched_at.isoformat() if enriched_at else None,
        **_latest_score_fields(init.scores),
        technology_domains=init.technology_domains,
        categories=init.categories,
        member_count=init.member_count,
        outreach_now_score=init.outreach_now_score,
        venture_upside_score=init.venture_upside_score,
    )


_PROJECT_SCORE_KEYS = (
    "verdict", "score", "classification",
    "grade_team", "grade_team_num", "grade_tech", "grade_tech_num",
    "grade_opportunity", "grade_opportunity_num",
)


def _project_to_out(proj: Project) -> ProjectOut:
    sf = _latest_score_fields(proj.scores)
    return ProjectOut(
        id=proj.id, initiative_id=proj.initiative_id,
        name=proj.name, description=proj.description,
        website=proj.website, github_url=proj.github_url,
        team=proj.team, extra_links=_json(proj.extra_links_json),
        **{k: sf[k] for k in _PROJECT_SCORE_KEYS},
    )


_DETAIL_FIELDS = (
    "team_page", "team_size", "linkedin", "github_org", "key_repos",
    "sponsors", "competitions", "market_domains", "member_examples",
    "member_roles", "github_repo_count", "github_contributors",
    "github_commits_90d", "github_ci_present", "huggingface_model_hits",
    "openalex_hits", "semantic_scholar_hits", "dd_key_roles",
    "dd_references_count", "dd_is_investable", "profile_coverage_score",
    "known_url_count", "linkedin_hits", "researchgate_hits",
)


def _initiative_to_detail(init: Initiative) -> InitiativeDetail:
    base = _initiative_to_out(init)
    enrichment_outs = [
        EnrichmentOut(id=e.id, source_type=e.source_type, summary=e.summary,
                      fetched_at=e.fetched_at.isoformat())
        for e in init.enrichments
    ]
    project_outs = [_project_to_out(p) for p in init.projects]
    extras = {f: getattr(init, f) for f in _DETAIL_FIELDS}
    return InitiativeDetail(
        **base.model_dump(), **extras,
        extra_links=_json(init.extra_links_json),
        enrichments=enrichment_outs,
        projects=project_outs,
    )


async def _run_enrichment(session: Session, init: Initiative) -> int:
    """Run all enrichers; only delete old enrichments if at least one succeeds. Caller must commit."""
    new_enrichments = []
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
    return len(new_enrichments)


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


@app.post("/api/import", response_model=ImportResult)
async def import_file(file: UploadFile = File(...), session: Session = Depends(db_session)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx files are supported")

    content = await file.read()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(content)
            tmp_path = Path(f.name)
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


@app.get("/api/initiatives", response_model=InitiativeListResponse)
async def list_initiatives(
    verdict: str | None = Query(None),
    classification: str | None = Query(None),
    uni: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("score"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(200, ge=1, le=500),
    session: Session = Depends(db_session),
):
    initiatives = session.execute(select(Initiative)).scalars().all()
    items = [_initiative_to_out(i) for i in initiatives]

    # Filter
    if verdict:
        verdicts = {v.strip().lower() for v in verdict.split(",")}
        items = [i for i in items if (i.verdict or "unscored") in verdicts]
    if classification:
        classes = {c.strip().lower() for c in classification.split(",")}
        items = [i for i in items if (i.classification or "") in classes]
    if uni:
        unis = {u.strip().upper() for u in uni.split(",")}
        items = [i for i in items if i.uni.upper() in unis]
    if search:
        q = search.lower()
        items = [i for i in items if q in i.name.lower() or q in i.description.lower() or q in i.sector.lower()]

    # Sort
    def sort_key(item: InitiativeOut):
        if sort_by == "score":
            return item.score if item.score is not None else -1
        elif sort_by == "name":
            return item.name.lower()
        elif sort_by == "uni":
            return item.uni.lower()
        elif sort_by == "verdict":
            order = {"reach_out_now": 0, "reach_out_soon": 1, "monitor": 2, "skip": 3}
            return order.get(item.verdict or "", 4)
        elif sort_by == "grade_team":
            return item.grade_team_num if item.grade_team_num is not None else 99
        elif sort_by == "grade_tech":
            return item.grade_tech_num if item.grade_tech_num is not None else 99
        elif sort_by == "grade_opportunity":
            return item.grade_opportunity_num if item.grade_opportunity_num is not None else 99
        return item.name.lower()

    items.sort(key=sort_key, reverse=(sort_dir == "desc"))
    total = len(items)
    start = (page - 1) * per_page
    items = items[start : start + per_page]
    return InitiativeListResponse(items=items, total=total)


@app.get("/api/initiatives/{initiative_id}", response_model=InitiativeDetail)
async def get_initiative(initiative_id: int, session: Session = Depends(db_session)):
    return _initiative_to_detail(_get_or_404(session, Initiative, initiative_id, "Initiative"))


_INITIATIVE_UPDATE_FIELDS = (
    "name", "uni", "sector", "mode", "description", "website", "email",
    "relevance", "team_page", "team_size", "linkedin", "github_org",
    "key_repos", "sponsors", "competitions",
)


@app.put("/api/initiatives/{initiative_id}", response_model=InitiativeDetail)
async def update_initiative(initiative_id: int, body: InitiativeUpdate, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    _apply_patch(init, body, _INITIATIVE_UPDATE_FIELDS)
    session.commit()
    return _initiative_to_detail(init)


# ---------------------------------------------------------------------------
# Routes: Enrichment (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


def _batch_stream(initiative_ids, process_fn, stat_key, delay=0.1):
    """SSE streaming wrapper for batch enrich/score operations."""
    async def stream():
        session = get_session()
        try:
            query = select(Initiative)
            if initiative_ids:
                query = query.where(Initiative.id.in_(initiative_ids))
            initiatives = session.execute(query).scalars().all()
            total = len(initiatives)
            ok = failed = 0
            init_names = {init.id: init.name for init in initiatives}

            for idx, init in enumerate(initiatives):
                yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'name': init_names[init.id]})}\n\n"
                try:
                    await process_fn(session, init)
                    session.commit()
                    ok += 1
                except Exception as exc:
                    log.warning("Batch %s failed for %s: %s", stat_key, init_names[init.id], exc)
                    failed += 1
                    session.rollback()
                await asyncio.sleep(delay)

            yield f"data: {json.dumps({'type': 'complete', 'stats': {stat_key: ok, 'failed': failed}})}\n\n"
        finally:
            session.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/enrich/batch")
async def enrich_batch(body: dict[str, Any] | None = None):
    return _batch_stream((body or {}).get("initiative_ids"), _run_enrichment, "enriched")


@app.post("/api/enrich/{initiative_id}")
async def enrich_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    added = await _run_enrichment(session, init)
    session.commit()
    return {"enrichments_added": added}


# ---------------------------------------------------------------------------
# Routes: Scoring (batch before parameterized to avoid route shadowing)
# ---------------------------------------------------------------------------


@app.post("/api/score/batch")
async def score_batch(body: dict[str, Any] | None = None):
    client = LLMClient()

    async def _score_one(session, init):
        enrichments = session.execute(
            select(Enrichment).where(Enrichment.initiative_id == init.id)
        ).scalars().all()
        outreach_score = await score_initiative(init, list(enrichments), client)
        session.execute(delete(OutreachScore).where(OutreachScore.initiative_id == init.id))
        session.add(outreach_score)

    return _batch_stream((body or {}).get("initiative_ids"), _score_one, "scored", delay=0.3)


@app.post("/api/score/{initiative_id}")
async def score_one(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == initiative_id)
    ).scalars().all()
    client = LLMClient()
    try:
        outreach_score = await score_initiative(init, list(enrichments), client)
        session.execute(delete(OutreachScore).where(OutreachScore.initiative_id == initiative_id))
        session.add(outreach_score)
        session.commit()
    except Exception as exc:
        raise HTTPException(500, f"Scoring failed: {exc}") from exc
    return {f: getattr(outreach_score, f) for f in _SCORE_RESPONSE_FIELDS}


# ---------------------------------------------------------------------------
# Routes: Projects
# ---------------------------------------------------------------------------


@app.get("/api/initiatives/{initiative_id}/projects", response_model=list[ProjectOut])
async def list_projects(initiative_id: int, session: Session = Depends(db_session)):
    init = _get_or_404(session, Initiative, initiative_id, "Initiative")
    return [_project_to_out(p) for p in init.projects]


@app.post("/api/initiatives/{initiative_id}/projects", response_model=ProjectOut, status_code=201)
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
    return _project_to_out(proj)


@app.put("/api/projects/{project_id}", response_model=ProjectOut)
async def update_project(project_id: int, body: ProjectUpdate, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    _apply_patch(proj, body, ("name", "description", "website", "github_url", "team"))
    if body.extra_links is not None:
        proj.extra_links_json = json.dumps(body.extra_links)
    session.commit()
    return _project_to_out(proj)


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    session.delete(proj)
    session.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/score")
async def score_project_endpoint(project_id: int, session: Session = Depends(db_session)):
    proj = _get_or_404(session, Project, project_id, "Project")
    init = _get_or_404(session, Initiative, proj.initiative_id, "Initiative")
    client = LLMClient()
    try:
        outreach_score = await score_project(proj, init, client)
        session.execute(delete(OutreachScore).where(OutreachScore.project_id == project_id))
        session.add(outreach_score)
        session.commit()
    except Exception as exc:
        raise HTTPException(500, f"Scoring failed: {exc}") from exc
    return {f: getattr(outreach_score, f) for f in _SCORE_RESPONSE_FIELDS}


# ---------------------------------------------------------------------------
# Routes: Stats
# ---------------------------------------------------------------------------


@app.get("/api/stats", response_model=StatsOut)
async def get_stats(session: Session = Depends(db_session)):
    initiatives = session.execute(select(Initiative)).scalars().all()

    by_verdict: Counter[str] = Counter()
    by_classification: Counter[str] = Counter()
    by_uni: Counter[str] = Counter()
    enriched = 0
    scored = 0

    for init in initiatives:
        by_uni[init.uni or "Unknown"] += 1
        if init.enrichments:
            enriched += 1
        if init.scores:
            scored += 1
            latest = max(init.scores, key=lambda s: s.scored_at)
            by_verdict[latest.verdict] += 1
            by_classification[latest.classification] += 1

    return StatsOut(
        total=len(initiatives), enriched=enriched, scored=scored,
        by_verdict=dict(by_verdict), by_classification=dict(by_classification),
        by_uni=dict(by_uni),
    )


# ---------------------------------------------------------------------------
# Routes: Reset
# ---------------------------------------------------------------------------


@app.delete("/api/reset")
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
