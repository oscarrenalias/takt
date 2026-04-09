from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


BEAD_OPEN = "open"
BEAD_READY = "ready"
BEAD_IN_PROGRESS = "in_progress"
BEAD_HANDED_OFF = "handed_off"
BEAD_BLOCKED = "blocked"
BEAD_DONE = "done"

ACTIVE_STATUSES = {BEAD_OPEN, BEAD_READY, BEAD_IN_PROGRESS, BEAD_HANDED_OFF, BEAD_BLOCKED}
TERMINAL_STATUSES = {BEAD_DONE}
AGENT_TYPES = {"planner", "developer", "tester", "documentation", "review", "scheduler", "recovery", "investigator"}
MUTATING_AGENTS = {"developer", "tester", "documentation"}
BEAD_TYPES = {"task", "epic", "feature", "merge-conflict", "recovery"}
# Valid non-default priority values.  "normal" is the alias for None (not persisted).
BEAD_PRIORITIES: frozenset[str] = frozenset({"high"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Lease:
    owner: str
    expires_at: str


@dataclass
class ExecutionRecord:
    timestamp: str
    event: str
    agent_type: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffSummary:
    completed: str = ""
    remaining: str = ""
    risks: str = ""
    verdict: str = ""
    findings_count: int = 0
    requires_followup: bool = False
    changed_files: list[str] = field(default_factory=list)
    updated_docs: list[str] = field(default_factory=list)
    next_action: str = ""
    next_agent: str = ""
    block_reason: str = ""
    expected_files: list[str] = field(default_factory=list)
    expected_globs: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    conflict_risks: str = ""
    design_decisions: str = ""
    test_coverage_notes: str = ""
    known_limitations: str = ""


@dataclass
class Bead:
    bead_id: str
    title: str
    agent_type: str
    description: str
    status: str = BEAD_OPEN
    bead_type: str = "task"
    parent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    linked_docs: list[str] = field(default_factory=list)
    feature_root_id: str | None = None
    execution_branch_name: str = ""
    execution_worktree_path: str = ""
    expected_files: list[str] = field(default_factory=list)
    expected_globs: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    updated_docs: list[str] = field(default_factory=list)
    handoff_summary: HandoffSummary = field(default_factory=HandoffSummary)
    block_reason: str = ""
    conflict_risks: str = ""
    branch_name: str = ""
    worktree_path: str = ""
    lease: Lease | None = None
    retries: int = 0
    execution_history: list[ExecutionRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    recovery_for: str | None = None
    priority: str | None = None

    def __post_init__(self) -> None:
        if self.priority == "normal":
            self.priority = None
        elif self.priority is not None and self.priority not in BEAD_PRIORITIES:
            valid = ", ".join(sorted(BEAD_PRIORITIES | {"normal"}))
            raise ValueError(f"Invalid priority {self.priority!r}; valid values are: {valid}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def scope_source(self) -> str:
        if self.touched_files:
            return "touched_files"
        if self.expected_files:
            return "expected_files"
        if self.expected_globs:
            return "expected_globs"
        return "none"

    def scope_entries(self) -> list[str]:
        if self.touched_files:
            return list(self.touched_files)
        if self.expected_files:
            return list(self.expected_files)
        if self.expected_globs:
            return list(self.expected_globs)
        return []

    def has_scope(self) -> bool:
        return self.scope_source() != "none"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bead":
        handoff = HandoffSummary(**data.get("handoff_summary", {}))
        lease_data = data.get("lease")
        lease = Lease(**lease_data) if lease_data else None
        history = [ExecutionRecord(**item) for item in data.get("execution_history", [])]
        return cls(
            bead_id=data["bead_id"],
            title=data["title"],
            agent_type=data["agent_type"],
            description=data["description"],
            status=data.get("status", BEAD_OPEN),
            bead_type=data.get("bead_type", "task"),
            parent_id=data.get("parent_id"),
            dependencies=list(data.get("dependencies", [])),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            linked_docs=list(data.get("linked_docs", [])),
            feature_root_id=data.get("feature_root_id"),
            execution_branch_name=data.get("execution_branch_name", ""),
            execution_worktree_path=data.get("execution_worktree_path", ""),
            expected_files=list(data.get("expected_files", [])),
            expected_globs=list(data.get("expected_globs", [])),
            touched_files=list(data.get("touched_files", [])),
            changed_files=list(data.get("changed_files", [])),
            updated_docs=list(data.get("updated_docs", [])),
            handoff_summary=handoff,
            block_reason=data.get("block_reason", ""),
            conflict_risks=data.get("conflict_risks", ""),
            branch_name=data.get("branch_name", ""),
            worktree_path=data.get("worktree_path", ""),
            lease=lease,
            retries=int(data.get("retries", 0)),
            execution_history=history,
            metadata=dict(data.get("metadata", {})),
            labels=list(data.get("labels", [])),
            recovery_for=data.get("recovery_for"),
            priority=data.get("priority"),
        )


@dataclass
class PlanChild:
    title: str
    agent_type: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    linked_docs: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    expected_globs: list[str] = field(default_factory=list)
    children: list["PlanChild"] = field(default_factory=list)


@dataclass
class PlanProposal:
    epic_title: str
    epic_description: str
    linked_docs: list[str] = field(default_factory=list)
    feature: PlanChild | None = None


@dataclass
class AgentRunResult:
    outcome: str = "completed"
    summary: str = ""
    completed: str = ""
    remaining: str = ""
    risks: str = ""
    verdict: str = ""
    findings_count: int = 0
    requires_followup: bool | None = None
    expected_files: list[str] = field(default_factory=list)
    expected_globs: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    updated_docs: list[str] = field(default_factory=list)
    next_action: str = ""
    next_agent: str = ""
    new_beads: list[dict[str, Any]] = field(default_factory=list)
    block_reason: str = ""
    conflict_risks: str = ""
    design_decisions: str = ""
    test_coverage_notes: str = ""
    known_limitations: str = ""
    # Investigator-specific fields (populated only for investigator beads)
    findings: str = ""
    recommendations: str = ""
    risk_areas: str = ""
    report_path: str = ""
    telemetry: dict[str, Any] | None = None


@dataclass
class SchedulerResult:
    started: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    correctives_created: list[str] = field(default_factory=list)
