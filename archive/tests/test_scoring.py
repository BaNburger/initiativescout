from sqlalchemy import select

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Score
from initiative_tracker.pipeline.score import score_initiatives
from initiative_tracker.store import add_signal, upsert_initiative


def test_scoring_writes_dimension_scores(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'scoring.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Autonomy Lab",
            university="TUM",
            primary_url="https://autonomy.example",
            technologies=["autonomous_systems"],
            markets=["mobility_transport"],
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="technology_domain",
            signal_key="autonomous_systems",
            value=4.0,
            evidence_text="autonomous systems",
            source_type="test",
            source_url="https://autonomy.example",
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="team_metric",
            signal_key="team_size",
            value=40.0,
            evidence_text="40 members",
            source_type="test",
            source_url="https://autonomy.example",
        )
        add_signal(
            session,
            initiative_id=initiative.id,
            signal_type="market_metric",
            signal_key="commercial_mentions",
            value=6.0,
            evidence_text="commercial applications",
            source_type="test",
            source_url="https://autonomy.example",
        )

    details = score_initiatives(db_url=db_url)
    assert details["scores_written"] >= 1

    with session_scope(db_url) as session:
        scores = session.execute(select(Score)).scalars().all()
        assert len(scores) == 1
        score = scores[0]
        assert 1.0 <= score.tech_depth <= 5.0
        assert 1.0 <= score.market_opportunity <= 5.0
        assert 1.0 <= score.team_strength <= 5.0
        assert 1.0 <= score.maturity <= 5.0
