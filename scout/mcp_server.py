from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from sqlalchemy import and_, delete, func, select

from scout import services
from scout.db import (
    backup_database, create_database, current_db_name, delete_database,
    get_entity_type, get_session, init_db,
    list_databases, session_scope, switch_db, validate_db_name,
)
from scout.enricher import open_crawler
from scout.models import Enrichment, Initiative, OutreachScore, Project
from scout.scorer import (
    ENTITY_CONFIG, GRADE_MAP, VALID_GRADES, Grade,
    LLMClient, build_full_dossier, build_team_dossier, build_tech_dossier,
    create_score_from_grades, default_prompts_for, get_entity_config,
    valid_classifications,
)
from scout.utils import json_parse

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
        "QUICK: get_overview() → get_work_queue() → process_queue(). "
        "Use submit_enrichment() to store data you find via web search. "
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
            "warning": "No data fetched — add website/github URLs or run enrich_initiative(id, discover=True)"}


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
                "1. get_overview() — database state + analytics.",
                "2. get_work_queue() — prioritized items needing work.",
                "3. process_queue(limit=20) — enriches AND scores in one call.",
                "4. Repeat until remaining_in_queue=0.",
                f"5. list_initiatives(verdict='reach_out_now') — review top {lp}.",
            ],
            "single_item": [
                f"1. manage_initiative(action='create', name=..., uni=...) — add new {cfg['label']}.",
                "2. enrich_initiative(id, discover=True) — find URLs + fetch data.",
                "3. score_initiative(id) — LLM scoring (3 parallel dimensions).",
                "4. get_initiative(id) — inspect full details.",
            ],
            "llm_enrichment": [
                "1. Search the web for information about the entity.",
                "2. submit_enrichment(id, source_type='...', content='...') — store what you found.",
                "3. Repeat for different sources (LinkedIn, news, patents, etc.).",
                "4. score_initiative(id) — score with enriched data.",
            ],
            "llm_free_scoring": [
                "1. get_scoring_dossier(id) — get prompts + dossiers.",
                "2. Evaluate each dimension per its prompt.",
                "3. submit_score(id, grade_team=..., ...) — save results.",
            ],
        },
        "tools_by_frequency": {
            "core": "list_initiatives, get_initiative, process_queue, get_work_queue, get_overview",
            "single_item": "enrich_initiative, score_initiative, manage_initiative",
            "llm_enrichment": "submit_enrichment — store data you find via web search",
            "scoring": "get_scoring_dossier, submit_score",
            "search": "find_similar",
            "admin": "manage_project, manage_database, manage_settings",
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
    }, indent=2)


# ---------------------------------------------------------------------------
# Tools: List & Detail
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def list_initiatives(
    verdict: str | None = None, classification: str | None = None,
    uni: str | None = None, faculty: str | None = None,
    search: str | None = None,
    sort_by: str = "score", sort_dir: str = "desc", limit: int = 20,
    fields: str | None = None,
) -> list[dict]:
    """List and filter entities. Returns summaries with scores and verdicts.

    WHEN: Browse, search, or filter. For autonomous processing, use get_work_queue().
    COMPACT: fields="id,name,verdict,score" returns only those keys (saves tokens).

    Args:
        verdict: Filter: reach_out_now, reach_out_soon, monitor, skip, unscored (comma-separated).
        classification: Filter by type (comma-separated).
        uni: Filter by university (comma-separated).
        faculty: Filter by faculty (comma-separated).
        search: Free-text FTS5 search across name, description, sector, domains, faculty.
        sort_by: score, name, uni, faculty, verdict, grade_team, grade_tech, grade_opportunity.
        sort_dir: asc or desc.
        limit: Max results (default 20, max 500).
        fields: Comma-separated field names for compact output.
    """
    fields_set = {f.strip() for f in fields.split(",") if f.strip()} if fields else None
    with session_scope() as session:
        items, _ = services.query_initiatives(
            session, verdict=verdict, classification=classification,
            uni=uni, faculty=faculty, search=search, sort_by=sort_by, sort_dir=sort_dir,
            page=1, per_page=max(1, min(limit, 500)), fields=fields_set,
        )
        return _trim(items, max_str=200)


