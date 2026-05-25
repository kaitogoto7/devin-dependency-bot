# Devin Dependency Bot

Human-in-the-loop **frontend** dependency management for **Apache superset**.

A human triggers a scan, reviews categorized dependency issues, selects which
to fix, and the bot posts GitHub Issues and triggers
[Devin](https://devin.ai) sessions to open PRs for each selected issue.

The scan step runs `npm audit` and `npm outdated` locally inside the Docker
container. After scanning, a Devin session automatically filters and ranks
the issues by priority, recommending the top-k most important ones. Devin
sessions are also used for **fixing** selected issues.

---

## System Architecture

```
Human (POST /scan)
    │
    ▼
┌──────────────────────────────┐
│  Devin Dependency Bot        │
│  (Docker container)          │
│                              │
│  POST /scan                  │
│    → Runs npm install,       │
│      npm audit, npm outdated │
│    → If issues found,        │
│      triggers Devin filter   │
│      session                 │
│    → Returns scan_id         │
│                              │
│  GET /scan/{scan_id}         │
│    → Returns "in_progress"   │
│      during npm scan         │
│    → Returns "filtering"     │
│      while Devin prioritizes │
│    → Returns categorized     │
│      issues with             │
│      "recommended" flag      │
│      when complete           │
│                              │
│  POST /scan/{scan_id}/select │
│    → Human selects issues    │
│    → Posts to GitHub Issues  │
│      (labeled devin-issue)   │
│    → Triggers fix sessions   │
│                              │
│  GET /health                 │
│  GET /metrics                │
│  GET /sessions               │
│                              │
│  Background: Filter + fix    │
│    session poller            │
│  Persistence: SQLite @ /data │
│  Telemetry: Embedded process │
│    loop counters             │
└──────────────────────────────┘
    │                    │
    │ Devin API          │ GitHub API
    │ (fix sessions)     │
    ▼                    ▼
Devin Sessions      GitHub Issues + PRs
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Docker & Docker Compose** | Docker Compose v2+ (the `docker compose` CLI plugin) |
| **Devin API token** | Obtain from <https://app.devin.ai/settings> (needed for fix sessions) |
| **GitHub PAT** | Personal Access Token with `repo` scope |
| **superset-frontend/** | Must be accessible to the container via volume mount |

Node.js 20 LTS is bundled in the Docker image — no host installation required.

---

## Setup Instructions

### 1. Clone and enter the bot directory

```bash
git clone https://github.com/superset.git
cd devin-dependency-bot
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your tokens
```

### 3. Cleanly start the Docker

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

### 4. Verify

```bash
curl http://localhost:8000/health
```

---

## Usage

### 1. Trigger a scan

```bash
curl -X POST http://localhost:8000/scan
# Returns: {"scan_id": "abc123def456", "status": "in_progress"}
```

The bot runs `npm install`, `npm audit --json`, and `npm outdated --json`
inside the container against the mounted `superset-frontend/` directory.
If issues are found, a Devin filter session automatically ranks them by
priority and marks the top-k as `recommended: true`. Results are available
once both the npm commands and the filter session complete.

### 2. Check scan results

```bash
curl http://localhost:8000/scan/{scan_id}
# Returns categorized dependency issues with recommended flag when complete
```

Issues include a `recommended: true/false` field. The recommended issues are
the top-k most important as determined by Devin's priority analysis.

### 3. Review and select issues to fix

```bash
curl -X POST http://localhost:8000/scan/{scan_id}/select \
  -H "Content-Type: application/json" \
  -d '{"issue_ids": ["id1", "id2"]}'
# Posts GitHub Issues (labeled devin-issue) and triggers Devin fix sessions
```

All GitHub issues created via this endpoint are labeled with `devin-issue`
in addition to the category label and `devin-dependency-bot`. This label can
be used to filter all bot-created issues at
<https://github.com/kaitogoto7/superset/issues?q=label%3Adevin-issue>.

### 4. Monitor progress

```bash
curl http://localhost:8000/sessions
curl http://localhost:8000/metrics
```

### 5. Check GitHub

- Issues: <https://github.com/kaitogoto7/superset/issues>
- PRs: <https://github.com/kaitogoto7/superset/pulls>

---

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEVIN_API_TOKEN` | Yes | — | Devin API bearer token (for fix sessions) |
| `GITHUB_TOKEN` | Yes | — | GitHub PAT for API calls |
| `GITHUB_REPO` | No | `kaitogoto7/superset` | Target repository |
| `DEVIN_API_BASE_URL` | No | `https://api.devin.ai/v1` | Devin API base URL |
| `LOG_LEVEL` | No | `INFO` | Python log level |
| `DB_PATH` | No | `/data/bot.db` | SQLite database path (inside container) |
| `POLL_INTERVAL_SECONDS` | No | `30` | Seconds between background polling cycles |
| `FRONTEND_DIR` | No | `/app/superset-frontend` | Path to superset-frontend inside the container |
| `TOP_K_ISSUES` | No | `3` | Number of top issues Devin recommends per scan |

---

## API Endpoints

### `POST /scan`

Runs `npm install`, `npm audit`, and `npm outdated` locally and returns a
scan ID. The scan executes in the background; poll `GET /scan/{scan_id}`
for results.

**Response:**
```json
{"scan_id": "abc123def456", "status": "in_progress"}
```

### `GET /scan/{scan_id}`

Returns categorized dependency issues once the scan completes.

**Response (in progress):**
```json
{"status": "in_progress", "scan_id": "abc123def456"}
```

**Response (filtering):**
```json
{"status": "filtering", "scan_id": "abc123def456"}
```

**Response (completed):**
```json
{
  "status": "completed",
  "scan_id": "abc123def456",
  "completed_at": "2025-06-01T12:05:00Z",
  "issues": [
    {
      "issue_id": "a1b2c3d4e5f6",
      "category": "frontend_security",
      "package": "nth-check",
      "current_version": ">=1.0.0",
      "fixed_version": "true",
      "description": "Inefficient Regular Expression Complexity",
      "severity": "high",
      "advisory_id": "https://github.com/advisories/GHSA-rp65-9cf3-cjxr",
      "selected": false,
      "recommended": true
    }
  ]
}
```

### `POST /scan/{scan_id}/select`

Submit issue IDs to fix. Creates GitHub Issues and triggers Devin fix sessions.

**Request:**
```json
{"issue_ids": ["a1b2c3d4e5f6", "f6e5d4c3b2a1"]}
```

**Response:**
```json
{
  "github_issues": [
    {"url": "https://github.com/kaitogoto7/superset/issues/100", "number": "100"}
  ],
  "fix_sessions": [
    {"issue_id": "a1b2c3d4e5f6", "session_id": "s-fix-1", "github_issue_url": "..."}
  ]
}
```

### `GET /health`

```json
{
  "status": "healthy",
  "uptime_seconds": 3621.5,
  "db_connected": true,
  "last_scan": "2025-06-01T12:00:00Z",
  "active_sessions": 2
}
```

### `GET /metrics`

Returns all observability metrics including scan telemetry, issue counters,
fix session counters, human review latency, and issue-to-PR latency.

### `GET /sessions`

Returns all triggered Devin sessions with their statuses.

---

## Issue Categories

| Category | Source | Description |
|---|---|---|
| `frontend_security` | `npm audit --json` | Known vulnerabilities in npm packages |
| `frontend_general` | `npm outdated --json` | Packages with newer patch/minor versions available |

---

## Observability

### Structured Logging

All log entries are JSON-formatted and include:
- `timestamp`, `level`, `event_type`, `correlation_id`

Event types: `scan.started`, `scan.npm_install`, `scan.completed`,
`scan.failed`, `scan.filter_triggered`, `scan.filter_poll`,
`scan.filter_completed`, `scan.filter_fallback`, `scan.filter_parse_error`,
`fix.session.failed`, `devin.session.completed`,
`devin.session.failed`, `devin.api.response`, `github.api.response`,
`error.*`.

### Embedded Operational Telemetry

Counters are incremented inline at each step of the process loop:

- `scans_triggered_total` — scan sessions initiated
- `scans_completed_total` — scan sessions completed (by outcome)
- `issues_found_total` — dependency issues found (by category)
- `issues_selected_total` — issues selected by human (by category)
- `issues_posted_total` — GitHub issues created (by category)
- `fix_sessions_triggered_total` — fix sessions triggered (by category)
- `fix_sessions_completed_total` — fix sessions completed (by outcome)
- `human_review_latency_seconds` — time from scan completion to human selection
- `issue_to_pr_latency_seconds` — time from GitHub issue to Devin PR
- `github_api_calls_total` — GitHub API calls (by endpoint and status)
- `github_api_latency_seconds` — GitHub API latency

### Background Filter & Fix Session Poller

A background asyncio task starts automatically when the application boots.
It polls all Devin **filter** sessions (scans in `filtering` status) every
`POLL_INTERVAL_SECONDS` (default: 30 seconds). When a filter session
completes, the poller parses the response, marks recommended issues, and
transitions the scan to `completed`. Local npm scans complete during the
scan request itself.

---

## Devin Prompts

### Fix Prompt

The fix prompt is sent to Devin when a user selects an issue to fix. It
contains the GitHub issue URL, category, package details, and instructions
for updating the dependency and running tests. Only frontend categories
are supported:

- **frontend_security**: Run `npm install`, update the package, run lint
  and tests.
- **frontend_general**: Update `package.json`, run `npm install`, update
  overrides if needed, run lint and tests.

---

## Testing

```bash
# Run inside Docker
docker compose run --rm devin-bot pytest tests/ -v --cov=app --cov-report=term-missing

# Or locally (Python 3.12+)
cd devin-dependency-bot
pip install -r requirements.txt
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Devin API auth errors | Verify `DEVIN_API_TOKEN` is valid at <https://app.devin.ai/settings> |
| GitHub API auth errors | Verify `GITHUB_TOKEN` has `repo` scope |
| SQLite permission errors | Ensure the Docker volume mount `/data` is writable |
| Container not starting | Check `docker compose logs devin-bot` for startup errors |
| Tests fail locally | Ensure Python 3.12+ and all deps installed from `requirements.txt` |
| `npm install` fails | Verify `superset-frontend/` is mounted at `FRONTEND_DIR` and contains a valid `package.json` |
| `npm audit --json` returns unexpected format | Ensure Node.js 20+ is installed (npm v7+ JSON format expected) |
| `npm outdated --json` returns empty `{}` | All packages are at their wanted versions — this is normal |
| Scan returns empty issues | Both `npm audit` and `npm outdated` found nothing to report |
