# Implementation Plan
## Munich Student Initiatives Tracker - Claude Code Execution

**Version:** 1.0
**Date:** 2026-02-02
**Target:** Build complete system using Claude Code

---

## Overview

This document provides a step-by-step implementation plan optimized for execution with Claude Code. Each phase is broken into atomic tasks that can be completed in single Claude Code sessions.

---

## Project Structure

```
munich-initiatives-tracker/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app entry point
│   │   ├── config.py            # Configuration management
│   │   ├── database.py          # Database setup
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── initiative.py
│   │   │   ├── score.py
│   │   │   ├── news.py
│   │   │   └── user.py
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   └── initiative.py
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── routes/
│   │   │   │   ├── initiatives.py
│   │   │   │   ├── scores.py
│   │   │   │   ├── news.py
│   │   │   │   └── export.py
│   │   │   └── deps.py
│   │   ├── collectors/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── github.py
│   │   │   ├── web_scraper.py
│   │   │   └── news.py
│   │   ├── scoring/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py
│   │   │   └── dimensions.py
│   │   └── export/
│   │       ├── __init__.py
│   │       ├── markdown.py
│   │       └── csv_export.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_api.py
│   │   ├── test_collectors.py
│   │   └── test_scoring.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic/                 # Database migrations
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── Layout/
│   │   │   ├── InitiativeList/
│   │   │   ├── InitiativeDetail/
│   │   │   ├── ScoreChart/
│   │   │   └── common/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Initiatives.tsx
│   │   │   ├── InitiativeDetail.tsx
│   │   │   └── Admin.tsx
│   │   ├── hooks/
│   │   ├── api/
│   │   ├── store/
│   │   └── types/
│   ├── package.json
│   ├── tailwind.config.js
│   ├── vite.config.ts
│   └── Dockerfile
├── data/
│   ├── seed/
│   │   └── initial_data.json    # Our researched data
│   └── exports/
├── docker-compose.yml
├── .env.example
├── README.md
└── Makefile
```

---

## Phase 1: Foundation (Week 1)

### Session 1.1: Project Setup
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Create a new Python FastAPI project for tracking Munich student initiatives.

Set up:
1. Project structure as specified in implementation_plan.md
2. requirements.txt with: fastapi, uvicorn, sqlalchemy, pydantic, python-dotenv
3. Basic FastAPI app with health check endpoint
4. Configuration management with pydantic-settings
5. SQLite database connection with SQLAlchemy

Make sure the project can run with `uvicorn app.main:app --reload`
```

**Deliverables:**
- [ ] Project folder structure created
- [ ] requirements.txt with dependencies
- [ ] Basic FastAPI app running
- [ ] Config management working
- [ ] Health check endpoint at /health

---

### Session 1.2: Database Models
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
In the munich-initiatives-tracker project, create SQLAlchemy models:

1. University model (id, name, short_code, website_url)
2. Initiative model with all fields from technical_requirements.md
3. Score model with all dimensions and composite score
4. NewsArticle model
5. Achievement model
6. GitHubMetrics model

Include:
- Proper relationships between models
- Created/updated timestamps
- Indexes for common queries
- A script to create all tables
```

**Deliverables:**
- [ ] All models defined in models/
- [ ] Relationships properly configured
- [ ] Database tables can be created
- [ ] Test data insertion works

---

### Session 1.3: Pydantic Schemas
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Create Pydantic schemas for the API:

1. InitiativeCreate, InitiativeUpdate, InitiativeResponse
2. ScoreCreate, ScoreResponse, ScoreHistory
3. NewsArticleResponse
4. AchievementCreate, AchievementResponse

Include:
- Proper validation rules
- Example values in schema
- Nested schemas where appropriate
```

**Deliverables:**
- [ ] All schemas in schemas/
- [ ] Validation rules working
- [ ] OpenAPI docs show examples

---

### Session 1.4: Basic CRUD API
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Create REST API endpoints for initiatives:

1. GET /api/v1/initiatives - List all (with pagination, filtering)
2. GET /api/v1/initiatives/{id} - Get single
3. POST /api/v1/initiatives - Create new
4. PUT /api/v1/initiatives/{id} - Update
5. DELETE /api/v1/initiatives/{id} - Delete (soft delete)

Include:
- Query parameters for filtering (university, tier, search)
- Sorting options
- Proper error handling
- Dependency injection pattern
```

