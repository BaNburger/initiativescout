"""Scoring engine: parallel dimension evaluations with deterministic aggregation.

Architecture
------------
Each entity is scored on configurable dimensions in parallel (default: team,
tech, opportunity).  Prompts use chain-of-thought (reasoning before grade),
few-shot calibration examples, and anti-verbosity-bias instructions.

Key features:

- **Dimension pruning** — dimensions with near-empty dossiers (< 5 lines)
  are skipped, defaulting to grade C.  Saves 20-30% on LLM cost.
- **Low temperature** (0.2) — more consistent, reproducible scores.
- **Entity-type-aware** — built-in types (initiative, professor) use
  hardcoded field lists; custom types include all metadata_json fields.
- **Classification-aware weighted aggregation** — dimension weights vary
  by entity classification (deep_tech weights tech higher, etc.).

The dimension grade numerics (A+=1.0 … D=4.0) are aggregated to compute:

- ``verdict``  — deterministic mapping from weighted avg_grade
- ``score``    — ``round(5.0 - avg_grade, 1)`` snapped to half-points
- ``key_evidence`` — the dimension reasonings
- ``data_gaps``    — computed from missing enrichment sources
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from scout.models import Enrichment, Initiative, OutreachScore, Project
from scout.utils import json_parse

log = logging.getLogger(__name__)


class LLMCallError(Exception):
    """LLM call failed or returned unparseable output."""
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Grade map
# ---------------------------------------------------------------------------

GRADE_MAP = {
    "A+": 1.0, "A": 1.3, "A-": 1.7,
    "B+": 2.0, "B": 2.3, "B-": 2.7,
    "C+": 3.0, "C": 3.3, "C-": 3.7,
    "D": 4.0,
}
VALID_GRADES = set(GRADE_MAP.keys())


@dataclass(frozen=True)
class Grade:
    """Parsed, validated grade — always holds a valid letter + numeric.

    Follows "Parse, Don't Validate": construction either succeeds with a
    valid grade or falls back to a safe default. Downstream code never
    needs to re-validate.
    """
    letter: str
    numeric: float

    @classmethod
    def parse(cls, raw: Any, default: str = "C") -> Grade:
        g = str(raw or default).strip().upper().replace(" ", "")
        if g not in VALID_GRADES:
            log.warning("Unrecognizable grade %r, defaulting to %s", raw, default)
            g = default
        return cls(letter=g, numeric=GRADE_MAP[g])

# ---------------------------------------------------------------------------
# Default prompts (editable via API / frontend)
# ---------------------------------------------------------------------------

DEFAULT_TEAM_PROMPT = """\
You are evaluating the TEAM dimension of a Munich university student initiative \
for venture outreach purposes.

Assess the quality and readiness of the founding / core team based on:
- Team composition: roles filled (CEO, CTO, etc.), complementarity of skills
- Team size and depth (more relevant roles = stronger signal)
- LinkedIn / social presence and professional backgrounds
- Advisor or mentor involvement
- Evidence of execution capability (past ventures, competitions won, etc.)

Be opinionated. A strong team has clear leadership, technical talent, and \
business sense. A weak team is a group of friends with no defined roles.
Judge based on signal quality, not quantity of available data.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = exceptional founding team, D = no evidence of team quality)

CALIBRATION EXAMPLES:
- A: "Team of 8 with defined CTO, CEO, COO roles. Multiple LinkedIn profiles \
show prior startup experience. Won TUM IDEAward."
- C: "3-person team listed on website. No role descriptions. Generic LinkedIn \
profiles with only student experience."
- D: "No team page, no member names anywhere. Cannot assess team quality."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>"
}
"""

DEFAULT_TECH_PROMPT = """\
You are evaluating the TECH dimension of a Munich university student initiative \
for venture outreach purposes.

Assess technical depth and differentiation based on:
- GitHub activity: number of repos, contributors, recent commits, CI/CD presence
- Code quality signals: active development, multiple contributors, automation
- Research output: HuggingFace models, OpenAlex papers, Semantic Scholar hits
- Key repositories: what they're actually building, technical novelty
- Technology domains and technical moat

Be opinionated. Strong tech means active development of novel technology with \
measurable output. Weak tech means a landing page with no code or research.
Judge based on signal quality, not quantity of available data.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = deep tech with strong GitHub + research presence, D = no technical evidence)

CALIBRATION EXAMPLES:
- A: "12 GitHub repos, 45 contributors, 200+ commits in 90 days. CI/CD present. \
Published HuggingFace model with 500+ downloads. Active ML research."
- C: "2 GitHub repos with sporadic commits. Single contributor. No research \
output. Basic web app with standard frameworks."
- D: "No GitHub organization. No code repositories. No research publications. \
Only a marketing website."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>"
}
"""

DEFAULT_OPPORTUNITY_PROMPT = """\
You are evaluating the OPPORTUNITY dimension of a Munich university student \
initiative for venture outreach purposes.

Assess market opportunity and timing based on:
- Market size and growth potential
- Competitive landscape and differentiation
- Regulatory tailwinds or headwinds
- Funding climate for this sector
- University ecosystem support (Munich TUM/LMU/HM advantage)
- Commercial intent signals (product, customers, revenue)

Also provide:
- A classification of the initiative type
- A specific contact recommendation
- An engagement hook for first outreach

