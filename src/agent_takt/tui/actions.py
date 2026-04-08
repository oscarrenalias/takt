from __future__ import annotations

import io
from argparse import Namespace
from typing import TYPE_CHECKING, Callable

from ..console import ConsoleReporter
from ..models import BEAD_BLOCKED, BEAD_DONE, BEAD_READY
from ..storage import RepositoryStorage

if TYPE_CHECKING:
    from .state import TuiRuntimeState

_STATUS_ACTION_TARGETS = (BEAD_READY, BEAD_BLOCKED, BEAD_DONE)


def request_merge(state: "TuiRuntimeState") -> None:
    state._clear_pending_retry()
    state._clear_pending_status_flow()
    state._clear_pending_merge()
    bead = state.selected_bead()
    bead_id = bead.bead_id if bead is not None else "<id>"
    state.status_message = f"Use CLI to merge: takt merge {bead_id}"


def confirm_merge(
    state: "TuiRuntimeState",
    merge_callable: Callable[[Namespace, RepositoryStorage, ConsoleReporter], int] | None = None,
) -> bool:
    if not state.awaiting_merge_confirmation:
        state.status_message = "No merge pending confirmation."
        return False
    bead_id = state.pending_merge_bead_id
    if bead_id is None:
        state.status_message = "No merge pending confirmation."
        state.awaiting_merge_confirmation = False
        return False
    bead = next((row.bead for row in state.rows if row.bead_id == bead_id), None)
    if bead is None or bead.status != BEAD_DONE:
        state.status_message = f"Merge cancelled for {bead_id}; press m again."
        state.awaiting_merge_confirmation = False
        state.pending_merge_bead_id = None
        return False
    if merge_callable is None:
        from ..cli import command_merge

        merge_callable = command_merge
    console_stream = io.StringIO()
    try:
        exit_code = merge_callable(Namespace(bead_id=bead.bead_id), state.storage, ConsoleReporter(stream=console_stream))
    except SystemExit as exc:
        state._record_action_result(
            f"merge {bead.bead_id}",
            "failed",
            status_message=f"Merge failed for {bead.bead_id}.",
        )
        detail = str(exc.code).strip() if exc.code not in (None, 0) else ""
        state.activity_message = detail or console_stream.getvalue().strip() or "Merge command exited early."
        state.awaiting_merge_confirmation = False
        state.pending_merge_bead_id = None
        return False
    except Exception as exc:
        state._record_action_result(
            f"merge {bead.bead_id}",
            f"failed: {exc}",
            status_message=f"Merge failed for {bead.bead_id}: {exc}",
        )
        state.activity_message = console_stream.getvalue().strip() or "Merge command raised an exception."
        state.awaiting_merge_confirmation = False
        state.pending_merge_bead_id = None
        return False
    state.awaiting_merge_confirmation = False
    state.pending_merge_bead_id = None
    if exit_code != 0:
        state._record_action_result(
            f"merge {bead.bead_id}",
            f"failed ({exit_code})",
            status_message=f"Merge failed for {bead.bead_id}.",
        )
        state.activity_message = console_stream.getvalue().strip() or f"Merge command exited with {exit_code}."
        return False
    state._record_action_result(
        f"merge {bead.bead_id}",
        "success",
        status_message=f"Merged {bead.bead_id}.",
    )
    state.refresh(activity_message=console_stream.getvalue().strip() or f"Merged {bead.bead_id}.")
    return True


