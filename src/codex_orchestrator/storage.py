from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    SUMMARY_STATUS_KEYS = (
        BEAD_OPEN,
        BEAD_READY,
        BEAD_IN_PROGRESS,
        BEAD_BLOCKED,
        BEAD_DONE,
        BEAD_HANDED_OFF,
    )

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_dir = self.root / ".orchestrator"
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

    def save_bead(self, bead: Bead) -> None:
        self.initialize()
        path = self.bead_path(bead.bead_id)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_text(json.dumps(bead.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)

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
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError(f"Bead file is empty: {path}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid bead JSON in {path}: {exc}") from exc
        return Bead.from_dict(payload)

    def list_beads(self) -> list[Bead]:
        if not self.beads_dir.exists():
            return []
        beads = [self.load_bead(path.stem) for path in sorted(self.beads_dir.glob("*.json"))]
        return sorted(beads, key=lambda bead: bead.bead_id)

    def allocate_bead_id(self) -> str:
        self.initialize()
        numbers: list[int] = []
        for path in self.beads_dir.glob("B*.json"):
            stem = path.stem
            if stem.startswith("B") and stem[1:].isdigit():
                numbers.append(int(stem[1:]))
        return f"B{(max(numbers) + 1) if numbers else 1:04d}"

    def allocate_child_bead_id(self, parent_id: str, suffix: str) -> str:
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
        bead_id: str | None = None,
        metadata: dict | None = None,
        conflict_risks: str = "",
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
            metadata=dict(metadata or {}),
            conflict_risks=conflict_risks,
        )
        bead.execution_history.append(
            ExecutionRecord(timestamp=utc_now(), event="created", agent_type="scheduler", summary="Bead created")
        )
        self.save_bead(bead)
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
        return all(self.load_bead(dep).status == BEAD_DONE for dep in bead.dependencies)

    def ready_beads(self) -> list[Bead]:
        ready: list[Bead] = []
        for bead in self.list_beads():
            if bead.status != BEAD_READY:
                continue
            if bead.lease is not None:
                continue
            if self.dependency_satisfied(bead):
                ready.append(bead)
        return sorted(ready, key=lambda item: item.bead_id)

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
            if any(part in {".git", ".orchestrator"} for part in match.parts):
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
        return f"feature/{feature_root_id.lower()}"

    def feature_root_id_for(self, bead: Bead) -> str | None:
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
            "next_up": [self._summary_item(bead) for bead in sorted(ready, key=lambda item: item.bead_id)[:5]],
            "attention": [self._summary_item(bead, include_block_reason=True) for bead in sorted(blocked, key=lambda item: item.bead_id)[:5]],
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
