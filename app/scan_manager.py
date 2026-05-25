"""Scan lifecycle management — start scans, parse results, dispatch fixes."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.devin_client import DevinClient
from app.github_client import GitHubClient
from app.models import DependencyIssue
from app.observability import get_logger, metrics
from app.prompts import FILTER_PROMPT_TEMPLATE, FIX_PROMPT_TEMPLATE

logger = get_logger(__name__)

CATEGORY_LABELS: dict[str, str] = {
    "frontend_security": "frontend-security",
    "frontend_general": "frontend-general",
}


class ScanManager:
    """Manages the scan → review → fix lifecycle."""

    def __init__(
        self,
        db_path: str,
        devin_client: DevinClient,
        github_client: GitHubClient,
        frontend_dir: str = "/app/superset-frontend",
        top_k: int = 3,
    ) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._devin = devin_client
        self._github = github_client
        self._frontend_dir = frontend_dir
        self._top_k = top_k
        self._init_db()

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    devin_session_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'in_progress',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    raw_output TEXT,
                    filter_session_id TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_issues (
                    issue_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    package TEXT NOT NULL,
                    current_version TEXT NOT NULL,
                    fixed_version TEXT NOT NULL,
                    description TEXT NOT NULL,
                    severity TEXT,
                    advisory_id TEXT,
                    selected INTEGER NOT NULL DEFAULT 0,
                    recommended INTEGER NOT NULL DEFAULT 0,
                    github_issue_url TEXT,
                    fix_session_id TEXT,
                    FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
                )
                """
            )

            # -- Schema migrations for columns added after initial release --
            for migration in [
                "ALTER TABLE scans ADD COLUMN filter_session_id TEXT",
                "ALTER TABLE scan_issues ADD COLUMN recommended INTEGER NOT NULL DEFAULT 0",
            ]:
                try:
                    self._conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already exists

    def close(self) -> None:
        self._conn.close()

    # -- npm command runner --

    async def _run_npm_command(self, args: list[str]) -> tuple[str, str, int]:
        """Run an npm command and return (stdout, stderr, returncode)."""
        proc = await asyncio.create_subprocess_exec(
            "npm",
            *args,
            cwd=self._frontend_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode or 0

    # -- create scan record (for background task pattern) --

    def create_scan_record(self) -> str:
        """Insert a new scan row with status 'in_progress' and return the scan_id."""
        scan_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO scans (scan_id, devin_session_id, status, created_at) VALUES (?, ?, ?, ?)",
                (scan_id, "local", "in_progress", now),
            )
            self._conn.commit()
        metrics.inc_scan_triggered()
        logger.info(
            "Scan record created (local npm)",
            extra={"event_type": "scan.started", "scan_id": scan_id},
        )
        return scan_id

    # -- run the actual scan --

    async def run_scan(self, scan_id: str) -> None:
        """Execute npm install, npm audit, and npm outdated, then store results."""
        try:
            # Step 1: npm install
            stdout, stderr, rc = await self._run_npm_command(["install"])
            logger.info(
                "npm install completed",
                extra={
                    "event_type": "scan.npm_install",
                    "scan_id": scan_id,
                    "returncode": rc,
                },
            )

            # Step 2: npm audit --json
            audit_stdout, audit_stderr, audit_rc = await self._run_npm_command(
                ["audit", "--json"]
            )
            audit_data: dict[str, Any] = (
                json.loads(audit_stdout) if audit_stdout.strip() else {}
            )

            # Step 3: npm outdated --json
            outdated_stdout, outdated_stderr, outdated_rc = await self._run_npm_command(
                ["outdated", "--json"]
            )
            outdated_data: dict[str, Any] = (
                json.loads(outdated_stdout) if outdated_stdout.strip() else {}
            )

            # Step 4: Parse into DependencyIssue objects
            issues = self._parse_npm_audit(
                scan_id, audit_data
            ) + self._parse_npm_outdated(scan_id, outdated_data)

            # Step 5: Store issues
            with self._lock:
                for issue in issues:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO scan_issues
                        (issue_id, scan_id, category, package, current_version,
                         fixed_version, description, severity, advisory_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            issue.issue_id,
                            scan_id,
                            issue.category,
                            issue.package,
                            issue.current_version,
                            issue.fixed_version,
                            issue.description,
                            issue.severity,
                            issue.advisory_id,
                        ),
                    )
                self._conn.commit()

            for issue in issues:
                metrics.inc_issues_found(issue.category)

            if issues:
                metrics.inc_scan_completed("findings_found")
            else:
                metrics.inc_scan_completed("no_findings")

            # Step 6: If issues found, trigger Devin filter session
            if issues:
                issues_json = json.dumps(
                    [
                        {
                            "issue_id": i.issue_id,
                            "category": i.category,
                            "package": i.package,
                            "current_version": i.current_version,
                            "fixed_version": i.fixed_version,
                            "description": i.description,
                            "severity": i.severity,
                            "advisory_id": i.advisory_id,
                        }
                        for i in issues
                    ],
                    indent=2,
                )
                k = min(self._top_k, len(issues))
                prompt = FILTER_PROMPT_TEMPLATE.format(issues_json=issues_json, k=k)

                now_ts = datetime.now(timezone.utc).isoformat()
                prompt += f"\n\nScan ID: {scan_id} | Timestamp: {now_ts}"

                try:
                    filter_result = await self._devin.create_session(
                        prompt, f"filter-{scan_id}"
                    )
                    filter_session_id = filter_result.get(
                        "session_id", filter_result.get("id", "")
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to trigger filter session",
                        extra={
                            "event_type": "scan.filter_failed",
                            "scan_id": scan_id,
                            "error": str(exc),
                        },
                    )
                    with self._lock:
                        self._conn.execute(
                            "UPDATE scan_issues SET recommended = 1 WHERE scan_id = ?",
                            (scan_id,),
                        )
                        self._conn.execute(
                            "UPDATE scans SET status = 'completed',"
                            " completed_at = ?, raw_output = ?"
                            " WHERE scan_id = ?",
                            (
                                datetime.now(timezone.utc).isoformat(),
                                json.dumps(
                                    {"audit": audit_data, "outdated": outdated_data}
                                ),
                                scan_id,
                            ),
                        )
                        self._conn.commit()
                    return

                with self._lock:
                    self._conn.execute(
                        "UPDATE scans SET status = 'filtering',"
                        " filter_session_id = ?,"
                        " raw_output = ? WHERE scan_id = ?",
                        (
                            filter_session_id,
                            json.dumps(
                                {"audit": audit_data, "outdated": outdated_data}
                            ),
                            scan_id,
                        ),
                    )
                    self._conn.commit()

                metrics.inc_session_active()
                logger.info(
                    "Filter session triggered",
                    extra={
                        "event_type": "scan.filter_triggered",
                        "scan_id": scan_id,
                        "filter_session_id": filter_session_id,
                        "k": k,
                        "total_issues": len(issues),
                    },
                )
            else:
                completed_at = datetime.now(timezone.utc).isoformat()
                with self._lock:
                    self._conn.execute(
                        "UPDATE scans SET status = 'completed',"
                        " completed_at = ?, raw_output = ?"
                        " WHERE scan_id = ?",
                        (
                            completed_at,
                            json.dumps(
                                {"audit": audit_data, "outdated": outdated_data}
                            ),
                            scan_id,
                        ),
                    )
                    self._conn.commit()

                logger.info(
                    "Scan completed",
                    extra={
                        "event_type": "scan.completed",
                        "scan_id": scan_id,
                        "issues_found": len(issues),
                    },
                )

        except Exception as exc:
            logger.error(
                "Scan failed",
                extra={
                    "event_type": "scan.failed",
                    "scan_id": scan_id,
                    "error": str(exc),
                },
            )
            with self._lock:
                self._conn.execute(
                    "UPDATE scans SET status = 'failed' WHERE scan_id = ?",
                    (scan_id,),
                )
                self._conn.commit()
            metrics.inc_scan_completed("failed")

    # -- npm audit parsing --

    def _parse_npm_audit(
        self, scan_id: str, audit_data: dict[str, Any]
    ) -> list[DependencyIssue]:
        """Parse ``npm audit --json`` output into DependencyIssue objects."""
        issues: list[DependencyIssue] = []
        vulnerabilities = audit_data.get("vulnerabilities", {})
        for pkg_name, vuln_info in vulnerabilities.items():
            if not isinstance(vuln_info, dict):
                continue
            severity = vuln_info.get("severity", "unknown")
            via_list = vuln_info.get("via", [])
            has_direct_advisory = False
            for via in via_list:
                if isinstance(via, dict):
                    has_direct_advisory = True
                    issue_id = uuid.uuid4().hex[:12]
                    issues.append(
                        DependencyIssue(
                            issue_id=issue_id,
                            category="frontend_security",
                            package=pkg_name,
                            current_version=vuln_info.get("range", "unknown"),
                            fixed_version=str(vuln_info.get("fixAvailable", "unknown")),
                            description=via.get("title", ""),
                            severity=severity,
                            advisory_id=str(via.get("url", "")),
                        )
                    )
            if not has_direct_advisory and via_list:
                issue_id = uuid.uuid4().hex[:12]
                issues.append(
                    DependencyIssue(
                        issue_id=issue_id,
                        category="frontend_security",
                        package=pkg_name,
                        current_version=vuln_info.get("range", "unknown"),
                        fixed_version=str(vuln_info.get("fixAvailable", "unknown")),
                        description=f"Transitive vulnerability via {', '.join(str(v) for v in via_list)}",
                        severity=severity,
                    )
                )
        return issues

    # -- npm outdated parsing --

    def _parse_npm_outdated(
        self, scan_id: str, outdated_data: dict[str, Any]
    ) -> list[DependencyIssue]:
        """Parse ``npm outdated --json`` output into DependencyIssue objects.

        npm workspaces may return a list of dicts (one per workspace) for
        a single package instead of a single dict.
        """
        issues: list[DependencyIssue] = []
        for pkg_name, info in outdated_data.items():
            entries = info if isinstance(info, list) else [info]
            seen = False
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                current = entry.get("current", "unknown")
                wanted = entry.get("wanted", "unknown")
                latest = entry.get("latest", "unknown")

                if "file:" in str(entry.get("resolved", "")):
                    continue
                if current == wanted:
                    continue
                if seen:
                    continue

                issue_id = uuid.uuid4().hex[:12]
                issues.append(
                    DependencyIssue(
                        issue_id=issue_id,
                        category="frontend_general",
                        package=pkg_name,
                        current_version=current,
                        fixed_version=wanted,
                        description=f"Outdated: current={current}, wanted={wanted}, latest={latest}",
                    )
                )
                seen = True
        return issues

    # -- get active scans for background poller (fix sessions only) --

    def get_active_scans(self) -> list[dict[str, Any]]:
        """Return scans awaiting Devin session completion (in_progress or filtering)."""
        rows = self._conn.execute(
            "SELECT scan_id, filter_session_id, status, created_at "
            "FROM scans WHERE status IN ('in_progress', 'filtering') AND devin_session_id != 'local'"
        ).fetchall()
        return [
            {
                "scan_id": r["scan_id"],
                "filter_session_id": r["filter_session_id"],
                "status": r["status"],
            }
            for r in rows
        ]

    # -- poll and update a single scan (for fix sessions only) --

    async def poll_and_update_scan(self, scan_id: str) -> None:
        """Poll the Devin API for a scan's session and update SQLite if terminal.

        Skips scans with devin_session_id == 'local' (local npm scans).
        """
        row = self._conn.execute(
            "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row or row["status"] != "in_progress":
            return
        if row["devin_session_id"] == "local":
            return

        session_id = row["devin_session_id"]
        status_data = await self._devin.get_session_status(session_id)

        session_status = (
            status_data.get("status_enum") or status_data.get("status") or ""
        )

        logger.info(
            "Scan session status polled",
            extra={
                "event_type": "scan.poll_status",
                "scan_id": scan_id,
                "session_id": session_id,
                "status_enum": status_data.get("status_enum"),
                "status": status_data.get("status"),
                "resolved_status": session_status,
            },
        )

        if session_status not in ("finished", "blocked", "expired", "stopped", "failed"):
            return

        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._conn.execute(
                "UPDATE scans SET status = 'completed', completed_at = ? WHERE scan_id = ?",
                (now, scan_id),
            )
            self._conn.commit()

        metrics.dec_session_active()

        logger.info(
            "Scan completed by background poller",
            extra={
                "event_type": "poller.scan_completed",
                "scan_id": scan_id,
                "session_id": session_id,
            },
        )

    # -- poll and update filter session --

    async def poll_and_update_filter(self, scan_id: str) -> None:
        """Poll the Devin filter session and update recommendations when complete."""
        row = self._conn.execute(
            "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row or row["status"] != "filtering":
            return

        filter_session_id = row["filter_session_id"]
        if not filter_session_id:
            return

        status_data = await self._devin.get_session_status(filter_session_id)
        status_enum = status_data.get("status_enum", status_data.get("status", ""))

        logger.info(
            "Filter session status polled",
            extra={
                "event_type": "scan.filter_poll",
                "scan_id": scan_id,
                "session_id": filter_session_id,
                "status_enum": status_enum,
            },
        )

        if status_enum not in ("finished", "blocked", "expired", "stopped", "failed"):
            return

        recommended_ids: list[str] = []
        if status_enum == "finished":
            try:
                url = f"{self._devin._base_url}/sessions/{filter_session_id}"
                response = await self._devin._client.get(url)
                response.raise_for_status()
                data = response.json()
                messages = data.get("messages", [])

                output_text = ""
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("type") == "devin_message":
                        output_text = msg.get("message", "")
                        break

                recommended_ids = self._parse_filter_output(output_text, scan_id)
            except Exception as exc:
                logger.error(
                    "Failed to parse filter output",
                    extra={
                        "event_type": "scan.filter_parse_error",
                        "scan_id": scan_id,
                        "error": str(exc),
                    },
                )

        if not recommended_ids:
            logger.warning(
                "Filter returned no recommendations, marking all as recommended",
                extra={
                    "event_type": "scan.filter_fallback",
                    "scan_id": scan_id,
                },
            )
            with self._lock:
                self._conn.execute(
                    "UPDATE scan_issues SET recommended = 1 WHERE scan_id = ?",
                    (scan_id,),
                )
        else:
            with self._lock:
                placeholders = ",".join("?" * len(recommended_ids))
                self._conn.execute(
                    f"UPDATE scan_issues SET recommended = 1 WHERE scan_id = ? AND issue_id IN ({placeholders})",
                    [scan_id, *recommended_ids],
                )

        completed_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE scans SET status = 'completed', completed_at = ? WHERE scan_id = ?",
                (completed_at, scan_id),
            )
            self._conn.commit()

        metrics.dec_session_active()
        logger.info(
            "Filter complete, scan finalized",
            extra={
                "event_type": "scan.filter_completed",
                "scan_id": scan_id,
                "recommended_count": len(recommended_ids),
            },
        )

    def _parse_filter_output(self, output_text: str, scan_id: str) -> list[str]:
        """Extract selected_issue_ids from Devin's filter response."""
        if not output_text:
            return []

        # Strategy 1: direct JSON parse
        try:
            data = json.loads(output_text.strip())
            if isinstance(data, dict) and "selected_issue_ids" in data:
                return [str(x) for x in data["selected_issue_ids"]]
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 2: extract JSON from markdown code block
        for match in re.findall(r"```(?:json)?\s*\n(.*?)\n```", output_text, re.DOTALL):
            try:
                data = json.loads(match)
                if isinstance(data, dict) and "selected_issue_ids" in data:
                    return [str(x) for x in data["selected_issue_ids"]]
            except (json.JSONDecodeError, TypeError):
                continue

        # Strategy 3: find first JSON object via brace matching
        start = output_text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(output_text)):
                if output_text[i] == "{":
                    depth += 1
                elif output_text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(output_text[start : i + 1])
                            if isinstance(data, dict) and "selected_issue_ids" in data:
                                return [str(x) for x in data["selected_issue_ids"]]
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break

        logger.warning(
            "Could not parse filter output",
            extra={
                "event_type": "scan.filter_parse_error",
                "scan_id": scan_id,
                "output_preview": output_text[:500],
            },
        )
        return []

    # -- get scan results (reads from DB) --

    async def get_scan_results(
        self, scan_id: str, on_demand_poll: bool = True
    ) -> dict[str, Any]:
        """Return parsed/categorized findings or in_progress status."""
        row = self._conn.execute(
            "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return {"error": "scan_not_found"}

        # For local npm scans, skip on-demand polling (they complete via run_scan)
        is_local = row["devin_session_id"] == "local"

        if row["status"] == "in_progress" and on_demand_poll and not is_local:
            try:
                await self.poll_and_update_scan(scan_id)
                row = self._conn.execute(
                    "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
                ).fetchone()
            except Exception as exc:
                logger.error(
                    "On-demand poll failed",
                    extra={
                        "event_type": "scan.poll_error",
                        "scan_id": scan_id,
                        "error": str(exc),
                    },
                )

        if row["status"] == "filtering" and on_demand_poll:
            filter_sid = (
                row["filter_session_id"]
                if "filter_session_id" in row.keys()
                else None
            )
            if filter_sid:
                try:
                    await self.poll_and_update_filter(scan_id)
                    row = self._conn.execute(
                        "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
                    ).fetchone()
                except Exception as exc:
                    logger.error(
                        "On-demand filter poll failed",
                        extra={
                            "event_type": "scan.filter_poll_error",
                            "scan_id": scan_id,
                            "error": str(exc),
                        },
                    )

        if row["status"] == "completed":
            issues = self._get_issues(scan_id)
            return {
                "status": "completed",
                "scan_id": scan_id,
                "completed_at": row["completed_at"],
                "issues": [i.model_dump(mode="json") for i in issues],
                "parse_status": "success",
            }

        if row["status"] == "filtering":
            return {"status": "filtering", "scan_id": scan_id}

        if row["status"] == "failed":
            return {"status": "failed", "scan_id": scan_id}

        return {"status": row["status"], "scan_id": scan_id}

    # -- select issues and dispatch fixes --

    async def select_and_dispatch(
        self, scan_id: str, issue_ids: list[str]
    ) -> dict[str, Any]:
        """For each selected issue: create a GitHub issue and trigger a fix session."""
        row = self._conn.execute(
            "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return {"error": "scan_not_found"}
        if row["status"] != "completed":
            return {"error": "scan_not_completed"}

        scan_completed_at = row["completed_at"]
        now = datetime.now(timezone.utc)
        if scan_completed_at:
            completed_dt = datetime.fromisoformat(scan_completed_at)
            review_seconds = (now - completed_dt).total_seconds()
            metrics.record_human_review_latency(review_seconds)

        all_issues = self._get_issues(scan_id)
        issue_map = {i.issue_id: i for i in all_issues}

        invalid_ids = [iid for iid in issue_ids if iid not in issue_map]
        if invalid_ids:
            return {"error": "invalid_issue_ids", "invalid_ids": invalid_ids}

        github_issues: list[dict[str, str]] = []
        fix_sessions: list[dict[str, str]] = []

        for iid in issue_ids:
            issue = issue_map[iid]
            category_label = CATEGORY_LABELS.get(issue.category, issue.category)

            title = f"chore(deps): fix {issue.package} ({category_label})"
            body = (
                f"**Category:** {category_label}\n"
                f"**Package:** {issue.package}\n"
                f"**Current version:** {issue.current_version}\n"
                f"**Target version:** {issue.fixed_version}\n\n"
                f"{issue.description}"
            )
            if issue.severity:
                body += f"\n\n**Severity:** {issue.severity}"
            if issue.advisory_id:
                body += f"\n**Advisory:** {issue.advisory_id}"

            labels = [category_label, "devin-dependency-bot", "devin-issue"]

            gh_issue = await self._github.create_issue(title, body, labels)
            github_issues.append(gh_issue)

            metrics.inc_issues_selected(issue.category)
            metrics.inc_issues_posted(issue.category)

            fix_prompt = FIX_PROMPT_TEMPLATE.format(
                github_issue_url=gh_issue["url"],
                category=category_label,
                package=issue.package,
                current_version=issue.current_version,
                fixed_version=issue.fixed_version,
                description=issue.description,
            )
            idempotency_key = f"fix-{issue.package}-{scan_id}"

            try:
                fix_result = await self._devin.trigger_fix_session(
                    fix_prompt, idempotency_key
                )
                fix_session_id = fix_result.get("session_id", fix_result.get("id", ""))
            except Exception as exc:
                logger.error(
                    "Failed to trigger fix session",
                    extra={
                        "event_type": "fix.session.failed",
                        "issue_id": iid,
                        "error": str(exc),
                    },
                )
                fix_session_id = ""

            fix_sessions.append(
                {
                    "issue_id": iid,
                    "session_id": fix_session_id,
                    "github_issue_url": gh_issue["url"],
                }
            )

            metrics.inc_fix_session_triggered(issue.category)
            metrics.inc_session_active()

            with self._lock:
                self._conn.execute(
                    "UPDATE scan_issues SET selected = 1, github_issue_url = ?, fix_session_id = ? WHERE issue_id = ?",
                    (gh_issue["url"], fix_session_id, iid),
                )
                self._conn.commit()

        logger.info(
            "Issues dispatched",
            extra={
                "event_type": "scan.dispatched",
                "scan_id": scan_id,
                "count": len(issue_ids),
            },
        )

        return {
            "github_issues": github_issues,
            "fix_sessions": fix_sessions,
        }

    # -- helpers --

    def _get_issues(self, scan_id: str) -> list[DependencyIssue]:
        rows = self._conn.execute(
            "SELECT * FROM scan_issues WHERE scan_id = ? ORDER BY issue_id",
            (scan_id,),
        ).fetchall()
        return [
            DependencyIssue(
                issue_id=r["issue_id"],
                category=r["category"],
                package=r["package"],
                current_version=r["current_version"],
                fixed_version=r["fixed_version"],
                description=r["description"],
                severity=r["severity"],
                advisory_id=r["advisory_id"],
                selected=bool(r["selected"]),
                recommended=bool(r["recommended"]),
                github_issue_url=r["github_issue_url"],
                fix_session_id=r["fix_session_id"],
            )
            for r in rows
        ]

    def get_last_scan_time(self) -> str | None:
        """Return ISO timestamp of the most recent scan."""
        row = self._conn.execute(
            "SELECT created_at FROM scans ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["created_at"] if row else None
