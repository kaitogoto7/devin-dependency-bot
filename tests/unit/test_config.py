"""Tests for app.config."""

from __future__ import annotations

import pytest
from app.config import ConfigError, get_settings


def test_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp")
    settings = get_settings()
    assert settings.DEVIN_API_TOKEN == "tok"
    assert settings.GITHUB_TOKEN == "ghp"


def test_missing_devin_api_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVIN_API_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="DEVIN_API_TOKEN"):
        get_settings()


def test_missing_github_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        get_settings()


def test_default_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_TOKEN", "t")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    for var in (
        "GITHUB_REPO",
        "DEVIN_API_BASE_URL",
        "LOG_LEVEL",
        "DB_PATH",
        "POLL_INTERVAL_SECONDS",
        "TOP_K_ISSUES",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = get_settings()
    assert settings.GITHUB_REPO == "kaitogoto7/superset"
    assert settings.DEVIN_API_BASE_URL == "https://api.devin.ai/v1"
    assert settings.LOG_LEVEL == "INFO"
    assert settings.DB_PATH == "/data/bot.db"
    assert settings.POLL_INTERVAL_SECONDS == 30
    assert settings.TOP_K_ISSUES == 3


def test_top_k_issues_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOP_K_ISSUES", raising=False)
    settings = get_settings()
    assert settings.TOP_K_ISSUES == 3


def test_top_k_issues_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOP_K_ISSUES", "5")
    settings = get_settings()
    assert settings.TOP_K_ISSUES == 5
