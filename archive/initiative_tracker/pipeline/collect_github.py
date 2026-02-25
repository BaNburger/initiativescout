from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from typing import Any

import requests
from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import Initiative, InitiativeSource
from initiative_tracker.pipeline.dd_common import make_evidence, stage_from_text
from initiative_tracker.store import upsert_dd_tech_fact
from initiative_tracker.utils import canonicalize_url, clip

GITHUB_API_BASE = "https://api.github.com"


def _find_github_targets(initiative: Initiative, sources: list[InitiativeSource]) -> tuple[str, str, str]:
    candidates = [initiative.primary_url]
    for source in sources:
        candidates.append(source.external_url)
        candidates.append(source.source_url)

    for raw_url in candidates:
        url = canonicalize_url(raw_url)
        if "github.com" not in url.casefold():
            continue
        path = url.split("github.com", 1)[1].strip("/")
        if not path:
            continue
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1], url
        return parts[0], "", url

    return "", "", ""


def _github_get(path: str, *, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, Any] | list[Any] | None]:
    try:
        response = requests.get(f"{GITHUB_API_BASE}{path}", headers=headers, timeout=timeout)
        if response.status_code >= 400:
            return response.status_code, None
        return response.status_code, response.json()
    except Exception:  # noqa: BLE001
        return 0, None


def _collect_repo_metrics(org: str, repo: str, *, timeout: float, headers: dict[str, str]) -> dict[str, Any]:
    metrics = {
        "repo_count": 0,
        "contributors": 0,
        "commits_90d": 0,
        "ci_present": False,
        "test_signal": 0.0,
        "benchmark_artifacts": 0,
        "prototype_stage": "research",
        "ip_indicators": [],
        "evidences": [],
    }

    if not org:
        return metrics

    repo_payload: dict[str, Any] = {}
    if repo:
        status, payload = _github_get(f"/repos/{org}/{repo}", headers=headers, timeout=timeout)
        if status == 200 and isinstance(payload, dict):
            repo_payload = payload
            metrics["repo_count"] = 1
            description = str(payload.get("description") or "")
            topics = payload.get("topics") or []
            topic_text = " ".join(str(item) for item in topics)
            metrics["prototype_stage"] = stage_from_text(" ".join([description, topic_text]))
            if any(token in description.casefold() for token in ["benchmark", "latency", "accuracy", "throughput"]):
                metrics["benchmark_artifacts"] += 1
            if any(token in description.casefold() for token in ["patent", "license", "publication"]):
                metrics["ip_indicators"].append("ip_keyword_in_description")
            metrics["evidences"].append(
                make_evidence(
                    source_type="github_api",
                    source_url=str(payload.get("html_url") or f"https://github.com/{org}/{repo}"),
                    snippet=f"Repo stars={payload.get('stargazers_count', 0)}, forks={payload.get('forks_count', 0)}",
                    doc_id="repo_summary",
                )
            )

    if repo:
        since = (datetime.now(tz=UTC) - timedelta(days=90)).isoformat()
        status, contributors = _github_get(
            f"/repos/{org}/{repo}/contributors?per_page=100",
            headers=headers,
            timeout=timeout,
        )
        if status == 200 and isinstance(contributors, list):
            metrics["contributors"] = len(contributors)
            metrics["evidences"].append(
                make_evidence(
                    source_type="github_api",
                    source_url=f"https://github.com/{org}/{repo}/graphs/contributors",
                    snippet=f"Contributors discovered: {len(contributors)}",
                    doc_id="contributors",
                )
            )

        status, commits = _github_get(
            f"/repos/{org}/{repo}/commits?per_page=100&since={since}",
            headers=headers,
            timeout=timeout,
        )
        if status == 200 and isinstance(commits, list):
            metrics["commits_90d"] = len(commits)
            metrics["evidences"].append(
                make_evidence(
                    source_type="github_api",
                    source_url=f"https://github.com/{org}/{repo}/commits",
                    snippet=f"Commits in 90 days: {len(commits)}",
                    doc_id="commit_velocity_90d",
                )
            )

        status, workflows = _github_get(
            f"/repos/{org}/{repo}/contents/.github/workflows",
            headers=headers,
            timeout=timeout,
        )
        if status == 200 and isinstance(workflows, list) and workflows:
            metrics["ci_present"] = True
            metrics["test_signal"] = 3.0
        elif "test" in str(repo_payload.get("description") or "").casefold() or "ci" in str(repo_payload.get("description") or "").casefold():
            metrics["test_signal"] = 1.0

    if repo and metrics["repo_count"] == 0:
        metrics["evidences"].append(
            make_evidence(
                source_type="github_api",
                source_url=f"https://github.com/{org}/{repo}",
                snippet="Repo metadata not accessible (possibly private or missing).",
                doc_id="repo_missing",
                confidence=0.2,
            )
        )

    return metrics


def collect_github(
    *,
    initiative_id: int | None = None,
    all_initiatives: bool = False,
    settings: Settings | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "processed": 0,
        "updated": 0,
        "missing_github": 0,
        "errors": 0,
    }

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": cfg.user_agent,
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "collect_github")
        try:
            query = select(Initiative)
            if initiative_id is not None:
                query = query.where(Initiative.id == initiative_id)
            initiatives = session.execute(query).scalars().all()
            if initiative_id is None and not all_initiatives:
                initiatives = initiatives[:25]

            source_rows = session.execute(select(InitiativeSource)).scalars().all()
            source_map: dict[int, list[InitiativeSource]] = {}
            for source in source_rows:
                source_map.setdefault(source.initiative_id, []).append(source)

            for initiative in initiatives:
                details["processed"] += 1
                org, repo, source_url = _find_github_targets(initiative, source_map.get(initiative.id, []))
                if not org:
                    details["missing_github"] += 1
                    continue

                metrics = _collect_repo_metrics(org, repo, timeout=cfg.request_timeout_seconds, headers=headers)
                confidence = clip(
                    0.2
                    + (0.2 if metrics["repo_count"] > 0 else 0.0)
                    + (0.2 if metrics["contributors"] > 1 else 0.0)
                    + (0.2 if metrics["commits_90d"] > 5 else 0.0)
                    + (0.2 if metrics["ci_present"] else 0.0),
                    0.0,
                    1.0,
                )

                upsert_dd_tech_fact(
                    session,
                    initiative_id=initiative.id,
                    github_org=org,
                    github_repo=repo,
                    repo_count=int(metrics["repo_count"]),
                    contributor_count=int(metrics["contributors"]),
                    commit_velocity_90d=float(metrics["commits_90d"]),
                    ci_present=bool(metrics["ci_present"]),
                    test_signal=float(metrics["test_signal"]),
                    benchmark_artifacts=int(metrics["benchmark_artifacts"]),
                    prototype_stage=str(metrics["prototype_stage"]),
                    ip_indicators=[str(item) for item in metrics["ip_indicators"]],
                    evidence=[dict(item) for item in metrics["evidences"]],
                    source_type="github_api",
                    source_url=source_url,
                    confidence=confidence,
                )
                details["updated"] += 1

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            details["errors"] += 1
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
