"""Tests for the script store, executor, SDK, prompt store, and script-enricher pipeline.

Covers: CRUD operations, script execution, SDK context methods,
import restrictions, timeout handling, MCP tool integration, prompt CRUD,
ctx.scores/enrichments/prompt, and script-enricher pipeline integration.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from scout import services
from scout.executor import run_script
from scout.models import Base, Enrichment, Initiative, OutreachScore, Prompt, Script


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    # Create FTS table so enrichment tests don't fail on FTS auto-sync
    try:
        from scout.db import _ensure_fts_table
        _ensure_fts_table(eng)
    except Exception:
        pass
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
def sample_entity(session: Session) -> Initiative:
    init = Initiative(name="Test Entity", uni="TUM", website="https://example.com")
    session.add(init)
    session.commit()
    return init


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


# ---------------------------------------------------------------------------
# Script CRUD (services layer)
# ---------------------------------------------------------------------------


class TestScriptCRUD:
    def test_save_and_read(self, session: Session):
        result = services.save_script(
            session, name="test_script", code="ctx.result(42)",
            description="A test", script_type="custom",
        )
        session.commit()
        assert result["name"] == "test_script"
        assert result["code"] == "ctx.result(42)"
        assert result["script_type"] == "custom"

        read = services.get_script(session, "test_script")
        assert read is not None
        assert read["code"] == "ctx.result(42)"

    def test_save_upsert(self, session: Session):
        services.save_script(session, name="s1", code="v1")
        session.commit()
        services.save_script(session, name="s1", code="v2", description="updated")
        session.commit()

        read = services.get_script(session, "s1")
        assert read["code"] == "v2"
        assert read["description"] == "updated"

        # Should still be only one script
        scripts = services.list_scripts(session)
        assert len(scripts) == 1

    def test_list_scripts(self, session: Session):
        services.save_script(session, name="a", code="1", script_type="enricher")
        services.save_script(session, name="b", code="2", script_type="connector")
        services.save_script(session, name="c", code="3", script_type="enricher")
        session.commit()

        all_scripts = services.list_scripts(session)
        assert len(all_scripts) == 3

        enrichers = services.list_scripts(session, script_type="enricher")
        assert len(enrichers) == 2

        # List should not include code
        assert "code" not in all_scripts[0]

    def test_delete_script(self, session: Session):
        services.save_script(session, name="to_delete", code="x")
        session.commit()
        assert services.delete_script(session, "to_delete") is True
        session.commit()
        assert services.get_script(session, "to_delete") is None

    def test_delete_nonexistent(self, session: Session):
        assert services.delete_script(session, "nope") is False

    def test_get_script_code(self, session: Session):
        services.save_script(session, name="s", code="hello")
        session.commit()
        assert services.get_script_code(session, "s") == "hello"
        assert services.get_script_code(session, "missing") is None

    def test_invalid_script_type(self, session: Session):
        with pytest.raises(ValueError, match="Invalid script_type"):
            services.save_script(session, name="bad", code="x", script_type="invalid")


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class TestExecutor:
    def test_simple_result(self, session: Session):
        result = run_script("ctx.result(42)", session)
        assert result["ok"] is True
        assert result["result"] == 42
        assert result["error"] is None

    def test_logging(self, session: Session):
        code = 'ctx.log("hello")\nctx.log("world")\nctx.result("done")'
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["logs"] == ["hello", "world"]

    def test_print_captured(self, session: Session):
        result = run_script('print("printed!")\nctx.result(True)', session)
        assert result["ok"] is True
        assert "printed!" in result["logs"]

    def test_syntax_error(self, session: Session):
        result = run_script("def bad(", session)
        assert result["ok"] is False
        assert result["error"] is not None

    def test_runtime_error(self, session: Session):
        result = run_script("1/0", session)
        assert result["ok"] is False
        assert "ZeroDivision" in result["error"]

    def test_allowed_imports(self, session: Session):
        result = run_script("import json\nctx.result(json.dumps([1]))", session)
        assert result["ok"] is True
        assert result["result"] == "[1]"

    def test_blocked_imports(self, session: Session):
        result = run_script("import os", session)
        assert result["ok"] is False
        assert "not allowed" in result["error"]

    def test_httpx_import_allowed(self, session: Session):
        result = run_script("import httpx\nctx.result(True)", session)
        assert result["ok"] is True

    def test_duration_tracked(self, session: Session):
        result = run_script("ctx.result(1)", session)
        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], int)


# ---------------------------------------------------------------------------
# SDK context
# ---------------------------------------------------------------------------


class TestSDK:
    def test_entity_read(self, session: Session, sample_entity: Initiative):
        eid = sample_entity.id
        code = f"e = ctx.entity({eid})\nctx.result(e['name'])"
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["result"] == "Test Entity"

    def test_entity_id_from_context(self, session: Session, sample_entity: Initiative):
        code = "e = ctx.entity()\nctx.result(e['name'])"
        result = run_script(code, session, entity_id=sample_entity.id)
        assert result["ok"] is True
        assert result["result"] == "Test Entity"

    def test_entity_not_found(self, session: Session):
        code = "ctx.entity(99999)"
        result = run_script(code, session)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_update_entity(self, session: Session, sample_entity: Initiative):
        eid = sample_entity.id
        code = f'ctx.update({eid}, sector="AI")\nctx.result("ok")'
        result = run_script(code, session)
        assert result["ok"] is True

        # Verify the update persisted
        session.refresh(sample_entity)
        assert sample_entity.sector == "AI"

    def test_create_entity(self, session: Session):
        code = 'e = ctx.create(name="New One", uni="LMU")\nctx.result(e["id"])'
        result = run_script(code, session)
        assert result["ok"] is True
        assert isinstance(result["result"], int)

    def test_enrich_entity(self, session: Session, sample_entity: Initiative):
        eid = sample_entity.id
        code = f"""
