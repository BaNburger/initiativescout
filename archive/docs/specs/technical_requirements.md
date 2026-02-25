# Technical Requirements Document
## Munich Student Initiatives Tracker - Automation Script

**Version:** 1.0
**Date:** 2026-02-02
**Author:** UnicornInitiative Team

---

## 1. Executive Summary

This document specifies the technical requirements for an automated system to track, rate, and monitor Munich student initiatives for deep tech startup potential. The system will collect data from multiple sources, apply scoring algorithms, and present insights through a web-based dashboard.

---

## 2. System Overview

### 2.1 Purpose
Automate the collection, aggregation, rating, and monitoring of student initiatives across TUM, LMU, and HM to identify high-potential deep tech spinout candidates.

### 2.2 Scope
- Data collection from websites, GitHub, LinkedIn, and news sources
- Automated scoring and rating
- Change detection and alerting
- Web-based dashboard for visualization
- Export capabilities for reporting

### 2.3 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Data Collection Layer                        │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────┤
│  Web        │  GitHub     │  LinkedIn   │  News       │  Manual │
│  Scraper    │  API        │  Scraper    │  Aggregator │  Input  │
└──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┴────┬────┘
       │             │             │             │           │
       └─────────────┴─────────────┴─────────────┴───────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      Data Processing        │
                    │  - Normalization            │
                    │  - Deduplication            │
                    │  - Entity Matching          │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      Scoring Engine         │
                    │  - Tech Depth Score         │
                    │  - Talent Score             │
                    │  - Applicability Score      │
                    │  - Maturity Score           │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      Data Storage           │
                    │  - SQLite / PostgreSQL      │
                    │  - JSON exports             │
                    │  - Markdown sync            │
                    └──────────────┬──────────────┘
                                   │
       ┌───────────────────────────┼───────────────────────────┐
       │                           │                           │
┌──────▼──────┐          ┌─────────▼─────────┐       ┌─────────▼─────────┐
│  HTML/React │          │  API Endpoints    │       │  Export/Sync      │
│  Dashboard  │          │  (REST)           │       │  (Obsidian, etc)  │
└─────────────┘          └───────────────────┘       └───────────────────┘
```

---

## 3. Functional Requirements

### 3.1 Data Collection (FR-DC)

#### FR-DC-001: Web Scraping
- **Description:** Scrape initiative websites for basic information
- **Sources:** Initiative homepages, university listings, competition pages
- **Data Collected:** Name, description, contact info, team size, achievements
- **Frequency:** Weekly
- **Constraints:** Respect robots.txt, rate limiting (1 req/sec)

#### FR-DC-002: GitHub Integration
- **Description:** Collect repository metrics via GitHub API
- **Data Collected:**
  - Repository count
  - Star count, fork count
  - Commit frequency (last 90 days)
  - Language distribution
  - Contributors count
  - Last activity date
- **Frequency:** Daily
- **Authentication:** GitHub Personal Access Token (PAT)

#### FR-DC-003: LinkedIn Monitoring
- **Description:** Track LinkedIn company pages and posts
- **Data Collected:**
  - Follower count
  - Post frequency
  - Engagement metrics (where available)
  - Team size indicators
- **Frequency:** Weekly
- **Constraints:** Use official API where possible, respect TOS

#### FR-DC-004: News Aggregation
- **Description:** Monitor news mentions and press coverage
- **Sources:**
  - Google News API
  - University press releases (RSS)
  - Munich Startup (RSS)
  - Competition results pages
- **Data Collected:**
  - Article title, URL, date
  - Sentiment (positive/neutral/negative)
  - Mention context
- **Frequency:** Daily

#### FR-DC-005: Manual Data Entry
- **Description:** Allow manual addition/correction of data
- **Capabilities:**
  - Add new initiatives
  - Correct scraped data
  - Add qualitative notes
  - Override automated scores
- **Audit:** Track all manual changes with timestamp and user

### 3.2 Scoring Engine (FR-SE)

#### FR-SE-001: Tech Depth Score (1-5)
**Inputs:**
- GitHub metrics (complexity, activity, languages)
- Technology keywords in description
- Competition results (technical competitions)
- Patent/publication mentions
- Technology recency (cutting-edge vs mature)

**Algorithm:**
```python
tech_score = weighted_average([
    github_complexity_score * 0.25,
    technology_stack_score * 0.25,
    competition_results_score * 0.20,
    innovation_indicators_score * 0.20,
    activity_recency_score * 0.10
])
```

#### FR-SE-002: Talent Score (1-5)
**Inputs:**
- Team size
- Competition wins
- Alumni success (tracked separately)
- University reputation factor
- Leadership indicators

**Algorithm:**
```python
talent_score = weighted_average([
    team_size_score * 0.20,
    competition_success_score * 0.30,
    alumni_track_record * 0.25,
    university_factor * 0.15,
    growth_trajectory * 0.10
])
```

#### FR-SE-003: Market Applicability Score (1-5)
**Inputs:**
- Technology-market fit keywords
- B2B/B2C indicators
- Industry partnership mentions
- Market size indicators (external data)
- Commercial activity indicators

**Algorithm:**
```python
applicability_score = weighted_average([
    market_fit_score * 0.30,
    industry_partnerships * 0.25,
    commercial_indicators * 0.25,
    market_timing_score * 0.20
])
```

#### FR-SE-004: Maturity Score (1-5)
**Inputs:**
- Years active
- Achievement history
- Infrastructure (website quality, social presence)
- Consistency of activity
- Team stability indicators

**Algorithm:**
```python
maturity_score = weighted_average([
    years_active_score * 0.25,
    achievement_consistency * 0.25,
    infrastructure_quality * 0.20,
    activity_consistency * 0.20,
    team_stability * 0.10
])
```

#### FR-SE-005: Composite Spinout Potential
**Calculation:**
```python
spinout_potential = (
    tech_score * 0.30 +
    talent_score * 0.25 +
    applicability_score * 0.25 +
    maturity_score * 0.20
)

