from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import func, select

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from scout import services
from scout.db import (
    backup_database, create_database, current_db_name, delete_backup,
    delete_database, get_entity_type, get_session, init_db,
    list_backups, list_databases, restore_database, session_scope,
    switch_db, validate_db_name,
)
from scout.enricher import open_crawler
from scout.models import Enrichment, Initiative, OutreachScore, Project
from scout.scorer import (
    GRADE_MAP, VALID_GRADES, Grade, _BUILTIN_ENTITY_TYPES,
    LLMClient, get_entity_config, valid_classifications,
)
from scout.utils import json_parse, parse_comma_set

log = logging.getLogger(__name__)


def _entity_cfg() -> dict:
    """Return entity config for the current database's entity type."""
    return get_entity_config(get_entity_type())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_instructions(entity_type: str) -> str:
    """Compact instructions — details live in scout://overview resource."""
    cfg = get_entity_config(entity_type)
    return (
        f"Scout: sourcing, enrichment & scoring engine for {cfg['context']}. "
        "Read scout://overview for workflows, grading scale, and classifications. "
        "QUICK: overview() → overview(queue_limit=10) → enrich(action='process'). "
        "Use enrich(action='submit') to store data you find via web search. "
        "All errors return {error, error_code, retryable, fix}."
    )


def _error(message: str, error_code: str, *, retryable: bool = False,
           fix: str | None = None, fix_tool: str | None = None,
           fix_args: dict | None = None) -> dict:
    """Build an error response with optional recovery guidance."""
    result: dict = {"error": message, "error_code": error_code, "retryable": retryable}
    if fix:
        result["fix"] = fix
    if fix_tool:
        result["fix_action"] = {"tool": fix_tool, "args": fix_args or {}}
    return result


def _llm_error(exc: Exception) -> dict:
    """Convert an LLM-related exception into a standard error dict."""
    retryable = getattr(exc, "retryable", False)
    return _error(f"Scoring failed: {exc}", "LLM_ERROR", retryable=retryable)


def _suggest(data: dict, *actions: dict) -> dict:
    """Add next-action suggestions to a response dict."""
    if actions:
        data["next"] = list(actions)
    return data


def _next(tool: str, reason: str, **args) -> dict:
    """Build a next-action suggestion."""
    return {"tool": tool, "args": args, "reason": reason}


def _get_or_error(session, model, entity_id):
    obj = services.get_entity(session, model, entity_id)
    if not obj:
        return None, _error(f"{model.__name__} {entity_id} not found", "NOT_FOUND")
    return obj, None


def _seed_custom_prompts(entity_type: str, cfg: dict) -> None:
    """Seed generic scoring prompts for a custom entity type."""
    from scout.db import session_scope as _ss
    from scout.models import ScoringPrompt
    dims = cfg.get("dimensions", ["team", "tech", "opportunity"])
    ctx = cfg.get("context", entity_type)
    label = cfg.get("label", entity_type)
    with _ss() as session:
        for dim in dims:
            existing = session.execute(
                select(ScoringPrompt).where(ScoringPrompt.key == dim)
            ).scalar_one_or_none()
            if existing:
                continue
            is_last = dim == dims[-1]
            extra_json = ""
            if is_last:
                extra_json = (
                    ',\n  "classification": "<your classification>",\n'
                    '  "contact_who": "<contact recommendation>",\n'
                    '  "contact_channel": "<email|linkedin|event|website_form>",\n'
                    '  "engagement_hook": "<specific opener>"'
                )
            prompt = (
                f"You are evaluating the {dim.upper()} dimension of a {label} "
                f"in the context of {ctx}.\n\n"
                f"Assess quality and strength based on all available evidence.\n\n"
                f"Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D\n"
                f"(A+ = exceptional, D = no evidence)\n\n"
                f"Respond with ONLY valid JSON:\n"
                "{\n"
                '  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",\n'
                '  "reasoning": "<2-3 sentences explaining the grade>"'
                f'{extra_json}\n'
                "}\n"
            )
            session.add(ScoringPrompt(
                key=dim,
                label=dim.replace("_", " ").title(),
                content=prompt,
            ))
        session.commit()


