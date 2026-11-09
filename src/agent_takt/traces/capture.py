"""Capture agent session transcripts into takt's trace store.

``capture_traces`` never raises: every per-session failure is turned into a
``status=error`` record where possible and reported back as a warning string.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import (
    ROLE_MAIN,
    ROLE_PLANNER,
    STATUS_CAPTURED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_TOO_LARGE,
    STATUS_UNSUPPORTED,
    TraceSession,
)
from .normalize import NormalizeResult, count_tool_calls, normalize_claude_transcript
from .store import TraceStore

BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"


@dataclass
class TraceContext:
    """Identity of the run being captured (bead-less for planner runs)."""

    bead_id: str | None = None
    agent_type: str | None = None
    feature_root_id: str | None = None
    spec_id: str | None = None


def _context_from(obj: Any) -> TraceContext:
    if isinstance(obj, TraceContext):
        return obj
    return TraceContext(
        bead_id=getattr(obj, "bead_id", None),
        agent_type=getattr(obj, "agent_type", None),
        feature_root_id=getattr(obj, "feature_root_id", None),
        spec_id=getattr(obj, "spec_id", None),
    )


def locate_claude_transcript(
    session_id: str, env: Mapping[str, str] | None = None
) -> Path | None:
    """Find ``<config>/projects/*/<session_id>.jsonl`` or return None."""
    if not session_id or any(c in session_id for c in ("/", "\\", "\x00")) or session_id in (".", ".."):
        return None
    config_dir = (env or {}).get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    pattern = os.path.join(glob.escape(str(base)), "projects", "*", glob.escape(session_id) + ".jsonl")
    for match in sorted(glob.glob(pattern)):
        path = Path(match)
        if path.is_file():
            return path
    return None


def _normalize_file(path: Path) -> NormalizeResult:
    with path.open("rb") as handle:
        return normalize_claude_transcript(handle)


def _record_event(storage: Any, ctx: TraceContext, meta: Any) -> None:
    storage.record_event(
        "trace_captured",
        {
            "bead_id": ctx.bead_id,
            "agent_type": ctx.agent_type,
            "feature_root_id": ctx.feature_root_id,
            "seq": meta.seq,
            "role": meta.role,
            "status": meta.status,
            "backend": meta.backend,
            "session_id": meta.session_id,
            "tool_calls": meta.tool_calls,
        },
    )


def _capture_one(
    store: TraceStore,
    ctx: TraceContext,
    session: TraceSession,
    enabled: bool,
    max_bytes: int,
    outcome: str | None,
    telemetry_attempt: int | None,
    env: Mapping[str, str] | None,
) -> Any:
    """Write one record; returns its meta. Raises on failure (caller handles)."""
    fields: dict[str, Any] = dict(
        agent_type=ctx.agent_type,
        feature_root_id=ctx.feature_root_id,
        spec_id=ctx.spec_id,
        backend=session.backend,
        session_id=session.session_id,
        role=session.role,
        started_at=session.started_at,
        finished_at=session.finished_at,
        outcome=outcome,
        telemetry_attempt=telemetry_attempt,
    )
    bead_id = ctx.bead_id
    if not enabled:
        return store.new_record(bead_id, status=STATUS_DISABLED, **fields)
    if session.backend != BACKEND_CLAUDE or not session.session_id:
        return store.new_record(bead_id, status=STATUS_UNSUPPORTED, **fields)
    source = locate_claude_transcript(session.session_id, env)
    if source is None:
        return store.new_record(bead_id, status=STATUS_MISSING, **fields)

    size = source.stat().st_size
    meta = store.new_record(bead_id, status=STATUS_ERROR, source_path=str(source), **fields)
    try:
        if size > max_bytes:
            result = _normalize_file(source)
            status, raw_bytes = STATUS_TOO_LARGE, size
        else:
            raw = store.write_raw(bead_id, meta.seq, source)
            result = _normalize_file(raw)
            status, raw_bytes = STATUS_CAPTURED, raw.stat().st_size
        store.write_events(bead_id, meta.seq, result.events)
        return store.update_meta(
            bead_id,
            meta.seq,
            status=status,
            raw_bytes=raw_bytes,
            tool_calls=count_tool_calls(result.events),
            malformed_lines=result.malformed_lines,
            error=None,
        )
    except Exception:
        # leave meta as status=error for the caller to annotate
        raise _PartialRecord(meta)  # noqa: B904


class _PartialRecord(Exception):
    def __init__(self, meta: Any) -> None:
        super().__init__("partial trace record")
        self.meta = meta


def capture_traces(
    root: Path | str,
    config: Any,
    bead_or_context: Any,
    sessions: list[TraceSession] | None,
    outcome: str | None,
    telemetry_attempt: int | None,
    backend_name: str | None = None,
    *,
    warnings: list[str] | None = None,
    storage: Any = None,
    env: Mapping[str, str] | None = None,
) -> list[int]:
    """Write one trace record per session and return their seq numbers.

    Failures never propagate; human-readable messages are appended to
    *warnings* when a list is supplied. *storage* (a ``RepositoryStorage``)
    is used for the ``trace_captured`` event; one is built from *root* if absent.
    """
    seqs: list[int] = []
    warn = warnings if warnings is not None else []
    try:
        ctx = _context_from(bead_or_context)
        session_list = list(sessions or [])
        if not session_list:
            if backend_name != BACKEND_CODEX:
                return seqs
            session_list = [TraceSession(backend=BACKEND_CODEX, session_id=None, role=ROLE_MAIN)]
        common = getattr(config, "common", config)
        enabled = bool(getattr(common, "capture_traces", True))
        max_bytes = int(getattr(common, "trace_max_bytes", 52428800))
        store = TraceStore(root)
        if storage is None:
            try:
                from ..storage import RepositoryStorage

                storage = RepositoryStorage(Path(root), commit_bead_state=False)
            except Exception as exc:  # noqa: BLE001
                warn.append(f"trace capture: cannot open event log: {exc}")
    except Exception as exc:  # noqa: BLE001
        warn.append(f"trace capture failed: {exc}")
        return seqs

    for session in session_list:
        label = session.session_id or session.backend
        meta = None
        try:
            try:
                meta = _capture_one(
                    store, ctx, session, enabled, max_bytes, outcome, telemetry_attempt, env
                )
            except _PartialRecord as partial:
                meta = partial.meta
                cause = partial.__context__
                raise (cause or RuntimeError("capture failed"))
        except Exception as exc:  # noqa: BLE001
            msg = f"trace capture failed for session {label}: {exc}"
            warn.append(msg)
            try:
                if meta is not None:
                    meta = store.update_meta(
                        ctx.bead_id, meta.seq, status=STATUS_ERROR, error=str(exc)
                    )
                else:
                    meta = store.new_record(
                        ctx.bead_id,
                        status=STATUS_ERROR,
                        error=str(exc),
                        agent_type=ctx.agent_type,
                        feature_root_id=ctx.feature_root_id,
                        spec_id=ctx.spec_id,
                        backend=session.backend,
                        session_id=session.session_id,
                        role=session.role,
                        outcome=outcome,
                        telemetry_attempt=telemetry_attempt,
                    )
            except Exception as exc2:  # noqa: BLE001
                warn.append(f"trace capture: could not record error for {label}: {exc2}")
                meta = None
        if meta is None:
            continue
        seqs.append(meta.seq)
        if storage is not None:
            try:
                _record_event(storage, ctx, meta)
            except Exception as exc:  # noqa: BLE001
                warn.append(f"trace capture: event not recorded for {label}: {exc}")
    return seqs


def capture_planner_traces(
    root: Path | str,
    config: Any,
    sessions: list[TraceSession] | None,
    outcome: str | None,
    spec_id: str | None = None,
    feature_root_id: str | None = None,
    *,
    warnings: list[str] | None = None,
    storage: Any = None,
    env: Mapping[str, str] | None = None,
) -> list[int]:
    """Planner variant: bead_id None, stored under ``_planner``, role planner."""
    planned = [
        TraceSession(
            backend=s.backend,
            session_id=s.session_id,
            role=ROLE_PLANNER,
            started_at=s.started_at,
            finished_at=s.finished_at,
        )
        for s in (sessions or [])
    ]
    ctx = TraceContext(bead_id=None, agent_type="planner", feature_root_id=feature_root_id, spec_id=spec_id)
    return capture_traces(
        root, config, ctx, planned, outcome, None,
        warnings=warnings, storage=storage, env=env,
    )
