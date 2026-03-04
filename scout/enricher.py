from __future__ import annotations

import asyncio
import json as json_mod
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse

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
    from ddgs import DDGS  # noqa: F401
    _DDGS_AVAILABLE = True
except ImportError:
    DDGS = None  # type: ignore[assignment,misc]

# ddgs v9+ no longer exposes a RatelimitException; use a generic fallback.
RatelimitException: type[Exception] = Exception  # type: ignore[assignment,misc]


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
    raw_html = None

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
        source_url=url,
        raw_text=text[:_MAX_TEXT],
        summary=summary,
        fetched_at=datetime.now(UTC),
    )


# Keywords that indicate important subpages worth scraping
_IMPORTANT_LINK_KEYWORDS = re.compile(
    r"(?i)\b(about|team|members|people|contact|imprint|impressum|"
    r"projects|portfolio|research|partners|products|services|"
    r"what.we.do|our.work|ueber.uns|angebot)\b"
)
_CONTACT_LINK_KEYWORDS = re.compile(
    r"(?i)\b(contact|imprint|impressum|kontakt)\b"
)
_MAX_SUBPAGES = 5


def _extract_important_links(raw_html: str, base_url: str) -> list[str]:
    """Extract internal links from HTML that match important subpage keywords."""
    try:
        cleaned = re.sub(r"^<\?xml[^?]*\?>\s*", "", raw_html, count=1)
        tree = lxml_html.fromstring(cleaned)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return []

    base_domain = urlparse(base_url).netloc
    seen: set[str] = set()
    links: list[str] = []

    for anchor in tree.xpath("//a[@href]"):
        href = (anchor.get("href") or "").strip()
        text = (anchor.text_content() or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        # Only follow internal links (same domain)
        if parsed.netloc and parsed.netloc != base_domain:
            continue
        # Check if href path or link text matches important keywords
        combined = f"{parsed.path} {text}"
        if not _IMPORTANT_LINK_KEYWORDS.search(combined):
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if normalized in seen or normalized.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(normalized)
        links.append(absolute)
        if len(links) >= _MAX_SUBPAGES:
            break

    return links


def _is_contact_link(url: str) -> bool:
    """Check if a URL looks like a contact/imprint page."""
    return bool(_CONTACT_LINK_KEYWORDS.search(urlparse(url).path))


async def enrich_website(
    initiative: Initiative, crawler: object | None = None,
) -> list[Enrichment]:
    """Fetch initiative website + important subpages, extract text content.

    Returns a list of enrichments: the main page plus any discovered subpages.
    Contact/imprint pages get source_type="contact".
    """
    url = (initiative.field("website") or "").strip()
    if not url:
        return []
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Fetch main page
    main = await _enrich_page(initiative, url, "website", crawler)
    if main is None:
        return []

    results: list[Enrichment] = [main]

    # Try to discover important subpages from the raw HTML
    try:
        raw_html = await _fetch_url(url)
    except Exception:
        return results

    subpage_urls = _extract_important_links(raw_html, url)
    if not subpage_urls:
        return results

    # Fetch subpages in parallel
    sub_tasks = []
    for sub_url in subpage_urls:
        stype = "contact" if _is_contact_link(sub_url) else "website_subpage"
        sub_tasks.append(_enrich_page(initiative, sub_url, stype, crawler))

    sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)
    for sub_url, result in zip(subpage_urls, sub_results):
        if isinstance(result, Exception):
            log.warning("Subpage enrichment failed for %s: %s", sub_url, result)
        elif result is not None:
            results.append(result)

    return results


