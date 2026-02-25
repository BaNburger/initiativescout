from sqlalchemy import select

from initiative_tracker.config import Settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Ranking
from initiative_tracker.pipeline.rank import rank_initiatives
from initiative_tracker.store import upsert_initiative
from initiative_tracker.models import Score


def _settings(tmp_path) -> Settings:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "technology_taxonomy.yaml").write_text(
        "technology_domains:\n"
        "  robotics:\n"
        "    - robotics\n"
        "  biotech_synbio:\n"
        "    - synbio\n",
        encoding="utf-8",
    )
    (config_dir / "market_taxonomy.yaml").write_text(
        "market_domains:\n"
        "  mobility_transport:\n"
        "    - mobility\n"
        "  health_medtech:\n"
        "    - health\n",
        encoding="utf-8",
    )
    (config_dir / "scoring_weights.yaml").write_text("dimension_weights: {}\n", encoding="utf-8")
    (config_dir / "technology_aliases.yaml").write_text("aliases: {}\n", encoding="utf-8")
    (config_dir / "market_aliases.yaml").write_text("aliases: {}\n", encoding="utf-8")
    return Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        exports_dir=tmp_path / "data" / "exports",
        reports_dir=tmp_path / "reports" / "latest",
        config_dir=config_dir,
        database_path=tmp_path / "data" / "initiatives.db",
        seed_markdown_files=[tmp_path / "a.md", tmp_path / "b.md"],
        technology_taxonomy_file=config_dir / "technology_taxonomy.yaml",
        market_taxonomy_file=config_dir / "market_taxonomy.yaml",
        scoring_weights_file=config_dir / "scoring_weights.yaml",
        technology_aliases_file=config_dir / "technology_aliases.yaml",
        market_aliases_file=config_dir / "market_aliases.yaml",
    )


def test_rank_generates_three_ranking_types(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'rank.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        one = upsert_initiative(
            session,
            name="Alpha Robotics",
            university="TUM",
            primary_url="https://alpha.example",
            technologies=["robotics"],
            markets=["mobility_transport"],
            team_signals=["team_size:35"],
        )
        two = upsert_initiative(
            session,
            name="Beta Bio",
            university="LMU",
            primary_url="https://beta.example",
            technologies=["biotech_synbio"],
            markets=["health_medtech"],
            team_signals=["team_size:15"],
        )
        session.add(
            Score(
                initiative_id=one.id,
                tech_depth=4.6,
                market_opportunity=4.2,
                team_strength=4.5,
                maturity=3.9,
                composite_score=4.34,
                confidence_tech=0.9,
                confidence_market=0.8,
                confidence_team=0.85,
                confidence_maturity=0.7,
            )
        )
        session.add(
            Score(
                initiative_id=two.id,
                tech_depth=4.1,
                market_opportunity=4.3,
                team_strength=3.8,
                maturity=3.5,
                composite_score=3.95,
                confidence_tech=0.85,
                confidence_market=0.8,
                confidence_team=0.75,
                confidence_maturity=0.7,
            )
        )

    details = rank_initiatives(top_n=15, settings=settings, db_url=db_url)
    assert details["team_items"] >= 1
    assert details["technology_items"] >= 1
    assert details["market_items"] >= 1

    with session_scope(db_url) as session:
        ranking_types = {r.ranking_type for r in session.execute(select(Ranking)).scalars().all()}
        assert {"teams", "technologies", "market_opportunities"}.issubset(ranking_types)
        assert {"outreach_targets", "venture_upside"}.issubset(ranking_types)
