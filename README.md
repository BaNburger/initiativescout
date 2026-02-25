# Scout — Outreach Intelligence

Web app and MCP server for discovering, enriching, and scoring Munich student initiatives for outreach. Import spreadsheet data, enrich with live web/GitHub signals, and get LLM-powered verdicts on which initiatives to contact.

## Quickstart

```bash
cd scout
pip install -e .
scout            # web UI on http://127.0.0.1:8001
scout-mcp        # MCP server over stdio (for Claude Desktop / MCP clients)
```

Open the browser and import an `.xlsx` spreadsheet (see `output/spreadsheet/`).

## How It Works

1. **Import** — Upload the enriched XLSX. Supports three sheet types: Spin-Off Targets, All Initiatives, and the Initiatives overview sheet. Deduplicates by name+uni.
2. **Enrich** — Fetches live data from initiative websites, team pages, and GitHub orgs.
3. **Score** — LLM-based scoring produces a verdict (`reach_out_now`, `reach_out_soon`, `monitor`, `skip`), classification, dimension grades, reasoning, and engagement hooks.
4. **Browse** — Filter, sort, and inspect initiatives in the UI. Keyboard navigation with arrow keys. Inline editing via double-click.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/initiatives` | List with filters (`verdict`, `uni`, `classification`, `search`) and pagination |
| `GET` | `/api/initiatives/{id}` | Full detail with enrichments, projects, and scores |
| `PUT` | `/api/initiatives/{id}` | Update initiative fields (partial update) |
| `GET` | `/api/initiatives/{id}/projects` | List projects for an initiative |
| `POST` | `/api/initiatives/{id}/projects` | Create a new project |
| `PUT` | `/api/projects/{id}` | Update project fields |
| `DELETE` | `/api/projects/{id}` | Delete a project and its scores |
| `POST` | `/api/enrich/{id}` | Enrich single initiative |
| `POST` | `/api/enrich/batch` | Enrich all (SSE progress stream) |
| `POST` | `/api/score/{id}` | Score single initiative |
| `POST` | `/api/score/batch` | Score all (SSE progress stream) |
| `POST` | `/api/projects/{id}/score` | Score a project via LLM |
| `GET` | `/api/stats` | Counts by verdict, classification, uni |
| `POST` | `/api/import` | Upload `.xlsx` (multipart form) |
| `DELETE` | `/api/reset` | Wipe all data |

## MCP Server

The `scout-mcp` entry point runs an MCP server over stdio, exposing Scout's functionality as tools for Claude Desktop and other MCP clients.

Available tools: `list_initiatives`, `get_initiative`, `update_initiative`, `enrich_initiative`, `score_initiative_tool`, `get_stats`, `create_project`, `update_project`, `delete_project`, `score_project_tool`.

## Project Structure

```
UnicornInitiative/
├── scout/                   # FastAPI web app + MCP server
│   ├── app.py               #   Routes & API endpoints
│   ├── mcp_server.py        #   MCP server (Claude Desktop integration)
│   ├── services.py          #   Shared business logic
│   ├── models.py            #   SQLAlchemy models + Pydantic schemas
│   ├── db.py                #   SQLite session management
│   ├── importer.py          #   XLSX parser (Spin-Off, All Initiatives, Overview)
│   ├── enricher.py          #   Website, team page, GitHub enrichment
│   ├── scorer.py            #   LLM-based scoring (Anthropic / OpenAI)
│   ├── static/index.html    #   Single-page UI
│   └── pyproject.toml       #   Package config & dependencies
├── output/spreadsheet/      # Source spreadsheets for import
├── archive/                 # Retired CLI tool (initiative-tracker)
└── .gitignore
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (default provider) | Anthropic API key for LLM scoring |
| `GITHUB_TOKEN` | No | Increases GitHub API rate limits during enrichment |
| `LLM_PROVIDER` | No | `anthropic` (default) or `openai` / `openai_compatible` |
| `LLM_MODEL` | No | Override model name (default: `claude-haiku-4-5-20251001` or `gpt-4o-mini`) |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API key |
| `OPENAI_BASE_URL` | No | Custom OpenAI-compatible endpoint |
