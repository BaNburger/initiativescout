from __future__ import annotations

from datetime import date
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import typer
from sqlalchemy import select
from rich.box import ROUNDED
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from initiative_tracker.config import get_settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Initiative, InitiativeTier, LLMScore, Person, TalentScore
from initiative_tracker.pipeline.assemble_evidence import assemble_evidence
from initiative_tracker.pipeline.collect_dd_public import collect_dd_public
from initiative_tracker.pipeline.collect_github import collect_github
from initiative_tracker.pipeline.comparative_rank import comparative_rank
from initiative_tracker.pipeline.dd_source_audit import source_audit
from initiative_tracker.pipeline.dossiers import build_initiative_dossiers
from initiative_tracker.pipeline.enrich_websites import enrich_websites
from initiative_tracker.pipeline.export import export_outputs
from initiative_tracker.pipeline.ingest_people import ingest_people
from initiative_tracker.pipeline.import_dd_manual import import_dd_manual
from initiative_tracker.pipeline.llm_score import score_with_llm
from initiative_tracker.pipeline.scrape_directories import scrape_directories
from initiative_tracker.pipeline.seed_markdown import seed_from_markdown
from initiative_tracker.results_view import build_html_dashboard, load_result_payload, open_html, render_cli_results
from initiative_tracker.store import get_json_list, set_initiative_status
from initiative_tracker.utils import from_json

app = typer.Typer(help="Munich student initiatives venture-scout intelligence pipeline")
console = Console()


def _configure_logging(*, verbose: int, json_output: bool) -> None:
    if verbose <= 0:
        level = logging.ERROR
    elif verbose == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    handlers: list[logging.Handler]
    if json_output:
        handlers = [logging.StreamHandler()]
        fmt = "%(levelname)s: %(message)s"
    else:
        handlers = [RichHandler(console=console, show_time=False, show_path=False, markup=True)]
        fmt = "%(message)s"

    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


@app.callback()
def app_callback(
    ctx: typer.Context,
    project_root: str | None = typer.Option(
        None,
        "--project-root",
        help="Repository root containing config/, data/, and markdown sources.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON for scripting."),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="Increase log verbosity."),
) -> None:
    if project_root:
        os.environ["INITIATIVE_TRACKER_HOME"] = str(Path(project_root).expanduser().resolve())
        get_settings.cache_clear()
    ctx.obj = {"json_output": json_output, "verbose": verbose}
    _configure_logging(verbose=verbose, json_output=json_output)


def _wants_json(ctx: typer.Context) -> bool:
    return bool(ctx.obj and ctx.obj.get("json_output"))


def _format_scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "-"
    return str(value)


def _render_table(title: str, rows: list[tuple[str, str]], *, border_style: str = "cyan") -> None:
    table = Table(show_header=True, header_style="bold cyan", box=ROUNDED)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    for metric, value in rows:
        table.add_row(metric, value)
    console.print(Panel(table, title=title, border_style=border_style))


def _print(title: str, payload: dict[str, Any], ctx: typer.Context) -> None:
    if _wants_json(ctx):
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    scalar_rows: list[tuple[str, str]] = []
    nested_rows: list[tuple[str, Any]] = []

    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            scalar_rows.append((key, _format_scalar(value)))
        else:
            nested_rows.append((key, value))

    if scalar_rows:
        _render_table(title, scalar_rows)
    else:
        console.print(Panel(f"[bold]{title}[/bold]", border_style="cyan"))

    for key, value in nested_rows:
        if isinstance(value, dict):
            _render_table(
                f"{title} · {key}",
                [(nested_key, _format_scalar(nested_value)) for nested_key, nested_value in value.items()],
                border_style="magenta",
            )
        elif isinstance(value, list):
            preview = value[:3]
            _render_table(
                f"{title} · {key}",
                [
                    ("items", str(len(value))),
                    ("preview", json.dumps(preview, ensure_ascii=False)),
                ],
                border_style="yellow",
            )
        else:
            _render_table(f"{title} · {key}", [("value", _format_scalar(value))], border_style="yellow")


def _run_stage(
    ctx: typer.Context,
    stage_name: str,
    runner: Callable[[], dict[str, Any] | str],
) -> tuple[dict[str, Any] | str, float]:
    started = time.perf_counter()
    if _wants_json(ctx):
        result = runner()
        return result, time.perf_counter() - started

    with console.status(f"[bold cyan]{stage_name}[/bold cyan]", spinner="dots"):
        result = runner()
    elapsed = time.perf_counter() - started
    console.print(f"[green]✓[/green] {stage_name} ({elapsed:.2f}s)")
    return result, elapsed


