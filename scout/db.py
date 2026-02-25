from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base

_engine = None
_SessionLocal = None


def init_db(db_path: str | Path | None = None) -> None:
    global _engine, _SessionLocal
    if db_path is None:
        db_path = Path(__file__).parent / "data" / "scout.db"
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    _engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()  # type: ignore[misc]
