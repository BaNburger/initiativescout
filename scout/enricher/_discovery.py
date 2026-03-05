"""DuckDuckGo-based URL discovery."""
from __future__ import annotations

import asyncio
import logging
import time

from scout.enricher._core import (
    DDGS,
    RatelimitException,
    _DDGS_AVAILABLE,
)
from scout.models import Initiative
from scout.utils import json_parse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DuckDuckGo rate limiter
# ---------------------------------------------------------------------------


class _DDGRateLimiter:
    """Global rate limiter for DuckDuckGo searches."""

    def __init__(self, min_delay: float = 12.0, max_delay: float = 120.0):
        self._lock = asyncio.Lock()
        self._min_delay = min_delay
        self._current_delay = min_delay
        self._max_delay = max_delay
        self._last_call: float = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._current_delay - (now - self._last_call)
            if wait > 0:
                log.debug("DDG rate limiter: waiting %.1fs", wait)
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    def backoff(self) -> None:
        self._current_delay = min(self._current_delay * 2, self._max_delay)
        log.warning("DDG rate limited, backing off to %.0fs between requests", self._current_delay)

    def reset(self) -> None:
        self._current_delay = self._min_delay


_ddg_limiter = _DDGRateLimiter()


# ---------------------------------------------------------------------------
# DuckDuckGo URL discovery
# ---------------------------------------------------------------------------

_PLATFORM_PATTERNS: dict[str, str] = {
    "linkedin": "linkedin.com",
    "github": "github.com",
    "huggingface": "huggingface.co",
    "instagram": "instagram.com",
    "x_twitter": "x.com",
    "facebook": "facebook.com",
    "youtube": "youtube.com",
    "researchgate": "researchgate.net",
    "crunchbase": "crunchbase.com",
    "tiktok": "tiktok.com",
    "discord": "discord.gg",
    "google_scholar": "scholar.google.com",
    "orcid": "orcid.org",
    "semantic_scholar": "semanticscholar.org",
    "openalex": "openalex.org",
}


def _ddg_search_sync(query: str, max_results: int = 10) -> list[dict]:
    """Synchronous DDG search (called via asyncio.to_thread)."""
    return list(DDGS().text(query, max_results=max_results))


async def _ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """Run a single DuckDuckGo search with rate limiting and one retry."""
    for attempt in range(2):
        await _ddg_limiter.acquire()
        try:
            results = await asyncio.to_thread(_ddg_search_sync, query, max_results)
            _ddg_limiter.reset()
            return results
        except RatelimitException:
            _ddg_limiter.backoff()
    log.warning("DDG rate limited after retry for query=%r, skipping", query)
    return []


async def discover_urls(initiative: Initiative) -> dict[str, str]:
    """Use DuckDuckGo to discover platform URLs for an initiative.

    Returns a dict of ``{source_key: url}`` for newly discovered URLs
    not already in extra_links_json.  Does NOT modify the initiative object.

    Raises ImportError if ddgs is not installed.
    """
    if not _DDGS_AVAILABLE:
        raise ImportError("ddgs not installed. Install: pip install 'scout[crawl]'")

    name = (initiative.field("name") or "").strip()
    uni = (initiative.field("uni") or "").strip()
    if not name:
        return {}

    query = f'"{name}" {uni}' if uni else f'"{name}"'

    try:
        results = await _ddg_search(query)
    except Exception as exc:
        log.warning("DDG search failed for %s: %s", name, exc)
        return {}

    existing = json_parse(initiative.extra_links_json)
    known_domains: set[str] = set()
    for url_field in ("website", "team_page", "linkedin", "github_org"):
        val = (initiative.field(url_field) or "").strip()
        if val:
            known_domains.add(val.lower())

    discovered: dict[str, str] = {}
    for result in results:
        href = result.get("href", "")
        if not href:
            continue
        for key, domain in _PLATFORM_PATTERNS.items():
            if domain in href.lower() and key not in existing and key not in discovered:
                if not any(domain in kd for kd in known_domains):
                    discovered[key] = href
                break

    return discovered