Be opinionated. A strong opportunity has a large addressable market with clear \
timing advantages. A weak opportunity is a solution looking for a problem.
Judge based on signal quality, not quantity of available data.

CLASSIFICATION (assign exactly one):
- deep_tech: Novel hardware, software, or deep research with application potential
- student_venture: Explicit commercial intent — forming a company, building a product
- applied_research: University research with potential commercial application but no venture intent
- student_club: Educational, networking, or social club without venture characteristics
- dormant: No evidence of activity in past 12 months

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = massive timely opportunity, D = no market opportunity)

CALIBRATION EXAMPLES:
- A: "AI-powered drug discovery startup from TUM. €500K pre-seed raised. \
Clear product-market fit in growing pharma-AI market. Strong IP from lab."
- C: "Student consulting club offering generic strategy advice. Competitive \
space with no differentiation. No commercial product or revenue."
- D: "Last social media post 18 months ago. Website domain expired. No \
evidence of any current activity."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "classification": "<deep_tech|student_venture|applied_research|student_club|dormant>",
  "contact_who": "<specific person/role + channel for outreach>",
  "contact_channel": "<email|linkedin|event|website_form>",
  "engagement_hook": "<specific opener referencing something concrete from the dossier>"
}
"""

# ---------------------------------------------------------------------------
# Professor default prompts
# ---------------------------------------------------------------------------

DEFAULT_PROFESSOR_TEAM_PROMPT = """\
You are evaluating the RESEARCH GROUP dimension of a TUM professor \
for collaboration and outreach purposes.

Assess the quality and activity of the professor's research group based on:
- Research group size and composition (postdocs, PhD students, research staff)
- LinkedIn / professional presence of group members
- Advisor and collaborator network quality
- Evidence of successful mentorship (graduated PhDs, placed students)
- Industry advisory roles or board memberships

Be opinionated. A strong research group has active members, clear structure, \
and evidence of producing results. A weak signal is a professor with no \
visible group activity.
Judge based on signal quality, not quantity of available data.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = exceptional research group, D = no evidence of group quality)

CALIBRATION EXAMPLES:
- A: "Group of 15+ with 3 postdocs, 8 PhD students. Multiple alumni now at \
Google/DeepMind. Active LinkedIn profiles show strong publication records."
- C: "Small group page lists 4 PhD students. No alumni tracking. Limited \
online presence for group members."
- D: "No group page found. No team members listed anywhere."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>"
}
"""

DEFAULT_PROFESSOR_TECH_PROMPT = """\
You are evaluating the RESEARCH OUTPUT dimension of a TUM professor \
for collaboration and outreach purposes.

Assess research depth and technical impact based on:
- Publication record: recent papers, venue quality, citation signals
- GitHub activity: open-source code, research artifacts, tools
- Research output: HuggingFace models, datasets, benchmarks
- Patents, standards contributions, technical reports
- Research funding signals (ERC grants, DFG projects, industry funding)

Be opinionated. Strong research output means active publication with \
reproducible artifacts and open-source impact. Weak means no visible \
publications or code.
Judge based on signal quality, not quantity of available data.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = prolific researcher with open-source impact, D = no research evidence)

CALIBRATION EXAMPLES:
- A: "50+ papers in top venues (NeurIPS, ICML). 3 GitHub repos with 500+ \
stars. Published HuggingFace datasets. H-index > 30."
- C: "12 papers in regional conferences. No open-source code. No models or \
datasets shared publicly."
- D: "No publications found in any database. No GitHub presence."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>"
}
"""

DEFAULT_PROFESSOR_OPPORTUNITY_PROMPT = """\
You are evaluating the COLLABORATION POTENTIAL of a TUM professor \
for outreach purposes.

Assess collaboration opportunity based on:
- Research relevance to industry applications
- Existing industry partnerships or spin-offs
- Openness to collaboration (consulting, joint projects, advisory roles)
- Research group capacity for new projects
- Complementarity with venture and startup ecosystem
- Track record of technology transfer

Also provide:
- A classification of the professor type
- A specific contact recommendation
- An engagement hook for first outreach

Be opinionated. A strong collaboration opportunity means the professor \
has relevant expertise AND demonstrated interest in applied work. \
Weak means purely theoretical or no evidence of external engagement.
Judge based on signal quality, not quantity of available data.

CLASSIFICATION (assign exactly one):
- research_leader: Established authority with significant impact and citations
- emerging_researcher: Junior professor or recently appointed with high potential
- industry_bridge: Strong industry connections, spin-offs, or consulting activity
- teaching_focused: Primarily teaching role, limited research output
- emeritus: Retired or emeritus professor

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = ideal collaboration partner, D = no collaboration potential)

CALIBRATION EXAMPLES:
- A: "Co-founded 2 spin-offs from lab research. Active industry advisory \
board member at Siemens. ERC Consolidator grant holder. Regularly speaks at \
industry conferences."
- C: "Pure theoretical research with limited applied potential. No industry \
partnerships. Responds to collaboration requests but doesn't initiate."
- D: "No industry connections found. No spin-offs. No consulting activity. \
Research area has no clear commercial application."