# Apply tier classification
if spinout_potential >= 4.0:
    tier = "Tier 1 - Immediate Potential"
elif spinout_potential >= 3.0:
    tier = "Tier 2 - Strong Potential"
else:
    tier = "Tier 3 - Developing"
```

### 3.3 Data Storage (FR-DS)

#### FR-DS-001: Primary Database
- **Type:** SQLite (development), PostgreSQL (production)
- **Schema:** See Section 5
- **Backup:** Daily automated backups
- **Retention:** Full history, no deletion

#### FR-DS-002: Export Formats
- **Markdown:** Sync to repository/Obsidian
- **JSON:** API responses
- **CSV:** Spreadsheet export
- **PDF:** Report generation

#### FR-DS-003: Version History
- Track all data changes
- Maintain score history over time
- Enable trend analysis

### 3.4 Web Dashboard (FR-WD)

#### FR-WD-001: Overview Dashboard
- Total initiatives count by university
- Score distribution charts
- Tier breakdown
- Recent updates feed

#### FR-WD-002: Initiative List View
- Sortable, filterable table
- Quick filters: University, Tier, Technology domain
- Search functionality
- Bulk actions (export, compare)

#### FR-WD-003: Initiative Detail View
- Full profile display
- Score breakdown with explanations
- Historical score trend
- Related news feed
- GitHub activity charts
- Manual notes section

#### FR-WD-004: Comparison View
- Side-by-side initiative comparison
- Radar chart for score dimensions
- Highlight differentiators

#### FR-WD-005: Alerts & Notifications
- New initiative detected
- Significant score change (>0.5)
- New competition win
- Major news mention

#### FR-WD-006: Admin Panel
- Manual data entry forms
- Score override capabilities
- User management
- System configuration

### 3.5 API Requirements (FR-API)

#### FR-API-001: REST Endpoints
```
GET  /api/v1/initiatives           # List all initiatives
GET  /api/v1/initiatives/{id}      # Get single initiative
POST /api/v1/initiatives           # Add new initiative (admin)
PUT  /api/v1/initiatives/{id}      # Update initiative (admin)

GET  /api/v1/scores                # Get all scores
GET  /api/v1/scores/{id}/history   # Score history

GET  /api/v1/news                  # News feed
GET  /api/v1/news/{initiative_id}  # News for initiative

GET  /api/v1/export/markdown       # Export as markdown
GET  /api/v1/export/csv            # Export as CSV
GET  /api/v1/export/json           # Export as JSON

