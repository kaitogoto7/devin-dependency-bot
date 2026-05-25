"""Devin API client — trigger fix sessions and poll status."""

from __future__ import annotations

import time

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.observability import get_logger, metrics

logger = get_logger(__name__)


class DevinAPIError(Exception):
    """Non-retryable Devin API error (e.g. 401)."""


class DevinTransientError(Exception):
    """Retryable Devin API error (e.g. 429, 500+)."""


class DevinClient:
    """Wraps the Devin REST API for session management."""

    def __init__(
        self, api_token: str, base_url: str = "https://api.devin.ai/v1"
    ) -> None:
        self._token = api_token
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    # -- session creation (with retry) --

    @retry(
        retry=retry_if_exception_type(DevinTransientError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def create_session(self, prompt: str, idempotency_key: str) -> dict:
        """Create a Devin session. Returns session metadata dict."""
        url = f"{self._base_url}/sessions"
        payload: dict[str, object] = {"prompt": prompt, "idempotent": True}

        start = time.monotonic()
        try:
            response = await self._client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            elapsed = time.monotonic() - start
            metrics.record_devin_latency(elapsed)
            logger.error(
                "Devin API timeout",
                extra={"event_type": "devin.api.timeout", "latency": elapsed},
            )
            raise DevinTransientError("Timeout contacting Devin API") from exc

        elapsed = time.monotonic() - start
        metrics.record_devin_latency(elapsed)

        prompt_type = (
            idempotency_key.rsplit("-", 1)[0] if "-" in idempotency_key else "unknown"
        )
        metrics.inc_devin_api_call(prompt_type, response.status_code)

        logger.info(
            "Devin API response",
            extra={
                "event_type": "devin.api.response",
                "status_code": response.status_code,
                "latency": round(elapsed, 3),
                "idempotency_key": idempotency_key,
            },
        )

        if response.status_code == 401:
            raise DevinAPIError("Authentication failed — check DEVIN_API_TOKEN")
        if response.status_code == 429 or response.status_code >= 500:
            raise DevinTransientError(
                f"Devin API returned {response.status_code}: {response.text}"
            )
        response.raise_for_status()
        return response.json()

    # -- fix session --

    async def trigger_fix_session(self, prompt: str, idempotency_key: str) -> dict:
        """Trigger a single fix session for one dependency issue."""
        return await self.create_session(prompt, idempotency_key)

    # -- filter session --

    async def trigger_filter_session(
        self, prompt: str, scan_id: str
    ) -> dict[str, object]:
        """Trigger a filter/prioritization session for scan results."""
        idempotency_key = f"filter-{scan_id}"
        return await self.create_session(prompt, idempotency_key)

    # -- session status polling --

    async def get_session_status(self, session_id: str) -> dict:
        """Poll a single Devin session for its status."""
        url = f"{self._base_url}/sessions/{session_id}"
        start = time.monotonic()
        response = await self._client.get(url)
        elapsed = time.monotonic() - start
        metrics.record_devin_latency(elapsed)
        metrics.inc_devin_api_call("poll", response.status_code)
        response.raise_for_status()
        return response.json()
