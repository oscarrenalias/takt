"""Regression tests: bead-state merge fallback must never delete bead files."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_takt.gitutils import GitError, WorktreeManager

BEAD = ".takt/beads/B-x.json"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout


class BeadConflictResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        (self.root / ".takt/beads").mkdir(parents=True)
        (self.root / BEAD).write_text("base\n")
        (self.root / "f.txt").write_text("x\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "base")
        _git(self.root, "branch", "feature")
        self.wm = WorktreeManager(self.root, self.root / ".takt/worktrees")

    def _commit(self, msg: str) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", msg)

    def _delete(self, msg: str) -> None:
        _git(self.root, "rm", "-q", BEAD)
        _git(self.root, "commit", "-qm", msg)

    def _modify(self, text: str, msg: str) -> None:
        (self.root / BEAD).write_text(text)
        self._commit(msg)

    def test_final_merge_feature_deletes_main_modifies_keeps_file(self) -> None:
        _git(self.root, "checkout", "-q", "feature")
        self._delete("feature deletes")
        _git(self.root, "checkout", "-q", "main")
        self._modify("main\n", "main modifies")
        self.wm.merge_branch("feature")
        self.assertEqual((self.root / BEAD).read_text(), "main\n")

    def test_final_merge_main_deletes_feature_modifies_keeps_file(self) -> None:
        # DU conflict on main: previously git rm'd the bead file.
        self._delete("main deletes")
        _git(self.root, "checkout", "-q", "feature")
        self._modify("feature\n", "feature modifies")
        _git(self.root, "checkout", "-q", "main")
        self.wm.merge_branch("feature")
        self.assertEqual((self.root / BEAD).read_text(), "feature\n")

    def test_preflight_main_deletes_feature_modifies_keeps_file(self) -> None:
        self._delete("main deletes")
        _git(self.root, "checkout", "-q", "feature")
        self._modify("feature\n", "feature modifies")
        # Preflight: merge main into the feature branch (ours = feature).
        self.wm._merge_with_bead_state_fallback(
            self.root, "feature", "merge", "--no-ff", "main", "-m", "preflight"
        )
        self.assertEqual((self.root / BEAD).read_text(), "feature\n")

    def test_preflight_feature_deletes_main_modifies_keeps_file(self) -> None:
        _git(self.root, "checkout", "-q", "feature")
        self._delete("feature deletes")
        _git(self.root, "checkout", "-q", "main")
        self._modify("main\n", "main modifies")
        _git(self.root, "checkout", "-q", "feature")
        self.wm._merge_with_bead_state_fallback(
            self.root, "feature", "merge", "--no-ff", "main", "-m", "preflight"
        )
        self.assertEqual((self.root / BEAD).read_text(), "main\n")

    def test_both_modified_ours_wins_each_direction(self) -> None:
        _git(self.root, "checkout", "-q", "feature")
        self._modify("feature\n", "feature modifies")
        _git(self.root, "checkout", "-q", "main")
        self._modify("main\n", "main modifies")
        # Final merge into main: main snapshot wins.
        self.wm.merge_branch("feature")
        self.assertEqual((self.root / BEAD).read_text(), "main\n")
        # Preflight into feature: feature snapshot wins.
        _git(self.root, "checkout", "-q", "-B", "m3", "main~1")  # main before its change
        (self.root / BEAD).write_text("main3\n")
        self._commit("m3 modifies")
        _git(self.root, "checkout", "-q", "feature")
        self.wm._merge_with_bead_state_fallback(
            self.root, "feature", "merge", "--no-ff", "m3", "-m", "pre"
        )
        self.assertEqual((self.root / BEAD).read_text(), "feature\n")


if __name__ == "__main__":
    unittest.main()