POST /api/v1/sync/obsidian         # Trigger Obsidian sync
POST /api/v1/collect/trigger       # Trigger data collection
```

#### FR-API-002: Authentication
- API key authentication for external access
- Session-based auth for dashboard
- Role-based access (viewer, editor, admin)

---

## 4. Non-Functional Requirements

### 4.1 Performance (NFR-P)
- **NFR-P-001:** Dashboard page load < 2 seconds
- **NFR-P-002:** API response time < 500ms (95th percentile)
- **NFR-P-003:** Data collection cycle complete within 4 hours
- **NFR-P-004:** Support 50+ concurrent dashboard users

### 4.2 Reliability (NFR-R)
- **NFR-R-001:** 99% uptime for dashboard
- **NFR-R-002:** Graceful degradation if data sources unavailable
- **NFR-R-003:** Automatic retry with exponential backoff
- **NFR-R-004:** Data validation before storage

### 4.3 Security (NFR-S)
- **NFR-S-001:** HTTPS only
- **NFR-S-002:** API keys stored encrypted
- **NFR-S-003:** No PII storage beyond public information
- **NFR-S-004:** Audit logging for all admin actions

### 4.4 Maintainability (NFR-M)
- **NFR-M-001:** Modular architecture for easy source addition
- **NFR-M-002:** Configuration-driven scraper targets
- **NFR-M-003:** Comprehensive logging
- **NFR-M-004:** Unit test coverage > 80%

### 4.5 Scalability (NFR-SC)
- **NFR-SC-001:** Support 200+ initiatives without performance degradation
- **NFR-SC-002:** Horizontal scaling capability for collectors
- **NFR-SC-003:** Database indexing for common queries

---

## 5. Data Model

### 5.1 Entity-Relationship Diagram

```
┌─────────────────┐       ┌─────────────────┐
│   Initiative    │       │   University    │
├─────────────────┤       ├─────────────────┤
│ id (PK)         │──────<│ id (PK)         │
│ name            │       │ name            │
│ university_id   │       │ short_code      │
│ description     │       └─────────────────┘
│ website_url     │
│ github_url      │       ┌─────────────────┐
│ linkedin_url    │       │   Technology    │
│ founded_year    │       ├─────────────────┤
│ team_size       │>──────│ id (PK)         │
│ status          │       │ name            │
│ created_at      │       │ category        │
│ updated_at      │       └─────────────────┘
└────────┬────────┘
         │
         │       ┌─────────────────┐
         │       │     Score       │
         │       ├─────────────────┤
         └──────<│ id (PK)         │
                 │ initiative_id   │
                 │ tech_score      │
                 │ talent_score    │
                 │ applicability   │
                 │ maturity_score  │
                 │ composite_score │
                 │ tier            │
                 │ scored_at       │
                 │ auto_generated  │
                 └─────────────────┘

┌─────────────────┐       ┌─────────────────┐
│   NewsArticle   │       │   Achievement   │
├─────────────────┤       ├─────────────────┤
│ id (PK)         │       │ id (PK)         │
│ initiative_id   │       │ initiative_id   │
│ title           │       │ title           │
│ url             │       │ description     │
│ source          │       │ achievement_date│
│ published_at    │       │ category        │
│ sentiment       │       │ verified        │
└─────────────────┘       └─────────────────┘

┌─────────────────┐       ┌─────────────────┐
│  GitHubMetrics  │       │   DataChange    │
├─────────────────┤       ├─────────────────┤
│ id (PK)         │       │ id (PK)         │
│ initiative_id   │       │ initiative_id   │
│ repo_count      │       │ field_name      │
│ total_stars     │       │ old_value       │
│ total_forks     │       │ new_value       │
│ commit_count_90d│       │ changed_at      │
│ contributors    │       │ change_source   │
│ last_activity   │       └─────────────────┘
│ collected_at    │
└─────────────────┘
```

### 5.2 SQL Schema

```sql
-- Core Tables
CREATE TABLE universities (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    short_code VARCHAR(10) NOT NULL,
    website_url VARCHAR(255)
);

