from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.db import finish_pipeline_run, init_db, session_scope, start_pipeline_run
from initiative_tracker.models import Initiative, InitiativePerson, InitiativeSource, Person
from initiative_tracker.sources.people_markdown import parse_people_from_markdown
from initiative_tracker.sources.people_web import crawl_people_from_website
from initiative_tracker.store import (
    add_talent_score,
    get_json_list,
    link_person_to_initiative,
    upsert_person,
)
from initiative_tracker.utils import clip, normalize_name, unique_list

DEFAULT_PEOPLE_FILES = [
    "docs/research/tier1_contacts.md",
    "docs/research/alumni_network.md",
    "docs/obsidian/Student Initiatives - Contacts.md",
    "docs/obsidian/Student Initiatives - Alumni Network.md",
]


def _people_markdown_files(settings: Settings) -> list[Path]:
    return [settings.project_root / rel for rel in DEFAULT_PEOPLE_FILES]


def _match_initiative_ids(initiatives: list[Initiative], names: list[str]) -> list[int]:
    if not names:
        return []

    matched: set[int] = set()
    for raw in names:
        normalized = normalize_name(raw)
        if not normalized:
            continue
        best_score = 0
        best_id: int | None = None
        for item in initiatives:
            score = max(
                fuzz.ratio(normalized, item.normalized_name),
                fuzz.partial_ratio(normalized, item.normalized_name),
            )
            if score > best_score:
                best_score = score
                best_id = item.id
        if best_id is not None and best_score >= 88:
            matched.add(best_id)
    return sorted(matched)


def _choose_website_url(initiative: Initiative, sources: list[InitiativeSource], social_domains: set[str]) -> str:
    candidates = [initiative.primary_url, *(source.external_url for source in sources)]
    for candidate in unique_list([c for c in candidates if c]):
        lowered = candidate.casefold()
        if any(domain in lowered for domain in social_domains):
            continue
        if "tum.de" in lowered or "lmu.de" in lowered or "hm.edu" in lowered:
            continue
        return candidate
    return ""


def _score_person(
    *,
    person_type: str,
    role: str,
    contact_channels: list[str],
    linked_initiative_count: int,
    reason_count: int,
) -> dict[str, float]:
    has_email = any("@" in channel for channel in contact_channels)
    has_linkedin = any("linkedin.com" in channel.casefold() for channel in contact_channels)
    has_web = any(channel.startswith("http") for channel in contact_channels)

    reachability = 1.0 + (1.2 if has_email else 0.0) + (0.8 if has_linkedin else 0.0) + (0.3 if has_web else 0.0)
    reachability += min(1.7, 0.25 * len(contact_channels))

    role_l = role.casefold()
    leadership_bonus = 1.2 if any(t in role_l for t in ["lead", "founder", "chair", "president", "cto", "ceo", "captain"]) else 0.4
    operator_strength = 1.0 + leadership_bonus + min(2.0, 0.4 * linked_initiative_count) + min(0.8, 0.2 * reason_count)

    investor_relevance = 1.2
    if person_type == "alumni_angel":
        investor_relevance = 3.2 + (0.8 if has_linkedin else 0.0) + min(1.0, 0.2 * reason_count)

    network_score = 1.0 + min(1.8, 0.35 * linked_initiative_count) + min(1.8, 0.25 * len(contact_channels))

    return {
        "reachability": clip(reachability, 1.0, 5.0),
        "operator_strength": clip(operator_strength, 1.0, 5.0),
        "investor_relevance": clip(investor_relevance, 1.0, 5.0),
        "network_score": clip(network_score, 1.0, 5.0),
    }