async def enrich_team_page(
    initiative: Initiative, crawler: object | None = None,
) -> Enrichment | None:
    """Fetch team page if different from main website."""
    url = (initiative.field("team_page") or "").strip()
    if not url or url == (initiative.field("website") or "").strip():
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
    org = (initiative.field("github_org") or "").strip()
    if not org:
        return None

    # Clean up: might be a full URL
    if "github.com" in org:
        parts = org.split("github.com")[-1].strip("/").split("/")
        org = parts[0] if parts else ""
    if not org:
        return None

    repos_text = (initiative.field("key_repos") or "").strip()
    repo = repos_text.split(",")[0].strip().split("/")[-1] if repos_text else ""

    token = os.environ.get("GITHUB_TOKEN", "")
    headers: dict[str, str] = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    lines: list[str] = [f"GitHub org: {org}"]

    def _format_repos(repos: list) -> None:
        lines.append(f"Public repos: {len(repos)}")
        for r in repos[:5]:
            lines.append(f"  - {r.get('name')}: stars={r.get('stargazers_count', 0)}, forks={r.get('forks_count', 0)}, lang={r.get('language', '?')}")
            desc = r.get("description") or ""
            if desc:
                lines.append(f"    {desc[:120]}")

    # Fetch org repos (fall back to user repos on 404)
    try:
        status, data = await _github_get(f"/orgs/{org}/repos?per_page=30&sort=updated", headers)
        if status == 200 and isinstance(data, list):
            _format_repos(data)
        elif status == 404:
            status, data = await _github_get(f"/users/{org}/repos?per_page=30&sort=updated", headers)
            if status == 200 and isinstance(data, list):
                _format_repos(data)
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
        source_url=f"https://github.com/{org}",
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

    # Extract platform URLs not already known
    existing = json_parse(initiative.extra_links_json)
    # Also consider fields directly on the initiative as "known"
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
                # Skip if this URL is already known via a direct field
                if not any(domain in kd for kd in known_domains):
                    discovered[key] = href
                break

    return discovered


# ---------------------------------------------------------------------------
# Structured data extraction (JSON-LD, OpenGraph, meta tags)
# ---------------------------------------------------------------------------


def _extract_structured_data(raw_html: str) -> str | None:
    """Extract JSON-LD, OpenGraph, and meta tags from HTML.

    Returns a text summary of structured data found, or None.
    """
    try:
        cleaned = re.sub(r"^<\?xml[^?]*\?>\s*", "", raw_html, count=1)
        tree = lxml_html.fromstring(cleaned)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return None

    lines: list[str] = []

    # JSON-LD (Schema.org)
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        text = (script.text or "").strip()
        if not text:
            continue
        try:
            data = json_mod.loads(text)
            items = data if isinstance(data, list) else [data]
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                ld_type = item.get("@type", "")
                if ld_type:
                    lines.append(f"Schema.org type: {ld_type}")
                for key in ("name", "description", "url", "foundingDate",
                            "numberOfEmployees", "address", "sameAs",
                            "founder", "email", "telephone", "logo",
                            "areaServed", "knowsAbout", "memberOf"):
                    val = item.get(key)
                    if val:
                        if isinstance(val, list):
                            val = ", ".join(str(v) for v in val[:5])
                        elif isinstance(val, dict):
                            val = val.get("name") or val.get("value") or str(val)[:200]
                        lines.append(f"  {key}: {str(val)[:300]}")
        except (json_mod.JSONDecodeError, TypeError):
            continue

    # OpenGraph tags
    og_tags = tree.xpath('//meta[starts-with(@property, "og:")]')
    for tag in og_tags:
        prop = (tag.get("property") or "")[3:]  # strip "og:"
        content = (tag.get("content") or "").strip()
        if prop and content:
            lines.append(f"OG {prop}: {content[:300]}")

    # Twitter card tags
    tw_tags = tree.xpath('//meta[starts-with(@name, "twitter:")]')
    for tag in tw_tags:
        name = (tag.get("name") or "")[8:]  # strip "twitter:"
        content = (tag.get("content") or "").strip()
        if name and content and name not in ("card",):
            lines.append(f"Twitter {name}: {content[:300]}")

    # Additional meta tags
    for meta_name in ("author", "keywords", "generator", "geo.region",
                      "geo.placename", "geo.position"):
        vals = tree.xpath(f'//meta[@name="{meta_name}"]/@content')
        for val in vals:
            if val and val.strip():
                lines.append(f"Meta {meta_name}: {val.strip()[:200]}")

    return "\n".join(lines) if lines else None


