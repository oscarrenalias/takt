from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ...audit import build_feature_audit, build_merge_summary_lines, render_feature_audit_markdown
from ...config import load_config
from ...console import ConsoleReporter
from ...gitutils import GitError, WorktreeManager, git_user_identity
from ...models import Bead
from ...storage import RepositoryStorage


@dataclass
class MergeGateRecord:
    """Approver and gate outcomes collected by command_merge for later consumers."""

    approver: str
    approver_source: str
    preflight: str = "not_run"
    test_gate: dict = field(
        default_factory=lambda: {"status": "not_configured", "command": None, "duration_seconds": None}
    )


def _resolve_approver(args: argparse.Namespace, repo_root: Path) -> tuple[str, str] | None:
    flag = (getattr(args, "approver", None) or "").strip()
    if flag:
        return flag, "flag"
    name, email = git_user_identity(repo_root)
    if not name:
        return None
    return (f"{name} <{email}>" if email else name), "git-config"


def _record_gate_failure(
    storage: RepositoryStorage,
    feature_root_id: str,
    gate: str,
    reason: str,
    record: MergeGateRecord,
) -> None:
    storage.record_event(
        "merge_gate_failed",
        {
            "feature_root_id": feature_root_id,
            "gate": gate,
            "reason": reason,
            "approver": record.approver,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )


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
    resolved = _resolve_approver(args, storage.root)
    if resolved is None:
        console.error("error: cannot determine approver; set git user.name or pass --approver")
        return 1
    gate_record = MergeGateRecord(approver=resolved[0], approver_source=resolved[1])
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
    if args.skip_rebase:
        gate_record.preflight = "skipped"
    else:
        if worktree_path and worktree_path.exists():
            with console.spin("Preflight: merging main into feature branch") as spinner:
                try:
                    worktrees.merge_main_into_branch(worktree_path)
                    gate_record.preflight = "passed"
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
                    _record_gate_failure(
                        storage, feature_root_id, "preflight",
                        f"conflict in {len(conflicted)} files" if len(conflicted) != 1 else "conflict in 1 file",
                        gate_record,
                    )
                    _emit_merge_conflict_bead(
                        storage, console, feature_root, feature_root_id,
                        config.scheduler.max_corrective_attempts,
                        conflict_desc, conflicted, args.bead_id,
                    )
                    return 1

    # Test gate
    if args.skip_tests:
        gate_record.test_gate = {"status": "skipped", "command": None, "duration_seconds": None}
    else:
        test_command = config.common.test_command
        if not test_command:
            console.warn("No test_command configured; skipping test gate")
        else:
            console.info(f"Running test gate: {test_command}")
            cwd = worktree_path if worktree_path and worktree_path.exists() else storage.root
            output_lines: list[str] = []
            started_at = time.monotonic()

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
                    _record_gate_failure(
                        storage, feature_root_id, "tests",
                        f"tests timed out after {config.common.test_timeout_seconds}s", gate_record,
                    )
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
                    _record_gate_failure(
                        storage, feature_root_id, "tests",
                        f"tests failed (exit {test_proc.returncode})", gate_record,
                    )
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
                gate_record.test_gate = {
                    "status": "passed",
                    "command": test_command,
                    "duration_seconds": round(time.monotonic() - started_at, 3),
                }
                console.success("Test gate passed")
            except OSError as exc:
                console.error(f"Test gate failed to start: {exc}")
                _record_gate_failure(
                    storage, feature_root_id, "tests",
                    f"tests failed to start: {' '.join(str(exc).split())}", gate_record,
                )
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

    # Audit snapshot and pre-merge summary (informational only; never prompts)
    audit = build_feature_audit(storage, feature_root_id)
    for line, highlight in build_merge_summary_lines(
        audit,
        preflight=gate_record.preflight,
        test_gate=gate_record.test_gate,
        approver=gate_record.approver,
    ):
        if highlight:
            console.warn(line)
        else:
            console.detail(line)

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    audit_dir = storage.root / ".takt" / "audits" / feature_root_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    json_path = audit_dir / f"{stamp}.json"
    md_path = audit_dir / f"{stamp}.md"
    json_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_feature_audit_markdown(audit) + "\n", encoding="utf-8")
    audit_sha256 = hashlib.sha256(json_path.read_bytes()).hexdigest()
    try:
        audit_path = json_path.relative_to(storage.root).as_posix()
    except ValueError:
        audit_path = str(json_path)

    try:
        main_sha_before = worktrees.rev_parse("HEAD")
        feature_head_sha = worktrees.rev_parse(branch_name)
        with console.spin(f"Merging {branch_name}") as spinner:
            try:
                worktrees.merge_branch(branch_name)
            except GitError:
                spinner.fail(f"Merge of {branch_name} failed")
                raise
            spinner.success(f"Merged {branch_name}")
    except GitError as exc:
        storage.record_event(
            "merge_gate_failed",
            {
                "feature_root_id": feature_root_id,
                "gate": "merge",
                "reason": str(exc),
                "approver": gate_record.approver,
                "audit_path": audit_path,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
        console.error(f"Merge failed: {exc}")
        return 1

    try:
        main_sha_after = worktrees.rev_parse("HEAD")
    except GitError:
        main_sha_after = None
    record = {
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "approver": gate_record.approver,
        "approver_source": gate_record.approver_source,
        "branch": branch_name,
        "feature_head_sha": feature_head_sha,
        "main_sha_before": main_sha_before,
        "main_sha_after": main_sha_after,
        "preflight": gate_record.preflight,
        "test_gate": gate_record.test_gate,
        "audit_path": audit_path,
        "audit_sha256": audit_sha256,
    }
    # Reload so bead state written by the merge (or other writers) is not clobbered.
    feature_root = storage.load_bead(feature_root_id)
    records = feature_root.metadata.get("merge_records")
    if not isinstance(records, list):
        records = []
    records.append(record)
    feature_root.metadata["merge_records"] = records
    storage.update_bead(
        feature_root,
        event="merged",
        summary=f"Merged {branch_name} into main by {gate_record.approver}",
    )
    storage.record_event("feature_merged", {**record, "feature_root_id": feature_root_id})
    return 0
