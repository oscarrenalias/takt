from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence


def _col_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    return widths


def format_table(
    headers: list[str],
    rows: list[list[str]],
    plain: bool = False,
) -> str:
    """Render a table with fixed-width columns.

    In plain mode, output is tab-separated (header + rows) without a separator
    line — suitable for piping to awk/cut.
    """
    if not rows:
        return ""

    widths = _col_widths(headers, rows)
    sep = "  "

    def fmt_row_padded(cells: list[str]) -> str:
        return sep.join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines: list[str] = []
    if plain:
        lines.append("\t".join(headers))
        for row in rows:
            lines.append("\t".join(row))
    else:
        lines.append(fmt_row_padded(headers))
        for row in rows:
            lines.append(fmt_row_padded(row))
        lines.append(sep.join("-" * max(w, 3) for w in widths))

    return "\n".join(lines)


def format_project_list(
    projects: Sequence,
    health_map: dict[str, str],
    plain: bool = False,
) -> str:
    """Render the `takt-fleet list` table.

    `projects` is a sequence of `Project` instances.
    `health_map` maps project name → health string.
    """
    headers = ["NAME", "PATH", "TAGS", "HEALTH"]
    rows = [
        [
            p.name,
            str(p.path),
            ",".join(p.tags) if p.tags else "",
            health_map.get(p.name, "unknown"),
        ]
        for p in projects
    ]
    return format_table(headers, rows, plain=plain)


def format_fleet_summary(
    rows: list[dict[str, Any]],
    plain: bool = False,
) -> str:
    """Render the `takt-fleet summary` table.

    Each dict in rows must have:
      name: str
      health: str
      counts: dict | None  — keys: open, ready, in_progress, blocked, done, handed_off

    Rows with counts=None are rendered with "-" placeholders.
    """
    headers = ["PROJECT", "DONE", "READY", "IN_PROGRESS", "BLOCKED", "HANDED_OFF", "HEALTH"]
    table_rows: list[list[str]] = []
    for row in rows:
        counts = row.get("counts")
        health = row.get("health", "error")
        if counts is None:
            table_rows.append([row["name"], "-", "-", "-", "-", "-", health])
        else:
            table_rows.append([
                row["name"],
                str(counts.get("done", 0)),
                str(counts.get("ready", 0)),
                str(counts.get("in_progress", 0)),
                str(counts.get("blocked", 0)),
                str(counts.get("handed_off", 0)),
                health,
            ])
    return format_table(headers, table_rows, plain=plain)


# ── Run and dispatch summaries ────────────────────────────────────────────────


def format_run_summary(run: Any) -> str:
    """Render a post-run result table with per-project takt run statistics."""
    headers = ["PROJECT", "STATUS", "DONE", "BLOCKED", "ERROR"]
    rows: list[list[str]] = []
    for p in run.projects:
        run_summary = (p.outputs or {}).get("run_summary") or {}
        final_state = run_summary.get("final_state") or {}
        done_count = str(final_state.get("done", 0)) if final_state else "-"
        blocked_count = str(final_state.get("blocked", 0)) if final_state else "-"
        error_str = p.error or ""
        rows.append([p.name, p.status, done_count, blocked_count, error_str])

    agg = run.aggregate
    summary_line = (
        f"{agg['succeeded']} succeeded, {agg['failed']} failed"
        f" (total {agg['total']})"
    )
    return format_table(headers, rows) + "\n\n" + summary_line


def format_dispatch_summary(run: Any) -> str:
    """Render a post-dispatch result table with per-project bead IDs.

    Intended for direct stdout output immediately after a dispatch completes.
    """
    headers = ["PROJECT", "STATUS", "BEAD", "ERROR"]
    rows: list[list[str]] = []
    for p in run.projects:
        created = (p.outputs or {}).get("created_beads") or []
        bead_str = ", ".join(created) if created else "-"
        error_str = p.error or ""
        rows.append([p.name, p.status, bead_str, error_str])

    agg = run.aggregate
    summary_line = (
        f"{agg['succeeded']} succeeded, {agg['failed']} failed"
        f" (total {agg['total']})"
    )
    return format_table(headers, rows) + "\n\n" + summary_line


# ── Run log formatters ────────────────────────────────────────────────────────


def _format_duration_secs(seconds: float) -> str:
    """Format a duration in seconds as 'Nm NNs'."""
    total = int(seconds)
    mins = total // 60
    secs = total % 60
    return f"{mins}m {secs:02d}s"


