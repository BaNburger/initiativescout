from __future__ import annotations

from typing import Any

import requests

from initiative_tracker.models import Initiative, InitiativePerson, InitiativeSource, Person
from initiative_tracker.pipeline.dd_common import has_keyword, make_evidence
from initiative_tracker.store import get_json_list
from initiative_tracker.utils import clip

ALLOWED_SOURCE_KEYS = {
    "github",
    "openalex",
    "semantic_scholar",
    "huggingface",
    "linkedin_safe",
    "researchgate_safe",
}


def parse_source_keys(raw: str | None, *, default_csv: str) -> set[str]:
    value = (raw or default_csv or "").strip()
    if not value:
        return {"github", "openalex", "semantic_scholar", "huggingface"}
    out: set[str] = set()
    for token in value.replace(";", ",").split(","):
        key = token.strip().casefold()
        if key in ALLOWED_SOURCE_KEYS:
            out.add(key)
    return out or {"github", "openalex", "semantic_scholar", "huggingface"}


def _http_get_json(url: str, *, params: dict[str, Any], timeout: float, user_agent: str) -> dict[str, Any] | list[Any] | None:
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        if resp.status_code >= 400:
            return None
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def fetch_openalex_signals(*, initiative_name: str, timeout: float, user_agent: str) -> dict[str, Any]:
    payload = _http_get_json(
        "https://api.openalex.org/works",
        params={"search": initiative_name, "per-page": 5},
        timeout=timeout,
        user_agent=user_agent,
    )
    if not isinstance(payload, dict):
        return {"evidence": [], "publication_count": 0, "citation_total": 0, "recent_citations": 0}

    works = payload.get("results")
    if not isinstance(works, list):
        works = []

    publication_count = len(works)
    citation_total = 0
    recent_citations = 0
    venues: list[str] = []
    evidence: list[dict[str, Any]] = []

    for work in works[:5]:
        if not isinstance(work, dict):
            continue
        cited = int(work.get("cited_by_count") or 0)
        citation_total += cited
        if int(work.get("publication_year") or 0) >= 2024:
            recent_citations += cited

        display = str(work.get("display_name") or "")
        host_venue = work.get("host_venue") if isinstance(work.get("host_venue"), dict) else {}
        venue_name = str(host_venue.get("display_name") or "")
        if venue_name:
            venues.append(venue_name)
        source_url = str(work.get("id") or "")
        evidence.append(
            make_evidence(
                source_type="openalex",
                source_url=source_url,
                snippet=f"{display[:120]} | citations={cited} | venue={venue_name[:80]}",
                doc_id="openalex_work",
                confidence=0.78,
            )
        )

    venue_quality = 1.0
    if any(has_keyword(venue, ["nature", "science", "neurips", "icml", "iclr", "ieee", "acm"]) for venue in venues):
        venue_quality = 4.2
    elif venues:
        venue_quality = 3.0

    return {
        "evidence": evidence,
        "publication_count": publication_count,
        "citation_total": citation_total,
        "recent_citations": recent_citations,
        "venue_quality": venue_quality,
    }


def fetch_semantic_scholar_signals(*, initiative_name: str, timeout: float, user_agent: str) -> dict[str, Any]:
    payload = _http_get_json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": initiative_name, "limit": 5, "fields": "title,year,citationCount,authors,url"},
        timeout=timeout,
        user_agent=user_agent,
    )
    if not isinstance(payload, dict):
        return {"evidence": [], "paper_count": 0, "citation_total": 0, "collaboration_depth": 1.0}

    papers = payload.get("data")
    if not isinstance(papers, list):
        papers = []

    paper_count = len(papers)
    citation_total = 0
    author_counts: list[int] = []
    evidence: list[dict[str, Any]] = []

    for paper in papers[:5]:
        if not isinstance(paper, dict):
            continue
        citation = int(paper.get("citationCount") or 0)
        citation_total += citation
        authors = paper.get("authors") if isinstance(paper.get("authors"), list) else []
        author_counts.append(len(authors))
        title = str(paper.get("title") or "")
        source_url = str(paper.get("url") or "")
        evidence.append(
            make_evidence(
                source_type="semantic_scholar",
                source_url=source_url,
                snippet=f"{title[:120]} | citations={citation} | authors={len(authors)}",
                doc_id="s2_paper",
                confidence=0.76,
            )
        )

    collaboration_depth = clip(1.0 + (sum(author_counts) / max(1, len(author_counts))) / 3.0, 1.0, 5.0)
    return {
        "evidence": evidence,
        "paper_count": paper_count,
        "citation_total": citation_total,
        "collaboration_depth": collaboration_depth,
    }


