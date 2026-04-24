from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    tags: tuple[str, ...]


@dataclass(frozen=True)
class RunInputs:
    bead: dict | None
    tag_filter: tuple[str, ...]
    project_filter: tuple[str, ...]
    max_parallel: int
    runner: str | None
    project_max_workers: int | None


@dataclass
class ProjectResult:
    name: str
    path: Path
    status: str  # "success" | "error" | "skipped"
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    outputs: dict  # {created_beads: [...] | None, run_summary: {...} | None}


@dataclass
class FleetRun:
    run_id: str  # "FR-<8hex>"
    command: str  # "dispatch" | "run"
    started_at: datetime
    finished_at: datetime | None
    inputs: RunInputs
    projects: list[ProjectResult] = field(default_factory=list)
    crashed: bool = False

    @property
    def aggregate(self) -> dict:
        total = len(self.projects)
        succeeded = sum(1 for p in self.projects if p.status == "success")
        failed = sum(1 for p in self.projects if p.status == "error")
        skipped = sum(1 for p in self.projects if p.status == "skipped")
        return {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
        }
