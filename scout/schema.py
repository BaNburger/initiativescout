"""Entity type schemas — single source of truth for field definitions.

Each entity type has a schema that drives the entire system:
- UI columns, filters, and detail sections
- API response shapes (summary, detail, compact)
- Enricher and scoring configuration

Built-in types (initiative, professor) have hardcoded schemas.
Custom types build schemas from DB config + reasonable defaults.
"""
from __future__ import annotations

from typing import Any


def _cols(*specs) -> list[dict]:
    """Build column list from compact (key, label, type, sort_key) tuples."""
    result = []
    for s in specs:
        key, label, col_type = s[0], s[1], s[2]
        sort_key = s[3] if len(s) > 3 else key
        col: dict[str, Any] = {"key": key, "label": label, "type": col_type}
        if sort_key:
            col["sort"] = sort_key
        if col_type == "text":
            col["editable"] = True
        result.append(col)
    return result


_VERDICT_OPTIONS = [
    {"value": "reach_out_now", "label": "Reach Out Now"},
    {"value": "reach_out_soon", "label": "Reach Out Soon"},
    {"value": "monitor", "label": "Monitor"},
    {"value": "skip", "label": "Skip"},
    {"value": "unscored", "label": "Unscored"},
]


# ---------------------------------------------------------------------------
# Built-in schemas
# ---------------------------------------------------------------------------

_INITIATIVE_SCHEMA: dict[str, Any] = {
    "label": "Initiative",
    "label_plural": "Initiatives",

    "columns": _cols(
        ("name", "Initiative", "text", "name"),
        ("uni", "Uni", "text", "uni"),
        ("verdict", "Verdict", "verdict", "verdict"),
        ("grade_team", "Team", "grade", "grade_team"),
        ("grade_tech", "Tech", "grade", "grade_tech"),
        ("grade_opportunity", "Opp", "grade", "grade_opportunity"),
        ("classification", "Class", "badge", None),
    ),

    "filters": [
        {"key": "verdict", "label": "All Verdicts", "type": "verdict", "options": _VERDICT_OPTIONS},
        {"key": "classification", "label": "All Types", "type": "dynamic", "source": "classification"},
        {"key": "uni", "label": "All Unis", "type": "dynamic", "source": "uni"},
        {"key": "faculty", "label": "All Faculties", "type": "api", "endpoint": "/api/faculties"},
    ],

    # Detail header: editable fields shown in the meta bar
    "meta_fields": ["uni", "sector", "mode", "relevance"],

    # Links section in detail view
    "link_fields": [
        {"key": "website", "label": "Website"},
        {"key": "email", "label": "Email"},
        {"key": "linkedin", "label": "LinkedIn"},
        {"key": "github_org", "label": "GitHub"},
        {"key": "team_page", "label": "Team"},
    ],

    # Additional info section in detail view
    "info_fields": [
        {"key": "team_size", "label": "Team size"},
        {"key": "key_repos", "label": "Key repos"},
        {"key": "sponsors", "label": "Sponsors"},
        {"key": "competitions", "label": "Competitions"},
    ],

    # API response field lists
    "summary_fields": [
        "id", "name", "uni", "faculty", "sector", "mode", "description",
        "website", "email", "relevance", "sheet_source",
    ],
    "summary_extra": [
        "technology_domains", "categories", "member_count",
        "outreach_now_score", "venture_upside_score",
    ],
    "detail_fields": [
        "team_page", "team_size", "linkedin", "github_org", "key_repos",
        "sponsors", "competitions", "market_domains", "member_examples",
        "member_roles", "github_repo_count", "github_contributors",
        "github_commits_90d", "github_ci_present", "huggingface_model_hits",
        "openalex_hits", "semantic_scholar_hits", "dd_key_roles",
        "dd_references_count", "dd_is_investable", "profile_coverage_score",
        "known_url_count", "linkedin_hits", "researchgate_hits",
    ],
    "updatable_fields": [
        "name", "uni", "faculty", "sector", "mode", "description", "website", "email",
        "relevance", "team_page", "team_size", "linkedin", "github_org",
        "key_repos", "sponsors", "competitions",
    ],
    "searchable_fields": [
        "name", "description", "sector", "technology_domains",
        "categories", "market_domains", "faculty",
    ],
    "compact_fields": [
        "id", "name", "uni", "faculty", "sector", "mode", "description",
        "website", "email", "relevance", "sheet_source",
        "enriched", "enriched_at",
        "verdict", "score", "classification",
        "grade_team", "grade_tech", "grade_opportunity",
        "technology_domains", "categories", "member_count",
        "outreach_now_score", "venture_upside_score",
        "custom_fields",
    ],

    # Scoring config (used by scorer.py get_entity_config)
    "dimensions": {"team": "Team", "tech": "Tech", "opportunity": "Opportunity"},
    "enrichers": [
        "website", "team_page", "github", "extra_links",
        "structured_data", "tech_stack", "dns", "sitemap", "careers", "git_deep",
        "openalex", "wikidata",
    ],
    "enricher_targets": {
        "github": ["github_repo_count", "github_contributors", "github_commits_90d", "github_ci_present"],
        "structured_data": ["email", "description", "linkedin", "github_org", "member_count"],
        "openalex": ["openalex_hits"],
        "wikidata": ["website", "github_org", "member_count"],
    },
    "context": "Munich student initiatives",

    # Enrichable fields: standard fields the LLM should fill via submit_enrichment
    "enrichable_fields": {
        # URLs & contact
        "website": {"label": "Website", "type": "url"},
        "email": {"label": "Email", "type": "email"},
        "linkedin": {"label": "LinkedIn", "type": "url"},
        "github_org": {"label": "GitHub Org", "type": "url"},
        "team_page": {"label": "Team Page", "type": "url"},
        # Team signals
        "member_count": {"label": "Member Count", "type": "int"},
        "team_size": {"label": "Team Size", "type": "text"},
        "member_examples": {"label": "Key Members", "type": "text"},
        "member_roles": {"label": "Member Roles", "type": "text"},
        # GitHub signals
        "github_repo_count": {"label": "GitHub Repos", "type": "int"},
        "github_contributors": {"label": "GitHub Contributors", "type": "int"},
        "github_commits_90d": {"label": "GitHub Commits (90d)", "type": "int"},
        "github_ci_present": {"label": "Has CI/CD", "type": "bool"},
        "key_repos": {"label": "Key Repos", "type": "text"},
        # Classification
        "technology_domains": {"label": "Tech Domains", "type": "text"},
        "market_domains": {"label": "Market Domains", "type": "text"},
        "categories": {"label": "Categories", "type": "text"},
        # Research signals
        "huggingface_model_hits": {"label": "HuggingFace Hits", "type": "int"},
        "openalex_hits": {"label": "OpenAlex Hits", "type": "int"},
        "semantic_scholar_hits": {"label": "Semantic Scholar Hits", "type": "int"},
        # Context
        "sponsors": {"label": "Sponsors", "type": "text"},
        "competitions": {"label": "Competitions", "type": "text"},
        "description": {"label": "Description", "type": "text"},
    },
}


