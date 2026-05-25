"""Tests for app.webhook_handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import DevinSession
from app.pr_counter import PRCounter
from app.webhook_handler import handle_webhook, validate_signature
from tests.conftest import _make_pr_payload


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


def test_valid_signature() -> None:
    import hashlib, hmac, json

    body = b'{"hello": "world"}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert validate_signature(body, "secret", sig)


def test_invalid_signature() -> None:
    assert not validate_signature(b"body", "secret", "sha256=bad")


def test_empty_signature() -> None:
    assert not validate_signature(b"body", "secret", "")


# ---------------------------------------------------------------------------
# handle_webhook
# ---------------------------------------------------------------------------


@pytest.fixture
def counter() -> PRCounter:
    return PRCounter(":memory:")


@pytest.fixture
def mock_devin() -> AsyncMock:
    client = AsyncMock()
    from datetime import datetime, timezone

    client.trigger_all_dependency_updates.return_value = [
        DevinSession(
            session_id=f"s-{i}",
            prompt_type=f"type-{i}",
            idempotency_key=f"key-{i}",
            created_at=datetime.now(timezone.utc),
            status="running",
        )
        for i in range(4)
    ]
    return client


@pytest.mark.asyncio
async def test_merged_pr_increments_counter(counter: PRCounter, mock_devin: AsyncMock) -> None:
    payload = _make_pr_payload(pr_number=1)
    result = await handle_webhook(payload, counter, mock_devin, threshold=10, correlation_id="c1")
    assert result["action"] == "counted"
    assert result["current_count"] == 1


@pytest.mark.asyncio
async def test_non_merged_pr_ignored(counter: PRCounter, mock_devin: AsyncMock) -> None:
    payload = _make_pr_payload(merged=False)
    result = await handle_webhook(payload, counter, mock_devin, threshold=10, correlation_id="c2")
    assert result["action"] == "ignored"
    assert result["reason"] == "not_merged"


@pytest.mark.asyncio
async def test_non_main_branch_ignored(counter: PRCounter, mock_devin: AsyncMock) -> None:
    payload = _make_pr_payload(base_ref="develop")
    result = await handle_webhook(payload, counter, mock_devin, threshold=10, correlation_id="c3")
    assert result["action"] == "ignored"
    assert result["reason"] == "not_main_branch"


@pytest.mark.asyncio
async def test_threshold_triggers_sessions(counter: PRCounter, mock_devin: AsyncMock) -> None:
    for i in range(1, 4):
        payload = _make_pr_payload(pr_number=i, merged_at=f"2025-06-0{i}T00:00:00Z")
        result = await handle_webhook(payload, counter, mock_devin, threshold=3, correlation_id=f"c{i}")

    assert result["action"] == "triggered"
    assert len(result["sessions"]) == 4
    mock_devin.trigger_all_dependency_updates.assert_awaited_once()
    # Counter should be reset
    assert counter.get_count() == 0


@pytest.mark.asyncio
async def test_malformed_payload(counter: PRCounter, mock_devin: AsyncMock) -> None:
    result = await handle_webhook({"bad": "data"}, counter, mock_devin, threshold=10, correlation_id="cx")
    assert result["action"] == "ignored"
    assert result["reason"] == "malformed_payload"


@pytest.mark.asyncio
async def test_missing_fields_handled(counter: PRCounter, mock_devin: AsyncMock) -> None:
    result = await handle_webhook(
        {"action": "closed", "pull_request": {}},
        counter, mock_devin, threshold=10, correlation_id="cy",
    )
    assert result["action"] == "ignored"
