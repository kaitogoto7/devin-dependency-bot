"""Integration tests for /health, /metrics, and /sessions endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_returns_200(test_client: TestClient) -> None:
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "uptime_seconds" in data
    assert "db_connected" in data
    assert "active_sessions" in data


def test_health_does_not_include_merged_pr_count(test_client: TestClient) -> None:
    resp = test_client.get("/health")
    data = resp.json()
    assert "merged_pr_count" not in data


def test_metrics_returns_200(test_client: TestClient) -> None:
    resp = test_client.get("/metrics")
    assert resp.status_code == 200
    data = resp.json()
    expected_fields = [
        "devin_api_calls_total",
        "devin_api_latency_p50",
        "devin_api_latency_p95",
        "devin_api_latency_p99",
        "github_api_calls_total",
        "github_api_latency_p50",
        "github_api_latency_p95",
        "github_api_latency_p99",
        "mttr_seconds",
        "devin_sessions_active",
        "devin_sessions_completed_total",
        "devin_sessions_success_total",
        "devin_sessions_failure_total",
        "scans_triggered_total",
        "scans_completed_total",
        "issues_found_total",
        "issues_selected_total",
        "issues_posted_total",
        "fix_sessions_triggered_total",
        "fix_sessions_completed_total",
        "human_review_latency_p50",
        "human_review_latency_p95",
        "issue_to_pr_latency_p50",
        "issue_to_pr_latency_p95",
    ]
    for field in expected_fields:
        assert field in data, f"Missing metric field: {field}"


def test_metrics_does_not_include_old_fields(test_client: TestClient) -> None:
    resp = test_client.get("/metrics")
    data = resp.json()
    assert "webhook_processing_errors_total" not in data
    assert "merged_prs_since_last_trigger" not in data
    assert "trigger_events_total" not in data
    assert "prs_per_trigger_cycle" not in data


def test_sessions_empty_initially(test_client: TestClient) -> None:
    resp = test_client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json() == []