_PROFESSOR_SCHEMA: dict[str, Any] = {
    "label": "Professor",
    "label_plural": "Professors",

    "columns": _cols(
        ("name", "Professor", "text", "name"),
        ("uni", "University", "text", "uni"),
        ("faculty", "Faculty", "text", "faculty"),
        ("verdict", "Verdict", "verdict", "verdict"),
        ("grade_team", "Group", "grade", "grade_team"),
        ("grade_tech", "Research", "grade", "grade_tech"),
        ("grade_opportunity", "Collab", "grade", "grade_opportunity"),
        ("classification", "Class", "badge", None),
    ),

    "filters": [
        {"key": "verdict", "label": "All Verdicts", "type": "verdict", "options": _VERDICT_OPTIONS},
        {"key": "classification", "label": "All Types", "type": "dynamic", "source": "classification"},
        {"key": "uni", "label": "All Universities", "type": "dynamic", "source": "uni"},
        {"key": "faculty", "label": "All Faculties", "type": "api", "endpoint": "/api/faculties"},
    ],

    "meta_fields": ["uni", "faculty"],
    "link_fields": [
        {"key": "website", "label": "Website"},
        {"key": "email", "label": "Email"},
        {"key": "linkedin", "label": "LinkedIn"},
    ],
    "info_fields": [],

    "summary_fields": [
        "id", "name", "uni", "faculty", "description", "website", "email",
    ],
    "summary_extra": [],
    "detail_fields": ["linkedin"],
    "updatable_fields": [
        "name", "uni", "faculty", "description", "website", "email",
    ],
    "searchable_fields": ["name", "description", "faculty"],
    "compact_fields": [
        "id", "name", "uni", "faculty", "description", "website",
        "enriched", "enriched_at",
        "verdict", "score", "classification",
        "grade_team", "grade_tech", "grade_opportunity",
        "custom_fields",
    ],

    "dimensions": {"team": "Research Group", "tech": "Research Output", "opportunity": "Collaboration Potential"},
    "enrichers": ["website", "extra_links", "structured_data", "dns", "sitemap", "openalex", "wikidata"],
    "context": "TUM professors",

    # Enrichable fields: professor-specific
    "enrichable_fields": {
        "website": {"label": "Website", "type": "url"},
        "email": {"label": "Email", "type": "email"},
        "linkedin": {"label": "LinkedIn", "type": "url"},
        "description": {"label": "Research Focus", "type": "text"},
        "technology_domains": {"label": "Research Areas", "type": "text"},
        "member_count": {"label": "Group Size", "type": "int"},
        "member_examples": {"label": "Key Researchers", "type": "text"},
        "openalex_hits": {"label": "OpenAlex Hits", "type": "int"},
        "semantic_scholar_hits": {"label": "Semantic Scholar Hits", "type": "int"},
        "huggingface_model_hits": {"label": "HuggingFace Hits", "type": "int"},
        "github_org": {"label": "GitHub", "type": "url"},
    },
}


_BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "initiative": _INITIATIVE_SCHEMA,
    "professor": _PROFESSOR_SCHEMA,
}


def get_schema(entity_type: str | None = None) -> dict[str, Any]:
    """Return the complete schema for the current or specified entity type."""
    if entity_type is None:
        from scout.db import get_entity_type
        entity_type = get_entity_type()

    if entity_type in _BUILTIN_SCHEMAS:
        schema = dict(_BUILTIN_SCHEMAS[entity_type])
    else:
        schema = _build_custom_schema(entity_type)

    schema["entity_type"] = entity_type

    # Note: runtime-extended enrichable_fields (extra_enrichable_fields in DB _meta)
    # are merged by get_entity_config() in scorer.py, NOT here. Calling DB functions
    # from get_schema() risks deadlock since _lock in db.py is not re-entrant.

    return schema


def _build_custom_schema(entity_type: str) -> dict[str, Any]:
    """Build schema for a custom entity type from DB config + defaults."""
    try:
        from scout.db import get_entity_config_json
        cfg = get_entity_config_json()
    except Exception:
        cfg = {}

    label = cfg.get("label", entity_type.replace("_", " ").title())
    label_plural = cfg.get("label_plural", label + "s")

    dims = cfg.get("dimensions", {"team": "Dimension 1", "tech": "Dimension 2", "opportunity": "Dimension 3"})
    if isinstance(dims, list):
        dims = {d: d.replace("_", " ").title() for d in dims}

    dim_labels = list(dims.values())
    grade_keys = ["grade_team", "grade_tech", "grade_opportunity"]
    grade_cols = [
        {"key": grade_keys[i], "label": dl[:8], "type": "grade", "sort": grade_keys[i]}
        for i, dl in enumerate(dim_labels) if i < len(grade_keys)
    ]

    columns = [
        {"key": "name", "label": label, "type": "text", "sort": "name", "editable": True},
        {"key": "verdict", "label": "Verdict", "type": "verdict", "sort": "verdict"},
        *grade_cols,
        {"key": "classification", "label": "Class", "type": "badge"},
    ]

    return {
        "label": label,
        "label_plural": label_plural,
        "columns": columns,
        "filters": [
            {"key": "verdict", "label": "All Verdicts", "type": "verdict", "options": _VERDICT_OPTIONS},
            {"key": "classification", "label": "All Types", "type": "dynamic", "source": "classification"},
        ],
        "meta_fields": cfg.get("meta_fields", []),
        "link_fields": cfg.get("link_fields", [
            {"key": "website", "label": "Website"},
            {"key": "email", "label": "Email"},
        ]),
        "info_fields": cfg.get("info_fields", []),
        "summary_fields": cfg.get("summary_fields", ["id", "name", "description", "website"]),
        "summary_extra": cfg.get("summary_extra", []),
        "detail_fields": cfg.get("detail_fields", []),
        "updatable_fields": cfg.get("updatable_fields", ["name", "description", "website", "email"]),
        "searchable_fields": cfg.get("searchable_fields", ["name", "description"]),
        "compact_fields": cfg.get("compact_fields", [
            "id", "name", "description", "website", "enriched", "enriched_at",
            "verdict", "score", "classification",
            "grade_team", "grade_tech", "grade_opportunity", "custom_fields",
        ]),
        "dimensions": dims,
        "enrichers": cfg.get("enrichers", ["website", "extra_links", "structured_data"]),
        "context": cfg.get("context", entity_type),
        "enrichable_fields": cfg.get("enrichable_fields", {
            "website": {"label": "Website", "type": "url"},
            "email": {"label": "Email", "type": "email"},
            "linkedin": {"label": "LinkedIn", "type": "url"},
            "description": {"label": "Description", "type": "text"},
        }),
    }
