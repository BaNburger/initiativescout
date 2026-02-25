from pathlib import Path

from initiative_tracker.pipeline.seed_markdown import parse_markdown_initiatives


def test_parse_markdown_initiatives_extracts_core_fields(tmp_path: Path) -> None:
    markdown = tmp_path / "sample.md"
    markdown.write_text(
        """
## TUM Initiatives
#### 1. Example Initiative
| **Description** | Builds autonomous drones for rescue operations. |
| **Website** | https://example.org |
| **Technologies** | AI, Robotics, Computer Vision |
| **Team Size** | 25 members |
| **Tech Rating** | 4 |
| **Talent Rating** | 5 |
| **Applicability** | 4 |
| **Maturity** | 3 |
""",
        encoding="utf-8",
    )

    rows = parse_markdown_initiatives(markdown)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Example Initiative"
    assert row["university"] == "TUM"
    assert row["primary_url"] == "https://example.org"
    assert "AI" in row["technologies"]
    assert row["team_size"] == 25
    assert row["ratings"]["tech_depth"] == 4.0
