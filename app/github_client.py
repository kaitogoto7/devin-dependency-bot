"""GitHub REST API client for creating issues."""

from __future__ import annotations

import time

import httpx

from app.observability import get_logger, metrics

logger = get_logger(__name__)


class GitHubAPIError(Exception):
    """Raised on non-retryable GitHub API errors."""


class GitHubClient:
    """Wraps the GitHub REST API for issue management."""

    def __init__(self, token: str, repo: str) -> None:
        self._repo = repo
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def create_issue(
        self, title: str, body: str, labels: list[str]
    ) -> dict[str, str]:
        """Create a GitHub issue and return ``{"url": ..., "number": ...}``."""
        url = f"https://api.github.com/repos/{self._repo}/issues"
        payload = {"title": title, "body": body, "labels": labels}

        start = time.monotonic()
        response = await self._client.post(url, json=payload)
        elapsed = time.monotonic() - start

        metrics.record_github_latency(elapsed)
        metrics.inc_github_api_call("create_issue", response.status_code)

        logger.info(
            "GitHub API response",
            extra={
                "event_type": "github.api.response",
                "endpoint": "create_issue",
                "status_code": response.status_code,
                "latency": round(elapsed, 3),
            },
        )

        if response.status_code == 401:
            raise GitHubAPIError("GitHub authentication failed — check GITHUB_TOKEN")
        if response.status_code == 422:
            raise GitHubAPIError(
                f"GitHub validation error: {response.text}"
            )
        response.raise_for_status()

        data = response.json()
        return {
            "url": data["html_url"],
            "number": str(data["number"]),
        }
