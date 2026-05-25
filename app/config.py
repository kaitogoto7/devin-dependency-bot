"""Configuration loaded from environment variables."""

from __future__ import annotations

import os


class ConfigError(Exception):
    """Raised when a required configuration variable is missing."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name!r} is not set")
    return value


class Settings:
    """Application settings populated from the environment."""

    def __init__(self) -> None:
        self.DEVIN_API_TOKEN: str = _require("DEVIN_API_TOKEN")
        self.GITHUB_TOKEN: str = _require("GITHUB_TOKEN")
        self.GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "kaitogoto7/superset")
        self.DEVIN_API_BASE_URL: str = os.environ.get(
            "DEVIN_API_BASE_URL", "https://api.devin.ai/v1"
        )
        self.LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
        self.DB_PATH: str = os.environ.get("DB_PATH", "/data/bot.db")
        self.POLL_INTERVAL_SECONDS: int = int(
            os.environ.get("POLL_INTERVAL_SECONDS", "30")
        )
        self.FRONTEND_DIR: str = os.environ.get(
            "FRONTEND_DIR", "/app/superset-frontend"
        )
        self.TOP_K_ISSUES: int = int(os.environ.get("TOP_K_ISSUES", "3"))


def get_settings() -> Settings:
    """Return a new ``Settings`` instance from the current environment."""
    return Settings()
