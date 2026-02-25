from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, func, select

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
    OutreachScore,
    StatsOut,
)
from scout.scorer import LLMClient, score_initiative

log = logging.getLogger(__name__)

app = FastAPI(title="Scout", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _initiative_to_out(init: Initiative) -> InitiativeOut:
    latest_score = None
    if init.scores:
        latest_score = max(init.scores, key=lambda s: s.scored_at)
    enriched = bool(init.enrichments)
    enriched_at = max((e.fetched_at for e in init.enrichments), default=None) if enriched else None

    evidence: list[str] = []
    gaps: list[str] = []
    if latest_score:
        try:
            evidence = json.loads(latest_score.key_evidence_json or "[]")
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            gaps = json.loads(latest_score.data_gaps_json or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

    return InitiativeOut(
        id=init.id,
        name=init.name,
        uni=init.uni,
        sector=init.sector,
        mode=init.mode,
        description=init.description,
        website=init.website,
        email=init.email,
        relevance=init.relevance,
        sheet_source=init.sheet_source,
        enriched=enriched,
        enriched_at=enriched_at.isoformat() if enriched_at else None,
        verdict=latest_score.verdict if latest_score else None,
        score=latest_score.score if latest_score else None,
        classification=latest_score.classification if latest_score else None,
        reasoning=latest_score.reasoning if latest_score else None,
        contact_who=latest_score.contact_who if latest_score else None,
        contact_channel=latest_score.contact_channel if latest_score else None,
        engagement_hook=latest_score.engagement_hook if latest_score else None,
        key_evidence=evidence,
        data_gaps=gaps,
    )


def _initiative_to_detail(init: Initiative) -> InitiativeDetail:
    base = _initiative_to_out(init)
    try:
        extra_links = json.loads(init.extra_links_json or "{}")
    except (json.JSONDecodeError, TypeError):
        extra_links = {}

    enrichment_outs = [
        EnrichmentOut(
            id=e.id,
            source_type=e.source_type,
            summary=e.summary,
            fetched_at=e.fetched_at.isoformat(),
        )
        for e in init.enrichments
    ]

    return InitiativeDetail(
        **base.model_dump(),
        team_page=init.team_page,
        team_size=init.team_size,
        linkedin=init.linkedin,
        github_org=init.github_org,
        key_repos=init.key_repos,
        sponsors=init.sponsors,
        competitions=init.competitions,
        extra_links=extra_links,
        enrichments=enrichment_outs,
    )


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
async def import_file(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx files are supported")

    tmp_path = Path("/tmp") / f"scout_import_{file.filename}"
    content = await file.read()
    tmp_path.write_bytes(content)

    try:
        session = get_session()
        result = import_xlsx(tmp_path, session)
        session.close()
        return result
    finally:
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
):
    session = get_session()
    initiatives = session.execute(select(Initiative)).scalars().all()
    items = [_initiative_to_out(i) for i in initiatives]
    session.close()

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
        return item.name.lower()

    items.sort(key=sort_key, reverse=(sort_dir == "desc"))

    total = len(items)
    start = (page - 1) * per_page
    items = items[start : start + per_page]

    return InitiativeListResponse(items=items, total=total)


@app.get("/api/initiatives/{initiative_id}", response_model=InitiativeDetail)
async def get_initiative(initiative_id: int):
    session = get_session()
    init = session.execute(select(Initiative).where(Initiative.id == initiative_id)).scalars().first()
    if not init:
        session.close()
        raise HTTPException(404, "Initiative not found")
    detail = _initiative_to_detail(init)
    session.close()
    return detail


# ---------------------------------------------------------------------------
# Routes: Enrichment
# ---------------------------------------------------------------------------


@app.post("/api/enrich/{initiative_id}")
async def enrich_one(initiative_id: int):
    session = get_session()
    init = session.execute(select(Initiative).where(Initiative.id == initiative_id)).scalars().first()
    if not init:
        session.close()
        raise HTTPException(404, "Initiative not found")

    # Delete old enrichments for this initiative
    session.execute(delete(Enrichment).where(Enrichment.initiative_id == initiative_id))
    session.commit()

    added = 0
    for enrich_fn in (enrich_website, enrich_team_page, enrich_github):
        try:
            result = await enrich_fn(init)
            if result:
                session.add(result)
                added += 1
        except Exception as exc:
            log.warning("Enrichment failed for %s: %s", init.name, exc)

    session.commit()
    session.close()
    return {"enrichments_added": added}


@app.post("/api/enrich/batch")
async def enrich_batch(body: dict[str, Any] | None = None):
    initiative_ids = (body or {}).get("initiative_ids")

    async def stream():
        session = get_session()
        query = select(Initiative)
        if initiative_ids:
            query = query.where(Initiative.id.in_(initiative_ids))
        initiatives = session.execute(query).scalars().all()
        total = len(initiatives)
        enriched = 0
        failed = 0

        for idx, init in enumerate(initiatives):
            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'name': init.name})}\n\n"

            # Delete old enrichments
            session.execute(delete(Enrichment).where(Enrichment.initiative_id == init.id))
            session.commit()

            try:
                for enrich_fn in (enrich_website, enrich_team_page, enrich_github):
                    result = await enrich_fn(init)
                    if result:
                        session.add(result)
                session.commit()
                enriched += 1
            except Exception as exc:
                log.warning("Batch enrich failed for %s: %s", init.name, exc)
                failed += 1
                session.rollback()

            await asyncio.sleep(0.1)  # small delay between initiatives

        session.close()
        yield f"data: {json.dumps({'type': 'complete', 'stats': {'enriched': enriched, 'failed': failed}})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Routes: Scoring
