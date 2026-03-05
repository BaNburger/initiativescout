# Scout v1.1.0

**Entity-agnostic sourcing & scoring engine.**

Scout discovers, enriches, and scores any type of entity — initiatives, professors, companies, products, research papers, and more. It combines automated web enrichment with LLM-powered multi-dimensional scoring to produce actionable outreach recommendations.

## What's New in v1.1.0

### Entity-Agnostic Architecture

Scout is no longer limited to student initiatives. The entity type system now supports any domain with configurable:

- **Scoring dimensions** and weights (not just team/tech/opportunity)
- **Classification labels** per entity type
- **Default LLM prompts** seeded per entity type
- **Metadata storage** via `metadata_json` for arbitrary per-entity fields

Built-in types: `initiative`, `professor`. Custom types: define any dimensions, classifications, and prompts.

### Enrichment Pipeline Overhaul

- **Modular enricher package** — split from a single 1,300-line file into focused submodules: `_core.py` (HTTP client, URL cache), `_website.py` (site + team page), `_github.py` (org/repo analysis), `_metadata.py` (structured data, tech stack, DNS, sitemap), `_discovery.py` (DuckDuckGo URL discovery).
- **6 new enrichment methods**: tech stack detection, DNS/WHOIS, sitemap parsing, careers page, git deep analysis, structured data (JSON-LD, microdata, RDFa via extruct).
- **Per-entity URL cache** — avoids re-fetching the same URL within a session.
- **Shared async HTTP client** — single `httpx.AsyncClient` instance across all enrichers.

### Scoring Improvements

- **Chain-of-thought reasoning** with low temperature for more consistent grades.
- **Dimension pruning** — skips dimensions with insufficient evidence rather than producing noise.
- **Weighted grade aggregation** — classification-aware weights (e.g., heavy-tech entities weight the tech dimension higher).
- **Custom dimension support** via `dimension_grades` parameter for non-standard entity types.

### Backup & Restore

- **Separate backup directory** — backups now stored in `scout/data/backups/`, not alongside live databases.
- **Backups panel** in the Web UI — create, list, restore, and delete backups from a dedicated overlay.
- **MCP tools** — `manage_database` supports `backup`, `list_backups`, `restore`, and `delete_backup` actions.
- **REST API** — `GET /api/databases/backups`, `POST /api/databases/restore`, `DELETE /api/databases/backups/{name}`.

### Bulk Operations

- **`bulk_create` action** on `manage_initiative` — create 100+ entities in a single MCP call with automatic deduplication.
- **`process_queue`** — autonomous enrich+score pipeline that handles batches end-to-end.

### Web UI

- **Shadcn/zinc dark theme** — modern aesthetic with consistent design tokens.
- **Backups panel** — manage database backups from the header.
- **Dynamic university selector** — free-text input replaces hardcoded dropdown.
- **Keyboard shortcuts** — full grid/detail navigation, `?` for shortcut help overlay.
- **Revision polling** — auto-pauses when tab is hidden to reduce idle load.
- **Custom columns** — define additional per-entity fields visible in the list view.
- **Type-to-confirm delete** — database deletion requires typing the name.

### MCP Server

- **Tool consolidation** — from 32 tools down to ~20 focused tools.
- **Self-guiding responses** — every result includes `next` action suggestions.
- **State pulse** — `_db` field in responses shows database health at a glance.
- **Tool annotations** — `readOnlyHint` and `destructiveHint` for MCP clients.
- **`scout://overview` resource** — full workflow docs, grading scale, and classifications.

### Developer Experience

- **Optional dependencies** — minimal install with `pip install scout`, extras for XLSX, embeddings, LLM providers, crawling, and structured data extraction.
- **CI** — GitHub Actions with Python 3.11/3.12/3.13 matrix.
- **221 tests** — comprehensive coverage of API, MCP tools, enrichment, and scoring.
- **`scout-setup`** — auto-configure MCP for Claude Desktop, Cursor, and Windsurf.

### Code Quality (v1.1.0)

- Extracted `_add_column_if_missing()` helper to DRY up 6 migration blocks.
- Extracted `merge_custom_fields()` helper to DRY up 3 custom fields merge patterns.
- Added `Grade.normalize()` static method, replacing 3 inline normalization patterns.
- Fixed N+1 query in `import_scraped_entities()` — loads all names upfront.
- Removed unused import (`and_` in mcp_server.py, `_normalize_url` in enricher).
- Replaced deprecated `session.query()` with `select()` in bulk_create.
- Frontend revision polling pauses when browser tab is hidden.

## Architecture

- **LLM-as-a-user** — the MCP server is the primary interface; the web UI is a companion.
- **Lean** — ~11k lines of Python, ~1,900 lines of vanilla JS. No frameworks, no build step.
- **SQLite** — single-file databases with WAL mode, FTS5 full-text search, trigger-based revision tracking.
- **Deterministic scoring** — grades and verdicts are reproducible from the same enrichment data.

## Install

```bash
pip install "scout[all] @ git+https://github.com/BaNburger/initiativescout.git"
```

## License

MIT
