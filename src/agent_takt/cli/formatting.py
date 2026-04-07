from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..models import Bead
from ..storage import RepositoryStorage
from ..console import ConsoleReporter


LIST_PLAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("BEAD_ID", "bead_id"),
    ("STATUS", "status"),
    ("AGENT", "agent_type"),
    ("TYPE", "bead_type"),
    ("TITLE", "title"),
    ("FEATURE_ROOT", "feature_root_id"),
    ("PARENT", "parent_id"),
)


def _plain_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value or "-"
    return str(value)


def format_bead_list_plain(beads: list[Bead]) -> str:
    ordered = sorted(
        beads,
        key=lambda bead: (bead.execution_history[0].timestamp if bead.execution_history else "", bead.bead_id),
    )
    if not ordered:
        return "No beads found."

    rows = [
        [_plain_value(getattr(bead, attribute, None)) for _, attribute in LIST_PLAIN_COLUMNS]
        for bead in ordered
    ]
    widths = [
        max(len(header), max((len(row[column_index]) for row in rows), default=0))
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    ]

    header_line = "  ".join(
        header.ljust(widths[column_index])
        for column_index, (header, _) in enumerate(LIST_PLAIN_COLUMNS)
    )
    row_lines = [
        "  ".join(
            value.ljust(widths[column_index])
            for column_index, value in enumerate(row)
        )
        for row in rows
    ]
    return "\n".join([header_line, *row_lines])


def format_claims_plain(claims: list[dict[str, object]]) -> str:
    if not claims:
        return "No active claims."

    lines: list[str] = []
    for claim in claims:
        lease_owner = "-"
        lease = claim.get("lease")
        if isinstance(lease, dict):
            lease_owner = _plain_value(lease.get("owner"))
        lines.append(
            f"{_plain_value(claim.get('bead_id'))} | "
            f"{_plain_value(claim.get('agent_type'))} | "
            f"feature={_plain_value(claim.get('feature_root_id'))} | "
            f"lease={lease_owner}"
        )
    return "\n".join(lines)


def _filter_beads_by_days(beads: list[Bead], days: int) -> list[Bead]:
    """Return beads whose first execution_history entry falls within the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for bead in beads:
        if not bead.execution_history:
            continue
        ts = bead.execution_history[0].timestamp
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                result.append(bead)
        except ValueError:
            pass
    return result


def _bead_wall_clock_seconds(bead: Bead) -> float | None:
    """Compute total wall-clock seconds from started->completed/blocked/failed pairs.

    Skips incomplete entries (no terminal event after a started event).
    """
    TERMINAL_EVENTS = {"completed", "blocked", "failed"}
    started_ts: str | None = None
    total: float = 0.0
    found_any = False
    for record in bead.execution_history:
        if record.event == "started":
            started_ts = record.timestamp
        elif record.event in TERMINAL_EVENTS and started_ts is not None:
            try:
                start = datetime.fromisoformat(started_ts)
                end = datetime.fromisoformat(record.timestamp)
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                total += (end - start).total_seconds()
                found_any = True
            except ValueError:
                pass
            started_ts = None
    return total if found_any else None


def _bead_turns(storage: RepositoryStorage, bead_id: str) -> int | None:
    """Load the total num_turns from all telemetry artifact files for a bead."""
    bead_telemetry_dir = storage.telemetry_dir / bead_id
    if not bead_telemetry_dir.exists():
        return None
    total = 0
    found_any = False
    for artifact_path in sorted(bead_telemetry_dir.glob("*.json")):
        try:
            data = json.loads(artifact_path.read_text(encoding="utf-8"))
            turns = data.get("metrics", {}).get("num_turns")
            if turns is not None:
                total += int(turns)
                found_any = True
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return total if found_any else None


def _bead_cost_usd(bead: Bead) -> float | None:
    """Return cost_usd for a bead from its lightweight telemetry metadata."""
    tel = bead.metadata.get("telemetry") if bead.metadata else None
    if tel is None:
        return None
    cost = tel.get("cost_usd")
    return float(cost) if cost is not None else None


def _percentile(values: list[float], p: float) -> float | None:
    """Compute the p-th percentile of a sorted list of values (linear interpolation)."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def aggregate_telemetry(
    beads: list[Bead],
    storage: RepositoryStorage,
    transient_patterns: tuple[str, ...] = (),
) -> dict:
    """Compute aggregate telemetry metrics from the given list of beads.

    Returns a structured dict suitable for both table and JSON output modes.
    """
    by_status: Counter[str] = Counter()
    by_agent_type: Counter[str] = Counter()
    wall_clock_values: list[float] = []
    turns_values: list[int] = []
    cost_usd_values: list[float] = []
    retry_count = 0
    corrective_count = 0
    merge_conflict_count = 0
    timeout_block_count = 0
    transient_block_count = 0

    for bead in beads:
        by_status[bead.status] += 1
        by_agent_type[bead.agent_type] += 1

        wc = _bead_wall_clock_seconds(bead)
        if wc is not None:
            wall_clock_values.append(wc)

        turns = _bead_turns(storage, bead.bead_id)
        if turns is not None:
            turns_values.append(turns)

        cost = _bead_cost_usd(bead)
        if cost is not None:
            cost_usd_values.append(cost)

        if bead.retries > 0:
            retry_count += 1

        if bead.bead_id.endswith("-corrective"):
            corrective_count += 1

        if bead.bead_type == "merge-conflict":
            merge_conflict_count += 1

        if bead.status == "blocked":
            reason_lower = (bead.block_reason or "").lower()
            if "timeout" in reason_lower or "timed out" in reason_lower:
                timeout_block_count += 1
            if transient_patterns and any(p in reason_lower for p in transient_patterns):
                transient_block_count += 1

    total = len(beads)
    avg_wc = sum(wall_clock_values) / len(wall_clock_values) if wall_clock_values else None
    p95_wc = _percentile(wall_clock_values, 95)
    avg_turns = sum(turns_values) / len(turns_values) if turns_values else None
    total_cost = sum(cost_usd_values) if cost_usd_values else None
    avg_cost = total_cost / len(cost_usd_values) if cost_usd_values else None

    return {
        "total_beads": total,
        "by_status": dict(by_status),
        "by_agent_type": dict(by_agent_type),
        "avg_wall_clock_seconds": round(avg_wc, 1) if avg_wc is not None else None,
        "p95_wall_clock_seconds": round(p95_wc, 1) if p95_wc is not None else None,
        "avg_turns": round(avg_turns, 1) if avg_turns is not None else None,
        "retry_rate": round(retry_count / total, 3) if total > 0 else None,
        "corrective_bead_count": corrective_count,
        "merge_conflict_bead_count": merge_conflict_count,
        "timeout_block_count": timeout_block_count,
        "transient_block_count": transient_block_count,
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "avg_cost_usd_per_bead": round(avg_cost, 4) if avg_cost is not None else None,
    }


