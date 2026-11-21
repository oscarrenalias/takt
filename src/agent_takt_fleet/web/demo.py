"""Synthetic fleet builder for the web UI tests and ``--demo`` mode.

``build_fixture_fleet`` writes a set of fake takt projects plus a fleet
registry file under a caller-supplied directory.  It never touches the real
registry or any real project, and takes all timestamps from an injectable
clock so output is deterministic for a fixed clock.

Project variants (assigned by project index, cycling every five projects):

* 0 ``full``      current bead shape, finished run plus an alive active run
* 1 ``older``     beads missing optional fields
* 2 ``newer``     beads (and handoff summaries) with unknown extra fields
* 3 ``corrupt``   one unparseable bead file next to valid ones
* 4 ``no-version`` no ``.takt/version.json``

One extra registry entry always points at a path that does not exist.
Use ``count >= 5`` to get every variant.  This module imports nothing from
``agent_takt``: the file shapes follow docs/events.md and the bead JSON
contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

Clock = Callable[[], datetime]

VARIANTS = ("full", "older", "newer", "corrupt", "no-version")
MISSING_PROJECT_NAME = "ghost"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class FixtureProject:
    name: str
    path: Path
    tags: tuple[str, ...]
    variant: str
    feature_root_id: str | None = None
    active_run_id: str | None = None
    bead_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixtureFleet:
    root: Path
    registry_path: Path
    projects: tuple[FixtureProject, ...]
    now: datetime
    missing: tuple[FixtureProject, ...] = field(default=())


def _hex8(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bead(
    bead_id: str,
    title: str,
    agent: str,
    status: str,
    now: datetime,
    *,
    root: str | None,
    parent: str | None = None,
    deps: list[str] | None = None,
    age_min: int = 60,
    block_reason: str = "",
    bead_type: str = "task",
) -> dict[str, Any]:
    created = (now - timedelta(minutes=age_min)).isoformat()
    history = [
        {"timestamp": created, "event": "created", "agent_type": "scheduler",
         "summary": "Bead created", "details": {}},
    ]
    if status in ("in_progress", "done", "blocked", "handed_off"):
        history.append({
            "timestamp": (now - timedelta(minutes=age_min - 5)).isoformat(),
            "event": "started", "agent_type": agent,
            "summary": "Worker started", "details": {},
        })
    if status == "done":
        history.append({
            "timestamp": (now - timedelta(minutes=age_min - 15)).isoformat(),
            "event": "completed", "agent_type": agent,
            "summary": f"{title} completed", "details": {},
        })
    handoff = {
        "completed": f"{title} done" if status == "done" else "",
        "remaining": "", "risks": "",
        "verdict": "approved" if status == "done" else "",
        "findings_count": 0, "requires_followup": False,
        "changed_files": [], "updated_docs": [], "next_action": "",
        "next_agent": "", "block_reason": block_reason,
        "expected_files": [], "expected_globs": [], "touched_files": [],
        "conflict_risks": "", "design_decisions": "",
        "test_coverage_notes": "", "known_limitations": "",
        "assumptions": [], "criteria_status": [],
    }
    return {
        "bead_id": bead_id,
        "title": title,
        "agent_type": agent,
        "description": f"Synthetic bead: {title}",
        "status": status,
        "bead_type": bead_type,
        "parent_id": parent,
        "dependencies": deps or [],
        "acceptance_criteria": [f"{title} works"],
        "linked_docs": [],
        "feature_root_id": root,
        "execution_branch_name": f"feature/{root.lower()}" if root else "",
        "execution_worktree_path": "",
        "expected_files": [],
        "expected_globs": [],
        "touched_files": [],
        "changed_files": [],
        "updated_docs": [],
        "handoff_summary": handoff,
        "block_reason": block_reason,
        "conflict_risks": "",
        "branch_name": "",
        "worktree_path": "",
        "lease": None,
        "retries": 0,
        "execution_history": history,
        "metadata": {},
        "labels": [],
        "recovery_for": None,
        "priority": None,
    }


_OPTIONAL = (
    "bead_type", "parent_id", "linked_docs", "execution_branch_name",
    "execution_worktree_path", "expected_files", "expected_globs",
    "touched_files", "changed_files", "updated_docs", "conflict_risks",
    "branch_name", "worktree_path", "lease", "retries", "metadata", "labels",
    "recovery_for", "priority", "handoff_summary", "block_reason",
)


def _to_older(bead: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bead.items() if k not in _OPTIONAL}


def _to_newer(bead: dict[str, Any]) -> dict[str, Any]:
    out = dict(bead)
    out["risk_score"] = 0.42
    out["reviewers"] = [{"name": "alice", "state": "pending"}]
    out["handoff_summary"] = {**bead["handoff_summary"], "confidence": "high"}
    return out


def _event(ts: datetime, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": ts.isoformat(), "event_type": event_type, "payload": payload}


def _run_events(
    run_id: str,
    start: datetime,
    *,
    pid: int,
    hostname: str,
    feature_root: str,
    finished: bool,
) -> list[dict[str, Any]]:
    def at(minutes: float) -> datetime:
        return start + timedelta(minutes=minutes)

    rid = {"run_id": run_id}
    events = [
        _event(start, "scheduler_run_started", {
            **rid, "pid": pid, "hostname": hostname, "takt_version": "0.9.0",
            "runner": "claude", "max_workers": 2, "max_cycles": 50,
            "feature_root_id": None,
        }),
        _event(at(0.1), "scheduler_cycle_started", {
            **rid, "cycle_index": 1, "max_workers": 2,
            "feature_root_id": None, "pid": pid,
        }),
        _event(at(4), "bead_completed", {
            **rid, "bead_id": f"{feature_root}-dev-a", "agent_type": "developer",
            "summary": "Implemented module", "created_bead_ids": [],
            "feature_root_id": feature_root, "verdict": "approved",
            "duration_seconds": 214.5, "cost_usd": 0.31, "attempt": 1,
        }),
        _event(at(5), "bead_blocked", {
            **rid, "bead_id": f"{feature_root}-dev-b", "agent_type": "developer",
            "summary": "Blocked on missing API", "feature_root_id": feature_root,
            "duration_seconds": 60.0, "cost_usd": 0.12, "attempt": 1,
        }),
        _event(at(5.5), "bead_deferred", {
            **rid, "bead_id": f"{feature_root}-test", "agent_type": "tester",
            "reason": "dependencies not done", "feature_root_id": feature_root,
        }),
    ]
    if finished:
        events.append(_event(at(6), "scheduler_cycle_completed", {
            **rid, "cycle_index": 1, "started_count": 2, "completed_count": 1,
            "blocked_count": 1, "deferred_count": 1, "pid": pid,
        }))
        events.append(_event(at(6.2), "scheduler_run_finished", {
            **rid, "pid": pid, "exit_reason": "quiescence", "error": None,
            "cycles": 1, "started_count": 2, "completed_count": 1,
            "blocked_count": 1, "correctives_created_count": 0,
            "deferred_count": 1,
            "final_state": {"done": 1, "blocked": 1, "open": 2, "ready": 1},
            "duration_seconds": 372.0, "cost_usd": 0.43,
        }))
    return events


def _build_project(
    path: Path, name: str, variant: str, index: int, now: datetime
) -> FixtureProject:
    takt = path / ".takt"
    beads_dir = takt / "beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (takt / "logs").mkdir(parents=True, exist_ok=True)

    (takt / "config.yaml").write_text(
        yaml.safe_dump({"common": {"default_runner": "claude",
                                   "test_command": "pytest -q",
                                   "commit_bead_state": False}},
                       sort_keys=False),
        encoding="utf-8",
    )

    root = f"B-{_hex8(name)}"
    standalone = f"B-{_hex8(name + '-solo')}"
    beads = [
        _bead(root, "Feature: demo", "planner", "in_progress", now,
              root=root, age_min=120),
        _bead(f"{root}-dev-a", "Implement module", "developer", "done", now,
              root=root, parent=root, age_min=110),
        _bead(f"{root}-dev-b", "Wire API", "developer", "blocked", now,
              root=root, parent=root, age_min=100,
              block_reason="Blocked on missing API"),
        _bead(f"{root}-test", "Test feature", "tester", "open", now,
              root=root, parent=root, deps=[f"{root}-dev-a", f"{root}-dev-b"],
              age_min=90),
        _bead(f"{root}-docs", "Document feature", "documentation", "ready", now,
              root=root, parent=root, deps=[f"{root}-dev-a"], age_min=90),
        _bead(f"{root}-review", "Review feature", "review", "open", now,
              root=root, parent=root,
              deps=[f"{root}-test", f"{root}-docs"], age_min=90),
        _bead(standalone, "Standalone fix", "developer", "handed_off", now,
              root=standalone, age_min=200),
    ]
    for bead in beads:
        if variant == "older":
            bead = _to_older(bead)
        elif variant == "newer":
            bead = _to_newer(bead)
        _write_json(beads_dir / f"{bead['bead_id']}.json", bead)
    if variant == "corrupt":
        (beads_dir / f"B-{_hex8(name + '-bad')}.json").write_text(
            '{"bead_id": "B-broken", "title": ', encoding="utf-8"
        )

    hostname = socket.gethostname()
    old_run = f"SR-{_hex8(name + '-run1')}"
    events = _run_events(old_run, now - timedelta(hours=2), pid=4000 + index,
                         hostname=hostname, feature_root=root, finished=True)
    active_run: str | None = None
    if variant == "full":
        active_run = f"SR-{_hex8(name + '-run2')}"
        start = now - timedelta(minutes=10)
        pid = os.getpid()
        events += _run_events(active_run, start, pid=pid, hostname=hostname,
                              feature_root=root, finished=False)
        _write_json(takt / "runs" / "active" / f"{active_run}.json", {
            "run_id": active_run, "pid": pid, "hostname": hostname,
            "started_at": start.isoformat(), "runner": "claude",
            "max_workers": 2, "feature_root_id": None, "cycle_index": 1,
            "updated_at": (now - timedelta(minutes=1)).isoformat(),
        })
    events.sort(key=lambda e: e["timestamp"])
    (takt / "logs" / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )

    if variant != "no-version":
        version = "0.3.0" if variant == "older" else (
            "99.0.0" if variant == "newer" else "0.9.0")
        _write_json(takt / "version.json", {
            "takt_version": version,
            "last_upgraded_at": (now - timedelta(days=3)).isoformat(),
        })

    return FixtureProject(
        name=name, path=path, tags=("demo", variant), variant=variant,
        feature_root_id=root, active_run_id=active_run,
        bead_ids=tuple(b["bead_id"] for b in beads),
    )


def build_fixture_fleet(
    root: Path, *, count: int = 5, clock: Clock = _utc_now
) -> FixtureFleet:
    """Write ``count`` synthetic projects and a registry file under ``root``.

    Projects live in ``<root>/projects/<name>``; the registry is written to
    ``<root>/fleet.yaml`` in the format ``agent_takt_fleet.registry`` reads
    (one extra entry points at a nonexistent path).
    """
    root = Path(root)
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    projects_dir = root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    projects: list[FixtureProject] = []
    for i in range(count):
        variant = VARIANTS[i % len(VARIANTS)]
        name = f"demo-{i + 1}-{variant}"
        projects.append(_build_project(projects_dir / name, name, variant, i, now))

    missing = FixtureProject(
        name=MISSING_PROJECT_NAME, path=projects_dir / "does-not-exist",
        tags=("demo", "missing"), variant="missing",
    )
    entries = [
        {"name": p.name, "path": str(p.path), "tags": list(p.tags)}
        for p in (*projects, missing)
    ]
    registry_path = root / "fleet.yaml"
    registry_path.write_text(
        yaml.safe_dump({"version": 1, "projects": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return FixtureFleet(root=root, registry_path=registry_path,
                        projects=tuple(projects), now=now, missing=(missing,))


# ---------------------------------------------------------------------------
# Mutation helpers (used by tests and demo mode to simulate live changes)
# ---------------------------------------------------------------------------

def _events_path(project_path: Path) -> Path:
    return Path(project_path) / ".takt" / "logs" / "events.jsonl"


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def append_event(
    project_path: Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Append one JSON event line to the project's events.jsonl; return it."""
    event = _event(_aware(clock()), event_type, dict(payload or {}))
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write((json.dumps(event) + "\n").encode("utf-8"))
    return event


