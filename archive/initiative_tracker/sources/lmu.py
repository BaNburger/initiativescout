from __future__ import annotations

from lxml import html

from initiative_tracker.config import Settings, get_settings
from initiative_tracker.sources.common import absolutize, fetch_html, normalize_whitespace
from initiative_tracker.types import SourceInitiative


def _description_from_list_item(list_item_text: str, anchor_text: str) -> str:
    description = list_item_text.strip()
    if description.startswith(anchor_text):
        description = description[len(anchor_text) :].strip(" :-")
    elif ":" in description:
        description = description.split(":", 1)[1].strip()
    return normalize_whitespace(description)


def parse_lmu_html(html_text: str, *, page_url: str) -> list[SourceInitiative]:
    tree = html.fromstring(html_text)
    sections = tree.xpath("//div[contains(@class,'rte__content')]")
    initiatives: list[SourceInitiative] = []

    for section in sections:
        heading_nodes = section.xpath("./h2[1]")
        section_name = normalize_whitespace("".join(heading_nodes[0].itertext())) if heading_nodes else "LMU Section"

        links = section.xpath(".//a[contains(@class,'is-external')]")
        for link in links:
            anchor_text = normalize_whitespace("".join(link.itertext()))
            href = absolutize(page_url, link.get("href"))
            if not anchor_text or not href:
                continue

            list_items = link.xpath("ancestor::li[1]")
            list_item_text = normalize_whitespace(" ".join(list_items[0].itertext())) if list_items else anchor_text
            description = _description_from_list_item(list_item_text, anchor_text)

            initiatives.append(
                SourceInitiative(
                    name=anchor_text,
                    university="LMU",
                    source_name="LMU directory",
                    source_url=page_url,
                    external_url=href,
                    description_raw=description,
                    categories=[section_name],
                    metadata={"source": "lmu", "section": section_name},
                )
            )

    unique: dict[tuple[str, str], SourceInitiative] = {}
    for item in initiatives:
        key = (item.name.casefold(), (item.external_url or "").casefold())
        unique[key] = item
    return list(unique.values())


def fetch_lmu_directory(settings: Settings | None = None) -> list[SourceInitiative]:
    cfg = settings or get_settings()
    html_text = fetch_html(cfg.lmu_directory_url, cfg, delay_seconds=cfg.directory_request_delay_seconds)
    return parse_lmu_html(html_text, page_url=cfg.lmu_directory_url)
