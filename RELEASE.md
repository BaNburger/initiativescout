# Scout v1.0.0

**Outreach intelligence for Munich student initiatives.**

Scout helps student organizations discover, evaluate, and prioritize outreach to other initiatives. It combines automated web enrichment with LLM-based scoring across three dimensions — team, technology, and opportunity — to produce actionable outreach recommendations.

## Highlights

- **MCP-first architecture** — designed for use with Claude, Cursor, and other AI assistants via the Model Context Protocol. Every feature is a tool call.
- **Web UI** — full-featured browser interface at `http://127.0.0.1:8001` with live updates, batch operations, keyboard navigation, and XLSX import/export.
- **3-dimension LLM scoring** — parallel evaluation of team strength, technical depth, and collaboration opportunity. Configurable prompts. Supports OpenAI and Anthropic providers.
- **Automated enrichment** — scrapes websites, GitHub orgs, LinkedIn, team pages, and extra links. Optional deep discovery via DuckDuckGo.
- **Semantic search** — dense embeddings (model2vec, local, no API calls) for similarity queries and thematic clustering.
- **Mutation safeguards** — delete operations require explicit confirmation, name changes produce warnings, data safety instructions baked into MCP context.
- **XLSX import/export** — bulk data loading from spreadsheets, styled exports with verdict-colored cells and auto-filters.
- **Multi-database** — switch between isolated datasets from the UI or MCP.
- **Zero config** — `pipx install` and go. SQLite, no external services, no authentication.

## Install

```bash
pipx install "scout[openai] @ git+https://github.com/BaNburger/initiativescout.git"
```

Optional extras: `anthropic` (Anthropic LLM provider), `crawl` (Crawl4AI + DuckDuckGo discovery).

## Quick start

```bash
export OPENAI_API_KEY=sk-...    # or ANTHROPIC_API_KEY
scout                           # web UI → http://127.0.0.1:8001
scout-mcp                      # MCP server for Claude/Cursor
scout-setup                    # auto-configure MCP client
```

## Architecture

- **LLM-as-a-user** — the MCP server is the primary interface; the web UI is a companion, not the other way around.
- **Lean** — ~2,500 lines of Python, ~1,700 lines of vanilla JS. No frameworks, no build step.
- **SQLite** — single-file database with WAL mode, FTS5 full-text search, and trigger-based revision tracking for live UI updates.
- **Deterministic scoring** — given the same enrichment data and LLM responses, the verdict and score are always reproducible.

## License

MIT
