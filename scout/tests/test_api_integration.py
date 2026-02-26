"""Integration tests for FastAPI endpoints after refactoring.

Uses TestClient to verify HTTP-level behavior is preserved.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from scout.models import Base, Enrichment, Initiative, OutreachScore, Project


@pytest.fixture()
def test_db():
    """Create a temporary SQLite in-memory database for testing.

    Uses StaticPool so all connections share the same in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, TestSession


@pytest.fixture()
def client(test_db):
    """FastAPI TestClient using in-memory database."""
    engine, TestSession = test_db
    from scout.app import app, db_session

    def override_db_session():
        session = TestSession()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[db_session] = override_db_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, TestSession
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded_client(client):
    """Client with a sample initiative pre-seeded."""
    c, TestSession = client
    session = TestSession()
    init = Initiative(
        name="TestAPI", uni="TUM", sector="AI",
        website="https://testapi.dev", email="test@api.dev",
    )
    session.add(init)
    session.commit()
    session.refresh(init)
    init_id = init.id
    session.close()
    return c, TestSession, init_id


class TestInitiativeEndpoints:
    def test_list_initiatives(self, seeded_client):
        c, _, _ = seeded_client
        resp = c.get("/api/initiatives")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1
        item = data["items"][0]
        # Verify all expected keys from the unified dict
        assert "id" in item
        assert "name" in item
        assert "enriched" in item
        assert "verdict" in item
        assert "custom_fields" in item

    def test_get_initiative(self, seeded_client):
        c, _, init_id = seeded_client
        resp = c.get(f"/api/initiatives/{init_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestAPI"
        assert data["uni"] == "TUM"
        assert "enrichments" in data
        assert "projects" in data

    def test_get_initiative_404(self, client):
        c, _ = client
        resp = c.get("/api/initiatives/9999")
        assert resp.status_code == 404

    def test_update_initiative(self, seeded_client):
        c, _, init_id = seeded_client
        resp = c.put(f"/api/initiatives/{init_id}", json={"sector": "BioTech"})
        assert resp.status_code == 200
        assert resp.json()["sector"] == "BioTech"

    def test_update_initiative_custom_fields(self, seeded_client):
        c, _, init_id = seeded_client
        resp = c.put(f"/api/initiatives/{init_id}", json={"custom_fields": {"stage": "seed"}})
        assert resp.status_code == 200
        assert resp.json()["custom_fields"]["stage"] == "seed"


class TestProjectEndpoints:
    def test_create_project(self, seeded_client):
        c, _, init_id = seeded_client
        resp = c.post(f"/api/initiatives/{init_id}/projects", json={
            "name": "Side Project", "description": "Testing create_project",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Side Project"
        assert data["initiative_id"] == init_id

    def test_create_project_with_extra_links(self, seeded_client):
        c, _, init_id = seeded_client
        resp = c.post(f"/api/initiatives/{init_id}/projects", json={
            "name": "Linked", "extra_links": {"demo": "https://demo.dev"},
        })
        assert resp.status_code == 201
        assert resp.json()["extra_links"]["demo"] == "https://demo.dev"

    def test_update_project(self, seeded_client):
        c, _, init_id = seeded_client
        create_resp = c.post(f"/api/initiatives/{init_id}/projects", json={"name": "ToUpdate"})
        proj_id = create_resp.json()["id"]
        resp = c.put(f"/api/projects/{proj_id}", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_delete_project(self, seeded_client):
        c, _, init_id = seeded_client
        create_resp = c.post(f"/api/initiatives/{init_id}/projects", json={"name": "ToDelete"})
        proj_id = create_resp.json()["id"]
        resp = c.delete(f"/api/projects/{proj_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_list_projects(self, seeded_client):
        c, _, init_id = seeded_client
        c.post(f"/api/initiatives/{init_id}/projects", json={"name": "P1"})
        c.post(f"/api/initiatives/{init_id}/projects", json={"name": "P2"})
        resp = c.get(f"/api/initiatives/{init_id}/projects")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2


class TestDatabaseEndpoints:
    def test_list_databases(self, client):
        c, _ = client
        with patch("scout.app.list_databases", return_value=["scout", "test"]):
            with patch("scout.app.current_db_name", return_value="scout"):
                resp = c.get("/api/databases")
                assert resp.status_code == 200
                data = resp.json()
                assert "databases" in data
                assert "current" in data

    def test_select_database_invalid(self, client):
        c, _ = client
        resp = c.post("/api/databases/select", json={"name": "bad name!"})
        assert resp.status_code == 400

    def test_create_database_invalid(self, client):
        c, _ = client
        resp = c.post("/api/databases/create", json={"name": ""})
        assert resp.status_code == 400


class TestCustomColumnEndpoints:
    def test_list_custom_columns(self, client):
        c, _ = client
        resp = c.get("/api/custom-columns")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_custom_column(self, client):
        c, _ = client
        resp = c.post("/api/custom-columns", json={
            "key": "test_api_col", "label": "API Test",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["key"] == "test_api_col"
        assert data["label"] == "API Test"

    def test_create_duplicate_column(self, client):
        c, _ = client
        c.post("/api/custom-columns", json={"key": "dup", "label": "First"})
        resp = c.post("/api/custom-columns", json={"key": "dup", "label": "Second"})
        assert resp.status_code == 409

    def test_update_custom_column(self, client):
        c, _ = client
        create_resp = c.post("/api/custom-columns", json={"key": "upd", "label": "Old"})
        col_id = create_resp.json()["id"]
        resp = c.put(f"/api/custom-columns/{col_id}", json={"label": "New"})
        assert resp.status_code == 200
        assert resp.json()["label"] == "New"

    def test_delete_custom_column(self, client):
        c, _ = client
        create_resp = c.post("/api/custom-columns", json={"key": "del", "label": "Del"})
        col_id = create_resp.json()["id"]
        resp = c.delete(f"/api/custom-columns/{col_id}")
        assert resp.status_code == 200

    def test_delete_nonexistent_column(self, client):
        c, _ = client
        resp = c.delete("/api/custom-columns/9999")
        assert resp.status_code == 404


class TestStatsEndpoint:
    def test_get_stats(self, seeded_client):
        c, _, _ = seeded_client
        resp = c.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "enriched" in data
        assert "scored" in data
        assert data["total"] >= 1


class TestResetEndpoint:
    def test_reset(self, seeded_client):
        c, _, _ = seeded_client
        resp = c.delete("/api/reset")
        assert resp.status_code == 200
        # Verify data is gone
        list_resp = c.get("/api/initiatives")
        assert list_resp.json()["total"] == 0
