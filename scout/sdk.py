"""Thin context API for user scripts.

Scripts receive a ``ScriptContext`` instance as ``ctx`` in their namespace.
It provides entity CRUD, enrichment creation, HTTP access, and logging —
everything a script needs to interact with Scout data without importing
internal modules.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from scout.models import Enrichment, Initiative, OutreachScore
from scout.utils import json_parse


class ScriptContext:
    """Execution context injected into every script as ``ctx``."""

    def __init__(self, session: Session, *, entity_id: int | None = None):
        self._session = session
        self.entity_id = entity_id
        self._logs: list[str] = []
        self._result: Any = None
        self._result_set = False
        self.http = httpx.Client(timeout=30, follow_redirects=True)

    # -- Logging -----------------------------------------------------------

    def log(self, msg: str) -> None:
        """Append a message to the execution log."""
        self._logs.append(str(msg))

    # -- Result ------------------------------------------------------------

    def result(self, data: Any) -> None:
        """Set the return value of the script."""
        self._result = data
        self._result_set = True

    # -- Entity read -------------------------------------------------------

    def entity(self, entity_id: int | None = None) -> dict:
        """Get a single entity as a dict. Uses ``self.entity_id`` if omitted."""
        eid = entity_id or self.entity_id
        if eid is None:
            raise ValueError("No entity_id provided")
        init = self._session.execute(
            select(Initiative).where(Initiative.id == eid)
        ).scalars().first()
        if init is None:
            raise ValueError(f"Entity {eid} not found")
        return _entity_to_dict(init)

    def entities(
        self,
        *,
        verdict: str | None = None,
        search: str | None = None,
        uni: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query entities with simple filters. Returns list of dicts."""
        from scout.services import query_entities
        items, _ = query_entities(
            self._session,
            verdict=verdict,
            search=search,
            uni=uni,
            per_page=min(limit, 500),
        )
        return items

    # -- Entity write ------------------------------------------------------

    def update(self, entity_id: int | None = None, **fields: Any) -> dict:
        """Update fields on an entity. Returns updated entity dict."""
        eid = entity_id or self.entity_id
        if eid is None:
            raise ValueError("No entity_id provided")
        init = self._session.execute(
            select(Initiative).where(Initiative.id == eid)
        ).scalars().first()
        if init is None:
            raise ValueError(f"Entity {eid} not found")
        for key, value in fields.items():
            init.set_field(key, value)
        self._session.commit()
        return _entity_to_dict(init)

    def create(self, **fields: Any) -> dict:
        """Create a new entity. Returns the created entity dict."""
        from scout.services import create_entity
        init = create_entity(self._session, **fields)
        self._session.commit()
        return _entity_to_dict(init)

    # -- Enrichment --------------------------------------------------------

    def enrich(
        self,
        entity_id: int | None = None,
        *,
        source_type: str = "script",
        source_url: str = "",
        raw_text: str = "",
        summary: str = "",
        fields: dict | None = None,
    ) -> int:
        """Add an enrichment to an entity. Returns the enrichment ID."""
        eid = entity_id or self.entity_id
        if eid is None:
            raise ValueError("No entity_id provided")
        enrichment = Enrichment(
            initiative_id=eid,
            source_type=source_type,
            source_url=source_url,
            raw_text=raw_text[:15000] if raw_text else "",
            summary=summary[:1500] if summary else "",
            structured_fields_json=json.dumps(fields) if fields else "{}",
            fetched_at=datetime.now(UTC),
        )
        self._session.add(enrichment)
        # Apply structured fields to entity if provided
        if fields:
            init = self._session.execute(
                select(Initiative).where(Initiative.id == eid)
            ).scalars().first()
            if init:
                from scout.services import apply_enrichment_fields
                apply_enrichment_fields(init, fields)
        self._session.commit()
        return enrichment.id

    # -- Read scores -------------------------------------------------------

    def scores(self, entity_id: int | None = None) -> list[dict]:
        """Get all scores for an entity, newest first."""
        eid = entity_id or self.entity_id
        if eid is None:
            raise ValueError("No entity_id provided")
        rows = self._session.execute(
            select(OutreachScore)
            .where(OutreachScore.initiative_id == eid, OutreachScore.project_id.is_(None))
            .order_by(OutreachScore.scored_at.desc())
        ).scalars().all()
        return [_score_to_dict(s) for s in rows]

    def enrichments(self, entity_id: int | None = None) -> list[dict]:
        """Get all enrichments for an entity."""
        eid = entity_id or self.entity_id
        if eid is None:
            raise ValueError("No entity_id provided")
        rows = self._session.execute(
            select(Enrichment)
            .where(Enrichment.initiative_id == eid)
            .order_by(Enrichment.fetched_at.desc())
        ).scalars().all()
        return [
            {"id": e.id, "source_type": e.source_type, "source_url": e.source_url,
             "summary": e.summary, "raw_text_length": len(e.raw_text or ""),
             "fetched_at": e.fetched_at.isoformat() if e.fetched_at else None}
            for e in rows
        ]

    # -- Prompts -----------------------------------------------------------

    def prompt(self, name: str) -> str:
        """Read a stored prompt by name. Returns the content string.

        Checks general Prompt table first, falls back to ScoringPrompt.
        """
        from scout.models import ScoringPrompt
        try:
            from scout.models import Prompt
            p = self._session.execute(
                select(Prompt).where(Prompt.name == name)
            ).scalars().first()
            if p:
                return p.content
        except Exception:
            pass  # Prompt table may not exist yet
        # Fallback to scoring prompts
        sp = self._session.execute(
            select(ScoringPrompt).where(ScoringPrompt.key == name)
        ).scalars().first()
        if sp:
            return sp.content
        raise ValueError(f"Prompt '{name}' not found")

    # -- Credentials -------------------------------------------------------

    def secret(self, name: str) -> str:
        """Read a stored credential by name. Falls back to env var."""
        from scout.services import get_credential
        val = get_credential(self._session, name)
        if val is not None:
            return val
        # Fallback: treat name as env var key
        env_val = os.environ.get(name, "")
        if env_val:
            return env_val
        raise ValueError(f"Credential '{name}' not found (checked DB and env)")

    # -- Environment -------------------------------------------------------

    def env(self, key: str, default: str = "") -> str:
        """Read an environment variable (for API keys etc.)."""
        return os.environ.get(key, default)

    # -- Cleanup -----------------------------------------------------------

    def _close(self) -> None:
        """Release resources."""
        self.http.close()


def _entity_to_dict(init: Initiative) -> dict:
    """Convert an entity to a plain dict with all non-empty fields."""
    d = {"id": init.id}
    d.update(init.all_fields())
    return d


def _score_to_dict(s: OutreachScore) -> dict:
    """Convert a score to a plain dict."""
    d = {
        "verdict": s.verdict, "score": s.score,
        "classification": s.classification, "reasoning": s.reasoning,
        "grade_team": s.grade_team, "grade_tech": s.grade_tech,
        "grade_opportunity": s.grade_opportunity,
        "scored_at": s.scored_at.isoformat() if s.scored_at else None,
    }
    dg = json_parse(s.dimension_grades_json)
    if dg:
        d["dimension_grades"] = dg
    return d
