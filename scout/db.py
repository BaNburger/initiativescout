from __future__ import annotations

import logging
import os
import re
import threading

log = logging.getLogger(__name__)
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, inspect as sa_inspect, text
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base, Initiative

DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_db_name(name: str) -> str:
    """Strip and validate a database name. Raises ValueError if invalid."""
    name = name.strip()
    if not name or not DB_NAME_RE.match(name):
        raise ValueError("Invalid database name (letters, numbers, hyphens, underscores only)")
    return name

_lock = threading.Lock()
_engine = None
_SessionLocal = None
_current_db_path: Path | None = None
_cached_entity_type: str | None = None

DATA_DIR = Path(__file__).parent / "data"


def init_db(db_path: str | Path | None = None) -> None:
    global _engine, _SessionLocal, _current_db_path, _cached_entity_type
    with _lock:
        old_engine = _engine
        if db_path is None:
            db_path = DATA_DIR / "scout.db"
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        new_engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(new_engine, "connect")
        def _set_pragmas(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=10000")       # ~40MB page cache
            cursor.execute("PRAGMA mmap_size=30000000")     # 30MB memory-mapped I/O
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()
        Base.metadata.create_all(new_engine)
        new_factory = sessionmaker(bind=new_engine, autoflush=False, expire_on_commit=False)
        _migrate_existing_db(new_engine)
        _engine = new_engine
        _SessionLocal = new_factory
        _current_db_path = db_path
        _cached_entity_type = None  # invalidate cache on DB init
    # Dispose old engine outside the lock so get_session() isn't blocked
    if old_engine is not None:
        old_engine.dispose()


def _migrate_existing_db(engine) -> None:
    """Add columns/tables that may be missing in older databases."""
    inspector = sa_inspect(engine)
    if not inspector.has_table("initiatives"):
        return
    columns = {col["name"] for col in inspector.get_columns("initiatives")}
    if "custom_fields_json" not in columns:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE initiatives ADD COLUMN custom_fields_json TEXT DEFAULT '{}'"
            ))
    if "faculty" not in columns:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE initiatives ADD COLUMN faculty VARCHAR(200) DEFAULT ''"
            ))
    if "metadata_json" not in columns:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE initiatives ADD COLUMN metadata_json TEXT DEFAULT '{}'"
            ))
    # Enrichment table migrations
    if inspector.has_table("enrichments"):
        ecols = {col["name"] for col in inspector.get_columns("enrichments")}
        if "source_url" not in ecols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE enrichments ADD COLUMN source_url TEXT"
                ))
    # Custom columns table migrations
    if inspector.has_table("custom_columns"):
        ccols = {col["name"] for col in inspector.get_columns("custom_columns")}
        if "database" not in ccols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE custom_columns ADD COLUMN database TEXT"
                ))
    # OutreachScore migrations
    if inspector.has_table("outreach_scores"):
        scols = {col["name"] for col in inspector.get_columns("outreach_scores")}
        if "dimension_grades_json" not in scols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE outreach_scores ADD COLUMN dimension_grades_json TEXT DEFAULT '{}'"
                ))
    # Ensure performance indexes exist (idempotent)
    with engine.begin() as conn:
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_initiative_uni ON initiatives(uni)",
            "CREATE INDEX IF NOT EXISTS ix_enrichment_initiative ON enrichments(initiative_id)",
            "CREATE INDEX IF NOT EXISTS ix_score_initiative_scored ON outreach_scores(initiative_id, scored_at)",
            "CREATE INDEX IF NOT EXISTS ix_score_project_id ON outreach_scores(project_id)",
            "CREATE INDEX IF NOT EXISTS ix_project_initiative ON projects(initiative_id)",
        ):
            conn.execute(text(stmt))
    _ensure_fts_table(engine)
    _ensure_revision_tracking(engine)
    _seed_scoring_prompts(engine)


def get_session() -> Session:
    with _lock:
        if _SessionLocal is None:
            raise RuntimeError("init_db() has not been called")
        factory = _SessionLocal
    return factory()  # type: ignore[misc]


def session_generator() -> Generator[Session, None, None]:
    """Generator yielding a transactional session.

    Use directly with FastAPI ``Depends()``, or via ``session_scope()``
    as a context manager for MCP / scripts.
    """
    session = get_session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Context manager wrapper for non-FastAPI code (MCP server, scripts, etc.)
session_scope = contextmanager(session_generator)


def current_db_name() -> str:
    """Return the stem (filename without .db) of the active database."""
    if _current_db_path is None:
        return "scout"
    return _current_db_path.stem


