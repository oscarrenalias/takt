"""Tests for the worktree-level bead-state protections in gitutils.

Covers:
- _write_worktree_exclude creates exclude file with .takt/beads/ when absent
- _write_worktree_exclude is idempotent (no duplicate lines on second call)
- _write_worktree_exclude appends to an existing exclude file without clobbering it
- ensure_worktree early-return path retrofits existing worktrees
- ensure_worktree calls git rm --cached and git commit --allow-empty after worktree add
- ensure_worktree raises GitError when git rm returns non-zero
- ensure_worktree raises GitError when git commit returns non-zero
- _write_worktree_exclude is called once for a freshly created worktree
"""
from __future__ import annotations

import subprocess
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

    def test_existing_worktree_runs_retrofit_path(self) -> None:
        """Existing worktrees still pass through the bead-state protection retrofit."""
        target = self.wm.worktrees_dir / "B-abc12345"  # use resolved path from wm
        target.mkdir(parents=True, exist_ok=True)
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "_protect_worktree_bead_state") as mock_protect,
        ):
            result = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")
        mock_protect.assert_called_once_with(target)
        self.assertEqual(target, result)

    def test_git_rm_and_commit_called_with_correct_args(self) -> None:
        """After worktree add, git rm --cached and git commit --allow-empty are invoked."""
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude"),
            patch(
                "agent_takt.gitutils.subprocess.run",
                side_effect=[self._ok_proc("tracked\n"), self._ok_proc(), self._ok_proc()],
            ) as mock_run,
        ):
            target = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

        self.assertEqual(self.wm.worktrees_dir / "B-abc12345", target)
        self.assertEqual(3, len(mock_run.call_args_list), "expected ls-files, rm, and commit calls")

        ls_files_cmd = mock_run.call_args_list[0].args[0]
        self.assertIn("ls-files", ls_files_cmd)
        self.assertIn(".takt/beads", ls_files_cmd)

        rm_cmd = mock_run.call_args_list[1].args[0]
        self.assertIn("git", rm_cmd)
        self.assertIn("-C", rm_cmd)
        self.assertIn(str(target), rm_cmd)
        self.assertIn("rm", rm_cmd)
        self.assertIn("--cached", rm_cmd)
        self.assertIn(".takt/beads/", rm_cmd)

        commit_cmd = mock_run.call_args_list[2].args[0]
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
            patch(
                "agent_takt.gitutils.subprocess.run",
                side_effect=[self._ok_proc("tracked\n"), self._fail_proc("rm failed")],
            ),
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
                side_effect=[
                    self._ok_proc("tracked\n"),
                    self._ok_proc(),
                    self._fail_proc("commit failed"),
                ],
            ),
        ):
            with self.assertRaises(GitError):
                self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

    def test_write_exclude_called_once_for_new_worktree(self) -> None:
        """_write_worktree_exclude is invoked exactly once with the correct arguments."""
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "current_ref", return_value="deadbeef"),
            patch.object(self.wm, "branch_exists", return_value=False),
            patch.object(self.wm, "_run_git"),
            patch("agent_takt.gitutils._write_worktree_exclude") as mock_exclude,
            patch(
                "agent_takt.gitutils.subprocess.run",
                side_effect=[self._ok_proc("tracked\n"), self._ok_proc(), self._ok_proc()],
            ),
        ):
            target = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

        mock_exclude.assert_called_once_with(self.wm.root, target)

    def test_existing_worktree_skips_rm_and_commit_when_branch_already_untracks_beads(self) -> None:
        """Retrofit is a no-op when the existing worktree already stopped tracking bead files."""
        target = self.wm.worktrees_dir / "B-abc12345"
        target.mkdir(parents=True, exist_ok=True)
        with (
            patch.object(self.wm, "ensure_repository"),
            patch.object(self.wm, "_worktree_tracks_bead_state", return_value=False) as mock_tracked,
            patch("agent_takt.gitutils._write_worktree_exclude") as mock_exclude,
            patch("agent_takt.gitutils.subprocess.run") as mock_run,
        ):
            result = self.wm.ensure_worktree("B-abc12345", "feature/b-abc12345")

        self.assertEqual(target, result)
        mock_exclude.assert_called_once_with(self.wm.root, target)
        mock_tracked.assert_called_once_with(target)
        mock_run.assert_not_called()


