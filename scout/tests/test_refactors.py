"""Comprehensive tests for all refactored code paths.

Tests are grouped by refactor number to make it easy to trace failures back
to specific changes.
"""
from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scout.models import (
    Base, CustomColumn, Enrichment, Initiative, OutreachScore, Project, ScoringPrompt,
)

# ---------------------------------------------------------------------------
# Fixtures: in-memory SQLite database
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def sample_initiative(session: Session) -> Initiative:
    init = Initiative(
        name="TestBot", uni="TUM", sector="AI", mode="venture",
        description="A test initiative", website="https://testbot.dev",
        email="hi@testbot.dev", relevance="high", sheet_source="spin_off_targets",
        team_page="https://testbot.dev/team", team_size="5",
        linkedin="https://linkedin.com/company/testbot",
        github_org="testbot-org", key_repos="testbot-core",
        sponsors="BMW", competitions="TechCrunch",
        technology_domains="NLP, CV", categories="deep_tech",
        member_count=5, member_examples="Alice (CEO), Bob (CTO)",
        member_roles="CEO, CTO, ML Engineer",
        github_repo_count=10, github_contributors=8,
        github_commits_90d=150, github_ci_present=True,
        huggingface_model_hits=3, openalex_hits=5,
        semantic_scholar_hits=2,
        dd_key_roles="CEO, CTO", dd_references_count=4,
        dd_is_investable=True,
        outreach_now_score=4.5, venture_upside_score=3.8,
        custom_fields_json='{"stage": "pre-seed"}',
        extra_links_json='{"twitter": "https://x.com/testbot"}',
        market_domains="enterprise AI",
        linkedin_hits=12, researchgate_hits=2,
        profile_coverage_score=85, known_url_count=7,
    )
    session.add(init)
    session.flush()
    return init


@pytest.fixture()
def sample_enrichments(session: Session, sample_initiative: Initiative) -> list[Enrichment]:
    enrichments = [
        Enrichment(
            initiative_id=sample_initiative.id, source_type="website",
            raw_text="Website content about TestBot", summary="TestBot builds NLP tools",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        ),
        Enrichment(
            initiative_id=sample_initiative.id, source_type="team_page",
            raw_text="Team page: Alice, Bob, Charlie", summary="3 co-founders",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        ),
        Enrichment(
            initiative_id=sample_initiative.id, source_type="github",
            raw_text="GitHub org: 10 repos, 8 contributors", summary="Active GitHub",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        ),
    ]
    for e in enrichments:
        session.add(e)
    session.flush()
    return enrichments


@pytest.fixture()
def sample_score(session: Session, sample_initiative: Initiative) -> OutreachScore:
    score = OutreachScore(
        initiative_id=sample_initiative.id, project_id=None,
        verdict="reach_out_now", score=4.5, classification="deep_tech",
        reasoning="Strong team and tech", contact_who="Alice, CEO",
        contact_channel="linkedin", engagement_hook="Impressed by your NLP work",
        key_evidence_json='["Strong team", "Active GitHub"]',
        data_gaps_json='["No funding data"]',
        grade_team="A", grade_team_num=1.3,
        grade_tech="A-", grade_tech_num=1.7,
        grade_opportunity="B+", grade_opportunity_num=2.0,
        llm_model="claude-haiku-4-5-20251001",
        scored_at=datetime(2024, 6, 2, tzinfo=UTC),
    )
    session.add(score)
    session.flush()
    return score


@pytest.fixture()
def sample_project(session: Session, sample_initiative: Initiative) -> Project:
    proj = Project(
        initiative_id=sample_initiative.id, name="Sub Project",
        description="A side project", website="https://sub.testbot.dev",
        github_url="https://github.com/testbot-org/sub",
        team="Alice, Dave",
        extra_links_json='{"demo": "https://demo.testbot.dev"}',
    )
    session.add(proj)
    session.flush()
    return proj


# =========================================================================
# Refactor #9: json_parse in utils.py
# =========================================================================

