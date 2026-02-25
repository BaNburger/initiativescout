# User Stories
## Munich Student Initiatives Tracker

**Version:** 1.0
**Date:** 2026-02-02

---

## Personas

### Primary Users

#### 1. Investment Scout (Sarah)
- **Role:** Associate at UnicornInitiative
- **Goal:** Identify high-potential student initiatives for investment/support
- **Pain Points:** Manual research is time-consuming, hard to track changes
- **Tech Comfort:** High

#### 2. Partnership Manager (Marcus)
- **Role:** Business development lead
- **Goal:** Connect initiatives with compute partners and industry mentors
- **Pain Points:** Needs quick access to tech stack info and team contacts
- **Tech Comfort:** Medium

#### 3. Program Director (Dr. Weber)
- **Role:** Oversees deep tech support program
- **Goal:** Strategic decisions on which initiatives to support
- **Pain Points:** Needs data-driven insights and trend analysis
- **Tech Comfort:** Low-Medium

### Secondary Users

#### 4. Initiative Lead (Alex)
- **Role:** Student leading a tech initiative
- **Goal:** Get discovered and receive support
- **Pain Points:** Unclear on evaluation criteria
- **Tech Comfort:** High

#### 5. External Partner (Lisa)
- **Role:** Cloud provider partner manager
- **Goal:** Identify initiatives for compute credits program
- **Pain Points:** Limited visibility into Munich ecosystem
- **Tech Comfort:** Medium

---

## Epic 1: Initiative Discovery & Tracking

### US-1.1: View All Initiatives
**As** Sarah (Investment Scout)
**I want** to see a comprehensive list of all tracked initiatives
**So that** I can quickly scan the ecosystem and identify interesting candidates

**Acceptance Criteria:**
- [ ] Table displays: Name, University, Tier, Composite Score, Last Updated
- [ ] Table is sortable by any column
- [ ] Table loads within 2 seconds
- [ ] Pagination or infinite scroll for 50+ items
- [ ] Shows total count of initiatives

**Priority:** P0
**Story Points:** 5

---

### US-1.2: Filter Initiatives
**As** Marcus (Partnership Manager)
**I want** to filter initiatives by multiple criteria
**So that** I can find initiatives matching specific partner requirements

**Acceptance Criteria:**
- [ ] Filter by University (TUM, LMU, HM)
- [ ] Filter by Tier (1, 2, 3)
- [ ] Filter by Technology domain (AI, Robotics, Space, etc.)
- [ ] Filter by Score range (min-max slider)
- [ ] Multiple filters can be combined (AND logic)
- [ ] Filters persist across page navigation
- [ ] "Clear all filters" button

**Priority:** P0
**Story Points:** 5

---

### US-1.3: Search Initiatives
**As** Sarah (Investment Scout)
**I want** to search initiatives by name or keyword
**So that** I can quickly find a specific initiative I've heard about

**Acceptance Criteria:**
- [ ] Search box prominently displayed
- [ ] Searches name and description fields
- [ ] Results appear as user types (debounced)
- [ ] Highlights matching text
- [ ] Shows "No results" message with suggestions

**Priority:** P0
**Story Points:** 3

---

### US-1.4: View Initiative Detail
**As** Sarah (Investment Scout)
**I want** to see complete details about a single initiative
**So that** I can make an informed assessment of their potential

**Acceptance Criteria:**
- [ ] Displays all basic info (name, description, website, links)
- [ ] Shows all four score dimensions with visual breakdown
- [ ] Displays tier classification with explanation
- [ ] Lists key technologies as tags
- [ ] Shows achievements chronologically
- [ ] Displays team size and founding year
- [ ] Links to external resources (GitHub, LinkedIn, website) open in new tab
- [ ] "Edit" button for authorized users

**Priority:** P0
**Story Points:** 8

---

### US-1.5: View Score History
**As** Dr. Weber (Program Director)
**I want** to see how an initiative's scores have changed over time
**So that** I can identify momentum and trajectory

**Acceptance Criteria:**
- [ ] Line chart showing composite score over time
- [ ] Option to show individual dimension scores
- [ ] Date range selector (3mo, 6mo, 1yr, all)
- [ ] Tooltips showing exact values on hover
- [ ] Annotations for significant events (if available)

**Priority:** P1
**Story Points:** 5

---

### US-1.6: Compare Initiatives
**As** Marcus (Partnership Manager)
**I want** to compare two or more initiatives side-by-side
**So that** I can help partners choose between candidates

