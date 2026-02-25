from __future__ import annotations

import json
from pathlib import Path

from initiative_tracker.config import Settings
from initiative_tracker.results_view import build_html_dashboard, load_result_payload


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    exports_dir = data_dir / "exports"
    reports_dir = tmp_path / "reports" / "latest"
    config_dir = tmp_path / "config"
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        exports_dir=exports_dir,
        reports_dir=reports_dir,
        config_dir=config_dir,
        database_path=data_dir / "initiatives.db",
        seed_markdown_files=[tmp_path / "a.md", tmp_path / "b.md"],
        technology_taxonomy_file=config_dir / "technology_taxonomy.yaml",
        market_taxonomy_file=config_dir / "market_taxonomy.yaml",
        scoring_weights_file=config_dir / "scoring_weights.yaml",
    )


def test_load_result_payload_limits_to_top_n(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    tech = [{"technology_domain": f"t{i}", "opportunity_score": i, "evidence_count": i} for i in range(20)]
    market = [{"market_domain": f"m{i}", "opportunity_score": i, "evidence_count": i} for i in range(20)]
    teams = [{"initiative_name": f"team{i}", "team_strength": i, "composite_score": i} for i in range(20)]
    initiatives = [{"name": f"init{i}", "scores": {"composite_score": 100 - i}, "confidence": 0.5} for i in range(30)]

    (settings.exports_dir / "top_technologies.json").write_text(json.dumps(tech), encoding="utf-8")
    (settings.exports_dir / "top_market_opportunities.json").write_text(json.dumps(market), encoding="utf-8")
    (settings.exports_dir / "top_teams.json").write_text(json.dumps(teams), encoding="utf-8")
    (settings.exports_dir / "initiatives_master.json").write_text(json.dumps(initiatives), encoding="utf-8")

    payload = load_result_payload(settings, top_n=7)
    assert payload["top_n"] == 7
    assert len(payload["technologies"]) == 7
    assert len(payload["markets"]) == 7
    assert len(payload["teams"]) == 7
    assert len(payload["initiatives"]) == 7
    assert payload["total_initiatives"] == 30


def test_build_html_dashboard_writes_interactive_html(tmp_path: Path) -> None:
    payload = {
        "top_n": 3,
        "lens": "outreach",
        "total_initiatives": 8,
        "shortlist": [
            {
                "initiative_id": 1,
                "initiative_name": "Initiative Alpha",
                "score": 4.5,
                "team_strength": 4.3,
                "market_opportunity": 4.1,
                "support_fit": 3.9,
            }
        ],
        "technologies": [
            {"technology_domain": "ai_ml", "score": 4.2, "evidence_count": 10},
        ],
        "markets": [
            {"market_domain": "enterprise_b2b", "score": 3.8, "evidence_count": 6},
        ],
        "teams": [
            {"initiative_name": "Team Alpha", "score": 4.4, "legacy_composite": 4.1},
        ],
        "talent_operators": [{"person_name": "Alice Operator", "score": 4.3, "evidence_count": 4}],
        "talent_alumni": [{"person_name": "Bob Alumni", "score": 4.1, "evidence_count": 3}],
        "initiatives": [
            {"name": "Initiative Alpha", "university": "TUM", "confidence": 0.9},
        ],
        "dossiers": [
            {
                "initiative_id": 1,
                "initiative_name": "Initiative Alpha",
                "university": "TUM",
                "technology_profile": [{"technology_domain": "ai_ml", "stage": "prototype"}],
                "top_talent": [{"name": "Alice Operator", "roles": ["lead"]}],
                "risk_flags": [],
                "action_playbook": {
                    "why_now": "High momentum",
                    "primary_contact": "Alice Operator",
                    "first_meeting_goal": "Validate support fit",
                },
            }
        ],
        "selected_dossier": {
            "initiative_id": 1,
            "initiative_name": "Initiative Alpha",
            "university": "TUM",
            "technology_profile": [{"technology_domain": "ai_ml", "stage": "prototype"}],
            "top_talent": [{"name": "Alice Operator", "roles": ["lead"]}],
            "risk_flags": [],
            "action_playbook": {
                "why_now": "High momentum",
                "primary_contact": "Alice Operator",
                "first_meeting_goal": "Validate support fit",
            },
        },
    }
    output_path = tmp_path / "results_dashboard.html"
    generated = build_html_dashboard(payload, output_path, lens="outreach")

    assert generated.exists()
    html_content = generated.read_text(encoding="utf-8")
    assert "Initiative Tracker Venture Console" in html_content
    assert "Top Technologies" in html_content
    assert "Team Alpha" in html_content
    assert "renderDossier" in html_content