class TestJsonParse:
    def test_valid_json(self):
        from scout.utils import json_parse
        assert json_parse('{"a": 1}') == {"a": 1}

    def test_invalid_json_default(self):
        from scout.utils import json_parse
        assert json_parse("not json", []) == []

    def test_invalid_json_no_default(self):
        from scout.utils import json_parse
        assert json_parse("bad") == {}

    def test_none_input(self):
        from scout.utils import json_parse
        assert json_parse(None) == {}

    def test_none_with_default(self):
        from scout.utils import json_parse
        assert json_parse(None, "fallback") == "fallback"

    def test_empty_string(self):
        from scout.utils import json_parse
        assert json_parse("", []) == []

    def test_services_reexports(self):
        from scout import services
        assert services.json_parse('{"x": 1}') == {"x": 1}


# =========================================================================
# Refactor #2: get_entity in services.py
# =========================================================================

class TestGetEntity:
    def test_found(self, session, sample_initiative):
        from scout.services import get_entity
        result = get_entity(session, Initiative, sample_initiative.id)
        assert result is not None
        assert result.name == "TestBot"

    def test_not_found(self, session):
        from scout.services import get_entity
        result = get_entity(session, Initiative, 9999)
        assert result is None

    def test_different_model(self, session, sample_project):
        from scout.services import get_entity
        result = get_entity(session, Project, sample_project.id)
        assert result is not None
        assert result.name == "Sub Project"


# =========================================================================
# Refactor #3: validate_db_name in db.py
# =========================================================================

class TestValidateDbName:
    def test_valid_name(self):
        from scout.db import validate_db_name
        assert validate_db_name("my-database_1") == "my-database_1"

    def test_strips_whitespace(self):
        from scout.db import validate_db_name
        assert validate_db_name("  test  ") == "test"

    def test_empty_raises(self):
        from scout.db import validate_db_name
        with pytest.raises(ValueError, match="Invalid database name"):
            validate_db_name("")

    def test_invalid_chars_raises(self):
        from scout.db import validate_db_name
        with pytest.raises(ValueError, match="Invalid database name"):
            validate_db_name("my database!")

    def test_whitespace_only_raises(self):
        from scout.db import validate_db_name
        with pytest.raises(ValueError, match="Invalid database name"):
            validate_db_name("   ")


# =========================================================================
# Refactor #1: session_scope and session_generator in db.py
# =========================================================================

class TestSessionManagement:
    def test_session_scope(self, engine):
        from scout.db import session_scope, _engine, _SessionLocal
        import scout.db as db_mod
        # Temporarily wire up the module globals
        orig_engine = db_mod._engine
        orig_session = db_mod._SessionLocal
        try:
            db_mod._engine = engine
            db_mod._SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            with session_scope() as sess:
                assert isinstance(sess, Session)
                init = Initiative(name="ScopeTest", uni="LMU")
                sess.add(init)
                sess.commit()
                assert init.id is not None
        finally:
            db_mod._engine = orig_engine
            db_mod._SessionLocal = orig_session

    def test_session_scope_rollback(self, engine):
        from scout.db import session_scope
        import scout.db as db_mod
        orig_engine = db_mod._engine
        orig_session = db_mod._SessionLocal
        try:
            db_mod._engine = engine
            db_mod._SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            with pytest.raises(ValueError):
                with session_scope() as sess:
                    sess.add(Initiative(name="WillFail", uni="X"))
                    sess.flush()
                    raise ValueError("boom")
        finally:
            db_mod._engine = orig_engine
            db_mod._SessionLocal = orig_session

    def test_session_generator(self, engine):
        from scout.db import session_generator
        import scout.db as db_mod
        orig_engine = db_mod._engine
        orig_session = db_mod._SessionLocal
        try:
            db_mod._engine = engine
            db_mod._SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            gen = session_generator()
            sess = next(gen)
            assert isinstance(sess, Session)
            try:
                gen.throw(GeneratorExit)
            except GeneratorExit:
                pass
        finally:
            db_mod._engine = orig_engine
            db_mod._SessionLocal = orig_session


