"""Tests for app.observability."""

from __future__ import annotations

import pytest

from app.observability import MetricsCollector


def test_metrics_initialized_to_zero() -> None:
    m = MetricsCollector()
    snap = m.snapshot()
    assert snap.devin_api_calls_total == {}
    assert snap.github_api_calls_total == {}
    assert snap.devin_sessions_active == 0
    assert snap.scans_triggered_total == 0
    assert snap.issues_found_total == {}
    assert snap.issues_selected_total == {}
    assert snap.issues_posted_total == {}
    assert snap.fix_sessions_triggered_total == {}
    assert snap.fix_sessions_completed_total == {}


def test_increment_devin_api_counters() -> None:
    m = MetricsCollector()
    m.inc_devin_api_call("scan", 200)
    m.inc_devin_api_call("scan", 200)
    snap = m.snapshot()
    assert snap.devin_api_calls_total["scan_200"] == 2


def test_increment_github_api_counters() -> None:
    m = MetricsCollector()
    m.inc_github_api_call("create_issue", 201)
    m.inc_github_api_call("create_issue", 201)
    m.inc_github_api_call("create_issue", 401)
    snap = m.snapshot()
    assert snap.github_api_calls_total["create_issue_201"] == 2
    assert snap.github_api_calls_total["create_issue_401"] == 1


def test_devin_latency_histogram_percentiles() -> None:
    m = MetricsCollector()
    for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        m.record_devin_latency(v)
    snap = m.snapshot()
    assert snap.devin_api_latency_p50 == pytest.approx(0.55, abs=0.05)
    assert snap.devin_api_latency_p95 > 0.9
    assert snap.devin_api_latency_p99 > 0.95


def test_github_latency_histogram_percentiles() -> None:
    m = MetricsCollector()
    for v in [0.1, 0.2, 0.3]:
        m.record_github_latency(v)
    snap = m.snapshot()
    assert snap.github_api_latency_p50 == pytest.approx(0.2, abs=0.05)


def test_mttr_calculation() -> None:
    m = MetricsCollector()
    m.record_session_duration(120.0)
    m.record_session_duration(180.0)
    assert m.mttr_seconds() == pytest.approx(150.0)


def test_mttr_empty() -> None:
    m = MetricsCollector()
    assert m.mttr_seconds() == 0.0


def test_health_returns_correct_fields() -> None:
    m = MetricsCollector()
    m.inc_session_active()
    h = m.health(db_connected=True, last_scan=None)
    assert h.status == "healthy"
    assert h.uptime_seconds >= 0
    assert h.db_connected is True
    assert h.active_sessions == 1


def test_session_completed_updates_counters() -> None:
    m = MetricsCollector()
    m.inc_session_completed("pr_opened")
    m.inc_session_completed("failed")
    snap = m.snapshot()
    assert snap.devin_sessions_success_total == 1
    assert snap.devin_sessions_failure_total == 1
    assert snap.devin_sessions_completed_total["pr_opened"] == 1
    assert snap.devin_sessions_completed_total["failed"] == 1


def test_scan_triggered_counter() -> None:
    m = MetricsCollector()
    m.inc_scan_triggered()
    m.inc_scan_triggered()
    snap = m.snapshot()
    assert snap.scans_triggered_total == 2


def test_scan_completed_counter() -> None:
    m = MetricsCollector()
    m.inc_scan_completed("findings_found")
    m.inc_scan_completed("no_findings")
    m.inc_scan_completed("findings_found")
    snap = m.snapshot()
    assert snap.scans_completed_total["findings_found"] == 2
    assert snap.scans_completed_total["no_findings"] == 1


def test_issues_found_counter() -> None:
    m = MetricsCollector()
    m.inc_issues_found("python_security", 3)
    m.inc_issues_found("frontend_general", 1)
    snap = m.snapshot()
    assert snap.issues_found_total["python_security"] == 3
    assert snap.issues_found_total["frontend_general"] == 1


def test_issues_selected_counter() -> None:
    m = MetricsCollector()
    m.inc_issues_selected("python_security", 2)
    snap = m.snapshot()
    assert snap.issues_selected_total["python_security"] == 2


def test_issues_posted_counter() -> None:
    m = MetricsCollector()
    m.inc_issues_posted("python_general")
    snap = m.snapshot()
    assert snap.issues_posted_total["python_general"] == 1


def test_fix_session_triggered_counter() -> None:
    m = MetricsCollector()
    m.inc_fix_session_triggered("python_security")
    m.inc_fix_session_triggered("python_security")
    snap = m.snapshot()
    assert snap.fix_sessions_triggered_total["python_security"] == 2


def test_fix_session_completed_counter() -> None:
    m = MetricsCollector()
    m.inc_fix_session_completed("pr_opened")
    m.inc_fix_session_completed("failed")
    snap = m.snapshot()
    assert snap.fix_sessions_completed_total["pr_opened"] == 1
    assert snap.fix_sessions_completed_total["failed"] == 1


def test_human_review_latency() -> None:
    m = MetricsCollector()
    m.record_human_review_latency(60.0)
    m.record_human_review_latency(120.0)
    snap = m.snapshot()
    assert snap.human_review_latency_p50 == pytest.approx(90.0, abs=5.0)


def test_issue_to_pr_latency() -> None:
    m = MetricsCollector()
    m.record_issue_to_pr_latency(300.0)
    snap = m.snapshot()
    assert snap.issue_to_pr_latency_p50 == pytest.approx(300.0)