@mcp.tool(annotations=_READ)
def get_initiative(initiative_id: int, compact: bool = False) -> dict:
    """Get full details for one entity: profile, enrichments, projects, scores, data gaps.

    WHEN: After list_initiatives() to inspect before enriching or scoring.

    Args:
        initiative_id: Entity ID.
        compact: Lighter payload (skips enrichment summaries, projects, reasoning).
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err
        data = services.initiative_detail_compact(init) if compact else services.initiative_detail(init)
        actions = []
        if not data.get("enriched", False):
            actions.append(_next("enrich_initiative", "Not yet enriched", initiative_id=initiative_id))
        if data.get("verdict") is None:
            actions.append(_next("score_initiative", "Not yet scored", initiative_id=initiative_id))
        return _trim(_suggest(data, *actions))


# ---------------------------------------------------------------------------
# Tools: Manage Initiative (Create / Update / Delete)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
def manage_initiative(
    action: str,
    initiative_id: int | None = None,
    name: str | None = None,
    uni: str | None = None,
    updates: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Create, update, or delete entities.

    ACTIONS:
    - create: Requires name. Pass updates={field: value} for optional fields.
        For initiative/professor types, uni is also expected.
        For custom entity types, use updates or custom_fields for domain-specific data.
    - update: Requires initiative_id. Pass updates={field: value} for changes.
        For custom entity types, any key not in the standard columns is stored in metadata.
    - delete: Requires initiative_id + confirm=True.

    Args:
        action: "create", "update", or "delete".
        initiative_id: Entity ID (required for update/delete).
        name: Entity name (required for create).
        uni: University/institution (optional; mainly for initiative/professor types).
        updates: Dict of field->value. Standard keys: faculty, sector, description,
            website, email, team_page, team_size, linkedin, github_org, key_repos,
            sponsors, competitions, mode, relevance, custom_fields.
            Any other keys are stored in metadata (for custom entity types).
        confirm: Must be True for delete.
    """
    action = (action or "").strip().lower()

    if action == "create":
        if not name:
            return _error("name is required for create", "VALIDATION_ERROR")
        all_fields: dict = {"name": name}
        if uni:
            all_fields["uni"] = uni
        custom_fields = None
        metadata_fields: dict = {}
        if updates:
            updates = dict(updates)  # copy to avoid mutation
            custom_fields = updates.pop("custom_fields", None)
            # Separate standard fields from metadata fields
            for k, v in updates.items():
                if k in services.UPDATABLE_FIELDS:
                    all_fields[k] = v
                else:
                    metadata_fields[k] = v
        with session_scope() as session:
            init = services.create_initiative(session, **all_fields)
            if custom_fields and isinstance(custom_fields, dict):
                init.custom_fields_json = json.dumps(custom_fields)
            # Store non-standard fields in metadata_json
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
                _next("enrich_initiative", "Fetch web data or submit_enrichment()", initiative_id=init.id),
            )
            result["_db"] = _db_pulse(session)
            return result

    if action == "update":
        if initiative_id is None:
            return _error("initiative_id is required for update", "VALIDATION_ERROR")
        if not updates:
            return _error("updates dict is required", "VALIDATION_ERROR")
        updates = dict(updates)  # copy
        with session_scope() as session:
            init, err = _get_or_error(session, Initiative, initiative_id)
            if err:
                return err
            old_name = init.name
            custom_fields = updates.pop("custom_fields", None)
            services.apply_updates(init, updates, services.UPDATABLE_FIELDS)
            if custom_fields is not None and isinstance(custom_fields, dict):
                existing = json_parse(init.custom_fields_json)
                existing.update(custom_fields)
                existing = {k: v for k, v in existing.items() if v is not None}
                init.custom_fields_json = json.dumps(existing)
            session.flush()
            session.commit()
            detail = _trim(services.initiative_detail(init))
            if updates.get("name") and updates["name"] != old_name:
                detail["warning"] = (
                    f"Renamed: '{old_name}' -> '{updates['name']}'. "
                    "Verify this is correct."
                )
            detail["_db"] = _db_pulse(session)
            return detail

    if action == "delete":
        if initiative_id is None:
            return _error("initiative_id is required for delete", "VALIDATION_ERROR")
        if not confirm:
            with session_scope() as session:
                init, err = _get_or_error(session, Initiative, initiative_id)
                if err:
                    return err
                return {
                    "ok": False, "action": "delete",
                    "initiative_id": init.id, "initiative_name": init.name,
                    "warning": f"Will permanently delete '{init.name}' and all data. "
                               "Call again with confirm=True.",
                }
        with session_scope() as session:
            if not services.delete_initiative(session, initiative_id):
                return _error(f"Initiative {initiative_id} not found", "NOT_FOUND")
            session.commit()
            result = {"ok": True, "deleted_initiative_id": initiative_id}
            result["_db"] = _db_pulse(session)
            return result

    return _error(f"Unknown action: {action!r}. Use create, update, or delete.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tools: Enrichment & Scoring
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
async def enrich_initiative(initiative_id: int, discover: bool = False) -> dict:
    """Fetch enrichment data from website, GitHub, extra links, plus extended sources.

    WHAT: Scrapes all known URLs + extracts structured data (JSON-LD/OpenGraph), tech stack,
    DNS records, sitemap structure, career pages, and deep git analysis (README, deps, releases).
    Takes 5-20s. Auto-enables discovery when no URLs are configured.
    WHEN: Before score_initiative(). Discovery finds LinkedIn, GitHub, HuggingFace URLs via DuckDuckGo.

    Args:
        initiative_id: Entity ID.
        discover: Run DuckDuckGo URL discovery first (adds ~12s). Auto-enabled when no URLs configured.
    """
    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, initiative_id)
        if err:
            return err

        # Smart default: auto-discover when initiative has no URLs at all
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
                disc = await services.run_discovery(session, init)
                session.commit()
                discover_result = {"urls_found": disc["urls_found"]}
                if auto_discover:
                    discover_result["auto_triggered"] = True
            except ImportError:
                discover_result = {"skipped": True, "reason": "ddgs not installed — pip install 'scout[crawl]'"}
            except Exception as exc:
                discover_result = {"skipped": True, "reason": str(exc)[:100]}

        async with open_crawler() as crawler:
            new = await services.run_enrichment(session, init, crawler=crawler)
        session.commit()

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
            "initiative_id": init.id, "initiative_name": init.name,
            "enrichments_added": len(new),
            "sources_succeeded": succeeded,
            "sources_failed": failed,
            "sources_not_configured": not_configured,
        }
        if discover_result:
            result["discovery"] = discover_result
        result["_db"] = _db_pulse(session)
        return _suggest(
            result,
            _next("score_initiative", "Score using enrichment data", initiative_id=init.id),
        )