def ingest_people(
    *,
    crawl_mode: str = "safe",
    max_pages: int = 12,
    settings: Settings | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    init_db(db_url)

    details: dict[str, Any] = {
        "crawl_mode": crawl_mode,
        "max_pages": max_pages,
        "files_processed": 0,
        "people_upserted": 0,
        "links_created": 0,
        "website_people": 0,
        "talent_scores_written": 0,
    }

    with session_scope(db_url) as session:
        run = start_pipeline_run(session, "ingest_people")
        try:
            initiatives = session.execute(select(Initiative)).scalars().all()
            sources = session.execute(select(InitiativeSource)).scalars().all()
            source_by_initiative: dict[int, list[InitiativeSource]] = defaultdict(list)
            for source in sources:
                source_by_initiative[source.initiative_id].append(source)

            markdown_people: list[dict[str, Any]] = []
            for path in _people_markdown_files(cfg):
                if not path.exists():
                    continue
                details["files_processed"] += 1
                markdown_people.extend(parse_people_from_markdown(path))

            for record in markdown_people:
                person = upsert_person(
                    session,
                    name=record["name"],
                    person_type=record.get("person_type") or "operator",
                    headline=record.get("headline") or "",
                    contact_channels=record.get("contact_channels") or [],
                    source_urls=record.get("source_urls") or [record.get("source_path") or ""],
                    confidence=0.85,
                )
                details["people_upserted"] += 1

                linked_ids = _match_initiative_ids(initiatives, record.get("initiative_names") or [])
                for initiative_id in linked_ids:
                    link_person_to_initiative(
                        session,
                        initiative_id=initiative_id,
                        person_id=person.id,
                        role=record.get("role") or "",
                        is_primary_contact=True,
                        source_type="people_markdown",
                        source_url=record.get("source_path") or "",
                    )
                    details["links_created"] += 1

            for initiative in initiatives:
                website_url = _choose_website_url(initiative, source_by_initiative.get(initiative.id, []), cfg.social_domains_to_skip)
                if not website_url:
                    continue
                crawl_budget = max_pages if crawl_mode == "max-reach" else min(max_pages, 6)
                crawled = crawl_people_from_website(
                    website_url,
                    settings=cfg,
                    crawl_mode=crawl_mode,
                    max_pages=crawl_budget,
                )
                for record in crawled["records"]:
                    person = upsert_person(
                        session,
                        name=record["name"],
                        person_type=record.get("person_type") or "operator",
                        headline=record.get("headline") or "",
                        contact_channels=record.get("contact_channels") or [],
                        source_urls=record.get("source_urls") or [website_url],
                        confidence=0.55 if crawl_mode == "safe" else 0.5,
                    )
                    link_person_to_initiative(
                        session,
                        initiative_id=initiative.id,
                        person_id=person.id,
                        role=record.get("role") or "website_lead",
                        is_primary_contact=False,
                        source_type="people_web",
                        source_url=website_url,
                    )
                    details["people_upserted"] += 1
                    details["links_created"] += 1
                    details["website_people"] += 1

            people = session.execute(select(Person)).scalars().all()
            links = session.execute(select(InitiativePerson)).scalars().all()
            links_by_person: dict[int, list[InitiativePerson]] = defaultdict(list)
            for link in links:
                links_by_person[link.person_id].append(link)

            for person in people:
                person_links = links_by_person.get(person.id, [])
                roles = [link.role for link in person_links if link.role]
                channels = get_json_list(person.contact_channels_json)
                source_urls = get_json_list(person.source_urls_json)
                reasons = unique_list([person.headline, *roles])

                scored = _score_person(
                    person_type=person.person_type,
                    role=roles[0] if roles else "",
                    contact_channels=channels,
                    linked_initiative_count=len({link.initiative_id for link in person_links}),
                    reason_count=len(reasons),
                )

                if person.person_type == "alumni_angel":
                    talent_type = "alumni_angels"
                    composite = 0.45 * scored["investor_relevance"] + 0.3 * scored["network_score"] + 0.25 * scored["reachability"]
                else:
                    talent_type = "operators"
                    composite = 0.45 * scored["operator_strength"] + 0.3 * scored["reachability"] + 0.25 * scored["network_score"]

                confidence = clip(0.3 + 0.1 * len(source_urls) + 0.08 * len(person_links), 0.0, 1.0)
                add_talent_score(
                    session,
                    person_id=person.id,
                    talent_type=talent_type,
                    reachability=scored["reachability"],
                    operator_strength=scored["operator_strength"],
                    investor_relevance=scored["investor_relevance"],
                    network_score=scored["network_score"],
                    composite_score=clip(composite, 1.0, 5.0),
                    confidence=confidence,
                    reasons=reasons,
                )
                details["talent_scores_written"] += 1

            finish_pipeline_run(session, run, status="success", details=details)
            return details
        except Exception as exc:  # noqa: BLE001
            finish_pipeline_run(session, run, status="failed", details=details, error_message=str(exc))
            raise