def _check_api_key() -> dict | None:
    """Return an error dict if the LLM API key is not configured, else None."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return _error(
            "ANTHROPIC_API_KEY not set.",
            "CONFIG_ERROR",
            fix="Use get_scoring_dossier() + submit_score() for API-key-free scoring.",
        )
    if provider in ("openai", "openai_compatible") and not os.environ.get("OPENAI_API_KEY"):
        return _error(
            "OPENAI_API_KEY not set.",
            "CONFIG_ERROR",
            fix="Use get_scoring_dossier() + submit_score() for API-key-free scoring.",
        )
    if provider == "gemini" and not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        return _error(
            "GOOGLE_API_KEY (or GEMINI_API_KEY) not set.",
            "CONFIG_ERROR",
            fix="Use get_scoring_dossier() + submit_score() for API-key-free scoring.",
        )
    return None


def _parse_ids(raw: str | None) -> list[int] | None:
    """Parse a comma-separated string of IDs into a list of ints, or None."""
    if not raw:
        return None
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


VALID_CHANNELS = {"email", "linkedin", "event", "website_form"}


# ---------------------------------------------------------------------------
# Response optimizers — save tokens, keep LLM oriented
# ---------------------------------------------------------------------------

# Fields to keep even when their value is falsy (0, False, empty string)
_KEEP_KEYS = frozenset({
    "id", "name", "enriched", "ok", "action", "error", "error_code", "retryable",
})
_STRIP_VALUES = (None, "")


def _trim(data, *, max_str: int = 500):
    """Strip None/empty-string values and truncate long strings to save tokens.

    Preserves 0, False, [], {} — only removes None and "".
    Fields in _KEEP_KEYS are preserved regardless of value.
    """
    if isinstance(data, dict):
        return {
            k: _trim(v, max_str=max_str)
            for k, v in data.items()
            if k in _KEEP_KEYS or v not in _STRIP_VALUES
        }
    if isinstance(data, list):
        return [_trim(item, max_str=max_str) for item in data]
    if isinstance(data, str) and len(data) > max_str:
        return data[:max_str] + "…"
    return data


def _db_pulse(session) -> dict:
    """Compact database state snapshot (3 cheap COUNT queries).

    Injected into mutating-tool responses so the LLM always knows
    where it stands without calling get_overview().
    """
    total = session.execute(select(func.count(Initiative.id))).scalar() or 0
    enriched = session.execute(
        select(func.count(func.distinct(Enrichment.initiative_id)))
    ).scalar() or 0
    scored = session.execute(
        select(func.count(func.distinct(OutreachScore.initiative_id)))
        .where(OutreachScore.project_id.is_(None))
    ).scalar() or 0
    return {"total": total, "enriched": enriched, "scored": scored,
            "queue_est": total - scored}


# Annotation presets for tool safety hints
_READ = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)


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
# Shared batch operation callables
# ---------------------------------------------------------------------------


async def _do_enrich(s, init, *, crawler=None):
    """Internal: enrich a single initiative within a batch."""
    new = await services.run_enrichment(s, init, crawler=crawler)
    if new:
        return {"sources": len(new)}
    return {"ok": False, "sources": 0,
            "warning": "No data fetched — add website/github URLs or run enrich_entity(id, discover=True)"}


async def _do_score(s, init, *, client=None, entity_type="initiative"):
    """Internal: score a single initiative within a batch."""
    outreach = await services.run_scoring(s, init, client, entity_type=entity_type)
    return {"verdict": outreach.verdict, "score": outreach.score,
            "classification": outreach.classification}


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
# Resource
# ---------------------------------------------------------------------------


@mcp.resource("scout://overview")
def scout_overview() -> str:
    """Full workflow guide, data model, grading scale, and classifications."""
    cfg = _entity_cfg()
    lp = cfg["label_plural"]
    et = get_entity_type()
    cls_list = sorted(valid_classifications(et))
    ecfg = get_entity_config(et)
    dims = ecfg.get("dimensions", ["team", "tech", "opportunity"])
    return json.dumps({
        "system": f"Scout — Sourcing, Enrichment & Scoring Engine for {cfg['context'].title()}",
        "entity_type": et,
        "description": (
            f"Scout discovers, enriches, and scores {lp}. "
            "Contains profiles with enrichment data and LLM-powered scoring verdicts. "
            "Use submit_enrichment() to store data you find via your own web search."
        ),
        "data_model": {
            cfg["label"]: f"A {cfg['label']} record with profile, enrichments, scores, and projects.",
            "enrichment": (
                "Data attached to an entity — from automated scrapers or submitted by the LLM "
                "via submit_enrichment(). Source type is freeform (website, github, linkedin, "
                "patent_data, news, etc.)."
            ),
            "project": f"A sub-project within a {cfg['label']}. Can be scored independently.",
            "outreach_score": "LLM-generated verdict, score (1-5), classification, reasoning.",
        },
        "grading_scale": {
            "grades": {g: GRADE_MAP[g] for g in sorted(VALID_GRADES, key=lambda g: GRADE_MAP[g])},
            "dimensions": dims,
            "verdict_thresholds": {
                "reach_out_now": "avg_grade <= 1.7",
                "reach_out_soon": "avg_grade <= 2.7",
                "monitor": "avg_grade <= 3.3",
                "skip": "avg_grade > 3.3",
            },
            "score_formula": "round(5.0 - avg_grade_num, 1)",
        },
        "classifications": cls_list,
        "workflow": {
            "autonomous": [
                "1. overview() — database state + analytics.",
                "2. overview(queue_limit=10) — get prioritized work queue.",
                "3. enrich(action='process', limit=20) — enriches AND scores in one call.",
                "4. Repeat until remaining_in_queue=0.",
                f"5. entity(action='list', verdict='reach_out_now') — review top {lp}.",
            ],
            "single_item": [
                f"1. entity(action='create', name=..., uni=...) — add new {cfg['label']}.",
                "2. enrich(action='run', entity_id=id, discover=True) — find URLs + fetch data.",
                "3. score(action='run', entity_id=id) — LLM scoring (3 parallel dims).",
                "4. entity(action='get', entity_id=id) — inspect full details.",
            ],
            "llm_enrichment": [
                "1. Search the web for information about the entity.",
                "2. enrich(action='submit', entity_id=id, source_type='...', content='...').",
                "3. Repeat for different sources (LinkedIn, news, patents, etc.).",
                "4. score(action='run', entity_id=id) — score with enriched data.",
            ],
            "llm_free_scoring": [
                "1. score(action='dossier', entity_id=id) — get prompts + dossiers.",
                "2. Evaluate each dimension per its prompt.",
                "3. score(action='submit', entity_id=id, grade_team=..., ...) — save.",
            ],
        },
        "tools": {
            "entity": "list, get, create, bulk_create, update, delete, export, similar",
            "enrich": "run (scrape), submit (your research), process (autonomous pipeline)",
            "score": "run (LLM), dossier (build prompts), submit (manual grades)",
            "overview": "Database stats + work queue",
            "script": "save, list, read, delete, run — persist and run Python code",
            "prompt": "save, list, read, delete, scoring_list, scoring_update",
            "configure": "db_*, col_*, llm_show, llm_set, embed, scrape",
            "credential": "save, list, delete — encrypted API key storage",
            "project": "create, update, delete, score — sub-projects",
        },
        "scripts": {
            "description": (
                "Save and run Python scripts that persist across sessions. "
                "Scripts offload reasoning to classical code: API connectors, "
                "custom enrichers, data transforms, reports."
            ),
            "workflow": [
                "1. script(action='save', name='my_script', code='...') — save a script.",
                "2. script(action='run', name='my_script', entity_id=42) — run it.",
                "3. script(action='list') — see all saved scripts.",
            ],
            "ctx_api": {
                "ctx.entity(id)": "Get entity as dict",
                "ctx.entities(verdict=..., search=..., limit=...)": "Query entities",
                "ctx.update(id, field=val)": "Update entity fields",
                "ctx.create(name=..., ...)": "Create new entity",
                "ctx.enrich(id, source_type=..., raw_text=...)": "Add enrichment",
                "ctx.secret('name')": "Read encrypted credential",
                "ctx.http": "httpx.Client for HTTP requests",
                "ctx.env('KEY')": "Read environment variable",
                "ctx.log('msg')": "Add to execution log",
                "ctx.result(data)": "Set return value",
            },
            "script_types": "enricher, connector, transform, report, custom",
            "allowed_imports": "json, re, math, datetime, collections, itertools, functools, urllib.parse, hashlib, base64, csv, io, statistics, textwrap, httpx",
        },
        "performance": {
            "enrichment": f"2-10s per {cfg['label']} (web scraping).",
            "discovery": f"12+s per {cfg['label']} (DuckDuckGo rate limit).",
            "scoring": f"5-15s per {cfg['label']} (3 parallel LLM calls).",
            "listing": "Instant (SQL + FTS5).",
            "similarity": "Instant (numpy dot product).",
        },
        "error_handling": {
            "format": "All errors: {error, error_code, retryable, fix, fix_action}.",
            "codes": {
                "NOT_FOUND": "Entity does not exist.",
                "LLM_ERROR": "LLM API call failed. Check retryable flag.",
                "ALREADY_EXISTS": "Duplicate entity.",
                "VALIDATION_ERROR": "Invalid input.",
                "CONFIG_ERROR": "Missing API key or configuration.",
                "DEPENDENCY_MISSING": "Optional dependency not installed.",
            },
        },
        "verdicts": {
            "reach_out_now": "Strong signals, worth a cold email this week.",
            "reach_out_soon": "Promising but needs a trigger event.",
            "monitor": "Interesting but insufficient evidence.",
            "skip": "Out of scope or dormant.",
        },
        "enrichable_fields": {
            k: {"label": v["label"], "type": v["type"]}
            for k, v in ecfg.get("enrichable_fields", {}).items()
        },
    }, indent=2)


# ---------------------------------------------------------------------------
# Tool 1: entity() — list / get / create / update / delete / export / similar
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
def entity(
    action: str = "list",
    entity_id: int | None = None,
    # List / filter
    verdict: str | None = None, classification: str | None = None,
    uni: str | None = None, faculty: str | None = None,
    search: str | None = None,
    sort_by: str = "score", sort_dir: str = "desc", limit: int = 20,
    fields: str | None = None, compact: bool = True,
    # Get
    sources: str = "", include_gaps: bool = False,
    # Create / update
    name: str | None = None, updates: dict | None = None,
    confirm: bool = False, items: list | None = None,
    # Export
    include_enrichments: bool = True, include_scores: bool = True,
    include_extras: bool = False,
    # Similar
    query: str | None = None,
) -> dict | list:
    """Unified entity operations: list, get, create, update, delete, export, similar.

    ACTIONS:
      list        — Filter/browse entities. Default compact=True (id,name,uni,verdict,score).
      get         — Full detail for one entity (entity_id required).
      create      — New entity (name required). Pass updates={field: value}.
      bulk_create — Batch import (items=list of dicts with name+uni).
      update      — Modify entity (entity_id + updates required).
      delete      — Remove entity (entity_id + confirm=True).
      export      — Export to XLSX file. Optional verdict/uni filters.
      similar     — Semantic similarity search (query or entity_id required).

    Args:
        action: list | get | create | bulk_create | update | delete | export | similar.
        entity_id: Entity ID (for get/update/delete/similar).
        verdict: Filter by verdict (comma-separated).
        classification: Filter by classification (comma-separated).
        uni: Filter by university (comma-separated).
        faculty: Filter by faculty (comma-separated).
        search: Free-text FTS5 search.
        sort_by: score, name, uni, faculty, verdict.
        sort_dir: asc or desc.
        limit: Max results (default 20).
        fields: Comma-separated field names for list output.
        compact: Minimal output (default True).
        sources: Enrichment source types to include in get (e.g. "github,website").
        include_gaps: Include _missing_fields list in get output.
        name: Entity name (for create).
        updates: Dict of field->value (for create/update).
        confirm: Must be True for delete.
        items: List of dicts for bulk_create.
        include_enrichments: Include enrichments in export.
        include_scores: Include scores in export.
        include_extras: Include extra fields in export.
        query: Text query for similar search.
    """
    action = (action or "list").strip().lower()

    # --- LIST ---
    if action == "list":
        if fields:
            fields_set = {f.strip() for f in fields.split(",") if f.strip()}
        elif compact:
            fields_set = {"id", "name", "uni", "verdict", "score", "classification", "enriched"}
        else:
            fields_set = None
        with session_scope() as session:
            items_out, _ = services.query_entities(
                session, verdict=verdict, classification=classification,
                uni=uni, faculty=faculty, search=search, sort_by=sort_by, sort_dir=sort_dir,
                page=1, per_page=max(1, min(limit, 500)), fields=fields_set,
            )
            return _trim(items_out, max_str=200)

    # --- GET ---
    if action == "get":
        if entity_id is None:
            return _error("entity_id required for get", "VALIDATION_ERROR")
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            if compact:
                data = services.entity_detail_compact(init)
            else:
                data = services.entity_detail(init, sources=parse_comma_set(sources))
            data.pop("_missing_fields_count", None)
            missing = services.compute_missing_fields(init)
            if include_gaps:
                data["_missing_fields"] = missing
            elif missing:
                data["_missing_fields_count"] = len(missing)
            enriched = data.get("enriched", False)
            v = data.get("verdict")
            actions = []
            if not enriched:
                actions.append(_next("enrich", "Not yet enriched", action="run", entity_id=entity_id))
            if v is None:
                actions.append(_next("score", "Score this entity", action="run", entity_id=entity_id))
            if missing and enriched:
                keys = ", ".join(m["key"] for m in missing[:5])
                actions.append(_next("enrich", f"Fill missing: {keys}", action="submit", entity_id=entity_id))
            return _trim(_suggest(data, *actions))

    # --- BULK_CREATE ---
    if action == "bulk_create":
        if not items or not isinstance(items, list):
            return _error("items (list of dicts with name+uni) is required for bulk_create", "VALIDATION_ERROR")
        with session_scope() as session:
            existing = {
                (n.lower().strip(), (u or "").lower().strip())
                for n, u in session.execute(select(Initiative.name, Initiative.uni)).all()
            }
            created_items = []
            skipped = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_name = (item.get("name") or "").strip()
                item_uni = (item.get("uni") or "").strip()
                if not item_name or not item_uni:
                    skipped += 1
                    continue
                if (item_name.lower(), item_uni.lower()) in existing:
                    skipped += 1
                    continue
                f = {"name": item_name, "uni": item_uni}
                custom_f = item.get("custom_fields", None) if isinstance(item, dict) else None
                for k, v in item.items():
                    if k in ("name", "uni", "custom_fields"):
                        continue
                    if k in services.UPDATABLE_FIELDS and v:
                        f[k] = v
                init = services.create_entity(session, **f)
                if custom_f and isinstance(custom_f, dict):
                    init.custom_fields_json = json.dumps(custom_f)
                    session.flush()
                existing.add((item_name.lower(), item_uni.lower()))
                created_items.append({"id": init.id, "name": init.name, "uni": init.uni})
            session.commit()
            result = {"created": len(created_items), "skipped_duplicates": skipped, "items": created_items}
            result["_db"] = _db_pulse(session)
            return result

    # --- CREATE ---
    if action == "create":
        if not name:
            return _error("name is required for create", "VALIDATION_ERROR")
        all_fields: dict = {"name": name}
        if uni:
            all_fields["uni"] = uni
        custom_fields = None
        metadata_fields: dict = {}
        if updates:
            updates = dict(updates)
            custom_fields = updates.pop("custom_fields", None)
            for k, v in updates.items():
                if k in services.UPDATABLE_FIELDS:
                    all_fields[k] = v
                else:
                    metadata_fields[k] = v
        with session_scope() as session:
            init = services.create_entity(session, **all_fields)
            if custom_fields and isinstance(custom_fields, dict):
                init.custom_fields_json = json.dumps(custom_fields)
            if metadata_fields:
                for k, v in metadata_fields.items():
                    init.set_field(k, v)
            session.flush()
            session.commit()
            result_data: dict = {"id": init.id, "name": init.name}
            if init.uni:
                result_data["uni"] = init.uni
            if init.website:
                result_data["website"] = init.website
            if init.field("github_org"):
                result_data["github_org"] = init.field("github_org")
            if metadata_fields:
                result_data["metadata"] = metadata_fields
            result = _suggest(
                result_data,
                _next("enrich", "Fetch web data", action="run", entity_id=init.id),
            )
            result["_db"] = _db_pulse(session)
            return result

    # --- UPDATE ---
    if action == "update":
        if entity_id is None:
            return _error("entity_id is required for update", "VALIDATION_ERROR")
        if not updates:
            return _error("updates dict is required", "VALIDATION_ERROR")
        updates = dict(updates)
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            old_name = init.name
            custom_fields = updates.pop("custom_fields", None)
            services.apply_updates(init, updates, services.UPDATABLE_FIELDS)
            if custom_fields is not None and isinstance(custom_fields, dict):
                services.merge_custom_fields(init, custom_fields)
            session.flush()
            session.commit()
            detail = _trim(services.entity_detail(init))
            if updates.get("name") and updates["name"] != old_name:
                detail["warning"] = f"Renamed: '{old_name}' -> '{updates['name']}'."
            detail["_db"] = _db_pulse(session)
            return detail

    # --- DELETE ---
    if action == "delete":
        if entity_id is None:
            return _error("entity_id is required for delete", "VALIDATION_ERROR")
        if not confirm:
            with session_scope() as session:
                init, err = _get_or_error(session, Initiative, entity_id)
                if err:
                    return err
                return {
                    "ok": False, "action": "delete",
                    "entity_id": init.id, "entity_name": init.name,
                    "warning": f"Will permanently delete '{init.name}' and all data. "
                               "Call again with confirm=True.",
                }
        with session_scope() as session:
            if not services.delete_entity(session, entity_id):
                return _error(f"Entity {entity_id} not found", "NOT_FOUND")
            session.commit()
            result = {"ok": True, "deleted_entity_id": entity_id}
            result["_db"] = _db_pulse(session)
            return result

    # --- EXPORT ---
    if action == "export":
        from scout.db import DATA_DIR
        from scout.exporter import export_xlsx
        try:
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
            return {"ok": True, "file": str(out_path), "filename": filename}
        except ImportError:
            return _error("openpyxl not installed. Run: pip install scout[xlsx]", "MISSING_DEP")
        except Exception as exc:
            return _error(f"Export failed: {exc}", "EXPORT_ERROR")

    # --- SIMILAR ---
    if action == "similar":
        from scout.embedder import find_similar as _find_similar
        with session_scope() as session:
            id_mask = services.build_similarity_id_mask(session, uni=uni, verdict=verdict)
            if id_mask is not None and not id_mask:
                return {"results": [], "hint": f"No {_entity_cfg()['label_plural']} match the filters."}
            results = _find_similar(
                query_text=query, initiative_id=entity_id,
                top_k=max(1, min(limit, 100)), id_mask=id_mask,
            )
            if not results:
                return _suggest({"results": []}, _next("configure", "Build embeddings first", action="embed"))
            ids = [r[0] for r in results]
            inits = session.execute(
                select(Initiative.id, Initiative.name, Initiative.uni)
                .where(Initiative.id.in_(ids))
            ).all()
            name_map = {r.id: (r.name, r.uni) for r in inits}
            return {"results": [
                {"id": rid, "name": name_map.get(rid, ("?", "?"))[0],
                 "uni": name_map.get(rid, ("?", "?"))[1], "similarity": sv}
                for rid, sv in results
            ]}

    return _error(f"Unknown action: {action!r}. Use: list, get, create, bulk_create, update, delete, export, similar.",
                  "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tool 2: enrich() — run / submit / process
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
async def enrich(
    action: str = "run",
    entity_id: int | None = None,
    discover: bool = False, incremental: bool = True,
    # Submit params
    source_type: str = "", content: str = "", source_url: str = "",
    summary: str = "", structured_fields: dict | None = None,
    # Process params
    limit: int = 20, do_enrich: bool = True, score: bool = True, entity_ids: str | None = None,
) -> dict:
    """Enrich entities with web data or LLM-gathered research, or run the autonomous pipeline.

    ACTIONS:
      run     — Scrape website, GitHub, structured data, DNS, sitemap, etc. (entity_id required).
      submit  — Store data YOU found via web search (entity_id + source_type + content required).
      process — Autonomous pipeline: enrich then score a batch. Primary batch tool.

    Args:
        action: run | submit | process.
        entity_id: Entity ID (for run/submit).
        discover: Run DuckDuckGo URL discovery before enrichment (adds ~12s).
        incremental: Skip enrichers whose targets are filled (default True).
        source_type: For submit: category label (e.g. "web_research", "linkedin", "patent_data").
        content: For submit: the information you found.
        source_url: For submit: URL where you found it.
        summary: For submit: brief summary.
        structured_fields: For submit: direct field updates (e.g. {"linkedin": "https://..."}).
        limit: For process: max items (1-50, default 20).
        do_enrich: For process: run enrichment step (default True).
        score: For process: run scoring step (default True). Requires API key.
        entity_ids: For process: comma-separated IDs (auto-selects from queue if omitted).
    """
    action = (action or "run").strip().lower()

    # --- RUN ---
    if action == "run":
        if entity_id is None:
            return _error("entity_id required for run", "VALIDATION_ERROR")
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            result = await services.enrich_with_diagnostics(session, init, discover=discover, incremental=incremental)
            session.commit()
            result["_db"] = _db_pulse(session)
            return _suggest(result, _next("score", "Score using enrichment data", action="run", entity_id=init.id))

    # --- SUBMIT ---
    if action == "submit":
        if entity_id is None:
            return _error("entity_id required for submit", "VALIDATION_ERROR")
        if not content or not content.strip():
            return _error("content cannot be empty", "VALIDATION_ERROR")
        if not source_type or not source_type.strip():
            return _error("source_type cannot be empty", "VALIDATION_ERROR")
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            r = services.submit_enrichment_data(
                session, init, source_type=source_type, content=content,
                source_url=source_url, summary=summary, structured_fields=structured_fields,
            )
            session.commit()
            result = {
                "entity_id": init.id, "entity_name": init.name,
                "enrichment_id": r["enrichment_id"], "source_type": r["source_type"],
                "content_length": r["content_length"], "_db": _db_pulse(session),
            }
            if r["fields_applied"]:
                result["fields_applied"] = r["fields_applied"]
            if r["fields_skipped"]:
                result["fields_skipped"] = r["fields_skipped"]
            return _suggest(result, _next("score", "Score with new data", action="run", entity_id=init.id))

    # --- PROCESS (autonomous pipeline) ---
    if action == "process":
        do_score = score
        api_key_warning = None
        if do_score:
            key_err = _check_api_key()
            if key_err:
                if not do_enrich:
                    return key_err
                do_score = False
                api_key_warning = (
                    "No API key — enriching only. "
                    "Set API key or use score(action='dossier') + score(action='submit') to score manually."
                )
        limit = max(1, min(limit, 50))
        et = get_entity_type()
        explicit_ids = _parse_ids(entity_ids)
        with session_scope() as session:
            if explicit_ids is not None:
                queue = [{"id": i, "needs_enrichment": do_enrich, "needs_scoring": do_score}
                         for i in explicit_ids[:limit]]
            else:
                queue = services.get_work_queue(session, limit)
            stats = services.compute_stats(session)
        if not queue:
            return _suggest(
                {"enrichment": None, "scoring": None, "remaining_in_queue": 0,
                 "hint": f"Work queue is empty. All {_entity_cfg()['label_plural']} are processed."},
                _next("entity", "Review results", action="list", verdict="reach_out_now"),
            )
        enrich_ids = [item["id"] for item in queue if item["needs_enrichment"]]
        score_only_ids = [item["id"] for item in queue
                          if item.get("needs_scoring") and not item.get("needs_enrichment")]
        discover_result = None
        enrich_result = None
        score_result = None
        if discover and enrich_ids:
            async def _do_discover(s, init):
                r = await services.run_discovery(s, init)
                return {"urls_found": r["urls_found"]}
            try:
                disc_results = await _run_batch(enrich_ids, _do_discover, concurrency=1)
                disc_ok = sum(1 for r in disc_results if r.get("ok") and r.get("urls_found", 0) > 0)
                discover_result = {"processed": len(enrich_ids), "urls_found": disc_ok,
                                   "no_new_urls": len(enrich_ids) - disc_ok}
            except ImportError:
                discover_result = {"skipped": True, "reason": "ddgs not installed"}
        enrich_failures = []
        if do_enrich and enrich_ids:
            async with open_crawler() as crawler:
                enrich_results = await _run_batch(enrich_ids, _do_enrich, concurrency=3, crawler=crawler)
            enrich_ok, enrich_failed = _batch_summary(enrich_results)
            enrich_result = {"processed": len(enrich_ids), "succeeded": enrich_ok, "failed": enrich_failed}
            enrich_failures = [r for r in enrich_results if not r.get("ok")]
            if enrich_failures:
                enrich_result["failed_items"] = enrich_failures
        failed_ids = {f["id"] for f in enrich_failures}
        score_ids = (score_only_ids + [i for i in enrich_ids if i not in failed_ids]) if do_enrich else score_only_ids
        if do_score and score_ids:
            client = LLMClient()
            score_results = await _run_batch(score_ids, _do_score, concurrency=1, client=client, entity_type=et)
            score_ok, score_failed = _batch_summary(score_results)
            verdict_counts: dict[str, int] = {}
            for r in score_results:
                if r.get("ok") and "verdict" in r:
                    v = r["verdict"]
                    verdict_counts[v] = verdict_counts.get(v, 0) + 1
            score_result = {"processed": len(score_ids), "succeeded": score_ok,
                            "failed": score_failed, "results": score_results, "summary": verdict_counts}
        remaining = max(0, (stats["total"] - stats["scored"]) - (score_result["succeeded"] if score_result else 0))
        progress_pct = round(100 * (1 - remaining / stats["total"]), 1) if stats["total"] else 100.0
        result = {"discovery": discover_result, "enrichment": enrich_result,
                  "scoring": score_result, "remaining_in_queue": remaining, "progress_pct": progress_pct}
        if api_key_warning:
            result["warning"] = api_key_warning
        if not do_score:
            return _suggest(result, _next("enrich", "Score enriched items", action="process", score=True))
        elif remaining > 0:
            return _suggest(result, _next("enrich", "Process next batch", action="process"))
        else:
            return _suggest(result, _next("entity", "Review top results", action="list", verdict="reach_out_now"))

    return _error(f"Unknown action: {action!r}. Use: run, submit, process.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tool 3: score() — run / submit / dossier
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
async def score(
    action: str = "run",
    entity_id: int | None = None,
    compact: bool = False,
    # Submit params
    grade_team: str = "", grade_tech: str = "", grade_opportunity: str = "",
    classification: str = "",
    contact_who: str = "", contact_channel: str = "website_form",
    engagement_hook: str = "", reasoning: str = "",
    dimension_grades: dict | None = None,
) -> dict:
    """Score entities via LLM or manual grade submission, or build scoring dossiers.

    ACTIONS:
      run     — LLM-powered scoring (3 parallel calls). Requires API key. (entity_id required).
      dossier — Build scoring dossiers + prompts WITHOUT LLM calls. No API key needed.
      submit  — Submit grades you evaluated yourself. No LLM call needed.

    Args:
        action: run | dossier | submit.
        entity_id: Entity ID.
        compact: For dossier: truncate to ~1500 chars each.
        grade_team: Team grade (A+ through D). For submit with standard types.
        grade_tech: Tech grade. For submit with standard types.
        grade_opportunity: Opportunity grade. For submit with standard types.
        classification: Entity classification (for submit).
        contact_who: Recommended contact (for submit).
        contact_channel: email | linkedin | event | website_form (for submit).
        engagement_hook: Suggested opener (for submit).
        reasoning: Assessment reasoning (for submit).
        dimension_grades: Dict of dimension->grade for custom types (for submit).
    """
    action = (action or "run").strip().lower()

    if entity_id is None:
        return _error("entity_id required", "VALIDATION_ERROR")

    # --- RUN ---
    if action == "run":
        key_err = _check_api_key()
        if key_err:
            return key_err
        with session_scope() as session:
            try:
                init, err = _get_or_error(session, Initiative, entity_id)
                if err:
                    return err
                has_enrichments = session.execute(
                    select(func.count(Enrichment.id)).where(Enrichment.initiative_id == init.id)
                ).scalar() or 0
                auto_enriched = False
                if has_enrichments == 0:
                    try:
                        async with open_crawler() as crawler:
                            await services.run_enrichment(session, init, crawler=crawler)
                        session.commit()
                        auto_enriched = True
                    except Exception:
                        log.info("Auto-enrich failed for %s, scoring with limited data", init.name)
                outreach = await services.run_scoring(session, init, entity_type=get_entity_type())
                session.commit()
                result = services.score_response_dict(outreach, extended=True)
                result["entity_id"] = init.id
                result["entity_name"] = init.name
                if auto_enriched:
                    result["auto_enriched"] = True
                result["_db"] = _db_pulse(session)
                return _trim(result)
            except Exception as exc:
                return _llm_error(exc)

    # --- DOSSIER ---
    if action == "dossier":
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            result = services.build_scoring_dossiers(session, init, compact=compact)
            dims = result.pop("dimension_names")
            grade_args = {f"grade_{dim}": "" for dim in dims}
            grade_args["classification"] = ""
            if compact:
                result["_note"] = "Dossiers truncated (compact=True). Use compact=False for full text."
            return _suggest(result, _next("score", "Submit your evaluation", action="submit",
                                          entity_id=init.id, **grade_args))

    # --- SUBMIT ---
    if action == "submit":
        et = get_entity_type()
        ecfg = get_entity_config(et)
        dims = ecfg.get("dimensions", ["team", "tech", "opportunity"])
        is_standard = (dims == ["team", "tech", "opportunity"])
        grades: dict[str, Grade] = {}
        if dimension_grades and isinstance(dimension_grades, dict):
            for dim, raw_grade in dimension_grades.items():
                if Grade.normalize(raw_grade) not in VALID_GRADES:
                    return _error(f"Invalid grade for '{dim}': {raw_grade!r}.", "VALIDATION_ERROR")
                grades[dim] = Grade.parse(raw_grade)
        elif is_standard:
            for label, raw in [("grade_team", grade_team), ("grade_tech", grade_tech),
                               ("grade_opportunity", grade_opportunity)]:
                if not raw:
                    return _error(f"{label} is required", "VALIDATION_ERROR")
                if Grade.normalize(raw) not in VALID_GRADES:
                    return _error(f"Invalid {label}: {raw!r}.", "VALIDATION_ERROR")
            grades = {"team": Grade.parse(grade_team), "tech": Grade.parse(grade_tech),
                      "opportunity": Grade.parse(grade_opportunity)}
        else:
            positional = [grade_team, grade_tech, grade_opportunity]
            for i, dim in enumerate(dims):
                raw = positional[i] if i < len(positional) else ""
                if not raw:
                    return _error(f"Missing grade for dimension '{dim}'.", "VALIDATION_ERROR")
                if Grade.normalize(raw) not in VALID_GRADES:
                    return _error(f"Invalid grade for '{dim}': {raw!r}.", "VALIDATION_ERROR")
                grades[dim] = Grade.parse(raw)
        if classification:
            classification = classification.strip().lower()
            valid_cls = valid_classifications(et)
            if is_standard and classification not in valid_cls:
                return _error(f"Invalid classification: {classification!r}.", "VALIDATION_ERROR")
        contact_channel = contact_channel.strip().lower()
        if contact_channel and contact_channel not in VALID_CHANNELS:
            return _error(f"Invalid contact_channel: {contact_channel!r}.", "VALIDATION_ERROR")
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            outreach = services.submit_score_data(
                session, init, grades,
                classification=classification, contact_who=contact_who,
                contact_channel=contact_channel, engagement_hook=engagement_hook,
                reasoning=reasoning, entity_type=get_entity_type(),
            )
            session.commit()
            result: dict = {
                "entity_id": init.id, "entity_name": init.name,
                "verdict": outreach.verdict, "score": outreach.score,
                "classification": outreach.classification,
            }
            dim_grades_stored = json_parse(outreach.dimension_grades_json, {})
            if dim_grades_stored:
                result["dimension_grades"] = {k: v.get("letter", "") for k, v in dim_grades_stored.items()}
            else:
                result.update({"grade_team": outreach.grade_team, "grade_tech": outreach.grade_tech,
                               "grade_opportunity": outreach.grade_opportunity})
            result["_db"] = _db_pulse(session)
            return result

    return _error(f"Unknown action: {action!r}. Use: run, dossier, submit.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Internal batch helpers (not MCP tools — used by enrich(action=process))
# ---------------------------------------------------------------------------


async def batch_enrich(entity_ids: str | None = None, limit: int = 20) -> dict:
    """Enrich multiple entities (internal, used by tests)."""
    limit = max(1, min(limit, 50))
    ids = _parse_ids(entity_ids)
    with session_scope() as session:
        if ids is None:
            queue = services.get_work_queue(session, limit)
            ids = [item["id"] for item in queue if item["needs_enrichment"]]
        if not ids:
            return {"processed": 0, "succeeded": 0, "failed": 0, "results": [],
                    "hint": f"No {_entity_cfg()['label_plural']} need enrichment."}
        ids = ids[:limit]
    async with open_crawler() as crawler:
        results = await _run_batch(ids, _do_enrich, concurrency=3, crawler=crawler)
    ok, failed = _batch_summary(results)
    result: dict = {"processed": len(ids), "succeeded": ok, "failed": failed, "results": results}
    if ok > 0:
        result["hint"] = "Scoring is the next step."
    return result


async def batch_score(entity_ids: str | None = None, limit: int = 20) -> dict:
    """Score multiple entities (internal, used by tests)."""
    key_err = _check_api_key()
    if key_err:
        return key_err
    limit = max(1, min(limit, 50))
    ids = _parse_ids(entity_ids)
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
    results = await _run_batch(ids, _do_score, concurrency=1, client=client, entity_type=et)
    ok, failed = _batch_summary(results)
    verdict_counts: dict[str, int] = {}
    for r in results:
        if r.get("ok") and "verdict" in r:
            v = r["verdict"]
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
    return {"processed": len(ids), "succeeded": ok, "failed": failed,
            "results": results, "summary": verdict_counts}


# ---------------------------------------------------------------------------
# Tool 4: overview() — database stats + work queue
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def overview(detail: bool = False, queue_limit: int = 0) -> dict:
    """Database statistics, analytics, and optionally the work queue.

    WHAT: Counts (total, enriched, scored) + breakdowns by verdict, classification, uni.
    WHEN: First call to understand database state.

    Args:
        detail: Include score distributions, top-N per verdict, grade breakdowns.
        queue_limit: If > 0, include prioritized work queue (items needing enrichment/scoring).
    """
    with session_scope() as session:
        stats = services.compute_stats(session)
        if detail:
            stats["aggregations"] = services.compute_aggregations(session)
        if queue_limit > 0:
            queue = services.get_work_queue(session, queue_limit)
            stats["queue"] = queue
            if queue:
                ids = ",".join(str(q["id"]) for q in queue)
                return _suggest(stats, _next("enrich", "Process these items", action="process", entity_ids=ids))
        actions = []
        if stats.get("total", 0) > stats.get("scored", 0):
            actions.append(_next("overview", "Get work queue", queue_limit=10))
        return _suggest(stats, *actions)


# ---------------------------------------------------------------------------
# Tool 5: project() — create / update / delete / score
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
async def project(
    action: str,
    project_id: int | None = None,
    entity_id: int | None = None,
    name: str | None = None,
    updates: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Manage sub-projects within an entity.

    ACTIONS:
      create — Requires entity_id + name. Optional updates={description, website, github_url, team}.
      update — Requires project_id + updates.
      delete — Requires project_id + confirm=True.
      score  — Requires project_id. Runs LLM scoring.

    Args:
        action: create | update | delete | score.
        project_id: Project ID (for update/delete/score).
        entity_id: Parent entity ID (for create).
        name: Project name (for create).
        updates: Dict of field->value.
        confirm: Must be True for delete.
    """
    action = (action or "").strip().lower()

    if action == "create":
        if entity_id is None or not name:
            return _error("entity_id and name required for create", "VALIDATION_ERROR")
        with session_scope() as session:
            _, err = _get_or_error(session, Initiative, entity_id)
            if err:
                return err
            proj = services.create_project(session, entity_id, name=name, **(updates or {}))
            session.commit()
            return _suggest(services.project_summary(proj),
                            _next("project", "Score the project", action="score", project_id=proj.id))

    if action == "update":
        if project_id is None:
            return _error("project_id required for update", "VALIDATION_ERROR")
        with session_scope() as session:
            proj, err = _get_or_error(session, Project, project_id)
            if err:
                return err
            if updates:
                services.apply_updates(proj, updates, ("name", "description", "website", "github_url", "team"))
            session.commit()
            return services.project_summary(proj)

    if action == "delete":
        if project_id is None:
            return _error("project_id required for delete", "VALIDATION_ERROR")
        with session_scope() as session:
            proj, err = _get_or_error(session, Project, project_id)
            if err:
                return err
            if not confirm:
                return {"ok": False, "action": "delete_project", "project_id": proj.id,
                        "project_name": proj.name, "entity_id": proj.initiative_id,
                        "warning": f"Will permanently delete project '{proj.name}'. Call again with confirm=True."}
            session.delete(proj)
            session.commit()
            return {"ok": True, "deleted_project_id": project_id}

    if action == "score":
        if project_id is None:
            return _error("project_id required for score", "VALIDATION_ERROR")
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
                outreach = await services.run_project_scoring(session, proj, init, entity_type=get_entity_type())
                session.commit()
                result = services.score_response_dict(outreach, extended=True)
                result.update({"project_id": proj.id, "project_name": proj.name,
                               "entity_id": init.id, "entity_name": init.name})
                return result
            except Exception as exc:
                return _llm_error(exc)

    return _error(f"Unknown action: {action!r}. Use: create, update, delete, score.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tool 6: configure() — database / columns / llm / embed / scrape
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
async def configure(
    action: str,
    name: str | None = None,
    # Database params
    entity_type: str = "initiative", context: str = "", dimensions: str = "",
    # Column params
    column_id: int | None = None, key: str | None = None, label: str | None = None,
    col_type: str | None = None, show_in_list: bool | None = None, sort_order: int | None = None,
    # LLM params
    provider: str | None = None, model: str | None = None,
    api_key: str | None = None, base_url: str | None = None,
    # Scrape params
    school: str | None = None, limit: int = 50,
) -> dict:
    """Manage databases, custom columns, LLM config, embeddings, and scrapers.

    ACTIONS (database):
      db_list — List all databases.
      db_select — Switch to a database (name required).
      db_create — Create new database (name required).
      db_delete — Delete a database (name required).
      db_backup — Backup a database (name required).
      db_list_backups — List available backups.
      db_restore — Restore from backup (name=backup name).
      db_delete_backup — Delete a backup (name=backup name).

    ACTIONS (columns):
      col_list — List custom column definitions.
      col_create — Create column (key + label required).
      col_update — Update column (column_id required).
      col_delete — Delete column (column_id required).

    ACTIONS (llm):
      llm_show — Show current LLM config.
      llm_set — Set provider/model/api_key/base_url.

    ACTIONS (other):
      embed — Build/rebuild dense embeddings for semantic search.
      scrape — Scrape TUM professor directory (school, limit params).

    Args:
        action: See actions above.
        name: Database name or backup name.
        entity_type: For db_create (default "initiative").
        context: For db_create with custom entity types.
        dimensions: For db_create: comma-separated scoring dimensions.
        column_id: For col_update/col_delete.
        key: Column key (for col_create).
        label: Column label (for col_create/col_update).
        col_type: Column type: text, number, boolean, url.
        show_in_list: Show column in list view.
        sort_order: Column display order.
        provider: LLM provider (anthropic, openai, openai_compatible, gemini).
        model: LLM model name.
        api_key: API key for the provider.
        base_url: Custom base URL (for openai_compatible).
        school: For scrape: TUM school filter (CIT, ED, LS, MGT, MED, NAT).
        limit: For scrape: max professors to import.
    """
    action = (action or "").strip().lower()

    # --- DATABASE ---
    if action == "db_list":
        return {"databases": list_databases(), "current": current_db_name()}

    if action == "db_select":
        if not name:
            return _error("name required", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        switch_db(name)
        et = get_entity_type()
        mcp._mcp_server.instructions = _build_instructions(et)
        return {"current": current_db_name(), "entity_type": et}

    if action == "db_create":
        if not name:
            return _error("name required", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        try:
            create_database(name, entity_type=entity_type)
        except ValueError as exc:
            return _error(str(exc), "ALREADY_EXISTS")
        if entity_type not in _BUILTIN_ENTITY_TYPES:
            from scout.db import set_entity_config_json
            custom_cfg = {
                "label": entity_type.replace("_", " "),
                "label_plural": entity_type.replace("_", " ") + "s",
                "context": context or entity_type.replace("_", " "),
            }
            if dimensions:
                custom_cfg["dimensions"] = [d.strip() for d in dimensions.split(",") if d.strip()]
            set_entity_config_json(custom_cfg)
            _seed_custom_prompts(entity_type, custom_cfg)
        mcp._mcp_server.instructions = _build_instructions(entity_type)
        return {"current": current_db_name(), "entity_type": entity_type,
                "message": f"Created and switched to '{name}'"}

    if action == "db_delete":
        if not name:
            return _error("name required", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
            delete_database(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "deleted": name, "current": current_db_name()}

    if action == "db_backup":
        if not name:
            return _error("name required", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
            backup_name = backup_database(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "backup": backup_name}

    if action == "db_list_backups":
        return {"backups": list_backups()}

    if action == "db_restore":
        if not name:
            return _error("name (backup name) required", "VALIDATION_ERROR")
        try:
            restored = restore_database(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "restored": restored}

    if action == "db_delete_backup":
        if not name:
            return _error("name (backup name) required", "VALIDATION_ERROR")
        try:
            delete_backup(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "deleted": name}

    # --- COLUMNS ---
    if action == "col_list":
        try:
            with session_scope() as session:
                return {"columns": services.get_custom_columns(session, database=current_db_name())}
        except Exception as exc:
            return _error(f"Failed: {exc}", "DB_ERROR")

    if action == "col_create":
        if not key or not label:
            return _error("key and label required", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.create_custom_column(
                session, key=key, label=label,
                col_type=col_type or "text",
                show_in_list=show_in_list if show_in_list is not None else True,
                sort_order=sort_order or 0, database=current_db_name())
            if result is None:
                return _error(f"Column key '{key}' already exists", "ALREADY_EXISTS")
            session.commit()
            return result

    if action == "col_update":
        if column_id is None:
            return _error("column_id required", "VALIDATION_ERROR")
        kwargs = {}
        if label is not None:
            kwargs["label"] = label
        if col_type is not None:
            kwargs["col_type"] = col_type
        if show_in_list is not None:
            kwargs["show_in_list"] = show_in_list
        if sort_order is not None:
            kwargs["sort_order"] = sort_order
        with session_scope() as session:
            result = services.update_custom_column(session, column_id, **kwargs)
            if result is None:
                return _error(f"Custom column {column_id} not found", "NOT_FOUND")
            session.commit()
            return result

    if action == "col_delete":
        if column_id is None:
            return _error("column_id required", "VALIDATION_ERROR")
        with session_scope() as session:
            if not services.delete_custom_column(session, column_id):
                return _error(f"Custom column {column_id} not found", "NOT_FOUND")
            session.commit()
            return {"ok": True, "deleted_column_id": column_id}

    # --- LLM ---
    if action == "llm_show":
        p = os.environ.get("LLM_PROVIDER", "anthropic")
        m = os.environ.get("LLM_MODEL", "")
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
                       or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        return {"provider": p, "model": m or "(default)", "api_key_set": has_key,
                "base_url": os.environ.get("OPENAI_BASE_URL", "")}

    if action == "llm_set":
        if provider:
            os.environ["LLM_PROVIDER"] = provider
        if model:
            os.environ["LLM_MODEL"] = model
        if api_key:
            p = provider or os.environ.get("LLM_PROVIDER", "anthropic")
            if p == "anthropic":
                os.environ["ANTHROPIC_API_KEY"] = api_key
            elif p == "gemini":
                os.environ["GOOGLE_API_KEY"] = api_key
            else:
                os.environ["OPENAI_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
        return {"ok": True, "provider": os.environ.get("LLM_PROVIDER", "anthropic"),
                "model": os.environ.get("LLM_MODEL", "") or "(default)",
                "api_key_set": bool(api_key or _check_api_key() is None)}

    # --- EMBED ---
    if action == "embed":
        from scout.embedder import embed_all
        with session_scope() as session:
            try:
                count = embed_all(session)
            except Exception as exc:
                return _error(f"Embedding failed: {exc}", "EMBEDDING_ERROR")
            return _suggest({"ok": True, "embedded": count},
                            _next("entity", "Try semantic search", action="similar", query=""))

    # --- SCRAPE ---
    if action == "scrape":
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
        with session_scope() as session:
            result = services.import_scraped_entities(session, professors)
            session.commit()
        return _suggest({**result, "total_found": len(professors)},
                        _next("enrich", "Enrich and score imported professors", action="process"))

    return _error(f"Unknown action: {action!r}. Use: db_list, db_select, db_create, db_delete, "
                  "db_backup, db_list_backups, db_restore, db_delete_backup, "
                  "col_list, col_create, col_update, col_delete, "
                  "llm_show, llm_set, embed, scrape.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tool 7: script() — save / list / read / delete / run
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
def script(
    action: str,
    name: str | None = None,
    code: str | None = None,
    description: str | None = None,
    script_type: str = "custom",
    entity_type: str | None = None,
    entity_id: int | None = None,
    timeout: float = 60.0,
) -> dict:
    """Manage and run persistent scripts — offload reasoning to classical code.

    ACTIONS:
      save   — Create or update a script (name + code required).
      list   — List all saved scripts.
      read   — Read a script's code (name required).
      delete — Delete a script (name required).
      run    — Execute a saved script (name required).

    Args:
        action: save | list | read | delete | run.
        name: Script identifier.
        code: Python source code (for save). Scripts get a `ctx` object:
              ctx.entity(id), ctx.entities(), ctx.update(), ctx.create(),
              ctx.enrich(), ctx.secret("name"), ctx.http, ctx.env(), ctx.log(), ctx.result().
        description: What the script does (for save).
        script_type: enricher | connector | transform | report | custom.
        entity_type: Restrict to an entity type (NULL = all).
        entity_id: For run: entity ID available as ctx.entity_id.
        timeout: For run: max seconds (default 60, max 300).
    """
    action = action.strip().lower()

    if action == "save":
        if not name or not code:
            return _error("name and code required for save", "VALIDATION_ERROR")
        with session_scope() as session:
            try:
                result = services.save_script(
                    session, name=name, code=code, description=description or "",
                    script_type=script_type, entity_type=entity_type,
                )
                session.commit()
            except ValueError as e:
                return _error(str(e), "VALIDATION_ERROR")
        return _suggest({"ok": True, "action": "saved", **result},
                        _next("script", "Run this script", action="run", name=name))

    if action == "list":
        with session_scope() as session:
            scripts = services.list_scripts(
                session, script_type=script_type if script_type != "custom" else None,
                entity_type=entity_type,
            )
        return {"ok": True, "scripts": scripts, "count": len(scripts)}

    if action == "read":
        if not name:
            return _error("name required for read", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.get_script(session, name)
        if result is None:
            return _error(f"Script '{name}' not found", "NOT_FOUND")
        return {"ok": True, **result}

    if action == "delete":
        if not name:
            return _error("name required for delete", "VALIDATION_ERROR")
        with session_scope() as session:
            deleted = services.delete_script(session, name)
            session.commit()
        if not deleted:
            return _error(f"Script '{name}' not found", "NOT_FOUND")
        return {"ok": True, "action": "deleted", "name": name}

    if action == "run":
        from scout.executor import run_script as _run
        if not name:
            return _error("name required for run", "VALIDATION_ERROR")
        with session_scope() as session:
            script_code = services.get_script_code(session, name)
        if script_code is None:
            return _error(f"Script '{name}' not found", "NOT_FOUND",
                          fix="Use script(action='list') to see available scripts.")
        timeout = max(1.0, min(timeout, 300.0))
        with session_scope() as session:
            result = _run(script_code, session, entity_id=entity_id, timeout=timeout)
            if result["ok"]:
                session.commit()
        return result

    return _error(f"Unknown action: {action}", "VALIDATION_ERROR",
                  fix="Use: save, list, read, delete, run")


# ---------------------------------------------------------------------------
# Tool 8: prompt() — save / list / read / delete + scoring prompts
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
def prompt(
    action: str,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    prompt_type: str = "custom",
    entity_type: str | None = None,
    compact: bool = False,
) -> dict:
    """Manage prompts: general-purpose templates AND scoring prompts.

    ACTIONS (general prompts):
      save   — Create or update a prompt (name + content required).
      list   — List all saved prompts.
      read   — Read a prompt's content (name required).
      delete — Delete a prompt (name required).

    ACTIONS (scoring prompts):
      scoring_list   — List scoring prompt definitions.
      scoring_update — Update a scoring prompt (name=key, content required).

    Args:
        action: save | list | read | delete | scoring_list | scoring_update.
        name: Prompt identifier or scoring prompt key.
        content: Prompt text (for save / scoring_update).
        description: What the prompt does (for save).
        prompt_type: scoring | enrichment | analysis | classification | custom.
        entity_type: Restrict to an entity type (NULL = all).
        compact: For scoring_list: omit full content.
    """
    action = action.strip().lower()

    if action == "save":
        if not name or not content:
            return _error("name and content required for save", "VALIDATION_ERROR")
        with session_scope() as session:
            try:
                result = services.save_prompt(
                    session, name=name, content=content, description=description or "",
                    prompt_type=prompt_type, entity_type=entity_type,
                )
                session.commit()
            except ValueError as e:
                return _error(str(e), "VALIDATION_ERROR")
        return {"ok": True, "action": "saved", **result}

    if action == "list":
        with session_scope() as session:
            prompts = services.list_prompts(
                session, prompt_type=prompt_type if prompt_type != "custom" else None,
                entity_type=entity_type,
            )
        return {"ok": True, "prompts": prompts, "count": len(prompts)}

    if action == "read":
        if not name:
            return _error("name required for read", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.get_prompt(session, name)
        if result is None:
            return _error(f"Prompt '{name}' not found", "NOT_FOUND")
        return {"ok": True, **result}

    if action == "delete":
        if not name:
            return _error("name required for delete", "VALIDATION_ERROR")
        with session_scope() as session:
            deleted = services.delete_prompt(session, name)
            session.commit()
        if not deleted:
            return _error(f"Prompt '{name}' not found", "NOT_FOUND")
        return {"ok": True, "action": "deleted", "name": name}

    if action == "scoring_list":
        try:
            with session_scope() as session:
                prompts_list = services.get_scoring_prompts(session)
                if compact:
                    return [{"key": p["key"], "label": p["label"], "updated_at": p["updated_at"]}
                            for p in prompts_list]
                return prompts_list
        except Exception as exc:
            return _error(f"Failed: {exc}", "DB_ERROR")

    if action == "scoring_update":
        if not name or not content:
            return _error("name (key) and content required for scoring_update", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.update_scoring_prompt(session, name, content)
            if result is None:
                return _error(f"Scoring prompt '{name}' not found", "NOT_FOUND")
            session.commit()
            return result

    return _error(f"Unknown action: {action}", "VALIDATION_ERROR",
                  fix="Use: save, list, read, delete, scoring_list, scoring_update")


@mcp.tool(annotations=_WRITE)
def credential(
    action: str,
    name: str | None = None,
    value: str | None = None,
    service: str = "",
    description: str = "",
) -> dict:
    """Manage encrypted credentials — store API keys for use in scripts.

    Credentials are encrypted at rest (Fernet if cryptography is installed,
    base64 fallback otherwise). Scripts access them via ctx.secret("name").

    Actions:
      save   — Store or update a credential (requires name + value).
      list   — List all credentials (names and services only, never values).
      delete — Delete a credential (requires name).

    Args:
        action: save | list | delete.
        name: Credential identifier (required for save/delete).
        value: The secret value to store (required for save, never returned).
        service: Service name (e.g. "openai", "hubspot") for organization.
        description: What this credential is for.
    """
    action = action.strip().lower()

    if action == "save":
        if not name or not value:
            return _error("name and value required for save", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.save_credential(
                session, name=name, value=value,
                service=service, description=description,
            )
            session.commit()
        return {"ok": True, "action": "saved", **result}

    if action == "list":
        with session_scope() as session:
            creds = services.list_credentials(session)
        return {"ok": True, "credentials": creds, "count": len(creds)}

    if action == "delete":
        if not name:
            return _error("name required for delete", "VALIDATION_ERROR")
        with session_scope() as session:
            deleted = services.delete_credential(session, name)
            session.commit()
        if not deleted:
            return _error(f"Credential '{name}' not found", "NOT_FOUND")
        return {"ok": True, "action": "deleted", "name": name}

    return _error(
        f"Unknown action: {action}",
        "VALIDATION_ERROR",
        fix="Use: save, list, delete",
    )


# ---------------------------------------------------------------------------
# Sync helpers for backward-compat wrappers (avoid async in sync contexts)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Backward-compatible aliases — used by tests and REST API
# ---------------------------------------------------------------------------

def list_entities(**kw): return entity(action="list", **kw)
def get_entity(entity_id=None, **kw):
    if entity_id is not None:
        kw["entity_id"] = entity_id
    return entity(action="get", **kw)
def manage_entity(**kw): return entity(**kw)
async def enrich_entity(entity_id=None, **kw):
    if entity_id is not None:
        kw["entity_id"] = entity_id
    return await enrich(action="run", **kw)
def submit_enrichment(entity_id=None, **kw):
    if entity_id is None:
        return _error("entity_id required", "VALIDATION_ERROR")
    content = kw.get("content", "")
    source_type = kw.get("source_type", "")
    if not content or not content.strip():
        return _error("content cannot be empty", "VALIDATION_ERROR")
    if not source_type or not source_type.strip():
        return _error("source_type cannot be empty", "VALIDATION_ERROR")
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, entity_id)
        if err:
            return err
        r = services.submit_enrichment_data(
            session, init, source_type=source_type, content=content,
            source_url=kw.get("source_url", ""), summary=kw.get("summary", ""),
            structured_fields=kw.get("structured_fields"),
        )
        session.commit()
        result = {"entity_id": init.id, "entity_name": init.name, **r, "_db": _db_pulse(session)}
        return _suggest(result, _next("score", "Score with new data", action="run", entity_id=init.id))
async def score_entity(entity_id=None, **kw):
    if entity_id is not None:
        kw["entity_id"] = entity_id
    return await score(action="run", **kw)
def submit_score(entity_id=None, **kw):
    if entity_id is None:
        return _error("entity_id required", "VALIDATION_ERROR")
    return asyncio.get_event_loop().run_until_complete(
        score(action="submit", entity_id=entity_id, **kw)
    )
def get_scoring_dossier(entity_id=None, **kw):
    if entity_id is None:
        return _error("entity_id required", "VALIDATION_ERROR")
    compact = kw.get("compact", False)
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, entity_id)
        if err:
            return err
        result = services.build_scoring_dossiers(session, init, compact=compact)
        dims = result.pop("dimension_names")
        grade_args = {f"grade_{dim}": "" for dim in dims}
        grade_args["classification"] = ""
        if compact:
            result["_note"] = "Dossiers truncated (compact=True). Use compact=False for full text."
        return _suggest(result, _next("score", "Submit your evaluation", action="submit",
                                      entity_id=entity_id, **grade_args))
def get_overview(**kw): return overview(**kw)
def get_work_queue(limit: int = 10): return overview(queue_limit=limit)
def find_similar(**kw): return entity(action="similar", **kw)
def export_entities(**kw): return entity(action="export", **kw)
def list_scoring_prompts(**kw): return prompt(action="scoring_list", **kw)
def update_scoring_prompt(**kw): return prompt(action="scoring_update", name=kw.pop("key", None), **kw)
def manage_project(**kw): return asyncio.get_event_loop().run_until_complete(project(**kw))
def manage_database(**kw):
    a = kw.pop("action", "list")
    return asyncio.get_event_loop().run_until_complete(configure(action=f"db_{a}", **kw))
def get_custom_columns(): return asyncio.get_event_loop().run_until_complete(configure(action="col_list"))
def create_custom_column(**kw): return asyncio.get_event_loop().run_until_complete(configure(action="col_create", **kw))
def update_custom_column(**kw): return asyncio.get_event_loop().run_until_complete(configure(action="col_update", **kw))
def delete_custom_column(**kw): return asyncio.get_event_loop().run_until_complete(configure(action="col_delete", **kw))
def show_llm_config(): return asyncio.get_event_loop().run_until_complete(configure(action="llm_show"))
def configure_llm(**kw): return asyncio.get_event_loop().run_until_complete(configure(action="llm_set", **kw))
def embed_all_tool(): return asyncio.get_event_loop().run_until_complete(configure(action="embed"))
async def scrape_tum_professors(**kw): return await configure(action="scrape", **kw)
def run_script(**kw): return script(action="run", **kw)
async def process_queue(**kw):
    # Translate old param name: enrich= → do_enrich=
    if "enrich" in kw:
        kw["do_enrich"] = kw.pop("enrich")
    return await enrich(action="process", **kw)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Scout MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