def append_raw(project_path: Path, text: str | bytes) -> None:
    """Append raw text/bytes (no newline added) to events.jsonl.

    Use for partial-line or malformed-line scenarios.
    """
    data = text.encode("utf-8") if isinstance(text, str) else text
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(data)


def truncate_events(project_path: Path) -> None:
    """Truncate events.jsonl to zero length in place (simulates rotation)."""
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb"):
        pass


def set_bead_status(
    project_path: Path,
    bead_id: str,
    status: str,
    *,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Change a bead's status, rewriting its file atomically.

    Unknown fields are preserved.  A ``status_changed`` entry stamped with
    ``clock`` is appended to ``execution_history`` when that list exists.
    Returns the updated bead dict.
    """
    path = Path(project_path) / ".takt" / "beads" / f"{bead_id}.json"
    bead = json.loads(path.read_text(encoding="utf-8"))
    bead["status"] = status
    history = bead.get("execution_history")
    if isinstance(history, list):
        history.append({
            "timestamp": _aware(clock()).isoformat(),
            "event": "status_changed",
            "agent_type": "scheduler",
            "summary": f"Status set to {status}",
            "details": {},
        })
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(bead, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return bead


def add_registry_project(
    registry_path: Path,
    project_path: Path,
    name: str,
    tags: list[str] | tuple[str, ...] = (),
) -> None:
    """Add a project entry to a fleet registry file (created if missing)."""
    registry_path = Path(registry_path)
    data: dict[str, Any] = {"version": 1, "projects": []}
    if registry_path.exists():
        loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    projects = data.setdefault("projects", [])
    projects.append(
        {"name": name, "path": str(project_path), "tags": list(tags)}
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_name(registry_path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, registry_path)


def remove_project_dir(project_path: Path) -> None:
    """Delete a project directory tree (simulates a removed project)."""
    import shutil

    shutil.rmtree(project_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Demo activity driver (``takt-fleet serve --demo``)
# ---------------------------------------------------------------------------

class DemoActivityDriver:
    """Background thread that keeps appending synthetic scheduler activity.

    Each round, on the first ``full`` demo project, it starts a bead, completes
    it, starts another and blocks it, then finishes the run (and opens a new
    one on the next round).  Only the mutation helpers above are used, so the
    driver writes inside the temporary demo directory exclusively.
    """

    def __init__(self, fleet: FixtureFleet, *, interval: float = 3.0) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._target = next((p for p in fleet.projects if p.variant == "full"), None)
        self._round = 0

    def start(self) -> None:
        if self._target is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="demo-activity", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _steps(self) -> list[Callable[[], None]]:
        p = self._target
        assert p is not None
        root = p.feature_root_id
        self._round += 1
        run_id = f"SR-{_hex8(p.name + f'-demo-{self._round}')}"
        pid = os.getpid()
        docs, test = f"{root}-docs", f"{root}-test"
        rid = {"run_id": run_id}

        def run_started() -> None:
            append_event(p.path, "scheduler_run_started", {
                **rid, "pid": pid, "hostname": socket.gethostname(),
                "takt_version": "0.9.0", "runner": "claude", "max_workers": 2,
                "max_cycles": 50, "feature_root_id": None})

        def started(bead_id: str, agent: str) -> Callable[[], None]:
            def step() -> None:
                set_bead_status(p.path, bead_id, "in_progress")
                append_event(p.path, "bead_started", {
                    **rid, "bead_id": bead_id, "agent_type": agent,
                    "title": f"Demo {agent} work", "feature_root_id": root})
            return step

        def completed() -> None:
            set_bead_status(p.path, docs, "done")
            append_event(p.path, "bead_completed", {
                **rid, "bead_id": docs, "agent_type": "documentation",
                "summary": "Demo work finished", "created_bead_ids": [],
                "feature_root_id": root, "verdict": "approved",
                "duration_seconds": 12.0, "cost_usd": 0.05, "attempt": 1})

        def blocked() -> None:
            set_bead_status(p.path, test, "blocked")
            append_event(p.path, "bead_blocked", {
                **rid, "bead_id": test, "agent_type": "tester",
                "summary": "Demo block: tests failing", "feature_root_id": root,
                "duration_seconds": 8.0, "cost_usd": 0.04, "attempt": 1})

        def finished() -> None:
            append_event(p.path, "scheduler_run_finished", {
                **rid, "pid": pid, "exit_reason": "quiescence", "error": None,
                "cycles": 1, "started_count": 2, "completed_count": 1,
                "blocked_count": 1, "correctives_created_count": 0,
                "deferred_count": 0,
                "final_state": {"done": 2, "blocked": 2},
                "duration_seconds": 20.0, "cost_usd": 0.09})
            set_bead_status(p.path, docs, "ready")
            set_bead_status(p.path, test, "open")

        return [run_started, started(docs, "documentation"), completed,
                started(test, "tester"), blocked, finished]

    def _run(self) -> None:
        while not self._stop.is_set():
            for step in self._steps():
                if self._stop.wait(self._interval):
                    return
                try:
                    step()
                except Exception:  # demo data may vanish; never crash the thread
                    pass
