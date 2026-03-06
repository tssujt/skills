# skills

## Available Skills

### worklog-report

Generate worklog reports from Git commits, Claude Code sessions, and Cursor sessions with AI summary.

**Usage:**

```
/worklog-report
```

**Data sources:**

- **Git Commits** - scans all git repos under `WORKSPACE_ROOT`, filters by author
- **Claude Code Sessions** - reads `~/.claude/projects/*/sessions-index.json`
- **Cursor Sessions** - reads Cursor's `workspaceStorage/*/state.vscdb` (composer data)

**Output:**

Directly outputs the report in the conversation, containing:

- AI-generated summary (grouped by project)
- Git Commits (with timestamps)
- Claude Code Sessions (with time ranges and message counts)
- Cursor Sessions (with time ranges and modes)
