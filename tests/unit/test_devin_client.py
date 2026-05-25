"""Tests for app.devin_client."""

from __future__ import annotations

import pytest
import httpx
import respx

from app.devin_client import DevinAPIError, DevinClient, DevinTransientError
from app.observability import MetricsCollector


@pytest.fixture
def client() -> DevinClient:
    return DevinClient(api_token="test-token", base_url="https://api.devin.ai/v1")


@pytest.fixture(autouse=True)
def _reset_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the global metrics singleton with a fresh instance per test."""
    fresh = MetricsCollector()
    monkeypatch.setattr("app.devin_client.metrics", fresh)


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_session_success(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "s-123"})
    )
    result = await client.create_session("do stuff", "key-20250101")
    assert result["session_id"] == "s-123"


@respx.mock
@pytest.mark.asyncio
async def test_create_session_401_raises_api_error(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    with pytest.raises(DevinAPIError, match="Authentication"):
        await client.create_session("p", "k")


@respx.mock
@pytest.mark.asyncio
async def test_create_session_429_retries(client: DevinClient) -> None:
    route = respx.post("https://api.devin.ai/v1/sessions")
    route.side_effect = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"session_id": "s-retry"}),
    ]
    result = await client.create_session("p", "k")
    assert result["session_id"] == "s-retry"
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_create_session_500_retries_then_fails(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(DevinTransientError):
        await client.create_session("p", "k")


@respx.mock
@pytest.mark.asyncio
async def test_create_session_timeout(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(DevinTransientError, match="Timeout"):
        await client.create_session("p", "k")


@respx.mock
@pytest.mark.asyncio
async def test_idempotent_flag_sent(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "s-1"})
    )
    await client.create_session("prompt", "fix-pkg-20250601")
    req = respx.calls[0].request
    import json
    body = json.loads(req.content)
    assert body["idempotent"] is True


@respx.mock
@pytest.mark.asyncio
async def test_latency_is_recorded(client: DevinClient) -> None:
    import app.devin_client as dc

    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "s-lat"})
    )
    await client.create_session("p", "k")
    assert len(dc.metrics._devin_latencies) >= 1


# ---------------------------------------------------------------------------
# trigger_fix_session
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_trigger_fix_session(client: DevinClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "s-fix"})
    )
    result = await client.trigger_fix_session("fix prompt", "fix-pkg-abc123")
    assert result["session_id"] == "s-fix"


# ---------------------------------------------------------------------------
# get_session_status
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_get_session_status(client: DevinClient) -> None:
    respx.get("https://api.devin.ai/v1/sessions/s-123").mock(
        return_value=httpx.Response(200, json={"status_enum": "finished"})
    )
    result = await client.get_session_status("s-123")
    assert result["status_enum"] == "finished"