ctx.enrich({eid}, source_type="script_test", raw_text="Data from script",
           fields={{"sector": "HealthTech"}})
ctx.result("enriched")
"""
        result = run_script(code, session)
        assert result["ok"] is True

        # Check enrichment was created
        from sqlalchemy import select
        enrichments = session.execute(
            select(Enrichment).where(Enrichment.initiative_id == eid)
        ).scalars().all()
        assert len(enrichments) == 1
        assert enrichments[0].source_type == "script_test"

    def test_entities_query(self, session: Session, sample_entity: Initiative):
        code = 'items = ctx.entities(limit=10)\nctx.result(len(items))'
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["result"] >= 1

    def test_env_access(self, session: Session):
        code = 'ctx.result(ctx.env("PATH", "fallback"))'
        result = run_script(code, session)
        assert result["ok"] is True
        # PATH should exist on any system
        assert result["result"] != "fallback"

    def test_http_client(self, session: Session):
        # Just verify ctx.http is available (don't make actual requests in tests)
        code = 'ctx.result(type(ctx.http).__name__)'
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["result"] == "Client"


# ---------------------------------------------------------------------------
# MCP tool integration
# ---------------------------------------------------------------------------


class TestMCPTools:
    def test_script_save_and_list(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import script

        # Save
        result = script(action="save", name="test1", code="ctx.result(1)",
                        description="test script")
        assert result["ok"] is True
        assert result["action"] == "saved"

        # List
        result = script(action="list")
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["scripts"][0]["name"] == "test1"

    def test_script_read(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import script

        script(action="save", name="readable", code="ctx.result(42)")
        result = script(action="read", name="readable")
        assert result["ok"] is True
        assert result["code"] == "ctx.result(42)"

    def test_script_delete(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import script

        script(action="save", name="deletable", code="x")
        result = script(action="delete", name="deletable")
        assert result["ok"] is True

        result = script(action="read", name="deletable")
        assert "error" in result

    def test_script_not_found(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import script

        result = script(action="read", name="nonexistent")
        assert "error" in result
        assert result["error_code"] == "NOT_FOUND"

    def test_run_script_tool(self, session, _patch_db, _patch_entity_type, sample_entity):
        from scout.mcp_server import script, run_script as mcp_run

        script(action="save", name="runner", code="ctx.result('hello')")
        result = mcp_run(name="runner")
        assert result["ok"] is True
        assert result["result"] == "hello"

    def test_run_script_not_found(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import run_script as mcp_run

        result = mcp_run(name="nonexistent")
        assert "error" in result
        assert result["error_code"] == "NOT_FOUND"

    def test_invalid_action(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import script

        result = script(action="explode")
        assert "error" in result
        assert result["error_code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Phase 2: SDK enhanced read access
# ---------------------------------------------------------------------------


class TestSDKReadAccess:
    def test_scores(self, session: Session, sample_entity: Initiative):
        eid = sample_entity.id
        score = OutreachScore(
            initiative_id=eid, verdict="monitor", score=3.0,
            classification="deep_tech", grade_team="B", grade_tech="B+",
            grade_opportunity="B-", grade_team_num=3.0, grade_tech_num=2.7,
            grade_opportunity_num=3.3, reasoning="test",
            scored_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        session.add(score)
        session.commit()

        code = f"scores = ctx.scores({eid})\nctx.result(scores[0]['verdict'])"
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["result"] == "monitor"

    def test_enrichments(self, session: Session, sample_entity: Initiative):
        eid = sample_entity.id
        e = Enrichment(
            initiative_id=eid, source_type="website",
            source_url="https://example.com", raw_text="data",
            summary="summary", fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        session.add(e)
        session.commit()

        code = f"enrs = ctx.enrichments({eid})\nctx.result(enrs[0]['source_type'])"
        result = run_script(code, session)
        assert result["ok"] is True
        assert result["result"] == "website"

    def test_prompt_read(self, session: Session):
        p = Prompt(name="test_prompt", content="You are a helpful assistant.",
                   prompt_type="custom")
        session.add(p)
        session.commit()

        code = 'ctx.result(ctx.prompt("test_prompt"))'
        result = run_script(code, session)
        assert result["ok"] is True
        assert "helpful assistant" in result["result"]

    def test_prompt_not_found(self, session: Session):
        code = 'ctx.prompt("nonexistent")'
        result = run_script(code, session)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# Phase 2: Script-enricher pipeline
# ---------------------------------------------------------------------------


class TestScriptEnrichers:
    def test_script_enricher_runs_in_pipeline(self, session: Session, sample_entity: Initiative):
        """Script with type='enricher' should be picked up by _run_script_enrichers."""
        # Save an enricher script
        services.save_script(
            session, name="test_enricher",
            code='ctx.enrich(source_type="script_auto", raw_text="Auto enriched data")',
            script_type="enricher",
        )
        session.commit()

        # Run the script enricher function directly
        from scout.services import _run_script_enrichers
        new = _run_script_enrichers(session, sample_entity)

        # The enrichment should have been created via ctx.enrich()
        from sqlalchemy import select
        enrichments = session.execute(
            select(Enrichment).where(
                Enrichment.initiative_id == sample_entity.id,
                Enrichment.source_type == "script_auto",
            )
        ).scalars().all()
        assert len(enrichments) >= 1

    def test_script_enricher_entity_type_filter(self, session: Session, sample_entity: Initiative):
        """Script enricher with wrong entity_type should be skipped."""
        services.save_script(
            session, name="wrong_type",
            code='ctx.enrich(source_type="should_not_run", raw_text="nope")',
            script_type="enricher",
            entity_type="professor",  # won't match "initiative"
        )
        session.commit()

        from scout.services import _run_script_enrichers
        with patch("scout.db.get_entity_type", return_value="initiative"):
            _run_script_enrichers(session, sample_entity)

        from sqlalchemy import select
        enrichments = session.execute(
            select(Enrichment).where(
                Enrichment.initiative_id == sample_entity.id,
                Enrichment.source_type == "should_not_run",
            )
        ).scalars().all()
        assert len(enrichments) == 0


# ---------------------------------------------------------------------------
# Phase 3: Prompt CRUD (services layer)
# ---------------------------------------------------------------------------


class TestPromptCRUD:
    def test_save_and_read(self, session: Session):
        result = services.save_prompt(
            session, name="classify", content="Classify this entity.",
            description="Classification prompt", prompt_type="classification",
        )
        session.commit()
        assert result["name"] == "classify"
        assert result["content"] == "Classify this entity."

        read = services.get_prompt(session, "classify")
        assert read is not None
        assert read["prompt_type"] == "classification"

    def test_save_upsert(self, session: Session):
        services.save_prompt(session, name="p1", content="v1")
        session.commit()
        services.save_prompt(session, name="p1", content="v2", description="updated")
        session.commit()

        read = services.get_prompt(session, "p1")
        assert read["content"] == "v2"
        assert read["description"] == "updated"

        prompts = services.list_prompts(session)
        assert len(prompts) == 1

    def test_list_prompts(self, session: Session):
        services.save_prompt(session, name="a", content="1", prompt_type="enrichment")
        services.save_prompt(session, name="b", content="2", prompt_type="analysis")
        services.save_prompt(session, name="c", content="3", prompt_type="enrichment")
        session.commit()

        all_prompts = services.list_prompts(session)
        assert len(all_prompts) == 3

        enrichment = services.list_prompts(session, prompt_type="enrichment")
        assert len(enrichment) == 2

        # List should not include content
        assert "content" not in all_prompts[0]

    def test_delete_prompt(self, session: Session):
        services.save_prompt(session, name="to_delete", content="x")
        session.commit()
        assert services.delete_prompt(session, "to_delete") is True
        session.commit()
        assert services.get_prompt(session, "to_delete") is None

    def test_delete_nonexistent(self, session: Session):
        assert services.delete_prompt(session, "nope") is False

    def test_invalid_prompt_type(self, session: Session):
        with pytest.raises(ValueError, match="Invalid prompt_type"):
            services.save_prompt(session, name="bad", content="x", prompt_type="invalid")


# ---------------------------------------------------------------------------
# Phase 3: Prompt MCP tool
# ---------------------------------------------------------------------------


class TestPromptMCPTool:
    def test_prompt_save_and_list(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import prompt

        result = prompt(action="save", name="test_p", content="Hello {name}",
                        description="greeting prompt")
        assert result["ok"] is True
        assert result["action"] == "saved"

        result = prompt(action="list")
        assert result["ok"] is True
        assert result["count"] == 1

    def test_prompt_read(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import prompt

        prompt(action="save", name="readable", content="Read me.")
        result = prompt(action="read", name="readable")
        assert result["ok"] is True
        assert result["content"] == "Read me."

    def test_prompt_delete(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import prompt

        prompt(action="save", name="deletable", content="x")
        result = prompt(action="delete", name="deletable")
        assert result["ok"] is True

        result = prompt(action="read", name="deletable")
        assert "error" in result

    def test_prompt_not_found(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import prompt

        result = prompt(action="read", name="nonexistent")
        assert result["error_code"] == "NOT_FOUND"

    def test_prompt_invalid_action(self, session, _patch_db, _patch_entity_type):
        from scout.mcp_server import prompt

        result = prompt(action="explode")
        assert result["error_code"] == "VALIDATION_ERROR"
