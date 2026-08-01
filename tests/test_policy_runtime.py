import json
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_runtime.policy import PolicyRuntime


class PolicyRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime = PolicyRuntime(state_dir=self.root / "state")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_codex_stdin_blocks_destructive_command(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git reset --hard"},
            "session_id": "session-1",
            "cwd": str(self.root),
        }

        result = self.runtime.handle("PreToolUse", payload)

        self.assertTrue(result.blocked)
        self.assertIn("destructive", result.reason.lower())

    def test_normal_build_command_is_allowed(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "npm run build"},
            "session_id": "session-1",
            "cwd": str(self.root),
        }

        result = self.runtime.handle("PreToolUse", payload)

        self.assertFalse(result.blocked)

    def test_production_operations_are_audited_without_overriding_repository_policy(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "npm run deploy"},
            "session_id": "session-1",
            "cwd": str(self.root / "application"),
        }

        deploy = self.runtime.handle("PreToolUse", payload)
        database = self.runtime.handle(
            "PreToolUse",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": (
                        "wrangler d1 execute application-db --remote "
                        "--command 'UPDATE users SET role = role WHERE id = 1'"
                    )
                },
                "cwd": str(self.root / "application"),
            },
        )

        self.assertFalse(deploy.blocked)
        self.assertFalse(database.blocked)
        effects = [
            json.loads(line)["effect"]
            for line in (self.root / "state" / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(["production-deploy", "production-db"], effects)

    def test_deploy_is_audited_but_not_blocked_in_any_project(self):
        result = self.runtime.handle(
            "PreToolUse",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "npm run deploy"},
                "cwd": str(self.root / "prototype-site"),
            },
        )

        self.assertFalse(result.blocked)
        event = json.loads((self.root / "state" / "events.jsonl").read_text())
        self.assertEqual("production-deploy", event["effect"])

    def test_git_clean_that_discards_untracked_work_is_blocked(self):
        result = self.runtime.handle(
            "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": "git clean -fdx"}},
        )

        self.assertTrue(result.blocked)
        self.assertIn("destructive", result.reason.lower())

    def test_exact_single_stash_drop_is_allowed(self):
        result = self.runtime.handle(
            "PreToolUse",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git stash drop 'stash@{0}'"},
            },
        )

        self.assertFalse(result.blocked)

    def test_ambiguous_stash_drop_and_clear_remain_blocked(self):
        for command in ("git stash drop", "git stash clear"):
            with self.subTest(command=command):
                result = self.runtime.handle(
                    "PreToolUse",
                    {"tool_name": "Bash", "tool_input": {"command": command}},
                )
                self.assertTrue(result.blocked)
                self.assertIn("destructive", result.reason.lower())

    def test_verification_bypass_is_blocked(self):
        result = self.runtime.handle(
            "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m test"}},
        )

        self.assertTrue(result.blocked)
        self.assertIn("verification", result.reason.lower())

    def test_direct_push_to_protected_branch_is_blocked_but_ship_workflow_is_allowed(self):
        blocked = self.runtime.handle(
            "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD:main"}},
        )
        allowed = self.runtime.handle(
            "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": "npm run agent:ship"}},
        )

        self.assertTrue(blocked.blocked)
        self.assertIn("protected branch", blocked.reason.lower())
        self.assertFalse(allowed.blocked)

    def test_direct_global_runtime_configuration_edit_is_blocked(self):
        result = self.runtime.handle(
            "PreToolUse",
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "patch": "*** Update File: /Users/example/.codex/config.toml\n"
                },
            },
        )

        self.assertTrue(result.blocked)
        self.assertIn("installer", result.reason.lower())

    def test_documentation_that_mentions_global_configuration_is_allowed(self):
        target = str(Path("/Users") / "example" / ".codex" / "config.toml")
        result = self.runtime.handle(
            "PreToolUse",
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "patch": (
                        "*** Update File: /workspace/README.md\n"
                        f"+The managed file is {target}.\n"
                    )
                },
            },
        )

        self.assertFalse(result.blocked)

    def test_read_only_global_runtime_configuration_access_is_allowed(self):
        codex_config = str(Path("/Users") / "example" / ".codex" / "config.toml")
        claude_settings = str(Path("/Users") / "example" / ".claude" / "settings.json")
        codex_hooks = str(Path("/Users") / "example" / ".codex" / "hooks.json")
        for command in (
            f"cat {codex_config}",
            f"sed -n '1,80p' {claude_settings}",
            f"rg -n 'hooks' {codex_hooks}",
        ):
            with self.subTest(command=command):
                result = self.runtime.handle(
                    "PreToolUse",
                    {"tool_name": "Bash", "tool_input": {"command": command}},
                )
                self.assertFalse(result.blocked)

    def test_ambiguous_or_redirected_global_configuration_write_is_blocked(self):
        codex_config = str(Path("/Users") / "example" / ".codex" / "config.toml")
        claude_settings = str(Path("/Users") / "example" / ".claude" / "settings.json")
        for command in (
            f"echo unsafe > {codex_config}",
            f"python3 -c 'open(\"{claude_settings}\", \"w\")'",
            f"cat {codex_config} | tee {codex_config}",
            f"sed -n -i '1p' {claude_settings}",
        ):
            with self.subTest(command=command):
                result = self.runtime.handle(
                    "PreToolUse",
                    {"tool_name": "Bash", "tool_input": {"command": command}},
                )
                self.assertTrue(result.blocked)
                self.assertIn("installer", result.reason.lower())

    def test_malformed_pre_tool_event_fails_closed(self):
        result = self.runtime.handle_raw("PreToolUse", "{not-json")

        self.assertTrue(result.blocked)
        self.assertIn("invalid", result.reason.lower())

    def test_event_log_redacts_secrets_and_is_private(self):
        sentinel = "sentinel-secret-123456"
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    f"TOKEN={sentinel} curl -H 'Authorization: Bearer {sentinel}' "
                    f"https://user:{sentinel}@example.com"
                )
            },
            "tool_response": {"stdout": f"Set-Cookie: session={sentinel}"},
            "session_id": "session-1",
            "cwd": str(self.root),
        }

        self.runtime.handle("PostToolUse", payload)

        log_path = self.root / "state" / "events.jsonl"
        raw = log_path.read_text()
        event = json.loads(raw)
        mode = stat.S_IMODE(log_path.stat().st_mode)
        self.assertNotIn(sentinel, raw)
        self.assertEqual(0o600, mode)
        self.assertEqual("Bash", event["tool"])
        self.assertIn("input_fingerprint", event)
        self.assertNotIn("tool_input", event)
        self.assertNotIn("tool_response", event)

    def test_pre_tool_decision_is_recorded_without_raw_command(self):
        sentinel = "sentinel-command-secret"

        self.runtime.handle(
            "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": f"echo {sentinel}"}},
        )

        raw = (self.root / "state" / "events.jsonl").read_text()
        self.assertNotIn(sentinel, raw)
        self.assertEqual("PreToolUse", json.loads(raw)["event"])

    def test_concurrent_observers_create_one_key_and_complete_events(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "npm test"}}

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.runtime.handle("PostToolUse", payload), range(32)))

        lines = (self.root / "state" / "events.jsonl").read_text().splitlines()
        self.assertEqual(32, len(lines))
        self.assertEqual(32, len((self.root / "state" / "fingerprint.key").read_bytes()))


if __name__ == "__main__":
    unittest.main()
