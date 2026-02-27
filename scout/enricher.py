from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from lxml import etree, html as lxml_html

from scout.models import Enrichment, Initiative
from scout.utils import json_parse

log = logging.getLogger(__name__)

_USER_AGENT = "ScoutBot/1.0 (+https://scout.local)"
_TIMEOUT = 15.0
_MAX_TEXT = 15_000

GITHUB_API = "https://api.github.com"

# ---------------------------------------------------------------------------
# Optional dependency detection
# ---------------------------------------------------------------------------

_CRAWL4AI_AVAILABLE = False
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # noqa: F401
    _CRAWL4AI_AVAILABLE = True
except ImportError:
    AsyncWebCrawler = None  # type: ignore[assignment,misc]

_DDGS_AVAILABLE = False
try:
    from duckduckgo_search import AsyncDDGS  # noqa: F401
    from duckduckgo_search.exceptions import RatelimitException  # noqa: F401
    _DDGS_AVAILABLE = True
except ImportError:
    AsyncDDGS = None  # type: ignore[assignment,misc]
    RatelimitException = Exception  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Crawl4AI page fetcher
# ---------------------------------------------------------------------------


async def _crawl4ai_fetch(url: str, crawler: object) -> str | None:
    """Fetch a page using Crawl4AI, return markdown text."""
    try:
        config = CrawlerRunConfig(page_timeout=30000)
        result = await crawler.arun(url=url, config=config)  # type: ignore[union-attr]
        if not result.success:
            log.warning("Crawl4AI failed for %s: %s", url, getattr(result, "error_message", "unknown"))
            return None
        md = result.markdown
        text = (getattr(md, "fit_markdown", None) or getattr(md, "raw_markdown", None) or "").strip()
        return text[:_MAX_TEXT] if text else None
    except Exception as exc:
        log.warning("Crawl4AI exception for %s: %s", url, exc)
        return None


@asynccontextmanager
async def open_crawler():
    """Async context manager yielding an AsyncWebCrawler if crawl4ai is installed, else None.

    Usage::

        async with open_crawler() as crawler:
            # crawler is AsyncWebCrawler | None
            await _enrich_page(init, url, "website", crawler)
    """
    if not _CRAWL4AI_AVAILABLE:
        yield None
        return
    browser_config = BrowserConfig(headless=True, verbose=False)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        yield crawler


# ---------------------------------------------------------------------------
# Page enrichment (shared by website, team page, extra links)
# ---------------------------------------------------------------------------