**Deliverables:**
- [ ] All CRUD endpoints working
- [ ] Filtering and pagination
- [ ] Error responses proper JSON
- [ ] API docs at /docs

---

### Session 1.5: Seed Data Import
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Create a data seeding script:

1. Convert the munich_student_initiatives_database.md to JSON
2. Create a seed script that imports all 25 initiatives
3. Include universities, initiatives, initial scores
4. Make it idempotent (can run multiple times safely)

The JSON should match our Pydantic schemas.
```

**Deliverables:**
- [ ] initial_data.json created
- [ ] Seed script working
- [ ] All 25 initiatives imported
- [ ] Initial scores calculated

---

## Phase 2: Scoring Engine (Week 2)

### Session 2.1: Scoring Dimensions
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Implement the scoring dimension calculators:

1. TechDepthCalculator - based on GitHub metrics, tech keywords
2. TalentCalculator - based on team size, achievements, competition wins
3. ApplicabilityCalculator - based on market fit indicators
4. MaturityCalculator - based on years active, consistency

Each calculator should:
- Take an Initiative object
- Return a score 1-5 with explanation
- Be configurable via weights
```

**Deliverables:**
- [ ] All 4 dimension calculators
- [ ] Unit tests for each
- [ ] Configurable weights
- [ ] Score explanations generated

---

### Session 2.2: Scoring Engine
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Create the main scoring engine:

1. Combines all dimension scores
2. Calculates composite score with weights
3. Assigns tier (1, 2, or 3)
4. Stores score history
5. Provides score diff from previous

Include batch scoring for all initiatives.
```

**Deliverables:**
- [ ] ScoringEngine class
- [ ] Composite calculation
- [ ] Tier assignment
- [ ] History tracking

---

### Session 2.3: Score API Endpoints
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Add API endpoints for scores:

1. GET /api/v1/scores - All current scores
2. GET /api/v1/scores/{initiative_id}/history - Score history
3. POST /api/v1/scores/{initiative_id}/recalculate - Trigger recalc
4. PUT /api/v1/scores/{initiative_id}/override - Manual override

Include proper authorization checks for override.
```

**Deliverables:**
- [ ] Score endpoints working
- [ ] History includes all past scores
- [ ] Recalculate triggers scoring engine
- [ ] Override requires justification

---

## Phase 3: Data Collectors (Week 3)

### Session 3.1: GitHub Collector
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Implement GitHub data collector:

1. BaseCollector abstract class
2. GitHubCollector implementation using GitHub API
3. Collects: repos, stars, forks, commits, contributors, languages
4. Handles rate limiting and errors gracefully
5. Stores metrics in GitHubMetrics table

Use httpx for async requests. Handle pagination.
```

**Deliverables:**
- [ ] GitHubCollector class
- [ ] API authentication working
- [ ] Rate limiting handled
- [ ] Metrics stored correctly

---

### Session 3.2: Web Scraper
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Implement basic web scraper for initiative websites:

1. WebScraperCollector class
2. Extracts: team size mentions, technology keywords, achievements
3. Uses BeautifulSoup for parsing
4. Respects robots.txt
5. Has configurable selectors per site

Focus on reliability over comprehensiveness.
```

**Deliverables:**
- [ ] WebScraperCollector class
- [ ] Basic info extraction working
- [ ] Error handling for down sites
- [ ] robots.txt respected

---

### Session 3.3: News Aggregator
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Implement news aggregation:

1. NewsCollector class
2. Supports RSS feeds
3. Google News API integration (or NewsAPI.org)
4. Matches articles to initiatives by keyword
5. Basic sentiment detection (positive/neutral/negative)

Store in NewsArticle table.
```

**Deliverables:**
- [ ] NewsCollector class
- [ ] RSS parsing working
- [ ] Article-initiative matching
- [ ] Sentiment tags assigned

---

### Session 3.4: Collection Orchestrator
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Create collection orchestration:

1. CollectionOrchestrator class
2. Runs all collectors in sequence
3. Tracks success/failure per source
4. Triggers score recalculation after collection
5. CLI command to run: `python -m app.collectors.run`

Add scheduling support with APScheduler.
```