# ---------------------------------------------------------------------------


@app.post("/api/score/{initiative_id}")
async def score_one(initiative_id: int):
    session = get_session()
    init = session.execute(select(Initiative).where(Initiative.id == initiative_id)).scalars().first()
    if not init:
        session.close()
        raise HTTPException(404, "Initiative not found")

    enrichments = session.execute(
        select(Enrichment).where(Enrichment.initiative_id == initiative_id)
    ).scalars().all()

    client = LLMClient()
    try:
        outreach_score = await score_initiative(init, list(enrichments), client)
        # Delete old scores for this initiative
        session.execute(delete(OutreachScore).where(OutreachScore.initiative_id == initiative_id))
        session.add(outreach_score)
        session.commit()
    except Exception as exc:
        session.close()
        raise HTTPException(500, f"Scoring failed: {exc}") from exc

    session.close()
    return {
        "verdict": outreach_score.verdict,
        "score": outreach_score.score,
        "classification": outreach_score.classification,
    }


@app.post("/api/score/batch")
async def score_batch(body: dict[str, Any] | None = None):
    initiative_ids = (body or {}).get("initiative_ids")

    async def stream():
        session = get_session()
        query = select(Initiative)
        if initiative_ids:
            query = query.where(Initiative.id.in_(initiative_ids))
        initiatives = session.execute(query).scalars().all()
        total = len(initiatives)
        scored = 0
        failed = 0

        client = LLMClient()

        for idx, init in enumerate(initiatives):
            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'name': init.name})}\n\n"

            enrichments = session.execute(
                select(Enrichment).where(Enrichment.initiative_id == init.id)
            ).scalars().all()

            try:
                outreach_score = await score_initiative(init, list(enrichments), client)
                session.execute(delete(OutreachScore).where(OutreachScore.initiative_id == init.id))
                session.add(outreach_score)
                session.commit()
                scored += 1
            except Exception as exc:
                log.warning("Scoring failed for %s: %s", init.name, exc)
                failed += 1
                session.rollback()

            await asyncio.sleep(0.3)  # rate limit buffer

        session.close()
        yield f"data: {json.dumps({'type': 'complete', 'stats': {'scored': scored, 'failed': failed}})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Routes: Stats
# ---------------------------------------------------------------------------


@app.get("/api/stats", response_model=StatsOut)
async def get_stats():
    session = get_session()
    initiatives = session.execute(select(Initiative)).scalars().all()

    total = len(initiatives)
    enriched = sum(1 for i in initiatives if i.enrichments)
    scored = sum(1 for i in initiatives if i.scores)

    by_verdict: Counter[str] = Counter()
    by_classification: Counter[str] = Counter()
    by_uni: Counter[str] = Counter()

    for init in initiatives:
        by_uni[init.uni or "Unknown"] += 1
        if init.scores:
            latest = max(init.scores, key=lambda s: s.scored_at)
            by_verdict[latest.verdict] += 1
            by_classification[latest.classification] += 1

    session.close()
    return StatsOut(
        total=total,
        enriched=enriched,
        scored=scored,
        by_verdict=dict(by_verdict),
        by_classification=dict(by_classification),
        by_uni=dict(by_uni),
    )


# ---------------------------------------------------------------------------
# Routes: Reset
# ---------------------------------------------------------------------------


@app.delete("/api/reset")
async def reset_db():
    session = get_session()
    session.execute(delete(OutreachScore))
    session.execute(delete(Enrichment))
    session.execute(delete(Initiative))
    session.commit()
    session.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    init_db()


def main():
    import uvicorn
    init_db()
    uvicorn.run("scout.app:app", host="127.0.0.1", port=8001, reload=True)


if __name__ == "__main__":
    main()