def _run_duration_secs(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    """Return duration in seconds between two datetimes, or None."""
    if started_at is None or finished_at is None:
        return None
    s = started_at.astimezone(timezone.utc)
    f = finished_at.astimezone(timezone.utc)
    return max(0.0, (f - s).total_seconds())


def format_runs_list(runs: Sequence, plain: bool = False) -> str:
    """Render the `takt-fleet runs list` table.

    Each element in `runs` must be a FleetRun instance.  Imports FleetRun at
    call time to avoid a circular import at module load.
    """
    from .runlog import compute_run_status

    headers = ["RUN_ID", "STARTED", "CMD", "PROJECTS", "SUCCEEDED", "FAILED", "DURATION", "STATUS"]
    rows: list[list[str]] = []
    for run in runs:
        agg = run.aggregate
        started = (
            run.started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if run.started_at
            else "-"
        )
        dur_secs = _run_duration_secs(run.started_at, run.finished_at)
        duration = _format_duration_secs(dur_secs) if dur_secs is not None else "-"
        rows.append([
            run.run_id,
            started,
            run.command,
            str(agg["total"]),
            str(agg["succeeded"]),
            str(agg["failed"]),
            duration,
            compute_run_status(run),
        ])
    return format_table(headers, rows, plain=plain)


def format_run_show(run: Any) -> str:
    """Render a detailed view of a completed fleet run."""
    from .runlog import compute_run_status

    lines: list[str] = []

    status = compute_run_status(run)
    crashed_tag = "  [CRASHED]" if run.crashed else ""
    lines.append(f"Fleet Run {run.run_id}{crashed_tag}")

    dur_secs = _run_duration_secs(run.started_at, run.finished_at)
    started_str = (
        run.started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if run.started_at
        else "-"
    )
    lines.append(f"  Command:    {run.command}")
    lines.append(f"  Started:    {started_str}")
    if dur_secs is not None:
        lines.append(f"  Duration:   {_format_duration_secs(dur_secs)}")

    # Inputs section
    if run.inputs.bead or run.inputs.tag_filter or run.inputs.project_filter:
        lines.append("  Inputs:")
        if run.inputs.bead:
            bead = run.inputs.bead
            title = bead.get("title", "")
            agent = bead.get("agent_type", "")
            lines.append(f'    bead:     title="{title}", agent={agent}')
        filters: list[str] = []
        if run.inputs.tag_filter:
            filters.append(f"tags={list(run.inputs.tag_filter)}")
        if run.inputs.project_filter:
            filters.append(f"projects={list(run.inputs.project_filter)}")
        if filters:
            lines.append(f"    filters:  {', '.join(filters)}")

    # Projects section
    if run.projects:
        lines.append("  Projects:")
        for p in run.projects:
            glyph = "✓" if p.status in ("success", "skipped") else "✗"
            p_dur = _run_duration_secs(p.started_at, p.finished_at)
            dur_str = f"({_format_duration_secs(p_dur)})" if p_dur is not None else ""

            # bead IDs for dispatch runs
            bead_ids = ""
            if run.command == "dispatch":
                created = (p.outputs or {}).get("created_beads") or []
                bead_ids = "  ".join(created) if created else ""

            parts = [f"    {glyph} {p.name:<20}  {p.status:<8}"]
            if bead_ids:
                parts.append(f"  {bead_ids:<12}")
            else:
                parts.append("  " + " " * 12)
            parts.append(f"  {dur_str}")
            if p.error:
                parts.append(f"  {p.error}")
            lines.append("".join(parts))

    # Aggregate
    agg = run.aggregate
    lines.append(
        f"  Aggregate: {agg['succeeded']} succeeded, "
        f"{agg['failed']} failed, "
        f"{agg['skipped']} skipped "
        f"(total {agg['total']})"
    )

    return "\n".join(lines)


def format_run_show_header(run: Any) -> str:
    """Render the in-progress header for a live-tailing runs show."""
    started_str = (
        run.started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if run.started_at
        else "-"
    )
    lines = [
        f"Fleet Run {run.run_id}  (in progress)",
        f"  Command:    {run.command}",
        f"  Started:    {started_str}",
        "",
    ]
    return "\n".join(lines)


def format_project_result_line(run: Any, project: Any) -> str:
    """Render a single project result line for live-tailing output."""
    glyph = "✓" if project.status in ("success", "skipped") else "✗"
    p_dur = _run_duration_secs(project.started_at, project.finished_at)
    dur_str = f"({_format_duration_secs(p_dur)})" if p_dur is not None else ""
    parts = [f"  {glyph} {project.name:<20}  {project.status:<8}  {dur_str}"]
    if project.error:
        parts.append(f"  {project.error}")
    return "".join(parts)


def format_run_aggregate_line(run: Any) -> str:
    """Render the final aggregate line for a completed live-tailing session."""
    agg = run.aggregate
    dur_secs = _run_duration_secs(run.started_at, run.finished_at)
    dur_part = f"  — finished in {_format_duration_secs(dur_secs)}" if dur_secs is not None else ""
    return (
        f"\n  Aggregate: {agg['succeeded']} succeeded, "
        f"{agg['failed']} failed, "
        f"{agg['skipped']} skipped "
        f"(total {agg['total']}){dur_part}"
    )


# ── Watch event formatter ─────────────────────────────────────────────────────


def format_watch_event_line(event: Any) -> str:
    """Render a TailedEvent as a project-prefixed line for watch output.

    Parsed events show:  [project]  TIMESTAMP  event_type  summary
    Unparseable lines show the raw text after the project prefix.
    """
    prefix = f"[{event.project_name}]"

    if event.parsed is not None:
        ts_str = ""
        if event.timestamp is not None:
            ts_str = event.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        event_type = event.parsed.get("event", "")
        summary = event.parsed.get("summary", "")

        parts: list[str] = [prefix]
        if ts_str:
            parts.append(ts_str)
        if event_type:
            parts.append(f"{event_type:<20}")
        if summary:
            parts.append(summary)
        return "  ".join(parts)

    return f"{prefix}  {event.raw_line}"
