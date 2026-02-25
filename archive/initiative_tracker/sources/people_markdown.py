from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from initiative_tracker.utils import normalize_name, unique_list

TABLE_ROW_RE = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.*?)\s*\|\s*$")
HEADING_RE = re.compile(r"^###\s+(?:\d+\.\s+)?(.+?)\s*$")
URL_RE = re.compile(r"https?://[^\s)]+")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

CONTACT_KEYS = {
    "lead",
    "leads",
    "team lead",
    "team leads",
    "key contact",
    "contact",
    "lab lead",
    "other leaders",
    "founder",
    "key founders",
}
INITIATIVE_HINT_KEYS = {
    "initiative connection",
    "initiative",
    "origin",
}


def _clean_cell(value: str) -> str:
    out = value.strip()
    out = re.sub(r"\[(.*?)\]\((.*?)\)", r"\2", out)
    return out.strip()


def _extract_initiative_from_heading(heading: str) -> str:
    cleaned = heading.strip().strip("#").strip()
    # Examples:
    # "HORYZN (TUM) - Rescue Drones" -> HORYZN
    # "TUM Autonomous Motorsport" -> TUM Autonomous Motorsport
    if " - " in cleaned:
        left = cleaned.split(" - ", 1)[0].strip()
    else:
        left = cleaned
    left = re.sub(r"\([^)]*\)", "", left).strip()
    return left


def _split_people(raw: str) -> list[str]:
    text = _clean_cell(raw)
    text = re.sub(r"\bvia\b.*", "", text, flags=re.IGNORECASE).strip()
    chunks = re.split(r",|\band\b|&|/|;", text)
    out: list[str] = []
    for chunk in chunks:
        token = chunk.strip(" .")
        if not token:
            continue
        if any(w in token.casefold() for w in ["linkedin", "http", "@", "website"]):
            continue
        if len(token.split()) > 6:
            continue
        if re.search(r"\d", token):
            continue
        out.append(token)
    return unique_list(out)


def _guess_person_type(path: Path) -> str:
    lower = path.name.casefold()
    if "alumni" in lower:
        return "alumni_angel"
    return "operator"


def parse_people_from_markdown(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    person_type_default = _guess_person_type(path)

    current_heading = ""
    current_initiative = ""
    current_fields: dict[str, str] = {}
    records: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_fields
        if not current_fields:
            return

        contacts: list[str] = []
        urls: list[str] = []
        emails: list[str] = []
        names: list[str] = []

        for key, value in current_fields.items():
            clean_key = key.casefold().strip()
            clean_value = _clean_cell(value)

            urls.extend(URL_RE.findall(clean_value))
            emails.extend(EMAIL_RE.findall(clean_value))

            if clean_key in CONTACT_KEYS or "lead" in clean_key or "contact" in clean_key or "founder" in clean_key:
                names.extend(_split_people(clean_value))

        initiative_names: list[str] = []
        if current_initiative:
            initiative_names.append(current_initiative)

        for key, value in current_fields.items():
            clean_key = key.casefold().strip()
            if clean_key in INITIATIVE_HINT_KEYS:
                initiative_names.extend(_split_people(value))

        if not names and person_type_default == "alumni_angel" and current_heading:
            # Alumni sections often use heading as person identity.
            names.extend(_split_people(current_heading))
            if not names:
                names = [current_heading.split(" - ", 1)[0].strip()]

        reason = current_fields.get("Outreach Priority") or current_fields.get("Priority") or ""
        for person_name in unique_list(names):
            if normalize_name(person_name) in {"via main", "to contact", "unknown"}:
                continue
            records.append(
                {
                    "name": person_name,
                    "person_type": person_type_default,
                    "role": current_fields.get("Role") or current_fields.get("Lead") or "",
                    "initiative_names": unique_list([n for n in initiative_names if n]),
                    "contact_channels": unique_list([*urls, *emails]),
                    "source_urls": unique_list(urls),
                    "headline": current_heading,
                    "why_ranked": unique_list([reason]) if reason else [],
                    "evidence": f"{path.name}::{current_heading}",
                    "source_path": str(path),
                }
            )

        current_fields = {}

    for line in lines:
        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            current_heading = heading_match.group(1).strip()
            current_initiative = _extract_initiative_from_heading(current_heading)
            continue

        row_match = TABLE_ROW_RE.match(line)
        if row_match:
            key = row_match.group(1).strip()
            value = row_match.group(2).strip()
            current_fields[key] = value

    flush()

    unique_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (
            normalize_name(record["name"]),
            record["person_type"],
            ",".join(sorted(normalize_name(v) for v in record.get("initiative_names", []))),
        )
        existing = unique_records.get(key)
        if existing is None:
            unique_records[key] = record
            continue
        existing["contact_channels"] = unique_list([*existing["contact_channels"], *record["contact_channels"]])
        existing["source_urls"] = unique_list([*existing["source_urls"], *record["source_urls"]])
        existing["why_ranked"] = unique_list([*existing.get("why_ranked", []), *record.get("why_ranked", [])])

    return list(unique_records.values())
