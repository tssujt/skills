import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("worklog_collect", SCRIPT_PATH)
collect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collect
SPEC.loader.exec_module(collect)


class KimiSessionTests(unittest.TestCase):
    def test_collects_indexed_sessions_in_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            repo = workspace / "project-a"
            repo.mkdir(parents=True)
            kimi_home = root / ".kimi-code"
            session_dir = kimi_home / "sessions" / "wd_project-a" / "session-1"
            session_dir.mkdir(parents=True)
            (session_dir / "state.json").write_text(json.dumps({
                "createdAt": "2026-07-21T01:02:03.000Z",
                "updatedAt": "2026-07-21T04:05:06.000Z",
                "title": "Add Kimi Code support",
            }))
            kimi_home.mkdir(exist_ok=True)
            (kimi_home / "session_index.jsonl").write_text("\n".join([
                json.dumps({
                    "sessionId": "session-1",
                    "sessionDir": str(session_dir),
                    "workDir": str(repo),
                }),
                "not-json",
                json.dumps({
                    "sessionId": "missing-state",
                    "sessionDir": str(kimi_home / "sessions" / "missing"),
                    "workDir": str(repo),
                }),
            ]))

            config = collect.Config(
                workspace_root=workspace,
                git_author="Example",
                date_from=date(2026, 7, 21),
                date_until=date(2026, 7, 21),
            )
            config.kimi_code_home = kimi_home

            sessions = collect.collect_kimi_sessions(config)

            self.assertEqual(["project-a"], list(sessions))
            self.assertEqual("Add Kimi Code support", sessions["project-a"][0].title)

    def test_report_includes_kimi_sessions(self):
        config = collect.Config(
            workspace_root=Path("/workspace"),
            git_author="Example",
            date_from=date(2026, 7, 21),
            date_until=date(2026, 7, 21),
        )
        session = collect.KimiSession(
            title="Investigate an issue",
            work_dir="/workspace/project-a",
            created_at=datetime(2026, 7, 21, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 21, 2, tzinfo=timezone.utc),
        )

        report = collect.generate_report(
            config, {}, {}, {}, {"project-a": [session]}, {}
        )

        self.assertIn("## Kimi Code Sessions", report)
        self.assertIn("### project-a", report)
        self.assertIn("Investigate an issue", report)


if __name__ == "__main__":
    unittest.main()
