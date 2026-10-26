"""Tests for command_merge in cli/commands/merge.py.

Covers:
- command_merge uses feature root branch for descendant beads
- command_merge resolves bead ID prefix
- subprocess.Popen invoked with list arg and no shell=True
- shlex.split correctly tokenises standard and quoted test commands
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
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
from agent_takt.console import ConsoleReporter
from agent_takt.gitutils import GitError
from agent_takt.models import BEAD_DONE
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class TestCliMerge(_OrchestratorBase):

    def _create_branch(self, name: str) -> None:
        """command_merge records the feature head SHA, so the branch must really exist."""
        subprocess.run(["git", "branch", name], cwd=self.root, check=True, capture_output=True)

    def test_merge_uses_feature_root_branch_for_descendants(self) -> None:
        epic = self.storage.create_bead(title="Epic", agent_type="planner", description="root", status=BEAD_DONE, bead_type="epic")
        root = self.storage.create_bead(title="Feature root", agent_type="developer", description="feature", parent_id=epic.bead_id)
        child = self.storage.create_bead(
            title="Child task",
            agent_type="developer",
            description="subtask",
            parent_id=root.bead_id,
            dependencies=[root.bead_id],
        )
        root.execution_branch_name = "feature/b0001"
        root.branch_name = "feature/b0001"
        self.storage.save_bead(root)
        self._create_branch("feature/b0001")
        console = ConsoleReporter(stream=io.StringIO())
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as merge_branch:
            exit_code = command_merge(Namespace(bead_id=child.bead_id, skip_rebase=True, skip_tests=True), self.storage, console)
        self.assertEqual(0, exit_code)
        merge_branch.assert_called_once_with("feature/b0001")

    def test_cli_merge_resolves_prefix(self) -> None:
        bead = self.storage.create_bead(title="Merge me", agent_type="developer", description="work")
        bead.execution_branch_name = "feature/b-test"
        self.storage.save_bead(bead)
        self._create_branch("feature/b-test")
        prefix = bead.bead_id[:4]
        console = ConsoleReporter(stream=io.StringIO())
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as merge_branch:
            exit_code = command_merge(Namespace(bead_id=prefix, skip_rebase=True, skip_tests=True), self.storage, console)
        self.assertEqual(0, exit_code)
        merge_branch.assert_called_once_with("feature/b-test")

    def test_popen_invoked_with_list_no_shell(self) -> None:
        """subprocess.Popen must receive a list, never shell=True."""
        bead = self.storage.create_bead(title="Gate test", agent_type="developer", description="work")
        bead.execution_branch_name = "feature/b-gate"
        self.storage.save_bead(bead)
        self._create_branch("feature/b-gate")

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.returncode = 0

        mock_config = MagicMock()
        mock_config.common.test_command = "uv run pytest tests/ -n auto -q"
        mock_config.common.test_timeout_seconds = 120
        mock_config.scheduler.max_corrective_attempts = 5

        console = ConsoleReporter(stream=io.StringIO())
        with patch("agent_takt.cli.commands.merge.load_config", return_value=mock_config):
            with patch("agent_takt.cli.commands.merge.subprocess.Popen", return_value=mock_proc) as mock_popen:
                with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch"), \
                        patch("agent_takt.cli.commands.merge.WorktreeManager.rev_parse", return_value="a" * 40):
                    exit_code = command_merge(
                        Namespace(bead_id=bead.bead_id, skip_rebase=True, skip_tests=False, approver="tester"),
                        self.storage,
                        console,
                    )

        self.assertEqual(0, exit_code)
        mock_popen.assert_called_once()
        call_args, call_kwargs = mock_popen.call_args
        # First positional argument must be a list, not a string
        self.assertIsInstance(call_args[0], list)
        # shell=True must not appear
        self.assertNotIn("shell", call_kwargs)

    def test_standard_test_command_splits_to_expected_list(self) -> None:
        """'uv run pytest tests/ -n auto -q' must tokenise to the canonical list."""
        result = shlex.split("uv run pytest tests/ -n auto -q")
        self.assertEqual(["uv", "run", "pytest", "tests/", "-n", "auto", "-q"], result)

    def test_quoted_argument_splits_correctly(self) -> None:
        """shlex.split must strip quotes and keep the quoted string as one token."""
        result = shlex.split('uv run pytest -k "test_foo"')
        self.assertEqual(["uv", "run", "pytest", "-k", "test_foo"], result)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


class _MergeRecordBase(_OrchestratorBase):
    """Real-git fixtures for the approval record / audit snapshot behaviour of command_merge."""

    BRANCH = "feature/b-audit"

    def setUp(self) -> None:
        super().setUp()
        self.stream = io.StringIO()
        self.console = ConsoleReporter(stream=self.stream)
        self.main_branch = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        self.feature_commit("one")
        self.bead = self.storage.create_bead(title="Audited feature", agent_type="developer", description="work")
        self.bead.execution_branch_name = self.BRANCH
        self.storage.save_bead(self.bead)

    def feature_commit(self, name: str) -> None:
        if subprocess.run(["git", "rev-parse", "--verify", "--quiet", self.BRANCH], cwd=self.root).returncode != 0:
            _git(self.root, "branch", self.BRANCH)
        _git(self.root, "checkout", "-q", self.BRANCH)
        (self.root / f"{name}.txt").write_text(name, encoding="utf-8")
        _git(self.root, "add", f"{name}.txt")
        _git(self.root, "commit", "-q", "-m", name)
        _git(self.root, "checkout", "-q", self.main_branch)

    def ns(self, **overrides) -> Namespace:
        values = dict(bead_id=self.bead.bead_id, skip_rebase=True, skip_tests=True, approver=None)
        values.update(overrides)
        return Namespace(**values)

    def merge(self, **overrides) -> int:
        return command_merge(self.ns(**overrides), self.storage, self.console)

    def events(self, event_type: str | None = None) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        return [r for r in rows if event_type is None or r["event_type"] == event_type]

    def records(self) -> list[dict]:
        return self.storage.load_bead(self.bead.bead_id).metadata.get("merge_records") or []

    def audits_dir(self) -> Path:
        return self.root / ".takt" / "audits" / self.bead.bead_id

    def mock_config(self, test_command: str, timeout: float = 30) -> MagicMock:
        config = MagicMock()
        config.common.test_command = test_command
        config.common.test_timeout_seconds = timeout
        config.scheduler.max_corrective_attempts = 5
        return config

    def conflict_beads(self) -> list:
        return [b for b in self.storage.list_beads() if b.bead_type == "merge-conflict"]


class TestMergeApprover(_MergeRecordBase):
    def test_no_approver_and_no_git_user_exits_before_any_gate(self) -> None:
        _git(self.root, "config", "--unset", "user.name")
        _git(self.root, "config", "--unset", "user.email")
        env = {"HOME": self.temp_dir.name, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        with patch.dict(os.environ, env), \
                patch("agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch") as preflight, \
                patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as merge_branch:
            code = self.merge(skip_rebase=False, skip_tests=False)
        self.assertEqual(1, code)
        self.assertIn("error: cannot determine approver; set git user.name or pass --approver", self.stream.getvalue())
        preflight.assert_not_called()
        self.assertNotIn("Running test gate", self.stream.getvalue())
        merge_branch.assert_not_called()
        self.assertFalse((self.root / ".takt" / "audits").exists())
        self.assertEqual([], self.records())

    def test_approver_flag_wins_over_git_config(self) -> None:
        self.assertEqual(0, self.merge(approver="Alice Example"))
        (record,) = self.records()
        self.assertEqual("Alice Example", record["approver"])
        self.assertEqual("flag", record["approver_source"])

    def test_blank_approver_flag_falls_back_to_git_config(self) -> None:
        self.assertEqual(0, self.merge(approver="   "))
        (record,) = self.records()
        self.assertEqual("git-config", record["approver_source"])

    def test_git_config_approver_includes_email(self) -> None:
        self.assertEqual(0, self.merge())
        (record,) = self.records()
        self.assertEqual("Test User <test@example.com>", record["approver"])
        self.assertEqual("git-config", record["approver_source"])

    def test_git_config_approver_name_only_when_email_unset(self) -> None:
        _git(self.root, "config", "--unset", "user.email")
        env = {
            "HOME": self.temp_dir.name, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "t@example.com",
        }
        with patch.dict(os.environ, env):
            self.assertEqual(0, self.merge())
        self.assertEqual("Test User", self.records()[0]["approver"])


class _SuccessChecks:
    """Mixin: success-path assertions run once per commit_bead_state setting."""

    def _storage_for(self, commit_bead_state: bool) -> None:
        self.storage = RepositoryStorage(self.root, commit_bead_state=commit_bead_state)
        self.storage.initialize()

    def check_success(self) -> None:
        main_before = _git(self.root, "rev-parse", self.main_branch)
        feature_head = _git(self.root, "rev-parse", self.BRANCH)
        self.assertEqual(0, self.merge())
        main_after = self.records()[0]["main_sha_after"]
        self.assertNotEqual(main_before, main_after)
        # main_sha_after is the --no-ff merge commit: parents are the old main and the feature head
        self.assertEqual(f"{main_after} {main_before} {feature_head}", _git(self.root, "rev-list", "--parents", "-n", "1", main_after))

        (record,) = self.records()
        self.assertEqual(main_before, record["main_sha_before"])
        self.assertEqual(feature_head, record["feature_head_sha"])
        self.assertEqual(self.BRANCH, record["branch"])
        self.assertEqual("skipped", record["preflight"])
        self.assertEqual({"status": "skipped", "command": None, "duration_seconds": None}, record["test_gate"])
        self.assertTrue(record["merged_at"])

        audit_path = Path(record["audit_path"])
        self.assertFalse(audit_path.is_absolute())
        self.assertEqual(f".takt/audits/{self.bead.bead_id}", audit_path.parent.as_posix())
        json_file = self.root / audit_path
        md_file = json_file.with_suffix(".md")
        self.assertTrue(json_file.exists())
        self.assertTrue(md_file.exists())
        self.assertEqual(json_file.stem, md_file.stem)
        self.assertEqual(hashlib.sha256(json_file.read_bytes()).hexdigest(), record["audit_sha256"])
        snapshot = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertEqual(1, snapshot["audit_schema_version"])
        self.assertIn("## Merges", md_file.read_text(encoding="utf-8"))

        history = [r.event for r in self.storage.load_bead(self.bead.bead_id).execution_history]
        self.assertIn("merged", history)
        (event,) = self.events("feature_merged")
        self.assertEqual(self.bead.bead_id, event["payload"]["feature_root_id"])
        for key in ("approver", "approver_source", "audit_path", "audit_sha256", "main_sha_after"):
            self.assertEqual(record[key], event["payload"][key])
        self.assertEqual([], self.events("merge_gate_failed"))

        bead_commits = _git(self.root, "log", "--format=%s", "--grep", r"^\[bead\]")
        return bead_commits


class TestMergeSuccessCommitBeadStateTrue(_SuccessChecks, _MergeRecordBase):
    def test_merge_success_with_commit_bead_state_true(self) -> None:
        self._storage_for(True)
        with patch.object(RepositoryStorage, "_auto_commit", True):
            self.storage.save_bead(self.bead)
            commits = self.check_success()
        self.assertTrue(commits, "expected [bead] commits when commit_bead_state is true")


class TestMergeSuccessCommitBeadStateFalse(_SuccessChecks, _MergeRecordBase):
    def test_merge_success_with_commit_bead_state_false(self) -> None:
        self._storage_for(False)
        with patch.object(RepositoryStorage, "_auto_commit", True):
            self.storage.save_bead(self.bead)
            commits = self.check_success()
        self.assertEqual("", commits, "no [bead] commits expected when commit_bead_state is false")


class TestMergeGates(_MergeRecordBase):
    def run_with_config(self, config: MagicMock, **overrides) -> int:
        with patch("agent_takt.cli.commands.merge.load_config", return_value=config):
            return self.merge(**overrides)

    def assert_no_snapshot_or_record(self) -> None:
        self.assertFalse((self.root / ".takt" / "audits").exists())
        self.assertEqual([], self.records())
        self.assertEqual([], self.events("feature_merged"))

    def assert_gate_failed(self, gate: str, reason_prefix: str) -> dict:
        (event,) = self.events("merge_gate_failed")
        payload = event["payload"]
        self.assertEqual(gate, payload["gate"])
        self.assertTrue(payload["reason"].startswith(reason_prefix), payload["reason"])
        self.assertEqual("Test User <test@example.com>", payload["approver"])
        self.assertEqual(self.bead.bead_id, payload["feature_root_id"])
        self.assertTrue(payload["at"])
        self.assertEqual(1, len(self.conflict_beads()))
        return payload

    def test_tests_failing_records_gate_failure(self) -> None:
        cmd = f"{shlex.quote(sys.executable)} -c 'import sys; sys.exit(1)'"
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as merge_branch:
            code = self.run_with_config(self.mock_config(cmd), skip_tests=False)
        self.assertEqual(1, code)
        merge_branch.assert_not_called()
        self.assert_gate_failed("tests", "tests failed (exit 1)")
        self.assertEqual("tests failed (exit 1)", self.events("merge_gate_failed")[0]["payload"]["reason"])
        self.assert_no_snapshot_or_record()

    def test_tests_timeout_records_gate_failure(self) -> None:
        cmd = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(30)'"
        code = self.run_with_config(self.mock_config(cmd, timeout=0.3), skip_tests=False)
        self.assertEqual(1, code)
        self.assert_gate_failed("tests", "tests timed out after 0.3s")
        self.assert_no_snapshot_or_record()

    def test_tests_start_failure_records_gate_failure(self) -> None:
        code = self.run_with_config(self.mock_config("/nonexistent/takt-test-runner"), skip_tests=False)
        self.assertEqual(1, code)
        self.assert_gate_failed("tests", "tests failed to start:")
        self.assert_no_snapshot_or_record()

    def test_preflight_conflict_records_gate_failure(self) -> None:
        worktree = Path(self.temp_dir.name) / "wt"
        worktree.mkdir()
        self.bead.execution_worktree_path = str(worktree)
        self.storage.save_bead(self.bead)
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch", side_effect=GitError("boom")), \
                patch("agent_takt.cli.commands.merge.WorktreeManager.conflicted_files", return_value=["a.py"]), \
                patch("agent_takt.cli.commands.merge.WorktreeManager.abort_merge"), \
                patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch") as merge_branch:
            code = self.merge(skip_rebase=False)
        self.assertEqual(1, code)
        merge_branch.assert_not_called()
        payload = self.assert_gate_failed("preflight", "conflict in 1 file")
        self.assertEqual("conflict in 1 file", payload["reason"])
        self.assert_no_snapshot_or_record()

    def test_merge_git_error_records_gate_failure_and_keeps_snapshot(self) -> None:
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_branch", side_effect=GitError("merge exploded")):
            code = self.merge()
        self.assertEqual(1, code)
        (event,) = self.events("merge_gate_failed")
        payload = event["payload"]
        self.assertEqual("merge", payload["gate"])
        self.assertEqual("merge exploded", payload["reason"])
        self.assertEqual("Test User <test@example.com>", payload["approver"])
        self.assertEqual(self.bead.bead_id, payload["feature_root_id"])
        self.assertTrue((self.root / payload["audit_path"]).exists())
        self.assertEqual([], self.records())
        self.assertEqual([], self.events("feature_merged"))
        history = [r.event for r in self.storage.load_bead(self.bead.bead_id).execution_history]
        self.assertNotIn("merged", history)
        self.assertEqual(2, len(list(self.audits_dir().iterdir())))  # .json + .md kept
        self.assertIn("Merge failed", self.stream.getvalue())

    def test_skip_flags_recorded_and_highlighted(self) -> None:
        self.assertEqual(0, self.merge(skip_rebase=True, skip_tests=True))
        (record,) = self.records()
        self.assertEqual("skipped", record["preflight"])
        self.assertEqual("skipped", record["test_gate"]["status"])
        lines = self.stream.getvalue().splitlines()
        gate_line = next(ln for ln in lines if "test gate: skipped" in ln)
        self.assertIn("preflight: skipped", gate_line)
        self.assertTrue(gate_line.startswith("! "), gate_line)  # highlighted via console.warn

    def test_missing_worktree_records_preflight_not_run(self) -> None:
        self.bead.execution_worktree_path = str(Path(self.temp_dir.name) / "does-not-exist")
        self.storage.save_bead(self.bead)
        self.assertEqual(0, self.merge(skip_rebase=False, skip_tests=True))
        (record,) = self.records()
        self.assertEqual("not_run", record["preflight"])
        gate_line = next(ln for ln in self.stream.getvalue().splitlines() if "preflight: not_run" in ln)
        self.assertTrue(gate_line.startswith("! "), gate_line)

    def test_test_command_not_configured_recorded(self) -> None:
        self.assertEqual(0, self.run_with_config(self.mock_config(""), skip_tests=False))
        (record,) = self.records()
        self.assertEqual({"status": "not_configured", "command": None, "duration_seconds": None}, record["test_gate"])

    def test_passed_gates_recorded_and_not_highlighted(self) -> None:
        worktree = Path(self.temp_dir.name) / "wt"
        worktree.mkdir()
        self.bead.execution_worktree_path = str(worktree)
        self.storage.save_bead(self.bead)
        cmd = f"{shlex.quote(sys.executable)} -c pass"
        with patch("agent_takt.cli.commands.merge.WorktreeManager.merge_main_into_branch"):
            code = self.run_with_config(self.mock_config(cmd), skip_rebase=False, skip_tests=False)
        self.assertEqual(0, code)
        (record,) = self.records()
        self.assertEqual("passed", record["preflight"])
        gate = record["test_gate"]
        self.assertEqual("passed", gate["status"])
        self.assertEqual(cmd, gate["command"])
        self.assertIsInstance(gate["duration_seconds"], float)
        gate_line = next(ln for ln in self.stream.getvalue().splitlines() if "test gate: passed" in ln)
        self.assertIn("preflight: passed", gate_line)
        self.assertFalse(gate_line.startswith("! "), gate_line)

    def test_summary_printed_before_merge_branch_is_called(self) -> None:
        seen: list[str] = []
        with patch(
            "agent_takt.cli.commands.merge.WorktreeManager.merge_branch",
            side_effect=lambda branch: seen.append(self.stream.getvalue()),
        ):
            self.assertEqual(0, self.merge())
        self.assertEqual(1, len(seen))
        self.assertIn("Merge summary for", seen[0])
        self.assertIn("approver: Test User <test@example.com>", seen[0])


class TestMergeSnapshots(_MergeRecordBase):
    def test_second_snapshot_includes_first_record_but_not_vice_versa(self) -> None:
        self.assertEqual(0, self.merge())
        self.feature_commit("two")
        self.assertEqual(0, self.merge())

        first, second = self.records()
        self.assertNotEqual(first["audit_path"], second["audit_path"])
        first_snapshot = json.loads((self.root / first["audit_path"]).read_text(encoding="utf-8"))
        second_snapshot = json.loads((self.root / second["audit_path"]).read_text(encoding="utf-8"))
        self.assertEqual([], first_snapshot["merges"])  # snapshot never contains its own record
        self.assertEqual(1, len(second_snapshot["merges"]))
        self.assertEqual(first["audit_sha256"], second_snapshot["merges"][0]["audit_sha256"])
        self.assertEqual(first["audit_path"], second_snapshot["merges"][0]["audit_path"])
        self.assertEqual(2, len(self.events("feature_merged")))
        # the first snapshot file is unchanged by the second merge
        self.assertEqual(
            first["audit_sha256"],
            hashlib.sha256((self.root / first["audit_path"]).read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
