"""Tests for app.prompts."""

from __future__ import annotations

from app.prompts import FIX_PROMPT_TEMPLATE


def test_fix_prompt_template_has_placeholders() -> None:
    assert "{github_issue_url}" in FIX_PROMPT_TEMPLATE
    assert "{category}" in FIX_PROMPT_TEMPLATE
    assert "{package}" in FIX_PROMPT_TEMPLATE
    assert "{current_version}" in FIX_PROMPT_TEMPLATE
    assert "{fixed_version}" in FIX_PROMPT_TEMPLATE
    assert "{description}" in FIX_PROMPT_TEMPLATE


def test_fix_prompt_template_formats_correctly() -> None:
    result = FIX_PROMPT_TEMPLATE.format(
        github_issue_url="https://github.com/kaitogoto7/superset/issues/100",
        category="frontend-security",
        package="nth-check",
        current_version=">=1.0.0",
        fixed_version="true",
        description="Inefficient Regular Expression Complexity",
    )
    assert "nth-check" in result
    assert ">=1.0.0" in result
    assert "issues/100" in result
    assert "frontend-security" in result


def test_fix_prompt_no_python_references() -> None:
    assert "Python" not in FIX_PROMPT_TEMPLATE
    assert "python" not in FIX_PROMPT_TEMPLATE
    assert "pip-compile" not in FIX_PROMPT_TEMPLATE
    assert "pip-audit" not in FIX_PROMPT_TEMPLATE
    assert "requirements/" not in FIX_PROMPT_TEMPLATE
    assert "pyproject.toml" not in FIX_PROMPT_TEMPLATE


def test_fix_prompt_mentions_frontend_instructions() -> None:
    assert "frontend security" in FIX_PROMPT_TEMPLATE
    assert "frontend general" in FIX_PROMPT_TEMPLATE
    assert "npm install" in FIX_PROMPT_TEMPLATE
    assert "npm run lint:all" in FIX_PROMPT_TEMPLATE
