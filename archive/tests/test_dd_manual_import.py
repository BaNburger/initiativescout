from __future__ import annotations

from pathlib import Path

import pytest

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.pipeline.import_dd_manual import import_dd_manual
from initiative_tracker.store import upsert_initiative


def test_manual_import_rejects_malformed_rows(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'manual_dd.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        upsert_initiative(session, name="Manual Team", university="TUM", primary_url="https://manual.example")

    bad_csv = tmp_path / "bad_manual.csv"
    bad_csv.write_text(
        "initiative_name,commitment_level,source_url\n"
        "Manual Team,not_a_number,https://manual.example/dd\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        import_dd_manual(file_path=str(bad_csv), db_url=db_url)

    assert "Invalid float value" in str(exc.value)
