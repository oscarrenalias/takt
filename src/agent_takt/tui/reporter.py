from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..models import Bead
from .state import TuiRuntimeState


class TuiSchedulerReporter:
    """SchedulerReporter that posts events to a Textual app from a worker thread."""

    def __init__(self, app: object, state: TuiRuntimeState) -> None:
        self._app = app
        self._state = state
        self._cycle_header_logged = False
        self._deferred_this_cycle: set[str] = set()

    def _post(self, text: str, *, style: str | None = None) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        if not self._cycle_header_logged:
            self._cycle_header_logged = True
            self._state.deferred_this_cycle.clear()
            header = f"[{ts}] Scheduler cycle starting..."
            self._state.scheduler_log.append(header)
            try:
                self._app.call_from_thread(self._app._append_log_line, header)
            except Exception:
                pass
        line = f"[{ts}] {text}"
        if style:
            line = f"[{style}]{line}[/{style}]"
        self._state.scheduler_log.append(line)
        try:
            self._app.call_from_thread(self._app._append_log_line, line)
        except Exception:
            pass

    def stop(self) -> None:
        pass

    def lease_expired(self, bead_id: str) -> None:
        self._post(f"Lease expired: {bead_id} requeued")

    def bead_started(self, bead: Bead) -> None:
        self._post(f"[{bead.bead_id}] Started {bead.agent_type}: {bead.title}")

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None:
        self._post(f"[{bead.bead_id}] Worktree ready: {worktree_path}")

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None:
        self._post(f"[{bead.bead_id}] Completed")
        for child in created:
            self._post(f"[{bead.bead_id}] Created followup {child.bead_id} ({child.agent_type})")

    def bead_deferred(self, bead: Bead, reason: str) -> None:
        self._post(f"[{bead.bead_id}] Deferred: {reason}", style="dim")
        self._deferred_this_cycle.add(bead.bead_id)
        self._state.deferred_this_cycle = set(self._deferred_this_cycle)

    def bead_blocked(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Blocked: {summary}")

    def bead_failed(self, bead: Bead, summary: str) -> None:
        self._post(f"[{bead.bead_id}] Failed: {summary}")
