# Scout Development Guide

## Guiding Principles

1. **LLM-as-a-user first** — The MCP server is the primary interface. Every feature must work great for an LLM caller: token-efficient responses, clear next-action suggestions, compact mode options.

2. **Super lean** — Minimal dependencies, minimal abstractions. No framework beyond FastAPI+SQLAlchemy. Optional features use `pip install 'scout[extra]'` — never bloat the core.

3. **Super simple** — Three similar lines > premature abstraction. Code should be readable top-to-bottom. Flat is better than nested. If a function needs a docstring longer than 2 lines, it's probably too complex.

4. **High performance** — Parallel enrichment, parallel LLM scoring, async everywhere. SQLite WAL mode. No unnecessary DB round-trips. Cache at creation time, not query time. No overhead.

5. **Reliable & robust** — Parse don't validate (Grade dataclass). Deterministic aggregation (no LLM in the verdict loop). Graceful degradation when optional deps are missing. Never lose data on error.

## Architecture

- **scout/app.py** — FastAPI web UI + REST API
- **scout/mcp_server.py** — MCP server (Claude Desktop, Cursor, etc.)
- **scout/services.py** — Shared business logic (both app.py and mcp_server.py call this)
- **scout/scorer.py** — LLM scoring engine + dossier builders
- **scout/enricher/** — Web enrichment pipeline (parallel async)
- **scout/models.py** — SQLAlchemy ORM
- **scout/db.py** — Multi-database SQLite management
- **scout/prompts/** — Scoring prompt templates (external .txt files for eval framework compatibility)

## Conventions

- Optional deps: guarded with `try/except ImportError` + `_AVAILABLE` flag
- Error responses (MCP): `{"ok": False, "error": str, "error_code": str}`
- All enrichment data: `raw_text` (full, 15k max) + `summary` (compact, 1.5k max)
- Scores: deterministic from grade numerics, never from LLM text
- Tests: `python -m pytest scout/tests/ -x -q`

## What NOT to do

- Don't add dependencies to core — use optional extras
- Don't put business logic in app.py or mcp_server.py — put it in services.py
- Don't embed prompts in Python — use scout/prompts/*.txt files
- Don't add LLM calls to the verdict/score computation path
- Don't use `session.query()` — use `select()` (SQLAlchemy 2.0 style)
