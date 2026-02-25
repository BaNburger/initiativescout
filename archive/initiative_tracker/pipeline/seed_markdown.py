from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.store import add_initiative_source, add_raw_observation, add_signal, upsert_initiative
from initiative_tracker.utils import maybe_float, maybe_int, normalize_name, unique_list

HEADING_RE = re.compile(r"^####\s+(?:\d+\.\s+)?(.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.*?)\s*\|\s*$")
UNIVERSITY_RE = re.compile(r"^#{1,3}\s+.*\b(TUM|LMU|HM)\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)\]|]+")
SCORE_INLINE_RE = re.compile(
    r"Tech\s*:\s*(?P<tech>\d(?:\.\d+)?)\s*,\s*Talent\s*:\s*(?P<talent>\d(?:\.\d+)?)\s*,\s*Applicability\s*:\s*(?P<applicability>\d(?:\.\d+)?)\s*,\s*Maturity\s*:\s*(?P<maturity>\d(?:\.\d+)?)",
    re.IGNORECASE,
)


def _extract_first_url(text: str) -> str:
    match = URL_RE.search(text or "")
    return match.group(0).strip() if match else ""


def _split_list(value: str) -> list[str]:
    raw = value.replace(";", ",")
    parts = [segment.strip(" .") for segment in raw.split(",")]
    return unique_list([p for p in parts if p])


def _clean_name(raw: str) -> str:
    cleaned = raw.strip().strip("#").strip()
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    return cleaned


def _extract_ratings(fields: dict[str, str]) -> dict[str, float]:
    ratings: dict[str, float] = {}

    direct_map = {
        "Tech Rating": "tech_depth",
        "Talent Rating": "team_strength",
        "Applicability": "market_opportunity",
        "Maturity": "maturity",
    }
    for field_name, signal_key in direct_map.items():
        value = maybe_float(fields.get(field_name))
        if value is not None:
            ratings[signal_key] = value

    score_field = fields.get("Score") or ""
    match = SCORE_INLINE_RE.search(score_field)
    if match:
        ratings.setdefault("tech_depth", float(match.group("tech")))
        ratings.setdefault("team_strength", float(match.group("talent")))
        ratings.setdefault("market_opportunity", float(match.group("applicability")))
        ratings.setdefault("maturity", float(match.group("maturity")))

    return ratings


def parse_markdown_initiatives(path: Path) -> list[dict[str, Any]]:
    initiatives: list[dict[str, Any]] = []
    current_university = ""
    current_name = ""
    current_fields: dict[str, str] = {}

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    def flush_current() -> None:
        nonlocal current_name, current_fields
        if not current_name:
            return

        fields = {k.strip(): v.strip() for k, v in current_fields.items()}
        primary_url = _extract_first_url(fields.get("Website", ""))
        github_url = _extract_first_url(fields.get("GitHub", ""))
        linkedin_url = _extract_first_url(fields.get("LinkedIn", ""))

        technologies = _split_list(fields.get("Technologies", ""))
        categories = _split_list(fields.get("Category", ""))
        achievements = fields.get("Achievements", "")

        record = {
            "name": _clean_name(current_name),
            "normalized_name": normalize_name(_clean_name(current_name)),
            "university": current_university,
            "description_raw": fields.get("Description", ""),
            "description_summary_en": "",
            "primary_url": primary_url or github_url or linkedin_url,
            "github_url": github_url,
            "linkedin_url": linkedin_url,
            "categories": categories,
            "technologies": technologies,
            "markets": [],
            "team_signals": _split_list(fields.get("Team Size", "")) + _split_list(achievements),
            "achievements": achievements,
            "team_size": maybe_int(fields.get("Team Size")),
            "founded_year": maybe_int(fields.get("Founded")),
            "ratings": _extract_ratings(fields),
            "raw_fields": fields,
        }
        initiatives.append(record)

        current_name = ""
        current_fields = {}

    for line in lines:
        university_match = UNIVERSITY_RE.match(line)
        if university_match:
            current_university = university_match.group(1).upper()

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_current()
            current_name = heading_match.group(1).strip()
            current_fields = {}
            continue

        table_match = TABLE_ROW_RE.match(line)
        if table_match and current_name:
            key = table_match.group(1).strip()
            value = table_match.group(2).strip()
            current_fields[key] = value

    flush_current()

    # De-duplicate by normalized name + url while keeping richest payload.
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in initiatives:
        key = (item["normalized_name"], item["primary_url"] or "")
        existing = dedup.get(key)
        if existing is None:
            dedup[key] = item
            continue
        if len(item.get("description_raw", "")) > len(existing.get("description_raw", "")):
            dedup[key] = item
    return list(dedup.values())


def seed_from_markdown(settings: Settings | None = None, db_url: str | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "files_processed": 0,
        "records_parsed": 0,
        "records_upserted": 0,
        "signals_written": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "seed_from_markdown")
        try:
            for markdown_file in cfg.seed_markdown_files:
                if not markdown_file.exists():
                    continue
                details["files_processed"] += 1
                records = parse_markdown_initiatives(markdown_file)
                details["records_parsed"] += len(records)

                for record in records:
                    initiative = upsert_initiative(
                        session,
                        name=record["name"],
                        university=record.get("university") or "",
                        primary_url=record.get("primary_url") or "",
                        description_raw=record.get("description_raw") or "",
                        description_summary_en=record.get("description_summary_en") or "",
                        categories=record.get("categories") or [],
                        technologies=record.get("technologies") or [],
                        markets=record.get("markets") or [],
                        team_signals=record.get("team_signals") or [],
                        confidence=0.9,
                    )

                    payload = {
                        "file": str(markdown_file),
                        "record": record,
                    }

                    add_initiative_source(
                        session,
                        initiative_id=initiative.id,
                        source_type="seed_markdown",
                        source_name=markdown_file.name,
                        source_url=f"file://{markdown_file}",
                        external_url=record.get("primary_url") or "",
                        payload=payload,
                    )
                    add_raw_observation(
                        session,
                        initiative_id=initiative.id,
                        source_type="seed_markdown",
                        source_name=markdown_file.name,
                        source_url=f"file://{markdown_file}",
                        payload=payload,
                    )

                    for technology in record.get("technologies") or []:
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="technology_domain",
                            signal_key=technology,
                            value=1.0,
                            evidence_text="seed taxonomy",
                            source_type="seed_markdown",
                            source_url=f"file://{markdown_file}",
                        )
                        details["signals_written"] += 1

                    ratings: dict[str, float] = record.get("ratings") or {}
                    for signal_key, value in ratings.items():
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="seed_rating",
                            signal_key=signal_key,
                            value=float(value),
                            evidence_text="seed rating",
                            source_type="seed_markdown",
                            source_url=f"file://{markdown_file}",
                        )
                        details["signals_written"] += 1

                    if record.get("team_size"):
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="team_metric",
                            signal_key="team_size",
                            value=float(record["team_size"]),
                            evidence_text="seed team size",
                            source_type="seed_markdown",
                            source_url=f"file://{markdown_file}",
                        )
                        details["signals_written"] += 1

                    if record.get("founded_year"):
                        add_signal(
                            session,
                            initiative_id=initiative.id,
                            signal_type="maturity_metric",
                            signal_key="founded_year",
                            value=float(record["founded_year"]),
                            evidence_text="seed founded year",
                            source_type="seed_markdown",
                            source_url=f"file://{markdown_file}",
                        )
                        details["signals_written"] += 1

                    details["records_upserted"] += 1

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