def list_databases() -> list[str]:
    """Return sorted list of DB stems in the data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in DATA_DIR.glob("*.db"))


def _safe_db_path(name: str) -> Path:
    """Build a DB path and verify it stays inside DATA_DIR."""
    db_path = (DATA_DIR / f"{name}.db").resolve()
    if not db_path.is_relative_to(DATA_DIR.resolve()):
        raise ValueError("Invalid database path")
    return db_path


def switch_db(name: str) -> None:
    """Switch to a different database by stem name. Creates if it doesn't exist."""
    init_db(_safe_db_path(name))


def create_database(name: str, entity_type: str = "initiative") -> None:
    """Create a new database and switch to it."""
    db_path = _safe_db_path(name)
    if db_path.exists():
        raise ValueError(f"Database '{name}' already exists")
    init_db(db_path)
    set_entity_type(entity_type)


def delete_database(name: str) -> None:
    """Delete a database file. Cannot delete the currently active database."""
    db_path = _safe_db_path(name)
    if not db_path.exists():
        raise ValueError(f"Database '{name}' not found")
    if _current_db_path is not None and db_path.resolve() == _current_db_path.resolve():
        raise ValueError("Cannot delete the currently active database. Switch to another database first.")
    db_path.unlink()


def backup_database(name: str) -> str:
    """Copy a database file to a timestamped backup. Returns the backup filename."""
    import shutil
    from datetime import datetime
    db_path = _safe_db_path(name)
    if not db_path.exists():
        raise ValueError(f"Database '{name}' not found")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"{name}-backup-{ts}"
    backup_path = DATA_DIR / f"{backup_name}.db"
    shutil.copy2(db_path, backup_path)
    return backup_name


