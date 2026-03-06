# Scout — Entity Sourcing & Scoring Engine

Scout helps you find, research, and prioritize outreach targets — whether they're companies, professors, student initiatives, or any other type of entity. It automates the tedious parts: fetching data from websites, GitHub, LinkedIn, and academic databases, then scoring each entity with AI-powered evaluations.

**Two interfaces, one database:**
- **Web UI** — browser-based dashboard for browsing, filtering, and managing entities
- **MCP Server** — plug Scout into Claude Desktop, Cursor, or Windsurf and let AI drive the pipeline

## Getting Started

### 1. Install

Requires **Python 3.11+**.

```bash
# Recommended: install with common extras
pip install "scout[openai,xlsx,crawl] @ git+https://github.com/BaNburger/initiativescout.git"

# Minimal install (web UI + basic enrichment only)
pip install git+https://github.com/BaNburger/initiativescout.git
```

### 2. Set your API key

Scoring requires an LLM API key. Set one of these:

```bash
export OPENAI_API_KEY=sk-...        # OpenAI (default model: gpt-5-mini)
export ANTHROPIC_API_KEY=sk-ant-... # Anthropic (default model: claude-haiku-4-5)
```

### 3. Launch

```bash
scout                 # opens web UI at http://127.0.0.1:8001
scout-mcp             # starts MCP server (for AI editors)
scout-setup all       # auto-configure MCP for Claude Desktop, Cursor, Windsurf
```

### 4. Add entities and go

Open the browser. Import an XLSX spreadsheet, add entities manually, or use the MCP tools from Claude Desktop. Then:

1. **Enrich** — Scout fetches data from websites, GitHub, LinkedIn, and more
2. **Score** — AI evaluates each entity on configurable dimensions
3. **Browse** — Filter by verdict, sort by score, inspect details, export to XLSX

## How Scoring Works

Each entity is scored on configurable dimensions (e.g., Team, Tech, Opportunity) using parallel LLM calls. Each dimension returns a school grade (A+ through D).

| Average Grade | Verdict | Meaning |
|--------------|---------|---------|
| A to B+ | `reach_out_now` | High priority — contact immediately |
| B to C+ | `reach_out_soon` | Good fit — schedule outreach |
| C to C- | `monitor` | Interesting but not ready |
| D+ to D | `skip` | Not a match right now |

Verdicts and scores are **deterministic** — same enrichment data + same LLM response = same result every time.

## Entity Types

Scout is entity-agnostic. Each database has an entity type that controls scoring dimensions, prompts, and enrichment strategies. Built-in types:

| Type | Dimensions | Use Case |
|------|-----------|----------|
| `initiative` | Team, Tech, Opportunity | Student initiatives, startups, clubs |
| `professor` | Research Group, Research Output, Collaboration | Academic outreach, research partnerships |

You can define custom entity types with your own dimensions, classifications, and prompts.

## Optional Extras

Install only what you need:

```bash
pip install 'scout[xlsx]'          # XLSX import/export
pip install 'scout[embeddings]'    # Semantic similarity search (model2vec, ~15MB)
pip install 'scout[mcp]'           # MCP server for AI editors
pip install 'scout[anthropic]'     # Anthropic scoring provider
pip install 'scout[openai]'        # OpenAI scoring provider
pip install 'scout[crawl]'         # Crawl4AI (JS rendering) + DuckDuckGo discovery
pip install 'scout[extract]'       # trafilatura + extruct (structured data)
pip install 'scout[dns]'           # DNS/MX/SPF record analysis
pip install 'scout[all]'           # Everything
```

Combine extras: `pip install 'scout[xlsx,embeddings,anthropic]'`

## Web UI

- **Database switching** — manage multiple databases from the header dropdown
- **Batch operations** — "Enrich All" and "Score Unscored" with progress bar, pause, cancel
- **Inline editing** — double-click any cell to edit
- **Prompt editor** — customize scoring prompts per database
- **Custom columns** — add text, number, boolean, or URL fields to the list view
- **Keyboard shortcuts** — press `?` for the full shortcut overlay
- **Live updates** — revision polling auto-refreshes when data changes from MCP or API calls

## MCP Server

The `scout-mcp` entry point exposes Scout as tools for Claude Desktop and other MCP clients.

