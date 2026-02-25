from __future__ import annotations

from sqlalchemy import select

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Score, ScoreComponent, ScoreEvidence
from initiative_tracker.pipeline.score import score_initiatives
from initiative_tracker.store import add_signal, upsert_initiative


def test_score_persists_component_breakdown_and_evidence(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'explain.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Explainable Team",
            university="TUM",
            primary_url="https://explainable.example",
            technologies=["ai_ml"],
            markets=["enterprise_b2b"],
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="technology_domain",
            signal_key="ai_ml",
            value=4.0,
            evidence_text="AI model development and deployment",
            source_type="test",
            source_url="https://explainable.example",
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="team_metric",
            signal_key="team_size",
            value=35.0,
            evidence_text="35 members",
            source_type="test",
            source_url="https://explainable.example",
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="market_metric",
            signal_key="commercial_mentions",
            value=5.0,
            evidence_text="commercial pilots with industry",
            source_type="test",
            source_url="https://explainable.example",
        )

    score_initiatives(db_url=db_url)

    with session_scope(db_url) as session:
        score = session.execute(select(Score)).scalars().first()
        components = session.execute(select(ScoreComponent)).scalars().all()
        evidences = session.execute(select(ScoreEvidence)).scalars().all()

        assert score is not None
        assert 1.0 <= score.outreach_now_score <= 5.0
        assert 1.0 <= score.venture_upside_score <= 5.0
        assert any(row.dimension == "actionability_0_6m" for row in components)
        assert any(row.dimension == "support_fit" for row in components)
        assert evidences