async def _enrich_page(
    initiative: Initiative, url: str, source_type: str,
    crawler: object | None = None,
) -> Enrichment | None:
    """Fetch a page, extract text, return an Enrichment.

    Uses Crawl4AI when a crawler is provided, otherwise falls back to httpx+lxml.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    text = None

    # Try Crawl4AI first
    if _CRAWL4AI_AVAILABLE and crawler is not None:
        text = await _crawl4ai_fetch(url, crawler)

    # Fallback to httpx + lxml
    if text is None:
        try:
            raw_html = await _fetch_url(url)
        except Exception as exc:
            log.warning("Failed to fetch %s: %s", url, exc)
            return None
        text = _extract_text(raw_html)

    if not text or not text.strip():
        return None

    summary = _summarize_text(text, url)
    return Enrichment(
        initiative_id=initiative.id,
        source_type=source_type,
        raw_text=text[:_MAX_TEXT],
        summary=summary,
        fetched_at=datetime.now(UTC),
    )


async def enrich_website(
    initiative: Initiative, crawler: object | None = None,
) -> Enrichment | None:
    """Fetch initiative website, extract text content."""
    url = (initiative.website or "").strip()
    return await _enrich_page(initiative, url, "website", crawler) if url else None


async def enrich_team_page(
    initiative: Initiative, crawler: object | None = None,
) -> Enrichment | None:
    """Fetch team page if different from main website."""
    url = (initiative.team_page or "").strip()
    if not url or url == (initiative.website or "").strip():
        return None
    return await _enrich_page(initiative, url, "team_page", crawler)


# ---------------------------------------------------------------------------
# Extra links enrichment
# ---------------------------------------------------------------------------

# Keys that overlap with standard enrichers or aren't crawlable
_SKIP_LINK_KEYS = {
    "website", "website_urls", "team_page",
    "github", "github_urls", "github_org",
    "directory_source_urls", "other_social_urls",
}


async def enrich_extra_links(
    initiative: Initiative, crawler: object | None = None,
) -> list[Enrichment]:
    """Crawl all URLs in extra_links_json, return enrichments.

    Source type is derived from the dict key with ``_urls``/``_url`` suffix stripped.
    Keys that overlap with standard enrichers are skipped.
    """
    extra = json_parse(initiative.extra_links_json)
    if not extra:
        return []

    tasks: list[tuple[str, asyncio.Task]] = []
    for key, url in extra.items():
        if key in _SKIP_LINK_KEYS or not url or not isinstance(url, str):
            continue
        url = url.strip()
        if not url:
            continue
        # Normalize source_type: strip _urls/_url suffix
        source_type = key.removesuffix("_urls").removesuffix("_url")
        tasks.append((key, _enrich_page(initiative, url, source_type, crawler)))

    if not tasks:
        return []

    results = await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)
    enrichments: list[Enrichment] = []
    for (key, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            log.warning("Extra link enrichment failed for key=%s: %s", key, result)
        elif result is not None:
            enrichments.append(result)
    return enrichments


# ---------------------------------------------------------------------------
# GitHub enrichment (unchanged — uses REST API, not web crawling)
# ---------------------------------------------------------------------------


async def enrich_github(initiative: Initiative) -> Enrichment | None:
    """Fetch GitHub org/repo metrics."""
    org = (initiative.github_org or "").strip()
    if not org:
        return None

    # Clean up: might be a full URL
    if "github.com" in org:
        parts = org.split("github.com")[-1].strip("/").split("/")
        org = parts[0] if parts else ""
    if not org:
        return None

    repos_text = (initiative.key_repos or "").strip()
    repo = repos_text.split(",")[0].strip().split("/")[-1] if repos_text else ""

    token = os.environ.get("GITHUB_TOKEN", "")
    headers: dict[str, str] = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    lines: list[str] = [f"GitHub org: {org}"]

    # Fetch org repos
    try:
        status, data = await _github_get(f"/orgs/{org}/repos?per_page=30&sort=updated", headers)
        if status == 200 and isinstance(data, list):
            lines.append(f"Public repos: {len(data)}")
            for r in data[:5]:
                lines.append(f"  - {r.get('name')}: stars={r.get('stargazers_count', 0)}, forks={r.get('forks_count', 0)}, lang={r.get('language', '?')}")
                desc = r.get("description") or ""
                if desc:
                    lines.append(f"    {desc[:120]}")
        elif status == 404:
            # Try as user instead of org
            status, data = await _github_get(f"/users/{org}/repos?per_page=30&sort=updated", headers)
            if status == 200 and isinstance(data, list):
                lines.append(f"Public repos: {len(data)}")
                for r in data[:5]:
                    lines.append(f"  - {r.get('name')}: stars={r.get('stargazers_count', 0)}, forks={r.get('forks_count', 0)}")
    except Exception as exc:
        log.warning("GitHub org fetch failed for %s: %s", org, exc)

    # Fetch specific repo metrics if available
    if repo:
        try:
            metrics = await _collect_repo_metrics(org, repo, headers)
            if metrics:
                lines.append(f"\nKey repo: {org}/{repo}")
                lines.append(f"  Contributors: {metrics.get('contributors', '?')}")
                lines.append(f"  Commits (90d): {metrics.get('commits_90d', '?')}")
                lines.append(f"  CI/CD: {'yes' if metrics.get('ci_present') else 'no'}")
        except Exception as exc:
            log.warning("GitHub repo fetch failed for %s/%s: %s", org, repo, exc)

    text = "\n".join(lines)
    if len(lines) <= 1:
        return None

    return Enrichment(
        initiative_id=initiative.id,
        source_type="github",
        raw_text=text,
        summary=text[:500],
        fetched_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# DuckDuckGo rate limiter
# ---------------------------------------------------------------------------


class _DDGRateLimiter:
    """Global rate limiter for DuckDuckGo searches.

    Enforces a minimum delay between calls and exponential backoff
    on rate limit errors.
    """

    def __init__(self, min_delay: float = 12.0, max_delay: float = 120.0):
        self._lock = asyncio.Lock()
        self._min_delay = min_delay
        self._current_delay = min_delay
        self._max_delay = max_delay
        self._last_call: float = 0.0

    async def acquire(self) -> None:
        """Wait until the minimum delay has elapsed since the last call."""
        async with self._lock:
            now = time.monotonic()
            wait = self._current_delay - (now - self._last_call)
            if wait > 0:
                log.debug("DDG rate limiter: waiting %.1fs", wait)
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    def backoff(self) -> None:
        """Double the current delay (up to max) after a rate limit error."""
        self._current_delay = min(self._current_delay * 2, self._max_delay)
        log.warning("DDG rate limited, backing off to %.0fs between requests", self._current_delay)

    def reset(self) -> None:
        """Reset delay to baseline after a successful call."""
        self._current_delay = self._min_delay


_ddg_limiter = _DDGRateLimiter()


# ---------------------------------------------------------------------------
# DuckDuckGo URL discovery
# ---------------------------------------------------------------------------

# Map platform domains to extra_links_json keys
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
}


async def _ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """Run a single DuckDuckGo search with rate limiting and retry."""
    await _ddg_limiter.acquire()
    try:
        async with AsyncDDGS() as ddgs:
            results = [r async for r in ddgs.atext(query, max_results=max_results)]
        _ddg_limiter.reset()
        return results
    except RatelimitException:
        _ddg_limiter.backoff()
        # One retry after backoff
        await _ddg_limiter.acquire()
        try:
            async with AsyncDDGS() as ddgs:
                results = [r async for r in ddgs.atext(query, max_results=max_results)]
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

    Raises ImportError if duckduckgo-search is not installed.
    """
    if not _DDGS_AVAILABLE:
        raise ImportError("duckduckgo-search not installed. Install: pip install 'scout[crawl]'")

    name = (initiative.name or "").strip()
    uni = (initiative.uni or "").strip()
    if not name:
        return {}

    query = f'"{name}" {uni}' if uni else f'"{name}"'

    try:
        results = await _ddg_search(query)
    except Exception as exc:
        log.warning("DDG search failed for %s: %s", name, exc)
        return {}

    # Extract platform URLs not already known
    existing = json_parse(initiative.extra_links_json)
    # Also consider fields directly on the initiative as "known"
    known_domains: set[str] = set()
    for url_field in ("website", "team_page", "linkedin", "github_org"):
        val = (getattr(initiative, url_field, "") or "").strip()
        if val:
            known_domains.add(val.lower())

    discovered: dict[str, str] = {}
    for result in results:
        href = result.get("href", "")
        if not href:
            continue
        for key, domain in _PLATFORM_PATTERNS.items():
            if domain in href.lower() and key not in existing and key not in discovered:
                # Skip if this URL is already known via a direct field
                if not any(domain in kd for kd in known_domains):
                    discovered[key] = href
                break

    return discovered


