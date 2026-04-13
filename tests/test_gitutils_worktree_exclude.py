"""Tests for _write_worktree_exclude and the new ensure_worktree behaviour.

Covers:
- _write_worktree_exclude creates exclude file with .takt/beads/ when absent
- _write_worktree_exclude is idempotent (no duplicate lines on second call)
- _write_worktree_exclude appends to an existing exclude file without clobbering it
- ensure_worktree early-return path does NOT call _write_worktree_exclude
- ensure_worktree calls git rm --cached and git commit --allow-empty after worktree add
- ensure_worktree raises GitError when git rm returns non-zero
- ensure_worktree raises GitError when git commit returns non-zero
- _write_worktree_exclude is called once for a freshly created worktree
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import GitError, WorktreeManager, _write_worktree_exclude


class WriteWorktreeExcludeTests(unittest.TestCase):
    """Unit tests for the module-level _write_worktree_exclude helper."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".git").mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _exclude_file(self, worktree_name: str) -> Path:
        return self.root / ".git" / "worktrees" / worktree_name / "info" / "exclude"

    def test_creates_exclude_file_when_not_present(self) -> None:
        """Creates the per-worktree exclude file containing .takt/beads/."""
        worktree_path = self.root / ".takt" / "worktrees" / "B-abc12345"
        _write_worktree_exclude(self.root, worktree_path)
        exclude_file = self._exclude_file("B-abc12345")
        self.assertTrue(exclude_file.exists(), "exclude file was not created")
        self.assertIn(".takt/beads/", exclude_file.read_text())

    def test_idempotent_no_duplicate_lines(self) -> None:
        """Calling _write_worktree_exclude twice does not add a duplicate entry."""
        worktree_path = self.root / ".takt" / "worktrees" / "B-abc12345"
        _write_worktree_exclude(self.root, worktree_path)
        _write_worktree_exclude(self.root, worktree_path)
        exclude_file = self._exclude_file("B-abc12345")
        lines = [l for l in exclude_file.read_text().splitlines() if l.strip() == ".takt/beads/"]
        self.assertEqual(1, len(lines), "expected exactly one .takt/beads/ entry")

    def test_appends_to_existing_exclude_file_without_clobbering(self) -> None:
        """Appends .takt/beads/ when the file already exists with other content."""
        worktree_path = self.root / ".takt" / "worktrees" / "B-abc12345"
        exclude_dir = self.root / ".git" / "worktrees" / "B-abc12345" / "info"
        exclude_dir.mkdir(parents=True, exist_ok=True)
        exclude_file = exclude_dir / "exclude"
        exclude_file.write_text("existing-pattern\n")
        _write_worktree_exclude(self.root, worktree_path)
        content = exclude_file.read_text()
        self.assertIn("existing-pattern", content, "pre-existing content was removed")
        self.assertIn(".takt/beads/", content, ".takt/beads/ was not appended")


class EnsureWorktreeExcludeIntegrationTests(unittest.TestCase):
    """Integration tests for ensure_worktree's exclude and git rm/commit steps."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.worktrees_dir = self.root / ".takt" / "worktrees"
        self.wm = WorktreeManager(self.root, self.worktrees_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _ok_proc(self, stdout: str = "") -> MagicMock:
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = stdout
        proc.stderr = ""
        return proc

    def _fail_proc(self, stderr: str = "git error") -> MagicMock:
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = stderr
        return proc

    def test_early_return_skips_write_exclude(self) -> None:
        """If the worktree directory already exists, _write_worktree_exclude is not called."""
        target = self.wm.worktrees_dir / "B-abc12345"  # use resolved path from wm
        target.mkdir(parents=True, exist_ok=True)
        with (
            patch.object(self.wm, "ensure_repository"),
            patch("agent_takt.gitutils._write_worktree_exclude") as mock_exclude,
        ):
            result = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")
        mock_exclude.assert_not_called()
        self.assertEqual(target, result)

    def test_git_rm_and_commit_called_with_correct_args(self) -> None:
        """After worktree add, git rm --cached and git commit --allow-empty are invoked."""
        ok = self._ok_proc()
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude"),
            patch("agent_takt.gitutils.subprocess.run", return_value=ok) as mock_run,
        ):
            target = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

        self.assertEqual(self.wm.worktrees_dir / "B-abc12345", target)
        self.assertEqual(2, len(mock_run.call_args_list), "expected exactly 2 subprocess.run calls")

        rm_cmd = mock_run.call_args_list[0].args[0]
        self.assertIn("git", rm_cmd)
        self.assertIn("-C", rm_cmd)
        self.assertIn(str(target), rm_cmd)
        self.assertIn("rm", rm_cmd)
        self.assertIn("--cached", rm_cmd)
        self.assertIn(".takt/beads/", rm_cmd)

        commit_cmd = mock_run.call_args_list[1].args[0]
        self.assertIn("git", commit_cmd)
        self.assertIn("-C", commit_cmd)
        self.assertIn(str(target), commit_cmd)
        self.assertIn("commit", commit_cmd)
        self.assertIn("--allow-empty", commit_cmd)

    def test_git_rm_failure_raises_git_error(self) -> None:
        """GitError is raised when the git rm step fails."""
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude"),
            patch("agent_takt.gitutils.subprocess.run", return_value=self._fail_proc("rm failed")),
        ):
            with self.assertRaises(GitError):
                self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

    def test_git_commit_failure_raises_git_error(self) -> None:
        """GitError is raised when the git commit step fails."""
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude"),
            patch(
                "agent_takt.gitutils.subprocess.run",
                side_effect=[self._ok_proc(), self._fail_proc("commit failed")],
            ),
        ):
            with self.assertRaises(GitError):
                self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

    def test_write_exclude_called_once_for_new_worktree(self) -> None:
        """_write_worktree_exclude is invoked exactly once with the correct arguments."""
        ok = self._ok_proc()
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude") as mock_exclude,
            patch("agent_takt.gitutils.subprocess.run", return_value=ok),
        ):
            target = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

        mock_exclude.assert_called_once_with(self.wm.root, target)


if __name__ == "__main__":
    unittest.main()