def fetch_huggingface_signals(*, initiative_name: str, timeout: float, user_agent: str) -> dict[str, Any]:
    payload = _http_get_json(
        "https://huggingface.co/api/models",
        params={"search": initiative_name, "limit": 10, "full": "true"},
        timeout=timeout,
        user_agent=user_agent,
    )
    if not isinstance(payload, list):
        return {
            "evidence": [],
            "model_count": 0,
            "like_total": 0,
            "download_total": 0,
            "model_card_quality": 1.0,
            "license_quality": 1.0,
        }

    models = [item for item in payload if isinstance(item, dict)]
    like_total = 0
    download_total = 0
    card_count = 0
    licensed_count = 0
    evidence: list[dict[str, Any]] = []

    for model in models[:10]:
        likes = int(model.get("likes") or 0)
        downloads = int(model.get("downloads") or 0)
        like_total += likes
        download_total += downloads
        if model.get("cardData"):
            card_count += 1
        if model.get("license"):
            licensed_count += 1
        model_id = str(model.get("id") or "")
        source_url = f"https://huggingface.co/{model_id}" if model_id else ""
        evidence.append(
            make_evidence(
                source_type="huggingface",
                source_url=source_url,
                snippet=f"model={model_id[:80]} | likes={likes} | downloads={downloads}",
                doc_id="hf_model",
                confidence=0.74,
            )
        )

    model_card_quality = clip(1.0 + (card_count / max(1, len(models))) * 4.0, 1.0, 5.0)
    license_quality = clip(1.0 + (licensed_count / max(1, len(models))) * 4.0, 1.0, 5.0)

    return {
        "evidence": evidence,
        "model_count": len(models),
        "like_total": like_total,
        "download_total": download_total,
        "model_card_quality": model_card_quality,
        "license_quality": license_quality,
    }


def collect_linkedin_safe_signals(
    *,
    initiative: Initiative,
    initiative_sources: list[InitiativeSource],
    people: list[Person],
    links: list[InitiativePerson],
) -> dict[str, Any]:
    urls: set[str] = set()
    for source in initiative_sources:
        if "linkedin.com" in (source.source_url or "").casefold():
            urls.add(source.source_url)
        if "linkedin.com" in (source.external_url or "").casefold():
            urls.add(source.external_url)

    linked_ids = {link.person_id for link in links if link.initiative_id == initiative.id}
    for person in people:
        if person.id not in linked_ids:
            continue
        for url in get_json_list(person.source_urls_json):
            if "linkedin.com" in url.casefold():
                urls.add(url)

    evidence = [
        make_evidence(
            source_type="linkedin_safe",
            source_url=url,
            snippet="LinkedIn profile URL provided via safe/manual source (no scraping).",
            doc_id="linkedin_safe",
            confidence=0.7,
        )
        for url in sorted(urls)
    ]
    return {
        "evidence": evidence,
        "profile_count": len(urls),
    }


def collect_researchgate_safe_signals(*, initiative: Initiative, initiative_sources: list[InitiativeSource]) -> dict[str, Any]:
    urls: set[str] = set()
    for source in initiative_sources:
        if "researchgate.net" in (source.source_url or "").casefold():
            urls.add(source.source_url)
        if "researchgate.net" in (source.external_url or "").casefold():
            urls.add(source.external_url)

    evidence = [
        make_evidence(
            source_type="researchgate_safe",
            source_url=url,
            snippet="ResearchGate URL provided via safe/manual source; metadata only.",
            doc_id="researchgate_safe",
            confidence=0.68,
        )
        for url in sorted(urls)
    ]
    return {
        "evidence": evidence,
        "profile_count": len(urls),
    }
