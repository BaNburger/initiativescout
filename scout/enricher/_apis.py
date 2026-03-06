"""Free API-based enrichers: OpenAlex, Wikidata."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from scout.enricher._core import _make_enrichment, _shared_client
from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)

_UA = "Scout/1.0 (https://github.com/scout-project)"


# ---------------------------------------------------------------------------
# Async HTTP helper (reuses shared client from _html_cache when available)
# ---------------------------------------------------------------------------

async def _api_get(url: str, params: dict | None = None) -> dict | list | None:
    """GET JSON from a URL, reusing the shared httpx client if available."""
    import httpx
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    try:
        if _shared_client is not None:
            resp = await _shared_client.get(url, params=params, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("API GET failed %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# OpenAlex enricher
# ---------------------------------------------------------------------------

_OPENALEX_BASE = "https://api.openalex.org"


async def enrich_openalex(initiative: Initiative) -> Enrichment | None:
    """Query OpenAlex for publication/citation signals related to this entity.

    Searches works by entity name + university, returns hit count and
    top cited papers. Also searches institutions for the university.
    No API key needed — just polite-pool with mailto.
    """
    name = (initiative.field("name") or "").strip()
    uni = (initiative.field("uni") or "").strip()
    if not name:
        return None

    query = f"{name} {uni}" if uni else name
    lines: list[str] = [f"OPENALEX RESEARCH SIGNALS: {name}"]
    fields: dict = {}

    # Search works (publications mentioning this entity)
    works = await _api_get(f"{_OPENALEX_BASE}/works", params={
        "search": query,
        "per_page": "5",
        "mailto": "scout@enrichment.local",
    })
    if works and isinstance(works, dict):
        count = works.get("meta", {}).get("count", 0)
        fields["openalex_hits"] = min(count, 9999)
        lines.append(f"Publications found: {count}")
        for w in works.get("results", [])[:5]:
            title = (w.get("title") or "?")[:100]
            cited = w.get("cited_by_count", 0)
            year = w.get("publication_year", "?")
            lines.append(f"  [{year}] {title} (cited: {cited})")
            # Extract topics
            for topic in w.get("topics", [])[:3]:
                topic_name = topic.get("display_name", "")
                if topic_name:
                    lines.append(f"    topic: {topic_name}")

    # Search for the university as an institution
    if uni:
        inst_data = await _api_get(f"{_OPENALEX_BASE}/institutions", params={
            "search": uni,
            "per_page": "1",
            "mailto": "scout@enrichment.local",
        })
        if inst_data and isinstance(inst_data, dict):
            results = inst_data.get("results", [])
            if results:
                inst = results[0]
                lines.append(f"\nUniversity: {inst.get('display_name', uni)}")
                lines.append(f"  Total works: {inst.get('works_count', '?')}")
                lines.append(f"  Total citations: {inst.get('cited_by_count', '?')}")
                lines.append(f"  Type: {inst.get('type', '?')}")
                # Extract associated topics
                topics = inst.get("x_concepts", [])[:5]
                if topics:
                    topic_names = [t.get("display_name", "") for t in topics if t.get("display_name")]
                    if topic_names:
                        lines.append(f"  Key domains: {', '.join(topic_names)}")

    if len(lines) <= 1:
        return None

    text = "\n".join(lines)
    return _make_enrichment(
        initiative, "openalex",
        f"{_OPENALEX_BASE}/works?search={quote(query)}",
        text, structured_fields=fields or None,
    )


# ---------------------------------------------------------------------------
# Wikidata enricher
# ---------------------------------------------------------------------------

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Property IDs for fields we care about
_WD_PROPS = {
    "P856": "website",      # official website
    "P2002": "x_twitter",   # Twitter/X username
    "P2003": "instagram",   # Instagram username
    "P2013": "facebook",    # Facebook ID
    "P2037": "github_org",  # GitHub username
    "P3789": "telegram",    # Telegram username
    "P571": "founded",      # inception date
    "P1128": "member_count",  # employees / members
    "P154": "logo",         # logo image
    "P17": "country",       # country
    "P159": "headquarters", # headquarters location
    "P361": "part_of",      # part of (parent org)
    "P1813": "short_name",  # short name
}


def _extract_wikidata_value(snak: dict) -> str | None:
    """Extract a human-readable value from a Wikidata snak."""
    dv = snak.get("datavalue", {})
    vtype = dv.get("type")
    val = dv.get("value")
    if val is None:
        return None
    if vtype == "string":
        return val
    if vtype == "time":
        return val.get("time", "")[:10].lstrip("+")  # e.g. "2015-01-01"
    if vtype == "quantity":
        return val.get("amount", "").lstrip("+")
    if vtype == "wikibase-entityid":
        return val.get("id")  # Q-id, resolved later if needed
    if vtype == "monolingualtext":
        return val.get("text")
    return str(val)[:200]


async def _resolve_qid_label(qid: str) -> str:
    """Resolve a Wikidata Q-ID to its English label."""
    data = await _api_get(_WIKIDATA_API, params={
        "action": "wbgetentities",
        "ids": qid,
        "format": "json",
        "props": "labels",
        "languages": "en",
    })
    if data and isinstance(data, dict):
        labels = data.get("entities", {}).get(qid, {}).get("labels", {})
        en = labels.get("en", {})
        return en.get("value", qid)
    return qid


async def enrich_wikidata(initiative: Initiative) -> Enrichment | None:
    """Search Wikidata for this entity and extract structured properties.

    Wikidata has entries for many universities, research institutes, and
    notable student organizations with social links, founding dates, etc.
    """
    name = (initiative.field("name") or "").strip()
    uni = (initiative.field("uni") or "").strip()
    if not name:
        return None

    # Search Wikidata for the entity
    search_data = await _api_get(_WIKIDATA_API, params={
        "action": "wbsearchentities",
        "search": f"{name} {uni}" if uni else name,
        "language": "en",
        "format": "json",
        "limit": "3",
    })
    if not search_data or not isinstance(search_data, dict):
        return None

    results = search_data.get("search", [])
    if not results:
        # Try with just the name
        if uni:
            search_data = await _api_get(_WIKIDATA_API, params={
                "action": "wbsearchentities",
                "search": name,
                "language": "en",
                "format": "json",
                "limit": "3",
            })
            results = (search_data or {}).get("search", [])
        if not results:
            return None

    # Pick the best match
    qid = results[0]["id"]
    wd_label = results[0].get("label", name)
    wd_desc = results[0].get("description", "")

    # Fetch entity claims
    entity_data = await _api_get(_WIKIDATA_API, params={
        "action": "wbgetentities",
        "ids": qid,
        "format": "json",
        "props": "claims,sitelinks",
    })
    if not entity_data or not isinstance(entity_data, dict):
        return None

    entity = entity_data.get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})

    lines: list[str] = [f"WIKIDATA: {wd_label} ({qid})"]
    if wd_desc:
        lines.append(f"  Description: {wd_desc}")

    fields: dict = {}

    for pid, field_key in _WD_PROPS.items():
        if pid not in claims:
            continue
        raw = _extract_wikidata_value(claims[pid][0].get("mainsnak", {}))
        if not raw:
            continue

        # Resolve Q-IDs for entity references
        if raw.startswith("Q") and raw[1:].isdigit():
            raw = await _resolve_qid_label(raw)

        lines.append(f"  {field_key}: {raw}")

        # Map to structured fields
        if field_key == "website" and not initiative.field("website"):
            fields["website"] = raw
        elif field_key == "github_org" and not initiative.field("github_org"):
            fields["github_org"] = f"https://github.com/{raw}"
        elif field_key == "member_count" and not initiative.field("member_count"):
            try:
                fields["member_count"] = int(float(raw))
            except (ValueError, TypeError):
                pass

    # Check sitelinks for Wikipedia article (signals notability)
    sitelinks = entity.get("sitelinks", {})
    if "enwiki" in sitelinks:
        wiki_title = sitelinks["enwiki"].get("title", "")
        lines.append(f"  Wikipedia: https://en.wikipedia.org/wiki/{quote(wiki_title)}")

    if len(lines) <= 1:
        return None

    text = "\n".join(lines)
    return _make_enrichment(
        initiative, "wikidata",
        f"https://www.wikidata.org/wiki/{qid}",
        text, structured_fields=fields or None,
    )


# ---------------------------------------------------------------------------
# Enrichment text inference (regex-based field extraction from existing data)
# ---------------------------------------------------------------------------

_MEMBER_COUNT_RE = re.compile(
    r'(?:team\s+of|over|about|approximately|~|circa)\s+(\d{1,5})\s*(?:members|people|students|engineers|volunteers|participants)',
    re.IGNORECASE,
)
_MEMBER_COUNT_RE2 = re.compile(
    r'(\d{1,5})\s*(?:\+\s*)?(?:members|team members|active members|volunteers|student members)',
    re.IGNORECASE,
)
_FOUNDED_RE = re.compile(
    r'(?:founded|established|started|created|since|est\.?)\s+(?:in\s+)?(\d{4})',
    re.IGNORECASE,
)
_SPONSOR_RE = re.compile(
    r'(?:sponsors?|partners?|supported by|backed by|funded by)[:\s]+([^\n.]{10,200})',
    re.IGNORECASE,
)


def infer_fields_from_text(text: str) -> dict:
    """Extract structured fields from enrichment text using regex heuristics.

    Designed to run on the aggregated enrichment text for an entity.
    Returns a dict of field_key → value for fields that can be inferred.
    """
    fields: dict = {}

    # Member count
    for pattern in (_MEMBER_COUNT_RE, _MEMBER_COUNT_RE2):
        m = pattern.search(text)
        if m:
            try:
                count = int(m.group(1))
                if 2 <= count <= 50000:
                    fields["member_count"] = count
                    break
            except ValueError:
                pass

    # Sponsors
    m = _SPONSOR_RE.search(text)
    if m:
        sponsors_raw = m.group(1).strip()
        # Clean up: split by common delimiters, take meaningful names
        parts = re.split(r'[,;&]|\band\b', sponsors_raw)
        sponsors = [p.strip() for p in parts if len(p.strip()) > 2]
        if sponsors:
            fields["sponsors"] = "; ".join(sponsors[:10])

    return fields
