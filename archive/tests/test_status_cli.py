from __future__ import annotations

import json

from typer.testing import CliRunner

from initiative_tracker.cli import app
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.store import upsert_initiative


def test_set_status_command_updates_initiative_status(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'status.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        initiative = upsert_initiative(session, name="Status Team", university="TUM", primary_url="https://status.example")
        initiative_id = initiative.id

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--json",
            "set-status",
            "--initiative-id",
            str(initiative_id),
            "--status",
            "priority",
            "--owner",
            "Scout Test",
            "--next-step-date",
            "2026-02-20",
            "--note",
            "Queued discovery call",
            "--db-url",
            db_url,
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "priority"
    assert payload["owner"] == "Scout Test"
    assert payload["next_step_date"] == "2026-02-20"
