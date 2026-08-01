import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.installer import Installer


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.source = Path(__file__).resolve().parents[1]
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".claude").mkdir(parents=True)
        self.codex_config = self.home / ".codex" / "config.toml"
        self.codex_hooks = self.home / ".codex" / "hooks.json"
        self.claude_settings = self.home / ".claude" / "settings.json"
        self.codex_instructions = self.home / ".codex" / "AGENTS.md"
        self.claude_instructions = self.home / ".claude" / "CLAUDE.md"
        instruction_text = (
            "# Rules\n\n"
            "## Memory Model\n\n- Old memory layer\n\n"
            "## Worktree Hygiene\n\nPreserve dirty worktrees.\n\n"
            "## Keep This\n\nPreserve this rule.\n"
        )
        self.codex_instructions.write_text(instruction_text)
        self.claude_instructions.write_text(instruction_text)
        self.codex_config.write_text(
            'model = "gpt-test"\napproval_policy = "never"\n'
            'approvals_reviewer = "user"\nsandbox_mode = "danger-full-access"\n'
            '[mcp_servers.github]\ncommand = "npx"\n'
            'args = ["@modelcontextprotocol/server-github"]\n'
            '[mcp_servers.example]\nurl = "https://example.com"\n'
            '[mcp_servers.playwright]\ncommand = "npx"\nargs = ["@playwright/mcp"]\n'
        )
        self.codex_hooks.write_text('{"hooks":{"PreToolUse":[]}}\n')
        self.claude_settings.write_text(
            json.dumps({"enabledPlugins": {"example": True}, "hooks": {"PreToolUse": []}})
        )
        self.claude_user = self.home / ".claude.json"
        self.claude_user.write_text(
            '{"mcpServers":{"example":{"type":"http","url":"https://example.com"}}}\n'
        )
        self.original = {
            "config": self.codex_config.read_bytes(),
            "hooks": self.codex_hooks.read_bytes(),
            "settings": self.claude_settings.read_bytes(),
            "codex_instructions": self.codex_instructions.read_bytes(),
            "claude_instructions": self.claude_instructions.read_bytes(),
            "claude_user": self.claude_user.read_bytes(),
        }
        self.reme_patch = patch.object(
            Installer,
            "_install_reme_tool",
            return_value=(
                self.home
                / ".agent-runtime"
                / "tools"
                / "reme"
                / "0.4.1.3"
                / "bin"
                / "reme"
            ),
        )
        self.reme_patch.start()

    def tearDown(self):
        self.reme_patch.stop()
        self.temp_dir.cleanup()

    def test_install_replaces_hooks_and_preserves_unrelated_configuration(self):
        receipt = Installer(source_root=self.source, home=self.home).install(load_launchd=False)

        codex_hooks = json.loads(self.codex_hooks.read_text())
        claude_settings = json.loads(self.claude_settings.read_text())
        config = self.codex_config.read_text()
        self.assertIn("PreToolUse", codex_hooks["hooks"])
        self.assertIn("PermissionRequest", codex_hooks["hooks"])
        self.assertIn("UserPromptSubmit", codex_hooks["hooks"])
        self.assertIn("SubagentStart", codex_hooks["hooks"])
        self.assertEqual(3, codex_hooks["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"])
        self.assertEqual({"example": True}, claude_settings["enabledPlugins"])
        self.assertIn("PostToolUseFailure", claude_settings["hooks"])
        self.assertIn('approval_policy = "on-request"', config)
        self.assertIn('approvals_reviewer = "auto_review"', config)
        self.assertIn('sandbox_mode = "workspace-write"', config)
        self.assertIn("[sandbox_workspace_write]", config)
        self.assertIn("network_access = true", config)
        self.assertIn("[mcp_servers.github]", config)
        self.assertIn("[mcp_servers.example]", config)
        self.assertIn('"@playwright/mcp"', config)
        self.assertIn("[mcp_servers.reme]", config)
        self.assertIn('url = "http://127.0.0.1:2333/mcp"', config)
        claude_user = json.loads(self.claude_user.read_text())
        self.assertEqual("http://127.0.0.1:2333/mcp", claude_user["mcpServers"]["reme"]["url"])
        self.assertIn("example", claude_user["mcpServers"])
        for instructions in (self.codex_instructions, self.claude_instructions):
            text = instructions.read_text()
            self.assertIn("ReMe", text)
            self.assertNotIn("Old memory layer", text)
            self.assertIn("Preserve dirty worktrees.", text)
            self.assertIn("Preserve this rule.", text)
        self.assertRegex(receipt["source_revision"], r"^[0-9a-f]{40}$")
        self.assertIn("source_dirty", receipt)
        self.assertNotIn("quarantine", receipt)

    def test_install_does_not_touch_unowned_logs_or_skills(self):
        log = self.home / ".claude" / "bash-commands.log"
        log.write_text("user-owned log\n")
        skill = self.home / ".agents" / "skills" / "custom-skill" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("user-owned skill\n")

        Installer(source_root=self.source, home=self.home).install(load_launchd=False)

        self.assertEqual("user-owned log\n", log.read_text())
        self.assertEqual("user-owned skill\n", skill.read_text())

    def test_install_is_idempotent(self):
        installer = Installer(source_root=self.source, home=self.home)
        installer.install(load_launchd=False)
        first = {
            "config": self.codex_config.read_bytes(),
            "hooks": self.codex_hooks.read_bytes(),
            "settings": self.claude_settings.read_bytes(),
        }

        installer.install(load_launchd=False)

        self.assertEqual(first["config"], self.codex_config.read_bytes())
        self.assertEqual(first["hooks"], self.codex_hooks.read_bytes())
        self.assertEqual(first["settings"], self.claude_settings.read_bytes())

    def test_rollback_restores_configuration_bytes(self):
        installer = Installer(source_root=self.source, home=self.home)
        receipt = installer.install(load_launchd=False)

        with patch("agent_runtime.installer.subprocess.run") as run:
            installer.rollback(Path(receipt["backup"]))

        self.assertFalse(any(call.args[0][0] == "launchctl" for call in run.call_args_list))
        self.assertEqual(self.original["config"], self.codex_config.read_bytes())
        self.assertEqual(self.original["hooks"], self.codex_hooks.read_bytes())
        self.assertEqual(self.original["settings"], self.claude_settings.read_bytes())
        self.assertEqual(self.original["codex_instructions"], self.codex_instructions.read_bytes())
        self.assertEqual(self.original["claude_instructions"], self.claude_instructions.read_bytes())
        self.assertEqual(self.original["claude_user"], self.claude_user.read_bytes())
        self.assertFalse((self.home / ".agent-runtime" / "current").exists())
        self.assertFalse((self.home / ".agent-runtime" / "install-receipt.json").exists())

    def test_install_failure_rolls_back_before_raising(self):
        installer = Installer(source_root=self.source, home=self.home)

        with patch.object(installer, "_install_launchd", side_effect=RuntimeError("launchd failed")):
            with self.assertRaisesRegex(RuntimeError, "launchd failed"):
                installer.install(load_launchd=True)

        self.assertEqual(self.original["config"], self.codex_config.read_bytes())
        self.assertEqual(self.original["hooks"], self.codex_hooks.read_bytes())
        self.assertEqual(self.original["settings"], self.claude_settings.read_bytes())

    def test_reme_profile_is_minimal_and_workspace_is_private(self):
        config = (self.source / "agent_runtime" / "reme-minimal.yaml").read_text()
        self.assertIn("expand_links: false", config)
        for forbidden in ("agent_wrapper", "auto_memory", "auto_dream"):
            self.assertNotIn(forbidden, config)
        self.assertIn('embedding_store: ""', config)

        workspace = self.home / ".local" / "share" / "reme" / "workspace"
        nested = workspace / "digest" / "wiki" / "memory.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("private memory\n")
        workspace.chmod(0o755)
        nested.parent.chmod(0o755)
        nested.chmod(0o644)

        Installer(source_root=self.source, home=self.home)._harden_reme_workspace()

        self.assertEqual(0o700, stat.S_IMODE(workspace.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(nested.parent.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(nested.stat().st_mode))

    def test_reme_tool_is_created_at_its_final_path(self):
        self.reme_patch.stop()
        installer = Installer(source_root=self.source, home=self.home)
        target = installer.home / ".agent-runtime" / "tools" / "reme" / "0.4.1.3"
        target.mkdir(parents=True)
        (target / "install.json").write_text("{")

        def fake_run(command, **_kwargs):
            if command[1:3] == ["-m", "venv"]:
                (target / "bin").mkdir(parents=True)
                (target / "bin" / "python").write_text("python\n")
                (target / "bin" / "reme").write_text(f"#!{target / 'bin' / 'python'}\n")
            return type("Result", (), {"returncode": 0})()

        with patch("agent_runtime.installer.shutil.which", return_value="/usr/local/bin/python3.11"):
            with patch("agent_runtime.installer.subprocess.run", side_effect=fake_run) as run:
                cli = installer._install_reme_tool()

        self.assertEqual(target / "bin" / "reme", cli)
        self.assertEqual(str(target), run.call_args_list[0].args[0][-1])
        self.assertNotIn(".tmp", cli.read_text())
        self.assertTrue((target / "install.json").is_file())

    def test_reme_call_uses_the_managed_loopback_service(self):
        cli = Path("/managed/reme")
        completed = subprocess.CompletedProcess([], 0, "ok", "")

        with patch("agent_runtime.installer.subprocess.run", return_value=completed) as run:
            output = Installer._reme_call(cli, "version")

        self.assertEqual("ok", output)
        self.assertEqual(
            [
                str(cli),
                "version",
                "backend=mcp",
                "transport=streamable-http",
                "host=127.0.0.1",
                "port=2333",
            ],
            run.call_args.args[0],
        )

    def test_reme_bootstrap_retries_launchd_transition_failure(self):
        installer = Installer(source_root=self.source, home=self.home)
        plist = (
            self.home
            / "Library"
            / "LaunchAgents"
            / "io.github.hanzw.agent-runtime.reme.plist"
        )
        plist.parent.mkdir(parents=True)
        plist.write_text("synthetic\n")
        failure = subprocess.CompletedProcess([], 5)
        success = subprocess.CompletedProcess([], 0)

        with patch.object(installer, "_can_manage_launchd", return_value=True):
            with patch(
                "agent_runtime.installer.subprocess.run",
                side_effect=(failure, success),
            ) as run:
                installer._bootstrap_reme_service(strict=True)

        self.assertEqual(2, run.call_count)


if __name__ == "__main__":
    unittest.main()