def _format_telemetry_table(data: dict, console: ConsoleReporter, beads: list | None = None) -> None:
    """Render aggregated telemetry as a human-readable plain-text report."""
    filters = data["filters"]
    agg = data["aggregates"]
    lines: list[str] = []

    header = f"Telemetry report  (last {filters['days']} days)"
    if filters.get("feature_root"):
        header += f"  |  feature_root={filters['feature_root']}"
    if filters.get("agent_type"):
        header += f"  |  agent_type={filters['agent_type']}"
    if filters.get("status"):
        header += f"  |  status={filters['status']}"
    lines.append(header)

    if agg["total_beads"] == 0:
        lines.append("No beads found.")
        console.emit("\n".join(lines))
        return

    lines.append(f"Total beads: {agg['total_beads']}")
    lines.append("")

    if agg["by_status"]:
        lines.append("By status:")
        for status, count in sorted(agg["by_status"].items()):
            lines.append(f"  {status:<20} {count}")
        lines.append("")

    if agg["by_agent_type"]:
        lines.append("By agent type:")
        for agent_type, count in sorted(agg["by_agent_type"].items()):
            lines.append(f"  {agent_type:<20} {count}")
        lines.append("")

    feature_roots = data.get("feature_roots") or []
    if feature_roots and not filters.get("feature_root"):
        lines.append("By feature root:")
        for fr in feature_roots:
            frid = fr["feature_root_id"]
            title = fr.get("title") or ""
            truncated = (title[:37] + "...") if len(title) > 40 else title
            count = fr["bead_count"]
            lines.append(f"  {frid}  {truncated:<40}  {count}")
        lines.append("")

    wc_avg = agg["avg_wall_clock_seconds"]
    wc_p95 = agg["p95_wall_clock_seconds"]
    avg_turns = agg["avg_turns"]
    retry_rate = agg["retry_rate"]

    lines.append(f"Avg wall-clock    : {f'{wc_avg}s' if wc_avg is not None else 'N/A'}")
    lines.append(f"P95 wall-clock    : {f'{wc_p95}s' if wc_p95 is not None else 'N/A'}")
    lines.append(f"Avg turns         : {avg_turns if avg_turns is not None else 'N/A'}")
    lines.append(f"Retry rate        : {f'{retry_rate:.1%}' if retry_rate is not None else 'N/A'}")
    lines.append(f"Corrective beads  : {agg['corrective_bead_count']}")
    lines.append(f"Merge-conflict    : {agg['merge_conflict_bead_count']}")
    lines.append(f"Timeout blocks    : {agg['timeout_block_count']}")
    lines.append(f"Transient blocks  : {agg['transient_block_count']}")

    total_cost = agg.get("total_cost_usd")
    avg_cost = agg.get("avg_cost_usd_per_bead")
    lines.append(f"Total cost        : {f'${total_cost:.4f}' if total_cost is not None else 'N/A'}")
    lines.append(f"Avg cost/bead     : {f'${avg_cost:.4f}' if avg_cost is not None else 'N/A'}")

    if beads and filters.get("feature_root"):
        lines.append("")
        lines.append("Per-bead breakdown:")
        col_w = {"bead_id": 20, "agent_type": 14, "status": 12, "duration": 10, "cost_usd": 10}
        header_row = (
            f"  {'bead_id':<{col_w['bead_id']}}"
            f"  {'agent_type':<{col_w['agent_type']}}"
            f"  {'status':<{col_w['status']}}"
            f"  {'duration':>{col_w['duration']}}"
            f"  {'cost_usd':>{col_w['cost_usd']}}"
        )
        sep = "  " + "-" * (sum(col_w.values()) + 2 * (len(col_w) - 1))
        lines.append(header_row)
        lines.append(sep)
        for b in beads:
            wc = _bead_wall_clock_seconds(b)
            cost = _bead_cost_usd(b)
            dur_str = f"{wc:.1f}s" if wc is not None else "-"
            cost_str = f"${cost:.4f}" if cost is not None else "-"
            lines.append(
                f"  {b.bead_id:<{col_w['bead_id']}}"
                f"  {b.agent_type:<{col_w['agent_type']}}"
                f"  {b.status:<{col_w['status']}}"
                f"  {dur_str:>{col_w['duration']}}"
                f"  {cost_str:>{col_w['cost_usd']}}"
            )

    console.emit("\n".join(lines))
