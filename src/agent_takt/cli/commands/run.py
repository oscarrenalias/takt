from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

from ...console import ConsoleReporter, SpinnerPool
from ...models import Bead
from ...scheduler import Scheduler, SchedulerReporter
from ...storage import RepositoryStorage


class CliSchedulerReporter(SchedulerReporter):
    def __init__(self, console: ConsoleReporter, storage: RepositoryStorage | None = None, max_workers: int = 1, verbose: bool = False) -> None:
        self.console = console
        self.storage = storage
        self.max_workers = max_workers
        self.verbose = verbose
        self._spinner = None
        self._pool: SpinnerPool | None = None
        self._lock = threading.Lock()
        self._attempt_snapshots: dict[str, object] = {}
        self._cost_total = 0.0
        self._has_cost = False
        if max_workers > 1:
            self._pool = SpinnerPool(console, max_workers)
            self._pool.start()

    @property
    def run_cost_usd(self) -> float | None:
        with self._lock:
            if not self._has_cost:
                return None
            return round(self._cost_total, 4)

    def _feature_root_id(self, bead: Bead) -> str | None:
        if self.storage is None:
            return None
        return self.storage.feature_root_id_for(bead)

    def _terminal_telemetry(self, bead: Bead) -> dict:
        """Return duration/cost/attempt for this execution, or nulls if telemetry is stale."""
        empty = {"duration_seconds": None, "cost_usd": None, "attempt": None}
        with self._lock:
            snapshot = self._attempt_snapshots.pop(bead.bead_id, None)
        tel = (bead.metadata or {}).get("telemetry")
        if not isinstance(tel, dict) or not tel:
            return empty
        attempt = tel.get("attempt")
        if attempt == snapshot:
            return empty
        duration_ms = tel.get("duration_ms")
        cost = tel.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            cost = None
        if cost is not None:
            with self._lock:
                self._cost_total += cost
                self._has_cost = True
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
            duration_seconds = None
        else:
            duration_seconds = round(duration_ms / 1000, 1)
        return {"duration_seconds": duration_seconds, "cost_usd": cost, "attempt": attempt}

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.stop()

    def lease_expired(self, bead_id: str) -> None:
        self.console.warn(f"Lease expired for {bead_id}; requeued")
        if self.storage is not None:
            self.storage.record_event("lease_expired", {"bead_id": bead_id})

    def bead_started(self, bead: Bead) -> None:
        label = f"{bead.agent_type} {bead.bead_id} · {bead.title}"
        if self._pool is not None:
            self._pool.add(bead.bead_id, label)
        else:
            self._spinner = self.console.spin(label)
            self._spinner.__enter__()
        with self._lock:
            self._attempt_snapshots[bead.bead_id] = ((bead.metadata or {}).get("telemetry") or {}).get("attempt")
        if self.storage is not None:
            self.storage.record_event("bead_started", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "title": bead.title, "feature_root_id": self._feature_root_id(bead)})

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self.console.detail(f"worktree {worktree_path} on {branch_name}")
        if self.storage is not None:
            self.storage.record_event("worktree_ready", {"bead_id": bead.bead_id, "branch_name": branch_name, "worktree_path": str(worktree_path), "feature_root_id": self._feature_root_id(bead)})

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None:
        if self._pool is not None:
            from ...console import GREEN
            self._pool.finish(bead.bead_id, "✓", GREEN, f"{bead.bead_id} completed")
        elif self._spinner:
            self._spinner.success(f"{bead.bead_id} completed")
            self._spinner = None
        self.console.detail(summary)
        handoff = bead.handoff_summary
        structural = sum(
            1 for a in (handoff.assumptions or [])
            if isinstance(a, dict) and a.get("scope") == "structural"
        )
        if structural:
            self.console.detail(f"{structural} structural assumption(s) recorded")
        statuses = [c.get("status") for c in (handoff.criteria_status or []) if isinstance(c, dict)]
        unmet = statuses.count("unmet")
        descoped = statuses.count("descoped")
        if unmet or descoped:
            self.console.detail(f"criteria: {unmet} unmet, {descoped} descoped")
        for child in created:
            self.console.detail(f"created handoff bead {child.bead_id} ({child.agent_type})")
        tel = self._terminal_telemetry(bead)
        if self.storage is not None:
            self.storage.record_event("bead_completed", {
                "bead_id": bead.bead_id,
                "agent_type": bead.agent_type,
                "summary": summary,
                "created_bead_ids": [c.bead_id for c in created],
                "feature_root_id": self._feature_root_id(bead),
                "verdict": handoff.verdict or None,
                **tel,
            })

    def bead_deferred(self, bead: Bead, reason: str) -> None:
        if self.verbose:
            self.console.detail(f"{bead.bead_id} ({bead.title}) deferred: {reason}")
        if self.storage is not None:
            self.storage.record_event("bead_deferred", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "reason": reason, "feature_root_id": self._feature_root_id(bead)})

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from ...console import YELLOW
            self._pool.finish(bead.bead_id, "!", YELLOW, f"{bead.bead_id} blocked")
        elif self._spinner:
            self._spinner.warn(f"{bead.bead_id} blocked")
            self._spinner = None
        self.console.warn(summary)
        tel = self._terminal_telemetry(bead)
        if self.storage is not None:
            self.storage.record_event("bead_blocked", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "summary": summary, "feature_root_id": self._feature_root_id(bead), **tel})

    def bead_failed(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from ...console import RED
            self._pool.finish(bead.bead_id, "✗", RED, f"{bead.bead_id} failed")
        elif self._spinner:
            self._spinner.fail(f"{bead.bead_id} failed")
            self._spinner = None
        self.console.error(summary)
        tel = self._terminal_telemetry(bead)
        if self.storage is not None:
            self.storage.record_event("bead_failed", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "summary": summary, "feature_root_id": self._feature_root_id(bead), **tel})


