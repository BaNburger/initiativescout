from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from lxml import etree, html as lxml_html

from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)

_USER_AGENT = "ScoutBot/1.0 (+https://scout.local)"
_TIMEOUT = 15.0
_MAX_TEXT = 15_000

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Page enrichment (shared by website + team page)
# ---------------------------------------------------------------------------


async def _enrich_page(
    initiative: Initiative, url: str, source_type: str,
) -> Enrichment | None:
    """Fetch a page, extract text, return an Enrichment."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        raw_html = await _fetch_url(url)
    except Exception as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return None

    text = _extract_text(raw_html)
    if not text.strip():
        return None

    summary = _summarize_text(text, url)
    return Enrichment(
        initiative_id=initiative.id,
        source_type=source_type,
        raw_text=text[:_MAX_TEXT],
        summary=summary,
        fetched_at=datetime.now(UTC),
    )


async def enrich_website(initiative: Initiative) -> Enrichment | None:
    """Fetch initiative website, extract text content."""
    url = (initiative.website or "").strip()
    return await _enrich_page(initiative, url, "website") if url else None


async def enrich_team_page(initiative: Initiative) -> Enrichment | None:
    """Fetch team page if different from main website."""
    url = (initiative.team_page or "").strip()
    if not url or url == (initiative.website or "").strip():
        return None
    return await _enrich_page(initiative, url, "team_page")


# ---------------------------------------------------------------------------
# GitHub enrichment
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
# HTTP helpers
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
