"""Logging, metrics collection, and health tracking."""

from __future__ import annotations

import logging
import statistics
import time
import uuid
from typing import Any

from pythonjsonlogger import json as json_logger

from app.models import HealthStatus, MetricsSnapshot

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output."""
    handler = logging.StreamHandler()
    formatter = json_logger.JsonFormatter(_LOG_FORMAT)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Metrics store (in-memory, singleton)
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Thread-safe, in-memory metrics collector with embedded telemetry."""

    def __init__(self) -> None:
        self._start_time = time.monotonic()

        # Devin API metrics
        self._devin_api_calls: dict[str, int] = {}
        self._devin_latencies: list[float] = []

        # GitHub API metrics
        self._github_api_calls: dict[str, int] = {}
        self._github_latencies: list[float] = []

        # Session metrics
        self._sessions_active: int = 0
        self._sessions_completed: dict[str, int] = {
            "pr_opened": 0,
            "no_update": 0,
            "failed": 0,
        }
        self._sessions_success: int = 0
        self._sessions_failure: int = 0
        self._session_durations: list[float] = []

        # Scan telemetry
        self._scans_triggered: int = 0
        self._scans_completed: dict[str, int] = {}
        self._issues_found: dict[str, int] = {}
        self._issues_selected: dict[str, int] = {}
        self._issues_posted: dict[str, int] = {}

        # Fix session telemetry
        self._fix_sessions_triggered: dict[str, int] = {}
        self._fix_sessions_completed: dict[str, int] = {}

        # Human review and issue-to-PR latency
        self._human_review_latencies: list[float] = []
        self._issue_to_pr_latencies: list[float] = []

    # -- Devin API counters --

    def inc_devin_api_call(self, prompt_type: str, status_code: int) -> None:
        key = f"{prompt_type}_{status_code}"
        self._devin_api_calls[key] = self._devin_api_calls.get(key, 0) + 1

    def record_devin_latency(self, seconds: float) -> None:
        self._devin_latencies.append(seconds)

    # -- GitHub API counters --

    def inc_github_api_call(self, endpoint: str, status_code: int) -> None:
        key = f"{endpoint}_{status_code}"
        self._github_api_calls[key] = self._github_api_calls.get(key, 0) + 1

    def record_github_latency(self, seconds: float) -> None:
        self._github_latencies.append(seconds)

    # -- Session counters --

    def inc_session_active(self) -> None:
        self._sessions_active += 1

    def dec_session_active(self) -> None:
        self._sessions_active = max(0, self._sessions_active - 1)

    def inc_session_completed(self, outcome: str) -> None:
        self._sessions_completed[outcome] = (
            self._sessions_completed.get(outcome, 0) + 1
        )
        if outcome == "failed":
            self._sessions_failure += 1
        else:
            self._sessions_success += 1

    def record_session_duration(self, seconds: float) -> None:
        self._session_durations.append(seconds)

    # -- Scan telemetry --

    def inc_scan_triggered(self) -> None:
        self._scans_triggered += 1

    def inc_scan_completed(self, outcome: str) -> None:
        self._scans_completed[outcome] = self._scans_completed.get(outcome, 0) + 1

    def inc_issues_found(self, category: str, count: int = 1) -> None:
        self._issues_found[category] = self._issues_found.get(category, 0) + count

    def inc_issues_selected(self, category: str, count: int = 1) -> None:
        self._issues_selected[category] = self._issues_selected.get(category, 0) + count

    def inc_issues_posted(self, category: str, count: int = 1) -> None:
        self._issues_posted[category] = self._issues_posted.get(category, 0) + count

    # -- Fix session telemetry --

    def inc_fix_session_triggered(self, category: str) -> None:
        self._fix_sessions_triggered[category] = (
            self._fix_sessions_triggered.get(category, 0) + 1
        )

    def inc_fix_session_completed(self, outcome: str) -> None:
        self._fix_sessions_completed[outcome] = (
            self._fix_sessions_completed.get(outcome, 0) + 1
        )

    # -- Human review / issue-to-PR latency --

    def record_human_review_latency(self, seconds: float) -> None:
        self._human_review_latencies.append(seconds)

    def record_issue_to_pr_latency(self, seconds: float) -> None:
        self._issue_to_pr_latencies.append(seconds)

    # -- percentile helpers --

    @staticmethod
    def _percentile(data: list[float], pct: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * (pct / 100.0)
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[f]
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])

    # -- MTTR --

    def mttr_seconds(self) -> float:
        if not self._session_durations:
            return 0.0
        return statistics.mean(self._session_durations)

    # -- snapshot --

    def snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            devin_api_calls_total=dict(self._devin_api_calls),
            devin_api_latency_p50=self._percentile(self._devin_latencies, 50),
            devin_api_latency_p95=self._percentile(self._devin_latencies, 95),
            devin_api_latency_p99=self._percentile(self._devin_latencies, 99),
            github_api_calls_total=dict(self._github_api_calls),
            github_api_latency_p50=self._percentile(self._github_latencies, 50),
            github_api_latency_p95=self._percentile(self._github_latencies, 95),
            github_api_latency_p99=self._percentile(self._github_latencies, 99),
            mttr_seconds=self.mttr_seconds(),
            devin_sessions_active=self._sessions_active,
            devin_sessions_completed_total=dict(self._sessions_completed),
            devin_sessions_success_total=self._sessions_success,
            devin_sessions_failure_total=self._sessions_failure,
            scans_triggered_total=self._scans_triggered,
            scans_completed_total=dict(self._scans_completed),
            issues_found_total=dict(self._issues_found),
            issues_selected_total=dict(self._issues_selected),
            issues_posted_total=dict(self._issues_posted),
            fix_sessions_triggered_total=dict(self._fix_sessions_triggered),
            fix_sessions_completed_total=dict(self._fix_sessions_completed),
            human_review_latency_p50=self._percentile(self._human_review_latencies, 50),
            human_review_latency_p95=self._percentile(self._human_review_latencies, 95),
            issue_to_pr_latency_p50=self._percentile(self._issue_to_pr_latencies, 50),
            issue_to_pr_latency_p95=self._percentile(self._issue_to_pr_latencies, 95),
        )

    # -- health --

    def health(
        self,
        db_connected: bool,
        last_scan: Any,
    ) -> HealthStatus:
        from datetime import datetime

        last_scan_dt = None
        if last_scan is not None:
            if isinstance(last_scan, str):
                last_scan_dt = datetime.fromisoformat(last_scan)
            else:
                last_scan_dt = last_scan

        return HealthStatus(
            status="healthy",
            uptime_seconds=round(time.monotonic() - self._start_time, 2),
            db_connected=db_connected,
            last_scan=last_scan_dt,
            active_sessions=self._sessions_active,
        )


# Module-level singleton
metrics = MetricsCollector()
