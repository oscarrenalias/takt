from __future__ import annotations

import argparse
import shlex
import subprocess
import threading
from pathlib import Path

from ...config import load_config
from ...console import ConsoleReporter
from ...gitutils import GitError, WorktreeManager
from ...models import Bead
from ...storage import RepositoryStorage


def _get_diff_context(worktree_path: Path) -> str:
    proc = subprocess.run(
        ["git", "diff"],
        cwd=worktree_path,
        text=True,
        capture_output=True,
        check=False,
    )
    output = proc.stdout
    if len(output) > 4000:
        output = output[:4000] + "\n... (truncated)"
    return output


def _merge_conflict_attempt_cap_exceeded(
    storage: RepositoryStorage,
    feature_root_id: str,
    max_attempts: int,
) -> bool:
    all_conflict_beads = [
        b for b in storage.list_beads()
        if b.bead_type == "merge-conflict"
        and storage.feature_root_id_for(b) == feature_root_id
    ]
    return len(all_conflict_beads) >= max_attempts


def _emit_merge_conflict_bead(
    storage: RepositoryStorage,
    console: ConsoleReporter,
    feature_root: Bead,
    feature_root_id: str,
    max_attempts: int,
    description: str,
    conflicted_files: list[str],
    retry_bead_id: str,
) -> None:
    if _merge_conflict_attempt_cap_exceeded(storage, feature_root_id, max_attempts):
        console.error(
            f"Corrective attempt cap ({max_attempts}) exceeded for feature {feature_root_id}. "
            "Manual operator intervention required."
        )
        return
    conflict_bead = storage.create_bead(
        title=f"Resolve merge conflicts for {feature_root.title or feature_root_id}",
        agent_type="developer",
        description=description,
        bead_type="merge-conflict",
        parent_id=feature_root_id,
        feature_root_id=feature_root_id,
        expected_files=conflicted_files,
        conflict_risks=f"Conflicted files: {', '.join(conflicted_files)}" if conflicted_files else "Test/merge failure",
    )
    console.error(
        f"Created merge-conflict bead {conflict_bead.bead_id}. "
        f"Resolve it then retry: takt merge {retry_bead_id}"
    )


def command_merge(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    config = load_config(storage.root)
    bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    feature_root = storage.feature_root_bead_for(bead) or bead
    feature_root_id = storage.feature_root_id_for(bead) or bead.bead_id
    branch_name = (
        feature_root.execution_branch_name
        or bead.execution_branch_name
        or feature_root.branch_name
        or bead.branch_name
    )
    if not branch_name:
        raise SystemExit(f"{bead.bead_id} has no feature branch to merge")

    # Block if an unresolved merge-conflict bead already exists for this feature root
    existing_conflict = next(
        (
            b for b in storage.list_beads()
            if b.bead_type == "merge-conflict"
            and storage.feature_root_id_for(b) == feature_root_id
            and b.status != "done"
        ),
        None,
    )
    if existing_conflict:
        console.error(
            f"Unresolved merge-conflict bead {existing_conflict.bead_id} exists for this feature. "
            f"Resolve it first, then retry: takt merge {args.bead_id}"
        )
        return 1

    worktrees = WorktreeManager(storage.root, storage.worktrees_dir)
    worktree_path = Path(
        feature_root.execution_worktree_path or bead.execution_worktree_path or ""
    )

    # Preflight: merge main into the feature branch to detect conflicts early
    if not args.skip_rebase:
        if worktree_path and worktree_path.exists():
            with console.spin("Preflight: merging main into feature branch") as spinner:
                try:
                    worktrees.merge_main_into_branch(worktree_path)
                    spinner.success("Preflight passed")
                except GitError as exc:
                    spinner.fail("Preflight conflict detected")
                    conflicted = worktrees.conflicted_files(worktree_path)
                    diff_context = _get_diff_context(worktree_path)
                    try:
                        worktrees.abort_merge(worktree_path)
                    except GitError:
                        pass
                    conflict_desc = (
                        f"Merge conflict detected during preflight merge of main into {branch_name}.\n"
                        f"Conflicted files: {', '.join(conflicted) if conflicted else 'unknown'}\n\n"
                        f"Git error: {exc}\n\n"
                        f"Diff context:\n{diff_context}"
                    )
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        conflict_desc, conflicted, args.bead_id,
                    )
                    return 1

    # Test gate
    if not args.skip_tests:
        test_command = config.common.test_command
        if not test_command:
            console.warn("No test_command configured; skipping test gate")
        else:
            console.info(f"Running test gate: {test_command}")
            cwd = worktree_path if worktree_path and worktree_path.exists() else storage.root
            output_lines: list[str] = []

            try:
                test_proc = subprocess.Popen(
                    shlex.split(test_command),
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )

                def _stream_output() -> None:
                    assert test_proc.stdout is not None
                    for line in test_proc.stdout:
                        output_lines.append(line)
                        with console._lock:
                            console.stream.write(line)
                            console.stream.flush()

                reader = threading.Thread(target=_stream_output, daemon=True)
                reader.start()
                try:
                    test_proc.wait(timeout=config.common.test_timeout_seconds)
                except subprocess.TimeoutExpired:
                    test_proc.kill()
                    test_proc.wait()
                    reader.join(timeout=5)
                    console.error(f"Test gate timed out after {config.common.test_timeout_seconds}s")
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        (
                            f"Test gate timed out for {branch_name}.\n\n"
                            f"Command: {test_command}\n"
                            f"Timeout: {config.common.test_timeout_seconds}s"
                        ),
                        [], args.bead_id,
                    )
                    return 1
                reader.join(timeout=5)

                if test_proc.returncode != 0:
                    console.error("Test gate failed")
                    failure_output = "".join(output_lines).strip()
                    if len(failure_output) > 4000:
                        failure_output = failure_output[:4000] + "\n... (truncated)"
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        (
                            f"Test gate failed for {branch_name}.\n\n"
                            f"Command: {test_command}\n\n"
                            f"Output:\n{failure_output}"
                        ),
                        [], args.bead_id,
                    )
                    return 1
                console.success("Test gate passed")
            except OSError as exc:
                console.error(f"Test gate failed to start: {exc}")
                _emit_merge_conflict_bead(
                    storage, console, feature_root, feature_root_id,
                    config.scheduler.max_corrective_attempts,
                    (
                        f"Test gate failed to start for {branch_name}.\n\n"
                        f"Command: {test_command}\n\n"
                        f"Error: {exc}"
                    ),
                    [], args.bead_id,
                )
                return 1

    with console.spin(f"Merging {branch_name}") as spinner:
        worktrees.merge_branch(branch_name)
        spinner.success(f"Merged {branch_name}")
    return 0
