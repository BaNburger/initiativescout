from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests

from initiative_tracker.config import Settings

logger = logging.getLogger(__name__)


def fetch_html(url: str, settings: Settings, *, delay_seconds: float = 0.0) -> str:
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.request_timeout_seconds,
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            sleep_for = settings.request_backoff_seconds * attempt
            logger.warning("Request failed (%s/%s) %s: %s", attempt, settings.max_retries, url, exc)
            time.sleep(sleep_for)
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def normalize_whitespace(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def extract_text(nodes: list[Any]) -> str:
    out = " ".join(" ".join(node.itertext()) for node in nodes)
    return normalize_whitespace(out)


def absolutize(base_url: str, maybe_relative_url: str | None) -> str:
    if not maybe_relative_url:
        return ""
    return urljoin(base_url, maybe_relative_url.strip())