@mcp.tool(annotations=_WRITE)
def submit_enrichment(
    entity_id: int,
    source_type: str,
    content: str,
    source_url: str = "",
    summary: str = "",
) -> dict:
    """Store enrichment data that you (the LLM) found via your own research.

    Use this after you've searched the web, read documents, or gathered
    information about an entity. This persists your findings so they
    feed into dossiers and scoring.

    WHEN: After using web search/URL reading to gather info about an entity.
    WHY: Your findings become part of the scoring dossier automatically.

    Args:
        entity_id: Entity ID to enrich.
        source_type: Category label (e.g. "web_research", "linkedin", "patent_data",
            "citation_graph", "news", "funding", or any custom string).
        content: The information you found (raw text, extracted data, etc.).
        source_url: URL where you found it (recommended but optional).
        summary: Brief summary (optional; auto-truncated from content if omitted).
    """
    if not content or not content.strip():
        return _error("Content cannot be empty", "VALIDATION_ERROR")
    if not source_type or not source_type.strip():
        return _error("source_type cannot be empty", "VALIDATION_ERROR")

    with session_scope() as session:
        init, err = _get_or_error(session, Initiative, entity_id)
        if err:
            return err

        enrichment = Enrichment(
            initiative_id=init.id,
            source_type=source_type.strip(),
            source_url=source_url.strip() if source_url else None,
            raw_text=content.strip()[:15000],
            summary=(summary.strip() if summary else content.strip()[:500]),
            fetched_at=datetime.now(UTC),
        )
        session.add(enrichment)
        session.commit()

        return _suggest(
            {
                "entity_id": init.id,
                "entity_name": init.name,
                "enrichment_id": enrichment.id,
                "source_type": enrichment.source_type,
                "content_length": len(enrichment.raw_text),
                "_db": _db_pulse(session),
            },
            _next("score_initiative", "Score with new enrichment data", initiative_id=init.id),
        )


