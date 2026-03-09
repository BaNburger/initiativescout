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

## Phase 4: API Connectors + Credentials (future)

- Credential model (name, service, encrypted_value)
- `ctx.secret("name")` in scripts
- OAuth2 / API key / Bearer token support
- Bidirectional CRM sync patterns

## Phase 5: Tool Consolidation (future)

- Merge 25+ MCP tools to ~8 core tools
- entity(), overview(), script(), run(), score(), dossier(), enrich(), prompt()
- Massively reduced context window usage

## Non-goals (current)

- Credential management (use env vars for now — ctx.env("KEY"))
- Subprocess sandboxing (in-process is fine for LLM-as-user)
- Multi-tenant security (single-user tool)
- Script/prompt versioning (just overwrite on save)
