from __future__ import annotations

import re
from typing import Any

from lxml import html

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.sources.common import fetch_html, normalize_whitespace
from initiative_tracker.utils import extract_team_size


def _match_taxonomy(text: str, taxonomy: dict[str, list[str]]) -> dict[str, int]:
    lower = text.casefold()
    out: dict[str, int] = {}
    for domain, keywords in taxonomy.items():
        count = 0
        for keyword in keywords:
            pattern = re.escape(keyword.casefold())
            count += len(re.findall(pattern, lower))
        if count > 0:
            out[domain] = count
    return out


def _team_signal_counts(text: str) -> dict[str, float]:
    lower = text.casefold()
    leadership_terms = ["lead", "leader", "president", "chair", "board", "team", "vorstand", "leitung", "founder"]
    achievement_terms = [
        "winner",
        "award",
        "competition",
        "challenge",
        "finalist",
        "record",
        "preis",
        "sieger",
        "gewonnen",
    ]
    engineering_terms = ["github", "open source", "prototype", "system", "engineering", "robot", "ai", "autonomous"]

    return {
        "leadership_mentions": float(sum(lower.count(term) for term in leadership_terms)),
        "achievement_mentions": float(sum(lower.count(term) for term in achievement_terms)),
        "engineering_mentions": float(sum(lower.count(term) for term in engineering_terms)),
    }


def _market_signal_counts(text: str) -> dict[str, float]:
    lower = text.casefold()
    patterns = {
        "commercial_mentions": ["industry", "customer", "startup", "spinout", "product", "commercial"],
        "problem_solution_mentions": ["solution", "challenge", "problem", "impact", "application", "market"],
        "partnership_mentions": ["partner", "sponsor", "company", "enterprise", "collaboration"],
        "timing_mentions": ["future", "next", "now", "today", "urgent", "trend"],
    }
    out: dict[str, float] = {}
    for key, keywords in patterns.items():
        out[key] = float(sum(lower.count(k) for k in keywords))
    return out


def _build_summary_en(
    name: str,
    technologies: list[str],
    markets: list[str],
    team_signals: dict[str, float],
    team_size: int | None,
) -> str:
    tech_text = ", ".join(technologies[:3]) if technologies else "general technology work"
    market_text = ", ".join(markets[:3]) if markets else "broad commercialization opportunities"
    leadership = int(team_signals.get("leadership_mentions", 0))
    size_text = f"reported team size around {team_size}" if team_size else "team size not explicit"
    return (
        f"{name} shows activity in {tech_text}. Potential market exposure appears in {market_text}. "
        f"Leadership/organization signals are present ({leadership} mentions), with {size_text}."
    )


def analyze_website(
    url: str,
    technology_taxonomy: dict[str, list[str]],
    market_taxonomy: dict[str, list[str]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    html_text = fetch_html(url, cfg, delay_seconds=cfg.website_request_delay_seconds)
    tree = html.fromstring(html_text)

    title = normalize_whitespace(" ".join(tree.xpath("//title//text()")))
    meta_description = normalize_whitespace(" ".join(tree.xpath("//meta[@name='description']/@content")))
    heading_text = normalize_whitespace(" ".join(tree.xpath("//h1//text() | //h2//text() | //h3//text()")))
    paragraph_text = normalize_whitespace(" ".join(tree.xpath("//p//text()")))

    # Cap body text to avoid oversized payloads while preserving enough signals.
    combined_text = " ".join([title, meta_description, heading_text, paragraph_text])[:20000]

    tech_matches = _match_taxonomy(combined_text, technology_taxonomy)
    market_matches = _match_taxonomy(combined_text, market_taxonomy)
    team_counts = _team_signal_counts(combined_text)
    market_counts = _market_signal_counts(combined_text)
    team_size = extract_team_size(combined_text)

    technologies = sorted(tech_matches.keys(), key=lambda x: tech_matches[x], reverse=True)
    markets = sorted(market_matches.keys(), key=lambda x: market_matches[x], reverse=True)
    summary = _build_summary_en("Initiative", technologies, markets, team_counts, team_size)

    return {
        "url": url,
        "title": title,
        "meta_description": meta_description,
        "combined_text": combined_text,
        "technology_matches": tech_matches,
        "market_matches": market_matches,
        "team_counts": team_counts,
        "market_counts": market_counts,
        "team_size": team_size,
        "technologies": technologies,
        "markets": markets,
        "summary_en": summary,
    }
