from __future__ import annotations

from lxml import html

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.sources.common import absolutize, extract_text, fetch_html, normalize_whitespace
from initiative_tracker.types import SourceInitiative


def parse_tum_html(html_text: str, *, page_url: str) -> list[SourceInitiative]:
    tree = html.fromstring(html_text)
    cards = tree.xpath("//div[contains(@class,'c-club')]")
    initiatives: list[SourceInitiative] = []

    for card in cards:
        name_nodes = card.xpath(".//div[contains(@class,'c-club__content')]//h4[1]")
        if not name_nodes:
            continue
        name = normalize_whitespace("".join(name_nodes[0].itertext()))
        if not name:
            continue

        description = extract_text(card.xpath(".//div[contains(@class,'c-club__content')]//p"))
        category_nodes = card.xpath(".//div[contains(@class,'c-club__category')]//a")
        categories = [normalize_whitespace("".join(node.itertext())) for node in category_nodes]

        link_nodes = card.xpath(".//div[contains(@class,'c-club__link')]//a[1]/@href")
        external_url = absolutize(page_url, link_nodes[0]) if link_nodes else ""

        initiatives.append(
            SourceInitiative(
                name=name,
                university="TUM",
                source_name="TUM directory",
                source_url=page_url,
                external_url=external_url,
                description_raw=description,
                categories=[c for c in categories if c],
                metadata={"source": "tum", "card_type": "c-club"},
            )
        )

    unique: dict[tuple[str, str], SourceInitiative] = {}
    for item in initiatives:
        key = (item.name.casefold(), (item.external_url or "").casefold())
        unique[key] = item
    return list(unique.values())


def fetch_tum_directory(settings: Settings | None = None) -> list[SourceInitiative]:
    cfg = settings or get_settings()
    html_text = fetch_html(cfg.tum_directory_url, cfg, delay_seconds=cfg.directory_request_delay_seconds)
    return parse_tum_html(html_text, page_url=cfg.tum_directory_url)
