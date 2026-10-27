from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import command_merge
from agent_takt.config import CommonConfig, OrchestratorConfig, SchedulerConfig
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import GitError, WorktreeManager
from agent_takt.models import (
    BEAD_DONE,
    BEAD_OPEN,
    BEAD_READY,
)
from agent_takt.prompts import BUILT_IN_AGENT_TYPES
from agent_takt.storage import RepositoryStorage


def _make_config(
    *,
    test_command: str | None = None,
    test_timeout_seconds: int = 120,
    max_corrective_attempts: int = 2,
) -> OrchestratorConfig:
    return OrchestratorConfig(
        common=CommonConfig(
            test_command=test_command,
            test_timeout_seconds=test_timeout_seconds,
        ),
        scheduler=SchedulerConfig(
            max_corrective_attempts=max_corrective_attempts,
        ),
    )


class MergeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("seed\n", encoding="utf-8")
        source_templates = Path(__file__).resolve().parents[1] / "templates" / "agents"
        target_templates = self.root / "templates" / "agents"
        target_templates.mkdir(parents=True, exist_ok=True)
        for template in BUILT_IN_AGENT_TYPES:
            shutil.copy2(source_templates / f"{template}.md", target_templates / f"{template}.md")
        # copy merge-conflict template if present
        mc_template = Path(__file__).resolve().parents[1] / "templates" / "agents" / "merge-conflict.md"
        if mc_template.exists():
            shutil.copy2(mc_template, target_templates / "merge-conflict.md")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "templates/agents"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.root, check=True, capture_output=True)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()
        self._isolate_git_lookups()

    def _isolate_git_lookups(self) -> None:
        """command_merge resolves the approver and branch SHAs via git before the gates run.

        Several tests here use fake branch names and patch ``subprocess.Popen`` globally, so give
        those lookups a fixed approver and a placeholder SHA when the real lookup is unavailable.
        """
        real_rev_parse = WorktreeManager.rev_parse

        def _rev_parse(manager, ref, cwd=None):
            try:
                return real_rev_parse(manager, ref, cwd)
            except Exception:
                return "0" * 40

        for target, replacement in (
            ("agent_takt.cli.commands.merge.git_user_identity", lambda root: ("Test User", "test@example.com")),
            ("agent_takt.cli.commands.merge.WorktreeManager.rev_parse", _rev_parse),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_done_bead_with_branch(self, branch_name: str = "feature/b-test") -> object:
        bead = self.storage.create_bead(
            title="Feature",
            agent_type="developer",
            description="some feature",
            status=BEAD_DONE,
        )
        bead.execution_branch_name = branch_name
        self.storage.save_bead(bead)
        return bead

    # -------------------------------------------------------------------------
    # Happy path
    # -------------------------------------------------------------------------

    def test_happy_path_skipping_both_preflight_and_tests(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-happy")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config()
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as mock_merge,
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=True),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        mock_merge.assert_called_once_with("feature/b-happy")

    # -------------------------------------------------------------------------
    # Existing unresolved merge-conflict bead blocks merge
    # -------------------------------------------------------------------------

    def test_existing_unresolved_merge_conflict_bead_blocks_merge(self) -> None:
        root_bead = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="root",
            status=BEAD_DONE,
        )
        root_bead.execution_branch_name = "feature/b-root"
        self.storage.save_bead(root_bead)

        # create an unresolved merge-conflict child
        self.storage.create_bead(
            title="Resolve conflicts",
            agent_type="developer",
            description="conflicts",
            bead_type="merge-conflict",
            parent_id=root_bead.bead_id,
            feature_root_id=root_bead.bead_id,
            status=BEAD_OPEN,
        )

        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config()
        with patch("agent_takt.cli.commands.merge.load_config", return_value=cfg):
            exit_code = command_merge(
                Namespace(bead_id=root_bead.bead_id, skip_rebase=True, skip_tests=True),
                self.storage,
                console,
            )
        self.assertEqual(1, exit_code)

    def test_done_merge_conflict_bead_does_not_block_merge(self) -> None:
        root_bead = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="root",
            status=BEAD_DONE,
        )
        root_bead.execution_branch_name = "feature/b-root"
        self.storage.save_bead(root_bead)

        # create a resolved (done) merge-conflict child
        conflict_bead = self.storage.create_bead(
            title="Resolve conflicts",
            agent_type="developer",
            description="conflicts",
            bead_type="merge-conflict",
            parent_id=root_bead.bead_id,
            feature_root_id=root_bead.bead_id,
        )
        conflict_bead.status = BEAD_DONE
        self.storage.save_bead(conflict_bead)

        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config()
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as mock_merge,
        ):
            exit_code = command_merge(
                Namespace(bead_id=root_bead.bead_id, skip_rebase=True, skip_tests=True),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        mock_merge.assert_called_once()

    # -------------------------------------------------------------------------
    # Preflight: merge main into feature branch
    # -------------------------------------------------------------------------

    def test_preflight_skipped_when_skip_rebase_is_true(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-norebase")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config()
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch") as mock_preflight,
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=True),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        mock_preflight.assert_not_called()

    def test_preflight_conflict_creates_merge_conflict_bead_and_returns_1(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-conflict")
        # give the bead a worktree path that "exists"
        worktree_path = self.root / ".takt" / "worktrees" / "fake"
        worktree_path.mkdir(parents=True, exist_ok=True)
        bead.execution_worktree_path = str(worktree_path)
        self.storage.save_bead(bead)

        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config()
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch(
                "agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch",
                side_effect=GitError("conflict"),
            ),
            patch("agent_takt.cli.commands.merge.WorktreeManager.conflicted_files", return_value=["a.py"]),
            patch("agent_takt.cli.commands.merge._get_diff_context", return_value=""),
            patch("agent_takt.cli.commands.merge.WorktreeManager.abort_merge"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=False, skip_tests=True),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        conflict_beads = [
            b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"
        ]
        self.assertEqual(1, len(conflict_beads))
        self.assertIn("a.py", conflict_beads[0].expected_files)

    # -------------------------------------------------------------------------
    # Test gate
    # -------------------------------------------------------------------------

    def test_test_gate_skipped_when_skip_tests_is_true(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-notests")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(test_command="uv run python -m unittest")
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.subprocess.run") as mock_proc,
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=True),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        # subprocess.run should not have been called for the test command
        # (merge_branch calls WorktreeManager, not subprocess.run directly)
        for call in mock_proc.call_args_list:
            if isinstance(call.args[0], str):
                self.assertNotIn("unittest", call.args[0])

    def test_test_gate_missing_test_command_warns_and_skips(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-noconfig")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(test_command=None)
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn("No test_command configured", output)

    def test_test_gate_failure_creates_merge_conflict_bead_and_returns_1(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-testfail")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(test_command="false")  # always fails
        failing_proc = MagicMock()
        failing_proc.returncode = 1
        failing_proc.stdout = iter(["FAILED\n"])
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.subprocess.Popen", return_value=failing_proc),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        conflict_beads = [
            b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"
        ]
        self.assertEqual(1, len(conflict_beads))

    def test_test_gate_timeout_creates_merge_conflict_bead_and_returns_1(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-timeout")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(test_command="sleep 999", test_timeout_seconds=1)
        timeout_proc = MagicMock()
        timeout_proc.stdout = iter([])
        timeout_proc.wait.side_effect = [subprocess.TimeoutExpired("sleep", 1), None]
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.subprocess.Popen", return_value=timeout_proc),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        conflict_beads = [
            b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"
        ]
        self.assertEqual(1, len(conflict_beads))
        self.assertIn("timed out", conflict_beads[0].description.lower())

    def test_test_gate_uses_configured_timeout(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-timeoutval")
        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(test_command="echo ok", test_timeout_seconds=42)
        ok_proc = MagicMock()
        ok_proc.returncode = 0
        ok_proc.stdout = iter([])
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.subprocess.Popen", return_value=ok_proc),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        # verify the timeout was passed to wait()
        ok_proc.wait.assert_called_once_with(timeout=42)

    def test_test_gate_streams_output_to_console(self) -> None:
        bead = self._make_done_bead_with_branch("feature/b-stream")
        stream = io.StringIO()
        console = ConsoleReporter(stream=stream)
        cfg = _make_config(test_command="echo hello", test_timeout_seconds=30)
        ok_proc = MagicMock()
        ok_proc.returncode = 0
        ok_proc.stdout = iter(["line one\n", "line two\n"])
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch("agent_takt.cli.commands.merge.subprocess.Popen", return_value=ok_proc),
            patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False),
                self.storage,
                console,
            )
        self.assertEqual(0, exit_code)
        output = stream.getvalue()
        self.assertIn("line one", output)
        self.assertIn("line two", output)

    # -------------------------------------------------------------------------
    # Attempt cap escalation
    # -------------------------------------------------------------------------

    def test_attempt_cap_prevents_new_merge_conflict_bead_when_exceeded(self) -> None:
        root_bead = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="root",
            status=BEAD_DONE,
        )
        root_bead.execution_branch_name = "feature/b-cap"
        worktree_path = self.root / ".takt" / "worktrees" / "cap"
        worktree_path.mkdir(parents=True, exist_ok=True)
        root_bead.execution_worktree_path = str(worktree_path)
        self.storage.save_bead(root_bead)

        # create existing merge-conflict beads that saturate the cap (2)
        for i in range(2):
            self.storage.create_bead(
                title=f"Resolve conflict {i}",
                agent_type="developer",
                description="conflict",
                bead_type="merge-conflict",
                parent_id=root_bead.bead_id,
                feature_root_id=root_bead.bead_id,
            )

        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(max_corrective_attempts=2)
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch(
                "agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch",
                side_effect=GitError("conflict"),
            ),
            patch("agent_takt.cli.commands.merge.WorktreeManager.conflicted_files", return_value=[]),
            patch("agent_takt.cli.commands.merge._get_diff_context", return_value=""),
            patch("agent_takt.cli.commands.merge.WorktreeManager.abort_merge"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=root_bead.bead_id, skip_rebase=False, skip_tests=True),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        # no new merge-conflict bead should have been created beyond the 2 already there
        conflict_beads = [
            b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"
        ]
        self.assertEqual(2, len(conflict_beads))

    def test_attempt_cap_allows_new_bead_when_below_cap(self) -> None:
        root_bead = self.storage.create_bead(
            title="Feature root",
            agent_type="developer",
            description="root",
            status=BEAD_DONE,
        )
        root_bead.execution_branch_name = "feature/b-undercap"
        worktree_path = self.root / ".takt" / "worktrees" / "undercap"
        worktree_path.mkdir(parents=True, exist_ok=True)
        root_bead.execution_worktree_path = str(worktree_path)
        self.storage.save_bead(root_bead)

        # 1 resolved (done) conflict bead; cap is 2 so a new one should be allowed.
        # Using BEAD_DONE ensures the existing-conflict preflight check is not triggered.
        resolved_conflict = self.storage.create_bead(
            title="Resolve conflict",
            agent_type="developer",
            description="conflict",
            bead_type="merge-conflict",
            parent_id=root_bead.bead_id,
            feature_root_id=root_bead.bead_id,
        )
        resolved_conflict.status = BEAD_DONE
        self.storage.save_bead(resolved_conflict)

        console = ConsoleReporter(stream=io.StringIO())
        cfg = _make_config(max_corrective_attempts=2)
        with (
            patch("agent_takt.cli.commands.merge.load_config", return_value=cfg),
            patch(
                "agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch",
                side_effect=GitError("conflict"),
            ),
            patch("agent_takt.cli.commands.merge.WorktreeManager.conflicted_files", return_value=[]),
            patch("agent_takt.cli.commands.merge._get_diff_context", return_value=""),
            patch("agent_takt.cli.commands.merge.WorktreeManager.abort_merge"),
        ):
            exit_code = command_merge(
                Namespace(bead_id=root_bead.bead_id, skip_rebase=False, skip_tests=True),
                self.storage,
                console,
            )

        self.assertEqual(1, exit_code)
        conflict_beads = [
            b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"
        ]
        self.assertEqual(2, len(conflict_beads))

    # -------------------------------------------------------------------------
    # TUI no longer performs merges inline
    # -------------------------------------------------------------------------

    def test_tui_request_merge_does_not_set_awaiting_merge_confirmation(self) -> None:
        from agent_takt.tui import TuiRuntimeState, FILTER_ALL

        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIsNone(state.pending_merge_bead_id)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

    def test_tui_request_merge_shows_cli_redirect_for_non_done_bead(self) -> None:
        from agent_takt.tui import TuiRuntimeState, FILTER_ALL

        bead = self.storage.create_bead(
            bead_id="B0001",
            title="Ready",
            agent_type="developer",
            description="ready",
            status=BEAD_READY,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)
        state.request_merge()

        self.assertFalse(state.awaiting_merge_confirmation)
        self.assertIn(f"takt merge {bead.bead_id}", state.status_message)

    def test_tui_confirm_merge_is_noop_without_pending_state(self) -> None:
        from agent_takt.tui import TuiRuntimeState, FILTER_ALL

        self.storage.create_bead(
            bead_id="B0001",
            title="Done",
            agent_type="developer",
            description="done",
            status=BEAD_DONE,
        )
        state = TuiRuntimeState(self.storage, filter_mode=FILTER_ALL)

        # request_merge no longer sets pending confirmation
        state.request_merge()
        merged = state.confirm_merge()

        self.assertFalse(merged)
        self.assertEqual("No merge pending confirmation.", state.status_message)


class MergeBranchResolveStrategyTests(unittest.TestCase):
    """Verify WorktreeManager merge helpers pass the expected git arguments."""

    def test_merge_branch_uses_resolve_strategy(self) -> None:
        """merge_branch must invoke the final merge with -s resolve."""
        root = Path(tempfile.mkdtemp())
        wm = WorktreeManager(root, root / ".takt" / "worktrees")
        with (
            patch.object(wm, "ensure_repository"),
            patch.object(wm, "_merge_with_bead_state_fallback") as mock_merge,
        ):
            wm.merge_branch("feature/b-test")

        mock_merge.assert_called_once()
        args = mock_merge.call_args[0]
        self.assertEqual(wm.root, args[0])
        self.assertIn("merge", args)
        self.assertIn("--no-ff", args)
        self.assertIn("-s", args)
        resolve_idx = list(args).index("-s") + 1
        self.assertEqual(args[resolve_idx], "resolve")
        # branch name must appear after the strategy flag
        branch_idx = list(args).index("feature/b-test")
        self.assertGreater(branch_idx, resolve_idx)

    def test_preflight_merge_uses_same_merge_driver_wrapper(self) -> None:
        """merge_main_into_branch must route through the bead-state fallback wrapper."""
        root = Path(tempfile.mkdtemp())
        wm = WorktreeManager(root, root / ".takt" / "worktrees")
        worktree = root / ".takt" / "worktrees" / "B-test"
        with patch.object(wm, "_merge_with_bead_state_fallback") as mock_merge, \
             patch.object(wm, "_save_and_remove_bead_files", return_value=[]), \
             patch.object(wm, "_restore_saved_bead_files"), \
             patch.object(wm, "_protect_worktree_bead_state"):
            wm.merge_main_into_branch(worktree)

        mock_merge.assert_called_once_with(
            worktree,
            "feature",
            "merge",
            "--no-ff",
            "main",
            "-m",
            "Merge main into feature branch",
        )


class BeadStateMergeFallbackIntegrationTests(unittest.TestCase):
    """Integration coverage for leaked bead-state merges on both merge paths."""

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
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
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

    def _tracked_feature_worktree(self) -> Path:
        self._git("checkout", "-b", "feature/b-feature")
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"feature-branch"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "feature bead snapshot")
        self._git("checkout", "main")
        return self.root

    def test_preflight_merge_keeps_feature_branch_bead_state_when_both_branches_track_it(self) -> None:
        worktree = self._tracked_feature_worktree()
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"main-updated"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead update")
        self._git("checkout", "feature/b-feature")

        self.wm.merge_main_into_branch(worktree)

        self.assertEqual(
            '{"status":"feature-branch"}\n',
            (worktree / ".takt" / "beads" / "B-root.json").read_text(encoding="utf-8"),
        )

    def test_final_merge_keeps_main_branch_bead_state_when_both_branches_track_it(self) -> None:
        self._tracked_feature_worktree()
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"main-wins"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead update")

        self.wm.merge_branch("feature/b-feature")

        self.assertEqual(
            '{"status":"main-wins"}\n',
            (self.root / ".takt" / "beads" / "B-root.json").read_text(encoding="utf-8"),
        )

    def test_preflight_merge_does_not_create_chore_commit_on_feature_branch(self) -> None:
        """merge_main_into_branch must NOT produce a fresh
        `chore: untrack bead state` commit on the feature branch after the
        merge. Such a commit lives on the feature branch's tip; when the
        final feature→main merge runs it propagates `git rm --cached`
        deletes to main, wiping main's bead state.

        Regression reproducer for the bug introduced by commit 93933349
        (post-merge call to _protect_worktree_bead_state) and reverted in
        the same revision that adds this test.
        """
        # Set up a feature branch that already tracks the bead file.
        self._tracked_feature_worktree()
        # Update main so the preflight has actual work to do.
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"main-update"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead update")
        self._git("checkout", "feature/b-feature")

        self.wm.merge_main_into_branch(self.root)

        # Critical assertion: the latest commit on the feature branch must
        # be the merge commit, NOT a fresh `chore: untrack bead state`
        # commit. With the destructive post-merge protect at line 414,
        # the latest subject would be "chore: untrack bead state ...".
        log_proc = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, log_proc.returncode)
        self.assertNotIn(
            "untrack bead state",
            log_proc.stdout,
            "merge_main_into_branch left a destructive chore commit on "
            "the feature branch tip; the post-merge "
            "_protect_worktree_bead_state regression has reappeared.",
        )

    def test_du_conflict_in_bead_state_resolves_via_rm(self) -> None:
        """DU conflicts (deleted in HEAD, modified by other) on .takt/beads/
        files must be resolved via ``git rm`` — there is no "our" version to
        check out, since the safety net policy is to not track bead state on
        feature branches.
        """
        # Feature branch deletes the tracked bead file (simulates the safety
        # net's `git rm --cached` + commit at worktree creation).
        self._git("checkout", "-b", "feature/b-feature")
        self._git("rm", ".takt/beads/B-root.json")
        self._git("commit", "-m", "untrack bead state")
        self._git("checkout", "main")

        # Main modifies the same file.
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"main-updated"}\n',
            encoding="utf-8",
        )
        self._git("add", ".takt/beads/B-root.json")
        self._git("commit", "-m", "main bead update")

        # Switch to feature branch and merge main in. Without the DU fix this
        # raises GitError because `git checkout --ours` cannot operate on a
        # path with no "our" version. With the fix, the merge resolves
        # cleanly via `git rm`.
        self._git("checkout", "feature/b-feature")
        self.wm.merge_main_into_branch(self.root)

        # The file remains untracked on the feature branch (per safety-net
        # policy). `git ls-files` should not include it in the index.
        ls_proc = subprocess.run(
            ["git", "ls-files", "--", ".takt/beads/B-root.json"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, ls_proc.returncode)
        self.assertEqual("", ls_proc.stdout.strip())

    def test_mixed_conflicts_do_not_stage_partial_bead_resolution(self) -> None:
        self._tracked_feature_worktree()
        (self.root / ".takt" / "beads" / "B-root.json").write_text(
            '{"status":"main-mixed"}\n',
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("main change\n", encoding="utf-8")
        self._git("add", ".takt/beads/B-root.json", "README.md")
        self._git("commit", "-m", "main mixed update")

        feature_worktree = self.root
        self._git("checkout", "feature/b-feature", cwd=feature_worktree)
        (feature_worktree / "README.md").write_text("feature change\n", encoding="utf-8")
        self._git("add", "README.md", cwd=feature_worktree)
        self._git("commit", "-m", "feature mixed update", cwd=feature_worktree)

        with self.assertRaises(GitError):
            self.wm.merge_main_into_branch(feature_worktree)

        self.assertEqual(
            sorted([".takt/beads/B-root.json", "README.md"]),
            sorted(self.wm.conflicted_files(feature_worktree)),
        )
        status_proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=feature_worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, status_proc.returncode)
        self.assertIn("UU .takt/beads/B-root.json", status_proc.stdout)
        self.assertIn("UU README.md", status_proc.stdout)


class EndToEndMergeSafetyCycleTests(unittest.TestCase):
    """End-to-end: bead state survives the full create-worktree → feature-commit → main-diverge → preflight-sync → merge cycle.

    This test would have failed on pre-fix code where:
    - _protect_worktree_bead_state left a destructive chore commit whose git rm --cached
      deletes propagated to main during the final feature→main merge, wiping bead state, OR
    - merge_main_into_branch failed with "would be overwritten by merge" when main added new
      bead files that the feature worktree had locally as untracked.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.worktrees_dir = self.root / ".takt" / "worktrees"
        self.wm = WorktreeManager(self.root, self.worktrees_dir)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        self._git("config", "commit.gpgsign", "false")
        (self.root / ".gitattributes").write_text(".takt/beads/** merge=ours\n", encoding="utf-8")
        bead_dir = self.root / ".takt" / "beads"
        bead_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 6):
            (bead_dir / f"B-{i}.json").write_text(
                f'{{"id":"B-{i}","status":"ready"}}\n', encoding="utf-8"
            )
        (self.root / "src").mkdir()
        (self.root / "src" / "feature.py").write_text("# base\n", encoding="utf-8")
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

    def test_takt_merge_preserves_main_bead_state_through_full_cycle(self) -> None:
        """Full cycle: worktree created, feature committed, main diverges, preflight sync, final merge.

        Asserts that after merge_branch all 6 bead files exist on main, B-2 carries
        main's modification, and B-6 (added on main after branching) survived.
        """
        # Step 1: create feature worktree (triggers _protect_worktree_bead_state)
        worktree = self.wm.ensure_worktree("B-feature", "feature/b-feature")
        self.assertTrue(worktree.exists(), "worktree directory should be created")

        # Step 2: write a non-bead file change in the feature worktree and commit
        (worktree / "src" / "feature.py").write_text("# feature implementation\n", encoding="utf-8")
        commit_hash = self.wm.commit_all(worktree, "[takt] B-feature: implement feature")
        self.assertIsNotNone(commit_hash, "commit_all should produce a commit for the feature change")

        # Step 3: on main, modify B-2.json and add B-6.json in a new commit
        bead_dir = self.root / ".takt" / "beads"
        b2_modified = '{"id":"B-2","status":"done","modified_on_main":true}\n'
        (bead_dir / "B-2.json").write_text(b2_modified, encoding="utf-8")
        b6_content = '{"id":"B-6","status":"ready"}\n'
        (bead_dir / "B-6.json").write_text(b6_content, encoding="utf-8")
        self._git("add", ".takt/beads/B-2.json", ".takt/beads/B-6.json")
        self._git("commit", "-m", "main: update B-2 and add B-6")

        # Step 4: preflight sync — merge main into feature branch
        self.wm.merge_main_into_branch(worktree)

        # Step 5: final merge — merge feature branch into main
        self.wm.merge_branch("feature/b-feature")

        # All 6 bead files must be present on main
        for i in range(1, 7):
            bead_file = bead_dir / f"B-{i}.json"
            self.assertTrue(
                bead_file.exists(),
                f"B-{i}.json missing on main after full merge cycle",
            )

        # B-2 must carry main's modification, not the original or deleted content
        self.assertEqual(
            b2_modified,
            (bead_dir / "B-2.json").read_text(encoding="utf-8"),
            "B-2.json should have main's modified content after merge",
        )

        # B-6 (added on main after branching) must have survived
        self.assertEqual(
            b6_content,
            (bead_dir / "B-6.json").read_text(encoding="utf-8"),
            "B-6.json added on main after branching should survive the merge",
        )

        # Exactly 6 bead files — no files deleted and none extra
        actual = sorted(f.name for f in bead_dir.glob("*.json"))
        self.assertEqual(
            [f"B-{i}.json" for i in range(1, 7)],
            actual,
            f"Expected exactly B-1 through B-6 on main, got: {actual}",
        )


if __name__ == "__main__":
    unittest.main()
