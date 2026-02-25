from __future__ import annotations

from sqlalchemy import select

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Ranking, Score
from initiative_tracker.pipeline.rank import rank_initiatives
from initiative_tracker.store import add_talent_score, upsert_initiative, upsert_person


def test_rank_writes_talent_rankings(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'talent_rank.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Talent Initiative",
            university="TUM",
            primary_url="https://talent.example",
            technologies=["ai_ml"],
            markets=["enterprise_b2b"],
        )
        session.add(
            Score(
                initiative_id=initiative.id,
                tech_depth=4.2,
                market_opportunity=3.9,
                team_strength=4.1,
                maturity=3.7,
                composite_score=4.0,
                confidence_tech=0.7,
                confidence_market=0.7,
                confidence_team=0.7,
                confidence_maturity=0.7,
                actionability_0_6m=3.8,
                support_fit=3.6,
                outreach_now_score=3.9,
                venture_upside_score=4.0,
                confidence_actionability=0.6,
                confidence_support_fit=0.6,
            )
        )

        operator = upsert_person(
            session,
            name="Op Person",
            person_type="operator",
            contact_channels=["op@example.com"],
            source_urls=["https://talent.example/team"],
            confidence=0.8,
        )
        alumni = upsert_person(
            session,
            name="Alumni Angel",
            person_type="alumni_angel",
            contact_channels=["https://www.linkedin.com/in/alumni-angel"],
            source_urls=["https://talent.example/alumni"],
            confidence=0.8,
        )

        add_talent_score(
            session,
            person_id=operator.id,
            talent_type="operators",
            reachability=4.2,
            operator_strength=4.4,
            investor_relevance=1.0,
            network_score=3.8,
            composite_score=4.1,
            confidence=0.7,
            reasons=["operator test"],
        )
        add_talent_score(
            session,
            person_id=alumni.id,
            talent_type="alumni_angels",
            reachability=3.8,
            operator_strength=1.0,
            investor_relevance=4.6,
            network_score=4.2,
            composite_score=4.3,
            confidence=0.7,
            reasons=["alumni test"],
        )

    rank_initiatives(db_url=db_url)

    with session_scope(db_url) as session:
        ranking_types = {row.ranking_type for row in session.execute(select(Ranking)).scalars().all()}
        assert "talent_operators" in ranking_types
        assert "talent_alumni_angels" in ranking_types
