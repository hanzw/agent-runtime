import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_runtime.cli import run


class HookCliTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def invoke(self, event: str, payload: dict) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        raw = json.dumps(payload)
        with patch.dict("os.environ", {"HOME": str(self.home)}, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = run(event, raw, home=self.home)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_pre_tool_use_blocks_destructive_codex_stdin(self):
        code, stdout, stderr = self.invoke(
            "PreToolUse",
            {
                "tool_name": "exec_command",
                "tool_input": {"cmd": "git reset --hard"},
                "cwd": str(self.project),
            },
        )

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("destructive", stderr.lower())

    def test_permission_request_denies_classified_danger_without_prompt(self):
        code, stdout, stderr = self.invoke(
            "PermissionRequest",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin HEAD:main"},
                "cwd": str(self.project),
            },
        )

        response = json.loads(stdout)
        decision = response["hookSpecificOutput"]["decision"]
        self.assertEqual(0, code)
        self.assertEqual("deny", decision["behavior"])
        self.assertIn("protected branch", decision["message"].lower())
        self.assertEqual("", stderr)

    def test_prompt_injects_only_reme_context(self):
        sentinel = "sentinel-private-prompt"
        memory_context = "ReMe recalled durable context."
        with patch("agent_runtime.cli.ReMeMemory.context", return_value=memory_context):
            code, stdout, _ = self.invoke(
                "UserPromptSubmit",
                {"prompt": sentinel, "cwd": str(self.project), "session_id": "s1"},
            )

        response = json.loads(stdout)
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(0, code)
        self.assertEqual(memory_context, context)
        self.assertNotIn("capability", context.lower())
        state_text = "".join(
            path.read_text(errors="ignore")
            for path in (self.home / ".agent-runtime" / "state").rglob("*")
            if path.is_file()
        )
        self.assertNotIn(sentinel, state_text)

    def test_session_start_declares_native_skill_discovery(self):
        with patch("agent_runtime.cli._ensure_reme", return_value=True):
            code, stdout, _ = self.invoke(
                "SessionStart",
                {"cwd": str(self.project), "session_id": "s1"},
            )

        context = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(0, code)
        self.assertIn("Native Codex/Claude Skill discovery", context)
        self.assertNotIn("Canonical skills", context)
        self.assertFalse((self.home / ".agent-runtime" / "state" / "registries").exists())

    def test_subagent_start_receives_global_policy_context(self):
        code, stdout, _ = self.invoke(
            "SubagentStart",
            {"cwd": str(self.project), "session_id": "s1", "agent_type": "reviewer"},
        )

        response = json.loads(stdout)
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(0, code)
        self.assertEqual("SubagentStart", response["hookSpecificOutput"]["hookEventName"])
        self.assertIn("side-effect policy", context)

    def test_maintenance_writes_private_healthy_heartbeat(self):
        reme_logs = [
            self.home / "Library" / "Logs" / "reme" / "service.log",
            self.home / "Library" / "Logs" / "reme" / "service-error.log",
        ]
        for path in reme_logs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("service event\n")
            path.chmod(0o644)

        with patch("agent_runtime.cli._ensure_reme", return_value=True):
            code, stdout, stderr = self.invoke("Maintenance", {})

        heartbeat_path = self.home / ".agent-runtime" / "state" / "heartbeat.json"
        heartbeat = json.loads(heartbeat_path.read_text())
        self.assertEqual(0, code)
        self.assertEqual("{}\n", stdout)
        self.assertEqual("", stderr)
        self.assertEqual(0o600, heartbeat_path.stat().st_mode & 0o777)
        self.assertEqual("healthy", heartbeat["reme_status"])
        self.assertEqual("ok", heartbeat["status"])
        for path in reme_logs:
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_maintenance_is_degraded_when_reme_cannot_be_recovered(self):
        with patch("agent_runtime.cli._ensure_reme", return_value=False):
            code, stdout, stderr = self.invoke("Maintenance", {})

        heartbeat = json.loads(
            (self.home / ".agent-runtime" / "state" / "heartbeat.json").read_text()
        )
        self.assertEqual(0, code)
        self.assertEqual("{}\n", stdout)
        self.assertEqual("", stderr)
        self.assertEqual("degraded", heartbeat["status"])
        self.assertEqual("unavailable", heartbeat["reme_status"])


if __name__ == "__main__":
    unittest.main()
