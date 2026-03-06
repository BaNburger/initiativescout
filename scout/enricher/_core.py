"""Core infrastructure: constants, optional deps, shared helpers, caching, HTTP."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from lxml import etree, html as lxml_html

from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)

_USER_AGENT = "ScoutBot/1.0 (+https://scout.local)"
_TIMEOUT = 15.0
_MAX_TEXT = 15_000
_MAX_SUMMARY = 1500

GITHUB_API = "https://api.github.com"

# ---------------------------------------------------------------------------
# Per-entity URL cache — avoids re-fetching the same page across enrichers
# ---------------------------------------------------------------------------

_CACHE_ERROR = object()  # sentinel for cached fetch failures
_url_cache: dict[str, str | object] = {}
_url_cache_lock = asyncio.Lock()
_url_cache_enabled = False
_shared_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def _html_cache():
    """Context manager that enables per-entity URL caching and shared HTTP client.

    While active, ``_fetch_url`` caches responses (and errors) so parallel enrichers
    sharing URLs don't duplicate requests. A shared ``httpx.AsyncClient`` reuses TCP
    connections across enrichers for the same entity.
    """
    global _url_cache_enabled, _shared_client
    _url_cache.clear()
    _url_cache_enabled = True
    _shared_client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(_TIMEOUT),
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        yield
    finally:
        _url_cache_enabled = False
        _url_cache.clear()
        await _shared_client.aclose()
        _shared_client = None


# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

_CRAWL4AI_AVAILABLE = False
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # noqa: F401
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    AsyncWebCrawler = None  # type: ignore[assignment,misc]
    BrowserConfig = None  # type: ignore[assignment,misc]
    CrawlerRunConfig = None  # type: ignore[assignment,misc]

_DDGS_AVAILABLE = False
try:
    from ddgs import DDGS  # noqa: F401
    _DDGS_AVAILABLE = True
except ImportError:
    DDGS = None  # type: ignore[assignment,misc]

_TRAFILATURA_AVAILABLE = False
try:
    import trafilatura  # noqa: F401
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    trafilatura = None  # type: ignore[assignment]

_EXTRUCT_AVAILABLE = False
try:
    import extruct  # noqa: F401
    _EXTRUCT_AVAILABLE = True
except ImportError:
    extruct = None  # type: ignore[assignment]

# ddgs v9+ no longer exposes a RatelimitException; use a generic fallback.
RatelimitException: type[Exception] = Exception  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str:
    """Ensure a URL has an http(s) scheme prefix."""
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _get_website_url(initiative: Initiative) -> str | None:
    """Extract and normalize the website URL from an initiative, or None if absent."""
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    return _normalize_url(url)


def _make_enrichment(
    initiative: Initiative, source_type: str, source_url: str,
    raw_text: str, summary: str | None = None,
    structured_fields: dict | None = None,
) -> Enrichment:
    """Create an Enrichment with automatic truncation and timestamp."""
    import json
    return Enrichment(
        initiative_id=initiative.id,
        source_type=source_type,
        source_url=source_url,
        raw_text=raw_text[:_MAX_TEXT],
        summary=(summary or raw_text)[:_MAX_SUMMARY],
        structured_fields_json=json.dumps(structured_fields) if structured_fields else "{}",
        fetched_at=datetime.now(UTC),
    )


def _parse_html(raw_html: str):
    """Strip XML declaration and parse HTML with lxml. Returns tree or None."""
    cleaned = re.sub(r"^<\?xml[^?]*\?>\s*", "", raw_html, count=1)
    try:
        return lxml_html.fromstring(cleaned)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return None


def _github_org_from_field(initiative: Initiative) -> str:
    """Extract a clean GitHub org/user name from the initiative's github_org field."""
    org = (initiative.field("github_org") or "").strip()
    if "github.com" in org:
        parts = org.split("github.com")[-1].strip("/").split("/")
        org = parts[0] if parts else ""
    return org


def _github_headers() -> dict[str, str]:
    """Build GitHub API headers, including auth token if available."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _fetch_url(url: str) -> str:
    """Fetch a URL, using per-entity cache when enabled.

    Caches both successes (str) and failures (sentinel) so parallel
    enrichers sharing the same website URL don't duplicate requests
    or hammer a broken URL repeatedly.
    """
    if not _url_cache_enabled:
        return await _fetch_url_uncached(url)

    async with _url_cache_lock:
        if url in _url_cache:
            cached = _url_cache[url]
            if cached is _CACHE_ERROR:
                raise httpx.HTTPError(f"Cached failure for {url}")
            return cached  # type: ignore[return-value]

    # Outside lock — do the actual fetch
    try:
        text = await _fetch_url_uncached(url)
    except Exception:
        async with _url_cache_lock:
            _url_cache[url] = _CACHE_ERROR
        raise

    async with _url_cache_lock:
        _url_cache[url] = text
    return text


async def _fetch_url_uncached(url: str) -> str:
    if _shared_client is not None:
        resp = await _shared_client.get(url)
        resp.raise_for_status()
        return resp.text
    # No shared client — create a one-off client
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(_TIMEOUT),
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_text(raw_html: str) -> str:
    """Extract main content text from HTML.

    Uses trafilatura (F1=0.958) as primary extractor for superior boilerplate
    removal, falling back to lxml-based extraction if trafilatura is unavailable
    or returns empty.
    """
    # Try trafilatura first — much better at extracting main content
    if _TRAFILATURA_AVAILABLE:
        try:
            text = trafilatura.extract(
                raw_html,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            if text and text.strip():
                return text.strip()[:_MAX_TEXT]
        except Exception:
            pass  # fall through to lxml

    # Fallback: lxml-based extraction
    tree = _parse_html(raw_html)
    if tree is None:
        return ""
    title = " ".join(tree.xpath("//title//text()")).strip()
    meta = " ".join(tree.xpath("//meta[@name='description']/@content")).strip()

    for el in tree.xpath("//script | //style | //nav | //footer | //header | //noscript"):
        el.getparent().remove(el)

    body = tree.xpath("//body")
    content = " ".join((body[0] if body else tree).text_content().split())

    parts = []
    if title:
        parts.append(f"TITLE: {title}")
    if meta:
        parts.append(f"META: {meta}")
    if content:
        parts.append(f"CONTENT: {content}")
    return "\n".join(parts)[:_MAX_TEXT]


def _summarize_text(text: str, url: str) -> str:
    """Create a compact summary for embeddings and display."""
    lines = text.split("\n")
    summary_parts = [f"Source: {url}"]
    for line in lines[:4]:
        if line.strip():
            summary_parts.append(line[:500])
    return "\n".join(summary_parts)[:_MAX_SUMMARY]


async def _github_get(path: str, headers: dict[str, str]) -> tuple[int, dict | list | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT), headers=headers) as client:
            resp = await client.get(f"{GITHUB_API}{path}")
            if resp.status_code >= 400:
                return resp.status_code, None
            return resp.status_code, resp.json()
    except Exception as exc:
        log.debug("GitHub API request failed for %s: %s", path, exc)
        return 0, None
