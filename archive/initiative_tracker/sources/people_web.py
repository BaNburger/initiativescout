from __future__ import annotations

import re
from collections import deque
from urllib.parse import urlparse

from lxml import html

from initiative_tracker.config import Settings
from initiative_tracker.sources.common import absolutize, fetch_html, normalize_whitespace
from initiative_tracker.utils import canonicalize_url, normalize_name, unique_list

NAME_PATTERN = re.compile(r"\b([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'\-]+\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'\-]+)\b")
ROLE_HINT_RE = re.compile(
    r"\b(founder|co-founder|lead|president|chair|board|captain|principal|cto|ceo|head|vorstand|leitung)\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

SAFE_KEYWORDS = (
    "team",
    "people",
    "lead",
    "about",
    "contact",
    "board",
    "vorstand",
)

NON_PERSON_TERMS = {
    "privacy",
    "policy",
    "cookie",
    "cookies",
    "about",
    "contact",
    "imprint",
    "impressum",
    "terms",
    "conditions",
    "legal",
    "notice",
    "datenschutz",
    "uber",
    "ueber",
    "home",
    "team",
    "news",
    "blog",
    "careers",
    "jobs",
    "support",
    "press",
    "media",
    "faq",
    "hilfe",
}


def _looks_like_human_name(name: str) -> bool:
    tokens = [token.strip(" .,'\"") for token in name.split() if token.strip(" .,'\"")]
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    lowered = [token.casefold() for token in tokens]
    if any(token in NON_PERSON_TERMS for token in lowered):
        return False
    if any(token.isdigit() for token in lowered):
        return False
    # Reject nav-like text fragments masquerading as names.
    if " ".join(lowered) in {
        "privacy policy",
        "about us",
        "terms conditions",
        "cookie policy",
        "legal notice",
        "imprint",
        "contact us",
    }:
        return False
    if any(len(token) <= 1 for token in lowered):
        return False
    return True


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except Exception:  # noqa: BLE001
        return ""


def _interesting_links(tree, page_url: str, *, crawl_mode: str) -> list[str]:
    hrefs = tree.xpath("//a[@href]/@href")
    page_domain = _extract_domain(page_url)
    links: list[str] = []
    for href in hrefs:
        absolute = canonicalize_url(absolutize(page_url, href))
        if not absolute:
            continue
        domain = _extract_domain(absolute)
        if not domain:
            continue

        if crawl_mode == "safe":
            if domain != page_domain:
                continue
            if not any(keyword in absolute.casefold() for keyword in SAFE_KEYWORDS):
                continue
        else:
            if domain != page_domain and not any(s in domain for s in ["linkedin.com", "github.com", "x.com", "twitter.com"]):
                continue

        links.append(absolute)
    return unique_list(links)


def _extract_person_candidates(tree, source_url: str) -> list[dict]:
    title = normalize_whitespace(" ".join(tree.xpath("//title//text()")))
    text = normalize_whitespace(" ".join(tree.xpath("//h1//text() | //h2//text() | //h3//text() | //p//text() | //li//text()")))

    emails = unique_list(EMAIL_RE.findall(text))
    names = unique_list([m.group(1).strip() for m in NAME_PATTERN.finditer(text)])

    records: list[dict] = []
    for name in names:
        normalized = normalize_name(name)
        if len(normalized.split()) < 2:
            continue
        if not _looks_like_human_name(name):
            continue
        # Keep probable human names and avoid brand/initiative names with all caps style acronyms.
        if any(token.isupper() and len(token) > 3 for token in name.split()):
            continue

        role = ""
        window_match = re.search(re.escape(name) + r".{0,80}", text)
        if window_match:
            window = window_match.group(0)
            role_match = ROLE_HINT_RE.search(window)
            if role_match:
                role = role_match.group(1)

        if not role:
            nearby_role = ROLE_HINT_RE.search(text)
            if nearby_role:
                role = nearby_role.group(1)

        records.append(
            {
                "name": name,
                "person_type": "operator",
                "role": role,
                "initiative_names": [],
                "contact_channels": emails,
                "source_urls": [source_url],
                "headline": title,
                "why_ranked": ["discovered on initiative website"],
                "evidence": text[:200],
            }
        )

    return records


def crawl_people_from_website(
    start_url: str,
    *,
    settings: Settings,
    crawl_mode: str = "safe",
    max_pages: int = 12,
) -> dict:
    if crawl_mode not in {"safe", "max-reach"}:
        crawl_mode = "safe"

    start = canonicalize_url(start_url)
    if not start:
        return {"visited": 0, "records": []}

    queue: deque[str] = deque([start])
    visited: set[str] = set()
    records: list[dict] = []

    delay = settings.website_request_delay_seconds
    if crawl_mode == "max-reach":
        delay = 0.1

    while queue and len(visited) < max_pages:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        try:
            html_text = fetch_html(current, settings, delay_seconds=delay)
        except Exception:  # noqa: BLE001
            continue

        try:
            tree = html.fromstring(html_text)
        except Exception:  # noqa: BLE001
            continue

        records.extend(_extract_person_candidates(tree, current))

        for link in _interesting_links(tree, current, crawl_mode=crawl_mode):
            if link not in visited and link not in queue:
                queue.append(link)

    dedup: dict[tuple[str, str], dict] = {}
    for record in records:
        key = (normalize_name(record["name"]), (record.get("role") or "").casefold())
        existing = dedup.get(key)
        if existing is None:
            dedup[key] = record
            continue
        existing["contact_channels"] = unique_list([*existing.get("contact_channels", []), *record.get("contact_channels", [])])
        existing["source_urls"] = unique_list([*existing.get("source_urls", []), *record.get("source_urls", [])])

    return {"visited": len(visited), "records": list(dedup.values())}
