"""Website, team page, extra links, and career page enrichers."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlparse

from scout.enricher._core import (
    _CRAWL4AI_AVAILABLE,
    _MAX_TEXT,
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    _extract_text,
    _fetch_url,
    _get_website_url,
    _make_enrichment,
    _normalize_url,
    _parse_html,
    _summarize_text,
)
from scout.models import Enrichment, Initiative
from scout.utils import json_parse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML-based field extraction (no LLM, pure heuristics)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,4}(?=[\s,;)<>\'"|\])]|$)'
)
# Common generic emails to skip
_GENERIC_EMAILS = {"info@", "hello@", "contact@", "admin@", "support@",
                   "noreply@", "no-reply@", "privacy@", "webmaster@"}


def _extract_fields_from_html(raw_html: str, base_url: str) -> dict:
    """Extract structured entity fields directly from HTML using heuristics.

    Extracts: email, social links (linkedin, github, instagram, etc.),
    and contact-related data. Returns a dict of field_key → value.
    """
    fields: dict = {}
    tree = _parse_html(raw_html)
    if tree is None:
        return fields

    # --- Email extraction ---
    # Prefer mailto: links over regex (more intentional)
    mailto_links = tree.xpath('//a[starts-with(@href, "mailto:")]/@href')
    for href in mailto_links:
        email = href.removeprefix("mailto:").split("?")[0].strip().lower()
        if email and "@" in email and not any(email.startswith(g) for g in _GENERIC_EMAILS):
            fields["email"] = email
            break
    # Fallback: regex over visible text (skip scripts/styles)
    if "email" not in fields:
        for el in tree.xpath("//script | //style"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
        body_text = tree.xpath("//body")
        text = (body_text[0] if body_text else tree).text_content()
        for match in _EMAIL_RE.finditer(text):
            email = match.group().lower()
            if not any(email.startswith(g) for g in _GENERIC_EMAILS):
                fields["email"] = email
                break

    # --- Social link extraction ---
    _social_patterns = {
        "linkedin": "linkedin.com/company/",
        "github_org": "github.com/",
        "instagram": "instagram.com/",
    }
    all_hrefs = tree.xpath("//a/@href")
    for href in all_hrefs:
        href_lower = (href or "").lower().strip()
        for field_key, pattern in _social_patterns.items():
            if field_key not in fields and pattern in href_lower:
                # Skip generic LinkedIn pages
                if field_key == "linkedin" and href_lower.rstrip("/").endswith("linkedin.com"):
                    continue
                # Skip github.com itself (no org path)
                if field_key == "github_org":
                    path = urlparse(href_lower).path.strip("/")
                    if not path or "/" in path:  # skip repo URLs, only orgs
                        continue
                fields[field_key] = href.strip()
                break

    # --- Team page auto-discovery ---
    _team_patterns = re.compile(
        r'(?i)\b(team|members|people|our.team|about.us|ueber.uns)\b'
    )
    base_domain = urlparse(base_url).netloc
    for anchor in tree.xpath("//a[@href]"):
        href_val = (anchor.get("href") or "").strip()
        link_text = (anchor.text_content() or "").strip()
        if not href_val or href_val.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href_val)
        parsed = urlparse(absolute)
        if parsed.netloc and parsed.netloc != base_domain:
            continue
        combined = f"{parsed.path} {link_text}"
        if _team_patterns.search(combined) and "team_page" not in fields:
            fields["team_page"] = absolute
            break

    return fields


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
    """Async context manager yielding an AsyncWebCrawler if crawl4ai is installed, else None."""
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
    """Fetch a page, extract text, return an Enrichment."""
    url = _normalize_url(url)

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
    return _make_enrichment(initiative, source_type, url, text, summary)


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
    tree = _parse_html(raw_html)
    if tree is None:
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
        if parsed.netloc and parsed.netloc != base_domain:
            continue
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
    """Fetch initiative website + important subpages, extract text + structured fields."""
    url = _get_website_url(initiative)
    if not url:
        return []

    main = await _enrich_page(initiative, url, "website", crawler)
    if main is None:
        return []

    results: list[Enrichment] = [main]

    try:
        raw_html = await _fetch_url(url)
    except Exception:
        return results

    # Extract structured fields from main page HTML
    fields = _extract_fields_from_html(raw_html, url)

    subpage_urls = _extract_important_links(raw_html, url)
    if subpage_urls:
        # Fetch subpages and extract fields from each
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
                # Extract fields from subpage HTML too (contact pages often have emails)
                try:
                    sub_html = await _fetch_url(sub_url)
                    sub_fields = _extract_fields_from_html(sub_html, sub_url)
                    for k, v in sub_fields.items():
                        if k not in fields:  # main page fields take priority
                            fields[k] = v
                except Exception:
                    pass

    # Store merged fields on the main enrichment
    if fields:
        main.structured_fields_json = json.dumps(fields)

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

_SKIP_LINK_KEYS = {
    "website", "website_urls", "team_page",
    "github", "github_urls", "github_org",
    "directory_source_urls", "other_social_urls",
}


async def enrich_extra_links(
    initiative: Initiative, crawler: object | None = None,
) -> list[Enrichment]:
    """Crawl all URLs in extra_links_json, return enrichments."""
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
# Career/job page enrichment
# ---------------------------------------------------------------------------

_CAREER_PATH_PATTERNS = [
    "/careers", "/jobs", "/join", "/join-us", "/hiring",
    "/karriere", "/stellen", "/work-with-us", "/open-positions",
    "/team/join", "/about/careers",
]


async def enrich_careers(initiative: Initiative) -> Enrichment | None:
    """Discover and parse career/job pages for growth signals."""
    url = _get_website_url(initiative)
    if not url:
        return None

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in _CAREER_PATH_PATTERNS:
        career_url = f"{base}{path}"
        try:
            raw_html = await _fetch_url(career_url)
            if not raw_html:
                continue
            text = _extract_text(raw_html)
            if not text or len(text) < 50:
                continue

            text_lower = text.lower()
            if not any(kw in text_lower for kw in (
                "position", "role", "apply", "job", "career",
                "hiring", "team", "stelle", "bewerb",
            )):
                continue

            full_text = f"CAREER PAGE: {career_url}\n{text[:_MAX_TEXT - 200]}"
            return _make_enrichment(initiative, "careers", career_url, full_text)
        except Exception:
            continue

    return None
