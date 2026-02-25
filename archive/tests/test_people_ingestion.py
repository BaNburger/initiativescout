from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from initiative_tracker.config import Settings
from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import InitiativePerson, Person
from initiative_tracker.pipeline.ingest_people import ingest_people
from initiative_tracker.store import upsert_initiative


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    exports_dir = data_dir / "exports"
    reports_dir = tmp_path / "reports" / "latest"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "technology_taxonomy.yaml").write_text("technology_domains: {}\n", encoding="utf-8")
    (config_dir / "market_taxonomy.yaml").write_text("market_domains: {}\n", encoding="utf-8")
    (config_dir / "scoring_weights.yaml").write_text("dimension_weights: {}\n", encoding="utf-8")
    (config_dir / "technology_aliases.yaml").write_text("aliases: {}\n", encoding="utf-8")
    (config_dir / "market_aliases.yaml").write_text("aliases: {}\n", encoding="utf-8")
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
        technology_aliases_file=config_dir / "technology_aliases.yaml",
        market_aliases_file=config_dir / "market_aliases.yaml",
    )


def test_ingest_people_from_markdown_without_web_crawl(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    settings.ensure_directories()

    (tmp_path / "tier1_contacts.md").write_text(
        """
### 1. Alpha Robotics (TUM)
| **Lead** | Jane Doe |
| **Role** | Founder |
| **LinkedIn** | https://www.linkedin.com/in/jane-doe |
| **Email** | jane@example.com |
""",
        encoding="utf-8",
    )
    (tmp_path / "alumni_network.md").write_text("", encoding="utf-8")
    obsidian = tmp_path / "obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)
    (obsidian / "Student Initiatives - Contacts.md").write_text("", encoding="utf-8")
    (obsidian / "Student Initiatives - Alumni Network.md").write_text("", encoding="utf-8")

    db_url = f"sqlite:///{tmp_path / 'people.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        upsert_initiative(
            session,
            name="Alpha Robotics",
            university="TUM",
            primary_url="https://alpha.example",
        )

    details = ingest_people(crawl_mode="safe", max_pages=0, settings=settings, db_url=db_url)

    assert details["files_processed"] >= 1
    assert details["people_upserted"] >= 1
    assert details["links_created"] >= 1

    with session_scope(db_url) as session:
        people = session.execute(select(Person)).scalars().all()
        links = session.execute(select(InitiativePerson)).scalars().all()
        assert people
        assert links
