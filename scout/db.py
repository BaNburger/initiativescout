from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime

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
BACKUP_DIR = DATA_DIR / "backups"


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


def _add_column_if_missing(engine, inspector, table: str, column: str, sql: str) -> None:
    """Add a column to a table if it doesn't exist yet."""
    if not inspector.has_table(table):
        return
    cols = {col["name"] for col in inspector.get_columns(table)}
    if column not in cols:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {sql}"))


def _migrate_existing_db(engine) -> None:
    """Add columns/tables that may be missing in older databases."""
    inspector = sa_inspect(engine)
    if not inspector.has_table("initiatives"):
        return
    _add_column_if_missing(engine, inspector, "initiatives", "custom_fields_json", "custom_fields_json TEXT DEFAULT '{}'")
    _add_column_if_missing(engine, inspector, "initiatives", "faculty", "faculty VARCHAR(200) DEFAULT ''")
    _add_column_if_missing(engine, inspector, "initiatives", "metadata_json", "metadata_json TEXT DEFAULT '{}'")
    _add_column_if_missing(engine, inspector, "enrichments", "source_url", "source_url TEXT")
    _add_column_if_missing(engine, inspector, "enrichments", "structured_fields_json", "structured_fields_json TEXT DEFAULT '{}'")
    _add_column_if_missing(engine, inspector, "custom_columns", "database", "database TEXT")
    _add_column_if_missing(engine, inspector, "outreach_scores", "dimension_grades_json", "dimension_grades_json TEXT DEFAULT '{}'")
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
    """Copy a database file to a timestamped backup in the backups directory."""
    import shutil
    db_path = _safe_db_path(name)
    if not db_path.exists():
        raise ValueError(f"Database '{name}' not found")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"{name}-backup-{ts}"
    backup_path = BACKUP_DIR / f"{backup_name}.db"
    shutil.copy2(db_path, backup_path)
    return backup_name


def list_backups() -> list[dict]:
    """Return sorted list of backups with metadata."""
    if not BACKUP_DIR.exists():
        return []
    backups = []
    for p in sorted(BACKUP_DIR.glob("*.db"), reverse=True):
        # Parse original DB name from backup filename: {name}-backup-{ts}
        stem = p.stem
        parts = stem.rsplit("-backup-", 1)
        origin = parts[0] if len(parts) == 2 else stem
        stat = p.stat()
        backups.append({
            "name": stem,
            "origin": origin,
            "size_bytes": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return backups


def restore_database(backup_name: str) -> str:
    """Restore a backup, replacing the original database. Returns the restored DB name."""
    import shutil
    backup_path = (BACKUP_DIR / f"{backup_name}.db").resolve()
    if not backup_path.is_relative_to(BACKUP_DIR.resolve()):
        raise ValueError("Invalid backup path")
    if not backup_path.exists():
        raise ValueError(f"Backup '{backup_name}' not found")
    # Derive original DB name
    parts = backup_name.rsplit("-backup-", 1)
    origin = parts[0] if len(parts) == 2 else backup_name
    target_path = _safe_db_path(origin)
    # Cannot overwrite the currently active database — switch away first
    if _current_db_path is not None and target_path.resolve() == _current_db_path.resolve():
        raise ValueError("Cannot restore over the currently active database. Switch to another database first.")
    shutil.copy2(backup_path, target_path)
    return origin


def delete_backup(backup_name: str) -> None:
    """Delete a backup file."""
    backup_path = (BACKUP_DIR / f"{backup_name}.db").resolve()
    if not backup_path.is_relative_to(BACKUP_DIR.resolve()):
        raise ValueError("Invalid backup path")
    if not backup_path.exists():
        raise ValueError(f"Backup '{backup_name}' not found")
    backup_path.unlink()


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
    with _lock:
        engine = _engine
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('entity_config', :cfg)"
        ), {"cfg": json.dumps(config)})


_FTS_TABLE = "initiative_fts"

# Cached FTS fields — set at table creation time, avoids DB calls inside event handlers
_fts_fields: tuple[str, ...] = ("name", "description", "sector", "technology_domains",
                                "categories", "market_domains", "faculty")


def _get_fts_fields() -> tuple[str, ...]:
    """Return the cached FTS searchable fields."""
    return _fts_fields


def _ensure_fts_table(engine) -> None:
    """Create the FTS5 table structure (rebuild deferred to first search)."""
    global _fts_fields
    try:
        # Read entity type directly from DB to avoid lock re-entry
        with engine.connect() as conn:
            et_row = conn.execute(text(
                "SELECT value FROM _meta WHERE key = 'entity_type'"
            )).scalar()
        entity_type = str(et_row) if et_row else "initiative"
        from scout.schema import get_schema
        _fts_fields = tuple(get_schema(entity_type)["searchable_fields"])
    except Exception:
        pass  # keep default
    cols = ", ".join(_fts_fields)
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5("
            f"    {cols},"
            f"    content='initiatives', content_rowid='id'"
            f")"
        ))


# ---------------------------------------------------------------------------
# FTS auto-sync via SQLAlchemy ORM events
# ---------------------------------------------------------------------------

def _fts_insert(connection, initiative) -> None:
    """Insert a single initiative into the FTS index."""
    fields = _get_fts_fields()
    params = {"id": initiative.id}
    for f in fields:
        params[f] = getattr(initiative, f, "") or ""
    cols = ", ".join(fields)
    placeholders = ", ".join(f":{f}" for f in fields)
    connection.execute(text(
        f"INSERT INTO {_FTS_TABLE}(rowid, {cols}) "
        f"VALUES (:id, {placeholders})"
    ), params)


def _fts_delete_by_values(connection, initiative_id: int, field_values: dict[str, str]) -> None:
    """Remove a single initiative from the FTS index using provided field values.

    FTS5 content-sync tables require the exact old values for delete operations.
    Using a SELECT from the content table is wrong in after_update handlers
    because the row already contains the new values at that point.
    """
    fields = _get_fts_fields()
    cols = ", ".join(fields)
    placeholders = ", ".join(f":{f}" for f in fields)
    params = {"id": initiative_id}
    params.update(field_values)
    connection.execute(text(
        f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, {cols}) "
        f"VALUES ('delete', :id, {placeholders})"
    ), params)


def _fts_field_values(initiative) -> dict[str, str]:
    """Extract current FTS field values from an Initiative ORM object."""
    return {f: getattr(initiative, f, "") or "" for f in _get_fts_fields()}


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
    for f in _get_fts_fields():
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
    from scout.scorer import seed_scoring_prompts
    seed_scoring_prompts(engine)
