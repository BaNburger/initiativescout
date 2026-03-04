"""Tests for batch MCP tools (batch_enrich, batch_score, process_queue) and helpers."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base, Enrichment, Initiative, OutreachScore, ScoringPrompt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    # Create FTS5 table (normally done by init_db / _ensure_fts_table)
    with eng.begin() as conn:
        conn.execute(__import__("sqlalchemy").text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS initiative_fts USING fts5("
            "name, description, sector, technology_domains, "
            "categories, market_domains, faculty, "
            "content='initiatives', content_rowid='id')"
        ))
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
def three_initiatives(session: Session) -> list[Initiative]:
    inits = []
    for name, uni in [("Alpha", "TUM"), ("Beta", "LMU"), ("Gamma", "HM")]:
        init = Initiative(name=name, uni=uni, website=f"https://{name.lower()}.dev")
        session.add(init)
    session.flush()
    inits = session.execute(select(Initiative).order_by(Initiative.id)).scalars().all()
    return list(inits)


@pytest.fixture()
def enriched_initiatives(session: Session, three_initiatives) -> list[Initiative]:
    """Three initiatives with enrichments (enriched but not scored)."""
    for init in three_initiatives:
        e = Enrichment(
            initiative_id=init.id, source_type="website",
            raw_text=f"Content for {init.name}", summary=f"Summary of {init.name}",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        session.add(e)
    session.flush()
    return three_initiatives


@pytest.fixture()
def _patch_db(SessionFactory):
    """Patch session_scope and get_session to use the test database."""
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


# ---------------------------------------------------------------------------
# _check_api_key tests
# ---------------------------------------------------------------------------


class TestCheckApiKey:
    def test_missing_anthropic_key(self):
        from scout.mcp_server import _check_api_key
        with patch.dict("os.environ", {}, clear=True):
            result = _check_api_key()
            assert result is not None
            assert result["error_code"] == "CONFIG_ERROR"
            assert "ANTHROPIC_API_KEY" in result["error"]

    def test_present_anthropic_key(self):
        from scout.mcp_server import _check_api_key
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test123"}):
            assert _check_api_key() is None

    def test_missing_openai_key(self):
        from scout.mcp_server import _check_api_key
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}, clear=True):
            result = _check_api_key()
            assert result is not None
            assert "OPENAI_API_KEY" in result["error"]

    def test_present_openai_key(self):
        from scout.mcp_server import _check_api_key
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}):
            assert _check_api_key() is None


# ---------------------------------------------------------------------------
# _parse_ids tests
# ---------------------------------------------------------------------------


class TestParseIds:
    def test_none_input(self):
        from scout.mcp_server import _parse_ids
        assert _parse_ids(None) is None

    def test_empty_string(self):
        from scout.mcp_server import _parse_ids
        assert _parse_ids("") is None

    def test_comma_separated(self):
        from scout.mcp_server import _parse_ids
        assert _parse_ids("1,2,3") == [1, 2, 3]

    def test_with_spaces(self):
        from scout.mcp_server import _parse_ids
        assert _parse_ids("1, 2 , 3") == [1, 2, 3]

    def test_skips_non_digits(self):
        from scout.mcp_server import _parse_ids
        assert _parse_ids("1,abc,3") == [1, 3]


# ---------------------------------------------------------------------------
# batch_enrich tests
# ---------------------------------------------------------------------------


def _fake_enrichment(init_id: int) -> Enrichment:
    return Enrichment(
        initiative_id=init_id, source_type="website",
        raw_text="content", summary="summary",
        fetched_at=datetime.now(UTC),
    )


class TestBatchEnrich:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_with_explicit_ids(self, three_initiatives):
        """Explicit IDs are enriched and results are compact."""
        from scout.mcp_server import batch_enrich

        async def _fake_run_enrichment(session, init, crawler=None):
            return [_fake_enrichment(init.id)]

        with (
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            ids_str = ",".join(str(i.id) for i in three_initiatives[:2])
            result = await batch_enrich(initiative_ids=ids_str, limit=20)

        assert result["processed"] == 2
        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert len(result["results"]) == 2
        # Verify compact response
        for item in result["results"]:
            assert set(item.keys()) <= {"id", "name", "ok", "sources", "error"}
            assert item["ok"] is True
            assert item["sources"] == 1
            assert "reasoning" not in item

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_auto_select_from_queue(self, three_initiatives):
        """When no IDs given, auto-selects from work queue."""
        from scout.mcp_server import batch_enrich

        async def _fake_run_enrichment(session, init, crawler=None):
            return [_fake_enrichment(init.id)]

        with (
            patch("scout.mcp_server.services.get_work_queue", return_value=[
                {"id": three_initiatives[0].id, "name": "Alpha",
                 "needs_enrichment": True, "needs_scoring": False},
            ]),
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await batch_enrich(initiative_ids=None, limit=20)

        assert result["processed"] == 1
        assert result["succeeded"] == 1

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_empty_queue(self):
        """When no items need enrichment, returns early."""
        from scout.mcp_server import batch_enrich

        with patch("scout.mcp_server.services.get_work_queue", return_value=[]):
            result = await batch_enrich(initiative_ids=None, limit=20)

        assert result["processed"] == 0
        assert "hint" in result

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_error_isolation(self, three_initiatives):
        """One failure should not stop the batch."""
        from scout.mcp_server import batch_enrich

        call_count = [0]

        async def _flaky_enrichment(session, init, crawler=None):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Network timeout")
            return [_fake_enrichment(init.id)]

        with (
            patch("scout.mcp_server.services.run_enrichment", side_effect=_flaky_enrichment),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            ids_str = ",".join(str(i.id) for i in three_initiatives)
            result = await batch_enrich(initiative_ids=ids_str, limit=50)

        assert result["processed"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1
        # The failed item should have an error message
        failed_items = [r for r in result["results"] if not r["ok"]]
        assert len(failed_items) == 1
        assert "Network timeout" in failed_items[0]["error"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_limit_respected(self, three_initiatives):
        """Limit should cap the number of items processed."""
        from scout.mcp_server import batch_enrich

        async def _fake_run_enrichment(session, init, crawler=None):
            return [_fake_enrichment(init.id)]

        with (
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            ids_str = ",".join(str(i.id) for i in three_initiatives)
            result = await batch_enrich(initiative_ids=ids_str, limit=2)

        assert result["processed"] == 2


# ---------------------------------------------------------------------------
# batch_score tests
# ---------------------------------------------------------------------------


def _fake_score(init_id: int, verdict: str = "reach_out_now", score: float = 4.5) -> OutreachScore:
    return OutreachScore(
        initiative_id=init_id,
        verdict=verdict, score=score, classification="deep_tech",
        grade_team="A", grade_team_num=1.3,
        grade_tech="A-", grade_tech_num=1.7,
        grade_opportunity="B+", grade_opportunity_num=2.0,
        reasoning="Detailed reasoning that should NOT appear in batch results",
        contact_who="CEO", contact_channel="LinkedIn",
        engagement_hook="Mention their award",
        key_evidence_json='["ev1", "ev2"]',
        data_gaps_json='["no github"]',
        scored_at=datetime.now(UTC),
    )


class TestBatchScore:
    @pytest.mark.asyncio
    async def test_checks_api_key_first(self):
        """Should return CONFIG_ERROR before doing any work."""
        from scout.mcp_server import batch_score

        with patch.dict("os.environ", {}, clear=True):
            result = await batch_score(initiative_ids="1,2,3", limit=10)

        assert result["error_code"] == "CONFIG_ERROR"
        assert "ANTHROPIC_API_KEY" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_compact_response_no_reasoning(self, enriched_initiatives):
        """Results should NOT contain reasoning, evidence, or contact details."""
        from scout.mcp_server import batch_score

        async def _fake_run_scoring(session, init, client=None, **kwargs):
            return _fake_score(init.id)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.run_scoring", side_effect=_fake_run_scoring),
        ):
            ids_str = ",".join(str(i.id) for i in enriched_initiatives[:1])
            result = await batch_score(initiative_ids=ids_str, limit=20)

        assert result["succeeded"] == 1
        item = result["results"][0]
        assert item["verdict"] == "reach_out_now"
        assert item["score"] == 4.5
        # Must NOT contain verbose fields
        assert "reasoning" not in item
        assert "key_evidence" not in item
        assert "contact_who" not in item
        assert "engagement_hook" not in item

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_auto_select_empty_queue(self):
        """When no items need scoring, returns early."""
        from scout.mcp_server import batch_score

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.get_work_queue", return_value=[]),
        ):
            result = await batch_score(initiative_ids=None, limit=20)

        assert result["processed"] == 0
        assert "hint" in result

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_verdict_summary(self, enriched_initiatives):
        """Result should include a summary of verdict counts."""
        from scout.mcp_server import batch_score

        verdicts = ["reach_out_now", "reach_out_now", "monitor"]
        call_idx = [0]

        async def _varied_scoring(session, init, client=None, **kwargs):
            v = verdicts[call_idx[0]]
            s = 4.5 if v == "reach_out_now" else 2.0
            call_idx[0] += 1
            return _fake_score(init.id, verdict=v, score=s)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.run_scoring", side_effect=_varied_scoring),
        ):
            ids_str = ",".join(str(i.id) for i in enriched_initiatives)
            result = await batch_score(initiative_ids=ids_str, limit=20)

        assert result["processed"] == 3
        assert result["succeeded"] == 3
        assert result["summary"]["reach_out_now"] == 2
        assert result["summary"]["monitor"] == 1

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_error_isolation(self, enriched_initiatives):
        """One scoring failure should not stop the batch."""
        from scout.mcp_server import batch_score

        call_idx = [0]

        async def _flaky_scoring(session, init, client=None, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 2:
                raise RuntimeError("API rate limited")
            return _fake_score(init.id)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.run_scoring", side_effect=_flaky_scoring),
        ):
            ids_str = ",".join(str(i.id) for i in enriched_initiatives)
            result = await batch_score(initiative_ids=ids_str, limit=20)

        assert result["processed"] == 3
        assert result["succeeded"] == 2
        assert result["failed"] == 1


# ---------------------------------------------------------------------------
# process_queue tests
# ---------------------------------------------------------------------------


class TestProcessQueue:
    @pytest.mark.asyncio
    async def test_checks_api_key_when_scoring(self):
        """Should check API key when score=True."""
        from scout.mcp_server import process_queue

        with patch.dict("os.environ", {}, clear=True):
            result = await process_queue(limit=20, score=True)

        assert result["error_code"] == "CONFIG_ERROR"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_empty_queue(self):
        """Empty queue returns immediately."""
        from scout.mcp_server import process_queue

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.get_work_queue", return_value=[]),
            patch("scout.mcp_server.services.compute_stats", return_value={
                "total": 10, "scored": 10, "enriched": 10,
            }),
        ):
            result = await process_queue(limit=20)

        assert result["enrichment"] is None
        assert result["scoring"] is None
        assert result["remaining_in_queue"] == 0
        assert "empty" in result["hint"].lower()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_enrich_then_score(self, three_initiatives):
        """Items needing enrichment should be enriched first, then scored."""
        from scout.mcp_server import process_queue

        async def _fake_run_enrichment(session, init, crawler=None):
            return [_fake_enrichment(init.id)]

        async def _fake_run_scoring(session, init, client=None, **kwargs):
            return _fake_score(init.id, verdict="monitor", score=2.5)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.get_work_queue", return_value=[
                {"id": three_initiatives[0].id, "name": "Alpha",
                 "needs_enrichment": True, "needs_scoring": True},
                {"id": three_initiatives[1].id, "name": "Beta",
                 "needs_enrichment": False, "needs_scoring": True},
            ]),
            patch("scout.mcp_server.services.compute_stats", return_value={
                "total": 3, "scored": 0, "enriched": 1,
            }),
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.mcp_server.services.run_scoring", side_effect=_fake_run_scoring),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await process_queue(limit=20)

        # Enrichment: only Alpha needs it
        assert result["enrichment"]["processed"] == 1
        assert result["enrichment"]["succeeded"] == 1
        # Scoring: both Beta (already enriched) and Alpha (freshly enriched)
        assert result["scoring"]["processed"] == 2
        assert result["scoring"]["succeeded"] == 2
        assert "remaining_in_queue" in result

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_skip_scoring_when_disabled(self, three_initiatives):
        """When score=False, scoring step is skipped."""
        from scout.mcp_server import process_queue

        async def _fake_run_enrichment(session, init, crawler=None):
            return [_fake_enrichment(init.id)]

        with (
            patch("scout.mcp_server.services.get_work_queue", return_value=[
                {"id": three_initiatives[0].id, "name": "Alpha",
                 "needs_enrichment": True, "needs_scoring": True},
            ]),
            patch("scout.mcp_server.services.compute_stats", return_value={
                "total": 3, "scored": 0, "enriched": 0,
            }),
            patch("scout.mcp_server.services.run_enrichment", side_effect=_fake_run_enrichment),
            patch("scout.enricher.open_crawler") as mock_crawler,
        ):
            mock_crawler.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_crawler.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await process_queue(limit=20, enrich=True, score=False)

        assert result["enrichment"] is not None
        assert result["enrichment"]["succeeded"] == 1
        assert result["scoring"] is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_db")
    async def test_enrich_only_when_needed(self, enriched_initiatives):
        """Items already enriched should go straight to scoring."""
        from scout.mcp_server import process_queue

        async def _fake_run_scoring(session, init, client=None, **kwargs):
            return _fake_score(init.id)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}),
            patch("scout.mcp_server.services.get_work_queue", return_value=[
                {"id": enriched_initiatives[0].id, "name": "Alpha",
                 "needs_enrichment": False, "needs_scoring": True},
            ]),
            patch("scout.mcp_server.services.compute_stats", return_value={
                "total": 3, "scored": 0, "enriched": 3,
            }),
            patch("scout.mcp_server.services.run_scoring", side_effect=_fake_run_scoring),
        ):
            result = await process_queue(limit=20)

        # No enrichment needed
        assert result["enrichment"] is None
        # Scoring happened
        assert result["scoring"]["processed"] == 1
        assert result["scoring"]["succeeded"] == 1
