import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.memory import ReMeMemory


class ReMeMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.project = self.home / "Documents" / "example-app"
        self.project.mkdir(parents=True)
        self.memory = ReMeMemory(self.home)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("agent_runtime.memory.subprocess.run")
    def test_recall_keeps_only_global_and_current_project_hits(self, run):
        key = self.memory.project_key(self.project)
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "========== digest/personal/global/preferences.md:5-7 [score=1.2] ==========\n"
                "Use concise Chinese.\n"
                f"========== digest/wiki/projects/{key}/release.md:4-8 [score=1.1] ==========\n"
                "Staging evidence is required.\n"
                "========== digest/wiki/projects/another-project-deadbeef/private.md:1-2 "
                "[score=1.3] ==========\n"
                "Do not leak this project.\n"
            ),
            stderr="",
        )

        hits = self.memory.recall("release rules", self.project)

        self.assertEqual(2, len(hits))
        self.assertTrue(all("another-project" not in hit.path for hit in hits))
        self.assertIn("example-app release rules", run.call_args.args[0][2])

    @patch("agent_runtime.memory.subprocess.run", side_effect=subprocess.TimeoutExpired("reme", 2))
    def test_recall_fails_open_when_reme_is_unavailable(self, _run):
        self.assertEqual([], self.memory.recall("prior decision", self.project))

    @patch("agent_runtime.memory.subprocess.run")
    def test_health_requires_an_exact_healthy_status(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, "ReMe v0.4.1.3 - unhealthy\n", ""
        )
        self.assertFalse(self.memory.healthy())

        run.return_value = subprocess.CompletedProcess(
            [], 0, "ReMe v0.4.1.3 - healthy\n", ""
        )
        self.assertTrue(self.memory.healthy())

    def test_format_context_is_bounded_and_identifies_reme_paths(self):
        key = self.memory.project_key(self.project)
        output = (
            f"========== digest/wiki/projects/{key}/decision.md:1-9 [score=1.0] ==========\n"
            + ("evidence " * 1000)
        )
        with patch(
            "agent_runtime.memory.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ):
            context = self.memory.context("decision", self.project)

        self.assertIn("ReMe recalled", context)
        self.assertIn("decision.md", context)
        self.assertLessEqual(len(context), 4096)

    @patch("agent_runtime.memory.subprocess.run")
    def test_worktree_uses_the_main_repository_memory_namespace(self, run):
        git_dir = self.project / ".git"
        worktree = self.home / "worktrees" / "release-candidate"
        worktree_git_dir = git_dir / "worktrees" / "release-candidate"
        git_dir.mkdir()
        worktree_git_dir.mkdir(parents=True)
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {worktree_git_dir}\n")

        self.assertEqual(
            self.memory.project_key(self.project),
            self.memory.project_key(worktree),
        )
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        self.memory.recall("release rules", worktree)

        self.assertIn("example-app release rules", run.call_args.args[0][2])

    def test_submodule_keeps_its_own_memory_namespace(self):
        git_dir = self.project / ".git"
        submodule = self.project / "vendor" / "library"
        submodule_git_dir = git_dir / "modules" / "vendor" / "library"
        git_dir.mkdir()
        submodule_git_dir.mkdir(parents=True)
        submodule.mkdir(parents=True)
        (submodule / ".git").write_text(f"gitdir: {submodule_git_dir}\n")

        self.assertNotEqual(
            self.memory.project_key(self.project),
            self.memory.project_key(submodule),
        )
        self.assertTrue(self.memory.project_key(submodule).startswith("library-"))

    def test_worktree_submodule_keeps_its_own_memory_namespace(self):
        git_dir = self.project / ".git"
        worktree = self.home / "worktrees" / "release-candidate"
        worktree_git_dir = git_dir / "worktrees" / "release-candidate"
        submodule = worktree / "vendor" / "library"
        submodule_git_dir = worktree_git_dir / "modules" / "vendor" / "library"
        git_dir.mkdir()
        worktree_git_dir.mkdir(parents=True)
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {worktree_git_dir}\n")
        submodule_git_dir.mkdir(parents=True)
        submodule.mkdir(parents=True)
        (submodule / ".git").write_text(f"gitdir: {submodule_git_dir}\n")

        self.assertNotEqual(
            self.memory.project_key(worktree),
            self.memory.project_key(submodule),
        )
        self.assertTrue(self.memory.project_key(submodule).startswith("library-"))


if __name__ == "__main__":
    unittest.main()
