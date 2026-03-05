# Scout — Entity Sourcing & Scoring Engine

Web app and MCP server for discovering, enriching, and scoring any type of entity — companies, people, initiatives, products, research papers, and more. Scout provides a structured pipeline: import data, enrich it with live web signals, score it with LLM-powered evaluations, and export results.

## Installation

Requires **Python 3.11+**.

### Minimal install (web UI + MCP server)

```bash
pip install git+https://github.com/BaNburger/initiativescout.git
```

This gives you the core: web UI, REST API, MCP server, web enrichment, and FTS5 search. No heavy dependencies — just FastAPI, SQLAlchemy, httpx, and lxml.

### Install with extras

Pick only what you need:

```bash
# XLSX import/export
pip install 'scout[xlsx]'

# Semantic similarity search (model2vec, ~15MB, no PyTorch)
pip install 'scout[embeddings]'

# LLM scoring providers
pip install 'scout[anthropic]'    # Anthropic (Claude)
pip install 'scout[openai]'       # OpenAI / OpenAI-compatible

# Enhanced web crawling
pip install 'scout[crawl]'        # Crawl4AI (JS rendering) + DuckDuckGo discovery
pip install 'scout[extract]'      # trafilatura + extruct (structured data extraction)

# Everything
pip install 'scout[all]'
```

You can combine extras: `pip install 'scout[xlsx,embeddings,anthropic]'`

### Recommended: pipx (isolated install)

```bash
pipx install 'git+https://github.com/BaNburger/initiativescout.git'

# With extras:
pipx install 'git+https://github.com/BaNburger/initiativescout.git[xlsx,embeddings,anthropic]'
```

### Development install

```bash
git clone https://github.com/BaNburger/initiativescout.git
cd initiativescout
pip install -e '.[dev]'           # core + pytest
pip install -e '.[dev,all]'       # core + pytest + all optional features
```

### Update / Uninstall

```bash
pip install --upgrade git+https://github.com/BaNburger/initiativescout.git
pip uninstall scout
```

## Quickstart

```bash
scout                 # web UI on http://127.0.0.1:8001
scout --port 9000     # use a different port
scout --host 0.0.0.0  # accept connections from the network
scout-mcp             # MCP server over stdio (for Claude Desktop / MCP clients)
scout-setup all       # auto-configure MCP for Claude Desktop, Cursor, Windsurf
scout --version       # print version
```

Open the browser and start adding entities — via the web UI, the REST API, or MCP tools.

## How It Works

1. **Import** — Add entities manually, via the REST API, via MCP tools, or by uploading an XLSX spreadsheet (requires `scout[xlsx]`).
2. **Discover** — Find additional URLs (LinkedIn, GitHub, HuggingFace, Crunchbase) via DuckDuckGo search (requires `scout[crawl]`).
3. **Enrich** — Fetch live data from entity websites, team pages, GitHub orgs, and all discovered links. Uses Crawl4AI for JS rendering when installed, otherwise falls back to httpx+lxml.
4. **Score** — Parallel LLM calls evaluate configurable dimensions (e.g. Team, Tech, Opportunity). Verdict and score are computed deterministically from the average grade. Supports Anthropic and OpenAI providers.
5. **Browse** — Filter, sort, and inspect entities in the web UI. Full keyboard navigation, inline editing, live updates via revision polling.
6. **Search** — FTS5 full-text search with BM25 ranking. Semantic similarity search via model2vec embeddings (requires `scout[embeddings]`).
7. **Export** — Download filtered results as a styled XLSX workbook (requires `scout[xlsx]`).

## Entity Types

Scout is entity-agnostic. The `entity_type` field on each database controls scoring dimensions, enrichment strategies, and default prompts. Built-in types include `initiative` and `professor`, but you can define any type via `ENTITY_CONFIG` with custom:

- Scoring dimensions and weights
- Enrichment pipelines
- Classification labels
- LLM prompts

All entity-specific data beyond the universal columns (name, description, website, email) is stored in `custom_fields_json`, so no schema changes are needed for new entity types.

## Web UI Features

- **Database switching** — Select or create databases from the header dropdown.
- **Import / Export XLSX** — Import spreadsheets and export scored results with verdict-colored rows.
- **Batch operations** — "Score Unscored" and "Rescore All" buttons with progress bar, pause, and cancel controls.
- **Prompt editor** — Edit the scoring dimension prompts directly in the UI (Prompts button).
- **MCP Setup** — In-app setup instructions for connecting Scout to Claude Desktop, Cursor, and Windsurf.
- **Revision polling** — The UI automatically refreshes when data changes from MCP tools or other processes.
- **Custom columns** — Define additional per-entity fields (text, number, boolean, URL) that appear in the list view and are editable inline.
- **Keyboard shortcuts** — Press `?` for the full shortcut overlay.

