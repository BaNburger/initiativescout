# Scout v1.2.0 — Programmable Scout

## Vision

Transform Scout from a tool collection into a **programmable platform** where LLMs can
write, store, and execute scripts — offloading reasoning to classical code for reliability,
reach, and performance.

**Research backing:**
- CodeAct (ICML 2024): code-as-action-space yields 20% higher success, 30% fewer steps
- Anthropic Engineering: code execution with MCP achieves 98.7% token reduction
- MCP community: minimalist tool surface + rich data > many specialized tools

## Phase 1: Script Store + Execution [DONE]

Script table + execution engine. LLMs can save, list, read, execute, and delete Python scripts.

- `Script` model (name, code, description, script_type, entity_type)
- `scout/sdk.py` — ScriptContext with entity CRUD, HTTP, enrichment, logging
- `scout/executor.py` — compile+exec with import filtering, signal timeout
- MCP tools: `script(action=save/list/read/delete)`, `run_script(name, entity_id)`
- REST: GET/POST/DELETE `/api/scripts`, POST `/api/scripts/{name}/run`
- 32 tests in `test_scripts.py`

## Phase 2: Script Enrichers in Pipeline [DONE]

Script-type enrichers run automatically as part of `enrich_entity()`.

- Scripts with `script_type='enricher'` execute after built-in enrichers
- Respects `entity_type` filter on scripts
- SDK enhanced with read access:
  - `ctx.scores(entity_id)` — read entity scores
  - `ctx.enrichments(entity_id)` — read entity enrichments
  - `ctx.prompt("name")` — read stored prompts (general + scoring)

## Phase 3: Generalized Prompt Store [DONE]

Separate `Prompt` model for reusable prompt templates (not touching ScoringPrompt).

- `Prompt` model (name, content, description, prompt_type, entity_type)
- prompt_types: scoring, enrichment, analysis, classification, custom
- MCP tool: `prompt(action=save/list/read/delete)`
- REST: GET/POST/DELETE `/api/prompts`
- Scripts access via `ctx.prompt("name")` — checks Prompt table, falls back to ScoringPrompt

### What Phases 1-3 enable together

```python
# Script that uses a stored prompt to classify entities
p = ctx.prompt("classify_sector")
entities = ctx.entities(verdict="monitor", limit=50)
for e in entities:
    # LLM would call this script, review results, then update
    ctx.log(f"{e['name']}: needs classification")
ctx.result({"to_classify": len(entities)})
```

```python
# Script-enricher: auto-runs during enrich_entity()
entity = ctx.entity()
resp = ctx.http.get(f"https://api.crunchbase.com/v4/entities/{entity['name']}")
if resp.status_code == 200:
    data = resp.json()
    ctx.enrich(source_type="crunchbase", raw_text=resp.text,
               fields={"sector": data.get("category")})
```

## Phase 4: API Connectors + Credentials [DONE]

- `Credential` model (name, service, encrypted_value, description)
- Fernet encryption via `SCOUT_SECRET_KEY` env var (base64 fallback without cryptography)
- `ctx.secret("name")` in scripts — checks DB then env vars
- MCP tool: `credential(action=save/list/delete)`
- REST: GET/POST/DELETE `/api/credentials`
- 13 tests in `test_scripts.py`

## Phase 5: Tool Consolidation [DONE]

Consolidated 29 MCP tools down to 9 core tools (69% reduction):

| Tool | Merged from | Actions |
|------|------------|---------|
| `entity()` | list_entities, get_entity, manage_entity, export_entities, find_similar | list/get/create/bulk_create/update/delete/export/similar |
| `enrich()` | enrich_entity, submit_enrichment, process_queue | run/submit/process |
| `score()` | score_entity, submit_score, get_scoring_dossier | run/submit/dossier |
| `overview()` | get_overview, get_work_queue | detail, queue_limit params |
| `project()` | manage_project | create/update/delete/score |
| `configure()` | manage_database, custom column CRUD (4), show/configure_llm, embed_all, scrape | db_*/col_*/llm_*/embed/scrape |
| `script()` | script + run_script | save/list/read/delete/run |
| `prompt()` | prompt + scoring prompt tools | save/list/read/delete/scoring_list/scoring_update |
| `credential()` | (new in Phase 4) | save/list/delete |

- All 25 old tool names preserved as backward-compat aliases (function-level, not @mcp.tool)
- Sync helper functions for submit_enrichment, submit_score, get_scoring_dossier to avoid async boundary issues
- 315 tests passing

## Non-goals (current)

- Subprocess sandboxing (in-process is fine for LLM-as-user)
- Multi-tenant security (single-user tool)
- Script/prompt versioning (just overwrite on save)
