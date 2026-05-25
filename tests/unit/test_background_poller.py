"""Tests for the background polling logic."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.observability import MetricsCollector
from app.scan_manager import ScanManager


@pytest.fixture
def mock_devin() -> AsyncMock:
    client = AsyncMock(spec=DevinClient)
    client.get_session_status.return_value = {"status_enum": "working"}
    client.trigger_fix_session.return_value = {"session_id": "devin-fix-1"}
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


@pytest.mark.asyncio
async def test_get_active_scans_excludes_local(manager: ScanManager) -> None:
    """Local npm scans (devin_session_id='local') are excluded from active scans."""
    scan_id = manager.create_scan_record()
    active = manager.get_active_scans()
    assert len(active) == 0  # local scans are excluded


@pytest.mark.asyncio
async def test_poll_and_update_scan_skips_local(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """poll_and_update_scan is a no-op for local npm scans."""
    scan_id = manager.create_scan_record()
    await manager.poll_and_update_scan(scan_id)
    mock_devin.get_session_status.assert_not_called()


@pytest.mark.asyncio
async def test_poller_respects_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The poller sleeps for POLL_INTERVAL_SECONDS between cycles."""
    sleep_calls: list[float] = []

    async def mock_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()  # Stop loop after first sleep

    mock_settings = MagicMock()
    mock_settings.POLL_INTERVAL_SECONDS = 42

    mock_scan_mgr = MagicMock()
    mock_scan_mgr.get_active_scans.return_value = []

    with (
        patch("app.main._scan_manager", mock_scan_mgr),
        patch("app.main._settings", mock_settings),
        patch("asyncio.sleep", mock_sleep),
    ):
        from app.main import _background_poller

        with pytest.raises(asyncio.CancelledError):
            await _background_poller()

    assert sleep_calls == [42]


@pytest.mark.asyncio
async def test_poller_task_cancelled_on_shutdown() -> None:
    """On app shutdown, the poller task is cleanly cancelled."""
    task_started = asyncio.Event()
    task_cancelled = False

    async def fake_poller() -> None:
        nonlocal task_cancelled
        task_started.set()
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            task_cancelled = True
            raise

    task = asyncio.create_task(fake_poller())
    await task_started.wait()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert task_cancelled is True
    assert task.cancelled()


@pytest.mark.asyncio
async def test_poller_does_nothing_when_no_active_scans(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """When there are no active scans (excluding local), no API calls are made."""
    mock_devin.get_session_status.reset_mock()

    active = manager.get_active_scans()
    assert len(active) == 0

    mock_devin.get_session_status.assert_not_called()


@pytest.mark.asyncio
async def test_get_scan_results_reads_from_db_without_poll(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """get_scan_results with on_demand_poll=False only reads DB."""
    scan_id = manager.create_scan_record()
    mock_devin.get_session_status.reset_mock()

    result = await manager.get_scan_results(scan_id, on_demand_poll=False)
    assert result["status"] == "in_progress"
    mock_devin.get_session_status.assert_not_called()


@pytest.mark.asyncio
async def test_local_scan_skips_on_demand_poll(
    manager: ScanManager, mock_devin: AsyncMock
) -> None:
    """get_scan_results with on_demand_poll=True skips polling for local scans."""
    scan_id = manager.create_scan_record()
    mock_devin.get_session_status.reset_mock()

    result = await manager.get_scan_results(scan_id, on_demand_poll=True)
    assert result["status"] == "in_progress"
    mock_devin.get_session_status.assert_not_called()


@pytest.mark.asyncio
async def test_poller_calls_poll_and_update_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poller calls poll_and_update_filter for scans with status 'filtering'."""
    poll_calls: list[str] = []

    async def mock_sleep(seconds: float) -> None:
        raise asyncio.CancelledError()

    mock_settings = MagicMock()
    mock_settings.POLL_INTERVAL_SECONDS = 30

    mock_scan_mgr = MagicMock()
    mock_scan_mgr.get_active_scans.return_value = [
        {"scan_id": "scan-filter-1", "filter_session_id": "fs-1"}
    ]

    async def fake_poll(scan_id: str) -> None:
        poll_calls.append(scan_id)

    mock_scan_mgr.poll_and_update_filter = fake_poll

    with (
        patch("app.main._scan_manager", mock_scan_mgr),
        patch("app.main._settings", mock_settings),
        patch("asyncio.sleep", mock_sleep),
    ):
        from app.main import _background_poller

        with pytest.raises(asyncio.CancelledError):
            await _background_poller()

    assert poll_calls == ["scan-filter-1"]


@pytest.mark.asyncio
async def test_poller_calls_poll_and_update_scan_for_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poller calls poll_and_update_scan for scans without filter_session_id."""
    scan_poll_calls: list[str] = []

    async def mock_sleep(seconds: float) -> None:
        raise asyncio.CancelledError()

    mock_settings = MagicMock()
    mock_settings.POLL_INTERVAL_SECONDS = 30

    mock_scan_mgr = MagicMock()
    mock_scan_mgr.get_active_scans.return_value = [
        {"scan_id": "scan-ip-1", "filter_session_id": None, "status": "in_progress"}
    ]

    async def fake_poll_scan(scan_id: str) -> None:
        scan_poll_calls.append(scan_id)

    mock_scan_mgr.poll_and_update_scan = fake_poll_scan
    mock_scan_mgr.poll_and_update_filter = AsyncMock()

    with (
        patch("app.main._scan_manager", mock_scan_mgr),
        patch("app.main._settings", mock_settings),
        patch("asyncio.sleep", mock_sleep),
    ):
        from app.main import _background_poller

        with pytest.raises(asyncio.CancelledError):
            await _background_poller()

    assert scan_poll_calls == ["scan-ip-1"]
    mock_scan_mgr.poll_and_update_filter.assert_not_called()