## Keyboard Shortcuts

**Grid mode** (table focused):

| Key | Action |
|-----|--------|
| Arrow keys | Move cursor cell-by-cell |
| `Enter` | Open detail for selected row |
| `e` | Enrich selected entity |
| `s` | Score selected entity |
| `i` | Open import |
| `/` | Focus search |
| `?` | Show keyboard shortcut help |

**Detail mode** (detail panel focused):

| Key | Action |
|-----|--------|
| `Up` / `Down` | Browse prev/next entity |
| `\` | Return to grid |
| `e` | Enrich current entity |
| `s` | Score current entity |
| `f` | Find similar |
| `Esc` | Close overlay / return to grid |

## Scoring Architecture

Each entity is scored on configurable dimensions in parallel. The default `initiative` type uses three dimensions:

| Dimension | Data Sources | LLM Evaluates |
|-----------|-------------|---------------|
| **Team** | Team page enrichment, LinkedIn, member count/roles, team size, competitions | Team composition, leadership, execution capability |
| **Tech** | GitHub enrichment, repo count/contributors/commits, HuggingFace/OpenAlex/Semantic Scholar hits | Technical depth, code quality, research output, novelty |
| **Opportunity** | Full dossier (all enrichments + all signals) | Market size, timing, competitive landscape, commercial intent |

Each dimension returns a school grade (A+ through D, where A+=1.0, D=4.0) and reasoning.

**Deterministic aggregation:**

| Average Grade | Verdict |
|--------------|---------|
| <= 1.7 | `reach_out_now` |
| <= 2.7 | `reach_out_soon` |
| <= 3.3 | `monitor` |
| > 3.3 | `skip` |

**Score** = `round(5.0 - avg_grade)` snapped to half-points (higher = better).

### LLM Provider Configuration

Scoring uses a configurable LLM provider, set via environment variables (typically in `.mcp.json`):

- **Anthropic** (default) — requires `ANTHROPIC_API_KEY` and `scout[anthropic]`. Default model: `claude-haiku-4-5-20251001`.
- **OpenAI** — set `LLM_PROVIDER=openai` and `OPENAI_API_KEY`, requires `scout[openai]`. Default model: `gpt-4o-mini`.
- **OpenAI-compatible** — set `LLM_PROVIDER=openai_compatible`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL`.

The web server auto-loads LLM env vars from `.mcp.json` if present, so the same config works for both `scout` and `scout-mcp`.

### Customizing Prompts

Scoring prompts are stored in the database and editable via:

- The **Prompts** button in the web UI
- `GET /api/scoring-prompts` / `PUT /api/scoring-prompts/{key}` (REST API)
- `list_scoring_prompts()` / `update_scoring_prompt()` (MCP tools)

Default prompts are seeded on first run and can be freely modified per database.

### LLM-Free Scoring

If no API key is available, use the MCP dossier-and-submit workflow:

1. `get_scoring_dossier(id)` — builds the dimension dossiers and prompts locally (no API call).
2. The calling LLM (e.g. Claude in Claude Desktop) evaluates the dossiers.
3. `submit_score(id, grade_team, grade_tech, grade_opportunity, classification, ...)` — saves the result.

## Search Modes