class WorktreeBeadLeakRegressionTests(unittest.TestCase):
    """Regression coverage for the merge path that previously leaked stale bead state.

    The failure mode was a feature worktree committing `.takt/beads/*` updates that later
    overwrote the main worktree's fresher bead status during `takt merge`. These tests
    document the intended fix: feature branches untrack bead state, worker commits exclude
    bead files, and merges preserve the main worktree's authoritative bead status.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.worktrees_dir = self.root / ".takt" / "worktrees"
        self.wm = WorktreeManager(self.root, self.worktrees_dir)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        (self.root / ".gitattributes").write_text(".takt/beads/** merge=ours\n", encoding="utf-8")
        bead_dir = self.root / ".takt" / "beads"
        bead_dir.mkdir(parents=True, exist_ok=True)
        (bead_dir / "B-root.json").write_text('{"status":"ready"}\n', encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "worker.txt").write_text("base\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "init")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout.strip()

    def test_commit_all_excludes_bead_state_from_worker_commit(self) -> None:
        """Worker commits keep content changes but leave bead JSON out of the commit."""
        worktree = self.wm.ensure_worktree("B-feature", "feature/b-feature")
        (worktree / "src" / "worker.txt").write_text("changed\n", encoding="utf-8")
        (worktree / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"in_progress"}\n',
            encoding="utf-8",
        )

        commit_hash = self.wm.commit_all(worktree, "[takt] B-feature: worker change")

        self.assertIsNotNone(commit_hash)
        names = self._git("show", "--name-only", "--pretty=format:", "HEAD", cwd=worktree).splitlines()
        self.assertIn("src/worker.txt", names)
        self.assertNotIn(".takt/beads/B-root.json", names)

        feature_path_log = self._git(
            "log",
            "--format=%s",
            "feature/b-feature",
            "--",
            ".takt/beads/B-root.json",
        ).splitlines()
        self.assertEqual(
            ["chore: untrack bead state from feature branch [skip ci]", "init"],
            feature_path_log,
        )

    def test_merge_keeps_main_bead_state_when_worker_changes_bead_locally(self) -> None:
        """Main keeps the later bead status when the feature worktree had stale local state."""
        worktree = self.wm.ensure_worktree("B-feature", "feature/b-feature")
        (worktree / "src" / "worker.txt").write_text("changed\n", encoding="utf-8")
        (worktree / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"in_progress"}\n',
            encoding="utf-8",
        )
        self.wm.commit_all(worktree, "[takt] B-feature: worker change")

        (self.root / ".takt" / "beads" / "B-root.json").write_text('{"status":"done"}\n', encoding="utf-8")
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead done")

        self.wm.merge_branch("feature/b-feature")

        self.assertEqual(
            '{"status":"done"}\n',
            (self.root / ".takt" / "beads" / "B-root.json").read_text(encoding="utf-8"),
        )
        feature_path_log = self._git(
            "log",
            "--format=%s",
            "feature/b-feature",
            "--",
            ".takt/beads/B-root.json",
        ).splitlines()
        self.assertEqual(
            ["chore: untrack bead state from feature branch [skip ci]", "init"],
            feature_path_log,
        )

    def test_existing_worktree_is_retrofitted_before_merge(self) -> None:
        """ensure_worktree untracks bead state for a pre-existing feature worktree before merge."""
        self._git("checkout", "-b", "feature/b-feature")
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"feature-branch"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "feature bead snapshot")
        self._git("checkout", "main")

        stale_worktree = self.wm.worktree_path("B-feature")
        stale_worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", str(stale_worktree), "feature/b-feature")

        retrofitted = self.wm.ensure_worktree("B-feature", "feature/b-feature")
        self.assertEqual(stale_worktree, retrofitted)

        (stale_worktree / "src" / "worker.txt").write_text("changed\n", encoding="utf-8")
        (stale_worktree / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"in_progress"}\n',
            encoding="utf-8",
        )
        self.wm.commit_all(stale_worktree, "[takt] B-feature: worker change")

        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"done"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead done")

        self.wm.merge_branch("feature/b-feature")

        self.assertEqual(
            '{"status":"done"}\n',
            (self.root / ".takt" / "beads" / "B-root.json").read_text(encoding="utf-8"),
        )
        feature_path_log = self._git(
            "log",
            "--format=%s",
            "feature/b-feature",
            "--",
            ".takt/beads/B-root.json",
        ).splitlines()
        self.assertEqual(
            [
                "chore: untrack bead state from feature branch [skip ci]",
                "feature bead snapshot",
                "init",
            ],
            feature_path_log,
        )


    def test_commit_all_is_noop_when_only_untracked_bead_dir_present(self) -> None:
        """commit_all returns None when the only untracked path is .takt/beads/.

        Regression for: worker auto-commit failed when the safety-net exclude left
        .takt/beads/ appearing as untracked in git status --porcelain output, causing
        git commit to fail with 'nothing to commit' rather than short-circuiting.
        """
        worktree = self.wm.ensure_worktree("B-feature", "feature/b-feature")

        # Simulate the safety-net state: .takt/beads/ is untracked (not ignored).
        # No real worker file changes — exactly the tester-bead scenario that regressed.
        (worktree / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"in_progress"}\n',
            encoding="utf-8",
        )

        # Verify the precondition: git status sees the untracked bead dir
        porcelain = self._git("status", "--porcelain", "--untracked-files=all", cwd=worktree)
        self.assertTrue(porcelain.strip(), "precondition: git status should be non-empty")

        result = self.wm.commit_all(worktree, "[takt] B-feature: tester with no changes")

        self.assertIsNone(result, "commit_all should return None when nothing real is staged")

        # No new commit should have been created (HEAD is still the untrack commit).
        # Feature branch has 2 commits: "init" (from main) + "chore: untrack bead state…"
        # created by ensure_worktree. commit_all must not add a 3rd.
        log = self._git("log", "--oneline", cwd=worktree).splitlines()
        self.assertEqual(2, len(log), "no extra commit should be produced for a no-op")


if __name__ == "__main__":
    unittest.main()
