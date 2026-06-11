"""Regression tests for WorktreeManager.commit_all() gitignore configurations.

Covers the two supported configurations for .takt/beads/ and the empty-changes case:
- Quiet-mode config: .takt/beads/ in .gitignore — commit_all must stage source files
  but never bead-state files, even when ignored paths exist in the working tree.
- Default scaffold: .takt/beads/ NOT in .gitignore — commit_all filters out untracked
  bead-state files via the _BEAD_STATE_PREFIX check and commits only real changes.
- Empty-changes case: only .takt/beads/ writes (or nothing) — commit_all returns None
  and no git commit is created.

All tests use a real on-disk git repo; no subprocess calls are mocked.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager


class CommitAllGitignoreConfigTests(unittest.TestCase):
    """Regression tests for commit_all() under the two supported .takt/beads/ configurations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.wm = WorktreeManager(self.root, self.root / ".takt" / "worktrees")

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

    def _init_repo(self, gitignore_content: str | None = None) -> None:
        """Initialise a minimal git repo with an optional .gitignore."""
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        if gitignore_content is not None:
            (self.root / ".gitignore").write_text(gitignore_content, encoding="utf-8")
        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("# initial\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "init")

    def _commit_tree_names(self) -> list[str]:
        """Return the non-empty file names changed in the most recent commit."""
        raw = self._git("show", "--name-only", "--pretty=format:", "HEAD")
        return [line for line in raw.splitlines() if line.strip()]

    def test_quiet_mode_gitignore_source_committed_no_bead_state_in_tree(self) -> None:
        """Quiet-mode config: .takt/beads/ in .gitignore.

        commit_all() must return a non-None commit hash and the resulting commit must
        contain the source file but no .takt/beads/ entries.  Bead files that are
        invisible to git (ignored) must not cause the git-add step to fail.
        """
        self._init_repo(gitignore_content=".takt/beads/\n")

        # Modify a source file — this is the real worker change.
        (self.root / "src" / "app.py").write_text("# changed\n", encoding="utf-8")

        # Write a bead-state file; git ignores it because of .gitignore.
        bead_dir = self.root / ".takt" / "beads"
        bead_dir.mkdir(parents=True, exist_ok=True)
        (bead_dir / "B-test.json").write_text('{"status":"in_progress"}\n', encoding="utf-8")

        result = self.wm.commit_all(self.root, "[takt] B-test: quiet mode")

        self.assertIsNotNone(result, "commit_all must return a commit hash")
        names = self._commit_tree_names()
        self.assertIn("src/app.py", names, "source file must appear in commit")
        bead_names = [n for n in names if n.startswith(".takt/beads/")]
        self.assertEqual([], bead_names, f"bead-state files must not appear in commit: {bead_names}")

    def test_default_scaffold_no_gitignore_source_committed_bead_state_absent(self) -> None:
        """Default scaffold: .takt/beads/ NOT in .gitignore.

        commit_all() must return a non-None commit hash.  The source file must appear
        in the commit; untracked bead-state files must be absent because the
        _BEAD_STATE_PREFIX filter strips them before git add.
        """
        self._init_repo()  # no .gitignore — bead files will be untracked

        # Modify a source file.
        (self.root / "src" / "app.py").write_text("# changed\n", encoding="utf-8")

        # Write a bead-state file; git status will report it as untracked.
        bead_dir = self.root / ".takt" / "beads"
        bead_dir.mkdir(parents=True, exist_ok=True)
        (bead_dir / "B-test.json").write_text('{"status":"in_progress"}\n', encoding="utf-8")

        result = self.wm.commit_all(self.root, "[takt] B-test: default scaffold")

        self.assertIsNotNone(result, "commit_all must return a commit hash")
        names = self._commit_tree_names()
        self.assertIn("src/app.py", names, "source file must appear in commit")
        bead_names = [n for n in names if n.startswith(".takt/beads/")]
        self.assertEqual([], bead_names, f"bead-state files must not appear in commit: {bead_names}")

    def test_rename_destination_path_is_staged_not_origin(self) -> None:
        """Rename entries: commit_all must stage the destination (new) path, not the origin.

        git status --porcelain=v1 -z emits "R  dest NUL origin NUL" for renames.
        token[3:] is the destination; the origin follows as the next NUL token.
        The old code overwrote destination with origin — this test guards against that.
        """
        self._init_repo()

        # Create a file to rename and commit it.
        (self.root / "src" / "old_name.py").write_text("# original\n", encoding="utf-8")
        self._git("add", "src/old_name.py")
        self._git("commit", "-m", "add file to rename")

        # Perform the rename in the working tree and stage it so git sees it as R.
        (self.root / "src" / "old_name.py").rename(self.root / "src" / "new_name.py")
        self._git("add", "-A", "src/")

        result = self.wm.commit_all(self.root, "[takt] rename test")

        self.assertIsNotNone(result, "commit_all must return a commit hash")
        names = self._commit_tree_names()
        self.assertIn("src/new_name.py", names, "destination path must appear in commit")
        self.assertNotIn("src/old_name.py", names, "origin path must not appear as an addition")

    def test_empty_changes_only_bead_writes_returns_none_no_commit_created(self) -> None:
        """Empty-changes case: only .takt/beads/ writes present.

        commit_all() must return None and must not create a new commit.  The git log
        must still contain exactly one entry (the init commit).
        """
        self._init_repo()

        # Write only bead-state files — no real worker changes.
        bead_dir = self.root / ".takt" / "beads"
        bead_dir.mkdir(parents=True, exist_ok=True)
        (bead_dir / "B-test.json").write_text('{"status":"in_progress"}\n', encoding="utf-8")

        result = self.wm.commit_all(self.root, "[takt] B-test: empty changes")

        self.assertIsNone(result, "commit_all must return None when there is nothing real to stage")
        log = self._git("log", "--oneline").splitlines()
        self.assertEqual(1, len(log), f"no extra commit should be produced; log: {log}")


if __name__ == "__main__":
    unittest.main()
