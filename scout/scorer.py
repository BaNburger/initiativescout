"""Scoring engine: three parallel dimension evaluations with deterministic aggregation.

Architecture
------------
Each initiative is scored on three dimensions in parallel:

- **Team** — quality of the founding/core team based on team page content,
  LinkedIn/social presence, member roles, and team size.
- **Tech** — technical depth based on GitHub activity, research output
  (HuggingFace, OpenAlex, Semantic Scholar), and key repositories.
- **Opportunity** — market opportunity as a pure LLM judgment using the full
  dossier.  Also produces classification, contact recommendation, and
  engagement hook.

The three grade numerics (A+=1.0 … D=4.0) are averaged to compute:

- ``verdict``  — deterministic mapping from avg_grade
- ``score``    — ``round(5.0 - avg_grade, 1)`` snapped to half-points
- ``key_evidence`` — the three dimension reasonings
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

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = exceptional founding team, D = no evidence of team quality)

Respond with ONLY valid JSON:
{
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "reasoning": "<2-3 sentences explaining the grade>"
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

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = deep tech with strong GitHub + research presence, D = no technical evidence)

Respond with ONLY valid JSON:
{
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "reasoning": "<2-3 sentences explaining the grade>"
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

CLASSIFICATION (assign exactly one):
- deep_tech: Novel hardware, software, or deep research with application potential
- student_venture: Explicit commercial intent — forming a company, building a product
- applied_research: University research with potential commercial application but no venture intent
- student_club: Educational, networking, or social club without venture characteristics
- dormant: No evidence of activity in past 12 months

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D
(A+ = massive timely opportunity, D = no market opportunity)

Respond with ONLY valid JSON:
{
  "grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "reasoning": "<2-3 sentences explaining the grade>",
  "classification": "<deep_tech|student_venture|applied_research|student_club|dormant>",
  "contact_who": "<specific person/role + channel for outreach>",
  "contact_channel": "<email|linkedin|event|website_form>",
  "engagement_hook": "<specific opener referencing something concrete from the dossier>"
}
"""

# Registry used by db.py to seed defaults — {key: (label, content)}
DEFAULT_PROMPTS: dict[str, tuple[str, str]] = {
    "team": ("Team", DEFAULT_TEAM_PROMPT),
    "tech": ("Tech", DEFAULT_TECH_PROMPT),
    "opportunity": ("Opportunity", DEFAULT_OPPORTUNITY_PROMPT),
}

VALID_VERDICTS = {"reach_out_now", "reach_out_soon", "monitor", "skip"}
VALID_CLASSIFICATIONS = {"deep_tech", "student_venture", "applied_research", "student_club", "dormant"}


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
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key or os.environ.get("ANTHROPIC_API_KEY")
            )
        elif self.provider in ("openai", "openai_compatible"):
            import openai
            self.model = self.model or "gpt-4o-mini"
            kwargs: dict[str, Any] = {}
            key = self._api_key or os.environ.get("OPENAI_API_KEY")
            if key:
                kwargs["api_key"] = key
            url = self._base_url or os.environ.get("OPENAI_BASE_URL")
            if url:
                kwargs["base_url"] = url
            self._client = openai.AsyncOpenAI(**kwargs)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider!r}")

    async def call(self, system: str, user: str) -> dict[str, Any]:
        """Send system+user message to the LLM, return parsed JSON."""
        try:
            if self.provider == "anthropic":
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                text = response.content[0].text.strip()
                m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
                if m:
                    text = m.group(1)
            else:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=2048,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
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


