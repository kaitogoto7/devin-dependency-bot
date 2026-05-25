"""Tests for app.scan_manager."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.observability import MetricsCollector
from app.scan_manager import ScanManager

from tests.conftest import (
    SAMPLE_AUDIT_OUTPUT,
    SAMPLE_OUTDATED_OUTPUT,
    SAMPLE_OUTDATED_OUTPUT_WORKSPACES,
)


@pytest.fixture
def mock_devin() -> AsyncMock:
    client = AsyncMock(spec=DevinClient)
    client.trigger_fix_session.return_value = {"session_id": "s-fix-1"}
    client.create_session.return_value = {"session_id": "s-filter-1"}
    return client


@pytest.fixture
def mock_github() -> AsyncMock:
    client = AsyncMock(spec=GitHubClient)
    client.create_issue.return_value = {
        "url": "https://github.com/kaitogoto7/superset/issues/101",
        "number": "101",
    }
    return client


@pytest.fixture
def manager(
    mock_devin: AsyncMock, mock_github: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> ScanManager:
    fresh = MetricsCollector()
    monkeypatch.setattr("app.scan_manager.metrics", fresh)
    return ScanManager(
        ":memory:", mock_devin, mock_github, frontend_dir="/tmp/fake-frontend", top_k=3
    )


# ---------------------------------------------------------------------------
# _init_db schema migration
# ---------------------------------------------------------------------------


def test_init_db_migrates_missing_columns(
    mock_devin: AsyncMock,
    mock_github: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_init_db() adds filter_session_id and recommended to a pre-existing DB."""
    fresh = MetricsCollector()
    monkeypatch.setattr("app.scan_manager.metrics", fresh)

    # 1. Create the first manager (creates tables with latest schema).
    mgr1 = ScanManager(
        ":memory:",
        mock_devin,
        mock_github,
        frontend_dir="/tmp/fake-frontend",
        top_k=3,
    )

    # 2. Simulate a pre-migration database by recreating tables
    #    without the new columns.
    conn = mgr1._conn
    conn.execute("DROP TABLE IF EXISTS scan_issues")
    conn.execute("DROP TABLE IF EXISTS scans")
    conn.execute(
        """
        CREATE TABLE scans (
            scan_id TEXT PRIMARY KEY,
            devin_session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            raw_output TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE scan_issues (
            issue_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            category TEXT NOT NULL,
            package TEXT NOT NULL,
            current_version TEXT NOT NULL,
            fixed_version TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT,
            advisory_id TEXT,
            selected INTEGER NOT NULL DEFAULT 0,
            github_issue_url TEXT,
            fix_session_id TEXT,
            FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
        )
        """
    )
    conn.commit()

    # Verify the new columns do NOT exist yet.
    col_names = {row[1] for row in conn.execute("PRAGMA table_info(scans)").fetchall()}
    assert "filter_session_id" not in col_names

    col_names_issues = {
        row[1] for row in conn.execute("PRAGMA table_info(scan_issues)").fetchall()
    }
    assert "recommended" not in col_names_issues

    # 3. Create a new ScanManager on the same connection's DB.
    #    We reuse the file-backed path trick: write to a temp file.
    #    But since we already have an open :memory: connection, we
    #    can just call _init_db() directly to trigger the migration.
    mgr1._init_db()

    # 4. Assert both columns now exist.
    conn.execute("SELECT filter_session_id FROM scans LIMIT 0")
    conn.execute("SELECT recommended FROM scan_issues LIMIT 0")

    # Also verify idempotency — calling _init_db() again should not raise.
    mgr1._init_db()

    mgr1.close()


# ---------------------------------------------------------------------------
# create_scan_record
# ---------------------------------------------------------------------------


def test_create_scan_record_returns_scan_id(manager: ScanManager) -> None:
    scan_id = manager.create_scan_record()
    assert isinstance(scan_id, str)
    assert len(scan_id) == 12