Think step-by-step: first analyze what evidence exists, then assign a grade.
Respond with ONLY valid JSON (reasoning FIRST, then grade):
{
  "reasoning": "<2-3 sentences: analyze evidence, then justify the grade>",
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "classification": "<research_leader|emerging_researcher|industry_bridge|teaching_focused|emeritus>",
  "contact_who": "<specific person/role + channel for outreach>",
  "contact_channel": "<email|linkedin|event|website_form>",
  "engagement_hook": "<specific opener referencing something concrete from the dossier>"
}
"""

# ---------------------------------------------------------------------------
# Prompt & classification registries (keyed by entity type)
# ---------------------------------------------------------------------------

# {entity_type: {key: (label, content)}}
_ALL_DEFAULT_PROMPTS: dict[str, dict[str, tuple[str, str]]] = {
    "initiative": {
        "team": ("Team", DEFAULT_TEAM_PROMPT),
        "tech": ("Tech", DEFAULT_TECH_PROMPT),
        "opportunity": ("Opportunity", DEFAULT_OPPORTUNITY_PROMPT),
    },
    "professor": {
        "team": ("Research Group", DEFAULT_PROFESSOR_TEAM_PROMPT),
        "tech": ("Research Output", DEFAULT_PROFESSOR_TECH_PROMPT),
        "opportunity": ("Collaboration Potential", DEFAULT_PROFESSOR_OPPORTUNITY_PROMPT),
    },
}

def default_prompts_for(entity_type: str) -> dict[str, tuple[str, str]]:
    """Return default prompt definitions for the given entity type."""
    return _ALL_DEFAULT_PROMPTS.get(entity_type, _ALL_DEFAULT_PROMPTS["initiative"])


VALID_VERDICTS = {"reach_out_now", "reach_out_soon", "monitor", "skip"}

# {entity_type: list of valid classifications}  — first element is the default fallback
DEFAULT_CLASSIFICATIONS: dict[str, list[str]] = {
    "initiative": ["deep_tech", "student_venture", "applied_research", "student_club", "dormant"],
    "professor": ["research_leader", "emerging_researcher", "industry_bridge", "teaching_focused", "emeritus"],
}

def valid_classifications(entity_type: str = "initiative") -> set[str]:
    """Return valid classification values for the given entity type."""
    return set(DEFAULT_CLASSIFICATIONS.get(entity_type, DEFAULT_CLASSIFICATIONS["initiative"]))


def default_classification(entity_type: str = "initiative") -> str:
    """Return the deterministic default classification for the given entity type."""
    return DEFAULT_CLASSIFICATIONS.get(entity_type, DEFAULT_CLASSIFICATIONS["initiative"])[0]


def _normalize_classification(value: str | None, entity_type: str = "initiative") -> str:
    """Normalize and validate a classification value, falling back to the entity default."""
    fallback = default_classification(entity_type)
    if not value:
        return fallback
    normalized = str(value).strip().lower()
    return normalized if normalized in valid_classifications(entity_type) else fallback


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Unified async LLM client supporting Anthropic and OpenAI."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.provider = provider or os.environ.get("LLM_PROVIDER", "anthropic")
        self.model = model or os.environ.get("LLM_MODEL", "")
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        if self.provider == "anthropic":
            import anthropic
            self.model = self.model or "claude-haiku-4-5-20251001"
            key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMCallError(
                    "ANTHROPIC_API_KEY not set. Export it in the shell where you run 'scout', "
                    "or set it in your MCP config.",
                    retryable=False,
                )
            self._client = anthropic.AsyncAnthropic(api_key=key)
        elif self.provider == "gemini":
            import openai
            self.model = self.model or "gemini-3.1-flash-lite"
            key = self._api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise LLMCallError(
                    "GOOGLE_API_KEY (or GEMINI_API_KEY) not set. Export it in your environment.",
                    retryable=False,
                )
            self._client = openai.AsyncOpenAI(
                api_key=key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        elif self.provider in ("openai", "openai_compatible"):
            import openai
            self.model = self.model or "gpt-5-mini"
            kwargs: dict[str, Any] = {}
            key = self._api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMCallError(
                    "OPENAI_API_KEY not set. Export it in your environment.",
                    retryable=False,
                )
            kwargs["api_key"] = key
            url = self._base_url or os.environ.get("OPENAI_BASE_URL")
            if url:
                kwargs["base_url"] = url
            self._client = openai.AsyncOpenAI(**kwargs)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider!r}")

    async def call(self, system: str, user: str, *, temperature: float | None = None) -> dict[str, Any]:
        """Send system+user message to the LLM, return parsed JSON.

        Args:
            system: System prompt.
            user: User message (typically the dossier).
            temperature: Sampling temperature. Lower = more deterministic.
                Defaults to 0.2 for consistent scoring results.
        """
        temp = temperature if temperature is not None else 0.2
        try:
            if self.provider == "anthropic":
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    temperature=temp,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                if not response.content:
                    raise LLMCallError("LLM returned empty response", retryable=True)
                text = response.content[0].text.strip()
                m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
                if m:
                    text = m.group(1)
            else:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    max_completion_tokens=2048,
                    temperature=temp,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                if not response.choices:
                    raise LLMCallError("LLM returned empty response", retryable=True)
                text = response.choices[0].message.content or "{}"
        except LLMCallError:
            raise
        except Exception as exc:
            raise LLMCallError(f"LLM API call failed: {exc}", retryable=True) from exc

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMCallError(
                f"LLM returned invalid JSON: {text[:200]}", retryable=False,
            ) from exc


# ---------------------------------------------------------------------------
# Dossier builders (dimension-specific)
# ---------------------------------------------------------------------------


def _build_dossier(
    obj,
    fields: list[tuple[str, str]],
    enrichments: list[Enrichment] | None = None,
    source_filter: dict[str, int] | None = None,
    header: list[str] | None = None,
    include_metadata: bool = False,
) -> str:
    """Build a dossier string from an object's attributes and enrichment data.

    Args:
        obj: ORM object (Initiative or Project) to read attributes from.
            Uses ``obj.field(attr)`` if available, else ``getattr(obj, attr)``.
        fields: List of (label, attr_name) pairs. For bool attrs, the label is
            used as-is when True (e.g. ``("GITHUB CI/CD: Present", "github_ci_present")``).
        enrichments: Optional enrichment records to include.
        source_filter: If given, only include enrichments whose source_type is a key,
            with the value being the max text length. ``None`` means include all.
        header: Initial header lines (e.g. ``["INITIATIVE: Foo", "UNIVERSITY: TUM"]``).
        include_metadata: If True, append all metadata_json fields. Used for
            custom entity types that store their domain data in metadata.
    """
    sections: list[str] = list(header or [])
    _field = getattr(obj, "field", None)
    seen_attrs: set[str] = set()
    for label, attr in fields:
        seen_attrs.add(attr)
        if _field is not None:
            val = _field(attr, default="")
        else:
            val = getattr(obj, attr, None)
        if val is None or val is False or val == "" or val == 0:
            continue
        if isinstance(val, bool):
            sections.append(label)
        else:
            sections.append(f"{label}: {val}")

    # For custom entity types: include metadata_json fields not already in the
    # hardcoded field list. This ensures domain-specific data (director, authors,
    # industry, etc.) appears in scoring dossiers.
    if include_metadata:
        _parsed_meta = getattr(obj, "_parsed_meta", None)
        if _parsed_meta is not None:
            for key, val in _parsed_meta().items():
                if key in seen_attrs or val is None or val == "":
                    continue
                label = key.upper().replace("_", " ")
                sections.append(f"{label}: {val}")

    if enrichments is not None:
        for e in enrichments:
            if source_filter is not None and e.source_type not in source_filter:
                continue
            max_len = (source_filter or {}).get(e.source_type, 5000)
            sections.append(f"\n--- {e.source_type.upper()} DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
            sections.append((e.summary or e.raw_text or "")[:max_len])

    return "\n".join(sections)


ENTITY_CONFIG: dict[str, dict] = {
    "initiative": {
        "label": "initiative", "label_plural": "initiatives",
        "context": "Munich student initiatives",
        "enrichers": [
            "website", "team_page", "github", "extra_links",
            "structured_data", "tech_stack", "dns", "sitemap", "careers", "git_deep",
        ],
        "dimensions": ["team", "tech", "opportunity"],
    },
    "professor": {
        "label": "professor", "label_plural": "professors",
        "context": "TUM professors",
        "enrichers": [
            "website", "extra_links", "structured_data", "dns", "sitemap",
        ],
        "dimensions": ["team", "tech", "opportunity"],
    },
}


def get_entity_config(entity_type: str) -> dict:
    """Return merged entity config: built-in defaults + any DB overrides."""
    base = ENTITY_CONFIG.get(entity_type)
    if base:
        return base
    # Unknown entity type — try DB-stored config, else build minimal default
    try:
        from scout.db import get_entity_config_json
        db_cfg = get_entity_config_json()
    except Exception:
        db_cfg = {}
    return {
        "label": db_cfg.get("label", entity_type),
        "label_plural": db_cfg.get("label_plural", entity_type + "s"),
        "context": db_cfg.get("context", entity_type),
        "enrichers": db_cfg.get("enrichers", ["website", "extra_links", "structured_data"]),
        "dimensions": db_cfg.get("dimensions", ["team", "tech", "opportunity"]),
    }


def _initiative_header(init: Initiative, entity_type: str = "initiative") -> list[str]:
    cfg = get_entity_config(entity_type)
    label = cfg["label"].upper()
    lines = [f"{label}: {init.name}"]
    uni = init.field("uni")
    if uni:
        lines.append(f"UNIVERSITY: {uni}")
    faculty = init.field("faculty")
    if faculty:
        lines.append(f"FACULTY: {faculty}")
    return lines


# Dimension-specific field specs: (label, attribute_name)
_TEAM_FIELDS: list[tuple[str, str]] = [
    ("DESCRIPTION", "description"),
    ("TEAM SIZE", "team_size"),
    ("MEMBER COUNT", "member_count"),
    ("MEMBER EXAMPLES", "member_examples"),
    ("MEMBER ROLES", "member_roles"),
    ("LINKEDIN", "linkedin"),
    ("LINKEDIN HITS", "linkedin_hits"),
    ("KEY ROLES (DD)", "dd_key_roles"),
    ("REFERENCES COUNT", "dd_references_count"),
    ("COMPETITIONS", "competitions"),
    ("SPONSORS", "sponsors"),
]

_TECH_FIELDS: list[tuple[str, str]] = [
    ("DESCRIPTION", "description"),
    ("TECHNOLOGY DOMAINS", "technology_domains"),
    ("GITHUB ORG", "github_org"),
    ("KEY REPOS", "key_repos"),
    ("GITHUB REPOS", "github_repo_count"),
    ("GITHUB CONTRIBUTORS", "github_contributors"),
    ("GITHUB COMMITS (90d)", "github_commits_90d"),
    ("GITHUB CI/CD: Present", "github_ci_present"),
    ("HUGGINGFACE MODEL HITS", "huggingface_model_hits"),
    ("OPENALEX HITS", "openalex_hits"),
    ("SEMANTIC SCHOLAR HITS", "semantic_scholar_hits"),
    ("RESEARCHGATE HITS", "researchgate_hits"),
]

_OPPORTUNITY_FIELDS: list[tuple[str, str]] = [
    ("SECTOR", "sector"),
    ("MODE", "mode"),
    ("DESCRIPTION", "description"),
    ("MANUAL RELEVANCE RATING", "relevance"),
    ("EMAIL", "email"),
    ("LINKEDIN", "linkedin"),
    ("WEBSITE", "website"),
    ("TEAM SIZE", "team_size"),
    ("TECHNOLOGY DOMAINS", "technology_domains"),
    ("MARKET DOMAINS", "market_domains"),
    ("CATEGORIES", "categories"),
    ("SPONSORS & PARTNERS", "sponsors"),
    ("COMPETITIONS & EVENTS", "competitions"),
    ("DUE DILIGENCE: Flagged as investable", "dd_is_investable"),
    ("MEMBER COUNT", "member_count"),
    ("GITHUB REPOS", "github_repo_count"),
]


def _is_builtin_entity(entity_type: str) -> bool:
    """Return True if this is a built-in entity type with hardcoded field lists."""
    return entity_type in ENTITY_CONFIG


def build_team_dossier(init: Initiative, enrichments: list[Enrichment], entity_type: str = "initiative") -> str:
    """Assemble team-relevant data for the first scoring dimension."""
    builtin = _is_builtin_entity(entity_type)
    return _build_dossier(
        init, _TEAM_FIELDS if builtin else [],
        enrichments=enrichments,
        # For built-in types: filter to team-relevant sources.
        # For custom types: include all enrichments (LLM-submitted data is all relevant).
        source_filter={
            "team_page": 5000, "website": 3000, "github": 3000,
            "linkedin": 3000, "instagram": 2000, "facebook": 2000,
            "careers": 3000, "structured_data": 2000,
        } if builtin else None,
        header=_initiative_header(init, entity_type),
        include_metadata=not builtin,
    )


def build_tech_dossier(init: Initiative, enrichments: list[Enrichment], entity_type: str = "initiative") -> str:
    """Assemble tech-relevant data for the second scoring dimension."""
    builtin = _is_builtin_entity(entity_type)
    return _build_dossier(
        init, _TECH_FIELDS if builtin else [],
        enrichments=enrichments,
        source_filter={
            "github": 5000, "website": 3000,
            "huggingface": 3000, "researchgate": 3000,
            "openalex": 3000, "semantic_scholar": 3000,
            "google_scholar": 3000, "orcid": 3000,
            "git_deep": 4000, "tech_stack": 2000,
        } if builtin else None,
        header=_initiative_header(init, entity_type),
        include_metadata=not builtin,
    )


def build_full_dossier(init: Initiative, enrichments: list[Enrichment], entity_type: str = "initiative") -> str:
    """Assemble full dossier for the last scoring dimension (needs big picture)."""
    builtin = _is_builtin_entity(entity_type)
    return _build_dossier(
        init, _OPPORTUNITY_FIELDS if builtin else [],
        enrichments=enrichments,
        source_filter=None,  # include all enrichment sources
        header=_initiative_header(init, entity_type),
        include_metadata=not builtin,
    )


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------


@dataclass
class DimensionResult:
    """Result from a single dimension LLM call."""
    grade: Grade
    reasoning: str
    extras: dict[str, Any]  # classification, contact_who, etc. from opportunity


def _dossier_has_substance(dossier: str, min_lines: int = 5) -> bool:
    """Check if a dossier has enough content to be worth scoring.

    A dossier with only header lines (INITIATIVE: X, UNIVERSITY: Y) and no
    enrichment data or field values is not worth sending to an LLM.
    """
    lines = [l for l in dossier.strip().splitlines() if l.strip()]
    return len(lines) >= min_lines


async def _score_dimension(client: LLMClient, system_prompt: str, dossier: str) -> DimensionResult:
    """Call LLM for a single dimension, return parsed result."""
    raw = await client.call(system_prompt, dossier)
    return DimensionResult(
        grade=Grade.parse(raw.get("grade")),
        reasoning=str(raw.get("reasoning", "")),
        extras={k: v for k, v in raw.items() if k not in ("grade", "reasoning")},
    )


# ---------------------------------------------------------------------------
# Deterministic aggregation
# ---------------------------------------------------------------------------


def compute_verdict(avg_grade: float) -> str:
    """Map average grade numeric to a verdict string."""
    if avg_grade <= 1.7:
        return "reach_out_now"
    if avg_grade <= 2.7:
        return "reach_out_soon"
    if avg_grade <= 3.3:
        return "monitor"
    return "skip"


def compute_score(avg_grade: float) -> float:
    """Convert average grade to a 1.0-5.0 score (higher = better)."""
    raw = 5.0 - avg_grade
    return round(max(1.0, min(5.0, raw)) * 2) / 2  # snap to half-point


# Classification-aware dimension weights.
# Instead of simple averaging, weight dimensions by what matters most
# for each classification.  Keys are (team, tech, opportunity) weights.
_CLASSIFICATION_WEIGHTS: dict[str, tuple[float, float, float]] = {
    # Initiative classifications
    "deep_tech":         (0.25, 0.45, 0.30),
    "student_venture":   (0.35, 0.25, 0.40),
    "applied_research":  (0.25, 0.40, 0.35),
    "student_club":      (0.40, 0.20, 0.40),
    "dormant":           (0.33, 0.33, 0.34),
    # Professor classifications
    "research_leader":   (0.30, 0.40, 0.30),
    "emerging_researcher": (0.25, 0.45, 0.30),
    "industry_bridge":   (0.25, 0.30, 0.45),
    "teaching_focused":  (0.40, 0.25, 0.35),
    "emeritus":          (0.33, 0.33, 0.34),
}
_DEFAULT_WEIGHTS = (1 / 3, 1 / 3, 1 / 3)


def compute_weighted_avg(
    team_num: float, tech_num: float, opp_num: float,
    classification: str = "",
) -> float:
    """Compute weighted average grade based on entity classification.

    Falls back to equal weights for unknown classifications.
    """
    w_team, w_tech, w_opp = _CLASSIFICATION_WEIGHTS.get(classification, _DEFAULT_WEIGHTS)
    return w_team * team_num + w_tech * tech_num + w_opp * opp_num


def compute_data_gaps(init: Initiative, enrichments: list[Enrichment], entity_type: str = "initiative") -> list[str]:
    """Identify missing data sources that could improve scoring."""
    gaps: list[str] = []
    source_types = {e.source_type for e in enrichments}
    cfg = get_entity_config(entity_type)
    configured_enrichers = set(cfg.get("enrichers", []))

    # For custom entity types with no configured enrichers, the primary gap
    # is simply having few/no enrichments at all.
    if not _is_builtin_entity(entity_type):
        if not enrichments:
            gaps.append("No enrichment data — use submit_enrichment() to add research findings")
        elif len(enrichments) < 2:
            gaps.append("Only 1 enrichment source — more data improves scoring accuracy")
        return gaps

    # Built-in entity types: check specific enricher coverage
    prof = entity_type == "professor"
    if "website" in configured_enrichers and "website" not in source_types:
        gaps.append("No website enrichment data available")
    if "team_page" in configured_enrichers and "team_page" not in source_types:
        gaps.append("No chair/group page data — research group assessment is limited" if prof
                     else "No team page data — team assessment is limited")
    if "github" in configured_enrichers and "github" not in source_types:
        gaps.append("No GitHub data — tech assessment is limited")
    if "git_deep" in configured_enrichers and "github" in source_types and "git_deep" not in source_types:
        gaps.append("No deep git analysis — README, dependencies, releases not analyzed")
    if not init.field("linkedin"):
        gaps.append("No LinkedIn URL — cannot verify academic network" if prof
                     else "No LinkedIn URL — cannot verify team backgrounds")
    if not init.field("email"):
        gaps.append("No contact email on file")
    if "structured_data" in configured_enrichers and "structured_data" not in source_types and "website" in source_types:
        gaps.append("No structured data (JSON-LD/OpenGraph) extracted from website")
    return gaps


def create_score_from_grades(
    initiative: Initiative,
    enrichments: list[Enrichment],
    grades: dict[str, Grade],
    *,
    classification: str = "",
    contact_who: str = "",
    contact_channel: str = "website_form",
    engagement_hook: str = "",
    reasoning: str = "",
    entity_type: str = "initiative",
) -> OutreachScore:
    """Build an OutreachScore from pre-evaluated grades (no LLM call).

    Use this when the calling LLM has already evaluated the dossiers
    (e.g. via get_scoring_dossier + submit_score).
    """
    classification = _normalize_classification(classification, entity_type)

    team_g = grades.get("team", Grade.parse("C"))
    tech_g = grades.get("tech", Grade.parse("C"))
    opp_g = grades.get("opportunity", Grade.parse("C"))

    avg = compute_weighted_avg(
        team_g.numeric, tech_g.numeric, opp_g.numeric, classification,
    )
    verdict = compute_verdict(avg)
    score = compute_score(avg)
    data_gaps = compute_data_gaps(initiative, enrichments, entity_type)

    key_evidence = [
        f"Team ({team_g.letter}): externally evaluated",
        f"Tech ({tech_g.letter}): externally evaluated",
        f"Opportunity ({opp_g.letter}): {reasoning}" if reasoning
        else f"Opportunity ({opp_g.letter}): externally evaluated",
    ]

    # Store all dimension grades in flexible JSON
    dim_grades = {k: {"letter": g.letter, "numeric": g.numeric} for k, g in grades.items()}

    return OutreachScore(
        initiative_id=initiative.id,
        project_id=None,
        verdict=verdict,
        score=score,
        classification=classification,
        reasoning=reasoning,
        contact_who=contact_who,
        contact_channel=contact_channel,
        engagement_hook=engagement_hook,
        key_evidence_json=json.dumps(key_evidence),
        data_gaps_json=json.dumps(data_gaps),
        grade_team=team_g.letter,
        grade_team_num=team_g.numeric,
        grade_tech=tech_g.letter,
        grade_tech_num=tech_g.numeric,
        grade_opportunity=opp_g.letter,
        grade_opportunity_num=opp_g.numeric,
        dimension_grades_json=json.dumps(dim_grades),
        llm_model="external",
        scored_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Score one initiative (3 parallel dimension calls)
# ---------------------------------------------------------------------------


async def score_initiative(
    initiative: Initiative,
    enrichments: list[Enrichment],
    client: LLMClient,
    prompts: dict[str, str] | None = None,
    entity_type: str = "initiative",
) -> OutreachScore:
    """Score an initiative across 3 dimensions in parallel.

    Args:
        initiative: The initiative to score.
        enrichments: Enrichment records for this initiative.
        client: LLM client for API calls.
        prompts: Optional ``{key: content}`` dict of custom prompts.
            Falls back to entity-type-specific defaults if not provided.
        entity_type: Entity type for classification validation and dossier headers.
    """
    defaults = default_prompts_for(entity_type)
    p = prompts or {}
    team_prompt = p.get("team", defaults["team"][1])
    tech_prompt = p.get("tech", defaults["tech"][1])
    opp_prompt = p.get("opportunity", defaults["opportunity"][1])

    team_dossier = build_team_dossier(initiative, enrichments, entity_type)
    tech_dossier = build_tech_dossier(initiative, enrichments, entity_type)
    full_dossier = build_full_dossier(initiative, enrichments, entity_type)

    # Dimension pruning: skip LLM calls for dimensions with near-empty dossiers.
    # This saves 20-30% on scoring cost when data is sparse and avoids
    # hallucinated grades. The full/opportunity dossier is always scored.
    tasks: dict[str, Any] = {}
    skipped: dict[str, DimensionResult] = {}

    if _dossier_has_substance(team_dossier):
        tasks["team"] = _score_dimension(client, team_prompt, team_dossier)
    else:
        skipped["team"] = DimensionResult(
            grade=Grade.parse("C"), reasoning="Skipped: insufficient data for assessment.", extras={},
        )

    if _dossier_has_substance(tech_dossier):
        tasks["tech"] = _score_dimension(client, tech_prompt, tech_dossier)
    else:
        skipped["tech"] = DimensionResult(
            grade=Grade.parse("C"), reasoning="Skipped: insufficient data for assessment.", extras={},
        )

    # Opportunity/full dossier is always scored — it drives classification + contact info
    tasks["opportunity"] = _score_dimension(client, opp_prompt, full_dossier)

    # Run non-skipped dimensions in parallel
    keys = list(tasks.keys())
    results_list = await asyncio.gather(*tasks.values())
    results = dict(zip(keys, results_list))
    results.update(skipped)

    team = results["team"]
    tech = results["tech"]
    opp = results["opportunity"]

    # Determine classification first so we can use weighted aggregation
    classification = _normalize_classification(
        opp.extras.get("classification"), entity_type,
    )

    avg_grade = compute_weighted_avg(
        team.grade.numeric, tech.grade.numeric, opp.grade.numeric, classification,
    )
    verdict = compute_verdict(avg_grade)
    score = compute_score(avg_grade)

    defaults = default_prompts_for(entity_type)
    key_evidence = [
        f"{defaults['team'][0]} ({team.grade.letter}): {team.reasoning}",
        f"{defaults['tech'][0]} ({tech.grade.letter}): {tech.reasoning}",
        f"{defaults['opportunity'][0]} ({opp.grade.letter}): {opp.reasoning}",
    ]
    data_gaps = compute_data_gaps(initiative, enrichments, entity_type)

    dim_grades = {
        "team": {"letter": team.grade.letter, "numeric": team.grade.numeric,
                 "reasoning": team.reasoning},
        "tech": {"letter": tech.grade.letter, "numeric": tech.grade.numeric,
                 "reasoning": tech.reasoning},
        "opportunity": {"letter": opp.grade.letter, "numeric": opp.grade.numeric,
                        "reasoning": opp.reasoning},
    }

    return OutreachScore(
        initiative_id=initiative.id,
        project_id=None,
        verdict=verdict,
        score=score,
        classification=classification,
        reasoning=opp.reasoning,
        contact_who=str(opp.extras.get("contact_who", "")),
        contact_channel=str(opp.extras.get("contact_channel", "website_form")),
        engagement_hook=str(opp.extras.get("engagement_hook", "")),
        key_evidence_json=json.dumps(key_evidence),
        data_gaps_json=json.dumps(data_gaps),
        grade_team=team.grade.letter,
        grade_team_num=team.grade.numeric,
        grade_tech=tech.grade.letter,
        grade_tech_num=tech.grade.numeric,
        grade_opportunity=opp.grade.letter,
        grade_opportunity_num=opp.grade.numeric,
        dimension_grades_json=json.dumps(dim_grades),
        llm_model=client.model,
        scored_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Score one project (kept as single-call for different data shape)
# ---------------------------------------------------------------------------

# Project scoring uses a combined prompt since projects have less data.
def _project_system_prompt(entity_type: str = "initiative") -> str:
    cls_list = "|".join(sorted(valid_classifications(entity_type)))
    ctx = ENTITY_CONFIG.get(entity_type, ENTITY_CONFIG["initiative"])["context"]
    return (
        f"You are an outreach assistant. Read the dossier about a project within "
        f"{ctx} and produce an outreach recommendation.\n\n"
        f"Provide grades for team, tech, and opportunity dimensions, plus a classification.\n"
        f"Judge based on signal quality, not quantity of available data.\n\n"
        f"Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D\n\n"
        f"Think step-by-step: first analyze what evidence exists, then assign grades.\n"
        f"Respond with ONLY valid JSON (reasoning FIRST):\n"
        "{\n"
        '  "reasoning": "<2-3 sentences: analyze evidence, then justify>",\n'
        '  "verdict": "<reach_out_now|reach_out_soon|monitor|skip>",\n'
        '  "score": <float 1.0-5.0>,\n'
        f'  "classification": "<{cls_list}>",\n'
        '  "contact_who": "<contact recommendation>",\n'
        '  "contact_channel": "<email|linkedin|event|website_form>",\n'
        '  "engagement_hook": "<specific opener>",\n'
        '  "key_evidence": ["<bullet 1>", "<bullet 2>"],\n'
        '  "data_gaps": ["<what is missing>"],\n'
        '  "team_grade": "<grade>",\n'
        '  "tech_grade": "<grade>",\n'
        '  "opportunity_grade": "<grade>"\n'
        "}\n"
    )


_PROJECT_DOSSIER_FIELDS: list[tuple[str, str]] = [
    ("DESCRIPTION", "description"),
    ("WEBSITE", "website"),
    ("GITHUB", "github_url"),
    ("TEAM", "team"),
]


def build_project_dossier(project: Project, initiative: Initiative, entity_type: str = "initiative") -> str:
    """Assemble project + parent initiative context into a dossier."""
    parent_label = ENTITY_CONFIG.get(entity_type, ENTITY_CONFIG["initiative"])["label"].upper()
    header = [
        f"PROJECT: {project.name}",
        f"PARENT {parent_label}: {initiative.name}",
        f"UNIVERSITY: {initiative.uni}",
    ]
    if initiative.sector:
        header.append(f"SECTOR: {initiative.sector}")

    sections: list[str] = [_build_dossier(project, _PROJECT_DOSSIER_FIELDS, header=header)]

    if initiative.description and initiative.description != project.description:
        sections.append(f"\nPARENT INITIATIVE DESCRIPTION: {initiative.description}")
    if initiative.sponsors:
        sections.append(f"SPONSORS & PARTNERS: {initiative.sponsors}")

    extra = json_parse(project.extra_links_json)
    for key, val in extra.items():
        if val:
            sections.append(f"{key.upper()}: {val}")

    return "\n".join(sections)


def _validate_project_response(raw: dict[str, Any], entity_type: str = "initiative") -> dict[str, Any]:
    """Validate and normalize LLM response for project scoring."""
    verdict = str(raw.get("verdict", "monitor")).strip().lower()
    if verdict not in VALID_VERDICTS:
        verdict = "monitor"

    score = max(1.0, min(5.0, float(raw.get("score", 3.0))))
    score = round(score * 2) / 2

    classification = _normalize_classification(raw.get("classification"), entity_type)

    key_evidence = raw.get("key_evidence", [])
    if not isinstance(key_evidence, list):
        key_evidence = []
    key_evidence = [str(e) for e in key_evidence[:10]]

    data_gaps = raw.get("data_gaps", [])
    if not isinstance(data_gaps, list):
        data_gaps = []
    data_gaps = [str(g) for g in data_gaps[:5]]

    team_grade = Grade.parse(raw.get("team_grade"))
    tech_grade = Grade.parse(raw.get("tech_grade"))
    opportunity_grade = Grade.parse(raw.get("opportunity_grade"))

    return {
        "verdict": verdict, "score": score, "classification": classification,
        "reasoning": str(raw.get("reasoning", "")),
        "contact_who": str(raw.get("contact_who", "")),
        "contact_channel": str(raw.get("contact_channel", "website_form")),
        "engagement_hook": str(raw.get("engagement_hook", "")),
        "key_evidence": key_evidence, "data_gaps": data_gaps,
        "team_grade": team_grade.letter, "tech_grade": tech_grade.letter,
        "opportunity_grade": opportunity_grade.letter,
    }


async def score_project(
    project: Project,
    initiative: Initiative,
    client: LLMClient,
    entity_type: str = "initiative",
) -> OutreachScore:
    """Score a project using a single combined LLM call."""
    dossier = build_project_dossier(project, initiative, entity_type)
    raw = await client.call(_project_system_prompt(entity_type), dossier)
    v = _validate_project_response(raw, entity_type)
    dim_grades = {
        "team": {"letter": v["team_grade"], "numeric": GRADE_MAP[v["team_grade"]]},
        "tech": {"letter": v["tech_grade"], "numeric": GRADE_MAP[v["tech_grade"]]},
        "opportunity": {"letter": v["opportunity_grade"], "numeric": GRADE_MAP[v["opportunity_grade"]]},
    }
    return OutreachScore(
        initiative_id=initiative.id,
        project_id=project.id,
        verdict=v["verdict"],
        score=v["score"],
        classification=v["classification"],
        reasoning=v["reasoning"],
        contact_who=v["contact_who"],
        contact_channel=v["contact_channel"],
        engagement_hook=v["engagement_hook"],
        key_evidence_json=json.dumps(v["key_evidence"]),
        data_gaps_json=json.dumps(v["data_gaps"]),
        grade_team=v["team_grade"],
        grade_team_num=GRADE_MAP[v["team_grade"]],
        grade_tech=v["tech_grade"],
        grade_tech_num=GRADE_MAP[v["tech_grade"]],
        grade_opportunity=v["opportunity_grade"],
        grade_opportunity_num=GRADE_MAP[v["opportunity_grade"]],
        dimension_grades_json=json.dumps(dim_grades),
        llm_model=client.model,
        scored_at=datetime.now(UTC),
    )