# ---------------------------------------------------------------------------
# HTTP helpers (fallback when Crawl4AI not available)
# ---------------------------------------------------------------------------


async def _fetch_url(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(_TIMEOUT),
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_text(raw_html: str) -> str:
    """Extract readable text from HTML using lxml."""
    try:
        tree = lxml_html.fromstring(raw_html)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return ""
    title = " ".join(tree.xpath("//title//text()")).strip()
    meta = " ".join(tree.xpath("//meta[@name='description']/@content")).strip()
    headings = " ".join(tree.xpath("//h1//text() | //h2//text() | //h3//text()")).strip()
    paragraphs = " ".join(tree.xpath("//p//text()")).strip()

    parts = []
    if title:
        parts.append(f"TITLE: {title}")
    if meta:
        parts.append(f"META: {meta}")
    if headings:
        parts.append(f"HEADINGS: {headings}")
    if paragraphs:
        parts.append(f"CONTENT: {paragraphs}")
    return "\n".join(parts)[:_MAX_TEXT]


def _summarize_text(text: str, url: str) -> str:
    """Create a compact summary for LLM consumption."""
    lines = text.split("\n")
    summary_parts = [f"Source: {url}"]
    for line in lines[:4]:
        if line.strip():
            summary_parts.append(line[:300])
    return "\n".join(summary_parts)[:500]


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


async def _collect_repo_metrics(org: str, repo: str, headers: dict[str, str]) -> dict:
    metrics: dict = {"contributors": 0, "commits_90d": 0, "ci_present": False}
    since = (datetime.now(UTC) - timedelta(days=90)).isoformat()

    (s1, contributors), (s2, commits), (s3, workflows) = await asyncio.gather(
        _github_get(f"/repos/{org}/{repo}/contributors?per_page=100", headers),
        _github_get(f"/repos/{org}/{repo}/commits?per_page=100&since={since}", headers),
        _github_get(f"/repos/{org}/{repo}/contents/.github/workflows", headers),
    )
    if s1 == 200 and isinstance(contributors, list):
        metrics["contributors"] = len(contributors)
    if s2 == 200 and isinstance(commits, list):
        metrics["commits_90d"] = len(commits)
    if s3 == 200 and isinstance(workflows, list) and workflows:
        metrics["ci_present"] = True

    return metrics