def _extract_highlights(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return payload

    priority_keys = [
        "files_processed",
        "records_parsed",
        "records_upserted",
        "successful",
        "failed",
        "scores_written",
        "team_items",
        "technology_items",
        "market_items",
        "initiatives_exported",
        "top_n",
    ]
    values: list[str] = []
    for key in priority_keys:
        if key in payload:
            values.append(f"{key}={payload[key]}")
    return ", ".join(values) if values else "ok"


def _cleanup_paths(paths: list[Path]) -> dict[str, int]:
    removed_files = 0
    removed_dirs = 0
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
            removed_dirs += 1
        else:
            path.unlink()
            removed_files += 1
    return {"removed_files": removed_files, "removed_dirs": removed_dirs}


def _resolve_initiative_id(*, db_url: str | None, initiative_id: int | None, initiative_name: str | None) -> int:
    if initiative_id is not None:
        return initiative_id
    if not initiative_name:
        raise typer.BadParameter("Provide either --initiative-id or --initiative-name")
    with session_scope(db_url) as session:
        row = session.execute(
            select(Initiative).where(Initiative.canonical_name.ilike(f"%{initiative_name.strip()}%"))
        ).scalars().first()
        if row is None:
            raise typer.BadParameter(f"No initiative found matching '{initiative_name}'")
        return int(row.id)


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # noqa: B904
        raise typer.BadParameter("Date must be YYYY-MM-DD") from exc


def _redact_channel(channel: str) -> str:
    if "@" in channel:
        name, _, domain = channel.partition("@")
        return f"{name[:2]}***@{domain}"
    return channel



@app.command("init-db")
def init_db_command(
    ctx: typer.Context,
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    init_db(db_url)
    _print("init-db", {"status": "ok", "database_url_override": db_url}, ctx)


@app.command("seed-from-markdown")
def seed_command(
    ctx: typer.Context,
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = seed_from_markdown(get_settings(), db_url)
    _print("seed-from-markdown", details, ctx)


@app.command("scrape-directories")
def scrape_command(
    ctx: typer.Context,
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = scrape_directories(get_settings(), db_url)
    _print("scrape-directories", details, ctx)


@app.command("enrich-websites")
def enrich_command(
    ctx: typer.Context,
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = enrich_websites(get_settings(), db_url)
    _print("enrich-websites", details, ctx)


@app.command("ingest-people")
def ingest_people_command(
    ctx: typer.Context,
    crawl_mode: str = typer.Option("safe", help="Crawl mode: safe or max-reach."),
    max_pages: int = typer.Option(12, help="Max pages per initiative website."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    mode = crawl_mode.strip().lower()
    if mode not in {"safe", "max-reach"}:
        raise typer.BadParameter("crawl-mode must be 'safe' or 'max-reach'")
    details = ingest_people(crawl_mode=mode, max_pages=max_pages, settings=get_settings(), db_url=db_url)
    _print("ingest-people", details, ctx)


@app.command("collect-github")
def collect_github_command(
    ctx: typer.Context,
    initiative_id: int | None = typer.Option(None, help="Optional initiative ID."),
    all: bool = typer.Option(False, "--all", help="Collect GitHub facts for all initiatives."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    if initiative_id is None and not all:
        raise typer.BadParameter("Provide --initiative-id or --all")
    details = collect_github(
        initiative_id=initiative_id,
        all_initiatives=all,
        settings=get_settings(),
        db_url=db_url,
    )
    _print("collect-github", details, ctx)


@app.command("collect-dd-public")
def collect_dd_public_command(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Collect DD public facts for all initiatives."),
    sources: str = typer.Option(
        "github,openalex,semantic_scholar,huggingface",
        help="Comma-separated source keys: github,openalex,semantic_scholar,huggingface,linkedin_safe,researchgate_safe.",
    ),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = collect_dd_public(
        all_initiatives=all,
        sources=sources,
        settings=get_settings(),
        db_url=db_url,
    )
    _print("collect-dd-public", details, ctx)


@app.command("import-dd-manual")
def import_dd_manual_command(
    ctx: typer.Context,
    file: str = typer.Option(..., "--file", help="CSV or JSON file with manual DD facts."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = import_dd_manual(file_path=file, db_url=db_url)
    _print("import-dd-manual", details, ctx)


@app.command("assemble-evidence")
def assemble_evidence_command(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Assemble evidence dossiers for all initiatives."),
    initiative_id: int | None = typer.Option(None, help="Optional initiative ID."),
    force: bool = typer.Option(False, help="Force reassembly even if dossier hash unchanged."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    if not all and initiative_id is None:
        raise typer.BadParameter("Provide --all or --initiative-id")
    ids = [initiative_id] if initiative_id is not None else None
    count = assemble_evidence(db_url=db_url, initiative_ids=ids, force=force)
    _print("assemble-evidence", {"assembled": count}, ctx)


@app.command("llm-score")
def llm_score_command(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Score all initiatives via LLM."),
    initiative_id: int | None = typer.Option(None, help="Optional initiative ID."),
    force: bool = typer.Option(False, help="Force re-scoring even if already scored."),
    provider: str | None = typer.Option(None, help="LLM provider: anthropic or openai."),
    model: str | None = typer.Option(None, help="LLM model name override."),
    delay: float = typer.Option(0.5, help="Delay between API calls in seconds."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    if not all and initiative_id is None:
        raise typer.BadParameter("Provide --all or --initiative-id")
    ids = [initiative_id] if initiative_id is not None else None
    config: dict[str, Any] = {}
    if provider:
        config["provider"] = provider
    if model:
        config["model"] = model
    count = score_with_llm(db_url=db_url, config=config, initiative_ids=ids, force=force, delay_seconds=delay)
    _print("llm-score", {"scored": count}, ctx)


@app.command("comparative-rank")
def comparative_rank_command(
    ctx: typer.Context,
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    count = comparative_rank(db_url=db_url)
    _print("comparative-rank", {"ranked": count}, ctx)


@app.command("explain")
def explain_command(
    ctx: typer.Context,
    initiative_id: int | None = typer.Option(None, help="Initiative ID."),
    initiative_name: str | None = typer.Option(None, help="Initiative name (fuzzy contains match)."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    resolved_id = _resolve_initiative_id(db_url=db_url, initiative_id=initiative_id, initiative_name=initiative_name)
    with session_scope(db_url) as session:
        initiative = session.execute(select(Initiative).where(Initiative.id == resolved_id)).scalars().first()
        if initiative is None:
            raise typer.BadParameter(f"Initiative id={resolved_id} not found")

        llm_score = (
            session.execute(
                select(LLMScore).where(LLMScore.initiative_id == resolved_id).order_by(LLMScore.scored_at.desc())
            )
            .scalars()
            .first()
        )
        tier = (
            session.execute(
                select(InitiativeTier).where(InitiativeTier.initiative_id == resolved_id).order_by(InitiativeTier.assigned_at.desc())
            )
            .scalars()
            .first()
        )

    if llm_score is None:
        raise typer.BadParameter(
            f"No LLM score found for initiative '{initiative.canonical_name}'. "
            "Run `initiative-tracker assemble-evidence --all && initiative-tracker llm-score --all` first."
        )

    dim_details = from_json(llm_score.dimension_details_json, {})
    data_gaps = from_json(llm_score.data_gaps_json, [])
    dim_percentiles = from_json(tier.dimension_percentiles_json, {}) if tier else {}

    payload: dict[str, Any] = {
        "initiative_id": resolved_id,
        "initiative_name": initiative.canonical_name,
        "university": initiative.university,
        "classification": llm_score.classification,
        "tier": tier.tier if tier else "?",
        "tier_rationale": tier.tier_rationale if tier else "",
        "composite_score": round(llm_score.composite_score, 4),
        "composite_confidence": round(llm_score.composite_confidence, 4),
        "composite_percentile": round(tier.composite_percentile, 1) if tier else None,
        "summary": llm_score.initiative_summary,
        "overall_assessment": llm_score.overall_assessment,
        "recommended_action": llm_score.recommended_action,
        "engagement_hook": llm_score.engagement_hook,
        "dimensions": {},
        "data_gaps": data_gaps,
        "llm_model": llm_score.llm_model,
        "scored_at": llm_score.scored_at.isoformat(),
    }

    dim_attrs = [
        ("technical_substance", "confidence_technical"),
        ("team_capability", "confidence_team"),
        ("problem_market_clarity", "confidence_market"),
        ("traction_momentum", "confidence_traction"),
        ("reachability", "confidence_reachability"),
        ("investability_signal", "confidence_investability"),
    ]
    for dim_key, conf_attr in dim_attrs:
        detail = dim_details.get(dim_key, {})
        payload["dimensions"][dim_key] = {
            "score": round(getattr(llm_score, dim_key, 0.0), 2),
            "confidence": round(getattr(llm_score, conf_attr, 0.0), 2),
            "percentile": dim_percentiles.get(dim_key),
            "reasoning": detail.get("reasoning", ""),
            "key_evidence": detail.get("key_evidence", []),
            "data_gaps": detail.get("data_gaps", []),
        }

    if tier:
        payload["tier_change"] = tier.tier_change
        payload["tier_change_reason"] = tier.tier_change_reason

    if _wants_json(ctx):
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Summary table
    _render_table(
        f"explain · {initiative.canonical_name}",
        [
            ("Classification", llm_score.classification),
            ("Tier", f"{tier.tier if tier else '?'} — {tier.tier_rationale if tier else ''}"),
            ("Composite Score", f"{llm_score.composite_score:.2f}"),
            ("Composite Confidence", f"{llm_score.composite_confidence:.2f}"),
            ("Percentile", f"{tier.composite_percentile:.1f}" if tier else "-"),
            ("Action", llm_score.recommended_action),
            ("Summary", llm_score.initiative_summary),
        ],
        border_style="cyan",
    )

    # Dimension scores table
    table = Table(show_header=True, header_style="bold green", box=ROUNDED)
    table.add_column("Dimension", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Pctl", justify="right")
    table.add_column("Reasoning")
    for dim_key, _ in dim_attrs:
        d = payload["dimensions"][dim_key]
        table.add_row(
            dim_key,
            _format_scalar(d["score"]),
            _format_scalar(d["confidence"]),
            f"{d['percentile']:.0f}" if d.get("percentile") is not None else "-",
            d["reasoning"][:80] + "..." if len(d["reasoning"]) > 80 else d["reasoning"],
        )
    console.print(Panel(table, title="LLM Score Dimensions", border_style="green"))

    # Assessment
    if llm_score.overall_assessment:
        console.print(Panel(llm_score.overall_assessment, title="Assessment", border_style="magenta"))
    if llm_score.engagement_hook:
        console.print(Panel(llm_score.engagement_hook, title="Engagement Hook", border_style="yellow"))


@app.command("shortlist")
def shortlist_command(
    ctx: typer.Context,
    tier: str = typer.Option("S,A", help="Comma-separated tiers to include (e.g. S,A,B)."),
    top_n: int = typer.Option(15, help="Number of initiatives to return."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    allowed_tiers = {t.strip().upper() for t in tier.split(",")}

    with session_scope(db_url) as session:
        initiatives = {row.id: row for row in session.execute(select(Initiative)).scalars().all()}
        tiers = session.execute(select(InitiativeTier)).scalars().all()
        scores = session.execute(select(LLMScore)).scalars().all()

    latest_tiers: dict[int, InitiativeTier] = {}
    for t in sorted(tiers, key=lambda x: x.assigned_at, reverse=True):
        latest_tiers.setdefault(t.initiative_id, t)

    latest_scores: dict[int, LLMScore] = {}
    for s in sorted(scores, key=lambda x: x.scored_at, reverse=True):
        latest_scores.setdefault(s.initiative_id, s)

    filtered = [
        (init_id, t) for init_id, t in latest_tiers.items()
        if t.tier in allowed_tiers
    ]
    filtered.sort(key=lambda x: x[1].composite_percentile, reverse=True)
    filtered = filtered[:top_n]

    payload = []
    for rank_idx, (init_id, tier_obj) in enumerate(filtered, start=1):
        initiative = initiatives.get(init_id)
        score = latest_scores.get(init_id)
        if not initiative or not score:
            continue
        payload.append({
            "rank": rank_idx,
            "initiative_id": init_id,
            "initiative_name": initiative.canonical_name,
            "university": initiative.university,
            "tier": tier_obj.tier,
            "composite_score": round(score.composite_score, 2),
            "classification": score.classification,
            "recommended_action": score.recommended_action,
            "percentile": round(tier_obj.composite_percentile, 1),
            "summary": score.initiative_summary,
        })

    if _wants_json(ctx):
        typer.echo(json.dumps({"tiers": list(allowed_tiers), "items": payload}, indent=2, ensure_ascii=False))
        return

    table = Table(show_header=True, header_style="bold yellow", box=ROUNDED)
    table.add_column("#", style="dim")
    table.add_column("Initiative", style="bold")
    table.add_column("Univ")
    table.add_column("Tier", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Pctl", justify="right")
    table.add_column("Class")
    table.add_column("Action")
    for row in payload:
        table.add_row(
            str(row["rank"]),
            row["initiative_name"],
            row["university"] or "-",
            row["tier"],
            _format_scalar(row["composite_score"]),
            f"{row['percentile']:.0f}",
            row["classification"],
            row["recommended_action"],
        )
    console.print(Panel(table, title=f"shortlist · tiers {','.join(sorted(allowed_tiers))}", border_style="yellow"))


@app.command("dd-source-audit")
def dd_source_audit_command(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Audit DD source coverage for all initiatives."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = source_audit(all_initiatives=all, settings=get_settings(), db_url=db_url)
    _print("dd-source-audit", details, ctx)


@app.command("export")
def export_command(
    ctx: typer.Context,
    top_n: int = typer.Option(default=15, help="Number of items to include in each exported ranking"),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    details = export_outputs(top_n=top_n, settings=get_settings(), db_url=db_url)
    _print("export", details, ctx)


@app.command("doctor")
def doctor_command(ctx: typer.Context) -> None:
    settings = get_settings()
    checks = {
        "database_exists": settings.database_path.exists(),
        "technology_taxonomy_exists": settings.technology_taxonomy_file.exists(),
        "market_taxonomy_exists": settings.market_taxonomy_file.exists(),
        "technology_aliases_exists": settings.technology_aliases_file.exists(),
        "market_aliases_exists": settings.market_aliases_file.exists(),
        "llm_scoring_config_exists": settings.llm_scoring_config_file.exists(),
        "tier_thresholds_exists": settings.tier_thresholds_file.exists(),
        "seed_file_1_exists": settings.seed_markdown_files[0].exists(),
        "seed_file_2_exists": settings.seed_markdown_files[1].exists(),
        "exports_dir_exists": settings.exports_dir.exists(),
    }
    _print("doctor", checks, ctx)


@app.command("explain")
def explain_command(
    ctx: typer.Context,
    initiative_id: int | None = typer.Option(None, help="Initiative ID."),
    initiative_name: str | None = typer.Option(None, help="Initiative name (fuzzy contains match)."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    resolved_id = _resolve_initiative_id(db_url=db_url, initiative_id=initiative_id, initiative_name=initiative_name)
    with session_scope(db_url) as session:
        initiative = session.execute(select(Initiative).where(Initiative.id == resolved_id)).scalars().first()
        if initiative is None:
            raise typer.BadParameter(f"Initiative id={resolved_id} not found")

        score = (
            session.execute(select(Score).where(Score.initiative_id == resolved_id).order_by(Score.scored_at.desc()))
            .scalars()
            .first()
        )
        components = (
            session.execute(
                select(ScoreComponent).where(ScoreComponent.initiative_id == resolved_id).order_by(ScoreComponent.dimension.asc())
            )
            .scalars()
            .all()
        )
        component_ids = [row.id for row in components]
        evidence_rows = (
            session.execute(select(ScoreEvidence).where(ScoreEvidence.score_component_id.in_(component_ids))).scalars().all()
            if component_ids
            else []
        )
        evidence_by_component: dict[int, list[ScoreEvidence]] = {}
        for row in evidence_rows:
            evidence_by_component.setdefault(row.score_component_id, []).append(row)
        payload = {
            "initiative_id": resolved_id,
            "initiative_name": initiative.canonical_name,
            "scores": {
                "tech_depth": round(score.tech_depth, 4) if score else None,
                "market_opportunity": round(score.market_opportunity, 4) if score else None,
                "team_strength": round(score.team_strength, 4) if score else None,
                "maturity": round(score.maturity, 4) if score else None,
                "actionability_0_6m": round(score.actionability_0_6m, 4) if score else None,
                "support_fit": round(score.support_fit, 4) if score else None,
                "outreach_now_score": round(score.outreach_now_score, 4) if score else None,
                "venture_upside_score": round(score.venture_upside_score, 4) if score else None,
                "legacy_composite": round(score.composite_score, 4) if score else None,
            },
            "components": [
                {
                    "dimension": row.dimension,
                    "component_key": row.component_key,
                    "raw_value": round(row.raw_value, 4),
                    "normalized_value": round(row.normalized_value, 4),
                    "weight": round(row.weight, 4),
                    "weighted_contribution": round(row.weighted_contribution, 4),
                    "confidence": round(row.confidence, 4),
                    "provenance": row.provenance,
                    "evidence_refs": [
                        {
                            "source_url": ev.source_url,
                            "snippet": ev.snippet,
                            "signal_type": ev.signal_type,
                            "signal_key": ev.signal_key,
                            "value": round(ev.value, 4),
                        }
                        for ev in evidence_by_component.get(row.id, [])
                    ],
                }
                for row in components
            ],
        }

    if _wants_json(ctx):
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    score_rows = [(k, _format_scalar(v)) for k, v in payload["scores"].items()]
    _render_table(f"explain · {initiative.canonical_name}", score_rows, border_style="cyan")

    table = Table(show_header=True, header_style="bold green", box=ROUNDED)
    table.add_column("Dimension", style="bold")
    table.add_column("Component")
    table.add_column("Norm", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Contribution", justify="right")
    table.add_column("Evidence", justify="right")
    table.add_column("Prov")
    for row in payload["components"]:
        table.add_row(
            row["dimension"],
            row["component_key"],
            _format_scalar(row["normalized_value"]),
            _format_scalar(row["weight"]),
            _format_scalar(row["weighted_contribution"]),
            str(len(row["evidence_refs"])),
            row["provenance"],
        )
    console.print(Panel(table, title="Score Components", border_style="green"))


@app.command("shortlist")
def shortlist_command(
    ctx: typer.Context,
    lens: str = typer.Option("outreach", help="Lens: outreach or upside."),
    top_n: int = typer.Option(15, help="Number of initiatives to return."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    selected_lens = lens.strip().lower()
    if selected_lens not in {"outreach", "upside"}:
        raise typer.BadParameter("lens must be 'outreach' or 'upside'")

    with session_scope(db_url) as session:
        initiatives = {row.id: row for row in session.execute(select(Initiative)).scalars().all()}
        scores = session.execute(select(Score)).scalars().all()

    latest: dict[int, Score] = {}
    for row in sorted(scores, key=lambda s: s.scored_at, reverse=True):
        latest.setdefault(row.initiative_id, row)

    ordered = sorted(
        latest.values(),
        key=lambda row: row.outreach_now_score if selected_lens == "outreach" else row.venture_upside_score,
        reverse=True,
    )[:top_n]

    payload = []
    for index, row in enumerate(ordered, start=1):
        initiative = initiatives.get(row.initiative_id)
        if not initiative:
            continue
        payload.append(
            {
                "rank": index,
                "initiative_id": initiative.id,
                "initiative_name": initiative.canonical_name,
                "university": initiative.university,
                "outreach_now_score": round(row.outreach_now_score, 4),
                "venture_upside_score": round(row.venture_upside_score, 4),
                "legacy_composite": round(row.composite_score, 4),
                "why": "Prioritize immediate outreach and support-fit" if selected_lens == "outreach" else "Prioritize long-term venture upside",
            }
        )

    if _wants_json(ctx):
        typer.echo(json.dumps({"lens": selected_lens, "items": payload}, indent=2, ensure_ascii=False))
        return

    table = Table(show_header=True, header_style="bold yellow", box=ROUNDED)
    table.add_column("#", style="dim")
    table.add_column("Initiative", style="bold")
    table.add_column("Univ")
    table.add_column("Outreach", justify="right")
    table.add_column("Upside", justify="right")
    table.add_column("Legacy", justify="right")
    for row in payload:
        table.add_row(
            str(row["rank"]),
            row["initiative_name"],
            row["university"] or "-",
            _format_scalar(row["outreach_now_score"]),
            _format_scalar(row["venture_upside_score"]),
            _format_scalar(row["legacy_composite"]),
        )
    console.print(Panel(table, title=f"shortlist · {selected_lens}", border_style="yellow"))


@app.command("talent")
def talent_command(
    ctx: typer.Context,
    type: str = typer.Option("all", "--type", help="Talent type: operators, alumni, or all."),
    top_n: int = typer.Option(25, help="Number of people to include."),
    include_private: bool = typer.Option(False, help="Include private channels (email/phone) in output."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    selected = type.strip().lower()
    if selected not in {"operators", "alumni", "all"}:
        raise typer.BadParameter("--type must be operators, alumni, or all")

    with session_scope(db_url) as session:
        people = {row.id: row for row in session.execute(select(Person)).scalars().all()}
        scores = session.execute(select(TalentScore)).scalars().all()

    latest: dict[int, TalentScore] = {}
    for row in sorted(scores, key=lambda s: s.scored_at, reverse=True):
        latest.setdefault(row.person_id, row)

    filtered = []
    for person_id, score_row in latest.items():
        if selected == "operators" and score_row.talent_type != "operators":
            continue
        if selected == "alumni" and score_row.talent_type != "alumni_angels":
            continue
        person = people.get(person_id)
        if not person:
            continue
        channels = get_json_list(person.contact_channels_json)
        if not include_private:
            channels = [_redact_channel(channel) for channel in channels]
        filtered.append(
            {
                "person_id": person_id,
                "name": person.canonical_name,
                "person_type": person.person_type,
                "talent_type": score_row.talent_type,
                "score": round(score_row.composite_score, 4),
                "confidence": round(score_row.confidence, 4),
                "contactability": round(score_row.reachability, 4),
                "channels": channels[:6],
                "reasons": get_json_list(score_row.reasons_json),
            }
        )

    filtered.sort(key=lambda row: row["score"], reverse=True)
    payload = filtered[:top_n]
    if _wants_json(ctx):
        typer.echo(json.dumps({"type": selected, "items": payload}, indent=2, ensure_ascii=False))
        return

    table = Table(show_header=True, header_style="bold magenta", box=ROUNDED)
    table.add_column("#", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Score", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Contactability", justify="right")
    for idx, row in enumerate(payload, start=1):
        table.add_row(
            str(idx),
            row["name"],
            row["talent_type"],
            _format_scalar(row["score"]),
            _format_scalar(row["confidence"]),
            _format_scalar(row["contactability"]),
        )
    console.print(Panel(table, title=f"talent · {selected}", border_style="magenta"))


@app.command("set-status")
def set_status_command(
    ctx: typer.Context,
    initiative_id: int = typer.Option(..., help="Initiative ID."),
    status: str = typer.Option(..., help="Status: new|priority|contacted|discovery|supporting|deferred"),
    owner: str = typer.Option("", help="Owner name."),
    next_step_date: str | None = typer.Option(None, help="Next step date (YYYY-MM-DD)."),
    note: str = typer.Option("", help="Status note."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    allowed = {"new", "priority", "contacted", "discovery", "supporting", "deferred"}
    normalized = status.strip().lower()
    if normalized not in allowed:
        raise typer.BadParameter(f"status must be one of: {', '.join(sorted(allowed))}")
    parsed_next_date = _parse_optional_date(next_step_date)

    with session_scope(db_url) as session:
        row = set_initiative_status(
            session,
            initiative_id=initiative_id,
            status=normalized,
            owner=owner,
            next_step_date=parsed_next_date,
            note=note,
        )
        payload = {
            "initiative_id": initiative_id,
            "status": row.status,
            "owner": row.owner,
            "last_contact_at": row.last_contact_at.isoformat() if row.last_contact_at else None,
            "next_step_date": row.next_step_date.isoformat() if row.next_step_date else None,
            "notes": row.notes,
        }
    _print("set-status", payload, ctx)


@app.command("view-results")
def view_results_command(
    ctx: typer.Context,
    mode: str = typer.Option("cli", help="Display mode: cli or html."),
    lens: str = typer.Option("outreach", help="Lens: outreach or upside."),
    top_n: int = typer.Option(15, help="Number of rows to display."),
    initiative_id: int | None = typer.Option(None, help="Optional initiative ID for dossier focus."),
    include_private: bool = typer.Option(False, help="Include private contact details in output."),
    refresh: bool = typer.Option(False, help="Refresh exports before viewing."),
    open_browser: bool = typer.Option(False, "--open", help="Open the generated HTML in the default browser."),
    output_html: str | None = typer.Option(
        None, help="Output HTML file path. Defaults to reports/latest/results_dashboard.html."
    ),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL for refresh/export."),
) -> None:
    selected_mode = mode.strip().lower()
    if selected_mode not in {"cli", "html"}:
        raise typer.BadParameter("mode must be either 'cli' or 'html'")
    selected_lens = lens.strip().lower()
    if selected_lens not in {"outreach", "upside"}:
        raise typer.BadParameter("lens must be either 'outreach' or 'upside'")

    settings = get_settings()
    required_exports = [
        settings.exports_dir / "top_technologies.json",
        settings.exports_dir / "top_market_opportunities.json",
        settings.exports_dir / "top_teams.json",
        settings.exports_dir / "top_outreach_targets.json",
        settings.exports_dir / "top_venture_upside.json",
        settings.exports_dir / "top_talent_operators.json",
        settings.exports_dir / "top_talent_alumni_angels.json",
        settings.exports_dir / "initiative_dossiers.json",
        settings.exports_dir / "initiatives_master.json",
        settings.exports_dir / "dd_score_components.json",
        settings.exports_dir / "team_capability_matrix.json",
        settings.exports_dir / "investable_rankings.json",
        settings.exports_dir / "watchlist_rankings.json",
    ]

    if refresh or any(not path.exists() for path in required_exports):
        export_outputs(top_n=top_n, settings=settings, db_url=db_url)

    payload = load_result_payload(
        settings,
        top_n=top_n,
        lens=selected_lens,
        initiative_id=initiative_id,
        include_private=include_private,
    )
    if include_private:
        fresh_dossiers = build_initiative_dossiers(db_url=db_url)
        payload["dossiers"] = fresh_dossiers
        if initiative_id is not None:
            payload["selected_dossier"] = next(
                (row for row in fresh_dossiers if int(row.get("initiative_id", -1)) == initiative_id),
                payload.get("selected_dossier"),
            )
        elif fresh_dossiers and payload.get("selected_dossier") is None:
            payload["selected_dossier"] = fresh_dossiers[0]

    if _wants_json(ctx):
        if selected_mode == "cli":
            compact = dict(payload)
            dossiers = compact.pop("dossiers", [])
            selected = compact.pop("selected_dossier", None)
            compact["dossiers_count"] = len(dossiers)
            compact["selected_initiative_id"] = selected.get("initiative_id") if isinstance(selected, dict) else None
            typer.echo(json.dumps({"mode": "cli", "payload": compact}, indent=2, ensure_ascii=False))
            return
        html_path = Path(output_html).expanduser().resolve() if output_html else settings.reports_dir / "results_dashboard.html"
        generated = build_html_dashboard(payload, html_path, lens=selected_lens, include_private=include_private)
        typer.echo(
            json.dumps(
                {
                    "mode": "html",
                    "lens": selected_lens,
                    "output_html": str(generated),
                    "top_n": top_n,
                    "total_initiatives": payload["total_initiatives"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if selected_mode == "cli":
        render_cli_results(console, payload, lens=selected_lens)
        return

    html_path = Path(output_html).expanduser().resolve() if output_html else settings.reports_dir / "results_dashboard.html"
    generated = build_html_dashboard(payload, html_path, lens=selected_lens, include_private=include_private)
    _render_table(
        "view-results",
        [
            ("mode", "html"),
            ("lens", selected_lens),
            ("output", str(generated)),
            ("top_n", str(top_n)),
            ("total_initiatives", str(payload["total_initiatives"])),
        ],
        border_style="green",
    )
    if open_browser:
        open_html(generated)


@app.command("clean")
def clean_command(
    ctx: typer.Context,
    all_generated: bool = typer.Option(
        False,
        "--all",
        help="Also remove generated database/exports/report artifacts.",
    ),
) -> None:
    root = Path.cwd()

    targets: list[Path] = [
        root / ".pytest_cache",
        root / "initiative_tracker.egg-info",
        root / "data" / "initiatives.db-journal",
    ]

    targets.extend(root.rglob("__pycache__"))
    targets.extend(root.rglob("*.pyc"))

    if all_generated:
        targets.append(root / "data" / "initiatives.db")
        targets.append(root / "reports" / "latest" / "phase1_summary.md")
        targets.append(root / "reports" / "latest" / "venture_scout_brief.md")
        targets.append(root / "reports" / "latest" / "results_dashboard.html")
        targets.extend((root / "data" / "exports").glob("*.json"))
        targets.extend((root / "data" / "exports").glob("*.csv"))

    stats = _cleanup_paths(targets)
    stats["all_generated"] = int(all_generated)
    _print("clean", stats, ctx)


@app.command("run-all")
def run_all_command(
    ctx: typer.Context,
    top_n: int = typer.Option(default=15, help="Number of items to include in each ranking"),
    crawl_mode: str = typer.Option("safe", help="People crawl mode: safe or max-reach."),
    max_pages: int = typer.Option(12, help="Max pages per initiative website for people crawl."),
    sources: str = typer.Option(
        "github,openalex,semantic_scholar,huggingface",
        help="Comma-separated DD source keys.",
    ),
    provider: str | None = typer.Option(None, help="LLM provider: anthropic or openai."),
    model: str | None = typer.Option(None, help="LLM model name override."),
    delay: float = typer.Option(0.5, help="Delay between LLM API calls in seconds."),
    db_url: str | None = typer.Option(default=None, help="Optional SQLAlchemy DB URL"),
) -> None:
    mode = crawl_mode.strip().lower()
    if mode not in {"safe", "max-reach"}:
        raise typer.BadParameter("crawl-mode must be 'safe' or 'max-reach'")

    llm_config: dict[str, Any] = {}
    if provider:
        llm_config["provider"] = provider
    if model:
        llm_config["model"] = model

    stages: list[tuple[str, Callable[[], dict[str, Any] | str]]] = [
        ("init-db", lambda: (init_db(db_url) or "ok")),
        ("seed-from-markdown", lambda: seed_from_markdown(get_settings(), db_url)),
        ("scrape-directories", lambda: scrape_directories(get_settings(), db_url)),
        ("enrich-websites", lambda: enrich_websites(get_settings(), db_url)),
        ("ingest-people", lambda: ingest_people(crawl_mode=mode, max_pages=max_pages, settings=get_settings(), db_url=db_url)),
        ("collect-github", lambda: collect_github(initiative_id=None, all_initiatives=True, settings=get_settings(), db_url=db_url)),
        (
            "collect-dd-public",
            lambda: collect_dd_public(all_initiatives=True, sources=sources, settings=get_settings(), db_url=db_url),
        ),
        ("assemble-evidence", lambda: {"assembled": assemble_evidence(db_url=db_url)}),
        ("llm-score", lambda: {"scored": score_with_llm(db_url=db_url, config=llm_config, delay_seconds=delay)}),
        ("comparative-rank", lambda: {"ranked": comparative_rank(db_url=db_url)}),
        ("export", lambda: export_outputs(top_n=top_n, settings=get_settings(), db_url=db_url)),
    ]

    if not _wants_json(ctx):
        console.print(Panel("[bold cyan]Running full pipeline (LLM scoring)[/bold cyan]", border_style="cyan"))

    result: dict[str, Any] = {}
    summary_rows: list[tuple[str, str, str, str]] = []

    for stage_name, stage_runner in stages:
        try:
            payload, elapsed = _run_stage(ctx, stage_name, stage_runner)
            result_key = stage_name.replace("-", "_")
            result[result_key] = payload
            summary_rows.append((stage_name, "ok", f"{elapsed:.2f}s", _extract_highlights(payload)))
        except Exception as exc:  # noqa: BLE001
            result_key = stage_name.replace("-", "_")
            result[result_key] = {"status": "failed", "error": str(exc)}
            summary_rows.append((stage_name, "failed", "-", str(exc)))
            if _wants_json(ctx):
                typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                summary = Table(show_header=True, header_style="bold red", box=ROUNDED)
                summary.add_column("Stage", style="bold")
                summary.add_column("Status")
                summary.add_column("Time")
                summary.add_column("Highlights")
                for row in summary_rows:
                    summary.add_row(*row)
                console.print(Panel(summary, title="run-all summary", border_style="red"))
            raise typer.Exit(code=1)

    if _wants_json(ctx):
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    summary = Table(show_header=True, header_style="bold green", box=ROUNDED)
    summary.add_column("Stage", style="bold")
    summary.add_column("Status")
    summary.add_column("Time")
    summary.add_column("Highlights")
    for stage, status, elapsed, highlights in summary_rows:
        status_cell = "[green]ok[/green]" if status == "ok" else "[red]failed[/red]"
        summary.add_row(stage, status_cell, elapsed, highlights)

    console.print(Panel(summary, title="run-all summary", border_style="green"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
