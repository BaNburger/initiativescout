from __future__ import annotations

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.pipeline.dossiers import build_initiative_dossiers
from initiative_tracker.pipeline.score import score_initiatives
from initiative_tracker.store import add_signal, upsert_initiative


def test_dossier_contains_playbook_and_breakdown(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'dossier.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Dossier Initiative",
            university="LMU",
            primary_url="https://dossier.example",
            technologies=["ai_ml"],
            markets=["health_medtech"],
            team_signals=["winner"],
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="technology_domain",
            signal_key="ai_ml",
            value=3.0,
            evidence_text="AI research and prototypes",
            source_type="test",
            source_url="https://dossier.example",
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="market_metric",
            signal_key="commercial_mentions",
            value=2.0,
            evidence_text="commercial pathway mention",
            source_type="test",
            source_url="https://dossier.example",
        )

    score_initiatives(db_url=db_url)
    dossiers = build_initiative_dossiers(db_url=db_url)

    assert dossiers
    dossier = dossiers[0]
    assert "action_playbook" in dossier
    assert "score_breakdown" in dossier
    assert isinstance(dossier["risk_flags"], list)
    assert "why_now" in dossier["action_playbook"]