**Deliverables:**
- [ ] Orchestrator running all collectors
- [ ] CLI command works
- [ ] Status tracking
- [ ] Scheduler configured

---

## Phase 4: Frontend - React Dashboard (Week 4)

### Session 4.1: React Project Setup
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Create React frontend with Vite:

1. Vite + React + TypeScript setup
2. Tailwind CSS configuration
3. React Router for navigation
4. Basic layout with sidebar navigation
5. API client setup with React Query

Pages: Dashboard, Initiatives, Admin (placeholder)
```

**Deliverables:**
- [ ] Vite project running
- [ ] Tailwind configured
- [ ] Router with 3 routes
- [ ] Basic layout component

---

### Session 4.2: Initiative List Component
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Create InitiativeList component:

1. Table displaying all initiatives
2. Columns: Name, University, Tier, Score, Updated
3. Sortable by clicking headers
4. Filter dropdowns (University, Tier)
5. Search input
6. Loading and error states
7. Click row to navigate to detail

Use React Query for data fetching.
```

**Deliverables:**
- [ ] InitiativeList component
- [ ] Sorting working
- [ ] Filters working
- [ ] Search working
- [ ] Navigation to detail

---

### Session 4.3: Initiative Detail Page
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Create InitiativeDetail page:

1. Full initiative information display
2. Score breakdown with visual bars
3. Tier badge component
4. Technology tags
5. Achievements timeline
6. External links section
7. Edit button (shows form)
8. Back navigation

Include loading skeleton.
```

**Deliverables:**
- [ ] Detail page complete
- [ ] Score visualization
- [ ] Achievements list
- [ ] Edit mode toggle

---

### Session 4.4: Score Charts
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Create score visualization components:

1. ScoreRadarChart - radar chart of 4 dimensions
2. ScoreHistoryChart - line chart over time
3. TierBadge - colored badge component
4. ScoreBar - horizontal bar with color

Use Recharts library.
```

**Deliverables:**
- [ ] Radar chart component
- [ ] History chart component
- [ ] Tier badge styled
- [ ] Score bars working

---

### Session 4.5: Dashboard Overview
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Create Dashboard page:

1. Summary cards (total initiatives, by tier, by university)
2. Top performers list (top 5 by score)
3. Recent updates feed (last 10 changes)
4. Quick filters to jump to initiative list
5. Mini chart of tier distribution

Make it the landing page.
```

**Deliverables:**
- [ ] Dashboard with stats
- [ ] Top performers section
- [ ] Recent updates
- [ ] Distribution chart

---

## Phase 5: Export & Integration (Week 5)

### Session 5.1: Markdown Export
**Estimated Time:** 45 min
**Claude Code Prompt:**
```
Implement markdown export:

1. MarkdownExporter class
2. Generates one .md file per initiative
3. YAML frontmatter with structured data
4. Index file with table of all initiatives
5. API endpoint: GET /api/v1/export/markdown (returns ZIP)

Match format for Obsidian compatibility.
```

**Deliverables:**
- [ ] Markdown generation
- [ ] YAML frontmatter correct
- [ ] Index file generated
- [ ] ZIP download working

---

### Session 5.2: CSV Export
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Implement CSV export:

1. CSVExporter class
2. Exports all initiatives or filtered
3. Includes all key fields and scores
4. API endpoint: GET /api/v1/export/csv
5. Frontend button to trigger download
```

**Deliverables:**
- [ ] CSV generation
- [ ] All fields included
- [ ] Download working
- [ ] Frontend button added

---

### Session 5.3: Obsidian Sync
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Add Obsidian vault sync:

1. ObsidianSync class
2. Writes markdown files to configured path
3. Preserves manual notes section
4. Bi-directional: reads manual updates
5. API endpoint: POST /api/v1/sync/obsidian
```

**Deliverables:**
- [ ] File writing working
- [ ] Notes preserved
- [ ] Manual edits read back
- [ ] Sync endpoint

---

### Session 5.4: Things Integration
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Add Things task creation:

1. ThingsIntegration class using URL scheme
2. Creates tasks from alerts
3. Task includes: Title, Notes with link, Project
4. API endpoint: POST /api/v1/integrations/things/task
5. Configuration in settings
```

