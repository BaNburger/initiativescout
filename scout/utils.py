"""Shared utility functions used across Scout modules."""
from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def json_parse(value: str | None, default: Any = _MISSING) -> Any:
    """Safely parse a JSON string, returning *default* on failure.

    If no default is given, returns ``{}`` on parse error.
    """
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return {} if default is _MISSING else default