**Acceptance Criteria:**
- [ ] Select up to 4 initiatives for comparison
- [ ] Side-by-side display of key attributes
- [ ] Radar chart comparing all score dimensions
- [ ] Highlight best-in-class for each dimension
- [ ] Export comparison as PDF
- [ ] Shareable link to comparison view

**Priority:** P1
**Story Points:** 8

---

## Epic 2: News & Activity Monitoring

### US-2.1: View News Feed
**As** Sarah (Investment Scout)
**I want** to see recent news about tracked initiatives
**So that** I stay informed about ecosystem developments

**Acceptance Criteria:**
- [ ] Chronological list of news articles
- [ ] Shows: Title, Source, Date, Related Initiative, Sentiment
- [ ] Click to open article in new tab
- [ ] Click initiative name to go to detail page
- [ ] Filter by initiative, source, sentiment
- [ ] Date range filter

**Priority:** P1
**Story Points:** 5

---

### US-2.2: View Initiative Activity
**As** Sarah (Investment Scout)
**I want** to see recent activity for a specific initiative
**So that** I can gauge their momentum and engagement

**Acceptance Criteria:**
- [ ] Activity timeline on initiative detail page
- [ ] Shows: News mentions, GitHub activity, Achievements
- [ ] Color-coded by activity type
- [ ] Expandable items for more detail
- [ ] "Show more" pagination

**Priority:** P1
**Story Points:** 5

---

### US-2.3: GitHub Activity Dashboard
**As** Marcus (Partnership Manager)
**I want** to see GitHub metrics for an initiative
**So that** I can assess their technical activity and open source engagement

**Acceptance Criteria:**
- [ ] Repository list with stars, forks, last activity
- [ ] Commit frequency chart (last 90 days)
- [ ] Language breakdown pie chart
- [ ] Contributor count
- [ ] Link to GitHub organization
- [ ] "Refresh" button to fetch latest data

**Priority:** P2
**Story Points:** 5

---

## Epic 3: Scoring & Rating

### US-3.1: Understand Scoring
**As** Alex (Initiative Lead)
**I want** to understand how initiatives are scored
**So that** I know what we need to improve

**Acceptance Criteria:**
- [ ] "How Scoring Works" documentation page
- [ ] Explains each dimension with examples
- [ ] Shows weight of each dimension
- [ ] Provides improvement suggestions per dimension
- [ ] FAQ section

**Priority:** P1
**Story Points:** 3

---

### US-3.2: Score Breakdown
**As** Dr. Weber (Program Director)
**I want** to see a detailed breakdown of each score dimension
**So that** I can understand an initiative's specific strengths and weaknesses

**Acceptance Criteria:**
- [ ] Each dimension expandable to show components
- [ ] Shows raw inputs that contributed to score
- [ ] Indicates which inputs were auto-collected vs manual
- [ ] Color-coded score bars (red/yellow/green)
- [ ] Comparison to average score in that dimension

**Priority:** P1
**Story Points:** 5

---

### US-3.3: Manual Score Override
**As** Sarah (Investment Scout)
**I want** to manually adjust a score with justification
**So that** I can incorporate qualitative insights the algorithm missed

**Acceptance Criteria:**
- [ ] "Override Score" button (authorized users only)
- [ ] Form to enter new score (1-5) per dimension
- [ ] Required justification text field (min 50 chars)
- [ ] Shows original auto-generated score
- [ ] Saves with user ID and timestamp
- [ ] Visible indicator that score was manually adjusted
- [ ] Audit log of all overrides

**Priority:** P2
**Story Points:** 5

---

## Epic 4: Alerts & Notifications

### US-4.1: Configure Alert Preferences
**As** Sarah (Investment Scout)
**I want** to configure what alerts I receive
**So that** I'm notified about changes I care about

**Acceptance Criteria:**
- [ ] Toggle alerts for: New initiatives, Score changes, News mentions, Achievements
- [ ] Set threshold for score change alerts (e.g., >0.5)
- [ ] Subscribe to specific initiatives
- [ ] Choose delivery method (email, in-app, both)
- [ ] Frequency setting (immediate, daily digest, weekly)

**Priority:** P2
**Story Points:** 5

---

### US-4.2: Receive Score Change Alert
**As** Sarah (Investment Scout)
**I want** to be notified when an initiative's score changes significantly
**So that** I don't miss important momentum shifts

