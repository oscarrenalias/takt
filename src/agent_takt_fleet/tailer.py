"""Event tailer: poll .takt/logs/events.jsonl with optional replay window.

One polling worker thread per project writes TailedEvent objects into a
shared output queue; the caller drains the queue and prints them.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .runlog import _parse_duration

_LOG = logging.getLogger(__name__)

EVENTS_JSONL = ".takt/logs/events.jsonl"
_POLL_INTERVAL = 1.0


@dataclass
class TailedEvent:
    project_name: str
    raw_line: str
    parsed: dict | None
    timestamp: datetime | None


# ── Helpers ────────────────────────────────────────────────────────────────────


def _parse_timestamp(parsed: dict | None) -> datetime | None:
    if parsed is None:
        return None
    ts = parsed.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None


def _emit_line(
    project_name: str,
    raw_line: str,
    out_queue: "queue.Queue[TailedEvent | None]",
) -> None:
    """Parse raw_line and push a TailedEvent into out_queue."""
    stripped = raw_line.rstrip("\n")
    if not stripped:
        return
    try:
        parsed: dict | None = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    ts = _parse_timestamp(parsed)
    out_queue.put(TailedEvent(
        project_name=project_name,
        raw_line=stripped,
        parsed=parsed,
        timestamp=ts,
    ))


# ── Replay window ──────────────────────────────────────────────────────────────


def _replay_window(
    project_name: str,
    f,
    cutoff: datetime,
    out_queue: "queue.Queue[TailedEvent | None]",
    stop_event: threading.Event,
) -> None:
    """Read f from current position to EOF, emit events on or after cutoff.

    Emits a stderr warning when the file's oldest event is newer than cutoff
    (i.e., requested history window is longer than what's available).
    Leaves f positioned at EOF so the caller can continue live-tailing.
    """
    import sys

    first_ts: datetime | None = None
    pending: list[str] = []

    for raw_line in f:
        if stop_event.is_set():
            return
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            parsed: dict | None = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None

        ts = _parse_timestamp(parsed)

        if first_ts is None and ts is not None:
            first_ts = ts

        if ts is None:
            # Unparseable timestamp: include best-effort
            pending.append(stripped)
            continue

        ts_utc = ts.astimezone(timezone.utc)
        if ts_utc >= cutoff:
            pending.append(stripped)

    if first_ts is not None and first_ts.astimezone(timezone.utc) > cutoff:
        first_str = first_ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(
            f"[{project_name}] warning: event log only goes back to "
            f"{first_str} (requested window starts earlier)",
            file=sys.stderr,
            flush=True,
        )

    for line in pending:
        if stop_event.is_set():
            return
        _emit_line(project_name, line, out_queue)


# ── Core tail loop ─────────────────────────────────────────────────────────────


def _do_tail(
    project_name: str,
    events_path: Path,
    since: str | None,
    out_queue: "queue.Queue[TailedEvent | None]",
    stop_event: threading.Event,
) -> None:
    """Block until stop_event, polling events_path and emitting new events."""
    while not events_path.exists() and not stop_event.is_set():
        time.sleep(_POLL_INTERVAL)

    if stop_event.is_set():
        return

    cutoff: datetime | None = None
    if since is not None:
        try:
            delta = _parse_duration(since)
            cutoff = datetime.now(tz=timezone.utc) - delta
        except ValueError as exc:
            _LOG.warning(
                "tailer[%s]: invalid --since value %r: %s", project_name, since, exc
            )

    with events_path.open("r", encoding="utf-8", errors="replace") as f:
        if cutoff is not None:
            _replay_window(project_name, f, cutoff, out_queue, stop_event)
        else:
            f.seek(0, 2)  # jump to EOF for live-only mode

        while not stop_event.is_set():
            line = f.readline()
            if not line:
                time.sleep(_POLL_INTERVAL)
                continue
            _emit_line(project_name, line, out_queue)


def _tail_worker(
    project_name: str,
    events_path: Path,
    since: str | None,
    out_queue: "queue.Queue[TailedEvent | None]",
    stop_event: threading.Event,
) -> None:
    """Thread target: run _do_tail and push None sentinel on exit."""
    try:
        _do_tail(project_name, events_path, since, out_queue, stop_event)
    except Exception as exc:
        _LOG.warning("tailer[%s]: unexpected error: %s", project_name, exc)
    finally:
        out_queue.put(None)


# ── Public API ─────────────────────────────────────────────────────────────────


def start_tailing(
    projects: list[tuple[str, Path]],
    since: str | None = None,
    events_file: str = EVENTS_JSONL,
) -> tuple["queue.Queue[TailedEvent | None]", threading.Event, list[threading.Thread]]:
    """Start one polling worker thread per project.

    Returns (merged_queue, stop_event, threads).  The caller must set
    stop_event to shut down workers; daemon threads exit on process exit.
    """
    merged_queue: queue.Queue[TailedEvent | None] = queue.Queue()
    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    for name, project_path in projects:
        events_path = project_path / events_file
        t = threading.Thread(
            target=_tail_worker,
            args=(name, events_path, since, merged_queue, stop_event),
            daemon=True,
            name=f"tailer-{name}",
        )
        t.start()
        threads.append(t)

    return merged_queue, stop_event, threads