# =========================================================================
# Refactor #6: score_response_dict in services.py
# =========================================================================

class TestScoreResponseDict:
    def test_basic(self, session, sample_initiative, sample_score):
        from scout.services import score_response_dict
        result = score_response_dict(sample_score)
        assert result["verdict"] == "reach_out_now"
        assert result["score"] == 4.5
        assert result["classification"] == "deep_tech"
        assert result["grade_team"] == "A"
        assert result["grade_tech"] == "A-"
        assert result["grade_opportunity"] == "B+"
        # Basic mode should NOT include extended fields
        assert "reasoning" not in result
        assert "key_evidence" not in result

    def test_extended(self, session, sample_initiative, sample_score):
        from scout.services import score_response_dict
        result = score_response_dict(sample_score, extended=True)
        assert result["verdict"] == "reach_out_now"
        assert result["reasoning"] == "Strong team and tech"
        assert result["contact_who"] == "Alice, CEO"
        assert result["contact_channel"] == "linkedin"
        assert result["engagement_hook"] == "Impressed by your NLP work"
        assert result["key_evidence"] == ["Strong team", "Active GitHub"]
        assert result["data_gaps"] == ["No funding data"]


# =========================================================================
# Refactor #10: update_custom_column uses apply_updates
# =========================================================================

class TestUpdateCustomColumn:
    def test_update_label(self, session):
        from scout.services import create_custom_column, update_custom_column
        col = create_custom_column(session, key="test_col", label="Old Label", col_type="text")
        assert col is not None
        result = update_custom_column(session, col["id"], label="New Label")
        assert result is not None
        assert result["label"] == "New Label"
        assert result["col_type"] == "text"  # unchanged

    def test_update_multiple_fields(self, session):
        from scout.services import create_custom_column, update_custom_column
        col = create_custom_column(session, key="multi", label="Multi")
        result = update_custom_column(
            session, col["id"],
            label="Updated", col_type="number", show_in_list=False, sort_order=5,
        )
        assert result["label"] == "Updated"
        assert result["col_type"] == "number"
        assert result["show_in_list"] is False
        assert result["sort_order"] == 5

    def test_update_not_found(self, session):
        from scout.services import update_custom_column
        result = update_custom_column(session, 9999, label="x")
        assert result is None


# =========================================================================
# Refactor #8: _ensure_client helper
# =========================================================================

class TestEnsureClient:
    def test_returns_existing(self):
        from scout.services import _ensure_client
        from scout.scorer import LLMClient
        mock_client = MagicMock(spec=LLMClient)
        assert _ensure_client(mock_client) is mock_client

    def test_creates_default(self):
        from scout.services import _ensure_client
        with patch("scout.services.LLMClient") as MockClient:
            result = _ensure_client(None)
            MockClient.assert_called_once()
            assert result is MockClient.return_value


# =========================================================================
# Refactor #11: services.create_project
# =========================================================================

class TestCreateProject:
    def test_basic_creation(self, session, sample_initiative):
        from scout.services import create_project
        proj = create_project(
            session, sample_initiative.id,
            name="New Project", description="Desc",
            website="https://proj.dev",
        )
        session.commit()
        assert proj.id is not None
        assert proj.name == "New Project"
        assert proj.description == "Desc"
        assert proj.website == "https://proj.dev"
        assert proj.initiative_id == sample_initiative.id

    def test_with_extra_links(self, session, sample_initiative):
        from scout.services import create_project
        proj = create_project(
            session, sample_initiative.id,
            name="Linked Project",
            extra_links={"demo": "https://demo.dev"},
        )
        session.commit()
        assert json.loads(proj.extra_links_json) == {"demo": "https://demo.dev"}

    def test_none_values_become_empty_string(self, session, sample_initiative):
        from scout.services import create_project
        proj = create_project(
            session, sample_initiative.id,
            name="Minimal",
            description=None, website=None,
        )
        session.commit()
        assert proj.description == ""
        assert proj.website == ""


