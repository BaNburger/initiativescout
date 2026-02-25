from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def canonicalize_url(url: str | None) -> str:
    if not url:
        return ""
    candidate = url.strip()
    if not candidate:
        return ""
    if candidate.startswith("www."):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.scheme:
        parsed = urlparse(f"https://{candidate}")
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    sanitized = parsed._replace(scheme=parsed.scheme.lower(), netloc=netloc, path=path, params="", query="", fragment="")
    return urlunparse(sanitized)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def unique_list(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def maybe_float(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def maybe_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d{1,5}", value.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weighted_average(components: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    weighted_sum = 0.0
    for key, weight in weights.items():
        weighted_sum += components.get(key, 0.0) * weight
    return weighted_sum / total_weight


def text_hash(payload: Any) -> str:
    return hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()


def extract_team_size(text: str) -> int | None:
    patterns = [
        r"(\d{1,4})\s*(?:\+)?\s*(?:members?|students?|people|contributors?)",
        r"team\s*of\s*(\d{1,4})",
    ]
    normalized = text.casefold()
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def guess_university_from_text(text: str) -> str | None:
    upper = text.upper()
    if "TUM" in upper:
        return "TUM"
    if "LMU" in upper:
        return "LMU"
    if "HM" in upper or "HOCHSCHULE MUNCHEN" in upper or "HOCHSCHULE MUENCHEN" in upper:
        return "HM"
    return None