def test_create_scan_record_inserts_local_session(manager: ScanManager) -> None:
    scan_id = manager.create_scan_record()
    row = manager._conn.execute(
        "SELECT devin_session_id FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["devin_session_id"] == "local"


# ---------------------------------------------------------------------------
# run_scan — mocking npm commands
# ---------------------------------------------------------------------------


def _mock_npm_factory(audit_data: dict, outdated_data: dict):
    """Return an async side_effect for _run_npm_command."""
    call_count = 0

    async def side_effect(args: list[str]) -> tuple[str, str, int]:
        nonlocal call_count
        call_count += 1
        if args == ["install"]:
            return "", "", 0
        if args == ["audit", "--json"]:
            return json.dumps(audit_data), "", 1
        if args == ["outdated", "--json"]:
            return json.dumps(outdated_data), "", 1
        return "", "", 0

    return side_effect


@pytest.mark.asyncio
async def test_run_scan_stores_issues(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # With issues found, scan transitions to 'filtering' (not 'completed')
    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "filtering"

    # Verify issues were stored in the DB
    rows = manager._conn.execute(
        "SELECT * FROM scan_issues WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    # nth-check (direct) + postcss (transitive) + lodash (outdated, current!=wanted)
    # react is skipped (current==wanted), @superset-ui/core is skipped (file: reference)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_run_scan_marks_filtering_when_issues_found(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT status, filter_session_id FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "filtering"
    assert row["filter_session_id"] == "s-filter-1"


@pytest.mark.asyncio
async def test_run_scan_stores_raw_output(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT raw_output FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    raw = json.loads(row["raw_output"])
    assert "audit" in raw
    assert "outdated" in raw


@pytest.mark.asyncio
async def test_run_scan_handles_failure(manager: ScanManager) -> None:
    async def failing_npm(args: list[str]) -> tuple[str, str, int]:
        raise RuntimeError("npm not found")

    manager._run_npm_command = AsyncMock(side_effect=failing_npm)
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT status FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_run_scan_empty_audit_and_outdated(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(side_effect=_mock_npm_factory({}, {}))
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "completed"
    assert result["issues"] == []


@pytest.mark.asyncio
async def test_run_scan_completes_immediately_when_no_issues(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(side_effect=_mock_npm_factory({}, {}))
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT status, completed_at FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None
    mock_devin.create_session.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_npm_audit
# ---------------------------------------------------------------------------


def test_parse_npm_audit_direct_advisory(manager: ScanManager) -> None:
    issues = manager._parse_npm_audit("scan-1", SAMPLE_AUDIT_OUTPUT)
    direct = [i for i in issues if i.package == "nth-check"]
    assert len(direct) == 1
    assert direct[0].category == "frontend_security"
    assert direct[0].severity == "high"
    assert "GHSA" in (direct[0].advisory_id or "")


def test_parse_npm_audit_transitive(manager: ScanManager) -> None:
    issues = manager._parse_npm_audit("scan-1", SAMPLE_AUDIT_OUTPUT)
    transitive = [i for i in issues if i.package == "postcss"]
    assert len(transitive) == 1
    assert "Transitive" in transitive[0].description


def test_parse_npm_audit_empty(manager: ScanManager) -> None:
    issues = manager._parse_npm_audit("scan-1", {})
    assert issues == []


def test_parse_npm_audit_no_vulnerabilities_key(manager: ScanManager) -> None:
    issues = manager._parse_npm_audit("scan-1", {"metadata": {}})
    assert issues == []


# ---------------------------------------------------------------------------
# _parse_npm_outdated
# ---------------------------------------------------------------------------


def test_parse_npm_outdated_basic(manager: ScanManager) -> None:
    issues = manager._parse_npm_outdated("scan-1", SAMPLE_OUTDATED_OUTPUT)
    pkgs = {i.package for i in issues}
    assert "lodash" in pkgs
    assert (
        len(issues) == 1
    )  # only lodash (react: current==wanted, @superset-ui/core: file:)


def test_parse_npm_outdated_skips_current_equals_wanted(manager: ScanManager) -> None:
    data = {
        "react": {
            "current": "18.2.0",
            "wanted": "18.2.0",
            "latest": "18.3.1",
        },
    }
    issues = manager._parse_npm_outdated("scan-1", data)
    assert issues == []


def test_parse_npm_outdated_skips_file_references(manager: ScanManager) -> None:
    data = {
        "@superset-ui/core": {
            "current": "0.20.0",
            "wanted": "0.20.1",
            "latest": "0.20.1",
            "resolved": "file:packages/superset-ui-core",
        },
    }
    issues = manager._parse_npm_outdated("scan-1", data)
    assert issues == []


def test_parse_npm_outdated_category_is_frontend_general(manager: ScanManager) -> None:
    data = {
        "lodash": {
            "current": "4.17.20",
            "wanted": "4.17.21",
            "latest": "4.17.21",
        },
    }
    issues = manager._parse_npm_outdated("scan-1", data)
    assert len(issues) == 1
    assert issues[0].category == "frontend_general"
    assert issues[0].fixed_version == "4.17.21"


def test_parse_npm_outdated_empty(manager: ScanManager) -> None:
    issues = manager._parse_npm_outdated("scan-1", {})
    assert issues == []


def test_parse_npm_outdated_workspace_list_format(manager: ScanManager) -> None:
    issues = manager._parse_npm_outdated("scan-1", SAMPLE_OUTDATED_OUTPUT_WORKSPACES)
    pkgs = {i.package for i in issues}
    assert "lodash" in pkgs
    assert "axios" in pkgs
    assert len(issues) == 2
    lodash_issues = [i for i in issues if i.package == "lodash"]
    assert len(lodash_issues) == 1  # deduplicated across workspaces


def test_parse_npm_outdated_mixed_types(manager: ScanManager) -> None:
    data = {
        "bad-string": "not-a-dict",
        "bad-none": None,
        "valid": {"current": "1.0.0", "wanted": "1.0.1", "latest": "2.0.0"},
    }
    issues = manager._parse_npm_outdated("scan-1", data)
    assert len(issues) == 1
    assert issues[0].package == "valid"


def test_parse_npm_audit_skips_non_dict_vuln_info(manager: ScanManager) -> None:
    data = {"vulnerabilities": {"bad-pkg": "not-a-dict", "also-bad": ["a", "b"]}}
    issues = manager._parse_npm_audit("scan-1", data)
    assert issues == []


# ---------------------------------------------------------------------------
# get_scan_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scan_results_not_found(manager: ScanManager) -> None:
    result = await manager.get_scan_results("nonexistent")
    assert result["error"] == "scan_not_found"


@pytest.mark.asyncio
async def test_get_scan_results_in_progress(manager: ScanManager) -> None:
    scan_id = manager.create_scan_record()
    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "in_progress"


@pytest.mark.asyncio
async def test_get_scan_results_completed(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # After run_scan with issues, status is 'filtering'
    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "filtering"

    # Manually complete the scan to test the completed response
    manager._conn.execute(
        "UPDATE scans SET status = 'completed',"
        " completed_at = '2025-01-01T00:00:00Z'"
        " WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()
    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "completed"
    assert result["parse_status"] == "success"
    assert len(result["issues"]) == 3


# ---------------------------------------------------------------------------
# select_and_dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_and_dispatch_creates_issues(
    manager: ScanManager, mock_devin: AsyncMock, mock_github: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # Manually complete the scan (run_scan leaves it in 'filtering')
    manager._conn.execute(
        "UPDATE scans SET status = 'completed',"
        " completed_at = '2025-01-01T00:00:00Z'"
        " WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    results = await manager.get_scan_results(scan_id, on_demand_poll=False)
    issue_ids = [i["issue_id"] for i in results["issues"][:2]]

    dispatch_result = await manager.select_and_dispatch(scan_id, issue_ids)
    assert len(dispatch_result["github_issues"]) == 2
    assert len(dispatch_result["fix_sessions"]) == 2
    assert mock_github.create_issue.await_count == 2
    assert mock_devin.trigger_fix_session.await_count == 2


@pytest.mark.asyncio
async def test_select_and_dispatch_invalid_issue_ids(manager: ScanManager) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # Manually complete the scan
    manager._conn.execute(
        "UPDATE scans SET status = 'completed',"
        " completed_at = '2025-01-01T00:00:00Z'"
        " WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    result = await manager.select_and_dispatch(scan_id, ["nonexistent"])
    assert result["error"] == "invalid_issue_ids"
    assert "nonexistent" in result["invalid_ids"]


@pytest.mark.asyncio
async def test_select_and_dispatch_scan_not_found(manager: ScanManager) -> None:
    result = await manager.select_and_dispatch("nonexistent", ["id1"])
    assert result["error"] == "scan_not_found"


@pytest.mark.asyncio
async def test_duplicate_scans_coexist(manager: ScanManager) -> None:
    scan_id_1 = manager.create_scan_record()
    scan_id_2 = manager.create_scan_record()
    assert scan_id_1 != scan_id_2


# ---------------------------------------------------------------------------
# run_scan — filter session triggering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scan_triggers_filter_session_when_issues_found(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT status, filter_session_id FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "filtering"
    assert row["filter_session_id"] == "s-filter-1"
    mock_devin.create_session.assert_called_once()


@pytest.mark.asyncio
async def test_run_scan_fallback_when_filter_session_fails(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    mock_devin.create_session.side_effect = RuntimeError("Devin API error")
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    row = manager._conn.execute(
        "SELECT status FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "completed"

    rows = manager._conn.execute(
        "SELECT recommended FROM scan_issues WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    assert all(r["recommended"] == 1 for r in rows)


# ---------------------------------------------------------------------------
# poll_and_update_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_and_update_filter_marks_recommended(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # Get issue IDs from DB
    issue_rows = manager._conn.execute(
        "SELECT issue_id FROM scan_issues WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    issue_ids = [r["issue_id"] for r in issue_rows]

    # Mock the Devin API to return a finished filter session
    selected_ids = issue_ids[:2]
    mock_devin.get_session_status.return_value = {"status_enum": "finished"}
    mock_devin._base_url = "https://api.devin.ai/v1"
    mock_devin._client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "messages": [
            {
                "type": "devin_message",
                "message": json.dumps({"selected_issue_ids": selected_ids}),
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()
    mock_devin._client.get.return_value = mock_response

    await manager.poll_and_update_filter(scan_id)

    row = manager._conn.execute(
        "SELECT status FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "completed"

    recommended_rows = manager._conn.execute(
        "SELECT issue_id FROM scan_issues WHERE scan_id = ? AND recommended = 1",
        (scan_id,),
    ).fetchall()
    assert len(recommended_rows) == 2
    recommended_set = {r["issue_id"] for r in recommended_rows}
    assert recommended_set == set(selected_ids)


@pytest.mark.asyncio
async def test_poll_and_update_filter_fallback_on_empty_output(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    mock_devin.get_session_status.return_value = {"status_enum": "finished"}
    mock_devin._base_url = "https://api.devin.ai/v1"
    mock_devin._client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"messages": []}
    mock_response.raise_for_status = MagicMock()
    mock_devin._client.get.return_value = mock_response

    await manager.poll_and_update_filter(scan_id)

    rows = manager._conn.execute(
        "SELECT recommended FROM scan_issues WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    assert all(r["recommended"] == 1 for r in rows)


# ---------------------------------------------------------------------------
# _parse_filter_output
# ---------------------------------------------------------------------------


def test_parse_filter_output_valid_json(manager: ScanManager) -> None:
    output = '{"selected_issue_ids": ["id1", "id2", "id3"]}'
    result = manager._parse_filter_output(output, "scan-1")
    assert result == ["id1", "id2", "id3"]


def test_parse_filter_output_markdown_wrapped(manager: ScanManager) -> None:
    output = (
        'Here are the results:\n```json\n{"selected_issue_ids": ["a", "b"]}\n```\nDone.'
    )
    result = manager._parse_filter_output(output, "scan-1")
    assert result == ["a", "b"]


def test_parse_filter_output_empty(manager: ScanManager) -> None:
    result = manager._parse_filter_output("", "scan-1")
    assert result == []


# ---------------------------------------------------------------------------
# get_scan_results — filtering status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scan_results_returns_filtering_status(manager: ScanManager) -> None:
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "filtering"


@pytest.mark.asyncio
async def test_get_scan_results_filtering_triggers_on_demand_poll(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """On-demand poll transitions a filtering scan to completed when the filter session is terminal."""
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # Verify scan is in filtering status
    row = manager._conn.execute(
        "SELECT status, filter_session_id FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "filtering"

    # Mock filter session as finished with recommendations
    mock_devin.get_session_status.return_value = {"status_enum": "finished"}
    mock_devin._base_url = "https://api.devin.ai/v1"
    mock_devin._client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"messages": []}
    mock_response.raise_for_status = MagicMock()
    mock_devin._client.get.return_value = mock_response

    result = await manager.get_scan_results(scan_id, on_demand_poll=True)
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_get_scan_results_filtering_no_poll_when_disabled(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """With on_demand_poll=False, filtering scans are returned as-is."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "filtering"
    mock_devin.get_session_status.assert_not_called()


@pytest.mark.asyncio
async def test_poll_and_update_filter_completes_on_finished(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """A filtering scan transitions to completed when the filter session is finished."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.execute(
        "INSERT INTO scan_issues (issue_id, scan_id, category, package, current_version, fixed_version, description) "
        "VALUES ('i1', ?, 'frontend_security', 'pkg', '1.0', '2.0', 'desc')",
        (scan_id,),
    )
    manager._conn.commit()

    mock_devin.get_session_status.return_value = {"status_enum": "finished"}
    mock_devin._base_url = "https://api.devin.ai/v1"
    mock_devin._client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"messages": []}
    mock_response.raise_for_status = MagicMock()
    mock_devin._client.get.return_value = mock_response

    await manager.poll_and_update_filter(scan_id)

    row = manager._conn.execute(
        "SELECT status FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_poll_and_update_filter_completes_on_blocked(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """A filtering scan transitions to completed when the filter session is blocked."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.execute(
        "INSERT INTO scan_issues (issue_id, scan_id, category, package, current_version, fixed_version, description) "
        "VALUES ('i1', ?, 'frontend_security', 'pkg', '1.0', '2.0', 'desc')",
        (scan_id,),
    )
    manager._conn.commit()

    mock_devin.get_session_status.return_value = {"status_enum": "blocked"}

    await manager.poll_and_update_filter(scan_id)

    row = manager._conn.execute(
        "SELECT status, completed_at FROM scans WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None

    # blocked sessions can't be parsed, so all issues should be marked as recommended
    recs = manager._conn.execute(
        "SELECT recommended FROM scan_issues WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    assert all(r["recommended"] == 1 for r in recs)


@pytest.mark.asyncio
async def test_get_active_scans_includes_filtering(manager: ScanManager) -> None:
    """get_active_scans returns scans with status 'filtering'."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1',"
        " devin_session_id = 'devin-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    active = manager.get_active_scans()
    assert len(active) == 1
    assert active[0]["scan_id"] == scan_id
    assert active[0]["filter_session_id"] == "fs-1"


@pytest.mark.asyncio
async def test_get_scan_results_returns_actual_status(manager: ScanManager) -> None:
    """The API response returns the actual scan status, not hardcoded in_progress."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "filtering"
    assert result["scan_id"] == scan_id


@pytest.mark.asyncio
async def test_get_scan_results_filtering_poll_failure_returns_current_status(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """If the on-demand filter poll raises, the endpoint still returns the current status."""
    scan_id = manager.create_scan_record()
    manager._conn.execute(
        "UPDATE scans SET status = 'filtering', filter_session_id = 'fs-1' WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    mock_devin.get_session_status.side_effect = RuntimeError("API unreachable")

    result = await manager.get_scan_results(scan_id, on_demand_poll=True)
    assert result["status"] == "filtering"
    assert result["scan_id"] == scan_id


# ---------------------------------------------------------------------------
# select_and_dispatch — devin-issue label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_and_dispatch_creates_issues_with_devin_issue_label(
    manager: ScanManager, mock_devin: AsyncMock, mock_github: AsyncMock
) -> None:
    manager._run_npm_command = AsyncMock(
        side_effect=_mock_npm_factory(SAMPLE_AUDIT_OUTPUT, SAMPLE_OUTDATED_OUTPUT)
    )
    scan_id = manager.create_scan_record()
    await manager.run_scan(scan_id)

    # Manually complete the scan
    manager._conn.execute(
        "UPDATE scans SET status = 'completed',"
        " completed_at = '2025-01-01T00:00:00Z'"
        " WHERE scan_id = ?",
        (scan_id,),
    )
    manager._conn.commit()

    results = await manager.get_scan_results(scan_id, on_demand_poll=False)
    issue_ids = [i["issue_id"] for i in results["issues"][:1]]

    await manager.select_and_dispatch(scan_id, issue_ids)

    # Verify the labels include devin-issue
    call_args = mock_github.create_issue.call_args
    labels = (
        call_args.args[2]
        if len(call_args.args) > 2
        else call_args.kwargs.get("labels", [])
    )
    assert "devin-issue" in labels
    assert "devin-dependency-bot" in labels
