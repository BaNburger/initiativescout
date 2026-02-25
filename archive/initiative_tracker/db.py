from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from initiative_tracker.config import get_settings
from initiative_tracker.models import Base, PipelineRun, SchemaMeta
from initiative_tracker.utils import to_json, utc_now

_ENGINES: dict[str, Engine] = {}
_SESSIONS: dict[str, sessionmaker[Session]] = {}


def get_engine(db_url: str | None = None) -> Engine:
    settings = get_settings()
    target_url = db_url or settings.database_url
    if target_url not in _ENGINES:
        connect_args = {"check_same_thread": False} if target_url.startswith("sqlite") else {}
        _ENGINES[target_url] = create_engine(target_url, future=True, connect_args=connect_args)
    return _ENGINES[target_url]


def get_session_factory(db_url: str | None = None) -> sessionmaker[Session]:
    settings = get_settings()
    target_url = db_url or settings.database_url
    if target_url not in _SESSIONS:
        _SESSIONS[target_url] = sessionmaker(
            bind=get_engine(target_url),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SESSIONS[target_url]


def init_db(db_url: str | None = None) -> None:
    settings = get_settings()
    settings.ensure_directories()
    if db_url is None:
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    _run_additive_migrations(engine)


@contextmanager
def session_scope(db_url: str | None = None) -> Iterator[Session]:
    session_factory = get_session_factory(db_url)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def start_pipeline_run(session: Session, stage: str) -> PipelineRun:
    run = PipelineRun(stage=stage, status="running", details_json="{}", error_message="", started_at=utc_now())
    session.add(run)
    session.flush()
    return run


def finish_pipeline_run(
    session: Session,
    run: PipelineRun,
    *,
    status: str,
    details: dict | None = None,
    error_message: str = "",
) -> PipelineRun:
    run.status = status
    run.details_json = to_json(details or {})
    run.error_message = error_message
    run.finished_at = utc_now()
    session.add(run)
    return run


def _table_exists(engine: Engine, table: str) -> bool:
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table},
            ).first()
        return row is not None
    return True


def _column_exists(engine: Engine, table: str, column: str) -> bool:
    if engine.dialect.name != "sqlite":
        return True
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
    for row in rows:
        if row[1] == column:
            return True
    return False


