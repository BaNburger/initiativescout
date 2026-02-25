# Scout — Outreach Intelligence

Web app for discovering, enriching, and scoring Munich student initiatives for outreach. Import spreadsheet data, enrich with live web/GitHub signals, and get LLM-powered verdicts on which initiatives to contact.

## Quickstart

```bash
cd scout
pip install -e .
scout            # starts on http://127.0.0.1:8001
```

Open the browser and import an `.xlsx` spreadsheet (see `output/spreadsheet/`).

## How It Works

1. **Import** — Upload the enriched XLSX (Spin-Off Targets + All Initiatives sheets). Deduplicates by name+uni.
2. **Enrich** — Fetches live data from initiative websites, team pages, and GitHub orgs.
3. **Score** — LLM-based scoring produces a verdict (`reach_out_now`, `reach_out_soon`, `monitor`, `skip`), classification, reasoning, and engagement hooks.
4. **Browse** — Filter, sort, and inspect initiatives in the UI. Keyboard navigation with arrow keys.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/initiatives` | List with filters (`verdict`, `uni`, `classification`, `search`) |
| `GET` | `/api/initiatives/{id}` | Full detail view |
| `GET` | `/api/stats` | Counts by verdict, classification, uni |
| `POST` | `/api/import` | Upload `.xlsx` (multipart form) |
| `POST` | `/api/enrich/{id}` | Enrich single initiative |
| `POST` | `/api/enrich/batch` | Enrich all (SSE progress stream) |
| `POST` | `/api/score/{id}` | Score single initiative |
| `POST` | `/api/score/batch` | Score all (SSE progress stream) |
| `DELETE` | `/api/reset` | Wipe all data |

## Project Structure

```
UnicornInitiative/
├── scout/                   # FastAPI web app
│   ├── app.py               #   Routes & API
│   ├── models.py            #   SQLAlchemy models + Pydantic schemas
│   ├── db.py                #   SQLite session management
│   ├── importer.py          #   XLSX parser (Spin-Off + All Initiatives)
│   ├── enricher.py          #   Website, team page, GitHub enrichment
│   ├── scorer.py            #   LLM-based scoring (Anthropic API)
│   ├── static/index.html    #   Single-page UI
│   └── pyproject.toml       #   Package config & dependencies
├── output/spreadsheet/      # Source spreadsheets for import
├── archive/                 # Retired CLI tool (initiative-tracker)
└── .gitignore
```

## Environment Variables

- `ANTHROPIC_API_KEY` — Required for LLM scoring
- `GITHUB_TOKEN` — Optional, increases GitHub API rate limits during enrichment