def _ensure_revision_tracking(engine) -> None:
    """Create the _meta table and triggers that bump a revision counter on data changes."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '0')"
        ))
        conn.execute(text(
            "INSERT OR IGNORE INTO _meta (key, value) VALUES ('revision', 0)"
        ))
        conn.execute(text(
            "INSERT OR IGNORE INTO _meta (key, value) VALUES ('entity_type', 'initiative')"
        ))
        for table in ("initiatives", "enrichments", "outreach_scores", "projects"):
            for op in ("INSERT", "UPDATE", "DELETE"):
                conn.execute(text(f"""
                    CREATE TRIGGER IF NOT EXISTS _meta_bump_{table}_{op.lower()}
                    AFTER {op} ON {table}
                    BEGIN
                        UPDATE _meta SET value = value + 1 WHERE key = 'revision';
                    END
                """))


def get_revision() -> int:
    """Read the current data revision counter (cheap single-row read)."""
    with _lock:
        engine = _engine
    if engine is None:
        return 0
    with engine.connect() as conn:
        return conn.execute(text("SELECT value FROM _meta WHERE key = 'revision'")).scalar() or 0


def get_entity_type() -> str:
    """Return the entity type for the current database ('initiative', 'professor', etc.)."""
    global _cached_entity_type
    with _lock:
        if _cached_entity_type is not None:
            return _cached_entity_type
        engine = _engine
    if engine is None:
        return "initiative"
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM _meta WHERE key = 'entity_type'")).scalar()
    result = str(row) if row else "initiative"
    with _lock:
        _cached_entity_type = result
    return result


def set_entity_type(entity_type: str) -> None:
    """Set the entity type for the current database."""
    global _cached_entity_type
    with _lock:
        engine = _engine
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('entity_type', :et)"
        ), {"et": entity_type})
    with _lock:
        _cached_entity_type = entity_type


def get_entity_config_json() -> dict:
    """Read custom entity type config from _meta (if any)."""
    from scout.utils import json_parse
    with _lock:
        engine = _engine
    if engine is None:
        return {}
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM _meta WHERE key = 'entity_config'")).scalar()
    return json_parse(str(row) if row else None)


def set_entity_config_json(config: dict) -> None:
    """Store custom entity type config in _meta."""
    import json as _json
    with _lock:
        engine = _engine
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('entity_config', :cfg)"
        ), {"cfg": _json.dumps(config)})


def _ensure_fts_table(engine) -> None:
    """Create the FTS5 table structure (rebuild deferred to first search)."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS initiative_fts USING fts5(
                name, description, sector, technology_domains,
                categories, market_domains, faculty,
                content='initiatives', content_rowid='id'
            )
        """))


# ---------------------------------------------------------------------------
# FTS auto-sync via SQLAlchemy ORM events
# ---------------------------------------------------------------------------

_FTS_FIELDS = ("name", "description", "sector", "technology_domains",
               "categories", "market_domains", "faculty")


def _fts_insert(connection, initiative) -> None:
    """Insert a single initiative into the FTS index."""
    params = {"id": initiative.id}
    for f in _FTS_FIELDS:
        params[f] = getattr(initiative, f, "") or ""
    connection.execute(text(
        "INSERT INTO initiative_fts(rowid, name, description, sector, "
        "technology_domains, categories, market_domains, faculty) "
        "VALUES (:id, :name, :description, :sector, "
        ":technology_domains, :categories, :market_domains, :faculty)"
    ), params)


def _fts_delete_by_values(connection, initiative_id: int, field_values: dict[str, str]) -> None:
    """Remove a single initiative from the FTS index using provided field values.

    FTS5 content-sync tables require the exact old values for delete operations.
    Using a SELECT from the content table is wrong in after_update handlers
    because the row already contains the new values at that point.
    """
    params = {"id": initiative_id}
    params.update(field_values)
    connection.execute(text(
        "INSERT INTO initiative_fts(initiative_fts, rowid, name, description, sector, "
        "technology_domains, categories, market_domains, faculty) "
        "VALUES ('delete', :id, :name, :description, :sector, "
        ":technology_domains, :categories, :market_domains, :faculty)"
    ), params)


def _fts_field_values(initiative) -> dict[str, str]:
    """Extract current FTS field values from an Initiative ORM object."""
    return {f: getattr(initiative, f, "") or "" for f in _FTS_FIELDS}


@event.listens_for(Initiative, "after_insert")
def _on_initiative_insert(mapper, connection, target):
    try:
        _fts_insert(connection, target)
    except Exception:
        log.warning("FTS auto-sync insert failed for %s", target.name, exc_info=True)


@event.listens_for(Initiative, "before_update")
def _on_initiative_before_update(mapper, connection, target):
    """Capture old FTS field values before the UPDATE overwrites them."""
    from sqlalchemy import inspect as orm_inspect
    state = orm_inspect(target)
    old_vals = {}
    for f in _FTS_FIELDS:
        hist = state.attrs[f].history
        # history.deleted contains old value(s) if the field changed
        if hist.deleted:
            old_vals[f] = hist.deleted[0] or ""
        else:
            old_vals[f] = getattr(target, f, "") or ""
    target._fts_old_values = old_vals


@event.listens_for(Initiative, "after_update")
def _on_initiative_update(mapper, connection, target):
    try:
        old_vals = getattr(target, "_fts_old_values", _fts_field_values(target))
        _fts_delete_by_values(connection, target.id, old_vals)
        _fts_insert(connection, target)
    except Exception:
        log.warning("FTS auto-sync update failed for %s", target.name, exc_info=True)


@event.listens_for(Initiative, "after_delete")
def _on_initiative_delete(mapper, connection, target):
    try:
        _fts_delete_by_values(connection, target.id, _fts_field_values(target))
    except Exception:
        log.warning("FTS auto-sync delete failed for id %d", target.id, exc_info=True)


def _seed_scoring_prompts(engine) -> None:
    """Seed or fix scoring prompts to match the database's entity type."""
    from scout.scorer import default_prompts_for, _ALL_DEFAULT_PROMPTS

    with engine.connect() as conn:
        et_row = conn.execute(text("SELECT value FROM _meta WHERE key = 'entity_type'")).scalar()
    entity_type = str(et_row) if et_row else "initiative"
    prompts = default_prompts_for(entity_type)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM scoring_prompts")).scalar()

    if count == 0:
        # Fresh DB — seed with correct defaults
        with engine.begin() as conn:
            for key, (label, content) in prompts.items():
                conn.execute(text(
                    "INSERT INTO scoring_prompts (key, label, content) VALUES (:key, :label, :content)"
                ), {"key": key, "label": label, "content": content})
        return

    # Existing prompts — check if they're stale defaults from a different entity type.
    # Only auto-fix if content exactly matches a different type's defaults (user edits preserved).
    wrong_defaults: dict[str, str] = {}
    for other_type, other_prompts in _ALL_DEFAULT_PROMPTS.items():
        if other_type != entity_type:
            for key, (_, content) in other_prompts.items():
                wrong_defaults[key + ":" + content] = key
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT key, content FROM scoring_prompts")).fetchall()
    to_fix = []
    for key, content in rows:
        if (key + ":" + content) in wrong_defaults and key in prompts:
            to_fix.append(key)
    if to_fix:
        with engine.begin() as conn:
            for key in to_fix:
                label, content = prompts[key]
                conn.execute(text(
                    "UPDATE scoring_prompts SET label = :label, content = :content WHERE key = :key"
                ), {"key": key, "label": label, "content": content})