def _add_column_if_missing(engine: Engine, table: str, column: str, definition: str) -> None:
    if not _table_exists(engine, table):
        return
    if _column_exists(engine, table, column):
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _run_additive_migrations(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    score_columns = {
        "actionability_0_6m": "FLOAT NOT NULL DEFAULT 0.0",
        "support_fit": "FLOAT NOT NULL DEFAULT 0.0",
        "outreach_now_score": "FLOAT NOT NULL DEFAULT 0.0",
        "venture_upside_score": "FLOAT NOT NULL DEFAULT 0.0",
        "confidence_actionability": "FLOAT NOT NULL DEFAULT 0.0",
        "confidence_support_fit": "FLOAT NOT NULL DEFAULT 0.0",
    }
    for column, ddl in score_columns.items():
        _add_column_if_missing(engine, "scores", column, ddl)

    _add_column_if_missing(engine, "rankings", "item_meta_json", "TEXT NOT NULL DEFAULT '{}'")
    dd_score_columns = {
        "team_product_fit": "FLOAT NOT NULL DEFAULT 0.0",
        "team_tech_fit": "FLOAT NOT NULL DEFAULT 0.0",
        "team_sales_fit": "FLOAT NOT NULL DEFAULT 0.0",
        "market_validation_stage": "VARCHAR(32) NOT NULL DEFAULT 'none'",
        "conviction_confidence": "FLOAT NOT NULL DEFAULT 0.0",
    }
    for column, ddl in dd_score_columns.items():
        _add_column_if_missing(engine, "dd_scores", column, ddl)
    dd_score_component_columns = {
        "rule_value": "FLOAT NOT NULL DEFAULT 0.0",
        "ai_suggested_value": "FLOAT NOT NULL DEFAULT 0.0",
        "final_value": "FLOAT NOT NULL DEFAULT 0.0",
        "ai_used": "BOOLEAN NOT NULL DEFAULT 0",
        "manual_review_flag": "BOOLEAN NOT NULL DEFAULT 0",
        "audit_reason": "TEXT NOT NULL DEFAULT ''",
    }
    for column, ddl in dd_score_component_columns.items():
        _add_column_if_missing(engine, "dd_score_components", column, ddl)

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_team_facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "commitment_level FLOAT NOT NULL DEFAULT 0.0, "
                "key_roles_json TEXT NOT NULL DEFAULT '[]', "
                "references_count INTEGER NOT NULL DEFAULT 0, "
                "founder_risk_flags_json TEXT NOT NULL DEFAULT '[]', "
                "investable_segment VARCHAR(64) NOT NULL DEFAULT 'unknown', "
                "is_investable BOOLEAN NOT NULL DEFAULT 0, "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_team_facts_initiative ON dd_team_facts (initiative_id)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_tech_facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "github_org VARCHAR(255) NOT NULL DEFAULT '', "
                "github_repo VARCHAR(255) NOT NULL DEFAULT '', "
                "repo_count INTEGER NOT NULL DEFAULT 0, "
                "contributor_count INTEGER NOT NULL DEFAULT 0, "
                "commit_velocity_90d FLOAT NOT NULL DEFAULT 0.0, "
                "ci_present BOOLEAN NOT NULL DEFAULT 0, "
                "test_signal FLOAT NOT NULL DEFAULT 0.0, "
                "benchmark_artifacts INTEGER NOT NULL DEFAULT 0, "
                "prototype_stage VARCHAR(64) NOT NULL DEFAULT 'unknown', "
                "ip_indicators_json TEXT NOT NULL DEFAULT '[]', "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_tech_facts_initiative ON dd_tech_facts (initiative_id)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_market_facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "customer_interviews INTEGER NOT NULL DEFAULT 0, "
                "lois INTEGER NOT NULL DEFAULT 0, "
                "pilots INTEGER NOT NULL DEFAULT 0, "
                "paid_pilots INTEGER NOT NULL DEFAULT 0, "
                "pricing_evidence BOOLEAN NOT NULL DEFAULT 0, "
                "buyer_persona_clarity FLOAT NOT NULL DEFAULT 0.0, "
                "sam_som_quality FLOAT NOT NULL DEFAULT 0.0, "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_market_facts_initiative ON dd_market_facts (initiative_id)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_legal_facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "entity_status VARCHAR(64) NOT NULL DEFAULT 'unknown', "
                "ip_ownership_status VARCHAR(64) NOT NULL DEFAULT 'unknown', "
                "founder_agreements BOOLEAN NOT NULL DEFAULT 0, "
                "licensing_constraints BOOLEAN NOT NULL DEFAULT 0, "
                "compliance_flags_json TEXT NOT NULL DEFAULT '[]', "
                "legal_risk_score FLOAT NOT NULL DEFAULT 0.0, "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_legal_facts_initiative ON dd_legal_facts (initiative_id)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_finance_facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "burn_monthly FLOAT NOT NULL DEFAULT 0.0, "
                "runway_months FLOAT NOT NULL DEFAULT 0.0, "
                "funding_dependence FLOAT NOT NULL DEFAULT 0.0, "
                "cap_table_summary TEXT NOT NULL DEFAULT '', "
                "dilution_risk FLOAT NOT NULL DEFAULT 0.0, "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_finance_facts_initiative ON dd_finance_facts (initiative_id)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_gates ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "gate_name VARCHAR(64) NOT NULL, "
                "status VARCHAR(32) NOT NULL DEFAULT 'fail', "
                "reason TEXT NOT NULL DEFAULT '', "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_gates_initiative_gate ON dd_gates (initiative_id, gate_name)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_scores ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "team_dd FLOAT NOT NULL DEFAULT 0.0, "
                "tech_dd FLOAT NOT NULL DEFAULT 0.0, "
                "market_dd FLOAT NOT NULL DEFAULT 0.0, "
                "execution_dd FLOAT NOT NULL DEFAULT 0.0, "
                "legal_dd FLOAT NOT NULL DEFAULT 0.0, "
                "team_product_fit FLOAT NOT NULL DEFAULT 0.0, "
                "team_tech_fit FLOAT NOT NULL DEFAULT 0.0, "
                "team_sales_fit FLOAT NOT NULL DEFAULT 0.0, "
                "market_validation_stage VARCHAR(32) NOT NULL DEFAULT 'none', "
                "conviction_confidence FLOAT NOT NULL DEFAULT 0.0, "
                "conviction_score FLOAT NOT NULL DEFAULT 0.0, "
                "scored_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_scores_initiative_scored_at ON dd_scores (initiative_id, scored_at)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_score_components ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "dd_score_id INTEGER, "
                "dimension VARCHAR(64) NOT NULL, "
                "component_key VARCHAR(128) NOT NULL, "
                "raw_value FLOAT NOT NULL DEFAULT 0.0, "
                "normalized_value FLOAT NOT NULL DEFAULT 0.0, "
                "weight FLOAT NOT NULL DEFAULT 0.0, "
                "weighted_contribution FLOAT NOT NULL DEFAULT 0.0, "
                "rule_value FLOAT NOT NULL DEFAULT 0.0, "
                "ai_suggested_value FLOAT NOT NULL DEFAULT 0.0, "
                "final_value FLOAT NOT NULL DEFAULT 0.0, "
                "ai_used BOOLEAN NOT NULL DEFAULT 0, "
                "manual_review_flag BOOLEAN NOT NULL DEFAULT 0, "
                "audit_reason TEXT NOT NULL DEFAULT '', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "evidence_json TEXT NOT NULL DEFAULT '[]', "
                "source_mix_json TEXT NOT NULL DEFAULT '[]', "
                "updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_score_components_initiative ON dd_score_components (initiative_id)"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dd_score_components_dim_key "
                "ON dd_score_components (initiative_id, dimension, component_key)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_evidence_items ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "source_type VARCHAR(64) NOT NULL DEFAULT '', "
                "source_url VARCHAR(512) NOT NULL DEFAULT '', "
                "snippet TEXT NOT NULL DEFAULT '', "
                "fetched_at DATETIME NOT NULL, "
                "quality FLOAT NOT NULL DEFAULT 0.0, "
                "reliability FLOAT NOT NULL DEFAULT 0.0)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dd_evidence_items_initiative_fetched "
                "ON dd_evidence_items (initiative_id, fetched_at)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_claims ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "claim_type VARCHAR(64) NOT NULL DEFAULT '', "
                "claim_key VARCHAR(128) NOT NULL DEFAULT '', "
                "claim_value_json TEXT NOT NULL DEFAULT '{}', "
                "extractor VARCHAR(32) NOT NULL DEFAULT 'rule', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "evidence_item_ids_json TEXT NOT NULL DEFAULT '[]', "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dd_claims_initiative_created "
                "ON dd_claims (initiative_id, created_at)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_ai_assists ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "dimension VARCHAR(64) NOT NULL DEFAULT '', "
                "component_key VARCHAR(128) NOT NULL DEFAULT '', "
                "model VARCHAR(128) NOT NULL DEFAULT 'heuristic-fallback', "
                "prompt_version VARCHAR(64) NOT NULL DEFAULT 'v1', "
                "ai_score FLOAT NOT NULL DEFAULT 0.0, "
                "rationale TEXT NOT NULL DEFAULT '', "
                "cited_claim_ids_json TEXT NOT NULL DEFAULT '[]', "
                "confidence FLOAT NOT NULL DEFAULT 0.0, "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dd_ai_assists_initiative_created "
                "ON dd_ai_assists (initiative_id, created_at)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dd_memos ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "initiative_id INTEGER NOT NULL, "
                "decision VARCHAR(32) NOT NULL DEFAULT 'monitor', "
                "check_size_band VARCHAR(64) NOT NULL DEFAULT 'n/a', "
                "rationale TEXT NOT NULL DEFAULT '', "
                "top_risks_json TEXT NOT NULL DEFAULT '[]', "
                "next_actions_json TEXT NOT NULL DEFAULT '[]', "
                "recommendation_json TEXT NOT NULL DEFAULT '{}', "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_dd_memos_initiative_created_at ON dd_memos (initiative_id, created_at)"))

    if not _table_exists(engine, "schema_meta"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_meta ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "key VARCHAR(128) NOT NULL, "
                    "value VARCHAR(512) NOT NULL DEFAULT '', "
                    "updated_at DATETIME NOT NULL)"
                )
            )
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_schema_meta_key ON schema_meta (key)"))

    with Session(engine, autoflush=False, autocommit=False, future=True) as session:
        existing = session.query(SchemaMeta).filter(SchemaMeta.key == "schema_version").first()
        if existing is None:
            existing = SchemaMeta(key="schema_version", value="2.2")
            session.add(existing)
        else:
            existing.value = "2.2"
        session.commit()
