"""Tests for app.github_client."""

from __future__ import annotations

import pytest
import httpx
import respx

from app.github_client import GitHubAPIError, GitHubClient
from app.observability import MetricsCollector


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="ghp_test", repo="kaitogoto7/superset")


@pytest.fixture(autouse=True)
def _reset_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = MetricsCollector()
    monkeypatch.setattr("app.github_client.metrics", fresh)


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_success(client: GitHubClient) -> None:
    respx.post("https://api.github.com/repos/kaitogoto7/superset/issues").mock(
        return_value=httpx.Response(
            201,
            json={
                "html_url": "https://github.com/kaitogoto7/superset/issues/42",
                "number": 42,
            },
        )
    )
    result = await client.create_issue("title", "body", ["python-security"])
    assert result["url"] == "https://github.com/kaitogoto7/superset/issues/42"
    assert result["number"] == "42"


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_correct_labels(client: GitHubClient) -> None:
    respx.post("https://api.github.com/repos/kaitogoto7/superset/issues").mock(
        return_value=httpx.Response(
            201,
            json={"html_url": "https://github.com/kaitogoto7/superset/issues/1", "number": 1},
        )
    )
    await client.create_issue("title", "body", ["python-security", "devin-dependency-bot"])
    import json
    req = respx.calls[0].request
    body = json.loads(req.content)
    assert "python-security" in body["labels"]
    assert "devin-dependency-bot" in body["labels"]


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_401_raises_auth_error(client: GitHubClient) -> None:
    respx.post("https://api.github.com/repos/kaitogoto7/superset/issues").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    with pytest.raises(GitHubAPIError, match="authentication"):
        await client.create_issue("title", "body", [])


@respx.mock
@pytest.mark.asyncio
async def test_create_issue_422_raises_validation_error(client: GitHubClient) -> None:
    respx.post("https://api.github.com/repos/kaitogoto7/superset/issues").mock(
        return_value=httpx.Response(422, text="Validation Failed")
    )
    with pytest.raises(GitHubAPIError, match="validation"):
        await client.create_issue("title", "body", [])


@respx.mock
@pytest.mark.asyncio
async def test_api_latency_recorded(client: GitHubClient) -> None:
    import app.github_client as gc

    respx.post("https://api.github.com/repos/kaitogoto7/superset/issues").mock(
        return_value=httpx.Response(
            201,
            json={"html_url": "https://github.com/kaitogoto7/superset/issues/1", "number": 1},
        )
    )
    await client.create_issue("title", "body", [])
    assert len(gc.metrics._github_latencies) >= 1
    snap = gc.metrics.snapshot()
    assert "create_issue_201" in snap.github_api_calls_total
