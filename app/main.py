"""FastAPI application entrypoint — scan, select, health, metrics, sessions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException

from app.config import get_settings, Settings
from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.models import SelectionRequest
from app.observability import get_logger, metrics, setup_logging
from app.scan_manager import ScanManager

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Application state (populated during lifespan)
# ---------------------------------------------------------------------------
_settings: Settings | None = None
_devin_client: DevinClient | None = None
_github_client: GitHubClient | None = None
_scan_manager: ScanManager | None = None
_sessions_store: list[dict[str, Any]] = []
_poller_task: asyncio.Task | None = None  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# Background poller (fix sessions only)
# ---------------------------------------------------------------------------


async def _background_poller() -> None:
    """Independent background loop that polls active Devin filter sessions.

    Local npm scans complete synchronously via ``run_scan()``.
    This poller handles filter sessions in 'filtering' status.
    """
    assert _scan_manager is not None
    assert _settings is not None

    while True:
        try:
            active_scans = _scan_manager.get_active_scans()
            if active_scans:
                logger.info(
                    "poller.cycle",
                    extra={
                        "event_type": "poller.cycle",
                        "active_scans": len(active_scans),
                    },
                )
                for scan in active_scans:
                    try:
                        if scan.get("filter_session_id"):
                            await _scan_manager.poll_and_update_filter(scan["scan_id"])
                        else:
                            await _scan_manager.poll_and_update_scan(scan["scan_id"])
                    except Exception as e:
                        logger.error(
                            "poller.scan_error",
                            extra={
                                "event_type": "poller.scan_error",
                                "scan_id": scan["scan_id"],
                                "error": str(e),
                            },
                        )
        except Exception as e:
            logger.error(
                "poller.cycle_error",
                extra={
                    "event_type": "poller.cycle_error",
                    "error": str(e),
                },
            )
        await asyncio.sleep(_settings.POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _settings, _devin_client, _github_client, _scan_manager, _poller_task

    _settings = get_settings()
    setup_logging(_settings.LOG_LEVEL)

    _devin_client = DevinClient(_settings.DEVIN_API_TOKEN, _settings.DEVIN_API_BASE_URL)
    _github_client = GitHubClient(_settings.GITHUB_TOKEN, _settings.GITHUB_REPO)
    _scan_manager = ScanManager(
        _settings.DB_PATH,
        _devin_client,
        _github_client,
        frontend_dir=_settings.FRONTEND_DIR,
        top_k=_settings.TOP_K_ISSUES,
    )

    _poller_task = asyncio.create_task(_background_poller())

    logger.info("Bot started", extra={"event_type": "startup"})
    yield

    _poller_task.cancel()
    try:
        await _poller_task
    except asyncio.CancelledError:
        pass
    _scan_manager.close()
    await _github_client.close()
    await _devin_client.close()
    logger.info("Bot stopped", extra={"event_type": "shutdown"})


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Devin Dependency Bot", lifespan=lifespan)


@app.post("/scan")
async def trigger_scan() -> dict[str, Any]:
    """Trigger a local npm dependency scan (runs in background)."""
    assert _scan_manager is not None

    try:
        scan_id = _scan_manager.create_scan_record()
        asyncio.create_task(_scan_manager.run_scan(scan_id))
    except Exception as exc:
        logger.error(
            "Scan trigger failed",
            extra={"event_type": "error.scan_trigger", "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to trigger scan")

    return {"scan_id": scan_id, "status": "in_progress"}


@app.get("/scan/{scan_id}")
async def get_scan(scan_id: str) -> dict[str, Any]:
    """Return the results of a scan."""
    assert _scan_manager is not None

    result = await _scan_manager.get_scan_results(scan_id)
    if result.get("error") == "scan_not_found":
        raise HTTPException(status_code=404, detail="Scan not found")
    return result


@app.post("/scan/{scan_id}/select")
async def select_issues(scan_id: str, body: SelectionRequest) -> dict[str, Any]:
    """Select issues to fix — posts GitHub issues and triggers Devin fix sessions."""
    assert _scan_manager is not None

    result = await _scan_manager.select_and_dispatch(scan_id, body.issue_ids)
    if result.get("error") == "scan_not_found":
        raise HTTPException(status_code=404, detail="Scan not found")
    if result.get("error") == "scan_not_completed":
        raise HTTPException(status_code=400, detail="Scan not yet completed")
    if result.get("error") == "invalid_issue_ids":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid issue IDs: {result['invalid_ids']}",
        )
    return result


@app.get("/health")
async def health() -> dict[str, Any]:
    """Return service health and uptime."""
    assert _scan_manager is not None
    db_ok = True
    try:
        _scan_manager.get_last_scan_time()
    except Exception:
        db_ok = False

    status = metrics.health(
        db_connected=db_ok,
        last_scan=_scan_manager.get_last_scan_time() if db_ok else None,
    )
    return status.model_dump(mode="json")


@app.get("/metrics")
async def metrics_endpoint() -> dict[str, Any]:
    """Return observability metrics."""
    snap = metrics.snapshot()
    return snap.model_dump(mode="json")


@app.get("/sessions")
async def sessions_endpoint() -> list[dict[str, Any]]:
    """Return all triggered Devin sessions."""
    return list(_sessions_store)
