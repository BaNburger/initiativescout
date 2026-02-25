from __future__ import annotations

from lxml import html

from initiative_tracker.sources.people_web import _extract_person_candidates


def test_person_extraction_filters_nav_and_legal_artifacts() -> None:
    tree = html.fromstring(
        """
        <html>
          <head><title>Alpha Team</title></head>
          <body>
            <p>Privacy Policy</p>
            <p>About Us</p>
            <p>Cookie Policy</p>
            <p>John Doe Founder and Jane Smith CTO</p>
          </body>
        </html>
        """
    )

    rows = _extract_person_candidates(tree, "https://alpha.example/team")
    names = {row["name"] for row in rows}

    assert "John Doe" in names
    assert "Jane Smith" in names
    assert "Privacy Policy" not in names
    assert "About Us" not in names
