from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from scout.models import Enrichment, Initiative, OutreachScore, Project

log = logging.getLogger(__name__)

VALID_VERDICTS = {"reach_out_now", "reach_out_soon", "monitor", "skip"}
VALID_CLASSIFICATIONS = {"deep_tech", "student_venture", "applied_research", "student_club", "dormant"}

GRADE_MAP = {
    "A+": 1.0, "A": 1.3, "A-": 1.7,
    "B+": 2.0, "B": 2.3, "B-": 2.7,
    "C+": 3.0, "C": 3.3, "C-": 3.7,
    "D": 4.0,
}
VALID_GRADES = set(GRADE_MAP.keys())

OUTREACH_SYSTEM_PROMPT = """\
You are a venture scout's assistant. Your job is to read a dossier about a \
Munich university student initiative and produce a single actionable outreach \
recommendation.

You are NOT writing an investment memo. You are answering one question:
"Should we reach out to this initiative, and if so, how?"

VERDICT (assign exactly one):
- reach_out_now: Strong signals of technical depth, active building, reachable \
team. Worth a cold email this week.
- reach_out_soon: Promising but needs a triggering event (upcoming demo day, \
new GitHub activity, etc). Queue for next month.
- monitor: Interesting space but insufficient evidence of substance or \
reachability. Check back in 3 months.
- skip: Student social club, dormant project, or clearly outside scope.

SCORE (1.0-5.0, half-point increments):
Rank within the verdict group. A 4.5 reach_out_now is your #1 priority. \
A 2.0 reach_out_now is still worth contacting but lower priority.

CLASSIFICATION (assign exactly one):
- deep_tech: Building novel hardware, software, or deep research with \
application potential (robotics, quantum, biotech, autonomous systems)
- student_venture: Explicit commercial intent — has or is forming a company, \
building a product, seeking customers
- applied_research: University research group with potential commercial \
application but no venture intent yet
- student_club: Educational, networking, or social club without venture \
characteristics
- dormant: No evidence of activity in past 12 months

CONTACT RECOMMENDATION:
- contact_who: Specific person/role + channel. E.g. "Email team lead via \
info@example.com" or "Connect with CTO on LinkedIn"
- contact_channel: Primary channel — "email" | "linkedin" | "event" | "website_form"

ENGAGEMENT HOOK:
A specific opening line or topic for the first message. Reference something \
concrete: a recent GitHub commit, a competition result, a sponsor relationship, \
a technical challenge they face. Generic "we love what you're doing" is useless.

KEY EVIDENCE:
List 3-5 concrete observations from the dossier that support your verdict. \
Each should be one sentence referencing specific data.

DATA GAPS:
List 1-3 pieces of missing information that, if found, might change the verdict.

DIMENSION GRADES (assign a school grade A+ through D for each):
- team_grade: Quality of the founding / core team. Consider: roles filled \
(CEO, CTO, etc.), relevant experience, team size, complementarity of skills, advisors.
- tech_grade: Technical depth and differentiation. Consider: novelty of technology, \
GitHub activity, research output, technical moat, prototype maturity.
- opportunity_grade: Market opportunity and timing. Consider: market size, competitive \
landscape, regulatory tailwinds, funding climate, university ecosystem support.

Valid grades: A+, A, A-, B+, B, B-, C+, C, C-, D

Respond with ONLY valid JSON:
{
  "verdict": "<reach_out_now|reach_out_soon|monitor|skip>",
  "score": <float 1.0-5.0>,
  "classification": "<deep_tech|student_venture|applied_research|student_club|dormant>",
  "reasoning": "<2-3 sentences explaining the verdict>",
  "contact_who": "<specific contact recommendation>",
  "contact_channel": "<email|linkedin|event|website_form>",
  "engagement_hook": "<specific opener for first outreach>",
  "key_evidence": ["<bullet 1>", "<bullet 2>", "<bullet 3>"],
  "data_gaps": ["<what is missing>"],
  "team_grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "tech_grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>",
  "opportunity_grade": "<A+|A|A-|B+|B|B-|C+|C|C-|D>"
}
"""


# ---------------------------------------------------------------------------
# LLM Client (adapted from initiative_tracker/llm/client.py)
# ---------------------------------------------------------------------------


