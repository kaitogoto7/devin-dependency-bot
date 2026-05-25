"""Devin prompt constants for the fix and filter dependency workflows."""

FIX_PROMPT_TEMPLATE: str = (
    "Fix the dependency issue described in {github_issue_url} for the "
    "kaitogoto7/superset repository.\n\n"
    "Issue details:\n"
    "- Category: {category}\n"
    "- Package: {package}\n"
    "- Current version: {current_version}\n"
    "- Target version: {fixed_version}\n"
    "- Description: {description}\n\n"
    "Instructions:\n"
    "- If frontend security: Run `cd superset-frontend && npm install` then "
    "update the specific package. Run `npm run lint:all` and "
    "`npm run test -- --bail --maxWorkers=2` to verify. The repo uses npm "
    "workspaces (`packages/*`, `plugins/*`, `src/setup/*`).\n"
    "- If frontend general: Update the package in the relevant "
    "`package.json` file(s) and run `npm install` to regenerate the lockfile. "
    "If the package appears in the `overrides` section of "
    "`superset-frontend/package.json`, update the override too. Run "
    "`npm run lint:all` and `npm run test -- --bail --maxWorkers=2` to "
    "verify.\n\n"
    'Open a PR titled "chore(deps): fix {package} ({category})" referencing '
    "{github_issue_url} in the PR body. Target the `main` branch.\n"
    "If the fix fails tests, describe the failure in the PR body and request "
    "human review."
)

FILTER_PROMPT_TEMPLATE: str = (
    "You are a dependency triage expert. Below is a JSON array of dependency "
    "issues found in the kaitogoto7/superset frontend codebase. Your task is "
    "to select the {k} most important issues to fix, ranked by priority.\n\n"
    "Prioritization criteria (in order of importance):\n"
    "1. Security vulnerabilities with critical or high severity\n"
    "2. Security vulnerabilities with direct advisories (not transitive-only)\n"
    "3. Packages with known exploits or active CVEs\n"
    "4. Outdated packages with large version gaps (many patches behind)\n"
    "5. Packages that are direct dependencies (not deep transitive)\n\n"
    "Issues:\n"
    "```json\n{issues_json}\n```\n\n"
    "Return ONLY a JSON object with a single key `selected_issues` mapping "
    "to an array of exactly {k} objects containing the detailed issue information, "
    "ordered by priority (most important first). Example:\n"
    '{{\n'
    '  "selected_issues": [\n'
    '    {{\n'
    '      "issue_id": "id1",\n'
    '      "category": "...",\n'
    '      "package": "...",\n'
    '      "current_version": "...",\n'
    '      "fixed_version": "...",\n'
    '      "description": "...",\n'
    '      "severity": "...",\n'
    '      "advisory_id": "..."\n'
    '    }},\n'
    '    {{\n'
    '      "issue_id": "id2",\n'
    '      "category": "...",\n'
    '      "package": "...",\n'
    '      "current_version": "...",\n'
    '      "fixed_version": "...",\n'
    '      "description": "...",\n'
    '      "severity": "...",\n'
    '      "advisory_id": "..."\n'
    '    }}\n'
    '  ]\n'
    '}}\n\n'
    "IMPORTANT: Your entire response must be ONLY the JSON object — no "
    "explanatory text, no markdown formatting. Start with {{ and end with }}."
)
