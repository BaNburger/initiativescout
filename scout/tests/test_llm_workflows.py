"""End-to-end LLM workflow tests — simulates how an LLM uses Scout MCP tools.

Tests cover 10 realistic GTM workflows: autonomous pipeline, single entity,
manual enrichment, API-key-free scoring, batch ops, incremental enrichment,
error recovery, enrichment preservation, and context window pressure.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base, Enrichment, Initiative, OutreachScore, ScoringPrompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measure(result) -> int:
    """Return JSON byte size of a tool response."""
    return len(json.dumps(result, default=str).encode())


def _fake_enrichment(init_id: int, source_type: str = "website",
                     structured_fields: dict | None = None) -> Enrichment:
    sf_json = json.dumps(structured_fields) if structured_fields else "{}"
    return Enrichment(
        initiative_id=init_id, source_type=source_type,
        source_url=f"https://example.com/{source_type}",
        raw_text=f"Enrichment data from {source_type} for entity {init_id}",
        summary=f"Summary from {source_type}",
        structured_fields_json=sf_json,
        fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _fake_score(init_id: int, verdict="reach_out_now", score=4.5) -> OutreachScore:
    return OutreachScore(
        initiative_id=init_id, verdict=verdict, score=score,
        classification="deep_tech", reasoning="Strong signals",
        grade_team="A", grade_team_num=1.3,
        grade_tech="A", grade_tech_num=1.3,
        grade_opportunity="A-", grade_opportunity_num=1.7,
        contact_who="Founder", contact_channel="email",
        engagement_hook="Saw your GitHub...",
        key_evidence_json='["Team (A): strong", "Tech (A): solid"]',
        data_gaps_json='[]',
        dimension_grades_json='{"team": "A", "tech": "A", "opportunity": "A-"}',
        llm_model="test", scored_at=datetime(2024, 6, 2, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    from scout.db import _ensure_fts_table
    _ensure_fts_table(eng)
    return eng


@pytest.fixture()
def SessionFactory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def session(SessionFactory):
    sess = SessionFactory()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def _patch_db(SessionFactory):
    @contextmanager
    def _test_session_scope():
        sess = SessionFactory()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    with (
        patch("scout.mcp_server.session_scope", _test_session_scope),
        patch("scout.mcp_server.get_session", SessionFactory),
        patch("scout.db.get_session", SessionFactory),
    ):
        yield


@pytest.fixture()
def _patch_entity_type():
    with patch("scout.mcp_server.get_entity_type", return_value="initiative"):
        yield


@pytest.fixture()
def five_entities(session: Session) -> list[Initiative]:
    items = []
    for i, (name, uni) in enumerate([
        ("AlphaAI", "TUM"), ("BetaRobotics", "LMU"), ("GammaBio", "TUM"),
        ("DeltaFintech", "HM"), ("EpsilonSpace", "TUM"),
    ]):
        init = Initiative(name=name, uni=uni, website=f"https://{name.lower()}.dev",
                          github_org=f"https://github.com/{name.lower()}")
        session.add(init)
    session.flush()
    items = session.execute(select(Initiative).order_by(Initiative.id)).scalars().all()
    return list(items)


@pytest.fixture()
def seed_prompts(session: Session):
    """Seed scoring prompts (normally done by init_db)."""
    for key in ["team", "tech", "opportunity"]:
        session.add(ScoringPrompt(key=key, label=key.title(), content=f"Evaluate {key}."))
    session.commit()


@pytest.fixture()
def enriched_scored_entities(session: Session, five_entities) -> list[Initiative]:
    verdicts = ["reach_out_now", "reach_out_now", "reach_out_soon", "monitor", "skip"]
    scores = [4.5, 4.2, 3.5, 2.0, 1.0]
    for init, verdict, score in zip(five_entities, verdicts, scores):
        session.add(_fake_enrichment(init.id, "website"))
        session.add(_fake_enrichment(init.id, "github"))
        session.add(_fake_score(init.id, verdict=verdict, score=score))
    session.flush()
    return five_entities


# ---------------------------------------------------------------------------
# 1. Autonomous Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestAutonomousPipeline:
    def test_full_pipeline(self, five_entities):
        from scout.mcp_server import get_overview, get_work_queue, list_entities

        overview = get_overview()
        assert overview["total"] == 5
        assert overview["scored"] == 0

        queue = get_work_queue(limit=10)
        assert len(queue["queue"]) == 5
        assert all(item["recommended_action"] for item in queue["queue"])

    def test_overview_next_actions(self, five_entities):
        from scout.mcp_server import get_overview
        overview = get_overview()
        assert any(n["tool"] in ("get_work_queue", "overview") for n in overview.get("next", []))

    @pytest.mark.asyncio
    async def test_process_queue_with_mocked_enrichment(self, five_entities):
        from scout.mcp_server import process_queue

        async def _fake_run_enrichment(session, init, crawler=None, *, incremental=True):
            return [_fake_enrichment(init.id)]

        async def _fake_run_scoring(session, init, client=None, entity_type="initiative"):
            return _fake_score(init.id)

        with (
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.mcp_server.services.run_scoring", side_effect=_fake_run_scoring),
            patch("scout.mcp_server.open_crawler", return_value=AsyncMock()),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.LLMClient"),
        ):
            result = await process_queue(limit=5)

        assert result["enrichment"]["succeeded"] >= 1
        assert result["scoring"]["succeeded"] >= 1


# ---------------------------------------------------------------------------
# 2. Single Entity Deep Dive
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestSingleEntityDeepDive:
    def test_create_suggests_enrich(self):
        from scout.mcp_server import manage_entity
        result = manage_entity(action="create", name="TestCorp", uni="TUM",
                               updates={"website": "https://testcorp.dev"})
        assert result.get("id")
        next_tools = [n["tool"] for n in result.get("next", [])]
        assert any(t in ("enrich_entity", "enrich") for t in next_tools)

    def test_compact_vs_full_response_sizes(self, five_entities):
        from scout.mcp_server import get_entity
        eid = five_entities[0].id
        compact = get_entity(eid, compact=True)
        full = get_entity(eid, compact=False)
        assert _measure(compact) < _measure(full)

    def test_include_gaps_returns_missing_fields(self, five_entities):
        from scout.mcp_server import get_entity
        eid = five_entities[0].id
        without_gaps = get_entity(eid, include_gaps=False)
        with_gaps = get_entity(eid, include_gaps=True)
        assert "_missing_fields" not in without_gaps
        assert "_missing_fields" in with_gaps
        assert isinstance(with_gaps["_missing_fields"], list)


# ---------------------------------------------------------------------------
# 3. Manual LLM Enrichment
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestManualLLMEnrichment:
    def test_structured_fields_applied(self, five_entities):
        from scout.mcp_server import get_entity, submit_enrichment
        eid = five_entities[0].id
        result = submit_enrichment(
            entity_id=eid, source_type="web_research",
            content="Found LinkedIn profile and team info",
            structured_fields={"linkedin": "https://linkedin.com/company/alpha", "member_count": 42},
        )
        assert "linkedin" in result.get("fields_applied", [])
        assert "member_count" in result.get("fields_applied", [])

        entity = get_entity(eid, include_gaps=True)
        assert entity["linkedin"] == "https://linkedin.com/company/alpha"

    def test_invalid_fields_skipped(self, five_entities):
        from scout.mcp_server import submit_enrichment
        result = submit_enrichment(
            entity_id=five_entities[0].id, source_type="web_research",
            content="some content",
            structured_fields={"nonexistent_field": "value"},
        )
        assert any(s["key"] == "nonexistent_field" for s in result.get("fields_skipped", []))

    def test_type_coercion(self, five_entities):
        from scout.mcp_server import get_entity, submit_enrichment
        eid = five_entities[0].id
        submit_enrichment(
            entity_id=eid, source_type="web_research", content="team data",
            structured_fields={"member_count": "42"},
        )
        entity = get_entity(eid)
        assert entity["member_count"] == 42

    def test_missing_fields_decrease_after_enrichment(self, five_entities):
        from scout.mcp_server import get_entity, submit_enrichment
        eid = five_entities[0].id
        before = get_entity(eid, include_gaps=True)
        before_count = len(before["_missing_fields"])

        submit_enrichment(
            entity_id=eid, source_type="web_research", content="data",
            structured_fields={"email": "team@alpha.dev", "member_count": 15,
                               "technology_domains": "AI, Robotics"},
        )

        after = get_entity(eid, include_gaps=True)
        after_count = len(after["_missing_fields"])
        assert after_count < before_count

    def test_enrichment_feeds_into_dossier(self, seed_prompts, five_entities):
        from scout.mcp_server import get_scoring_dossier, submit_enrichment
        eid = five_entities[0].id
        submit_enrichment(
            entity_id=eid, source_type="web_research",
            content="AlphaAI is building autonomous drones for agriculture. Team of 12.",
        )
        dossier = get_scoring_dossier(eid)
        # The enrichment content should appear in at least one dimension's dossier
        all_dossiers = " ".join(d["dossier"] for d in dossier["dimensions"].values())
        assert "autonomous drones" in all_dossiers


# ---------------------------------------------------------------------------
# 4. API-Key-Free Scoring
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type", "seed_prompts")
class TestAPIKeyFreeScoring:
    def test_dossier_then_submit(self, five_entities):
        from scout.mcp_server import get_scoring_dossier, submit_score
        eid = five_entities[0].id

        dossier = get_scoring_dossier(eid)
        assert "dimensions" in dossier
        assert set(dossier["dimensions"].keys()) == {"team", "tech", "opportunity"}
        for dim in dossier["dimensions"].values():
            assert "prompt" in dim
            assert "dossier" in dim

        result = submit_score(
            entity_id=eid,
            grade_team="A", grade_tech="B+", grade_opportunity="A-",
            classification="deep_tech",
        )
        assert result["verdict"] in ("reach_out_now", "reach_out_soon", "monitor", "skip")
        assert isinstance(result["score"], (int, float))

    def test_dossier_suggests_submit_score(self, five_entities):
        from scout.mcp_server import get_scoring_dossier
        dossier = get_scoring_dossier(five_entities[0].id)
        next_tools = [n["tool"] for n in dossier.get("next", [])]
        assert any(t in ("submit_score", "score") for t in next_tools)

    def test_verdict_determinism(self, five_entities):
        from scout.mcp_server import submit_score
        eid = five_entities[0].id
        r1 = submit_score(entity_id=eid, grade_team="A", grade_tech="A", grade_opportunity="A",
                          classification="deep_tech")
        r2 = submit_score(entity_id=eid, grade_team="A", grade_tech="A", grade_opportunity="A",
                          classification="deep_tech")
        assert r1["verdict"] == r2["verdict"]
        assert r1["score"] == r2["score"]

    def test_no_api_key_needed(self, five_entities):
        from scout.mcp_server import get_scoring_dossier, submit_score
        eid = five_entities[0].id
        with patch.dict("os.environ", {}, clear=True):
            dossier = get_scoring_dossier(eid)
            assert "dimensions" in dossier
            result = submit_score(entity_id=eid, grade_team="B", grade_tech="B+",
                                  grade_opportunity="B", classification="student_venture")
            assert "verdict" in result


# ---------------------------------------------------------------------------
# 5. Batch Operations
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestBatchOperations:
    def test_bulk_create_then_list(self):
        from scout.mcp_server import list_entities, manage_entity
        items = [
            {"name": f"Batch{i}", "uni": "TUM", "website": f"https://batch{i}.dev"}
            for i in range(5)
        ]
        result = manage_entity(action="bulk_create", items=items)
        assert result["created"] == 5

        listed = list_entities(limit=10)
        assert len(listed) == 5

    def test_bulk_create_deduplication(self):
        from scout.mcp_server import manage_entity
        items = [{"name": "DupCorp", "uni": "TUM"}]
        manage_entity(action="bulk_create", items=items)
        result = manage_entity(action="bulk_create", items=items)
        assert result["skipped_duplicates"] == 1
        assert result["created"] == 0


# ---------------------------------------------------------------------------
# 6. GTM Research Workflow
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestGTMResearchWorkflow:
    def test_progressive_detail_sizes(self, enriched_scored_entities):
        from scout.mcp_server import get_entity, list_entities
        # List view (compact by default)
        listed = list_entities(limit=5)
        list_size = _measure(listed)

        # Compact entity
        eid = enriched_scored_entities[0].id
        compact = get_entity(eid, compact=True)
        compact_size = _measure(compact)

        # Full entity
        full = get_entity(eid, compact=False)
        full_size = _measure(full)

        # Each level should provide more detail
        assert compact_size < full_size

    def test_filter_by_verdict(self, enriched_scored_entities):
        from scout.mcp_server import list_entities
        now = list_entities(verdict="reach_out_now", compact=False)
        assert all(e["verdict"] == "reach_out_now" for e in now)
        assert len(now) == 2

        skip = list_entities(verdict="skip", compact=False)
        assert len(skip) == 1

    def test_compact_list_default(self, enriched_scored_entities):
        from scout.mcp_server import list_entities
        compact = list_entities(limit=5)  # compact=True by default
        full = list_entities(limit=5, compact=False)
        assert _measure(compact) < _measure(full)
        # Compact should have basic fields
        for item in compact:
            assert "id" in item
            assert "name" in item


# ---------------------------------------------------------------------------
# 7. Incremental Enrichment
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestIncrementalEnrichment:
    @pytest.mark.asyncio
    async def test_incremental_skips_filled_targets(self, session, five_entities):
        from scout.mcp_server import enrich_entity

        init = five_entities[0]
        # Pre-fill github fields (simulating prior enrichment)
        init.github_repo_count = 10
        init.github_contributors = 5
        init.github_commits_90d = 100
        init.github_ci_present = True
        session.commit()

        call_log = []

        async def _tracking_enrichment(session, init, crawler=None, *, incremental=True):
            call_log.append({"incremental": incremental})
            # Return a website enrichment (github should be skipped)
            return [_fake_enrichment(init.id, "website")]

        with (
            patch("scout.mcp_server.services.enrich_with_diagnostics") as mock_ewd,
            patch("scout.mcp_server.open_crawler", return_value=AsyncMock()),
        ):
            mock_ewd.return_value = {
                "enrichments_added": 1, "sources_succeeded": ["website"],
                "sources_failed": [], "sources_not_configured": [],
            }
            await enrich_entity(entity_id=init.id, incremental=True)
            # Verify incremental=True was passed through
            mock_ewd.assert_called_once()
            call_kwargs = mock_ewd.call_args
            assert call_kwargs.kwargs.get("incremental") is True

    @pytest.mark.asyncio
    async def test_forced_re_enrichment(self, five_entities):
        from scout.mcp_server import enrich_entity

        with (
            patch("scout.mcp_server.services.enrich_with_diagnostics") as mock_ewd,
        ):
            mock_ewd.return_value = {
                "enrichments_added": 5, "sources_succeeded": ["website", "github"],
                "sources_failed": [], "sources_not_configured": [],
            }
            await enrich_entity(entity_id=five_entities[0].id, incremental=False)
            call_kwargs = mock_ewd.call_args
            assert call_kwargs.kwargs.get("incremental") is False


# ---------------------------------------------------------------------------
# 8. Error Recovery
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestErrorRecovery:
    def test_score_without_api_key(self):
        from scout.mcp_server import score_entity
        with patch.dict("os.environ", {}, clear=True):
            # score_entity is async but _check_api_key returns early
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(score_entity(entity_id=999))
            assert result["error_code"] == "CONFIG_ERROR"
            assert "fix" in result

    def test_entity_not_found(self):
        from scout.mcp_server import get_entity
        result = get_entity(entity_id=99999)
        assert result["error_code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_process_queue_degrades_without_key(self, five_entities):
        from scout.mcp_server import process_queue

        async def _fake_run_enrichment(session, init, crawler=None, *, incremental=True):
            return [_fake_enrichment(init.id)]

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.mcp_server.open_crawler", return_value=AsyncMock()),
        ):
            result = await process_queue(limit=5, score=True)

        # Should still enrich but skip scoring with a warning
        assert result.get("enrichment") is not None
        assert result.get("warning") or result.get("scoring") is None


# ---------------------------------------------------------------------------
# 9. Enrichment Preservation (B1 bug test)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestEnrichmentPreservation:
    @pytest.mark.asyncio
    async def test_llm_enrichment_survives_re_enrich(self, session, five_entities):
        """LLM-submitted enrichments must NOT be deleted when automated enrichment runs."""
        from scout.mcp_server import submit_enrichment

        init = five_entities[0]
        # LLM submits enrichment manually
        submit_enrichment(
            entity_id=init.id, source_type="web_research",
            content="Custom LLM research about AlphaAI",
            source_url="https://news.example.com/alpha",
        )

        # Verify it exists
        enrichments_before = session.execute(
            select(Enrichment).where(Enrichment.initiative_id == init.id)
        ).scalars().all()
        assert any(e.source_type == "web_research" for e in enrichments_before)

        # Now run automated enrichment (simulated)
        from scout.services import run_enrichment
        from scout.enricher import open_crawler

        async def _fake_website_enricher(init, crawler=None):
            return _fake_enrichment(init.id, "website")

        with (
            patch("scout.services.ENRICHER_REGISTRY", {"website": _fake_website_enricher}),
            patch("scout.services._CRAWLER_ENRICHERS", {"website"}),
        ):
            new = await run_enrichment(session, init, crawler=None)
            session.commit()

        # Verify LLM enrichment survived
        enrichments_after = session.execute(
            select(Enrichment).where(Enrichment.initiative_id == init.id)
        ).scalars().all()
        source_types = {e.source_type for e in enrichments_after}
        assert "web_research" in source_types, "LLM-submitted enrichment was deleted!"
        assert "website" in source_types

    def test_submit_enrichment_upsert(self, five_entities):
        """Submitting same source_type+url should update, not create duplicate."""
        from scout.mcp_server import submit_enrichment
        eid = five_entities[0].id
        r1 = submit_enrichment(entity_id=eid, source_type="linkedin",
                               content="First version", source_url="https://linkedin.com/alpha")
        r2 = submit_enrichment(entity_id=eid, source_type="linkedin",
                               content="Updated version", source_url="https://linkedin.com/alpha")

        assert r1["enrichment_id"] == r2["enrichment_id"]


# ---------------------------------------------------------------------------
# 10. Context Window Pressure
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_db", "_patch_entity_type")
class TestContextWindowPressure:
    def test_overview_under_budget(self, five_entities):
        from scout.mcp_server import get_overview
        size = _measure(get_overview())
        assert size < 3000, f"Overview is {size} bytes, expected < 3KB"

    def test_compact_list_under_budget(self, enriched_scored_entities):
        from scout.mcp_server import list_entities
        result = list_entities(limit=20)
        size = _measure(result)
        assert size < 15000, f"Compact list is {size} bytes, expected < 15KB"

    def test_compact_entity_under_budget(self, enriched_scored_entities):
        from scout.mcp_server import get_entity
        result = get_entity(enriched_scored_entities[0].id, compact=True)
        size = _measure(result)
        assert size < 3000, f"Compact entity is {size} bytes, expected < 3KB"

    def test_compact_fields_reduces_size(self, enriched_scored_entities):
        from scout.mcp_server import list_entities
        compact = list_entities(limit=5)  # default compact=True
        full = list_entities(limit=5, compact=False)
        assert _measure(compact) < _measure(full)

    def test_trim_strips_empty_values(self, five_entities):
        from scout.mcp_server import get_entity
        result = get_entity(five_entities[0].id)
        # None and "" should be stripped by _trim
        for key, val in result.items():
            if key.startswith("_"):
                continue
            assert val is not None, f"Key {key} has None value (should be trimmed)"
