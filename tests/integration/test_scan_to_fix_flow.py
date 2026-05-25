"""Integration tests: end-to-end scan → select → fix flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_scan_trigger_returns_scan_id(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.create_scan_record.return_value = "abc123def456"
        mock_mgr.run_scan = AsyncMock()
        resp = test_client.post("/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == "abc123def456"
        assert data["status"] == "in_progress"


def test_scan_results_not_found(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.get_scan_results = AsyncMock(return_value={"error": "scan_not_found"})
        resp = test_client.get("/scan/nonexistent")
        assert resp.status_code == 404


def test_scan_results_in_progress(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.get_scan_results = AsyncMock(
            return_value={"status": "in_progress", "scan_id": "abc123"}
        )
        resp = test_client.get("/scan/abc123")
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"


def test_scan_results_completed(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.get_scan_results = AsyncMock(
            return_value={
                "status": "completed",
                "scan_id": "abc123",
                "issues": [
                    {
                        "issue_id": "i1",
                        "category": "frontend_security",
                        "package": "nth-check",
                    },
                ],
            }
        )
        resp = test_client.get("/scan/abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["issues"]) == 1


def test_select_issues_returns_results(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.select_and_dispatch = AsyncMock(
            return_value={
                "github_issues": [
                    {
                        "url": "https://github.com/kaitogoto7/superset/issues/42",
                        "number": "42",
                    }
                ],
                "fix_sessions": [{"issue_id": "i1", "session_id": "s-fix-1"}],
            }
        )
        resp = test_client.post(
            "/scan/abc123/select",
            json={"issue_ids": ["i1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["github_issues"]) == 1
        assert len(data["fix_sessions"]) == 1


def test_select_zero_issues(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.select_and_dispatch = AsyncMock(
            return_value={
                "github_issues": [],
                "fix_sessions": [],
            }
        )
        resp = test_client.post(
            "/scan/abc123/select",
            json={"issue_ids": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["github_issues"] == []


def test_select_scan_not_found(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.select_and_dispatch = AsyncMock(
            return_value={"error": "scan_not_found"}
        )
        resp = test_client.post(
            "/scan/nonexistent/select",
            json={"issue_ids": ["i1"]},
        )
        assert resp.status_code == 404


def test_select_scan_not_completed(test_client: TestClient) -> None:
    with patch("app.main._scan_manager") as mock_mgr:
        mock_mgr.select_and_dispatch = AsyncMock(
            return_value={"error": "scan_not_completed"}
        )
        resp = test_client.post(
            "/scan/abc123/select",
            json={"issue_ids": ["i1"]},
        )
        assert resp.status_code == 400


def test_multiple_scans_coexist(test_client: TestClient) -> None:
    call_count = 0
    with patch("app.main._scan_manager") as mock_mgr:

        def mock_create_scan_record():
            nonlocal call_count
            call_count += 1
            return f"scan_{call_count}"

        mock_mgr.create_scan_record = mock_create_scan_record
        mock_mgr.run_scan = AsyncMock()
        resp1 = test_client.post("/scan")
        resp2 = test_client.post("/scan")
        assert resp1.json()["scan_id"] != resp2.json()["scan_id"]
