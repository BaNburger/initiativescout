from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from initiative_tracker.cli import app
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
    (config_dir / "dd_rubric.yaml").write_text(
        """
team_dd:
  components:
    tech_skill_fit: 0.45
    product_skill_fit: 0.30
    sales_skill_fit: 0.25
tech_dd:
  components:
    quality: 0.35
    performance: 0.25
    scalability: 0.25
    moat_signal: 0.15
market_dd:
  components:
    validation_stage: 0.60
    icp_pricing_clarity: 0.25
    sales_cycle_realism: 0.15
execution_dd:
  components:
    tech_outcomes: 0.50
    market_outcomes: 0.50
legal_dd:
  components:
    entity_ip_basics: 0.70
    compliance_risk_penalty: 0.30
conviction_weights:
  team_dd: 0.32
  tech_dd: 0.28
  market_dd: 0.25
  execution_dd: 0.10
  legal_dd: 0.05
validation_stage_scores:
  none: 1.0
  interviews: 1.8
  loi: 2.8
  pilot: 3.6
  paid_pilot: 4.4
  repeat_revenue: 5.0
quality_threshold: 0.55
no_evidence_floor: 1.0
no_evidence_confidence_penalty: 0.12
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (config_dir / "dd_gate_thresholds.yaml").write_text(
        """
quality_threshold: 0.55
gate_a:
  team_dd_min: 3.2
  team_tech_fit_min: 3.5
  min_named_operators: 2
  min_technical_leads: 1
  min_qualifying_evidence: 2
gate_b:
  tech_dd_min: 3.0
  min_source_classes: 2
  min_qualifying_evidence: 2
  require_hard_proof_artifact: true
gate_c:
  market_dd_min: 3.0
  min_stage: loi
  min_source_classes: 2
  min_qualifying_evidence: 2
gate_d:
  legal_dd_min: 2.5
  require_entity_known: true
  require_ip_known: true
  max_legal_risk_score: 3.5
  forbid_critical_conflict: true
  min_qualifying_evidence: 1
""".strip()
        + "\n",
        encoding="utf-8",
    )

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
        dd_rubric_file=config_dir / "dd_rubric.yaml",
        dd_gate_thresholds_file=config_dir / "dd_gate_thresholds.yaml",
    )


def _insert_base_facts(session, initiative_id: int, *, market_stage: str = "interviews") -> None:
    upsert_dd_team_fact(
        session,
        initiative_id=initiative_id,
        commitment_level=4.2,
        key_roles=["Founder", "CTO"],
        references_count=3,
        founder_risk_flags=[],
        investable_segment="spinout_candidate",
        is_investable=True,
        evidence=[
            {
                "source_type": "people_markdown",
                "source_url": "https://example.org/team",
                "snippet": "John Smith CTO with 5 years deep-tech engineering experience",
                "doc_id": "team_1",
                "confidence": 0.9,
            },
            {
                "source_type": "manual_dd",
                "source_url": "file:///tmp/team.md",
                "snippet": "Technical lead confirmed, product owner confirmed, full-time commitment 2026",
                "doc_id": "team_2",
                "confidence": 0.95,
            },
        ],
        source_type="manual_dd",
        source_url="file:///tmp/team.md",
        confidence=0.95,
    )
    upsert_dd_tech_fact(
        session,
        initiative_id=initiative_id,
        github_org="example",
        github_repo="core",
        repo_count=1,
        contributor_count=5,
        commit_velocity_90d=24,
        ci_present=True,
        test_signal=3.0,
        benchmark_artifacts=1,
        prototype_stage="prototype",
        ip_indicators=["patent_hint"],
        evidence=[
            {
                "source_type": "github_api",
                "source_url": "https://github.com/example/core",
                "snippet": "Commits in 90 days: 24 with CI checks and benchmarks",
                "doc_id": "tech_1",
                "confidence": 0.9,
            },
            {
                "source_type": "public_signals",
                "source_url": "https://example.org/tech",
                "snippet": "Prototype benchmark target met with low latency and high throughput",
                "doc_id": "tech_2",
                "confidence": 0.8,
            },
        ],
        source_type="github_api",
        source_url="https://github.com/example/core",
        confidence=0.9,
    )

    interviews, lois, pilots, paid_pilots = 8, 0, 0, 0
    if market_stage == "loi":
        interviews, lois = 10, 1
    elif market_stage == "pilot":
        interviews, lois, pilots = 12, 1, 1
    elif market_stage == "paid_pilot":
        interviews, lois, pilots, paid_pilots = 14, 2, 1, 1

    upsert_dd_market_fact(
        session,
        initiative_id=initiative_id,
        customer_interviews=interviews,
        lois=lois,
        pilots=pilots,
        paid_pilots=paid_pilots,
        pricing_evidence=True,
        buyer_persona_clarity=3.8,
        sam_som_quality=3.4,
        evidence=[
            {
                "source_type": "manual_dd",
                "source_url": "file:///tmp/market.csv",
                "snippet": f"Customer interviews logged: {interviews}; LOI count: {lois}; pilot count: {pilots}; paid pilot count: {paid_pilots}",
                "doc_id": "market_1",
                "confidence": 0.95,
            },
            {
                "source_type": "public_signals",
                "source_url": "https://example.org/market",
                "snippet": "Pricing and buyer persona are defined for enterprise customer pipeline",
                "doc_id": "market_2",
                "confidence": 0.8,
            },
        ],
        source_type="manual_dd",
        source_url="file:///tmp/market.csv",
        confidence=0.9,
    )
    upsert_dd_legal_fact(
        session,
        initiative_id=initiative_id,
        entity_status="incorporated",
        ip_ownership_status="team_owned",
        founder_agreements=True,
        licensing_constraints=False,
        compliance_flags=[],
        legal_risk_score=2.0,
        evidence=[
            {
                "source_type": "manual_dd",
                "source_url": "file:///tmp/legal.pdf",
                "snippet": "Entity registered, IP assignment signed, founder agreement in place 2026",
                "doc_id": "legal_1",
                "confidence": 0.95,
            },
            {
                "source_type": "public_signals",
                "source_url": "https://example.org/legal",
                "snippet": "No unresolved critical legal conflict reported",
                "doc_id": "legal_2",
                "confidence": 0.75,
            },
        ],
        source_type="manual_dd",
        source_url="file:///tmp/legal.pdf",
        confidence=0.9,
    )


def test_team_capability_matrix_marks_strong_and_need_help(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_v2_caps.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(session, name="TechCore", university="TUM", primary_url="https://techcore.example")
        _insert_base_facts(session, initiative.id, market_stage="interviews")
        # Weaken sales fit explicitly.
        upsert_dd_market_fact(
            session,
            initiative_id=initiative.id,
            customer_interviews=5,
            lois=0,
            pilots=0,
            paid_pilots=0,
            pricing_evidence=False,
            buyer_persona_clarity=2.2,
            sam_som_quality=2.0,
            evidence=[
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/market_weak.csv",
                    "snippet": "Customer interviews logged: 5 with no LOI and no pilot yet",
                    "doc_id": "market_weak_1",
                    "confidence": 0.9,
                },
                {
                    "source_type": "public_signals",
                    "source_url": "https://techcore.example/market",
                    "snippet": "Problem statement defined but commercial conversion is early",
                    "doc_id": "market_weak_2",
                    "confidence": 0.7,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/market_weak.csv",
            confidence=0.9,
        )

    score_dd(all_initiatives=True, settings=settings, db_url=db_url)

    team_matrix = json.loads((settings.exports_dir / "team_capability_matrix.json").read_text(encoding="utf-8"))
    assert len(team_matrix) == 1
    row = team_matrix[0]
    assert row["initiative_name"] == "TechCore"
    assert row["tech_fit"] > row["sales_fit"]
    assert "tech" in row["strong_in"]
    assert "sales" in row["need_help_in"]


def test_market_validation_stage_is_monotonic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_v2_market.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        interviews = upsert_initiative(session, name="InterviewsCo", university="TUM", primary_url="https://interviews.example")
        loi = upsert_initiative(session, name="LoiCo", university="TUM", primary_url="https://loi.example")
        paid = upsert_initiative(session, name="PaidPilotCo", university="TUM", primary_url="https://paid.example")
        _insert_base_facts(session, interviews.id, market_stage="interviews")
        _insert_base_facts(session, loi.id, market_stage="loi")
        _insert_base_facts(session, paid.id, market_stage="paid_pilot")

    score_dd(all_initiatives=True, settings=settings, db_url=db_url)

    scores = json.loads((settings.exports_dir / "dd_scores.json").read_text(encoding="utf-8"))
    by_name = {row["initiative_name"]: row for row in scores}

    assert by_name["InterviewsCo"]["market_validation_stage"] == "interviews"
    assert by_name["LoiCo"]["market_validation_stage"] == "loi"
    assert by_name["PaidPilotCo"]["market_validation_stage"] == "paid_pilot"

    assert by_name["InterviewsCo"]["market_dd"] < by_name["LoiCo"]["market_dd"]
    assert by_name["LoiCo"]["market_dd"] < by_name["PaidPilotCo"]["market_dd"]


def test_gate_a_blocks_weak_team_tech_fit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_v2_gate_a.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(session, name="GoToMarketStars", university="LMU", primary_url="https://gtm.example")
        upsert_dd_team_fact(
            session,
            initiative_id=initiative.id,
            commitment_level=4.1,
            key_roles=["Founder", "Product Lead", "Sales Lead"],
            references_count=3,
            founder_risk_flags=[],
            investable_segment="spinout_candidate",
            is_investable=True,
            evidence=[
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/team_gtm.md",
                    "snippet": "Product lead and sales lead full-time with strong customer interviews 2026",
                    "doc_id": "team_gtm_1",
                    "confidence": 0.95,
                },
                {
                    "source_type": "people_markdown",
                    "source_url": "https://gtm.example/team",
                    "snippet": "Named operators in product and sales; no technical lead named",
                    "doc_id": "team_gtm_2",
                    "confidence": 0.8,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/team_gtm.md",
            confidence=0.9,
        )
        upsert_dd_tech_fact(
            session,
            initiative_id=initiative.id,
            github_org="",
            github_repo="",
            repo_count=0,
            contributor_count=0,
            commit_velocity_90d=0.0,
            ci_present=False,
            test_signal=0.0,
            benchmark_artifacts=0,
            prototype_stage="research",
            ip_indicators=[],
            evidence=[
                {
                    "source_type": "public_signals",
                    "source_url": "https://gtm.example",
                    "snippet": "Technical implementation is still early and architecture is not yet validated",
                    "doc_id": "tech_gtm_1",
                    "confidence": 0.7,
                }
            ],
            source_type="public_signals",
            source_url="https://gtm.example",
            confidence=0.7,
        )
        upsert_dd_market_fact(
            session,
            initiative_id=initiative.id,
            customer_interviews=18,
            lois=2,
            pilots=1,
            paid_pilots=1,
            pricing_evidence=True,
            buyer_persona_clarity=4.0,
            sam_som_quality=3.8,
            evidence=[
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/market_gtm.csv",
                    "snippet": "Customer interviews 18, LOI 2, pilot 1, paid pilot 1",
                    "doc_id": "market_gtm_1",
                    "confidence": 0.95,
                },
                {
                    "source_type": "public_signals",
                    "source_url": "https://gtm.example/market",
                    "snippet": "Pricing and sales cycle validated through paid pilot",
                    "doc_id": "market_gtm_2",
                    "confidence": 0.8,
                },
            ],
            source_type="manual_dd",
            source_url="file:///tmp/market_gtm.csv",
            confidence=0.9,
        )
        upsert_dd_legal_fact(
            session,
            initiative_id=initiative.id,
            entity_status="incorporated",
            ip_ownership_status="team_owned",
            founder_agreements=True,
            licensing_constraints=False,
            compliance_flags=[],
            legal_risk_score=2.0,
            evidence=[
                {
                    "source_type": "manual_dd",
                    "source_url": "file:///tmp/legal_gtm.pdf",
                    "snippet": "Entity and IP ownership are documented",
                    "doc_id": "legal_gtm_1",
                    "confidence": 0.95,
                }
            ],
            source_type="manual_dd",
            source_url="file:///tmp/legal_gtm.pdf",
            confidence=0.9,
        )

    score_dd(all_initiatives=True, settings=settings, db_url=db_url)
    compute_dd_gates(all_initiatives=True, settings=settings, db_url=db_url)

    gate_rows = json.loads((settings.exports_dir / "dd_gates.json").read_text(encoding="utf-8"))
    gate_a = [row for row in gate_rows if row["initiative_name"] == "GoToMarketStars" and row["gate_name"] == "A"]
    assert gate_a
    assert gate_a[0]["status"] == "fail"
    assert "team_tech_fit_below_threshold" in gate_a[0]["reason"]


def test_dd_explain_command_outputs_component_math(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    db_url = f"sqlite:///{tmp_path / 'dd_v2_explain.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(session, name="Explain DD", university="TUM", primary_url="https://explain-dd.example")
        _insert_base_facts(session, initiative.id, market_stage="loi")
        initiative_id = initiative.id

    score_dd(all_initiatives=True, settings=settings, db_url=db_url)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(settings.project_root),
            "--json",
            "dd-explain",
            "--initiative-id",
            str(initiative_id),
            "--db-url",
            db_url,
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["initiative_id"] == initiative_id
    assert payload["scores"]["conviction_score"] >= 1.0
    assert "team_capability" in payload
    assert isinstance(payload["components"], list)
    assert payload["components"]
