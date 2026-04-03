#!/usr/bin/env python3
"""Collect worklog data from Git commits, Claude Code sessions, Codex sessions, and Cursor sessions.

Outputs raw Markdown to stdout (no summary). Zero external dependencies.

Required args:
  --workspace-root  - root directory containing git repos
  --git-author      - git author name to filter commits
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    workspace_root: Path
    git_author: str
    date_from: date
    date_until: date
    claude_projects_dir: Path = field(init=False)
    codex_state_db: Path = field(init=False)
    cursor_storage_dir: Path = field(init=False)

    def __post_init__(self):
        home = Path.home()
        self.claude_projects_dir = home / ".claude" / "projects"
        self.codex_state_db = home / ".codex" / "state_5.sqlite"
        self.cursor_storage_dir = (
            home / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage"
        )


# ---------------------------------------------------------------------------
# Git Commits
# ---------------------------------------------------------------------------

@dataclass
class GitCommit:
    time: datetime
    message: str


def collect_git_commits(config: Config) -> dict[str, list[GitCommit]]:
    result: dict[str, list[GitCommit]] = {}
    root = config.workspace_root
    if not root.is_dir():
        return result

    since_str = config.date_from.isoformat()
    until_str = (config.date_until + timedelta(days=1)).isoformat()

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / ".git").exists():
            continue

        repo_name = entry.name
        try:
            proc = subprocess.run(
                [
                    "git", "-C", str(entry), "log", "--all",
                    f"--since={since_str}",
                    f"--until={until_str}",
                    f"--author={config.git_author}",
                    "--format=%ai|%s",
                ],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        if proc.returncode != 0:
            continue

        commits: list[GitCommit] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            idx = line.find("|")
            if idx < 0:
                continue
            time_str = line[:idx].strip()
            message = line[idx + 1:]
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                try:
                    dt = datetime.strptime(time_str[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            commits.append(GitCommit(time=dt, message=message))

        commits.sort(key=lambda c: c.time)
        if commits:
            result[repo_name] = commits

    return result


# ---------------------------------------------------------------------------
# Claude Code Sessions
# ---------------------------------------------------------------------------

@dataclass
class ClaudeSession:
    first_prompt: str
    message_count: int
    created: datetime
    modified: datetime
    git_branch: Optional[str]


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse ISO 8601 datetime string."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _date_overlaps(created: datetime, modified: datetime, d_from: date, d_until: date) -> bool:
    created_date = created.astimezone().date()
    modified_date = modified.astimezone().date()
    return created_date <= d_until and modified_date >= d_from


def _parse_unix_seconds(ts: object) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _extract_repo_name(project_path: str, workspace_prefix: str) -> str:
    relative = project_path
    if relative.startswith(workspace_prefix):
        relative = relative[len(workspace_prefix):]
    relative = relative.lstrip("/")
    return relative.split("/")[0] if "/" in relative else relative


def collect_claude_sessions(config: Config) -> dict[str, list[ClaudeSession]]:
    result: dict[str, list[ClaudeSession]] = {}
    projects_dir = config.claude_projects_dir
    if not projects_dir.is_dir():
        return result

    workspace_prefix = str(config.workspace_root)

    for entry in sorted(projects_dir.iterdir()):
        index_path = entry / "sessions-index.json"
        if not index_path.is_file():
            continue
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        for se in data.get("entries", []):
            project_path = se.get("projectPath", "")
            if not project_path.startswith(workspace_prefix):
                continue

            prompt = se.get("firstPrompt", "")
            if not prompt or prompt == "No prompt":
                continue

            created = _parse_iso(se.get("created", ""))
            if created is None:
                continue
            modified = _parse_iso(se.get("modified", "")) or created

            if not _date_overlaps(created, modified, config.date_from, config.date_until):
                continue

            repo_name = _extract_repo_name(project_path, workspace_prefix)
            result.setdefault(repo_name, []).append(ClaudeSession(
                first_prompt=prompt,
                message_count=se.get("messageCount", 0),
                created=created,
                modified=modified,
                git_branch=se.get("gitBranch"),
            ))

    for sessions in result.values():
        sessions.sort(key=lambda s: s.created)

    return result


# ---------------------------------------------------------------------------
# Codex Sessions
# ---------------------------------------------------------------------------

@dataclass
class CodexSession:
    title: str
    cwd: str
    created_at: datetime
    updated_at: datetime
    git_branch: Optional[str]


def collect_codex_sessions(config: Config) -> dict[str, list[CodexSession]]:
    result: dict[str, list[CodexSession]] = {}
    db_path = config.codex_state_db
    if not db_path.is_file():
        return result

    workspace_prefix = str(config.workspace_root)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT title, cwd, created_at, updated_at, git_branch FROM threads"
        ).fetchall()
        conn.close()
    except (sqlite3.Error, OSError):
        return result

    for title, cwd, created_ts, updated_ts, git_branch in rows:
        if not isinstance(cwd, str) or not cwd.startswith(workspace_prefix):
            continue

        created_at = _parse_unix_seconds(created_ts)
        updated_at = _parse_unix_seconds(updated_ts)
        if created_at is None or updated_at is None:
            continue

        if not _date_overlaps(created_at, updated_at, config.date_from, config.date_until):
            continue

        repo_name = _extract_repo_name(cwd, workspace_prefix)
        if not repo_name:
            continue

        result.setdefault(repo_name, []).append(CodexSession(
            title=title or "(untitled)",
            cwd=cwd,
            created_at=created_at,
            updated_at=updated_at,
            git_branch=git_branch,
        ))

    for sessions in result.values():
        sessions.sort(key=lambda s: s.created_at)

    return result


# ---------------------------------------------------------------------------
# Cursor Sessions
# ---------------------------------------------------------------------------

@dataclass
class CursorSession:
    name: Optional[str]
    subtitle: Optional[str]
    created_at: datetime
    last_updated_at: Optional[datetime]
    mode: Optional[str]


def collect_cursor_sessions(config: Config) -> dict[str, list[CursorSession]]:
    result: dict[str, list[CursorSession]] = {}
    storage_dir = config.cursor_storage_dir
    if not storage_dir.is_dir():
        return result

    workspace_prefix = str(config.workspace_root)

    for entry in sorted(storage_dir.iterdir()):
        if not entry.is_dir():
            continue

        ws_json_path = entry / "workspace.json"
        if not ws_json_path.is_file():
            continue
        try:
            ws = json.loads(ws_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        folder_uri = ws.get("folder", "")
        folder_path = folder_uri.removeprefix("file://")
        if not folder_path.startswith(workspace_prefix):
            continue

        repo_name = _extract_repo_name(folder_path, workspace_prefix)

        db_path = entry / "state.vscdb"
        if not db_path.is_file():
            continue

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
            ).fetchone()
            conn.close()
        except (sqlite3.Error, OSError):
            continue

        if row is None:
            continue

        try:
            data = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            continue

        composers = data.get("allComposers", [])
        if not composers:
            continue

        for composer in composers:
            created_ms = composer.get("createdAt")
            if created_ms is None:
                continue

            created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            last_updated_ms = composer.get("lastUpdatedAt")
            last_updated = (
                datetime.fromtimestamp(last_updated_ms / 1000, tz=timezone.utc)
                if last_updated_ms else None
            )

            created_local = created.astimezone().date()
            end_local = last_updated.astimezone().date() if last_updated else created_local

            if created_local > config.date_until or end_local < config.date_from:
                continue

            result.setdefault(repo_name, []).append(CursorSession(
                name=composer.get("name"),
                subtitle=composer.get("subtitle"),
                created_at=created,
                last_updated_at=last_updated,
                mode=composer.get("unifiedMode"),
            ))

    for sessions in result.values():
        sessions.sort(key=lambda s: s.created_at)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _truncate(s: str, max_len: int = 80) -> str:
    first_line = s.split("\n", 1)[0]
    if len(first_line) <= max_len:
        return first_line
    return first_line[:max_len] + "..."


def generate_report(
    config: Config,
    git_data: dict[str, list[GitCommit]],
    claude_data: dict[str, list[ClaudeSession]],
    codex_data: dict[str, list[CodexSession]],
    cursor_data: dict[str, list[CursorSession]],
) -> str:
    lines: list[str] = []

    # Title
    if config.date_from == config.date_until:
        lines.append(f"# Worklog {config.date_from}\n")
    else:
        lines.append(f"# Worklog {config.date_from} ~ {config.date_until}\n")

    # Git Commits
    if git_data:
        lines.append("## Git Commits\n")
        for repo, commits in sorted(git_data.items()):
            lines.append(f"### {repo}\n")
            for c in commits:
                t = c.time.strftime("%H:%M")
                lines.append(f"- {c.message} ({t})")
            lines.append("")

    # Claude Code Sessions
    if claude_data:
        lines.append("## Claude Code Sessions\n")
        for repo, sessions in sorted(claude_data.items()):
            lines.append(f"### {repo}\n")
            for s in sessions:
                created = s.created.astimezone().strftime("%H:%M")
                modified = s.modified.astimezone().strftime("%H:%M")
                branch = f" [{s.git_branch}]" if s.git_branch else ""
                prompt = _truncate(s.first_prompt)
                lines.append(
                    f"- {prompt}{branch} ({created} - {modified}, {s.message_count} messages)"
                )
            lines.append("")

    # Codex Sessions
    if codex_data:
        lines.append("## Codex Sessions\n")
        for repo, sessions in sorted(codex_data.items()):
            lines.append(f"### {repo}\n")
            for s in sessions:
                created = s.created_at.astimezone().strftime("%H:%M")
                updated = s.updated_at.astimezone().strftime("%H:%M")
                branch = f" [{s.git_branch}]" if s.git_branch else ""
                lines.append(f"- {_truncate(s.title)}{branch} ({created} - {updated})")
            lines.append("")

    # Cursor Sessions
    if cursor_data:
        lines.append("## Cursor Sessions\n")
        for repo, sessions in sorted(cursor_data.items()):
            lines.append(f"### {repo}\n")
            for s in sessions:
                created = s.created_at.astimezone().strftime("%H:%M")
                if s.last_updated_at:
                    end = s.last_updated_at.astimezone().strftime("%H:%M")
                    time_range = f"{created} - {end}"
                else:
                    time_range = created
                mode = f"[{s.mode}] " if s.mode else ""
                label = s.name or s.subtitle or "(unnamed)"
                lines.append(f"- {mode}{_truncate(label)} ({time_range})")
            lines.append("")

    if not git_data and not claude_data and not codex_data and not cursor_data:
        lines.append("*No activity found for the specified date range.*")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Collect worklog data from Git, Claude Code, Codex, and Cursor sessions."
    )
    parser.add_argument("--workspace-root", required=True, help="Root directory containing git repos")
    parser.add_argument("--git-author", required=True, help="Git author name to filter commits")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD, defaults to today)")
    parser.add_argument("--since", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive)")
    args = parser.parse_args()

    today = date.today()

    if args.date and (args.since or args.until):
        print("Error: Cannot use --date together with --since/--until", file=sys.stderr)
        sys.exit(1)

    if args.date:
        d = date.fromisoformat(args.date)
        date_from, date_until = d, d
    elif args.since:
        date_from = date.fromisoformat(args.since)
        date_until = date.fromisoformat(args.until) if args.until else today
    elif args.until:
        d = date.fromisoformat(args.until)
        date_from, date_until = d, d
    else:
        date_from, date_until = today, today

    workspace_root = args.workspace_root
    git_author = args.git_author

    config = Config(
        workspace_root=Path(workspace_root),
        git_author=git_author,
        date_from=date_from,
        date_until=date_until,
    )

    git_data = collect_git_commits(config)
    claude_data = collect_claude_sessions(config)
    codex_data = collect_codex_sessions(config)
    cursor_data = collect_cursor_sessions(config)

    report = generate_report(config, git_data, claude_data, codex_data, cursor_data)
    sys.stdout.write(report)


if __name__ == "__main__":
    main()
