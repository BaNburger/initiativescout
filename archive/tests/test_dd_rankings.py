from __future__ import annotations

import json
from pathlib import Path

from initiative_tracker.config import Settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Score
from initiative_tracker.pipeline.dd_rank import rank_dd
from initiative_tracker.store import add_dd_score, upsert_dd_gate, upsert_dd_team_fact, upsert_initiative


def _settings(tmp_path: Path) -> Settings:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "technology_taxonomy.yaml").write_text("technology_domains: {}\n", encoding="utf-8")
    (config_dir / "market_taxonomy.yaml").write_text("market_domains: {}\n", encoding="utf-8")
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


def test_dd_ranking_excludes_non_investable_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_rank.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        investable = upsert_initiative(session, name="DeepTech Spinout", university="TUM", primary_url="https://deeptech.example")
        watch = upsert_initiative(session, name="General Student Club", university="LMU", primary_url="https://club.example")

        session.add(
            Score(
                initiative_id=investable.id,
                tech_depth=4.5,
                market_opportunity=4.0,
                team_strength=4.1,
                maturity=3.6,
                composite_score=4.1,
                confidence_tech=0.8,
                confidence_market=0.8,
                confidence_team=0.8,
                confidence_maturity=0.8,
                actionability_0_6m=4.0,
                support_fit=3.9,
                outreach_now_score=4.1,
                venture_upside_score=4.2,
                confidence_actionability=0.7,
                confidence_support_fit=0.7,
            )
        )
        session.add(
            Score(
                initiative_id=watch.id,
                tech_depth=2.1,
                market_opportunity=2.0,
                team_strength=2.2,
                maturity=2.0,
                composite_score=2.1,
                confidence_tech=0.4,
                confidence_market=0.4,
                confidence_team=0.4,
                confidence_maturity=0.4,
                actionability_0_6m=1.8,
                support_fit=1.9,
                outreach_now_score=1.9,
                venture_upside_score=2.0,
                confidence_actionability=0.4,
                confidence_support_fit=0.4,
            )
        )

        upsert_dd_team_fact(
            session,
            initiative_id=investable.id,
            commitment_level=4.1,
            key_roles=["Founder", "CTO"],
            references_count=2,
            founder_risk_flags=[],
            investable_segment="spinout_candidate",
            is_investable=True,
            evidence=[{"source_type": "people", "source_url": "https://deeptech.example/team", "snippet": "Named founders"}],
            source_type="people",
            source_url="https://deeptech.example/team",
            confidence=0.8,
        )
        upsert_dd_team_fact(
            session,
            initiative_id=watch.id,
            commitment_level=1.8,
            key_roles=["Member"],
            references_count=0,
            founder_risk_flags=["segment_not_investable"],
            investable_segment="non_investable_club",
            is_investable=False,
            evidence=[{"source_type": "public_signals", "source_url": "https://club.example", "snippet": "General club"}],
            source_type="public_signals",
            source_url="https://club.example",
            confidence=0.4,
        )

        for gate in ["A", "B", "C", "D"]:
            upsert_dd_gate(session, initiative_id=investable.id, gate_name=gate, status="pass", reason="ok", evidence=[])
        upsert_dd_gate(session, initiative_id=watch.id, gate_name="A", status="fail", reason="no team", evidence=[])
        upsert_dd_gate(session, initiative_id=watch.id, gate_name="B", status="fail", reason="no tech", evidence=[])
        upsert_dd_gate(session, initiative_id=watch.id, gate_name="C", status="fail", reason="no market", evidence=[])
        upsert_dd_gate(session, initiative_id=watch.id, gate_name="D", status="fail", reason="no legal", evidence=[])

        add_dd_score(
            session,
            initiative_id=investable.id,
            team_dd=4.2,
            tech_dd=4.4,
            market_dd=4.0,
            execution_dd=3.8,
            legal_dd=3.9,
            conviction_score=4.13,
        )

    details = rank_dd(top_n=15, investable_only=True, settings=settings, db_url=db_url)
    assert details["investable_items"] == 1
    assert details["watchlist_items"] >= 1

    investable_rows = json.loads((settings.exports_dir / "investable_rankings.json").read_text(encoding="utf-8"))
    watchlist_rows = json.loads((settings.exports_dir / "watchlist_rankings.json").read_text(encoding="utf-8"))

    assert len(investable_rows) == 1
    assert investable_rows[0]["initiative_name"] == "DeepTech Spinout"
    assert any(row["initiative_name"] == "General Student Club" for row in watchlist_rows)