# =========================================================================
# Refactor #4: Dossier builder base helper
# =========================================================================

class TestDossierBuilders:
    def test_team_dossier(self, sample_initiative, sample_enrichments):
        from scout.scorer import build_team_dossier
        dossier = build_team_dossier(sample_initiative, sample_enrichments)
        assert "INITIATIVE: TestBot" in dossier
        assert "UNIVERSITY: TUM" in dossier
        assert "DESCRIPTION: A test initiative" in dossier
        assert "TEAM SIZE: 5" in dossier
        assert "LINKEDIN: https://linkedin.com/company/testbot" in dossier
        assert "SPONSORS: BMW" in dossier
        # Should include team_page and website enrichments
        assert "TEAM_PAGE DATA" in dossier
        assert "WEBSITE DATA" in dossier
        # Should NOT include github enrichment
        assert "GITHUB DATA" not in dossier

    def test_tech_dossier(self, sample_initiative, sample_enrichments):
        from scout.scorer import build_tech_dossier
        dossier = build_tech_dossier(sample_initiative, sample_enrichments)
        assert "INITIATIVE: TestBot" in dossier
        assert "TECHNOLOGY DOMAINS: NLP, CV" in dossier
        assert "GITHUB ORG: testbot-org" in dossier
        assert "GITHUB CI/CD: Present" in dossier
        # Should include github enrichment only
        assert "GITHUB DATA" in dossier
        assert "TEAM PAGE DATA" not in dossier
        assert "WEBSITE DATA" not in dossier

    def test_full_dossier(self, sample_initiative, sample_enrichments):
        from scout.scorer import build_full_dossier
        dossier = build_full_dossier(sample_initiative, sample_enrichments)
        assert "INITIATIVE: TestBot" in dossier
        assert "SECTOR: AI" in dossier
        assert "MODE: venture" in dossier
        assert "DUE DILIGENCE: Flagged as investable" in dossier
        # Should include ALL enrichments
        assert "WEBSITE DATA" in dossier
        assert "TEAM_PAGE DATA" in dossier
        assert "GITHUB DATA" in dossier

    def test_project_dossier(self, sample_project, sample_initiative):
        from scout.scorer import build_project_dossier
        dossier = build_project_dossier(sample_project, sample_initiative)
        assert "PROJECT: Sub Project" in dossier
        assert "PARENT INITIATIVE: TestBot" in dossier
        assert "UNIVERSITY: TUM" in dossier
        assert "SECTOR: AI" in dossier
        assert "DESCRIPTION: A side project" in dossier
        assert "WEBSITE: https://sub.testbot.dev" in dossier
        assert "PARENT INITIATIVE DESCRIPTION: A test initiative" in dossier
        assert "SPONSORS & PARTNERS: BMW" in dossier
        # Extra links
        assert "DEMO: https://demo.testbot.dev" in dossier

    def test_empty_enrichments(self, sample_initiative):
        from scout.scorer import build_team_dossier, build_tech_dossier, build_full_dossier
        team = build_team_dossier(sample_initiative, [])
        tech = build_tech_dossier(sample_initiative, [])
        full = build_full_dossier(sample_initiative, [])
        # All should still have the header
        for dossier in (team, tech, full):
            assert "INITIATIVE: TestBot" in dossier
            assert "UNIVERSITY: TUM" in dossier

    def test_bool_field_rendering(self, sample_initiative, sample_enrichments):
        """Boolean fields should render as label-only (no ': True')."""
        from scout.scorer import build_tech_dossier
        dossier = build_tech_dossier(sample_initiative, sample_enrichments)
        assert "GITHUB CI/CD: Present" in dossier
        assert "GITHUB CI/CD: Present: True" not in dossier

    def test_falsy_fields_excluded(self, session):
        """Fields with falsy values (0, '', None) should be omitted."""
        from scout.scorer import build_team_dossier
        init = Initiative(name="Sparse", uni="HM")
        session.add(init)
        session.flush()
        dossier = build_team_dossier(init, [])
        assert "TEAM SIZE" not in dossier
        assert "LINKEDIN" not in dossier
        assert "SPONSORS" not in dossier


