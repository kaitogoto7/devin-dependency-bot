"""Pydantic data models for scans, sessions, metrics, and health."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Dependency issue models
# ---------------------------------------------------------------------------


class DependencyIssue(BaseModel):
    issue_id: str
    category: str
    package: str
    current_version: str
    fixed_version: str
    description: str
    severity: str | None = None
    advisory_id: str | None = None
    selected: bool = False
    recommended: bool = False
    github_issue_url: str | None = None
    fix_session_id: str | None = None


# ---------------------------------------------------------------------------
# Scan models
# ---------------------------------------------------------------------------


class ScanResult(BaseModel):
    scan_id: str
    status: str = "in_progress"
    created_at: datetime | None = None
    completed_at: datetime | None = None
    issues: list[DependencyIssue] = Field(default_factory=list)


class SelectionRequest(BaseModel):
    issue_ids: list[str]


class SelectionResponse(BaseModel):
    github_issues: list[dict[str, str]] = Field(default_factory=list)
    fix_sessions: list[dict[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Devin session models
# ---------------------------------------------------------------------------


class DevinSession(BaseModel):
    session_id: str
    prompt_type: str
    idempotency_key: str
    created_at: datetime
    completed_at: datetime | None = None
    status: str = "pending"
    api_latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Metrics / health models
# ---------------------------------------------------------------------------


class MetricsSnapshot(BaseModel):
    devin_api_calls_total: dict[str, int] = Field(default_factory=dict)
    devin_api_latency_p50: float = 0.0
    devin_api_latency_p95: float = 0.0
    devin_api_latency_p99: float = 0.0
    github_api_calls_total: dict[str, int] = Field(default_factory=dict)
    github_api_latency_p50: float = 0.0
    github_api_latency_p95: float = 0.0
    github_api_latency_p99: float = 0.0
    mttr_seconds: float = 0.0
    devin_sessions_active: int = 0
    devin_sessions_completed_total: dict[str, int] = Field(default_factory=dict)
    devin_sessions_success_total: int = 0
    devin_sessions_failure_total: int = 0
    scans_triggered_total: int = 0
    scans_completed_total: dict[str, int] = Field(default_factory=dict)
    issues_found_total: dict[str, int] = Field(default_factory=dict)
    issues_selected_total: dict[str, int] = Field(default_factory=dict)
    issues_posted_total: dict[str, int] = Field(default_factory=dict)
    fix_sessions_triggered_total: dict[str, int] = Field(default_factory=dict)
    fix_sessions_completed_total: dict[str, int] = Field(default_factory=dict)
    human_review_latency_p50: float = 0.0
    human_review_latency_p95: float = 0.0
    issue_to_pr_latency_p50: float = 0.0
    issue_to_pr_latency_p95: float = 0.0


class HealthStatus(BaseModel):
    status: str = "healthy"
    uptime_seconds: float = 0.0
    db_connected: bool = True
    last_scan: datetime | None = None
    active_sessions: int = 0
