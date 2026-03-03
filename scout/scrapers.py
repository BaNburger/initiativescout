"""Entity-type-specific scrapers (e.g. TUM professor directory)."""
from __future__ import annotations

import logging
import re

import httpx
from lxml import html as lxml_html

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TUM professors
# ---------------------------------------------------------------------------

TUM_PROF_BASE = "https://www.professoren.tum.de"
TUM_PROF_URL = f"{TUM_PROF_BASE}/en/professors/tum-schools"

# Map full school names to short codes used as faculty values.
_TUM_SCHOOLS: dict[str, str] = {
    "computation, information & technology": "CIT",
    "engineering and design": "ED",
    "life sciences": "LS",
    "management": "MGT",
    "medicine & health": "MED",
    "natural sciences": "NAT",
    "social sciences & technology": "SST",
}

# Sections to skip (retired / in memoriam).
_SKIP_SECTIONS = re.compile(r"(retired|emerit|ruhestand|trauern|memoriam)", re.IGNORECASE)


def _match_school(text: str) -> str | None:
    """Return the short school code if *text* matches a known TUM school heading."""
    lower = text.lower()
    for fragment, code in _TUM_SCHOOLS.items():
        if fragment in lower:
            return code
    return None


async def scrape_tum_professors() -> list[dict[str, str]]:
    """Scrape professoren.tum.de (English version) and return professor dicts.

    Each dict: ``{"name": ..., "uni": "TUM", "faculty": "<school code>", "website": "<profile url>"}``.
    Skips retired / in-memoriam sections.
    """
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True,
        headers={"User-Agent": "ScoutBot/1.0 (+https://scout.local)"},
    ) as client:
        resp = await client.get(TUM_PROF_URL)
        resp.raise_for_status()

    tree = lxml_html.fromstring(resp.text)
    professors: list[dict[str, str]] = []
    current_school: str | None = None
    skip_section = False

    for el in tree.body.iter():
        # Detect school headings (h2/h3).
        if el.tag in ("h2", "h3"):
            heading = (el.text_content() or "").strip()
            school = _match_school(heading)
            if school:
                current_school = school
                skip_section = False
                continue
            # Detect retired/memoriam sub-headings.
            if _SKIP_SECTIONS.search(heading):
                skip_section = True
                continue

        # Detect skip sections in other elements (bold text, strong, etc.).
        if el.tag in ("strong", "b", "p"):
            text = (el.text_content() or "").strip()
            if _SKIP_SECTIONS.search(text):
                skip_section = True
                continue

        if el.tag != "a" or not current_school or skip_section:
            continue

        href = el.get("href", "")
        name = (el.text_content() or "").strip()
        if not name or not href:
            continue

        # Professor links are relative paths like /en/lastname-firstname.
        # Skip navigation / non-professor links.
        if href.startswith("http") or href.startswith("#") or href.startswith("mailto:"):
            continue
        # Must look like a name path (at least one segment after /).
        segments = [s for s in href.strip("/").split("/") if s]
        if not segments or len(segments[-1]) < 3:
            continue

        full_url = f"{TUM_PROF_BASE}{href}" if href.startswith("/") else href
        professors.append({
            "name": name,
            "uni": "TUM",
            "faculty": current_school,
            "website": full_url,
        })

    log.info("Scraped %d professors from %s", len(professors), TUM_PROF_URL)
    return professors
