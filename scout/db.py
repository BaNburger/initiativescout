from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base

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

DATA_DIR = Path(__file__).parent / "data"


def init_db(db_path: str | Path | None = None) -> None:
    global _engine, _SessionLocal, _current_db_path
    with _lock:
        if _engine is not None:
            _engine.dispose()
        if db_path is None:
            db_path = DATA_DIR / "scout.db"
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
        _current_db_path = db_path
        _migrate_existing_db(_engine)


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
    _seed_scoring_prompts(engine)


def get_session() -> Session:
    with _lock:
        if _SessionLocal is None:
            raise RuntimeError("init_db() has not been called")
        factory = _SessionLocal
    return factory()  # type: ignore[misc]


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager providing a transactional session scope.

    Usage (MCP server, scripts, etc.)::

        with session_scope() as session:
            ...
    """
    session = get_session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_generator() -> Generator[Session, None, None]:
    """Generator-based session suitable for FastAPI ``Depends()``.

    Usage::

        def db_session() -> Generator[Session, None, None]:
            yield from session_generator()
    """
    session = get_session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_db_name() -> str:
    """Return the stem (filename without .db) of the active database."""
    if _current_db_path is None:
        return "scout"
    return _current_db_path.stem


def list_databases() -> list[str]:
    """Return sorted list of DB stems in the data directory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in DATA_DIR.glob("*.db"))


def switch_db(name: str) -> None:
    """Switch to a different database by stem name. Creates if it doesn't exist."""
    db_path = DATA_DIR / f"{name}.db"
    init_db(db_path)


def create_database(name: str) -> None:
    """Create a new database and switch to it."""
    db_path = DATA_DIR / f"{name}.db"
    if db_path.exists():
        raise ValueError(f"Database '{name}' already exists")
    init_db(db_path)


def _seed_scoring_prompts(engine) -> None:
    """Seed default scoring prompts if the table is empty."""
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM scoring_prompts")).scalar()
        if count > 0:
            return
    from scout.scorer import DEFAULT_PROMPTS
    with engine.begin() as conn:
        for key, (label, content) in DEFAULT_PROMPTS.items():
            conn.execute(text(
                "INSERT INTO scoring_prompts (key, label, content) VALUES (:key, :label, :content)"
            ), {"key": key, "label": label, "content": content})
