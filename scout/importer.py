from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from scout.models import ImportResult, Initiative

log = logging.getLogger(__name__)


def _s(value: object) -> str:
    """Safely coerce cell value to stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_spin_off_sheet(ws) -> list[dict]:
    """Parse the 'Spin-Off Targets' sheet (has 2 header rows: group + column)."""
    rows = list(ws.iter_rows(min_row=3, values_only=True))
    out: list[dict] = []
    for row in rows:
        if not row or not row[3]:  # col 3 = Initiative name
            continue
        out.append({
            "name": _s(row[3]),
            "uni": _s(row[2]),
            "sector": _s(row[0]),
            "mode": _s(row[1]),
            "description": _s(row[4]),
            "website": _s(row[5]),
            "email": _s(row[6]),
            "team_page": _s(row[10]),
            "team_size": _s(row[11]),
            "linkedin": _s(row[7]),
            "github_org": _s(row[17]),
            "key_repos": _s(row[18]),
            "sponsors": _s(row[22]),
            "competitions": _s(row[23]),
            "relevance": "",
            "sheet_source": "spin_off_targets",
            "extra_links_json": json.dumps({
                k: v for k, v in {
                    "instagram": _s(row[8]),
                    "x_twitter": _s(row[9]),
                    "discord": _s(row[12]),
                    "slack": _s(row[13]),
                    "luma": _s(row[14]),
                    "eventbrite": _s(row[15]),
                    "linktree": _s(row[16]),
                    "huggingface": _s(row[19]),
                    "github": _s(row[20]),
                    "sponsors_page": _s(row[21]),
                    "youtube": _s(row[24]) if len(row) > 24 else "",
                }.items() if v
            }),
        })
    return out


def _parse_all_initiatives_sheet(ws) -> list[dict]:
    """Parse the 'All Initiatives' sheet (has 1 header row)."""
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    out: list[dict] = []
    for row in rows:
        if not row or not row[4]:  # col 4 = Initiative name
            continue
        out.append({
            "name": _s(row[4]),
            "uni": _s(row[3]),
            "sector": _s(row[1]),
            "mode": _s(row[2]),
            "description": _s(row[5]),
            "website": _s(row[6]),
            "email": _s(row[7]),
            "team_page": _s(row[19]) if len(row) > 19 else "",
            "team_size": "",
            "linkedin": _s(row[8]),
            "github_org": _s(row[13]),
            "key_repos": "",
            "sponsors": "",
            "competitions": "",
            "relevance": _s(row[0]),
            "sheet_source": "all_initiatives",
            "extra_links_json": json.dumps({
                k: v for k, v in {
                    "instagram": _s(row[9]),
                    "x_twitter": _s(row[10]),
                    "facebook": _s(row[11]),
                    "youtube": _s(row[12]),
                    "discord": _s(row[14]),
                    "slack": _s(row[15]),
                    "luma": _s(row[16]),
                    "linktree": _s(row[17]),
                    "huggingface": _s(row[18]),
                    "sponsors_page": _s(row[20]) if len(row) > 20 else "",
                    "eventbrite": _s(row[21]) if len(row) > 21 else "",
                }.items() if v
            }),
        })
    return out


def _normalize_key(name: str, uni: str) -> str:
    return f"{name.strip().casefold()}|{uni.strip().casefold()}"


def _upsert(session: Session, data: dict, existing: dict[str, Initiative]) -> tuple[bool, Initiative]:
    """Insert new or update existing initiative. Returns (is_new, initiative)."""
    key = _normalize_key(data["name"], data["uni"])
    if key in existing:
        init = existing[key]
        # Update fields if the new data has more info
        for field in ("sector", "mode", "description", "website", "email", "team_page",
                      "team_size", "linkedin", "github_org", "key_repos", "sponsors",
                      "competitions", "relevance"):
            new_val = data.get(field, "")
            old_val = getattr(init, field, "") or ""
            if new_val and (not old_val or (data["sheet_source"] == "spin_off_targets" and field != "relevance")):
                setattr(init, field, new_val)
        # Merge extra links
        try:
            old_links = json.loads(init.extra_links_json or "{}")
        except (json.JSONDecodeError, TypeError):
            old_links = {}
        try:
            new_links = json.loads(data.get("extra_links_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            new_links = {}
        merged = {**old_links, **{k: v for k, v in new_links.items() if v}}
        init.extra_links_json = json.dumps(merged)
        return False, init
    else:
        init = Initiative(**data)
        session.add(init)
        existing[key] = init
        return True, init


def import_xlsx(file_path: str | Path, session: Session) -> ImportResult:
    """Import both sheets from the enriched XLSX. Upserts by name+uni."""
    file_path = Path(file_path)
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)

    # Load existing initiatives for dedup
    all_existing = session.execute(select(Initiative)).scalars().all()
    existing_map: dict[str, Initiative] = {
        _normalize_key(i.name, i.uni): i for i in all_existing
    }

    spin_off_rows: list[dict] = []
    all_init_rows: list[dict] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if "spin" in sheet_name.casefold() and "off" in sheet_name.casefold():
            spin_off_rows = _parse_spin_off_sheet(ws)
        elif "all" in sheet_name.casefold() and "init" in sheet_name.casefold():
            all_init_rows = _parse_all_initiatives_sheet(ws)

    wb.close()

    new_count = 0
    updated_count = 0

    # Import spin-off targets first (higher quality data)
    for data in spin_off_rows:
        is_new, _ = _upsert(session, data, existing_map)
        if is_new:
            new_count += 1
        else:
            updated_count += 1

    # Then all initiatives (fills gaps, adds relevance rating)
    for data in all_init_rows:
        is_new, _ = _upsert(session, data, existing_map)
        if is_new:
            new_count += 1
        else:
            updated_count += 1

    session.commit()

    return ImportResult(
        total_imported=new_count + updated_count,
        spin_off_count=len(spin_off_rows),
        all_initiatives_count=len(all_init_rows),
        duplicates_updated=updated_count,
    )
