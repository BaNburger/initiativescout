from __future__ import annotations

from lxml import html

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.sources.common import absolutize, fetch_html, normalize_whitespace
from initiative_tracker.types import SourceInitiative


def parse_hm_html(html_text: str, *, page_url: str) -> list[SourceInitiative]:
    tree = html.fromstring(html_text)
    cards = tree.xpath("//div[contains(@class,'ctl_card')]")
    initiatives: list[SourceInitiative] = []

    for card in cards:
        link_nodes = card.xpath(".//a[1]")
        if not link_nodes:
            continue
        link = link_nodes[0]

        paragraphs = link.xpath(".//p")
        if not paragraphs:
            continue

        name = normalize_whitespace("".join(paragraphs[0].itertext()))
        subtitle = normalize_whitespace("".join(paragraphs[1].itertext())) if len(paragraphs) > 1 else ""
        href = absolutize(page_url, link.get("href"))
        if not name or not href:
            continue

        section_nodes = card.xpath("ancestor::section[1]//h2[1]")
        section_name = normalize_whitespace("".join(section_nodes[0].itertext())) if section_nodes else "HM Section"

        initiatives.append(
            SourceInitiative(
                name=name,
                university="HM",
                source_name="HM directory",
                source_url=page_url,
                external_url=href,
                description_raw=subtitle,
                categories=[section_name],
                metadata={"source": "hm", "section": section_name},
            )
        )

    unique: dict[tuple[str, str], SourceInitiative] = {}
    for item in initiatives:
        key = (item.name.casefold(), (item.external_url or "").casefold())
        unique[key] = item
    return list(unique.values())


def fetch_hm_directory(settings: Settings | None = None) -> list[SourceInitiative]:
    cfg = settings or get_settings()
    html_text = fetch_html(cfg.hm_directory_url, cfg, delay_seconds=cfg.directory_request_delay_seconds)
    return parse_hm_html(html_text, page_url=cfg.hm_directory_url)
