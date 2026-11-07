from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ...console import ConsoleReporter, SpinnerPool
from ... import run_registry
from ...models import Bead
from ...scheduler import Scheduler, SchedulerReporter
from ...storage import RepositoryStorage


logger = logging.getLogger(__name__)


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


def _compute_final_state(storage: RepositoryStorage, feature_root_id: str | None) -> dict[str, int]:
    """Count beads by status, scoped to the feature tree when given."""
    beads = storage.list_beads()
    if feature_root_id:
        beads = [b for b in beads if b.feature_root_id == feature_root_id]
    counts: dict[str, int] = {}
    for bead in beads:
        counts[bead.status] = counts.get(bead.status, 0) + 1
    return counts


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def command_run(args: argparse.Namespace, scheduler: Scheduler, console: ConsoleReporter) -> int:
    reporter = CliSchedulerReporter(console, scheduler.storage, max_workers=args.max_workers, verbose=getattr(args, "verbose", False))
    storage = scheduler.storage
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
            feature_root_id = storage.resolve_bead_id(args.feature_root)
        except ValueError as exc:
            console.error(str(exc))
            return 1
    scope = f", feature_root={feature_root_id}" if feature_root_id else ""
    console.info(f"Starting scheduler loop with max_workers={args.max_workers}{scope}")
    max_cycles = getattr(args, "max_cycles", 50)

    run_id = "SR-" + uuid.uuid4().hex[:8]
    start_clock = time.monotonic()
    pid = os.getpid()
    cycles = 0
    loop_exit: str | None = None
    caught: BaseException | None = None
    terminated = {"flag": False}
    previous_handler = None
    handler_installed = False
    context_set = False

    try:
        run_registry.reap_stale_runs(storage, run_id)
        storage.set_event_context(run_id=run_id)
        context_set = True
        try:
            takt_version = importlib.metadata.version("agent-takt")
        except Exception:
            takt_version = None
        hostname = socket.gethostname()
        runner_name = getattr(scheduler, "runner_backend", None)
        storage.record_event("scheduler_run_started", {
            "run_id": run_id,
            "pid": pid,
            "hostname": hostname,
            "takt_version": takt_version,
            "runner": runner_name,
            "max_workers": args.max_workers,
            "max_cycles": max_cycles,
            "feature_root_id": feature_root_id,
        })
        now = _utc_iso()
        run_registry.write_active_marker(storage.root, {
            "run_id": run_id,
            "pid": pid,
            "hostname": hostname,
            "started_at": now,
            "runner": runner_name,
            "max_workers": args.max_workers,
            "feature_root_id": feature_root_id,
            "cycle_index": 1,
            "updated_at": now,
        })
        if threading.current_thread() is threading.main_thread():
            def _on_sigterm(signum, frame):  # noqa: ARG001
                terminated["flag"] = True
                raise SystemExit(143)
            previous_handler = signal.signal(signal.SIGTERM, _on_sigterm)
            handler_installed = True

        cycle_index = 1
        while True:
            if cycle_index > 1:
                console.info(f"Cycle {cycle_index}")
            cycles = cycle_index
            try:
                run_registry.update_active_marker(storage.root, run_id, cycle_index=cycle_index, updated_at=_utc_iso())
            except Exception:
                logger.warning("Failed to update active marker for %s", run_id, exc_info=True)
            storage.record_event("scheduler_cycle_started", {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "max_workers": args.max_workers,
                "feature_root_id": feature_root_id,
                "pid": pid,
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
            storage.record_event("scheduler_cycle_completed", {
                "run_id": run_id,
                "cycle_index": cycle_index,
                "started_count": len(result.started),
                "completed_count": len(result.completed),
                "blocked_count": len(result.blocked),
                "deferred_count": len(result.deferred),
                "pid": pid,
            })
            if not result.started:
                loop_exit = "quiescence"
                break
            if max_cycles > 0 and cycle_index >= max_cycles:
                console.warn(f"Reached max_cycles={max_cycles}; stopping scheduler loop")
                loop_exit = "max_cycles"
                break
            cycle_index += 1
    except BaseException as exc:
        caught = exc
        raise
    finally:
        if terminated["flag"]:
            exit_reason = "terminated"
        elif isinstance(caught, KeyboardInterrupt):
            exit_reason = "interrupted"
        elif caught is not None:
            exit_reason = "error"
        else:
            exit_reason = loop_exit or "error"
        error_text = None
        if caught is not None and not terminated["flag"]:
            error_text = f"{type(caught).__name__}: {caught}"[:500]
        if context_set:
            try:
                try:
                    final_state = _compute_final_state(storage, feature_root_id)
                except Exception:
                    logger.warning("Failed to compute final_state for %s", run_id, exc_info=True)
                    final_state = None
                try:
                    cost_usd = reporter.run_cost_usd
                except Exception:
                    cost_usd = None
                storage.record_event("scheduler_run_finished", {
                    "run_id": run_id,
                    "pid": pid,
                    "exit_reason": exit_reason,
                    "error": error_text,
                    "cycles": cycles,
                    "started_count": len(started),
                    "completed_count": len(completed),
                    "blocked_count": len(blocked),
                    "correctives_created_count": len(correctives_created),
                    "deferred_count": deferred_count,
                    "final_state": final_state,
                    "duration_seconds": round(time.monotonic() - start_clock, 1),
                    "cost_usd": cost_usd,
                })
            except Exception:
                logger.warning("Failed to write scheduler_run_finished for %s", run_id, exc_info=True)
        try:
            run_registry.remove_active_marker(storage.root, run_id)
        except Exception:
            logger.warning("Failed to remove active marker for %s", run_id, exc_info=True)
        if handler_installed:
            try:
                signal.signal(signal.SIGTERM, previous_handler if previous_handler is not None else signal.SIG_DFL)
            except Exception:
                logger.warning("Failed to restore SIGTERM handler", exc_info=True)
        try:
            storage.clear_event_context()
        except Exception:
            logger.warning("Failed to clear event context", exc_info=True)
        try:
            reporter.stop()
        except Exception:
            logger.warning("Failed to stop reporter", exc_info=True)

    final_counts = _compute_final_state(storage, feature_root_id)

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