**Acceptance Criteria:**
- [ ] Alert triggered when composite score changes ≥ threshold
- [ ] Alert shows: Initiative name, old score, new score, change direction
- [ ] Link to initiative detail page
- [ ] Indicates reason for change if determinable
- [ ] Delivered via configured channel

**Priority:** P2
**Story Points:** 3

---

### US-4.3: Receive New Initiative Alert
**As** Marcus (Partnership Manager)
**I want** to be notified when a new initiative is added
**So that** I can evaluate them for partner programs early

**Acceptance Criteria:**
- [ ] Alert triggered when new initiative created
- [ ] Shows: Name, University, Initial scores, Description summary
- [ ] Link to initiative detail page
- [ ] Delivered via configured channel

**Priority:** P2
**Story Points:** 2

---

### US-4.4: View Alert History
**As** Sarah (Investment Scout)
**I want** to see a history of all alerts
**So that** I can review what I might have missed

**Acceptance Criteria:**
- [ ] List of all alerts with date/time
- [ ] Filter by alert type
- [ ] Mark as read/unread
- [ ] Bulk actions (mark all read, delete)
- [ ] Search within alerts

**Priority:** P3
**Story Points:** 3

---

## Epic 5: Data Management

### US-5.1: Add New Initiative
**As** Sarah (Investment Scout)
**I want** to manually add a new initiative
**So that** I can track one the automated collection missed

**Acceptance Criteria:**
- [ ] Form with required fields: Name, University, Description
- [ ] Optional fields: Website, GitHub, LinkedIn, Team size, Founded year
- [ ] Technology tags (multi-select or free-form)
- [ ] Validation for URLs
- [ ] Success confirmation with link to new entry
- [ ] Triggers initial scoring

**Priority:** P1
**Story Points:** 5

---

### US-5.2: Edit Initiative Details
**As** Sarah (Investment Scout)
**I want** to correct or update initiative information
**So that** the database stays accurate

**Acceptance Criteria:**
- [ ] Edit button on detail page
- [ ] Form pre-populated with current data
- [ ] All fields editable
- [ ] Change is logged in audit trail
- [ ] Validation prevents invalid data
- [ ] Success confirmation

**Priority:** P1
**Story Points:** 3

---

### US-5.3: Add Achievement
**As** Sarah (Investment Scout)
**I want** to record a new achievement for an initiative
**So that** their track record is complete

**Acceptance Criteria:**
- [ ] Form fields: Title, Description, Date, Category, Source URL
- [ ] Category dropdown: Competition win, Publication, Award, Milestone, Other
- [ ] Achievement appears in initiative timeline
- [ ] Triggers score recalculation
- [ ] Verification checkbox (for confirmed achievements)

**Priority:** P2
**Story Points:** 3

---

### US-5.4: Trigger Data Refresh
**As** Sarah (Investment Scout)
**I want** to manually trigger a data refresh for an initiative
**So that** I can get the latest information before a meeting

**Acceptance Criteria:**
- [ ] "Refresh Data" button on detail page
- [ ] Shows progress indicator
- [ ] Updates GitHub metrics, news
- [ ] Shows last refresh time
- [ ] Rate-limited to prevent abuse (1 per initiative per hour)

**Priority:** P2
**Story Points:** 3

---

### US-5.5: View Data Sources
**As** Dr. Weber (Program Director)
**I want** to see what data sources fed into an initiative's profile
**So that** I can assess data quality and completeness

**Acceptance Criteria:**
- [ ] Section showing all data sources
- [ ] For each source: Type, URL, Last fetched, Status
- [ ] Indicates which fields came from which source
- [ ] Shows any fetch errors
- [ ] Link to source where applicable

**Priority:** P3
**Story Points:** 3

---

## Epic 6: Export & Integration

### US-6.1: Export to CSV
**As** Marcus (Partnership Manager)
**I want** to export initiative data to CSV
**So that** I can share it with partners who prefer spreadsheets

**Acceptance Criteria:**
- [ ] Export all initiatives or filtered subset
- [ ] Includes all key fields and scores
- [ ] Proper CSV formatting (quoted fields, UTF-8)
- [ ] Download as file
- [ ] Includes export timestamp

**Priority:** P1
**Story Points:** 3

---

### US-6.2: Export to Markdown
**As** Sarah (Investment Scout)
**I want** to export the database to markdown
**So that** I can sync with our Obsidian knowledge base