def command_run(args: argparse.Namespace, scheduler: Scheduler, console: ConsoleReporter) -> int:
    reporter = CliSchedulerReporter(console, scheduler.storage, max_workers=args.max_workers, verbose=getattr(args, "verbose", False))
    # Use dicts keyed by bead ID so each bead appears at most once (last event wins).
    started: dict[str, str] = {}
    completed: dict[str, str] = {}
    blocked: dict[str, str] = {}
    correctives_created: dict[str, str] = {}
    deferred_count = 0
    console.section("Scheduler")
    feature_root_id = None
    if args.feature_root:
        try:
            feature_root_id = scheduler.storage.resolve_bead_id(args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    scope = f", feature_root={feature_root_id}" if feature_root_id else ""
    console.info(f"Starting scheduler loop with max_workers={args.max_workers}{scope}")
    max_cycles = getattr(args, "max_cycles", 50)
    try:
        cycle_index = 1
        while True:
            if cycle_index > 1:
                console.info(f"Cycle {cycle_index}")
            scheduler.storage.record_event("scheduler_cycle_started", {
                "cycle_index": cycle_index,
                "max_workers": args.max_workers,
                "feature_root_id": feature_root_id,
                "pid": os.getpid(),
            })
            result = scheduler.run_once(
                max_workers=args.max_workers,
                feature_root_id=feature_root_id,
                reporter=reporter,
            )
            for bead_id in result.started:
                started[bead_id] = bead_id
            for bead_id in result.completed:
                completed[bead_id] = bead_id
            for bead_id in result.blocked:
                blocked[bead_id] = bead_id
            for bead_id in result.correctives_created:
                correctives_created[bead_id] = bead_id
            deferred_count += len(result.deferred)
            scheduler.storage.record_event("scheduler_cycle_completed", {
                "cycle_index": cycle_index,
                "started_count": len(result.started),
                "completed_count": len(result.completed),
                "blocked_count": len(result.blocked),
                "deferred_count": len(result.deferred),
                "pid": os.getpid(),
            })
            if not result.started:
                break
            if max_cycles > 0 and cycle_index >= max_cycles:
                console.warn(f"Reached max_cycles={max_cycles}; stopping scheduler loop")
                break
            cycle_index += 1
    finally:
        reporter.stop()

    # Build final-state counts from storage.
    all_beads = scheduler.storage.list_beads()
    if feature_root_id:
        all_beads = [b for b in all_beads if b.feature_root_id == feature_root_id]
    final_counts: dict[str, int] = {}
    for bead in all_beads:
        final_counts[bead.status] = final_counts.get(bead.status, 0) + 1

    if not started:
        console.warn("No ready beads to run")
    else:
        console.success(
            f"Cycle summary: started {len(started)}, completed {len(completed)}, "
            f"blocked {len(blocked)}, deferred {deferred_count} (total cycles)"
        )

    done_count = final_counts.get("done", 0)
    blocked_count = final_counts.get("blocked", 0)
    ready_count = final_counts.get("ready", 0)
    console.info(f"Final state: {done_count} done, {blocked_count} blocked, {ready_count} ready")

    summary = {
        "started": sorted(started.keys()),
        "completed": sorted(completed.keys()),
        "blocked": sorted(blocked.keys()),
        "correctives_created": sorted(correctives_created.keys()),
        "deferred_count": deferred_count,
        "final_state": final_counts,
    }
    console.dump_json(summary)
    return 0