# =========================================================================
# Refactor #5: Unified initiative summary dict
# =========================================================================

class TestInitiativeSummaryDict:
    def test_summary_keys(self, session, sample_initiative, sample_enrichments, sample_score):
        from scout.services import initiative_summary
        summary = initiative_summary(sample_initiative)
        expected_keys = {
            "id", "name", "uni", "sector", "mode", "description",
            "website", "email", "relevance", "sheet_source",
            "enriched", "enriched_at",
            "verdict", "score", "classification", "reasoning",
            "contact_who", "contact_channel", "engagement_hook",
            "grade_team", "grade_team_num", "grade_tech", "grade_tech_num",
            "grade_opportunity", "grade_opportunity_num",
            "key_evidence", "data_gaps",
            "technology_domains", "categories", "member_count",
            "outreach_now_score", "venture_upside_score",
            "custom_fields",
        }
        assert set(summary.keys()) == expected_keys

    def test_summary_values(self, session, sample_initiative, sample_enrichments, sample_score):
        from scout.services import initiative_summary
        summary = initiative_summary(sample_initiative)
        assert summary["id"] == sample_initiative.id
        assert summary["name"] == "TestBot"
        assert summary["enriched"] is True
        assert summary["verdict"] == "reach_out_now"
        assert summary["custom_fields"] == {"stage": "pre-seed"}

    def test_summary_unenriched(self, session, sample_initiative):
        from scout.services import initiative_summary
        summary = initiative_summary(sample_initiative)
        assert summary["enriched"] is False
        assert summary["enriched_at"] is None

    def test_summary_unscored(self, session, sample_initiative, sample_enrichments):
        from scout.services import initiative_summary
        summary = initiative_summary(sample_initiative)
        assert summary["verdict"] is None
        assert summary["score"] is None

    def test_detail_extends_summary(self, session, sample_initiative, sample_enrichments, sample_score):
        from scout.services import initiative_detail, initiative_summary
        summary = initiative_summary(sample_initiative)
        detail = initiative_detail(sample_initiative)
        # Detail should have all summary keys plus extra
        for key in summary:
            assert key in detail, f"Detail missing summary key: {key}"
        # Detail-specific keys
        assert "enrichments" in detail
        assert "projects" in detail
        assert "team_page" in detail
        assert "extra_links" in detail


# =========================================================================
# Refactor #7: _llm_error helper
# =========================================================================

class TestLlmErrorHelper:
    def test_with_retryable(self):
        from scout.mcp_server import _llm_error
        from scout.scorer import LLMCallError
        exc = LLMCallError("API timeout", retryable=True)
        result = _llm_error(exc)
        assert result["error"] == "Scoring failed: API timeout"
        assert result["error_code"] == "LLM_ERROR"
        assert result["retryable"] is True

    def test_without_retryable(self):
        from scout.mcp_server import _llm_error
        exc = RuntimeError("unexpected")
        result = _llm_error(exc)
        assert result["error"] == "Scoring failed: unexpected"
        assert result["error_code"] == "LLM_ERROR"
        assert result["retryable"] is False


# =========================================================================
# Integration: Services CRUD operations
# =========================================================================