class LLMClient:
    """Unified LLM client supporting Anthropic and OpenAI."""

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
            self._client = anthropic.Anthropic(
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
            self._client = openai.OpenAI(**kwargs)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider!r}")

    def _call_sync(self, system: str, user: str) -> dict[str, Any]:
        if self.provider == "anthropic":
            response = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = response.content[0].text.strip()
            # Extract JSON from markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                text = "\n".join(lines[1:end])
            return json.loads(text)
        else:
            response = self._client.chat.completions.create(
                model=self.model,
                max_tokens=2048,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = response.choices[0].message.content or "{}"
            return json.loads(text)

    async def call(self, system: str, user: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._call_sync, system, user)


# ---------------------------------------------------------------------------
# Dossier assembly
# ---------------------------------------------------------------------------


def build_dossier(initiative: Initiative, enrichments: list[Enrichment]) -> str:
    """Assemble all known data into a text dossier for the LLM."""
    sections: list[str] = []
    sections.append(f"INITIATIVE: {initiative.name}")
    sections.append(f"UNIVERSITY: {initiative.uni}")
    if initiative.sector:
        sections.append(f"SECTOR: {initiative.sector}")
    if initiative.mode:
        sections.append(f"MODE: {initiative.mode}")
    if initiative.description:
        sections.append(f"DESCRIPTION: {initiative.description}")
    if initiative.relevance:
        sections.append(f"MANUAL RELEVANCE RATING: {initiative.relevance}")
    if initiative.email:
        sections.append(f"EMAIL: {initiative.email}")
    if initiative.linkedin:
        sections.append(f"LINKEDIN: {initiative.linkedin}")
    if initiative.website:
        sections.append(f"WEBSITE: {initiative.website}")
    if initiative.team_size:
        sections.append(f"TEAM SIZE: {initiative.team_size}")
    if initiative.sponsors:
        sections.append(f"SPONSORS & PARTNERS: {initiative.sponsors}")
    if initiative.competitions:
        sections.append(f"COMPETITIONS & EVENTS: {initiative.competitions}")

    for e in enrichments:
        sections.append(f"\n--- {e.source_type.upper()} DATA (fetched {e.fetched_at.strftime('%Y-%m-%d')}) ---")
        sections.append(e.summary or e.raw_text[:5000])

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def validate_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize LLM response."""
    verdict = str(raw.get("verdict", "monitor")).strip().lower()
    if verdict not in VALID_VERDICTS:
        verdict = "monitor"

    score = max(1.0, min(5.0, float(raw.get("score", 3.0))))
    score = round(score * 2) / 2  # snap to half-point

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

    # Dimension grades
    def _validate_grade(val: Any) -> str:
        g = str(val or "C").strip().upper()
        # Normalize common variants
        if g in VALID_GRADES:
            return g
        # Try without spaces
        g = g.replace(" ", "")
        return g if g in VALID_GRADES else "C"

    team_grade = _validate_grade(raw.get("team_grade"))
    tech_grade = _validate_grade(raw.get("tech_grade"))
    opportunity_grade = _validate_grade(raw.get("opportunity_grade"))

    return {
        "verdict": verdict,
        "score": score,
        "classification": classification,
        "reasoning": str(raw.get("reasoning", "")),
        "contact_who": str(raw.get("contact_who", "")),
        "contact_channel": str(raw.get("contact_channel", "website_form")),
        "engagement_hook": str(raw.get("engagement_hook", "")),
        "key_evidence": key_evidence,
        "data_gaps": data_gaps,
        "team_grade": team_grade,
        "tech_grade": tech_grade,
        "opportunity_grade": opportunity_grade,
    }


# ---------------------------------------------------------------------------
# Score one initiative
# ---------------------------------------------------------------------------


async def score_initiative(
    initiative: Initiative,
    enrichments: list[Enrichment],
    client: LLMClient,
) -> OutreachScore:
    """Build dossier, call LLM, validate, return OutreachScore (not yet committed)."""
    dossier = build_dossier(initiative, enrichments)
    raw = await client.call(OUTREACH_SYSTEM_PROMPT, dossier)
    validated = validate_response(raw)

    return _build_outreach_score(validated, initiative.id, None, client.model)


# ---------------------------------------------------------------------------
# Score one project
# ---------------------------------------------------------------------------


def build_project_dossier(project: Project, initiative: Initiative) -> str:
    """Assemble project + parent initiative context into a dossier."""
    sections: list[str] = []
    sections.append(f"PROJECT: {project.name}")
    sections.append(f"PARENT INITIATIVE: {initiative.name}")
    sections.append(f"UNIVERSITY: {initiative.uni}")
    if initiative.sector:
        sections.append(f"SECTOR: {initiative.sector}")
    if project.description:
        sections.append(f"DESCRIPTION: {project.description}")
    if project.website:
        sections.append(f"WEBSITE: {project.website}")
    if project.github_url:
        sections.append(f"GITHUB: {project.github_url}")
    if project.team:
        sections.append(f"TEAM: {project.team}")

    # Include parent initiative context
    if initiative.description and initiative.description != project.description:
        sections.append(f"\nPARENT INITIATIVE DESCRIPTION: {initiative.description}")
    if initiative.sponsors:
        sections.append(f"SPONSORS & PARTNERS: {initiative.sponsors}")

    # Parse extra links
    try:
        extra = json.loads(project.extra_links_json or "{}")
        for key, val in extra.items():
            if val:
                sections.append(f"{key.upper()}: {val}")
    except (json.JSONDecodeError, TypeError):
        pass

    return "\n".join(sections)


async def score_project(
    project: Project,
    initiative: Initiative,
    client: LLMClient,
) -> OutreachScore:
    """Build project dossier, call LLM, validate, return OutreachScore."""
    dossier = build_project_dossier(project, initiative)
    raw = await client.call(OUTREACH_SYSTEM_PROMPT, dossier)
    validated = validate_response(raw)

    return _build_outreach_score(validated, initiative.id, project.id, client.model)


# ---------------------------------------------------------------------------
# Shared OutreachScore builder
# ---------------------------------------------------------------------------


def _build_outreach_score(
    validated: dict[str, Any],
    initiative_id: int,
    project_id: int | None,
    llm_model: str,
) -> OutreachScore:
    return OutreachScore(
        initiative_id=initiative_id,
        project_id=project_id,
        verdict=validated["verdict"],
        score=validated["score"],
        classification=validated["classification"],
        reasoning=validated["reasoning"],
        contact_who=validated["contact_who"],
        contact_channel=validated["contact_channel"],
        engagement_hook=validated["engagement_hook"],
        key_evidence_json=json.dumps(validated["key_evidence"]),
        data_gaps_json=json.dumps(validated["data_gaps"]),
        grade_team=validated["team_grade"],
        grade_team_num=GRADE_MAP[validated["team_grade"]],
        grade_tech=validated["tech_grade"],
        grade_tech_num=GRADE_MAP[validated["tech_grade"]],
        grade_opportunity=validated["opportunity_grade"],
        grade_opportunity_num=GRADE_MAP[validated["opportunity_grade"]],
        llm_model=llm_model,
        scored_at=datetime.now(UTC),
    )
