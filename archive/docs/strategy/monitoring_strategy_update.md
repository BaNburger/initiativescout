# Monitoring Strategy Update
## Official Data Sources Analysis

**Date:** 2026-02-02
**Purpose:** Update automation strategy based on official university page structures

---

## Key Discovery: Official University Initiative Directories

The three Munich universities maintain official student initiative directories that can serve as **primary data sources** for our monitoring tool:

| University | Official URL | Page Type |
|------------|--------------|-----------|
| TUM | tum.de/community/campusleben/student-clubs-galerie | Gallery with filters |
| LMU | lmu.de/de/workspace-fuer-studierende/studieren-und-leben/studentische-initiativen | List page |
| HM | hm.edu/studium_1/im_studium/rund_ums_studium/aktivitaeten | Category index |

---

## Implications for Monitoring Tool

### 1. Primary Data Source Strategy

**Before:** Relied on individual initiative websites and manual research
**After:** Use official directories as authoritative source, supplement with initiative websites

```
Official University Directories (Weekly)
         │
         ▼
   ┌─────────────┐
   │ New/Changed │──► Alert: New initiative detected
   │  Detection  │
   └─────────────┘
         │
         ▼
   ┌─────────────┐
   │  Enrichment │──► GitHub API, LinkedIn, Individual websites
   │    Layer    │
   └─────────────┘
         │
         ▼
   ┌─────────────┐
   │   Scoring   │──► Apply rating algorithm
   │   Engine    │
   └─────────────┘
```

### 2. Data Collection Updates

#### TUM Gallery Scraper
```python
# TUM uses a gallery format with filtering
# Structure: Card-based display with categories

class TUMGalleryScraper(BaseCollector):
    BASE_URL = "https://www.tum.de/community/campusleben/student-clubs-galerie"

    # Expected data per initiative:
    # - Name
    # - Category (Tech, Culture, Sports, etc.)
    # - Short description
    # - Link to detail page or external website
    # - Possibly: Logo/image

    # Categories to filter:
    TECH_CATEGORIES = [
        "Technik & Innovation",
        "Wissenschaft",
        "Entrepreneurship"
    ]
```

#### LMU List Scraper
```python
# LMU uses a straightforward list format
# Structure: Simple list with links

class LMUListScraper(BaseCollector):
    BASE_URL = "https://www.lmu.de/de/workspace-fuer-studierende/studieren-und-leben/studentische-initiativen"

    # Expected data per initiative:
    # - Name
    # - Brief description
    # - External link
    # - Possibly: Category tags
```

#### HM Category Scraper
```python
# HM uses category-based navigation
# Structure: Index page with sub-pages per category

class HMCategoryScraper(BaseCollector):
    BASE_URL = "https://hm.edu/studium_1/im_studium/rund_ums_studium/aktivitaeten"

    # Expected structure:
    # Main page → Category links → Individual initiatives
    # Categories: Student initiatives, Sports, Culture, etc.
```

### 3. Change Detection Algorithm

The official directories enable efficient change detection:

```python
def detect_changes(university: str, new_data: List[Initiative]) -> ChangeReport:
    """
    Compare new scrape with stored data to detect:
    1. New initiatives (not in database)
    2. Removed initiatives (in database but not in scrape)
    3. Updated initiatives (name/description changed)
    """

    stored = get_stored_initiatives(university)

    new_initiatives = [i for i in new_data if i.name not in stored]
    removed = [i for i in stored if i.name not in new_data]
    updated = detect_content_changes(stored, new_data)

    return ChangeReport(
        new=new_initiatives,
        removed=removed,
        updated=updated,
        timestamp=datetime.now()
    )
```

### 4. Updated Technical Requirements

#### New Requirement: Official Source Priority

**FR-DC-006: Official Directory Integration**
- **Description:** Scrape official university initiative directories as primary data source
- **Sources:** TUM Gallery, LMU List, HM Activities page
- **Data Collected:** Name, category, description, website link, logo (if available)
- **Frequency:** Weekly (official pages change slowly)
- **Priority:** Primary source - other sources supplement this data
- **Change Detection:** Track additions, removals, and updates