def run_scheduler_cycle(state: "TuiRuntimeState", reporter: object | None = None) -> bool:
    """Run a single scheduler cycle. Called from a worker thread when async."""
    if state.scheduler_running:
        state.status_message = "Scheduler cycle already in progress."
        return False
    state.scheduler_running = True
    state._record_action_result(
        "scheduler run",
        "started",
        status_message="Scheduler cycle running...",
    )
    try:
        from . import _make_services  # lazy import: keeps _make_services in tui.__init__ namespace for test patches

        _, scheduler, _ = _make_services(state.storage.root)
        result = scheduler.run_once(
            max_workers=state.max_workers,
            feature_root_id=state.feature_root_id,
            reporter=reporter,
        )
    except Exception as exc:
        state.scheduler_running = False
        state._record_action_result(
            "scheduler run",
            f"failed: {exc}",
            status_message=f"Scheduler run failed: {exc}",
        )
        state.refresh(activity_message="Scheduler run raised an exception.")
        return False
    summary_parts = []
    if result.started:
        summary_parts.append(f"started={len(result.started)}")
    if result.completed:
        summary_parts.append(f"completed={len(result.completed)}")
    if result.blocked:
        summary_parts.append(f"blocked={len(result.blocked)}")
    if result.deferred:
        summary_parts.append(f"deferred={len(result.deferred)}")
    result_text = ", ".join(summary_parts) if summary_parts else "no ready beads"
    state.scheduler_running = False
    state._record_action_result(
        "scheduler run",
        "success",
        status_message=f"Cycle done: {result_text}",
    )
    state.refresh(activity_message=f"Cycle: {result_text}")
    return True


def toggle_timed_refresh(state: "TuiRuntimeState") -> None:
    if state.timed_refresh_enabled:
        state.timed_refresh_enabled = False
        state.continuous_run_enabled = False
        phase = "manual"
        status_message = "Timed refresh disabled; manual mode active."
    else:
        state.timed_refresh_enabled = True
        phase = f"refresh/{state.refresh_seconds}s"
        status_message = f"Timed refresh enabled every {state.refresh_seconds}s."
    state._record_action_result(
        "timed refresh",
        phase,
        status_message=status_message,
    )


def toggle_continuous_run(state: "TuiRuntimeState") -> None:
    if state.continuous_run_enabled:
        state.continuous_run_enabled = False
        phase = "disabled"
        status_message = "Timed scheduler disabled; timed refresh remains enabled."
    else:
        state.timed_refresh_enabled = True
        state.continuous_run_enabled = True
        phase = "enabled"
        status_message = f"Timed scheduler enabled every {state.refresh_seconds}s."
    state._record_action_result(
        "continuous run",
        phase,
        status_message=status_message,
    )


def request_retry_selected_blocked_bead(state: "TuiRuntimeState") -> bool:
    state._clear_pending_merge()
    state._clear_pending_status_flow()
    bead = state.selected_bead()
    if bead is None:
        state._record_action_result("retry", "invalid", status_message="No bead selected.")
        state.awaiting_retry_confirmation = False
        state.pending_retry_bead_id = None
        return False
    if bead.status != BEAD_BLOCKED:
        state._record_action_result(
            f"retry {bead.bead_id}",
            "invalid",
            status_message=f"{bead.bead_id} is {bead.status}; only blocked beads can be retried.",
        )
        state.awaiting_retry_confirmation = False
        state.pending_retry_bead_id = None
        return False
    state.awaiting_retry_confirmation = True
    state.pending_retry_bead_id = bead.bead_id
    state.status_message = f"Confirm retry for {bead.bead_id} with y; c cancels."
    return True


def confirm_retry_selected_blocked_bead(state: "TuiRuntimeState") -> bool:
    from ..cli import command_retry

    if not state.awaiting_retry_confirmation:
        state._record_action_result("retry", "invalid", status_message="No retry pending confirmation.")
        return False
    bead_id = state.pending_retry_bead_id
    if bead_id is None:
        state._record_action_result("retry", "invalid", status_message="No retry pending confirmation.")
        state.awaiting_retry_confirmation = False
        return False
    bead = next((row.bead for row in state.rows if row.bead_id == bead_id), None)
    if bead is None or bead.status != BEAD_BLOCKED:
        state._record_action_result(
            f"retry {bead_id}",
            "invalid",
            status_message=f"Retry cancelled for {bead_id}; press t again.",
        )
        state.awaiting_retry_confirmation = False
        state.pending_retry_bead_id = None
        return False
    state.awaiting_retry_confirmation = False
    state.pending_retry_bead_id = None
    console_stream = io.StringIO()
    try:
        exit_code = command_retry(Namespace(bead_id=bead.bead_id), state.storage, ConsoleReporter(stream=console_stream))
    except SystemExit as exc:
        state._record_action_result(
            f"retry {bead.bead_id}",
            "failed",
            status_message=f"Retry failed for {bead.bead_id}.",
        )
        detail = str(exc.code).strip() if exc.code not in (None, 0) else ""
        state.refresh(activity_message=detail or console_stream.getvalue().strip() or "Retry command exited early.")
        return False
    except Exception as exc:
        state._record_action_result(
            f"retry {bead.bead_id}",
            f"failed: {exc}",
            status_message=f"Retry failed for {bead.bead_id}: {exc}",
        )
        state.refresh(activity_message=console_stream.getvalue().strip() or "Retry raised an exception.")
        return False
    result_text = console_stream.getvalue().strip() or f"Retried {bead.bead_id}."
    if exit_code != 0:
        state._record_action_result(
            f"retry {bead.bead_id}",
            f"failed ({exit_code})",
            status_message=f"Retry failed for {bead.bead_id}.",
        )
        state.refresh(activity_message=result_text)
        return False
    state._record_action_result(
        f"retry {bead.bead_id}",
        "success",
        status_message=f"Retried {bead.bead_id}.",
    )
    state.refresh(activity_message=result_text)
    return True


