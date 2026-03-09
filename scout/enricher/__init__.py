"""Enrichment pipeline — split into focused modules.

Submodules:
    _core       Constants, optional deps, helpers, caching, HTTP
    _website    Website, team page, extra links, career page enrichers
    _github     GitHub org/repo enrichers
    _discovery  DuckDuckGo URL discovery
    _metadata   Structured data, tech stack, DNS, sitemap enrichers
    _apis       Free API enrichers: OpenAlex, Wikidata

All public symbols are re-exported here so existing ``from scout.enricher import …``
imports continue to work unchanged.
"""

from scout.enricher._core import (  # noqa: F401
    _CRAWL4AI_AVAILABLE,
    _DDGS_AVAILABLE,
    _EXTRUCT_AVAILABLE,
    _MAX_TEXT,
    _TRAFILATURA_AVAILABLE,
    _extract_text,
    _html_cache,
    infer_fields_from_text,
)
from scout.enricher._discovery import (  # noqa: F401
    _DDGRateLimiter,
    _PLATFORM_PATTERNS,
    discover_urls,
)
from scout.enricher._apis import enrich_openalex, enrich_wikidata  # noqa: F401
from scout.enricher._github import enrich_git_deep, enrich_github  # noqa: F401
from scout.enricher._metadata import (  # noqa: F401
    _detect_tech_stack,
    _extract_structured_data,
    enrich_dns,
    enrich_sitemap,
    enrich_structured_data,
    enrich_tech_stack,
)
from scout.enricher._website import (  # noqa: F401
    _SKIP_LINK_KEYS,
    _crawl4ai_fetch,
    _enrich_page,
    enrich_careers,
    enrich_extra_links,
    enrich_team_page,
    enrich_website,
    open_crawler,
)

__all__ = [
    # Infrastructure
    "open_crawler",
    "_html_cache",
    # Website enrichers
    "enrich_website",
    "enrich_team_page",
    "enrich_extra_links",
    "enrich_careers",
    # GitHub enrichers
    "enrich_github",
    "enrich_git_deep",
    # Discovery
    "discover_urls",
    # Metadata enrichers
    "enrich_structured_data",
    "enrich_tech_stack",
    "enrich_dns",
    "enrich_sitemap",
    # API enrichers
    "enrich_openalex",
    "enrich_wikidata",
    "infer_fields_from_text",
]