async def enrich_structured_data(initiative: Initiative) -> Enrichment | None:
    """Extract JSON-LD, OpenGraph, and meta tags from the initiative's website.

    This piggybacks on the website HTML — no extra HTTP request needed when
    called after enrich_website, but works standalone too.
    """
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        raw_html = await _fetch_url(url)
    except Exception as exc:
        log.warning("Structured data fetch failed for %s: %s", url, exc)
        return None

    text = _extract_structured_data(raw_html)
    if not text:
        return None

    return Enrichment(
        initiative_id=initiative.id,
        source_type="structured_data",
        source_url=url,
        raw_text=text[:_MAX_TEXT],
        summary=text[:1500],
        fetched_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Technology stack detection (DIY BuiltWith)
# ---------------------------------------------------------------------------

# Maps regex patterns to technology names — checked against HTML source
_TECH_FINGERPRINTS: list[tuple[str, str, str]] = [
    # (category, name, pattern)
    # Frameworks
    ("framework", "React", r'react(?:\.production|\.development|dom)'),
    ("framework", "Next.js", r'(?:_next/static|__next|next/dist)'),
    ("framework", "Vue.js", r'(?:vue\.(?:min\.)?js|__vue__|v-cloak)'),
    ("framework", "Nuxt.js", r'(?:_nuxt/|__nuxt)'),
    ("framework", "Angular", r'(?:ng-version|angular(?:\.min)?\.js)'),
    ("framework", "Svelte", r'(?:svelte-[\w]+|__svelte)'),
    ("framework", "WordPress", r'(?:wp-content|wp-includes|wordpress)'),
    ("framework", "Shopify", r'(?:cdn\.shopify\.com|Shopify\.theme)'),
    ("framework", "Webflow", r'(?:webflow\.com|wf-page)'),
    ("framework", "Wix", r'(?:wix\.com|wixstatic\.com)'),
    ("framework", "Squarespace", r'(?:squarespace\.com|sqsp)'),
    ("framework", "Ghost", r'(?:ghost\.(?:io|org)|ghost-(?:url|api))'),
    ("framework", "Hugo", r'(?:gohugo\.io|powered.*hugo)'),
    ("framework", "Gatsby", r'gatsby'),
    ("framework", "Django", r'(?:csrfmiddlewaretoken|django)'),
    ("framework", "Ruby on Rails", r'(?:csrf-token.*authenticity|rails-ujs)'),
    ("framework", "Laravel", r'(?:laravel|XSRF-TOKEN)'),
    # Analytics
    ("analytics", "Google Analytics", r'(?:google-analytics\.com|gtag|googletagmanager)'),
    ("analytics", "Plausible", r'plausible\.io'),
    ("analytics", "Matomo", r'(?:matomo|piwik)'),
    ("analytics", "Mixpanel", r'mixpanel'),
    ("analytics", "Hotjar", r'hotjar'),
    ("analytics", "PostHog", r'posthog'),
    # Marketing & engagement
    ("marketing", "HubSpot", r'(?:hubspot|hs-scripts|hbspt)'),
    ("marketing", "Intercom", r'(?:intercom|intercomSettings)'),
    ("marketing", "Drift", r'drift\.com'),
    ("marketing", "Crisp", r'crisp\.chat'),
    ("marketing", "Mailchimp", r'mailchimp'),
    ("marketing", "Typeform", r'typeform'),
    # Payments
    ("payments", "Stripe", r'(?:stripe\.com/v|Stripe\()'),
    ("payments", "PayPal", r'paypal'),
    # Infrastructure
    ("infrastructure", "Cloudflare", r'(?:cloudflare|cf-ray)'),
    ("infrastructure", "Vercel", r'(?:vercel|\.vercel\.app)'),
    ("infrastructure", "Netlify", r'(?:netlify)'),
    ("infrastructure", "Heroku", r'heroku'),
    ("infrastructure", "Firebase", r'(?:firebase|firebaseapp)'),
]


def _detect_tech_stack(raw_html: str) -> str | None:
    """Detect technologies from HTML source code fingerprints."""
    if not raw_html:
        return None

    found: dict[str, list[str]] = {}  # category -> [names]
    html_lower = raw_html.lower()

    for category, name, pattern in _TECH_FINGERPRINTS:
        if re.search(pattern, html_lower, re.IGNORECASE):
            found.setdefault(category, []).append(name)

    if not found:
        return None

    lines: list[str] = ["DETECTED TECHNOLOGY STACK:"]
    for category, names in sorted(found.items()):
        lines.append(f"  {category}: {', '.join(names)}")

    return "\n".join(lines)


async def enrich_tech_stack(initiative: Initiative) -> Enrichment | None:
    """Detect the technology stack from the initiative's website HTML."""
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        raw_html = await _fetch_url(url)
    except Exception as exc:
        log.warning("Tech stack detection failed for %s: %s", url, exc)
        return None

    text = _detect_tech_stack(raw_html)
    if not text:
        return None

    return Enrichment(
        initiative_id=initiative.id,
        source_type="tech_stack",
        source_url=url,
        raw_text=text[:_MAX_TEXT],
        summary=text[:1500],
        fetched_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# DNS enrichment (MX records, TXT records)
# ---------------------------------------------------------------------------


async def _dns_lookup(domain: str) -> str | None:
    """Perform DNS lookups for MX and TXT records using socket.

    Uses asyncio.to_thread for non-blocking resolution.
    """
    lines: list[str] = [f"DNS ENRICHMENT: {domain}"]

    # MX records via socket.getaddrinfo doesn't do MX, use asyncio DNS
    try:
        loop = asyncio.get_running_loop()

        # A record — check if domain resolves
        try:
            addrs = await asyncio.to_thread(socket.getaddrinfo, domain, None, socket.AF_INET)
            if addrs:
                ips = {a[4][0] for a in addrs}
                lines.append(f"  Resolves to: {', '.join(sorted(ips)[:3])}")
        except socket.gaierror:
            lines.append("  Domain does not resolve (no A record)")
            return "\n".join(lines) if len(lines) > 1 else None

        # Try to detect mail provider via common MX patterns
        try:
            import dns.resolver  # type: ignore[import-untyped]
            mx_records = await asyncio.to_thread(
                lambda: list(dns.resolver.resolve(domain, "MX"))
            )
            mx_hosts = [str(r.exchange).rstrip(".").lower() for r in mx_records]
            lines.append(f"  MX records: {', '.join(mx_hosts[:5])}")
            # Identify email provider
            mx_str = " ".join(mx_hosts)
            if "google" in mx_str or "gmail" in mx_str:
                lines.append("  Email provider: Google Workspace")
            elif "outlook" in mx_str or "microsoft" in mx_str:
                lines.append("  Email provider: Microsoft 365")
            elif "zoho" in mx_str:
                lines.append("  Email provider: Zoho Mail")
            elif "protonmail" in mx_str or "proton" in mx_str:
                lines.append("  Email provider: ProtonMail")
        except ImportError:
            pass  # dnspython not installed — skip MX
        except Exception:
            pass  # domain may not have MX

        # TXT records (SPF, verification tokens)
        try:
            import dns.resolver  # type: ignore[import-untyped]
            txt_records = await asyncio.to_thread(
                lambda: list(dns.resolver.resolve(domain, "TXT"))
            )
            for rdata in txt_records[:10]:
                txt = str(rdata).strip('"')
                if txt.startswith("v=spf"):
                    lines.append(f"  SPF: {txt[:200]}")
                elif "google-site-verification" in txt:
                    lines.append("  Verified: Google Search Console")
                elif "facebook-domain-verification" in txt:
                    lines.append("  Verified: Facebook/Meta")
                elif "MS=" in txt:
                    lines.append("  Verified: Microsoft")
                elif "_dmarc" in txt or "v=DMARC" in txt.upper():
                    lines.append("  DMARC: configured")
        except ImportError:
            pass
        except Exception:
            pass

    except Exception as exc:
        log.debug("DNS lookup failed for %s: %s", domain, exc)
        return None

    return "\n".join(lines) if len(lines) > 1 else None


async def enrich_dns(initiative: Initiative) -> Enrichment | None:
    """Look up DNS records (MX, TXT) for the initiative's domain."""
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc
    if not domain:
        return None
    # Strip www prefix
    if domain.startswith("www."):
        domain = domain[4:]

    text = await _dns_lookup(domain)
    if not text:
        return None

    return Enrichment(
        initiative_id=initiative.id,
        source_type="dns",
        source_url=url,
        raw_text=text[:_MAX_TEXT],
        summary=text[:1500],
        fetched_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Sitemap / robots.txt enrichment
# ---------------------------------------------------------------------------


async def enrich_sitemap(initiative: Initiative) -> Enrichment | None:
    """Parse robots.txt and sitemap.xml for site structure signals."""
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    lines: list[str] = [f"SITE STRUCTURE: {parsed.netloc}"]

    # robots.txt
    try:
        robots_text = await _fetch_url(f"{base}/robots.txt")
        if robots_text and "user-agent" in robots_text.lower():
            disallowed = re.findall(r"Disallow:\s*(\S+)", robots_text, re.IGNORECASE)
            sitemaps = re.findall(r"Sitemap:\s*(\S+)", robots_text, re.IGNORECASE)
            if disallowed:
                lines.append(f"  Disallowed paths: {len(disallowed)}")
                for p in disallowed[:10]:
                    lines.append(f"    {p}")
            if sitemaps:
                lines.append(f"  Sitemap URLs declared: {len(sitemaps)}")
    except Exception:
        pass

    # sitemap.xml
    sitemap_urls = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
    page_count = 0
    page_types: dict[str, int] = {}  # path prefix -> count

    for sitemap_url in sitemap_urls:
        try:
            sitemap_text = await _fetch_url(sitemap_url)
            if not sitemap_text or "<urlset" not in sitemap_text.lower() and "<sitemapindex" not in sitemap_text.lower():
                continue
            # Count URLs in sitemap
            urls_found = re.findall(r"<loc>([^<]+)</loc>", sitemap_text)
            page_count += len(urls_found)
            # Categorize by path prefix
            for found_url in urls_found[:500]:
                path = urlparse(found_url).path.strip("/")
                prefix = path.split("/")[0] if path else "root"
                page_types[prefix] = page_types.get(prefix, 0) + 1
            break  # got a valid sitemap
        except Exception:
            continue

    if page_count:
        lines.append(f"  Total pages in sitemap: {page_count}")
        if page_types:
            # Top sections by page count
            sorted_types = sorted(page_types.items(), key=lambda x: x[1], reverse=True)
            lines.append("  Site sections:")
            for prefix, count in sorted_types[:10]:
                lines.append(f"    /{prefix}: {count} pages")

    # Identify career/job pages
    for found_url in re.findall(r"<loc>([^<]+)</loc>", sitemap_text if page_count else ""):
        path_lower = found_url.lower()
        if any(kw in path_lower for kw in ("career", "job", "stellen", "hiring", "join")):
            lines.append(f"  Career page found: {found_url}")
            break

    return Enrichment(
        initiative_id=initiative.id,
        source_type="sitemap",
        source_url=f"{base}/sitemap.xml",
        raw_text="\n".join(lines)[:_MAX_TEXT],
        summary="\n".join(lines)[:1500],
        fetched_at=datetime.now(UTC),
    ) if len(lines) > 1 else None


# ---------------------------------------------------------------------------
# Career/job page enrichment
# ---------------------------------------------------------------------------

_CAREER_PATH_PATTERNS = [
    "/careers", "/jobs", "/join", "/join-us", "/hiring",
    "/karriere", "/stellen", "/work-with-us", "/open-positions",
    "/team/join", "/about/careers",
]


async def enrich_careers(initiative: Initiative) -> Enrichment | None:
    """Discover and parse career/job pages for growth signals."""
    url = (initiative.field("website") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Try common career page paths
    for path in _CAREER_PATH_PATTERNS:
        career_url = f"{base}{path}"
        try:
            raw_html = await _fetch_url(career_url)
            if not raw_html:
                continue
            text = _extract_text(raw_html)
            if not text or len(text) < 50:
                continue

            # Basic validation: does it look like a careers page?
            text_lower = text.lower()
            if not any(kw in text_lower for kw in (
                "position", "role", "apply", "job", "career",
                "hiring", "team", "stelle", "bewerb",
            )):
                continue

            lines = [f"CAREER PAGE: {career_url}", text[:_MAX_TEXT - 200]]
            full_text = "\n".join(lines)

            return Enrichment(
                initiative_id=initiative.id,
                source_type="careers",
                source_url=career_url,
                raw_text=full_text[:_MAX_TEXT],
                summary=full_text[:1500],
                fetched_at=datetime.now(UTC),
            )
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Deep Git enrichment (README, dependencies, releases, license)
# ---------------------------------------------------------------------------


async def enrich_git_deep(initiative: Initiative) -> Enrichment | None:
    """Extract deeper GitHub signals: README, deps, license, releases."""
    org = (initiative.field("github_org") or "").strip()
    if not org:
        return None

    if "github.com" in org:
        parts = org.split("github.com")[-1].strip("/").split("/")
        org = parts[0] if parts else ""
    if not org:
        return None

    repos_text = (initiative.field("key_repos") or "").strip()
    repo = repos_text.split(",")[0].strip().split("/")[-1] if repos_text else ""

    token = os.environ.get("GITHUB_TOKEN", "")
    headers: dict[str, str] = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    lines: list[str] = [f"DEEP GIT ANALYSIS: {org}"]

    # If no specific repo, find the most starred one
    if not repo:
        status, repos_data = await _github_get(f"/orgs/{org}/repos?per_page=10&sort=stars", headers)
        if status == 404:
            status, repos_data = await _github_get(f"/users/{org}/repos?per_page=10&sort=stars", headers)
        if status == 200 and isinstance(repos_data, list) and repos_data:
            repo = repos_data[0].get("name", "")

    if not repo:
        return None

    # Parallel fetch: README, license, releases, languages, package files
    readme_task = _github_get(f"/repos/{org}/{repo}/readme", {**headers, "Accept": "application/vnd.github.raw"})
    license_task = _github_get(f"/repos/{org}/{repo}/license", headers)
    releases_task = _github_get(f"/repos/{org}/{repo}/releases?per_page=10", headers)
    langs_task = _github_get(f"/repos/{org}/{repo}/languages", headers)

    (s_readme, readme), (s_lic, lic), (s_rel, releases), (s_lang, langs) = await asyncio.gather(
        readme_task, license_task, releases_task, langs_task,
    )

    # README content
    if s_readme == 200 and readme:
        readme_text = str(readme) if not isinstance(readme, (dict, list)) else ""
        if isinstance(readme, dict):
            readme_text = readme.get("content", "") or readme.get("body", "")
            # GitHub raw returns the text directly when Accept: raw
        if readme_text:
            lines.append(f"\nREADME ({org}/{repo}):")
            lines.append(readme_text[:3000])

    # License
    if s_lic == 200 and isinstance(lic, dict):
        lic_info = lic.get("license", {})
        lic_name = lic_info.get("name") or lic_info.get("spdx_id") or "Unknown"
        lines.append(f"\nLicense: {lic_name}")

    # Releases
    if s_rel == 200 and isinstance(releases, list) and releases:
        lines.append(f"\nReleases: {len(releases)} (showing latest)")
        for rel in releases[:3]:
            tag = rel.get("tag_name", "?")
            date = (rel.get("published_at") or "")[:10]
            name = rel.get("name", "")
            lines.append(f"  {tag} ({date}): {name[:100]}")

    # Languages
    if s_lang == 200 and isinstance(langs, dict) and langs:
        total = sum(langs.values())
        lang_pcts = [(k, round(v / total * 100, 1)) for k, v in
                     sorted(langs.items(), key=lambda x: x[1], reverse=True)[:5]]
        lines.append(f"\nLanguages: {', '.join(f'{k} ({v}%)' for k, v in lang_pcts)}")

    # Dependency files — check for common package manifests
    dep_files = [
        ("package.json", "Node.js"),
        ("requirements.txt", "Python"),
        ("pyproject.toml", "Python"),
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("pom.xml", "Java/Maven"),
        ("build.gradle", "Java/Gradle"),
        ("Gemfile", "Ruby"),
    ]
    found_deps: list[str] = []
    dep_tasks = [_github_get(f"/repos/{org}/{repo}/contents/{f}", headers) for f, _ in dep_files]
    dep_results = await asyncio.gather(*dep_tasks, return_exceptions=True)
    for (filename, ecosystem), result in zip(dep_files, dep_results):
        if isinstance(result, tuple) and result[0] == 200:
            found_deps.append(f"{ecosystem} ({filename})")
    if found_deps:
        lines.append(f"\nDependency ecosystems: {', '.join(found_deps)}")

    if len(lines) <= 1:
        return None

    text = "\n".join(lines)
    return Enrichment(
        initiative_id=initiative.id,
        source_type="git_deep",
        source_url=f"https://github.com/{org}/{repo}",
        raw_text=text[:_MAX_TEXT],
        summary=text[:1500],
        fetched_at=datetime.now(UTC),
    )


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
    """Extract all visible text from HTML, stripping navigation and boilerplate."""
    try:
        # Strip XML declaration — lxml.html chokes on XHTML prologues like <?xml ...?>
        cleaned = re.sub(r"^<\?xml[^?]*\?>\s*", "", raw_html, count=1)
        tree = lxml_html.fromstring(cleaned)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return ""
    title = " ".join(tree.xpath("//title//text()")).strip()
    meta = " ".join(tree.xpath("//meta[@name='description']/@content")).strip()

    # Strip noise elements before extracting visible text
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
    return "\n".join(summary_parts)[:1500]


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
