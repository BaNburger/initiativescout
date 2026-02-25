# Munich Student Initiatives — Venture Scout CLI

CLI pipeline to find, score, rank, and reach out to Munich student initiatives with exceptional promise in **Tech · Opportunity · Team**.

## Quickstart

```bash
# Install
./scripts/install_app.sh

# Full pipeline (scrape → enrich → score → rank → export)
initiative-tracker run-all --top-n 15

# Investment-grade due diligence
initiative-tracker run-dd --top-n 15

# Open interactive dashboard
initiative-tracker view-results --mode html --lens outreach --top-n 15 --open
```

## Pipeline Steps

```bash
initiative-tracker init-db               # Create/reset database
initiative-tracker seed-from-markdown     # Import curated initiative list
initiative-tracker scrape-directories     # Pull from TUM/LMU/HM directories
initiative-tracker enrich-websites        # Crawl initiative websites
initiative-tracker ingest-people          # Discover team members
initiative-tracker score                  # Score on Tech/Opportunity/Team
initiative-tracker rank --top-n 15        # Rank & shortlist
initiative-tracker export --top-n 15      # Export JSON/CSV rankings
```

Or all at once: `initiative-tracker run-all --top-n 15`

## Key Commands

### Explore & Explain
```bash
initiative-tracker explain --initiative-name "TUM Boring"
initiative-tracker shortlist --lens outreach --top-n 15
initiative-tracker shortlist --lens upside --top-n 15
initiative-tracker talent --type operators --top-n 25
initiative-tracker talent --type alumni --top-n 25
```

### Due Diligence
```bash
initiative-tracker collect-github --all
initiative-tracker collect-dd-public --all
initiative-tracker import-dd-manual --file path/to/manual_dd.csv
initiative-tracker dd-gate --all
initiative-tracker dd-score --all
initiative-tracker dd-rank --top-n 15
initiative-tracker dd-report --top-n 15
initiative-tracker dd-explain --initiative-id 12
```

### Outreach Tracking
```bash
initiative-tracker set-status \
  --initiative-id 3 --status contacted \
  --owner "Scout A" --next-step-date 2026-02-20 \
  --note "Reached out by email"
```

### Output Formats
```bash
initiative-tracker view-results --mode cli --lens outreach --top-n 15
initiative-tracker view-results --mode html --lens outreach --top-n 15 --open
initiative-tracker --json shortlist --lens outreach --top-n 10
```

## Project Structure

```
UnicornInitiative/
├── initiative_tracker/          # Python package
│   ├── cli.py                   #   CLI entry point (Typer)
│   ├── models.py                #   SQLAlchemy / Pydantic models
│   ├── db.py                    #   Database layer
│   ├── store.py                 #   Data store operations
│   ├── config.py                #   Config loader
│   ├── types.py                 #   Shared types
│   ├── utils.py                 #   Helpers
│   ├── results_view.py          #   HTML/CLI result renderer
│   ├── pipeline/                #   Pipeline stages
│   │   ├── seed_markdown.py     #     Import from curated .md
│   │   ├── scrape_directories.py#     TUM/LMU/HM directory scrapers
│   │   ├── enrich_websites.py   #     Website crawling & enrichment
│   │   ├── ingest_people.py     #     Team member discovery
│   │   ├── score.py             #     Scoring engine
│   │   ├── rank.py              #     Ranking & shortlisting
│   │   ├── export.py            #     JSON/CSV export
│   │   ├── dossiers.py          #     Initiative dossier builder
│   │   ├── collect_github.py    #     GitHub signal collection
│   │   ├── collect_dd_public.py #     Public DD data collection
│   │   ├── import_dd_manual.py  #     Manual DD data import
│   │   ├── dd_score.py          #     DD scoring engine
│   │   ├── dd_gate.py           #     DD gate checks
│   │   ├── dd_rank.py           #     DD ranking
│   │   ├── dd_report.py         #     DD report generation
│   │   ├── dd_common.py         #     Shared DD utilities
│   │   └── dd_source_audit.py   #     Source audit trail
│   ├── scoring/                 #   Score explainability
│   │   └── explainability.py
│   └── sources/                 #   Data source adapters
│       ├── tum.py / lmu.py / hm.py  # University scrapers
│       ├── website.py           #     Generic website scraper
│       ├── people_web.py        #     People discovery (web)
│       ├── people_markdown.py   #     People discovery (markdown)
│       ├── dd_external.py       #     External DD sources
│       └── common.py            #     Shared source utilities
├── config/                      # Scoring & taxonomy configs
│   ├── scoring_weights.yaml
│   ├── technology_taxonomy.yaml
│   ├── market_taxonomy.yaml
│   ├── technology_aliases.yaml
│   ├── market_aliases.yaml
│   ├── dd_rubric.yaml
│   └── dd_gate_thresholds.yaml
├── data/                        # Runtime data (git-ignored)
│   ├── initiatives.db           #   Main SQLite database
│   └── exports/                 #   JSON/CSV pipeline outputs
├── docs/                        # Project documentation
│   ├── strategy/                #   Planning & analysis
│   │   ├── implementation_plan.md
│   │   ├── strategy_gap_analysis.md
│   │   ├── business_model_analysis.md
│   │   ├── monitoring_strategy_update.md
│   │   └── due_diligence_upgrade_considerations.md
│   ├── research/                #   Initiative intel & contacts
│   │   ├── munich_student_initiatives_comprehensive.md
│   │   ├── munich_student_initiatives_database.md
│   │   ├── alumni_network.md
│   │   └── tier1_contacts.md
│   ├── specs/                   #   Technical specs
│   │   ├── technical_requirements.md
│   │   └── user_stories.md
│   └── obsidian/                #   Obsidian vault summaries
├── reports/                     # Generated reports
│   └── latest/
│       ├── results_dashboard.html
│       ├── venture_scout_brief.md
│       └── due_diligence_brief.md
├── assets/                      # Presentation & media
│   ├── Student_Initiatives_Partnership_Deck.pptx
│   └── create_presentation.js
├── scripts/                     # Install/uninstall helpers
├── tests/                       # Pytest suite
├── pyproject.toml
└── .gitignore
```

## Scoring

Scoring is deterministic and traceable to source snippets. Components without qualifying evidence floor at 1.0 with a confidence penalty. Seed ratings are capped at 40% influence per dimension. Private contact data is redacted by default unless `--include-private` is passed.

## Tests

```bash
.venv/bin/python -m pytest
```
