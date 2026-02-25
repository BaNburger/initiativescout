from __future__ import annotations

import json
from pathlib import Path

from initiative_tracker.config import Settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.pipeline.dd_gate import compute_dd_gates
from initiative_tracker.pipeline.dd_score import score_dd
from initiative_tracker.store import (
    upsert_dd_legal_fact,
    upsert_dd_market_fact,
    upsert_dd_team_fact,
    upsert_dd_tech_fact,
    upsert_initiative,
)


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


def test_gate_engine_blocks_market_keyword_inflation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_gates.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(
            session,
            name="Pilot Robotics",
            university="TUM",
            primary_url="https://pilot-robotics.example",
            description_raw="Advanced robotics project",
            technologies=["robotics"],
        )

        upsert_dd_team_fact(
            session,
            initiative_id=initiative.id,
            commitment_level=3.8,
            key_roles=["Founder", "CTO"],
            references_count=2,
            founder_risk_flags=[],
            investable_segment="spinout_candidate",
            is_investable=True,
            evidence=[
                {
                    "source_type": "people",
                    "source_url": "https://pilot-robotics.example/team",
                    "snippet": "John Doe Founder",
                    "doc_id": "team",
                    "confidence": 0.8,
                },
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/team.txt",
                    "snippet": "Technical lead confirmed as CTO with full-time commitment",
                    "doc_id": "team_manual",
                    "confidence": 0.9,
                },
            ],
            source_type="people",
            source_url="https://pilot-robotics.example/team",
            confidence=0.8,
        )

        upsert_dd_tech_fact(
            session,
            initiative_id=initiative.id,
            github_org="pilot",
            github_repo="robotics",
            repo_count=1,
            contributor_count=4,
            commit_velocity_90d=20,
            ci_present=True,
            test_signal=3.0,
            benchmark_artifacts=1,
            prototype_stage="prototype",
            ip_indicators=["publication_hint"],
            evidence=[
                {
                    "source_type": "github_api",
                    "source_url": "https://github.com/pilot/robotics",
                    "snippet": "Commits in 90 days: 20",
                    "doc_id": "commits",
                    "confidence": 0.9,
                },
                {
                    "source_type": "public_signals",
                    "source_url": "https://pilot-robotics.example",
                    "snippet": "Prototype benchmark results published",
                    "doc_id": "bench",
                    "confidence": 0.7,
                },
            ],
            source_type="github_api",
            source_url="https://github.com/pilot/robotics",
            confidence=0.9,
        )

        # Market has boilerplate-only evidence and no customer proof.
        upsert_dd_market_fact(
            session,
            initiative_id=initiative.id,
            customer_interviews=0,
            lois=0,
            pilots=0,
            paid_pilots=0,
            pricing_evidence=True,
            buyer_persona_clarity=2.0,
            sam_som_quality=1.0,
            evidence=[
                {
                    "source_type": "public_signals",
                    "source_url": "https://pilot-robotics.example",
                    "snippet": "About us privacy policy contact us",
                    "doc_id": "boilerplate",
                    "confidence": 0.2,
                }
            ],
            source_type="public_signals",
            source_url="https://pilot-robotics.example",
            confidence=0.2,
        )

        upsert_dd_legal_fact(
            session,
            initiative_id=initiative.id,
            entity_status="incorporated",
            ip_ownership_status="team_owned",
            founder_agreements=True,
            licensing_constraints=False,
            compliance_flags=["privacy_compliance"],
            legal_risk_score=2.8,
            evidence=[
                {
                    "source_type": "public_signals",
                    "source_url": "https://pilot-robotics.example/legal",
                    "snippet": "Entity incorporated and IP assigned",
                    "doc_id": "legal",
                    "confidence": 0.8,
                },
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/legal.pdf",
                    "snippet": "Founder agreements signed",
                    "doc_id": "manual",
                    "confidence": 0.9,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/legal.pdf",
            confidence=0.9,
        )

        no_team = upsert_initiative(
            session,
            name="NoTeam Deeptech",
            university="TUM",
            primary_url="https://noteam.example",
            description_raw="Promising concept but no visible founders",
            technologies=["ai_ml"],
        )
        upsert_dd_tech_fact(
            session,
            initiative_id=no_team.id,
            github_org="noteam",
            github_repo="prototype",
            repo_count=1,
            contributor_count=2,
            commit_velocity_90d=12,
            ci_present=True,
            test_signal=3.0,
            benchmark_artifacts=1,
            prototype_stage="prototype",
            ip_indicators=[],
            evidence=[
                {
                    "source_type": "github_api",
                    "source_url": "https://github.com/noteam/prototype",
                    "snippet": "Commits in 90 days: 12",
                    "doc_id": "commits",
                    "confidence": 0.8,
                },
                {
                    "source_type": "public_signals",
                    "source_url": "https://noteam.example",
                    "snippet": "Prototype benchmark published",
                    "doc_id": "bench",
                    "confidence": 0.7,
                },
            ],
            source_type="github_api",
            source_url="https://github.com/noteam/prototype",
            confidence=0.8,
        )
        upsert_dd_market_fact(
            session,
            initiative_id=no_team.id,
            customer_interviews=12,
            lois=2,
            pilots=1,
            paid_pilots=0,
            pricing_evidence=True,
            buyer_persona_clarity=3.5,
            sam_som_quality=3.0,
            evidence=[
                {
                    "source_type": "public_signals",
                    "source_url": "https://noteam.example",
                    "snippet": "LOI count: 2",
                    "doc_id": "loi",
                    "confidence": 0.8,
                },
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/market.csv",
                    "snippet": "Customer interviews logged",
                    "doc_id": "interviews",
                    "confidence": 0.9,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/market.csv",
            confidence=0.8,
        )
        upsert_dd_legal_fact(
            session,
            initiative_id=no_team.id,
            entity_status="incorporated",
            ip_ownership_status="team_owned",
            founder_agreements=True,
            licensing_constraints=False,
            compliance_flags=[],
            legal_risk_score=2.0,
            evidence=[
                {
                    "source_type": "public_signals",
                    "source_url": "https://noteam.example/legal",
                    "snippet": "Entity incorporated",
                    "doc_id": "legal",
                    "confidence": 0.8,
                },
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/legal2.pdf",
                    "snippet": "IP assignment signed",
                    "doc_id": "legal2",
                    "confidence": 0.9,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/legal2.pdf",
            confidence=0.9,
        )

    score_dd(all_initiatives=True, settings=settings, db_url=db_url)
    details = compute_dd_gates(all_initiatives=True, settings=settings, db_url=db_url)
    assert details["pass_all"] == 0
    assert details["gate_pass"]["A"] == 0

    gates_export = json.loads((settings.exports_dir / "dd_gates.json").read_text(encoding="utf-8"))
    gate_c = [row for row in gates_export if row["gate_name"] == "C" and row["initiative_name"] == "Pilot Robotics"]
    assert gate_c
    assert gate_c[0]["status"] == "fail"

    gate_a_noteam = [row for row in gates_export if row["gate_name"] == "A" and row["initiative_name"] == "NoTeam Deeptech"]
    assert gate_a_noteam
    assert gate_a_noteam[0]["status"] == "fail"