class TestServicesCRUD:
    def test_create_initiative(self, session):
        from scout.services import create_initiative, initiative_detail
        init = create_initiative(
            session, name="NewInit", uni="LMU",
            sector="FinTech", website="https://newinit.dev",
        )
        session.commit()
        assert init.id is not None
        assert init.name == "NewInit"
        detail = initiative_detail(init)
        assert detail["name"] == "NewInit"
        assert detail["uni"] == "LMU"

    def test_delete_initiative(self, session, sample_initiative):
        from scout.services import delete_initiative, get_entity
        assert delete_initiative(session, sample_initiative.id) is True
        assert get_entity(session, Initiative, sample_initiative.id) is None

    def test_delete_initiative_not_found(self, session):
        from scout.services import delete_initiative
        assert delete_initiative(session, 9999) is False

    def test_custom_column_lifecycle(self, session):
        from scout.services import (
            create_custom_column, get_custom_columns,
            update_custom_column, delete_custom_column,
        )
        # Create
        col = create_custom_column(session, key="lifecycle_test", label="Life")
        assert col is not None
        assert col["key"] == "lifecycle_test"

        # Read
        cols = get_custom_columns(session)
        assert any(c["key"] == "lifecycle_test" for c in cols)

        # Update
        updated = update_custom_column(session, col["id"], label="Updated Life")
        assert updated["label"] == "Updated Life"

        # Delete
        assert delete_custom_column(session, col["id"]) is True
        assert delete_custom_column(session, col["id"]) is False  # already deleted

    def test_apply_updates(self, session, sample_initiative):
        from scout.services import apply_updates, UPDATABLE_FIELDS
        apply_updates(sample_initiative, {"name": "Renamed", "sector": "BioTech"}, UPDATABLE_FIELDS)
        assert sample_initiative.name == "Renamed"
        assert sample_initiative.sector == "BioTech"

    def test_apply_updates_ignores_none(self, session, sample_initiative):
        from scout.services import apply_updates, UPDATABLE_FIELDS
        original_name = sample_initiative.name
        apply_updates(sample_initiative, {"name": None}, UPDATABLE_FIELDS)
        assert sample_initiative.name == original_name


# =========================================================================
# Integration: project_summary
# =========================================================================

class TestProjectSummary:
    def test_project_summary_shape(self, session, sample_project):
        from scout.services import project_summary
        result = project_summary(sample_project)
        assert result["id"] == sample_project.id
        assert result["name"] == "Sub Project"
        assert result["initiative_id"] == sample_project.initiative_id
        assert result["extra_links"] == {"demo": "https://demo.testbot.dev"}
        assert result["verdict"] is None  # no scores yet


# =========================================================================
# Integration: scorer deterministic functions
# =========================================================================

class TestScorerDeterministic:
    def test_compute_verdict(self):
        from scout.scorer import compute_verdict
        assert compute_verdict(1.0) == "reach_out_now"
        assert compute_verdict(1.7) == "reach_out_now"
        assert compute_verdict(2.0) == "reach_out_soon"
        assert compute_verdict(2.7) == "reach_out_soon"
        assert compute_verdict(3.0) == "monitor"
        assert compute_verdict(3.3) == "monitor"
        assert compute_verdict(3.5) == "skip"
        assert compute_verdict(4.0) == "skip"

    def test_compute_score(self):
        from scout.scorer import compute_score
        assert compute_score(1.0) == 4.0
        assert compute_score(4.0) == 1.0
        assert compute_score(2.5) == 2.5

    def test_compute_data_gaps(self, sample_initiative):
        from scout.scorer import compute_data_gaps
        # With all enrichments present
        from scout.models import Enrichment
        enrichments = [
            Enrichment(initiative_id=1, source_type="website", fetched_at=datetime.now(UTC)),
            Enrichment(initiative_id=1, source_type="team_page", fetched_at=datetime.now(UTC)),
            Enrichment(initiative_id=1, source_type="github", fetched_at=datetime.now(UTC)),
        ]
        gaps = compute_data_gaps(sample_initiative, enrichments)
        assert "No website enrichment" not in str(gaps)
        assert "No contact email" not in str(gaps)

    def test_compute_data_gaps_missing(self, session):
        init = Initiative(name="Gappy", uni="TUM")
        session.add(init)
        session.flush()
        from scout.scorer import compute_data_gaps
        gaps = compute_data_gaps(init, [])
        assert len(gaps) >= 3  # website, team_page, github missing at minimum

    def test_validate_grade(self):
        from scout.scorer import _validate_grade
        assert _validate_grade("A+") == "A+"
        assert _validate_grade("a-") == "A-"
        assert _validate_grade("invalid") == "C"
        assert _validate_grade(None) == "C"
        assert _validate_grade("  B  ") == "B"


