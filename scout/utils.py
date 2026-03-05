"""Shared utility functions used across Scout modules."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_MISSING = object()


def parse_comma_set(value: str | None) -> set[str] | None:
    """Parse a comma-separated string into a set, or None if empty/blank."""
    if not value:
        return None
    result = {s.strip() for s in value.split(",") if s.strip()}
    return result or None


def json_parse(value: str | None, default: Any = _MISSING) -> Any:
    """Safely parse a JSON string, returning *default* on failure.

    If no default is given, returns ``{}`` on parse error.
    """
    if not value:
        return {} if default is _MISSING else default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {} if default is _MISSING else default


# LLM env vars that can be sourced from .mcp.json
_LLM_ENV_KEYS = {
    "LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "GOOGLE_API_KEY", "GEMINI_API_KEY",
}


def load_llm_env() -> None:
    """Set LLM env vars from .mcp.json if not already in the environment.

    Walks up from the scout package directory looking for .mcp.json,
    reads the ``scout`` server's ``env`` block, and sets any missing
    LLM-related variables. This lets ``scout`` (web server) share the
    same LLM config as ``scout-mcp`` without requiring manual export.
    """
    # Only fill in what's missing
    needed = _LLM_ENV_KEYS - set(os.environ)
    if not needed:
        return
    # Walk up from the package directory to find .mcp.json
    d = Path(__file__).resolve().parent.parent
    for _ in range(5):
        mcp_file = d / ".mcp.json"
        if mcp_file.is_file():
            break
        d = d.parent
    else:
        return
    try:
        cfg = json.loads(mcp_file.read_text())
        env = cfg.get("mcpServers", {}).get("scout", {}).get("env", {})
        for key in needed:
            if key in env:
                os.environ[key] = env[key]
    except Exception:
        pass