### Workflows

**Fully autonomous:** `process_queue(limit=20)` — enriches and scores entities in one call. Repeat until queue is empty.

**Step by step:** `get_work_queue()` → follow `recommended_action` for each item.

**Selective:** `batch_enrich(initiative_ids='1,2,3')` → `batch_score(initiative_ids='1,2,3')`

**Deep dive:** `discover_initiative(id)` → `enrich_initiative(id)` → `score_initiative_tool(id)`

**Analytics:** `get_stats()` → `list_initiatives(verdict='reach_out_now', fields='id,name,uni,score')`

### Mutation Safeguards

- Delete operations require `confirm=True` (dry-run without it)
- Name changes produce warnings showing old → new values
- MCP instructions warn against accidental renames/deletes

## REST API

Full API documentation is available at `http://127.0.0.1:8001/docs` (Swagger UI) when the server is running. Key endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/entities` | List with filters, sorting, pagination, compact mode |
| `GET` | `/api/entities/{id}` | Full detail with enrichments and scores |
| `PUT` | `/api/entities/{id}` | Update entity fields |
| `POST` | `/api/enrich/{id}` | Enrich single entity |
| `POST` | `/api/score/{id}` | Score single entity |
| `GET` | `/api/stats` | Aggregate statistics |
| `POST` | `/api/import` | Upload XLSX spreadsheet |
| `GET` | `/api/export` | Download filtered XLSX |

Backward-compatible aliases: `/api/initiatives` → `/api/entities`.

## LLM Providers

| Provider | Env Vars | Install | Default Model |
|----------|----------|---------|---------------|
| OpenAI | `OPENAI_API_KEY` | `scout[openai]` | `gpt-5-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `scout[anthropic]` | `claude-haiku-4-5-20251001` |
| OpenAI-compatible | `OPENAI_API_KEY` + `OPENAI_BASE_URL`, `LLM_PROVIDER=openai_compatible` | `scout[openai]` | — |
| Gemini | `GOOGLE_API_KEY`, `LLM_PROVIDER=gemini` | `scout[openai]` | `gemini-2.0-flash-lite` |

Set `LLM_PROVIDER` and `LLM_MODEL` to override defaults. These can be set in `.mcp.json` under `mcpServers.scout.env`.

## Project Structure

```
initiativescout/
├── pyproject.toml            # Package config & dependencies
├── scout/
│   ├── app.py                # FastAPI web server + REST API
│   ├── mcp_server.py         # MCP server (Claude Desktop, Cursor, etc.)
│   ├── services.py           # Shared business logic
│   ├── scorer.py             # LLM scoring + deterministic aggregation
│   ├── schema.py             # Entity type schema definitions
│   ├── models.py             # SQLAlchemy ORM models
│   ├── db.py                 # Multi-DB SQLite, FTS5, migrations
│   ├── enricher/             # Web enrichment pipeline
│   │   ├── _core.py          #   HTTP client, caching, parsing
│   │   ├── _website.py       #   Website, team page, careers
│   │   ├── _github.py        #   GitHub org/repo analysis
│   │   ├── _metadata.py      #   Structured data, tech stack, DNS
│   │   ├── _discovery.py     #   DuckDuckGo URL discovery
│   │   └── _apis.py          #   OpenAlex, Wikidata enrichers
│   ├── embedder.py           # Dense embeddings (model2vec)
│   ├── importer.py           # XLSX import
│   ├── exporter.py           # XLSX export
│   ├── scrapers.py           # TUM professor scraper
│   ├── prompts/              # Default scoring prompts per entity type
│   └── static/               # Web UI (HTML + CSS + JS, no build step)
└── .github/workflows/ci.yml  # CI: Python 3.11/3.12/3.13
```

## Development

```bash
git clone https://github.com/BaNburger/initiativescout.git
cd initiativescout
pip install -e '.[dev,all]'
python -m pytest scout/tests/ -x -q    # 253 tests
```

## Troubleshooting

**Port already in use:** `scout --port 9000`

**API key not configured:** Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (see LLM Providers above).

**Missing optional dependency:** If you see `ImportError` mentioning `scout[...]`, install the missing extra: `pip install 'scout[name]'`

## License

MIT — see [LICENSE](LICENSE).