| Mode | How | Best For |
|------|-----|----------|
| **FTS5 Keyword** | `list_initiatives(search='robotics')` | Fast ranked search across all text fields |
| **Semantic** | `find_similar_initiatives(query='applied ML workshops')` | Meaning-based search beyond exact keywords |
| **Similar** | `find_similar_initiatives(initiative_id=42)` | "Show me more like this one" |
| **Hybrid** | `find_similar_initiatives(query='...', uni='TUM')` | SQL pre-filter + semantic ranking |
| **Compact** | `list_initiatives(fields='id,name,verdict,score')` | Token-efficient listing for AI agents |

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/initiatives` | List with filters (`verdict`, `uni`, `faculty`, `classification`, `search`, `fields`) and pagination |
| `GET` | `/api/initiatives/{id}` | Full detail with enrichments, projects, and scores |
| `PUT` | `/api/initiatives/{id}` | Update entity fields (partial update) |
| `GET` | `/api/initiatives/{id}/projects` | List projects for an entity |
| `POST` | `/api/initiatives/{id}/projects` | Create a new project |
| `PUT` | `/api/projects/{id}` | Update project fields |
| `DELETE` | `/api/projects/{id}` | Delete a project and its scores |
| `POST` | `/api/enrich/{id}` | Enrich single entity |
| `POST` | `/api/enrich/batch` | Enrich all (SSE progress stream) |
| `POST` | `/api/discover/{id}` | Discover new URLs via DuckDuckGo |
| `POST` | `/api/score/{id}` | Score single entity (parallel LLM calls) |
| `POST` | `/api/score/batch` | Score all (SSE progress stream) |
| `POST` | `/api/projects/{id}/score` | Score a project via LLM |
| `GET` | `/api/scoring-prompts` | List scoring prompt definitions |
| `PUT` | `/api/scoring-prompts/{key}` | Update a scoring prompt |
| `GET` | `/api/stats` | Counts by verdict, classification, uni |
| `GET` | `/api/aggregations` | Score distributions by uni/faculty, top-N per verdict, grade breakdowns |
| `GET` | `/api/similar/{id}` | Find semantically similar entities |
| `GET` | `/api/search/semantic` | Semantic text search with optional SQL pre-filters |
| `POST` | `/api/embed` | Build/rebuild dense embeddings |
| `GET` | `/api/export` | Export entities to XLSX (with verdict/uni filters) |
| `POST` | `/api/import` | Upload `.xlsx` (multipart form) |
| `GET` | `/api/databases` | List available databases |
| `POST` | `/api/databases/select` | Switch database |
| `POST` | `/api/databases/create` | Create new database |
| `POST` | `/api/databases/delete` | Delete a database |
| `POST` | `/api/databases/backup` | Backup a database |
| `GET` | `/api/databases/backups` | List all backups |
| `POST` | `/api/databases/restore` | Restore a database from backup |
| `DELETE` | `/api/databases/backups/{name}` | Delete a backup |
| `GET` | `/api/custom-columns` | List custom column definitions |
| `POST` | `/api/custom-columns` | Add custom column |
| `PUT` | `/api/custom-columns/{id}` | Update custom column |
| `DELETE` | `/api/custom-columns/{id}` | Remove custom column |
| `GET` | `/api/revision` | Data revision counter for change detection |
| `DELETE` | `/api/reset` | Wipe all data |

## MCP Server

The `scout-mcp` entry point runs an MCP server over stdio, exposing Scout's functionality as tools for Claude Desktop and other MCP clients. Use `scout-setup all` to auto-configure all supported editors, or click "MCP Setup" in the web UI for step-by-step instructions.

### Mutation Safeguards

- `delete_initiative()` and `delete_project()` require `confirm=True` to execute. Without it, they return a dry-run warning showing what would be deleted.
- `update_initiative()` emits a warning when the `name` field is changed, showing old and new values, to prevent accidental identity changes.
- The MCP server instructions explicitly warn against renaming or deleting entities without verification, and recommend creating a test database for experiments.

### Workflows

**Autonomous:** `get_stats()` -> `get_work_queue()` -> follow `recommended_action` for each item -> repeat until queue is empty.

**Bulk (recommended):** `process_queue(limit=20)` enriches AND scores in one call. Repeat until `remaining_in_queue=0`.

**Selective batch:** `batch_enrich(initiative_ids='1,2,3')` -> `batch_score(initiative_ids='1,2,3')` for specific items.

**Deep mode:** `discover_initiative(id)` -> `enrich_initiative(id)` -> `score_initiative_tool(id)` for thorough single-item processing.

**Analytics:** `get_stats()` -> `get_aggregations()` -> `list_initiatives(verdict='reach_out_now', fields='id,name,uni,score')` for a quick overview.

**Similarity:** `find_similar_initiatives(query='applied ML workshops')` or `find_similar_initiatives(initiative_id=42)`. Embeddings auto-update on each enrichment; use `embed_all_tool()` to rebuild all at once.

**Export:** `export_initiatives(verdict='reach_out_now')` saves an XLSX file to the data directory.

### Available Tools

| Tool | Description |
|------|-------------|
| `get_stats` | Summary statistics and breakdowns |
| `get_aggregations` | Score distributions by uni/faculty, top-N per verdict, grade breakdowns |
| `get_work_queue` | Prioritized queue of entities needing enrichment or scoring |
| `list_initiatives` | Browse and filter with verdict, uni, faculty, classification, search, `fields` for compact mode |
| `get_initiative` | Full details with enrichments, projects, and scores (supports `compact` mode) |
| `create_initiative` | Add a new entity to the database |
| `update_initiative` | Update entity fields (warns on name changes) |
| `delete_initiative` | Remove an entity and all associated data (requires `confirm=True`) |
| `enrich_initiative` | Fetch fresh web/GitHub enrichment data |
| `discover_initiative` | Discover new URLs via DuckDuckGo (LinkedIn, GitHub, HuggingFace, etc.) |
| `score_initiative_tool` | Score dimensions in parallel, aggregate deterministically |
| `get_scoring_dossier` | Build scoring dossiers and prompts without making LLM calls |
| `submit_score` | Submit externally computed grades and verdict |
| `submit_enrichment` | Submit enrichment data found by the calling LLM |
| `batch_enrich` | Enrich multiple entities in one call (shared browser, 3 concurrent) |
| `batch_score` | Score multiple entities in one call |
| `process_queue` | Autonomous pipeline: fetch queue -> enrich -> score in one call |
| `embed_all_tool` | Build/rebuild dense embeddings for similarity search |
| `find_similar_initiatives` | Semantic similarity search (by query text or entity ID, with SQL pre-filters) |
| `export_initiatives` | Export entities to XLSX file (with verdict/uni filters) |
| `create_project` | Add a sub-project to an entity |
| `update_project` | Update project fields |
| `delete_project` | Remove a project and its scores (requires `confirm=True`) |
| `score_project_tool` | Score a project in context of its parent entity |
| `list_scoring_prompts` | View the dimension prompt definitions (supports `compact` mode) |
| `update_scoring_prompt` | Customize a dimension's LLM system prompt |
| `manage_database` | List, select, create, delete, backup, restore databases |
| `get_custom_columns` | List custom column definitions |
| `create_custom_column` | Add a custom column definition |
| `update_custom_column` | Update a custom column definition |
| `delete_custom_column` | Remove a custom column definition |
| `scrape_tum_professors` | Scrape TUM professor directory and import |

## Project Structure

```
initiativescout/
├── pyproject.toml            # Package config & dependencies
├── scout/                    # FastAPI web app + MCP server
│   ├── __init__.py           #   Version definition
│   ├── app.py                #   Routes & API endpoints
│   ├── mcp_server.py         #   MCP server (Claude Desktop integration)
│   ├── services.py           #   Shared business logic (queries, FTS, aggregations)
│   ├── models.py             #   SQLAlchemy ORM models
│   ├── schemas.py            #   Pydantic request/response schemas
│   ├── db.py                 #   Multi-DB SQLite management, backups, FTS5 setup
│   ├── importer.py           #   XLSX parser (Spin-Off, All Initiatives, Overview)
│   ├── exporter.py           #   XLSX export with styled verdict rows
│   ├── enricher/             #   Web enrichment pipeline
│   │   ├── _core.py          #     Shared HTTP client, URL cache, parsing
│   │   ├── _website.py       #     Website & team page enrichment
│   │   ├── _github.py        #     GitHub org/repo enrichment
│   │   ├── _metadata.py      #     Structured data, tech stack, DNS, sitemap
│   │   └── _discovery.py     #     DuckDuckGo URL discovery
│   ├── scorer.py             #   Dimension LLM scoring + deterministic aggregation
│   ├── embedder.py           #   Dense embeddings (model2vec) + similarity search
│   ├── scrapers.py           #   Entity-specific scrapers (TUM professor directory)
│   ├── utils.py              #   Shared utilities (JSON parsing, LLM env loading)
│   ├── setup_mcp.py          #   Auto-configure MCP for Claude Desktop, Cursor, Windsurf
│   └── static/
│       ├── index.html        #   Page structure
│       ├── style.css         #   Styles
│       └── app.js            #   Frontend logic
└── .gitignore
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | If using Anthropic | Anthropic API key for LLM scoring |
| `GITHUB_TOKEN` | No | Increases GitHub API rate limits during enrichment |
| `LLM_PROVIDER` | No | `anthropic` (default) or `openai` / `openai_compatible` |
| `LLM_MODEL` | No | Override model name (default: `claude-haiku-4-5-20251001` or `gpt-4o-mini`) |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `OPENAI_BASE_URL` | No | Custom OpenAI-compatible endpoint |

These can be set in `.mcp.json` under `mcpServers.scout.env` — both `scout` (web server) and `scout-mcp` read from this file automatically.

## Troubleshooting

**Port already in use**

If you see `Port 8001 is already in use`, another process is occupying the default port. Pick a different one:

```bash
scout --port 9000
```

**API key not configured**

Scoring requires an LLM API key. Set it via environment variable or in `.mcp.json`:

```bash
export ANTHROPIC_API_KEY=...   # for Anthropic (requires scout[anthropic])
export OPENAI_API_KEY=sk-...   # for OpenAI (requires scout[openai])
```

**Missing optional dependency**

If you see an `ImportError` mentioning `scout[...]`, install the missing extra:

```bash
pip install 'scout[xlsx]'          # for XLSX import/export
pip install 'scout[embeddings]'    # for semantic search
pip install 'scout[anthropic]'     # for Anthropic scoring
pip install 'scout[openai]'        # for OpenAI scoring
pip install 'scout[crawl]'         # for Crawl4AI + DuckDuckGo
pip install 'scout[extract]'       # for trafilatura + extruct
pip install 'scout[all]'           # everything
```

## License

MIT — see [LICENSE](LICENSE).
