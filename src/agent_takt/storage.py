from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_HANDED_OFF,
    BEAD_IN_PROGRESS,
    BEAD_OPEN,
    BEAD_READY,
    Bead,
    ExecutionRecord,
    HandoffSummary,
    utc_now,
)


class RepositoryStorage:
    _auto_commit: bool = True

    SUMMARY_STATUS_KEYS = (
        BEAD_OPEN,
        BEAD_READY,
        BEAD_IN_PROGRESS,
        BEAD_BLOCKED,
        BEAD_DONE,
        BEAD_HANDED_OFF,
    )

    def __init__(self, root: Path) -> None:
        self._git_lock = threading.Lock()  # instance-level: each storage instance gets its own lock, preventing cross-test blocking in parallel test runs
        self.root = root.resolve()
        self.state_dir = self.root / ".takt"
        self.beads_dir = self.state_dir / "beads"
        self.logs_dir = self.state_dir / "logs"
        self.worktrees_dir = self.state_dir / "worktrees"
        self.telemetry_dir = self.state_dir / "telemetry"
        self.memory_dir = self.root / "docs" / "memory"

    def initialize(self) -> None:
        for path in (self.beads_dir, self.logs_dir, self.worktrees_dir, self.telemetry_dir, self.memory_dir):
            path.mkdir(parents=True, exist_ok=True)

    def bead_path(self, bead_id: str) -> Path:
        return self.beads_dir / f"{bead_id}.json"

    def _git_commit_bead(self, bead: Bead, path: Path, *, is_new: bool) -> None:
        """Stage and commit a single bead file; git failures are non-fatal."""
        if not RepositoryStorage._auto_commit:
            return
        if is_new:
            message = f"[bead] {bead.bead_id}: created ({bead.agent_type})"
        else:
            message = f"[bead] {bead.bead_id}: {bead.status}"
        try:
            with self._git_lock:
                subprocess.run(
                    ["git", "add", str(path)],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                diff_result = subprocess.run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=self.root,
                    capture_output=True,
                    timeout=30,
                )
                if diff_result.returncode == 0:
                    return
                subprocess.run(
                    ["git", "commit", "-m", message, "--no-verify"],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
        except Exception as exc:
            logger.warning(
                "git commit failed for bead %s: %s",
                bead.bead_id,
                exc,
                exc_info=True,
            )
            # Append a failure event to bead execution history and persist directly
            # to disk — bypassing _write_bead to avoid infinite recursion.
            bead.execution_history.append(
                ExecutionRecord(
                    timestamp=utc_now(),
                    event="git_commit_failed",
                    agent_type="scheduler",
                    summary=f"git commit failed: {exc}",
                    details={"error": str(exc)},
                )
            )
            bead_path = self.bead_path(bead.bead_id)
            tmp_path = bead_path.parent / f"{bead_path.stem}.{uuid.uuid4().hex[:8]}.tmp"
            try:
                tmp_path.write_text(json.dumps(bead.to_dict(), indent=2) + "\n", encoding="utf-8")
                tmp_path.replace(bead_path)
            except Exception as write_exc:
                logger.warning(
                    "Failed to persist git_commit_failed event for bead %s: %s",
                    bead.bead_id,
                    write_exc,
                )

    def _git_commit_bead_deletion(self, bead: Bead, path: Path) -> None:
        """Stage and commit a single bead file removal; git failures are non-fatal."""
        if not RepositoryStorage._auto_commit:
            return
        message = f"[bead] {bead.bead_id}: deleted"
        try:
            with self._git_lock:
                subprocess.run(
                    ["git", "add", str(path)],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                subprocess.run(
                    ["git", "commit", "-m", message, "--no-verify"],
                    cwd=self.root,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
        except Exception as exc:
            # Bead file is already deleted at this point; only log — execution history
            # cannot be updated after deletion.
            logger.warning(
                "git commit failed for bead deletion %s: %s",
                bead.bead_id,
                exc,
                exc_info=True,
            )

    def _write_bead(self, bead: Bead) -> None:
        self.initialize()
        path = self.bead_path(bead.bead_id)
        is_new = not path.exists()
        tmp_path = path.parent / f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_path.write_text(json.dumps(bead.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
        self._git_commit_bead(bead, path, is_new=is_new)

    def _missing_dependency_ids(self, dependencies: list[str]) -> list[str]:
        missing: list[str] = []
        for dependency_id in dependencies:
            if self.bead_path(dependency_id).exists():
                continue
            if dependency_id not in missing:
                missing.append(dependency_id)
        return missing

    def _validate_dependencies(self, dependencies: list[str]) -> None:
        """Reject dependency lists that reference beads not present in storage."""
        missing = self._missing_dependency_ids(dependencies)
        if not missing:
            return
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing dependency beads: {missing_list}")

    def _record_missing_dependency_warning(self, bead: Bead, dependency_id: str, error: ValueError) -> None:
        summary = f"dependency_missing: {dependency_id} not found"
        for record in reversed(bead.execution_history):
            if record.event != "dependency_missing":
                continue
            if record.summary == summary:
                return
        bead.execution_history.append(
            ExecutionRecord(
                timestamp=utc_now(),
                event="dependency_missing",
                agent_type="scheduler",
                summary=summary,
                details={"dependency_id": dependency_id, "error": str(error)},
            )
        )
        self._write_bead(bead)

    def save_bead(self, bead: Bead) -> None:
        """Persist a bead after enforcing that all declared dependencies exist."""
        self._validate_dependencies(bead.dependencies)
        self._write_bead(bead)

    def _cleanup_deleted_dependency_references(self, bead_id: str) -> None:
        """Remove a deleted bead from dependents, tolerating legacy on-disk corruption."""
        for other in self.list_beads():
            if bead_id not in other.dependencies:
                continue
            other.dependencies = [dependency for dependency in other.dependencies if dependency != bead_id]
            self._write_bead(other)

    def write_telemetry_artifact(
        self,
        *,
        bead_id: str,
        agent_type: str,
        attempt: int,
        started_at: str,
        finished_at: str,
        outcome: str,
        prompt_text: str | None,
        response_text: str | None,
        parsed_result: dict[str, Any] | None,
        metrics: dict[str, Any],
        error: dict[str, str] | None,
    ) -> Path:
        """Write a full telemetry artifact file atomically.

        Returns the path to the written artifact file.
        """
        artifact = {
            "telemetry_version": 1,
            "bead_id": bead_id,
            "agent_type": agent_type,
            "attempt": attempt,
            "started_at": started_at,
            "finished_at": finished_at,
            "outcome": outcome,
            "prompt_text": prompt_text,
            "response_text": response_text,
            "parsed_result": parsed_result,
            "metrics": metrics,
            "error": error,
        }
        bead_telemetry_dir = self.telemetry_dir / bead_id
        bead_telemetry_dir.mkdir(parents=True, exist_ok=True)
        path = bead_telemetry_dir / f"{attempt}.json"
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
        return path

    def load_bead(self, bead_id: str) -> Bead:
        path = self.bead_path(bead_id)

        # check if the file exists and was loaded
        if not path.exists():
            raise ValueError(f"Bead not found: {bead_id}")

        raw = path.read_text(encoding="utf-8")

        if not raw.strip():
            raise ValueError(f"Bead file is empty: {path}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid bead JSON in {path}: {exc}") from exc
        return Bead.from_dict(payload)

    def resolve_bead_id(self, prefix: str) -> str:
        """Resolve a bead ID prefix to a full bead ID.

        Returns the full bead ID if exactly one bead matches the prefix.
        Raises ValueError on no match or ambiguous (multiple) matches.
        """
        if self.bead_path(prefix).exists():
            return prefix
        if not self.beads_dir.exists():
            raise ValueError(f"No bead found matching prefix '{prefix}'")
        matches = [
            path.stem for path in self.beads_dir.glob("*.json")
            if path.stem.startswith(prefix)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) == 0:
            raise ValueError(f"No bead found matching prefix '{prefix}'")
        matches.sort()
        match_list = ", ".join(matches)
        raise ValueError(
            f"Ambiguous prefix '{prefix}' matches {len(matches)} beads: {match_list}"
        )

    @staticmethod
    def _bead_sort_key(bead: Bead) -> tuple[str, str]:
        """Sort by creation timestamp (first execution_history entry), falling back to bead_id."""
        if bead.execution_history:
            return (bead.execution_history[0].timestamp, bead.bead_id)
        return ("", bead.bead_id)

    def list_beads(self) -> list[Bead]:
        if not self.beads_dir.exists():
            return []
        beads = [self.load_bead(path.stem) for path in sorted(self.beads_dir.glob("*.json"))]
        return sorted(beads, key=self._bead_sort_key)

    def allocate_bead_id(self) -> str:
        """Allocate a new bead ID using UUID format.

        Returns a bead ID of the form B-{first 8 hex chars of UUID}.
        """
        self.initialize()
        return f"B-{uuid.uuid4().hex[:8]}"

    def allocate_child_bead_id(self, parent_id: str, suffix: str) -> str:
        """Allocate a child bead ID given a parent ID and suffix.

        Generates an ID by appending the suffix to the parent ID with a hyphen.
        If a bead with that ID already exists, appends a numeric index (e.g., -2, -3)
        to ensure uniqueness.

        Args:
            parent_id: The parent bead ID (e.g., 'B-a7bc3f91').
            suffix: A short suffix identifying the bead type (e.g., 'test', 'docs', 'review').

        Returns:
            A unique child bead ID (e.g., 'B-a7bc3f91-test' or 'B-a7bc3f91-test-2').
        """
        candidate = f"{parent_id}-{suffix}"
        if not self.bead_path(candidate).exists():
            return candidate
        index = 2
        while self.bead_path(f"{candidate}-{index}").exists():
            index += 1
        return f"{candidate}-{index}"

    def create_bead(
        self,
        *,
        title: str,
        agent_type: str,
        description: str,
        status: str = BEAD_READY,
        bead_type: str = "task",
        parent_id: str | None = None,
        dependencies: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        linked_docs: list[str] | None = None,
        feature_root_id: str | None = None,
        execution_branch_name: str = "",
        execution_worktree_path: str = "",
        expected_files: list[str] | None = None,
        expected_globs: list[str] | None = None,
        touched_files: list[str] | None = None,
        changed_files: list[str] | None = None,
        bead_id: str | None = None,
        metadata: dict | None = None,
        conflict_risks: str = "",
        labels: list[str] | None = None,
        recovery_for: str | None = None,
        priority: str | None = None,
    ) -> Bead:
        allocated_bead_id = bead_id or self.allocate_bead_id()
        resolved_feature_root_id = feature_root_id
        resolved_branch_name = execution_branch_name
        resolved_worktree_path = execution_worktree_path
        if parent_id:
            parent = self.load_bead(parent_id)
            if resolved_feature_root_id is None:
                if parent.bead_type == "epic":
                    resolved_feature_root_id = allocated_bead_id
                else:
                    resolved_feature_root_id = self.feature_root_id_for(parent)
            if not resolved_branch_name:
                resolved_branch_name = parent.execution_branch_name
            if not resolved_worktree_path:
                resolved_worktree_path = parent.execution_worktree_path
        elif bead_type != "epic":
            resolved_feature_root_id = resolved_feature_root_id or allocated_bead_id
        if resolved_feature_root_id and not resolved_branch_name:
            resolved_branch_name = self.default_execution_branch_name(resolved_feature_root_id)
        if resolved_feature_root_id and not resolved_worktree_path:
            resolved_worktree_path = str(self.worktrees_dir / resolved_feature_root_id)
        bead = Bead(
            bead_id=allocated_bead_id,
            title=title,
            agent_type=agent_type,
            description=description,
            status=status,
            bead_type=bead_type,
            parent_id=parent_id,
            dependencies=list(dependencies or []),
            acceptance_criteria=list(acceptance_criteria or []),
            linked_docs=list(linked_docs or []),
            feature_root_id=resolved_feature_root_id,
            execution_branch_name=resolved_branch_name,
            execution_worktree_path=resolved_worktree_path,
            expected_files=list(expected_files or []),
            expected_globs=list(expected_globs or []),
            touched_files=list(touched_files or []),
            changed_files=list(changed_files or []),
            metadata=dict(metadata or {}),
            conflict_risks=conflict_risks,
            labels=list(labels or []),
            recovery_for=recovery_for,
            priority=priority,
        )
        bead.execution_history.append(
            ExecutionRecord(timestamp=utc_now(), event="created", agent_type="scheduler", summary="Bead created")
        )
        self.save_bead(bead)
        return bead

    def delete_bead(self, bead_id: str, *, force: bool = False) -> Bead:
        """Delete a bead by ID.

        Checks:
        1. Bead must exist.
        2. Bead must have no children (beads whose parent_id == bead_id).
        3. Status must be open/ready/blocked, or force=True to bypass status check.

        Returns the deleted Bead object.
        """
        bead = self.load_bead(bead_id)

        children = [b for b in self.list_beads() if b.parent_id == bead_id]
        if children:
            child_ids = ", ".join(c.bead_id for c in children)
            raise ValueError(f"Cannot delete bead {bead_id}: has child beads: {child_ids}")

        protected_statuses = {BEAD_IN_PROGRESS, BEAD_DONE, BEAD_HANDED_OFF}
        if not force and bead.status in protected_statuses:
            raise ValueError(
                f"Cannot delete bead {bead_id} with status '{bead.status}' without force=True"
            )

        path = self.bead_path(bead_id)
        path.unlink()
        self._git_commit_bead_deletion(bead, path)

        self._cleanup_deleted_dependency_references(bead_id)

        return bead

    def update_bead(self, bead: Bead, *, event: str | None = None, summary: str = "") -> None:
        if event:
            bead.execution_history.append(
                ExecutionRecord(timestamp=utc_now(), event=event, agent_type=bead.agent_type, summary=summary)
            )
        self.save_bead(bead)

    def record_guardrail_context(
        self,
        bead: Bead,
        *,
        template_path: Path,
        template_text: str,
        prompt_context: dict | None = None,
    ) -> None:
        try:
            relative_template_path = str(template_path.relative_to(self.root))
        except ValueError:
            relative_template_path = str(template_path)
        guardrails = {
            "agent_type": bead.agent_type,
            "template_path": relative_template_path,
            "template_text": template_text,
            "captured_at": utc_now(),
        }
        bead.metadata["guardrails"] = guardrails
        if prompt_context is not None:
            bead.metadata["worker_prompt_context"] = prompt_context
        bead.execution_history.append(
            ExecutionRecord(
                timestamp=utc_now(),
                event="guardrails_applied",
                agent_type=bead.agent_type,
                summary=f"Applied guardrails from {relative_template_path}",
                details={"template_path": relative_template_path},
            )
        )
        self.save_bead(bead)

    def dependency_satisfied(self, bead: Bead) -> bool:
        """Return whether all dependencies are done, recording corrupt references once.

        This keeps scheduler startup resilient if an on-disk bead predates
        dependency validation or was edited outside normal storage APIs.
        """
        for dependency_id in bead.dependencies:
            try:
                dependency = self.load_bead(dependency_id)
            except ValueError as exc:
                self._record_missing_dependency_warning(bead, dependency_id, exc)
                return False
            if dependency.status != BEAD_DONE:
                return False
        return True

    def ready_beads(self) -> list[Bead]:
        """List runnable beads, excluding leased beads and corrupt dependency graphs."""
        ready: list[Bead] = []
        for bead in self.list_beads():
            if bead.status != BEAD_READY:
                continue
            if bead.lease is not None:
                continue
            if self.dependency_satisfied(bead):
                ready.append(bead)
        return sorted(ready, key=self._bead_sort_key)

    def record_event(self, event_type: str, payload: dict) -> None:
        self.initialize()
        event_path = self.logs_dir / "events.jsonl"
        record = {"timestamp": utc_now(), "event_type": event_type, "payload": payload}
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def write_memory_file(self, relative_path: str, content: str) -> Path:
        target = self.memory_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _resolve_linked_doc_path(self, linked_doc: str) -> Path | None:
        direct_path = self.root / linked_doc
        if direct_path.exists():
            return direct_path

        target_name = Path(linked_doc).name
        if not target_name:
            return None

        matches: list[Path] = []
        for match in self.root.rglob(target_name):
            if not match.is_file():
                continue
            if any(part in {".git", ".takt"} for part in match.parts):
                continue
            matches.append(match)
        if len(matches) == 1:
            return matches[0]
        return None

    def linked_context_paths(self, bead: Bead) -> list[Path]:
        candidates: list[Path] = []
        for linked_doc in bead.linked_docs:
            resolved = self._resolve_linked_doc_path(linked_doc)
            if resolved is not None:
                candidates.append(resolved)
        agents_path = self.root / "AGENTS.md"
        if agents_path.exists():
            candidates.append(agents_path)
        if self.memory_dir.exists():
            candidates.extend(sorted(path for path in self.memory_dir.rglob("*") if path.is_file()))
        unique_candidates = list(dict.fromkeys(candidates))
        return [path for path in unique_candidates if path.exists()]

    def active_beads(self) -> list[Bead]:
        return [
            bead for bead in self.list_beads()
            if bead.status == "in_progress" and bead.lease is not None
        ]

    def active_claims(self) -> list[dict]:
        claims = []
        for bead in self.active_beads():
            claims.append({
                "bead_id": bead.bead_id,
                "feature_root_id": self.feature_root_id_for(bead),
                "agent_type": bead.agent_type,
                "title": bead.title,
                "scope_source": bead.scope_source(),
                "execution_branch_name": bead.execution_branch_name,
                "execution_worktree_path": bead.execution_worktree_path,
                "expected_files": bead.expected_files,
                "expected_globs": bead.expected_globs,
                "touched_files": bead.touched_files,
                "conflict_risks": bead.conflict_risks,
                "block_reason": bead.block_reason,
                "lease": bead.lease.__dict__ if bead.lease else None,
            })
        return claims

    def set_handoff(self, bead: Bead, handoff: HandoffSummary) -> None:
        bead.handoff_summary = handoff
        bead.changed_files = list(handoff.changed_files)
        bead.updated_docs = list(handoff.updated_docs)
        bead.expected_files = list(handoff.expected_files)
        bead.expected_globs = list(handoff.expected_globs)
        bead.touched_files = list(handoff.touched_files)
        bead.block_reason = handoff.block_reason
        bead.conflict_risks = handoff.conflict_risks
        self.save_bead(bead)

    def default_execution_branch_name(self, feature_root_id: str) -> str:
        """Generate a Git branch name from a feature root ID.

        Converts the feature root ID to lowercase to comply with Git branch naming
        conventions while preserving the hyphenated UUID format.

        Args:
            feature_root_id: The bead ID serving as the feature root (e.g., 'B-a7bc3f91').

        Returns:
            A lowercased branch name (e.g., 'feature/b-a7bc3f91').
        """
        return f"feature/{feature_root_id.lower()}"

    def feature_root_id_for(self, bead: Bead) -> str | None:
        """Determine the feature root ID for a given bead.

        Walks up the bead hierarchy to find the root bead that serves as the feature root.
        - If the bead has an explicit feature_root_id, returns it.
        - If the bead is a child of a non-epic bead, traverses to the root bead of that chain.
        - If the bead is a child of an epic, returns the bead's own ID (it's the root).
        - If the bead is itself an epic with no parent, returns None (epics have no feature root).

        Args:
            bead: The bead to find the feature root for.

        Returns:
            The feature root bead ID (which is used for branch naming and worktree paths),
            or None if the bead is an epic.
        """
        if bead.feature_root_id:
            return bead.feature_root_id
        current = bead
        while current.parent_id:
            parent = self.load_bead(current.parent_id)
            if parent.bead_type == "epic":
                return current.bead_id
            current = parent
        if current.bead_type == "epic":
            return None
        return current.bead_id

    def feature_root_bead_for(self, bead: Bead) -> Bead | None:
        feature_root_id = self.feature_root_id_for(bead)
        if not feature_root_id:
            return None
        return self.load_bead(feature_root_id)

    def summary(self, *, feature_root_id: str | None = None) -> dict:
        beads = self.list_beads()
        if feature_root_id:
            target_path = self.bead_path(feature_root_id)
            if not target_path.exists():
                beads = []
            else:
                target = self.load_bead(feature_root_id)
                if self.feature_root_id_for(target) != feature_root_id:
                    beads = []
                else:
                    beads = [
                        bead for bead in beads
                        if bead.bead_id == feature_root_id or self.feature_root_id_for(bead) == feature_root_id
                    ]

        counts = {status: 0 for status in self.SUMMARY_STATUS_KEYS}
        for bead in beads:
            if bead.status in counts:
                counts[bead.status] += 1

        ready = [bead for bead in beads if bead.status == BEAD_READY]
        blocked = [bead for bead in beads if bead.status == BEAD_BLOCKED]

        return {
            "counts": counts,
            "next_up": [self._summary_item(bead) for bead in sorted(ready, key=self._bead_sort_key)[:5]],
            "attention": [self._summary_item(bead, include_block_reason=True) for bead in sorted(blocked, key=self._bead_sort_key)[:5]],
        }

    def _summary_item(self, bead: Bead, *, include_block_reason: bool = False) -> dict:
        payload = {
            "bead_id": bead.bead_id,
            "title": bead.title,
            "agent_type": bead.agent_type,
            "status": bead.status,
            "parent_id": bead.parent_id,
            "feature_root_id": self.feature_root_id_for(bead),
        }
        if include_block_reason:
            payload["block_reason"] = bead.block_reason or bead.handoff_summary.block_reason or ""
        return payload
