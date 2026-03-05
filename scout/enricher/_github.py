"""GitHub enrichers: org/repo metrics and deep analysis."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from scout.enricher._core import (
    _github_get,
    _github_headers,
    _github_org_from_field,
    _make_enrichment,
)
from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)


def _first_repo(initiative: Initiative) -> str:
    """Extract the first repo name from the key_repos field."""
    repos_text = (initiative.field("key_repos") or "").strip()
    return repos_text.split(",")[0].strip().split("/")[-1] if repos_text else ""


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


async def enrich_github(initiative: Initiative) -> Enrichment | None:
    """Fetch GitHub org/repo metrics."""
    org = _github_org_from_field(initiative)
    if not org:
        return None

    repo = _first_repo(initiative)

    headers = _github_headers()

    lines: list[str] = [f"GitHub org: {org}"]

    def _format_repos(repos: list) -> None:
        lines.append(f"Public repos: {len(repos)}")
        for r in repos[:5]:
            lines.append(f"  - {r.get('name')}: stars={r.get('stargazers_count', 0)}, forks={r.get('forks_count', 0)}, lang={r.get('language', '?')}")
            desc = r.get("description") or ""
            if desc:
                lines.append(f"    {desc[:120]}")

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

    if len(lines) <= 1:
        return None

    text = "\n".join(lines)
    return _make_enrichment(initiative, "github", f"https://github.com/{org}", text)


async def enrich_git_deep(initiative: Initiative) -> Enrichment | None:
    """Extract deeper GitHub signals: README, deps, license, releases."""
    org = _github_org_from_field(initiative)
    if not org:
        return None

    repo = _first_repo(initiative)

    headers = _github_headers()

    lines: list[str] = [f"DEEP GIT ANALYSIS: {org}"]

    if not repo:
        status, repos_data = await _github_get(f"/orgs/{org}/repos?per_page=10&sort=stars", headers)
        if status == 404:
            status, repos_data = await _github_get(f"/users/{org}/repos?per_page=10&sort=stars", headers)
        if status == 200 and isinstance(repos_data, list) and repos_data:
            repo = repos_data[0].get("name", "")

    if not repo:
        return None

    readme_task = _github_get(f"/repos/{org}/{repo}/readme", {**headers, "Accept": "application/vnd.github.raw"})
    license_task = _github_get(f"/repos/{org}/{repo}/license", headers)
    releases_task = _github_get(f"/repos/{org}/{repo}/releases?per_page=10", headers)
    langs_task = _github_get(f"/repos/{org}/{repo}/languages", headers)

    (s_readme, readme), (s_lic, lic), (s_rel, releases), (s_lang, langs) = await asyncio.gather(
        readme_task, license_task, releases_task, langs_task,
    )

    if s_readme == 200 and readme:
        readme_text = str(readme) if not isinstance(readme, (dict, list)) else ""
        if isinstance(readme, dict):
            readme_text = readme.get("content", "") or readme.get("body", "")
        if readme_text:
            lines.append(f"\nREADME ({org}/{repo}):")
            lines.append(readme_text[:3000])

    if s_lic == 200 and isinstance(lic, dict):
        lic_info = lic.get("license", {})
        lic_name = lic_info.get("name") or lic_info.get("spdx_id") or "Unknown"
        lines.append(f"\nLicense: {lic_name}")

    if s_rel == 200 and isinstance(releases, list) and releases:
        lines.append(f"\nReleases: {len(releases)} (showing latest)")
        for rel in releases[:3]:
            tag = rel.get("tag_name", "?")
            date = (rel.get("published_at") or "")[:10]
            name = rel.get("name", "")
            lines.append(f"  {tag} ({date}): {name[:100]}")

    if s_lang == 200 and isinstance(langs, dict) and langs:
        total = sum(langs.values())
        lang_pcts = [(k, round(v / total * 100, 1)) for k, v in
                     sorted(langs.items(), key=lambda x: x[1], reverse=True)[:5]]
        lines.append(f"\nLanguages: {', '.join(f'{k} ({v}%)' for k, v in lang_pcts)}")

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
    return _make_enrichment(initiative, "git_deep", f"https://github.com/{org}/{repo}", text)