# =========================================================================
# Integration: latest_score_fields
# =========================================================================

class TestLatestScoreFields:
    def test_no_scores(self):
        from scout.services import latest_score_fields
        result = latest_score_fields([])
        assert result["verdict"] is None
        assert result["key_evidence"] == []
        assert result["data_gaps"] == []

    def test_with_scores(self, sample_score):
        from scout.services import latest_score_fields
        result = latest_score_fields([sample_score])
        assert result["verdict"] == "reach_out_now"
        assert result["grade_team"] == "A"
        assert result["key_evidence"] == ["Strong team", "Active GitHub"]

    def test_picks_latest(self, session, sample_initiative):
        older = OutreachScore(
            initiative_id=sample_initiative.id, verdict="monitor", score=2.0,
            grade_team="C", grade_team_num=3.3,
            grade_tech="C", grade_tech_num=3.3,
            grade_opportunity="C", grade_opportunity_num=3.3,
            scored_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        newer = OutreachScore(
            initiative_id=sample_initiative.id, verdict="reach_out_now", score=4.5,
            grade_team="A", grade_team_num=1.3,
            grade_tech="A", grade_tech_num=1.3,
            grade_opportunity="A", grade_opportunity_num=1.3,
            scored_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        session.add_all([older, newer])
        session.flush()
        from scout.services import latest_score_fields
        result = latest_score_fields([older, newer])
        assert result["verdict"] == "reach_out_now"


# =========================================================================
# Integration: importer uses json_parse
# =========================================================================

class TestImporterJsonParse:
    def test_upsert_merges_extra_links(self, session, sample_initiative):
        from scout.importer import _upsert, _normalize_key
        existing_map = {_normalize_key("TestBot", "TUM"): sample_initiative}
        data = {
            "name": "TestBot", "uni": "TUM",
            "sheet_source": "all_initiatives",
            "extra_links_json": '{"github": "https://github.com/testbot-org"}',
        }
        is_new, init = _upsert(session, data, existing_map)
        assert is_new is False
        links = json.loads(init.extra_links_json)
        assert "twitter" in links  # original
        assert "github" in links  # new


# =========================================================================
# Full module import smoke tests
# =========================================================================

class TestModuleImports:
    """Verify all modules import cleanly after refactoring."""

    def test_import_utils(self):
        from scout.utils import json_parse
        assert callable(json_parse)

    def test_import_db(self):
        from scout.db import validate_db_name, session_scope, session_generator
        assert callable(validate_db_name)
        assert callable(session_scope)
        assert callable(session_generator)

    def test_import_services(self):
        from scout.services import (
            get_entity, score_response_dict, create_project,
            _ensure_client, _build_initiative_dict,
        )
        assert callable(get_entity)
        assert callable(score_response_dict)
        assert callable(create_project)

    def test_import_scorer(self):
        from scout.scorer import (
            _build_dossier, build_team_dossier, build_tech_dossier,
            build_full_dossier, build_project_dossier,
        )
        assert callable(_build_dossier)
        assert callable(build_team_dossier)

    def test_import_app(self):
        from scout.app import app
        assert app is not None

    def test_import_mcp_server(self):
        from scout.mcp_server import mcp, _llm_error
        assert mcp is not None
        assert callable(_llm_error)

    def test_import_enricher(self):
        from scout.enricher import enrich_website, enrich_team_page, enrich_github
        assert callable(enrich_website)

    def test_import_importer(self):
        from scout.importer import import_xlsx
        assert callable(import_xlsx)

    def test_import_schemas(self):
        from scout.schemas import InitiativeOut, InitiativeDetail, ProjectOut
        assert InitiativeOut is not None