**Acceptance Criteria:**
- [ ] Generates markdown files (one per initiative)
- [ ] YAML frontmatter with structured data
- [ ] Index file with table and links
- [ ] Download as ZIP
- [ ] Option to auto-sync to configured Obsidian vault

**Priority:** P1
**Story Points:** 5

---

### US-6.3: Generate Report
**As** Dr. Weber (Program Director)
**I want** to generate a summary report
**So that** I can present ecosystem insights to stakeholders

**Acceptance Criteria:**
- [ ] Select date range and initiatives to include
- [ ] Report includes: Summary stats, Top performers, Score distributions, Trends
- [ ] Charts and visualizations included
- [ ] Export as PDF
- [ ] Professional formatting

**Priority:** P2
**Story Points:** 8

---

### US-6.4: Sync to Things
**As** Sarah (Investment Scout)
**I want** alerts to create tasks in Things
**So that** follow-up actions are tracked in my task manager

**Acceptance Criteria:**
- [ ] Connect Things via URL scheme or MCP
- [ ] New initiative alert creates "Review [Initiative]" task
- [ ] Score change alert creates "Follow up on [Initiative]" task
- [ ] Tasks include context link back to dashboard
- [ ] Configurable which alerts create tasks

**Priority:** P2
**Story Points:** 5

---

## Epic 7: Administration

### US-7.1: View System Status
**As** Sarah (Investment Scout)
**I want** to see the status of data collection jobs
**So that** I know if the data is fresh

**Acceptance Criteria:**
- [ ] Dashboard showing: Last collection run, Success/failure status
- [ ] Per-source status (GitHub, News, Scrapers)
- [ ] Error log for failed collections
- [ ] "Run Now" button for manual trigger

**Priority:** P2
**Story Points:** 3

---

### US-7.2: Manage Users
**As** Dr. Weber (Program Director)
**I want** to manage who has access to the system
**So that** only authorized team members can edit data

**Acceptance Criteria:**
- [ ] List of all users with roles
- [ ] Add new user (email invitation)
- [ ] Edit user role (viewer, editor, admin)
- [ ] Deactivate user
- [ ] Activity log per user

**Priority:** P2
**Story Points:** 5

---

### US-7.3: Configure Scoring Weights
**As** Dr. Weber (Program Director)
**I want** to adjust the scoring algorithm weights
**So that** we can evolve our evaluation criteria

**Acceptance Criteria:**
- [ ] Admin page showing current weights
- [ ] Sliders to adjust weights (must sum to 1.0)
- [ ] Preview impact on current scores
- [ ] Require confirmation before applying
- [ ] Log changes with timestamp and user

**Priority:** P3
**Story Points:** 5

---

### US-7.4: View Audit Log
**As** Dr. Weber (Program Director)
**I want** to see a log of all changes to the database
**So that** I can track accountability

**Acceptance Criteria:**
- [ ] List of all create/update/delete actions
- [ ] Shows: Timestamp, User, Action, Entity, Changes
- [ ] Filter by user, action type, entity type
- [ ] Search functionality
- [ ] Export to CSV

**Priority:** P3
**Story Points:** 3

---

## User Story Summary

| Epic | P0 | P1 | P2 | P3 | Total |
|------|----|----|----|----|-------|
| 1. Discovery & Tracking | 3 | 2 | 1 | 0 | 6 |
| 2. News & Activity | 0 | 2 | 1 | 0 | 3 |
| 3. Scoring & Rating | 0 | 2 | 1 | 0 | 3 |
| 4. Alerts & Notifications | 0 | 0 | 3 | 1 | 4 |
| 5. Data Management | 0 | 2 | 3 | 1 | 6 |
| 6. Export & Integration | 0 | 2 | 2 | 0 | 4 |
| 7. Administration | 0 | 0 | 2 | 2 | 4 |
| **Total** | **3** | **10** | **13** | **4** | **30** |

---

## MVP Scope (Phase 1)

The following user stories should be included in the MVP:

### Must Have (P0)
- US-1.1: View All Initiatives
- US-1.2: Filter Initiatives
- US-1.3: Search Initiatives

### Should Have (Selected P1)
- US-1.4: View Initiative Detail
- US-5.1: Add New Initiative
- US-5.2: Edit Initiative Details
- US-6.1: Export to CSV
- US-6.2: Export to Markdown

### Total MVP Story Points: ~40

---

*Document maintained by UnicornInitiative Team*
