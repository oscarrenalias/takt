from __future__ import annotations

from datetime import datetime, timezone

from .models import Bead


def bead_wall_clock_seconds(bead: Bead) -> float | None:
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


def bead_cost_usd(bead: Bead) -> float | None:
    """Return cost_usd for a bead from its lightweight telemetry metadata."""
    tel = bead.metadata.get("telemetry") if bead.metadata else None
    if tel is None:
        return None
    cost = tel.get("cost_usd")
    return float(cost) if cost is not None else None
