from __future__ import annotations

import json
from pathlib import Path

from initiative_tracker.config import Settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.pipeline.dd_report import generate_dd_report
from initiative_tracker.store import add_dd_score, upsert_dd_gate, upsert_initiative


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


def test_dd_report_is_deterministic_and_actionable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_report.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Memo Robotics",
            university="TUM",
            primary_url="https://memo.example",
        )
        for gate in ["A", "B", "C", "D"]:
            upsert_dd_gate(session, initiative_id=initiative.id, gate_name=gate, status="pass", reason="ok", evidence=[])

        add_dd_score(
            session,
            initiative_id=initiative.id,
            team_dd=4.1,
            tech_dd=4.3,
            market_dd=4.0,
            execution_dd=3.8,
            legal_dd=3.9,
            conviction_score=4.1,
        )

    first = generate_dd_report(top_n=15, settings=settings, db_url=db_url)
    second = generate_dd_report(top_n=15, settings=settings, db_url=db_url)

    assert first["memos_generated"] == 1
    assert second["memos_generated"] == 1

    memos = json.loads((settings.exports_dir / "investment_memos.json").read_text(encoding="utf-8"))
    assert len(memos) == 1
    memo = memos[0]

    assert memo["initiative_name"] == "Memo Robotics"
    assert memo["decision"] in {"invest", "monitor", "pass"}
    assert "Gate pass=" in memo["rationale"]
    assert isinstance(memo["top_risks"], list)
    assert isinstance(memo["next_actions"], list)
    assert memo["next_actions"]

    brief = (settings.reports_dir / "due_diligence_brief.md").read_text(encoding="utf-8")
    assert "Due Diligence Brief" in brief
    assert "Memo Robotics" in brief
