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
3. **Score** — Three parallel LLM calls evaluate Team, Tech, and Opportunity dimensions. Verdict and score are computed deterministically from the average grade.
4. **Browse** — Filter, sort, and inspect initiatives in the UI. Keyboard navigation with arrow keys. Inline editing via double-click.

## Scoring Architecture

Each initiative is scored on three dimensions in parallel:

| Dimension | Data Sources | LLM Evaluates |
|-----------|-------------|---------------|
| **Team** | Team page enrichment, LinkedIn, member count/roles, team size, competitions | Team composition, leadership, execution capability |
| **Tech** | GitHub enrichment, repo count/contributors/commits, HuggingFace/OpenAlex/Semantic Scholar hits | Technical depth, code quality, research output, novelty |
| **Opportunity** | Full dossier (all enrichments + all signals) | Market size, timing, competitive landscape, commercial intent |

Each dimension returns a school grade (A+ through D, where A+=1.0, D=4.0) and reasoning.

**Deterministic aggregation:**

| Average Grade | Verdict |
|--------------|---------|
| ≤ 1.7 | `reach_out_now` |
| ≤ 2.7 | `reach_out_soon` |
| ≤ 3.3 | `monitor` |
| > 3.3 | `skip` |

**Score** = `round(5.0 - avg_grade)` snapped to half-points (higher = better).

The Opportunity dimension also provides: classification, contact recommendation, and engagement hook.

### Customizing Prompts

Scoring prompts are stored in the database and editable via the "Prompts" button in the UI or the API:

- `GET /api/scoring-prompts` — list all 3 prompts
- `PUT /api/scoring-prompts/{key}` — update prompt content (key: `team`, `tech`, `opportunity`)

Default prompts are seeded on first run and can be freely modified per database.

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
| `POST` | `/api/score/{id}` | Score single initiative (3 parallel LLM calls) |
| `POST` | `/api/score/batch` | Score all (SSE progress stream) |
| `POST` | `/api/projects/{id}/score` | Score a project via LLM |
| `GET` | `/api/scoring-prompts` | List scoring prompt definitions |
| `PUT` | `/api/scoring-prompts/{key}` | Update a scoring prompt |
| `GET` | `/api/stats` | Counts by verdict, classification, uni |
| `GET` | `/api/databases` | List available databases |
| `POST` | `/api/databases/select` | Switch database |
| `POST` | `/api/databases/create` | Create new database |
| `GET` | `/api/custom-columns` | List custom column definitions |
| `POST` | `/api/custom-columns` | Add custom column |
| `PUT` | `/api/custom-columns/{id}` | Update custom column |
| `DELETE` | `/api/custom-columns/{id}` | Remove custom column |
| `POST` | `/api/import` | Upload `.xlsx` (multipart form) |
| `DELETE` | `/api/reset` | Wipe all data |

## MCP Server

The `scout-mcp` entry point runs an MCP server over stdio, exposing Scout's functionality as tools for Claude Desktop and other MCP clients.

**Available tools:**

| Tool | Description |
|------|-------------|
| `list_initiatives` | Browse and filter initiatives with verdict, uni, classification, search filters |
| `get_initiative` | Full details with enrichments, projects, and scores |
| `update_initiative` | Update initiative fields (partial update) |
| `enrich_initiative` | Fetch fresh web/GitHub enrichment data |
| `score_initiative_tool` | Score 3 dimensions in parallel, aggregate deterministically |
| `get_stats` | Summary statistics and breakdowns |
| `create_project` | Add a sub-project to an initiative |
| `update_project` | Update project fields |
| `delete_project` | Remove a project and its scores |
| `score_project_tool` | Score a project in context of its parent initiative |
| `list_scoring_prompts` | View the 3 dimension prompt definitions |
| `update_scoring_prompt` | Customize a dimension's LLM system prompt |
| `list_scout_databases` | List available databases |
| `select_scout_database` | Switch to a different database |
| `get_custom_columns` | List custom column definitions |

## Project Structure

```
UnicornInitiative/
├── scout/                   # FastAPI web app + MCP server
│   ├── app.py               #   Routes & API endpoints
│   ├── mcp_server.py        #   MCP server (Claude Desktop integration)
│   ├── services.py          #   Shared business logic
│   ├── models.py            #   SQLAlchemy ORM models
│   ├── schemas.py           #   Pydantic request/response schemas
│   ├── db.py                #   Multi-DB SQLite management
│   ├── importer.py          #   XLSX parser (Spin-Off, All Initiatives, Overview)
│   ├── enricher.py          #   Website, team page, GitHub enrichment
│   ├── scorer.py            #   3-dimension LLM scoring + deterministic aggregation
│   ├── static/
│   │   ├── index.html       #   Page structure
│   │   ├── style.css        #   Styles
│   │   └── app.js           #   Frontend logic
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
