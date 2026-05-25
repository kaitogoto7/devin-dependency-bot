"""Shared test fixtures."""

from __future__ import annotations

from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Environment overrides (set before any app import that reads config)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject required env vars for every test."""
    monkeypatch.setenv("DEVIN_API_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "99999")
    monkeypatch.setenv("FRONTEND_DIR", "/tmp/fake-frontend")
    monkeypatch.setenv("TOP_K_ISSUES", "3")


# ---------------------------------------------------------------------------
# Sample npm audit output (npm v7+ format)
# ---------------------------------------------------------------------------

SAMPLE_AUDIT_OUTPUT: dict[str, Any] = {
    "vulnerabilities": {
        "nth-check": {
            "severity": "high",
            "via": [
                {
                    "title": "Inefficient Regular Expression Complexity",
                    "url": "https://github.com/advisories/GHSA-rp65-9cf3-cjxr",
                }
            ],
            "range": ">=1.0.0",
            "fixAvailable": True,
        },
        "postcss": {
            "severity": "moderate",
            "via": ["nth-check"],
            "range": ">=7.0.0",
            "fixAvailable": True,
        },
    }
}

# ---------------------------------------------------------------------------
# Sample npm outdated output
# ---------------------------------------------------------------------------

SAMPLE_OUTDATED_OUTPUT: dict[str, Any] = {
    "lodash": {
        "current": "4.17.20",
        "wanted": "4.17.21",
        "latest": "4.17.21",
        "dependent": "superset-frontend",
        "location": "node_modules/lodash",
    },
    "react": {
        "current": "18.2.0",
        "wanted": "18.2.0",
        "latest": "18.3.1",
        "dependent": "superset-frontend",
        "location": "node_modules/react",
    },
    "@superset-ui/core": {
        "current": "0.20.0",
        "wanted": "0.20.1",
        "latest": "0.20.1",
        "dependent": "superset-frontend",
        "location": "node_modules/@superset-ui/core",
        "resolved": "file:packages/superset-ui-core",
    },
}

# ---------------------------------------------------------------------------
# Sample npm outdated output with workspace-style list values
# ---------------------------------------------------------------------------

SAMPLE_OUTDATED_OUTPUT_WORKSPACES: dict[str, Any] = {
    "lodash": [
        {
            "current": "4.17.20",
            "wanted": "4.17.21",
            "latest": "4.17.21",
            "dependent": "workspace-a",
        },
        {
            "current": "4.17.20",
            "wanted": "4.17.21",
            "latest": "4.17.21",
            "dependent": "workspace-b",
        },
    ],
    "axios": {
        "current": "1.6.0",
        "wanted": "1.6.8",
        "latest": "1.7.2",
        "dependent": "superset-frontend",
    },
}


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client(_mock_env: None) -> Generator[TestClient, None, None]:
    # Import inside fixture so env is already patched
    from app.main import app

    with TestClient(app) as client:
        yield client