**FR-DC-007: Multi-Layer Enrichment**
- **Description:** Enrich official directory data with additional sources
- **Layer 1:** Official directory (authoritative for existence)
- **Layer 2:** Initiative website (detailed info, team, achievements)
- **Layer 3:** GitHub (activity metrics, technology stack)
- **Layer 4:** LinkedIn (team size, growth indicators)
- **Layer 5:** News (achievements, press coverage)

### 5. Scoring Impact

Official directories help with maturity scoring:

```python
def calculate_maturity_score(initiative: Initiative) -> float:
    """
    Updated maturity calculation using official directory presence
    """
    score = 0.0

    # Listed in official directory = legitimacy signal
    if initiative.in_official_directory:
        score += 1.0  # Base legitimacy

    # Years active (from founding date or first appearance)
    if initiative.years_active >= 5:
        score += 2.0
    elif initiative.years_active >= 3:
        score += 1.5
    elif initiative.years_active >= 1:
        score += 1.0

    # Infrastructure quality
    score += rate_infrastructure(initiative)  # 0-2.0

    return min(score, 5.0)
```

### 6. Implementation Priority Update

#### Phase 1 MVP (Updated)
1. **Official directory scrapers** (3 universities) - NEW PRIORITY
2. Database and basic CRUD
3. Change detection alerts
4. Simple dashboard showing all initiatives

#### Phase 2 Enrichment
5. GitHub API integration
6. Initiative website scraping
7. Scoring algorithm
8. News aggregation

#### Phase 3 Advanced
9. LinkedIn monitoring (carefully, respecting TOS)
10. Automated ratings
11. Full dashboard features

### 7. Monitoring Frequency Strategy

| Data Source | Frequency | Rationale |
|-------------|-----------|-----------|
| Official directories | Weekly | Changes slowly, low volume |
| Initiative websites | Bi-weekly | Medium change frequency |
| GitHub metrics | Daily | High activity, API-friendly |
| News aggregation | Daily | Time-sensitive |
| LinkedIn | Monthly | Respect rate limits |

### 8. Alert Categories

Based on official directory monitoring:

```python
class AlertType(Enum):
    NEW_INITIATIVE = "new_initiative"          # Not seen before
    INITIATIVE_REMOVED = "initiative_removed"  # Was in directory, now gone
    CATEGORY_CHANGED = "category_changed"      # Recategorized
    DESCRIPTION_UPDATED = "description_updated"
    WEBSITE_CHANGED = "website_changed"
    SCORE_SIGNIFICANT_CHANGE = "score_change"  # ≥0.5 change
```

---

## Revised Data Model

### Initiative Source Tracking

```sql
CREATE TABLE initiative_sources (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    source_type VARCHAR(50),  -- 'official_directory', 'github', 'website', etc.
    source_url VARCHAR(500),
    first_seen_date DATE,
    last_seen_date DATE,
    last_checked_date DATE,
    is_primary BOOLEAN DEFAULT FALSE,
    raw_data JSON
);

-- Track presence in official directories
CREATE TABLE directory_presence (
    id INTEGER PRIMARY KEY,
    initiative_id INTEGER REFERENCES initiatives(id),
    university VARCHAR(10),  -- 'TUM', 'LMU', 'HM'
    directory_url VARCHAR(500),
    first_listed_date DATE,
    last_verified_date DATE,
    is_currently_listed BOOLEAN DEFAULT TRUE,
    category_in_directory VARCHAR(100)
);
```

---

## Benefits of This Approach

1. **Authoritative Data:** Official directories are the source of truth for which initiatives exist
2. **Change Detection:** Easy to detect new initiatives when they appear in directories
3. **Reduced False Positives:** Won't track defunct initiatives if removed from directories
4. **Legitimacy Signal:** Presence in official directory indicates university recognition
5. **Structured Categories:** Universities often pre-categorize initiatives
6. **Lower Scraping Load:** One page per university vs. hundreds of initiative sites

---

## Next Steps

1. [ ] Build and test scrapers for each university directory
2. [ ] Implement change detection logic
3. [ ] Create alert system for new initiatives
4. [ ] Backfill existing initiatives with directory presence data
5. [ ] Update scoring algorithm to weight directory presence

---

*Strategy document for UnicornInitiative Monitoring Tool*