def open_status_update_flow(state: "TuiRuntimeState") -> None:
    state._clear_pending_merge()
    state._clear_pending_retry()
    bead = state.selected_bead()
    if bead is None:
        state._record_action_result("status update", "invalid", status_message="No bead selected.")
        return
    state.status_flow_active = True
    state.pending_status_bead_id = bead.bead_id
    state.pending_status_target = None
    state.status_message = (
        f"Status update for {bead.bead_id}: press r, b, or d, then y to confirm or c to cancel."
    )


def choose_status_target(state: "TuiRuntimeState", target_status: str) -> None:
    bead_id = state.pending_status_bead_id
    if not state.status_flow_active or bead_id is None:
        state.status_message = "Press u before choosing a status update."
        return
    if target_status not in _STATUS_ACTION_TARGETS:
        state.status_message = f"Unsupported status target: {target_status}."
        return
    state.pending_status_target = target_status
    state.status_message = f"Confirm update for {bead_id} -> {target_status} with y; c cancels."


def cancel_pending_action(state: "TuiRuntimeState") -> bool:
    if state.awaiting_merge_confirmation:
        bead_id = state.pending_merge_bead_id or "selected bead"
        state._clear_pending_merge()
        state.status_message = f"Cancelled merge for {bead_id}."
        return True
    if state.awaiting_retry_confirmation:
        bead_id = state.pending_retry_bead_id or "selected bead"
        state._clear_pending_retry()
        state.status_message = f"Cancelled retry for {bead_id}."
        return True
    if state.status_flow_active:
        bead_id = state.pending_status_bead_id or "selected bead"
        state._clear_pending_status_flow()
        state.status_message = f"Cancelled status update for {bead_id}."
        return True
    state.status_message = "No pending action to cancel."
    return False


def confirm_status_update(state: "TuiRuntimeState") -> bool:
    from ..cli import apply_operator_status_update

    bead_id = state.pending_status_bead_id
    target_status = state.pending_status_target
    if not state.status_flow_active or bead_id is None:
        state._record_action_result(
            "status update",
            "invalid",
            status_message="No status update pending confirmation.",
        )
        return False
    if target_status is None:
        state._record_action_result(
            f"status update {bead_id}",
            "invalid",
            status_message=f"Choose ready, blocked, or done for {bead_id} before confirming.",
        )
        return False
    try:
        apply_operator_status_update(state.storage, bead_id, target_status)
    except ValueError as exc:
        state._record_action_result(
            f"status update {bead_id}",
            "invalid",
            status_message=str(exc),
        )
        state._clear_pending_status_flow()
        state.refresh(activity_message=f"No status change applied to {bead_id}.")
        return False
    except Exception as exc:
        state._record_action_result(
            f"status update {bead_id}",
            f"failed: {exc}",
            status_message=f"Status update failed for {bead_id}: {exc}",
        )
        state._clear_pending_status_flow()
        state.refresh(activity_message="Status update raised an exception.")
        return False
    state._record_action_result(
        f"status update {bead_id}",
        f"success -> {target_status}",
        status_message=f"Updated {bead_id} to {target_status}.",
    )
    state._clear_pending_status_flow()
    state.refresh(activity_message=f"Updated {bead_id} to {target_status}.")
    return True
