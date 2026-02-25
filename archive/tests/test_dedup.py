from sqlalchemy import select

from initiative_tracker.db import init_db, session_scope
from initiative_tracker.models import Initiative
from initiative_tracker.store import upsert_initiative


def test_upsert_merges_variants_by_normalized_name(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'dedup.db'}"
    init_db(db_url)

    with session_scope(db_url) as session:
        one = upsert_initiative(
            session,
            name="Akaflieg München e.V.",
            university="HM",
            primary_url="https://www.akaflieg.example/",
            categories=["Aerospace"],
        )
        two = upsert_initiative(
            session,
            name="Akaflieg Munchen e.V",
            university="HM",
            primary_url="https://www.akaflieg.example",
            technologies=["aerospace_space"],
        )

        assert one.id == two.id
        all_rows = session.execute(select(Initiative)).scalars().all()
        assert len(all_rows) == 1