def build_team_dossier(init: Initiative, enrichments: list[Enrichment]) -> str:
    """Assemble team-relevant data for the Team dimension LLM call."""
    sections: list[str] = [
        f"INITIATIVE: {init.name}",
        f"UNIVERSITY: {init.uni}",
    ]
    if init.description:
        sections.append(f"DESCRIPTION: {init.description}")
    if init.team_size:
        sections.append(f"TEAM SIZE: {init.team_size}")
    if init.member_count:
        sections.append(f"MEMBER COUNT: {init.member_count}")
    if init.member_examples:
        sections.append(f"MEMBER EXAMPLES: {init.member_examples}")
    if init.member_roles:
        sections.append(f"MEMBER ROLES: {init.member_roles}")
    if init.linkedin:
        sections.append(f"LINKEDIN: {init.linkedin}")
    if init.linkedin_hits:
        sections.append(f"LINKEDIN HITS: {init.linkedin_hits}")
    if init.dd_key_roles:
        sections.append(f"KEY ROLES (DD): {init.dd_key_roles}")
    if init.dd_references_count:
        sections.append(f"REFERENCES COUNT: {init.dd_references_count}")
    if init.competitions:
        sections.append(f"COMPETITIONS: {init.competitions}")
    if init.sponsors:
        sections.append(f"SPONSORS: {init.sponsors}")

    # Include team_page enrichment
    for e in enrichments:
        if e.source_type == "team_page":
            sections.append(f"\n--- TEAM PAGE DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
            sections.append(e.summary or e.raw_text[:5000])
        elif e.source_type == "website":
            sections.append(f"\n--- WEBSITE DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
            sections.append(e.summary or e.raw_text[:3000])

    return "\n".join(sections)


def build_tech_dossier(init: Initiative, enrichments: list[Enrichment]) -> str:
    """Assemble tech-relevant data for the Tech dimension LLM call."""
    sections: list[str] = [
        f"INITIATIVE: {init.name}",
        f"UNIVERSITY: {init.uni}",
    ]
    if init.description:
        sections.append(f"DESCRIPTION: {init.description}")
    if init.technology_domains:
        sections.append(f"TECHNOLOGY DOMAINS: {init.technology_domains}")
    if init.github_org:
        sections.append(f"GITHUB ORG: {init.github_org}")
    if init.key_repos:
        sections.append(f"KEY REPOS: {init.key_repos}")
    if init.github_repo_count:
        sections.append(f"GITHUB REPOS: {init.github_repo_count}")
    if init.github_contributors:
        sections.append(f"GITHUB CONTRIBUTORS: {init.github_contributors}")
    if init.github_commits_90d:
        sections.append(f"GITHUB COMMITS (90d): {init.github_commits_90d}")
    if init.github_ci_present:
        sections.append("GITHUB CI/CD: Present")
    if init.huggingface_model_hits:
        sections.append(f"HUGGINGFACE MODEL HITS: {init.huggingface_model_hits}")
    if init.openalex_hits:
        sections.append(f"OPENALEX HITS: {init.openalex_hits}")
    if init.semantic_scholar_hits:
        sections.append(f"SEMANTIC SCHOLAR HITS: {init.semantic_scholar_hits}")
    if init.researchgate_hits:
        sections.append(f"RESEARCHGATE HITS: {init.researchgate_hits}")

    # Include github enrichment
    for e in enrichments:
        if e.source_type == "github":
            sections.append(f"\n--- GITHUB DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
            sections.append(e.summary or e.raw_text[:5000])

    return "\n".join(sections)


def build_full_dossier(init: Initiative, enrichments: list[Enrichment]) -> str:
    """Assemble full dossier for the Opportunity dimension (needs big picture)."""
    sections: list[str] = [
        f"INITIATIVE: {init.name}",
        f"UNIVERSITY: {init.uni}",
    ]
    if init.sector:
        sections.append(f"SECTOR: {init.sector}")
    if init.mode:
        sections.append(f"MODE: {init.mode}")
    if init.description:
        sections.append(f"DESCRIPTION: {init.description}")
    if init.relevance:
        sections.append(f"MANUAL RELEVANCE RATING: {init.relevance}")
    if init.email:
        sections.append(f"EMAIL: {init.email}")
    if init.linkedin:
        sections.append(f"LINKEDIN: {init.linkedin}")
    if init.website:
        sections.append(f"WEBSITE: {init.website}")
    if init.team_size:
        sections.append(f"TEAM SIZE: {init.team_size}")
    if init.technology_domains:
        sections.append(f"TECHNOLOGY DOMAINS: {init.technology_domains}")
    if init.market_domains:
        sections.append(f"MARKET DOMAINS: {init.market_domains}")
    if init.categories:
        sections.append(f"CATEGORIES: {init.categories}")
    if init.sponsors:
        sections.append(f"SPONSORS & PARTNERS: {init.sponsors}")
    if init.competitions:
        sections.append(f"COMPETITIONS & EVENTS: {init.competitions}")
    if init.dd_is_investable:
        sections.append("DUE DILIGENCE: Flagged as investable")
    if init.member_count:
        sections.append(f"MEMBER COUNT: {init.member_count}")
    if init.github_repo_count:
        sections.append(f"GITHUB REPOS: {init.github_repo_count}")

    for e in enrichments:
        sections.append(f"\n--- {e.source_type.upper()} DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
        sections.append(e.summary or e.raw_text[:5000])

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------


@dataclass
class DimensionResult:
    """Result from a single dimension LLM call."""
    grade: str
    grade_num: float
    reasoning: str
    extras: dict[str, Any]  # classification, contact_who, etc. from opportunity


def _validate_grade(val: Any) -> str:
    """Normalize a grade string to a valid grade."""
    g = str(val or "C").strip().upper().replace(" ", "")
    if g not in VALID_GRADES:
        log.warning("Unrecognizable grade %r, defaulting to C", val)
        return "C"
    return g


async def _score_dimension(client: LLMClient, system_prompt: str, dossier: str) -> DimensionResult:
    """Call LLM for a single dimension, return parsed result."""
    raw = await client.call(system_prompt, dossier)
    grade = _validate_grade(raw.get("grade"))
    return DimensionResult(
        grade=grade,
        grade_num=GRADE_MAP[grade],
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


def compute_data_gaps(init: Initiative, enrichments: list[Enrichment]) -> list[str]:
    """Identify missing data sources that could improve scoring."""
    gaps: list[str] = []
    source_types = {e.source_type for e in enrichments}
    if "website" not in source_types:
        gaps.append("No website enrichment data available")
    if "team_page" not in source_types:
        gaps.append("No team page data — team assessment is limited")
    if "github" not in source_types:
        gaps.append("No GitHub data — tech assessment is limited")
    if not init.linkedin:
        gaps.append("No LinkedIn URL — cannot verify team backgrounds")
    if not init.email:
        gaps.append("No contact email on file")
    return gaps


# ---------------------------------------------------------------------------
# Score one initiative (3 parallel dimension calls)
# ---------------------------------------------------------------------------


async def score_initiative(
    initiative: Initiative,
    enrichments: list[Enrichment],
    client: LLMClient,
    prompts: dict[str, str] | None = None,
) -> OutreachScore:
    """Score an initiative across 3 dimensions in parallel.

    Args:
        initiative: The initiative to score.
        enrichments: Enrichment records for this initiative.
        client: LLM client for API calls.
        prompts: Optional ``{key: content}`` dict of custom prompts.
            Falls back to DEFAULT_PROMPTS if not provided.
    """
    p = prompts or {}
    team_prompt = p.get("team", DEFAULT_TEAM_PROMPT)
    tech_prompt = p.get("tech", DEFAULT_TECH_PROMPT)
    opp_prompt = p.get("opportunity", DEFAULT_OPPORTUNITY_PROMPT)

    team_dossier = build_team_dossier(initiative, enrichments)
    tech_dossier = build_tech_dossier(initiative, enrichments)
    full_dossier = build_full_dossier(initiative, enrichments)

    team, tech, opp = await asyncio.gather(
        _score_dimension(client, team_prompt, team_dossier),
        _score_dimension(client, tech_prompt, tech_dossier),
        _score_dimension(client, opp_prompt, full_dossier),
    )

    avg_grade = (team.grade_num + tech.grade_num + opp.grade_num) / 3
    verdict = compute_verdict(avg_grade)
    score = compute_score(avg_grade)

    classification = str(opp.extras.get("classification", "student_club")).strip().lower()
    if classification not in VALID_CLASSIFICATIONS:
        classification = "student_club"

    key_evidence = [
        f"Team ({team.grade}): {team.reasoning}",
        f"Tech ({tech.grade}): {tech.reasoning}",
        f"Opportunity ({opp.grade}): {opp.reasoning}",
    ]
    data_gaps = compute_data_gaps(initiative, enrichments)

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
        grade_team=team.grade,
        grade_team_num=team.grade_num,
        grade_tech=tech.grade,
        grade_tech_num=tech.grade_num,
        grade_opportunity=opp.grade,
        grade_opportunity_num=opp.grade_num,
        llm_model=client.model,
        scored_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Score one project (kept as single-call for different data shape)
# ---------------------------------------------------------------------------

# Project scoring uses a combined prompt since projects have less data.
PROJECT_SYSTEM_PROMPT = """\
You are a venture scout's assistant. Read the dossier about a project within \
a Munich university student initiative and produce an outreach recommendation.

Provide grades for team, tech, and opportunity dimensions, plus a classification.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D

Respond with ONLY valid JSON:
{
  "verdict": "<reach_out_now|reach_out_soon|monitor|skip>",
  "score": <float 1.0-5.0>,
  "classification": "<deep_tech|student_venture|applied_research|student_club|dormant>",
  "reasoning": "<2-3 sentences>",
  "contact_who": "<contact recommendation>",
  "contact_channel": "<email|linkedin|event|website_form>",
  "engagement_hook": "<specific opener>",
  "key_evidence": ["<bullet 1>", "<bullet 2>"],
  "data_gaps": ["<what is missing>"],
  "team_grade": "<grade>",
  "tech_grade": "<grade>",
  "opportunity_grade": "<grade>"
}
"""


def build_project_dossier(project: Project, initiative: Initiative) -> str:
    """Assemble project + parent initiative context into a dossier."""
    sections: list[str] = [
        f"PROJECT: {project.name}",
        f"PARENT INITIATIVE: {initiative.name}",
        f"UNIVERSITY: {initiative.uni}",
    ]
    for val, label in [
        (initiative.sector, "SECTOR"), (project.description, "DESCRIPTION"),
        (project.website, "WEBSITE"), (project.github_url, "GITHUB"), (project.team, "TEAM"),
    ]:
        if val:
            sections.append(f"{label}: {val}")

    if initiative.description and initiative.description != project.description:
        sections.append(f"\nPARENT INITIATIVE DESCRIPTION: {initiative.description}")
    if initiative.sponsors:
        sections.append(f"SPONSORS & PARTNERS: {initiative.sponsors}")

    try:
        extra = json.loads(project.extra_links_json or "{}")
        for key, val in extra.items():
            if val:
                sections.append(f"{key.upper()}: {val}")
    except (json.JSONDecodeError, TypeError):
        pass

    return "\n".join(sections)


def _validate_project_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize LLM response for project scoring."""
    verdict = str(raw.get("verdict", "monitor")).strip().lower()
    if verdict not in VALID_VERDICTS:
        verdict = "monitor"

    score = max(1.0, min(5.0, float(raw.get("score", 3.0))))
    score = round(score * 2) / 2

    classification = str(raw.get("classification", "student_club")).strip().lower()
    if classification not in VALID_CLASSIFICATIONS:
        classification = "student_club"

    key_evidence = raw.get("key_evidence", [])
    if not isinstance(key_evidence, list):
        key_evidence = []
    key_evidence = [str(e) for e in key_evidence[:10]]

    data_gaps = raw.get("data_gaps", [])
    if not isinstance(data_gaps, list):
        data_gaps = []
    data_gaps = [str(g) for g in data_gaps[:5]]

    team_grade = _validate_grade(raw.get("team_grade"))
    tech_grade = _validate_grade(raw.get("tech_grade"))
    opportunity_grade = _validate_grade(raw.get("opportunity_grade"))

    return {
        "verdict": verdict, "score": score, "classification": classification,
        "reasoning": str(raw.get("reasoning", "")),
        "contact_who": str(raw.get("contact_who", "")),
        "contact_channel": str(raw.get("contact_channel", "website_form")),
        "engagement_hook": str(raw.get("engagement_hook", "")),
        "key_evidence": key_evidence, "data_gaps": data_gaps,
        "team_grade": team_grade, "tech_grade": tech_grade,
        "opportunity_grade": opportunity_grade,
    }


async def score_project(
    project: Project,
    initiative: Initiative,
    client: LLMClient,
) -> OutreachScore:
    """Score a project using a single combined LLM call."""
    dossier = build_project_dossier(project, initiative)
    raw = await client.call(PROJECT_SYSTEM_PROMPT, dossier)
    v = _validate_project_response(raw)
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
        llm_model=client.model,
        scored_at=datetime.now(UTC),
    )