@mcp.tool(annotations=_WRITE)
async def score_initiative(initiative_id: int) -> dict:
    """Score an entity on 3 dimensions (team, tech, opportunity) via parallel LLM calls.

    WHAT: 3 parallel LLM calls -> deterministic verdict + score. Takes 5-15s. Requires API key.
    Auto-enriches first if no enrichment data exists (prevents weak scores).
    ALTERNATIVE: get_scoring_dossier() + submit_score() for API-key-free scoring.

    Args:
        initiative_id: Entity ID.
    """
    key_err = _check_api_key()
    if key_err:
        return key_err
    with session_scope() as session:
        try:
            init, err = _get_or_error(session, Initiative, initiative_id)
            if err:
                return err

            # Auto-enrich if no enrichments exist — prevents weak scores
            has_enrichments = session.execute(
                select(func.count(Enrichment.id))
                .where(Enrichment.initiative_id == init.id)
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

            outreach = await services.run_scoring(
                session, init, entity_type=get_entity_type(),
            )
            session.commit()
            result = services.score_response_dict(outreach, extended=True)
            result["initiative_id"] = init.id
            result["initiative_name"] = init.name
            if auto_enriched:
                result["auto_enriched"] = True
            result["_db"] = _db_pulse(session)
            return _trim(result)
        except Exception as exc:
            return _llm_error(exc)


@mcp.tool(annotations=_READ)
def get_scoring_dossier(initiative_id: int) -> dict:
    """Build scoring dossiers and prompts WITHOUT making LLM calls.

    WHAT: Returns 3 dimension dossiers + system prompts for LLM-free scoring. No API key needed.
    WHEN: When you want to evaluate the dossiers yourself.
    NEXT: Evaluate each dimension, then submit_score() with results.

    Args:
        initiative_id: Entity ID.
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

        ecfg = get_entity_config(et)
        dims = ecfg.get("dimensions", ["team", "tech", "opportunity"])

        # Build dimension dossiers: first dim gets team dossier, second gets tech,
        # last gets full (includes all enrichments for big-picture assessment).
        dossier_builders = [build_team_dossier, build_tech_dossier, build_full_dossier]
        dimensions_out = {}
        for i, dim in enumerate(dims):
            builder_idx = min(i, len(dossier_builders) - 1)
            # Last dimension always gets the full dossier
            if i == len(dims) - 1:
                builder_idx = len(dossier_builders) - 1
            builder = dossier_builders[builder_idx]
            prompt = prompts.get(dim, defaults.get(dim, (dim, f"Evaluate the {dim} dimension."))[1])
            dimensions_out[dim] = {"prompt": prompt, "dossier": builder(init, enrichments, et)}

        # Build grade args hint for submit_score
        grade_args = {f"grade_{dim}": "" for dim in dims}
        grade_args["classification"] = ""

        return _suggest(
            {
                "initiative_id": init.id, "initiative_name": init.name,
                "entity_type": et, "enriched": len(enrichments) > 0,
                "dimensions": dimensions_out,
            },
            _next("submit_score", "Submit your evaluation",
                  initiative_id=init.id, **grade_args),
        )


@mcp.tool(annotations=_WRITE)
def submit_score(
    initiative_id: int,
    grade_team: str = "", grade_tech: str = "", grade_opportunity: str = "",
    classification: str = "",
    contact_who: str = "", contact_channel: str = "website_form",
    engagement_hook: str = "", reasoning: str = "",
    dimension_grades: dict | None = None,
) -> dict:
    """Submit externally-evaluated scores. No LLM call needed.

    WHAT: Validates grades, computes verdict/score deterministically, saves the score.
    WHEN: After get_scoring_dossier() when you've evaluated the dossiers.

    Supports both standard and custom scoring dimensions:
    - Standard (initiative/professor): use grade_team, grade_tech, grade_opportunity.
    - Custom entity types: use dimension_grades={"dim_name": "grade", ...}.
      The dimension names must match those from get_scoring_dossier().

    Args:
        initiative_id: Entity ID.
        grade_team: Team grade (A+, A, A-, B+, B, B-, C+, C, C-, D). For standard types.
        grade_tech: Tech grade (same scale). For standard types.
        grade_opportunity: Opportunity grade (same scale). For standard types.
        classification: Entity classification (optional for custom types).
        contact_who: Recommended contact person/role.
        contact_channel: email, linkedin, event, or website_form.
        engagement_hook: Suggested opening line.
        reasoning: Brief reasoning for the assessment.
        dimension_grades: Dict of dimension->grade for custom entity types.
            Example: {"novelty": "A", "methodology": "B+", "impact": "A-"}.
    """
    et = get_entity_type()
    ecfg = get_entity_config(et)
    dims = ecfg.get("dimensions", ["team", "tech", "opportunity"])
    is_standard = (dims == ["team", "tech", "opportunity"])

    # Build grades dict from either standard params or dimension_grades
    grades: dict[str, Grade] = {}
    if dimension_grades and isinstance(dimension_grades, dict):
        # Custom dimensions
        for dim, raw_grade in dimension_grades.items():
            raw_str = str(raw_grade).strip().upper().replace(" ", "")
            if raw_str not in VALID_GRADES:
                return _error(
                    f"Invalid grade for '{dim}': {raw_grade!r}. Valid: {', '.join(sorted(VALID_GRADES))}",
                    "VALIDATION_ERROR")
            grades[dim] = Grade.parse(raw_grade)
    elif is_standard:
        # Standard team/tech/opportunity
        for label, raw in [("grade_team", grade_team), ("grade_tech", grade_tech),
                           ("grade_opportunity", grade_opportunity)]:
            if not raw:
                return _error(f"{label} is required", "VALIDATION_ERROR")
            normalized = raw.strip().upper().replace(" ", "")
            if normalized not in VALID_GRADES:
                return _error(
                    f"Invalid {label}: {raw!r}. Valid: {', '.join(sorted(VALID_GRADES))}",
                    "VALIDATION_ERROR")
        grades = {
            "team": Grade.parse(grade_team),
            "tech": Grade.parse(grade_tech),
            "opportunity": Grade.parse(grade_opportunity),
        }
    else:
        # Non-standard dims, but no dimension_grades provided — try grade_ params
        # as positional mapping to configured dimensions
        positional = [grade_team, grade_tech, grade_opportunity]
        for i, dim in enumerate(dims):
            raw = positional[i] if i < len(positional) else ""
            if not raw:
                return _error(
                    f"Missing grade for dimension '{dim}'. Use dimension_grades={{'{dim}': 'grade'}} "
                    f"or provide grades positionally.",
                    "VALIDATION_ERROR")
            normalized = raw.strip().upper().replace(" ", "")
            if normalized not in VALID_GRADES:
                return _error(f"Invalid grade for '{dim}': {raw!r}.", "VALIDATION_ERROR")
            grades[dim] = Grade.parse(raw)

    # Validate classification (relaxed for custom types)
    if classification:
        classification = classification.strip().lower()
        valid_cls = valid_classifications(et)
        # For custom types, accept any classification
        if is_standard and classification not in valid_cls:
            return _error(
                f"Invalid classification: {classification!r}. Valid: {', '.join(sorted(valid_cls))}",
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

        session.execute(delete(OutreachScore).where(
            OutreachScore.initiative_id == init.id,
            OutreachScore.project_id.is_(None),
        ))
        session.add(outreach)
        session.commit()

        result: dict = {
            "initiative_id": init.id, "initiative_name": init.name,
            "verdict": outreach.verdict, "score": outreach.score,
            "classification": outreach.classification,
        }
        # Include dimension grades — use dimension_grades_json for full picture
        dim_grades_stored = json_parse(outreach.dimension_grades_json, {})
        if dim_grades_stored:
            result["dimension_grades"] = {
                k: v.get("letter", "") for k, v in dim_grades_stored.items()
            }
        else:
            result.update({
                "grade_team": outreach.grade_team, "grade_tech": outreach.grade_tech,
                "grade_opportunity": outreach.grade_opportunity,
            })
        result["_db"] = _db_pulse(session)
        return result


# ---------------------------------------------------------------------------
# Tools: Batch Operations
# ---------------------------------------------------------------------------


async def batch_enrich(initiative_ids: str | None = None, limit: int = 20) -> dict:
    """Enrich multiple entities (internal, used by process_queue and tests)."""
    limit = max(1, min(limit, 50))
    ids = _parse_ids(initiative_ids)

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


async def batch_score(initiative_ids: str | None = None, limit: int = 20) -> dict:
    """Score multiple entities (internal, used by process_queue and tests)."""
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

    results = await _run_batch(ids, _do_score, concurrency=1, client=client, entity_type=et)
    ok, failed = _batch_summary(results)
    verdict_counts: dict[str, int] = {}
    for r in results:
        if r.get("ok") and "verdict" in r:
            v = r["verdict"]
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

    return {"processed": len(ids), "succeeded": ok, "failed": failed,
            "results": results, "summary": verdict_counts}


@mcp.tool(annotations=_WRITE)
async def process_queue(
    limit: int = 20, discover: bool = False, enrich: bool = True, score: bool = True,
    initiative_ids: str | None = None,
) -> dict:
    """Autonomous pipeline: enrich then score. The primary tool for batch processing.

    WHAT: Fetches work queue (or uses provided IDs), enriches, then scores. One call per batch.
    Auto-degrades to enrich-only when no API key is set (instead of failing).
    WHEN: Primary autonomous workflow. Call repeatedly until remaining_in_queue=0.

    Args:
        limit: Max items (1-50, default 20).
        discover: Run DuckDuckGo URL discovery before enrichment (adds ~12s/item).
        enrich: Run enrichment step (default true).
        score: Run scoring step (default true). Requires API key.
        initiative_ids: Comma-separated IDs. If omitted, auto-selects from work queue.
    """
    api_key_warning = None
    if score:
        key_err = _check_api_key()
        if key_err:
            if not enrich:
                # Can't enrich (disabled) and can't score (no key) — fail
                return key_err
            # Graceful degradation: enrich-only mode
            score = False
            api_key_warning = (
                "No API key — enriching only. "
                "Set API key or use get_scoring_dossier() + submit_score() to score manually."
            )

    limit = max(1, min(limit, 50))
    et = get_entity_type()
    explicit_ids = _parse_ids(initiative_ids)

    with session_scope() as session:
        if explicit_ids is not None:
            queue = [{"id": i, "needs_enrichment": enrich, "needs_scoring": score}
                     for i in explicit_ids[:limit]]
        else:
            queue = services.get_work_queue(session, limit)
        stats = services.compute_stats(session)

    if not queue:
        return _suggest(
            {"enrichment": None, "scoring": None, "remaining_in_queue": 0,
             "hint": f"Work queue is empty. All {_entity_cfg()['label_plural']} are processed."},
            _next("list_initiatives", "Review results", verdict="reach_out_now"),
        )

    enrich_ids = [item["id"] for item in queue if item["needs_enrichment"]]
    score_only_ids = [item["id"] for item in queue
                      if item.get("needs_scoring") and not item.get("needs_enrichment")]

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
            discover_result = {"skipped": True, "reason": "ddgs not installed — pip install 'scout[crawl]'"}

    # Step 1: Enrich
    if enrich and enrich_ids:
        async with open_crawler() as crawler:
            enrich_results = await _run_batch(enrich_ids, _do_enrich, concurrency=3, crawler=crawler)

        enrich_ok, enrich_failed = _batch_summary(enrich_results)
        enrich_result = {"processed": len(enrich_ids), "succeeded": enrich_ok, "failed": enrich_failed}
        enrich_failures = [r for r in enrich_results if not r.get("ok")]
        if enrich_failures:
            enrich_result["failed_items"] = enrich_failures
    else:
        enrich_failures = []

    failed_ids = {f["id"] for f in enrich_failures}
    score_ids = (score_only_ids + [i for i in enrich_ids if i not in failed_ids]) if enrich else score_only_ids

    # Step 2: Score
    if score and score_ids:
        client = LLMClient()

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
    progress_pct = round(100 * (1 - remaining / stats["total"]), 1) if stats["total"] else 100.0
    result: dict = {"discovery": discover_result, "enrichment": enrich_result,
                    "scoring": score_result, "remaining_in_queue": remaining,
                    "progress_pct": progress_pct}

    if api_key_warning:
        result["warning"] = api_key_warning

    if not score:
        return _suggest(result, _next("process_queue", "Score enriched items", score=True, enrich=False))
    elif remaining > 0:
        return _suggest(result, _next("process_queue", "Process next batch"))
    else:
        return _suggest(result,
                        _next("list_initiatives", "Review top results", verdict="reach_out_now"))


# ---------------------------------------------------------------------------
# Tools: Work Queue & Overview
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def get_work_queue(limit: int = 10) -> dict:
    """Get prioritized entities needing enrichment or scoring.

    WHAT: Returns items ordered by priority with recommended_action per item.
    WHEN: To drive autonomous workflows. Or use process_queue() to auto-process.

    Args:
        limit: Max items (1-100, default 10).
    """
    with session_scope() as session:
        queue = services.get_work_queue(session, limit)
        stats = services.compute_stats(session)
        result = {"queue": queue, "database_stats": stats}
        if queue:
            ids = ",".join(str(q["id"]) for q in queue)
            return _suggest(result, _next("process_queue", "Process these items", initiative_ids=ids))
        return _suggest(result, _next("list_initiatives", "All items processed"))


@mcp.tool(annotations=_READ)
def get_overview(detail: bool = False) -> dict:
    """Database statistics and analytical aggregations.

    WHAT: Counts (total, enriched, scored) + breakdowns by verdict, classification, uni.
    WHEN: First call to understand database state. Set detail=True for deeper analytics.

    Args:
        detail: Include score distributions, top-N per verdict, grade breakdowns.
    """
    with session_scope() as session:
        stats = services.compute_stats(session)
        if detail:
            stats["aggregations"] = services.compute_aggregations(session)
        actions = []
        if stats.get("total", 0) > stats.get("scored", 0):
            actions.append(_next("get_work_queue", "Items need processing"))
        return _suggest(stats, *actions)


# ---------------------------------------------------------------------------
# Tools: Similarity
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
def find_similar(
    query: str | None = None, initiative_id: int | None = None,
    uni: str | None = None, verdict: str | None = None,
    limit: int = 10,
) -> dict:
    """Semantic similarity search using dense embeddings.

    WHAT: Returns ranked results with similarity scores. Supports SQL pre-filters.
    WHEN: Find related entities, thematic clusters, or "show me entities like X".

    Args:
        query: Free-text query (e.g. "robotics research"). Either query or initiative_id required.
        initiative_id: Find entities similar to this one.
        uni: Pre-filter by university (comma-separated).
        verdict: Pre-filter by verdict (comma-separated).
        limit: Max results (default 10, max 100).
    """
    from scout.embedder import find_similar as _find_similar

    with session_scope() as session:
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
                return {"results": [], "hint": f"No {_entity_cfg()['label_plural']} match the filters."}

        results = _find_similar(
            query_text=query, initiative_id=initiative_id,
            top_k=max(1, min(limit, 100)), id_mask=id_mask,
        )

        if not results:
            return _suggest(
                {"results": []},
                _next("manage_settings", "Build embeddings first", action="rebuild_embeddings"),
            )

        ids = [r[0] for r in results]
        inits = session.execute(
            select(Initiative.id, Initiative.name, Initiative.uni)
            .where(Initiative.id.in_(ids))
        ).all()
        name_map = {r.id: (r.name, r.uni) for r in inits}

        return {"results": [
            {"id": rid, "name": name_map.get(rid, ("?", "?"))[0],
             "uni": name_map.get(rid, ("?", "?"))[1], "similarity": score_val}
            for rid, score_val in results
        ]}


# ---------------------------------------------------------------------------
# Tools: Manage Project
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
async def manage_project(
    action: str,
    project_id: int | None = None,
    initiative_id: int | None = None,
    name: str | None = None,
    updates: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Create, update, delete, or score projects under an entity.

    ACTIONS:
    - create: Requires initiative_id, name. Optional updates={description, website, github_url, team}.
    - update: Requires project_id. Pass updates={field: value}.
    - delete: Requires project_id + confirm=True.
    - score: Requires project_id. Runs LLM scoring. Requires API key.

    Args:
        action: "create", "update", "delete", or "score".
        project_id: Project ID (required for update/delete/score).
        initiative_id: Parent entity ID (required for create).
        name: Project name (required for create).
        updates: Dict of field->value. Valid: description, website, github_url, team.
        confirm: Must be True for delete.
    """
    action = (action or "").strip().lower()

    if action == "create":
        if initiative_id is None or not name:
            return _error("initiative_id and name required for create", "VALIDATION_ERROR")
        with session_scope() as session:
            _, err = _get_or_error(session, Initiative, initiative_id)
            if err:
                return err
            proj = services.create_project(
                session, initiative_id, name=name, **(updates or {}),
            )
            session.commit()
            return _suggest(
                services.project_summary(proj),
                _next("manage_project", "Score the project", action="score", project_id=proj.id),
            )

    if action == "update":
        if project_id is None:
            return _error("project_id required for update", "VALIDATION_ERROR")
        with session_scope() as session:
            proj, err = _get_or_error(session, Project, project_id)
            if err:
                return err
            if updates:
                services.apply_updates(proj, updates,
                                       ("name", "description", "website", "github_url", "team"))
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
                return {
                    "ok": False, "action": "delete_project",
                    "project_id": proj.id, "project_name": proj.name,
                    "initiative_id": proj.initiative_id,
                    "warning": f"Will permanently delete project '{proj.name}'. "
                               "Call again with confirm=True.",
                }
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

    return _error(f"Unknown action: {action!r}. Use create, update, delete, or score.",
                  "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tools: Manage Database
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
def manage_database(
    action: str,
    name: str | None = None,
    entity_type: str = "initiative",
    context: str = "",
    dimensions: str = "",
) -> dict:
    """List, select, create, delete, or backup Scout databases.

    ACTIONS:
    - list: Show all databases and which is active.
    - select: Switch to a database (creates if needed). Requires name.
    - create: Create a new empty database. Requires name. Supports ANY entity type.
    - delete: Delete a database. Requires name. Cannot delete the active database.
    - backup: Create a timestamped backup copy. Requires name.

    Args:
        action: "list", "select", "create", "delete", or "backup".
        name: Database name (letters, numbers, hyphens, underscores). Required for select/create/delete/backup.
        entity_type: For create: any entity type string (e.g. "initiative", "professor",
            "company", "patent", "research_paper"). Default "initiative".
        context: For create with custom entity types: description of the context
            (e.g. "US patent applications", "Berlin startups"). Used in scoring prompts.
        dimensions: For create with custom entity types: comma-separated scoring dimensions
            (e.g. "novelty,commercial_potential,prior_art"). Defaults to "team,tech,opportunity".
    """
    action = (action or "").strip().lower()

    if action == "list":
        return {"databases": list_databases(), "current": current_db_name()}

    if action == "select":
        if not name:
            return _error("name required for select", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        switch_db(name)
        et = get_entity_type()
        mcp._mcp_server.instructions = _build_instructions(et)
        return {"current": current_db_name(), "entity_type": et}

    if action == "create":
        if not name:
            return _error("name required for create", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        try:
            create_database(name, entity_type=entity_type)
        except ValueError as exc:
            return _error(str(exc), "ALREADY_EXISTS")
        # Store custom entity config for non-built-in types
        if entity_type not in ENTITY_CONFIG:
            from scout.db import set_entity_config_json
            custom_cfg = {
                "label": entity_type.replace("_", " "),
                "label_plural": entity_type.replace("_", " ") + "s",
                "context": context or entity_type.replace("_", " "),
            }
            if dimensions:
                custom_cfg["dimensions"] = [d.strip() for d in dimensions.split(",") if d.strip()]
            set_entity_config_json(custom_cfg)
            # Seed scoring prompts for custom dimensions
            _seed_custom_prompts(entity_type, custom_cfg)
        mcp._mcp_server.instructions = _build_instructions(entity_type)
        return {"current": current_db_name(), "entity_type": entity_type,
                "message": f"Created and switched to '{name}'"}

    if action == "delete":
        if not name:
            return _error("name required for delete", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
            delete_database(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "deleted": name, "current": current_db_name()}

    if action == "backup":
        if not name:
            return _error("name required for backup", "VALIDATION_ERROR")
        try:
            name = validate_db_name(name)
            backup_name = backup_database(name)
        except ValueError as exc:
            return _error(str(exc), "VALIDATION_ERROR")
        return {"ok": True, "backup": backup_name}

    return _error(f"Unknown action: {action!r}. Use list, select, create, delete, or backup.",
                  "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Tools: Manage Settings
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_DESTRUCTIVE)
async def manage_settings(
    action: str,
    # Column params
    column_id: int | None = None, key: str | None = None, label: str | None = None,
    col_type: str = "text", show_in_list: bool = True, sort_order: int = 0,
    # Prompt params
    content: str | None = None,
    # Export params
    verdict: str | None = None, uni: str | None = None,
    include_enrichments: bool = True, include_scores: bool = True, include_extras: bool = False,
    # Scraper params
    school: str | None = None, limit: int = 50,
    # Prompt list param
    compact: bool = False,
    # LLM config params
    provider: str | None = None, model: str | None = None,
    api_key: str | None = None, base_url: str | None = None,
) -> dict | list:
    """Admin operations: custom columns, scoring prompts, LLM config, export, embeddings, TUM scraper.

    ACTIONS:
    - list_columns: Show custom column definitions.
    - create_column: Create column. Requires key, label.
    - update_column: Update column. Requires column_id.
    - delete_column: Delete column. Requires column_id.
    - list_prompts: Show scoring prompt definitions.
    - update_prompt: Update prompt. Requires key ("team"/"tech"/"opportunity"), content.
    - configure_llm: Set LLM provider/model/api_key at runtime. All params optional.
    - show_llm_config: Show current LLM configuration (keys masked).
    - export: Export to XLSX. Optional verdict, uni filters.
    - rebuild_embeddings: Rebuild all dense embeddings.
    - scrape_tum: Scrape TUM professor directory. Optional school filter, limit.

    Args:
        action: The operation to perform (see above).
        column_id: Custom column ID (for update_column/delete_column).
        key: Column key or prompt key.
        label: Column display label.
        col_type: Column type: text, number, boolean, url.
        show_in_list: Show column in list view.
        sort_order: Column display order.
        content: New prompt content (for update_prompt).
        verdict: Export filter (comma-separated).
        uni: Export filter (comma-separated).
        include_enrichments: Include enrichments in export.
        include_scores: Include scores in export.
        include_extras: Include extra fields in export.
        school: TUM school filter (CIT, ED, LS, MGT, MED, NAT).
        limit: Scraper limit.
        compact: For list_prompts, return only key/label/updated_at.
        provider: LLM provider (anthropic, openai, openai_compatible, gemini).
        model: LLM model name.
        api_key: API key for the provider.
        base_url: Custom base URL (for openai_compatible).
    """
    action = (action or "").strip().lower()

    if action == "show_llm_config":
        p = os.environ.get("LLM_PROVIDER", "anthropic")
        m = os.environ.get("LLM_MODEL", "")
        has_key = bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        return {"provider": p, "model": m or "(default)", "api_key_set": has_key,
                "base_url": os.environ.get("OPENAI_BASE_URL", "")}

    if action == "configure_llm":
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

    if action == "list_columns":
        with session_scope() as session:
            return services.get_custom_columns(session, database=current_db_name())

    if action == "create_column":
        if not key or not label:
            return _error("key and label required", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.create_custom_column(
                session, key=key, label=label, col_type=col_type,
                show_in_list=show_in_list, sort_order=sort_order,
                database=current_db_name(),
            )
            if result is None:
                return _error(f"Column key '{key}' already exists", "ALREADY_EXISTS")
            session.commit()
            return result

    if action == "update_column":
        if column_id is None:
            return _error("column_id required", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.update_custom_column(
                session, column_id, label=label, col_type=col_type,
                show_in_list=show_in_list, sort_order=sort_order,
            )
            if result is None:
                return _error(f"Custom column {column_id} not found", "NOT_FOUND")
            session.commit()
            return result

    if action == "delete_column":
        if column_id is None:
            return _error("column_id required", "VALIDATION_ERROR")
        with session_scope() as session:
            if not services.delete_custom_column(session, column_id):
                return _error(f"Custom column {column_id} not found", "NOT_FOUND")
            session.commit()
            return {"ok": True, "deleted_column_id": column_id}

    # --- Scoring Prompts ---
    if action == "list_prompts":
        with session_scope() as session:
            prompts_list = services.get_scoring_prompts(session)
            if compact:
                return [{"key": p["key"], "label": p["label"], "updated_at": p["updated_at"]}
                        for p in prompts_list]
            return prompts_list

    if action == "update_prompt":
        if not key or not content:
            return _error("key and content required", "VALIDATION_ERROR")
        with session_scope() as session:
            result = services.update_scoring_prompt(session, key, content)
            if result is None:
                return _error(f"Scoring prompt '{key}' not found", "NOT_FOUND")
            session.commit()
            return result

    # --- Export ---
    if action == "export":
        from scout.db import DATA_DIR
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
        return {"ok": True, "file": str(out_path), "filename": filename}

    # --- Embeddings ---
    if action == "rebuild_embeddings":
        from scout.embedder import embed_all
        with session_scope() as session:
            try:
                count = embed_all(session)
            except Exception as exc:
                return _error(f"Embedding failed: {exc}", "EMBEDDING_ERROR")
            return _suggest(
                {"ok": True, "embedded": count},
                _next("find_similar", "Try semantic search", query=""),
            )

    # --- TUM Scraper ---
    if action == "scrape_tum":
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
                    name=prof["name"], uni=prof.get("uni", "TUM"),
                    faculty=prof.get("faculty", ""), website=prof.get("website", ""),
                )
                session.add(init)
                created += 1
            session.commit()

        return _suggest(
            {"created": created, "skipped_duplicates": skipped, "total_found": len(professors)},
            _next("process_queue", "Enrich and score imported professors"),
        )

    return _error(f"Unknown action: {action!r}.", "VALIDATION_ERROR")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the Scout MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