**Deliverables:**
- [ ] URL scheme generation
- [ ] Task creation tested
- [ ] Alert triggers task
- [ ] Configuration working

---

## Phase 6: Polish & Deploy (Week 6)

### Session 6.1: Admin Panel
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Create admin panel in frontend:

1. System status dashboard
2. Manual data entry form
3. Score override form
4. Collection job status
5. Basic user list (read-only for now)

Protect with simple auth check.
```

**Deliverables:**
- [ ] Admin page complete
- [ ] Data entry working
- [ ] Override working
- [ ] Status display

---

### Session 6.2: Docker Setup
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Dockerize the application:

1. Backend Dockerfile (Python)
2. Frontend Dockerfile (nginx)
3. docker-compose.yml with both services
4. Environment variable configuration
5. Volume mounts for data persistence

Include README instructions.
```

**Deliverables:**
- [ ] Both Dockerfiles
- [ ] docker-compose working
- [ ] Env vars configured
- [ ] README updated

---

### Session 6.3: Testing
**Estimated Time:** 60 min
**Claude Code Prompt:**
```
Add comprehensive tests:

1. API endpoint tests with pytest
2. Scoring engine unit tests
3. Collector mock tests
4. Frontend component tests with Vitest
5. GitHub Actions CI workflow

Target 80% coverage on critical paths.
```

**Deliverables:**
- [ ] pytest tests passing
- [ ] Frontend tests passing
- [ ] CI workflow running
- [ ] Coverage report

---

### Session 6.4: Documentation
**Estimated Time:** 30 min
**Claude Code Prompt:**
```
Complete documentation:

1. README.md with setup instructions
2. API documentation (auto-generated from OpenAPI)
3. Configuration reference
4. Deployment guide
5. Contributing guidelines
```

**Deliverables:**
- [ ] README complete
- [ ] API docs accessible
- [ ] Config documented
- [ ] Deploy guide

---

## Claude Code Session Tips

### Effective Prompts

**Start sessions with context:**
```
I'm working on the munich-initiatives-tracker project.
Current state: [describe what's done]
Goal for this session: [specific deliverable]
```

**Reference documentation:**
```
Following the technical requirements in technical_requirements.md,
implement [specific feature].
```

**Incremental development:**
```
Let's add [feature] to the existing [file].
The current code is [brief description].
Add [specific functionality].
```

### Session Management

1. **One deliverable per session** - Keep sessions focused
2. **Test after each session** - Verify before moving on
3. **Commit frequently** - Git commit after each session
4. **Document decisions** - Note any deviations from plan

### Error Recovery

If a session fails:
1. Note what was attempted
2. Identify the blocker
3. Start fresh session with explicit context
4. Simplify if needed

---

## Milestone Checkpoints

### Week 1 End: Foundation Complete
- [ ] API running with CRUD
- [ ] All 25 initiatives seeded
- [ ] Basic scoring working

### Week 2 End: Scoring Complete
- [ ] All dimensions calculated
- [ ] History tracking
- [ ] Score API working

### Week 3 End: Collectors Complete
- [ ] GitHub data fetching
- [ ] News aggregation
- [ ] Automated collection running

### Week 4 End: Frontend MVP
- [ ] Dashboard showing data
- [ ] List with filters
- [ ] Detail pages
- [ ] Charts working

### Week 5 End: Integration
- [ ] Export to Markdown/CSV
- [ ] Obsidian sync
- [ ] Things integration

### Week 6 End: Production Ready
- [ ] Docker deployment
- [ ] Tests passing
- [ ] Documentation complete
- [ ] Admin panel working

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Claude Code context limits | Break into smaller sessions |
| API rate limits during dev | Use mock data initially |
| Frontend complexity | Start with minimal UI, iterate |
| Integration failures | Build adapters with interfaces |

---

## Success Criteria

The system is complete when:
1. All 25 initiatives visible in dashboard
2. Scores calculated and displayed
3. Data refreshes automatically
4. Export to Obsidian works
5. Can run via Docker
6. Tests pass

---

*Plan maintained by UnicornInitiative Team*
