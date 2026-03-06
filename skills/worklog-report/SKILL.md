---
name: worklog-report
description: Generate worklog reports from Git commits, Claude Code sessions, and Cursor sessions with AI summary. Use for daily reports, weekly reports, and work summaries.
disable-model-invocation: true
argument-hint: "Generate this week's worklog for workspace"
allowed-tools: "Bash(python3 *), Bash(git config *)"
---

Generate a worklog report with the following steps:

1. Determine `WORKSPACE_ROOT` and `GIT_AUTHOR` before running any script:
   - Both should come from the user's environment, request, or CLAUDE.md settings. If not available, ask the user.
   - `WORKSPACE_ROOT` is the root directory containing the target git repositories.
   - `GIT_AUTHOR` is the git author name to filter commits. It can be obtained via `git config user.name`.

2. Convert the user's natural-language request into explicit `collect.py` arguments:
   - Never pass raw `$ARGUMENTS` directly to `collect.py`.
   - Only pass supported flags: `--workspace-root`, `--date`, `--since`, `--until`.
   - If the user mentions a workspace by name such as "caiyun", resolve it to a concrete absolute path before running the script.
   - If the request describes a date range in natural language, convert it to ISO dates first.
   - If any required value is ambiguous or cannot be resolved confidently, ask a clarifying question instead of running the script.

3. Use these date conversion rules:
   - "today" -> `--date YYYY-MM-DD`
   - "yesterday" -> `--date YYYY-MM-DD`
   - "this week" -> `--since` Monday of the current week, `--until` today
   - "last week" -> `--since` Monday of the previous week, `--until` Sunday of the previous week
   - Explicit dates or ranges should be converted directly to `--date` or `--since` + `--until`

4. Run the data collection script only after the arguments have been fully resolved:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/collect.py \
  --git-author "$GIT_AUTHOR" \
  --workspace-root "$WORKSPACE_ROOT" \
  [--date YYYY-MM-DD | --since YYYY-MM-DD --until YYYY-MM-DD]
```

5. Examples of correct conversions:
   - `/worklog-report generate this week's worklog for caiyun`
     -> resolve `WORKSPACE_ROOT` to the caiyun workspace path
     -> convert "this week" to `--since` Monday of the current week and `--until` today
   - `/worklog-report summarize yesterday's work`
     -> keep the default workspace if configured
     -> convert "yesterday" to `--date YYYY-MM-DD`
   - `/worklog-report --workspace-root /path/to/ws --since 2026-03-02 --until 2026-03-06`
     -> use the provided flags directly

6. Based on the raw data from stdout, generate a concise summary of the work done:
   - Group by project
   - Highlight main accomplishments and key changes
   - Do NOT include specific times, only summarize the work
   - Output the summary directly without extra headings or prefixes

7. Output the final report directly in the conversation:
   - Start with the title line from the raw report
   - Insert `## Summary\n\n{summary}\n` after the title
   - Do NOT append the rest of the raw report content (Git Commits, Claude Code Sessions, Cursor Sessions sections)
