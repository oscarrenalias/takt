from __future__ import annotations

import argparse
import os
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
        if max_workers > 1:
            self._pool = SpinnerPool(console, max_workers)
            self._pool.start()

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
        if self.storage is not None:
            self.storage.record_event("bead_started", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "title": bead.title})

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self.console.detail(f"worktree {worktree_path} on {branch_name}")
        if self.storage is not None:
            self.storage.record_event("worktree_ready", {"bead_id": bead.bead_id, "branch_name": branch_name, "worktree_path": str(worktree_path)})

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
        if self.storage is not None:
            self.storage.record_event("bead_completed", {
                "bead_id": bead.bead_id,
                "agent_type": bead.agent_type,
                "summary": summary,
                "created_bead_ids": [c.bead_id for c in created],
            })

    def bead_deferred(self, bead: Bead, reason: str) -> None:
        if self.verbose:
            self.console.detail(f"{bead.bead_id} ({bead.title}) deferred: {reason}")
        if self.storage is not None:
            self.storage.record_event("bead_deferred", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "reason": reason})

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from ...console import YELLOW
            self._pool.finish(bead.bead_id, "!", YELLOW, f"{bead.bead_id} blocked")
        elif self._spinner:
            self._spinner.warn(f"{bead.bead_id} blocked")
            self._spinner = None
        self.console.warn(summary)
        if self.storage is not None:
            self.storage.record_event("bead_blocked", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "summary": summary})

    def bead_failed(self, bead: Bead, summary: str) -> None:
        if self._pool is not None:
            from ...console import RED
            self._pool.finish(bead.bead_id, "✗", RED, f"{bead.bead_id} failed")
        elif self._spinner:
            self._spinner.fail(f"{bead.bead_id} failed")
            self._spinner = None
        self.console.error(summary)
        if self.storage is not None:
            self.storage.record_event("bead_failed", {"bead_id": bead.bead_id, "agent_type": bead.agent_type, "summary": summary})


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
