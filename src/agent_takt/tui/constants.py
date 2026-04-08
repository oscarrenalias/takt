from __future__ import annotations

# Detail section identifiers — shared between render.py and state.py.
# Kept here so render.py can import them without depending on state.py.
DETAIL_SECTION_ACCEPTANCE = "acceptance"
DETAIL_SECTION_FILES = "files"
DETAIL_SECTION_HANDOFF = "handoff"
DETAIL_SECTION_TELEMETRY = "telemetry"
DETAIL_SECTION_HISTORY = "history"
DETAIL_SECTION_ORDER = (
    DETAIL_SECTION_ACCEPTANCE,
    DETAIL_SECTION_FILES,
    DETAIL_SECTION_HANDOFF,
    DETAIL_SECTION_TELEMETRY,
    DETAIL_SECTION_HISTORY,
)

EXECUTION_HISTORY_DISPLAY_LIMIT = 5


def _format_duration_ms(ms: float | int | None) -> str:
    """Format milliseconds as m:ss."""
    if ms is None:
        return "-"
    total_seconds = int(ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def _format_block(values: list[str]) -> list[str]:
    if not values:
        return ["  -"]
    return [f"  - {value}" for value in values]


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "-"


def _value_or_dash(value: str | None) -> str:
    return value if value else "-"