CREATE TABLE initiatives (
    id INTEGER PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    university_id INTEGER REFERENCES universities(id),
    description TEXT,
    website_url VARCHAR(255),
    github_org VARCHAR(100),
    linkedin_url VARCHAR(255),
    founded_year INTEGER,
    team_size INTEGER,
    status VARCHAR(20) DEFAULT 'active',
    technology_focus TEXT, -- JSON array
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scores (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    tech_score DECIMAL(3,2),
    talent_score DECIMAL(3,2),
    applicability_score DECIMAL(3,2),
    maturity_score DECIMAL(3,2),
    composite_score DECIMAL(3,2),
    tier VARCHAR(50),
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    auto_generated BOOLEAN DEFAULT TRUE,
    notes TEXT
);

CREATE TABLE github_metrics (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    repo_count INTEGER,
    total_stars INTEGER,
    total_forks INTEGER,
    commit_count_90d INTEGER,
    contributors_count INTEGER,
    primary_languages TEXT, -- JSON array
    last_activity_date DATE,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE news_articles (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    title VARCHAR(500),
    url VARCHAR(500),
    source VARCHAR(100),
    published_at DATE,
    sentiment VARCHAR(20),
    snippet TEXT,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE achievements (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    title VARCHAR(300),
    description TEXT,
    achievement_date DATE,
    category VARCHAR(50),
    verified BOOLEAN DEFAULT FALSE,
    source_url VARCHAR(500)
);

-- Indexes
CREATE INDEX idx_initiatives_university ON initiatives(university_id);
CREATE INDEX idx_scores_initiative ON scores(initiative_id);
CREATE INDEX idx_scores_date ON scores(scored_at);
CREATE INDEX idx_news_initiative ON news_articles(initiative_id);
CREATE INDEX idx_news_date ON news_articles(published_at);
```

---

## 6. Technology Stack

### 6.1 Backend
- **Language:** Python 3.11+
- **Framework:** FastAPI
- **Database:** SQLite (dev) / PostgreSQL (prod)
- **ORM:** SQLAlchemy
- **Task Queue:** Celery + Redis
- **Scraping:** BeautifulSoup4, Playwright (for JS-heavy sites)

### 6.2 Frontend
- **Framework:** React 18+ with TypeScript
- **Styling:** Tailwind CSS
- **Charts:** Recharts
- **State Management:** Zustand
- **API Client:** React Query

### 6.3 Infrastructure
- **Deployment:** Docker + Docker Compose
- **Hosting:** Self-hosted or cloud (AWS/GCP/Vercel)
- **CI/CD:** GitHub Actions
- **Monitoring:** Prometheus + Grafana (optional)

### 6.4 External APIs
- GitHub REST API v3
- Google News API (or NewsAPI.org)
- OpenAI API (for sentiment analysis, optional)

---

## 7. Integration Requirements

### 7.1 Obsidian Sync
- Export database to Obsidian-compatible markdown
- One file per initiative with YAML frontmatter
- Index file with table and links
- Bi-directional sync for manual notes

### 7.2 Things Integration
- Create tasks from alerts
- Track follow-up actions per initiative
- Due date management for outreach

### 7.3 Notification Channels
- Email (SMTP)
- Slack webhook (optional)
- In-app notifications

---

## 8. Development Phases

### Phase 1: MVP (2-3 weeks)
- Core data model and database
- Manual data entry via CLI
- Basic scoring algorithm
- Markdown export
- Simple HTML dashboard (static)

### Phase 2: Automation (2-3 weeks)
- GitHub API integration
- Website scraper (basic)
- News aggregation
- Automated scoring
- API endpoints

### Phase 3: Dashboard (2-3 weeks)
- Full React dashboard
- All views implemented
- Admin panel
- Export functionality

### Phase 4: Polish & Scale (2 weeks)
- Performance optimization
- Enhanced scraping
- Alert system
- Documentation
- Testing

---

## 9. Success Metrics

| Metric | Target |
|--------|--------|
| Data freshness | < 7 days old |
| Score accuracy | 80% agreement with manual review |
| Dashboard uptime | 99% |
| Time to add new initiative | < 5 minutes manual |
| False positive alerts | < 10% |

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Website structure changes | Data collection fails | Modular scrapers, alerts on failures |
| API rate limits | Incomplete data | Caching, respectful rate limiting |
| Scoring algorithm bias | Incorrect rankings | Regular manual calibration |
| Data accuracy | Bad decisions | Verification flags, source tracking |

---

## Appendix A: Configuration Schema

```yaml
# config.yaml
database:
  type: sqlite  # or postgresql
  path: ./data/initiatives.db

collection:
  github:
    enabled: true
    token: ${GITHUB_TOKEN}
    rate_limit: 5000  # requests/hour

  web_scraping:
    enabled: true
    rate_limit: 1  # requests/second
    user_agent: "MunichInitiativesBot/1.0"

  news:
    enabled: true
    sources:
      - name: google_news
        api_key: ${GOOGLE_NEWS_API_KEY}
      - name: munich_startup_rss
        url: https://www.munich-startup.de/feed/

scoring:
  weights:
    tech_score: 0.30
    talent_score: 0.25
    applicability_score: 0.25
    maturity_score: 0.20

  thresholds:
    tier1_min: 4.0
    tier2_min: 3.0

notifications:
  email:
    enabled: true
    smtp_host: ${SMTP_HOST}
    recipients:
      - team@example.com

  slack:
    enabled: false
    webhook_url: ${SLACK_WEBHOOK}

sync:
  obsidian:
    enabled: true
    vault_path: /path/to/obsidian/vault
    folder: Student Initiatives
```

---

*Document maintained by UnicornInitiative Team*
