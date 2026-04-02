# Agent Conventions Memory

This file captures implicit patterns that emerged organically from working in this codebase.
It is **not** operator-maintained — it grows from agent experience across beads and features.

**Distinct from CLAUDE.md**: CLAUDE.md contains operator-defined rules and architecture reference.
This file contains conventions that agents discovered in practice and that future agents would benefit
from knowing upfront — things not obvious from reading the code alone.

**Append-only**: Never rewrite or delete existing entries. Add new dated entries at the bottom.

---

## Conventions

## 2026-04-02 — Always prefix commands with uv run

All orchestrator commands must be prefixed with `uv run` — invoking `orchestrator` or `python` directly without `uv run` will fail or use the wrong environment.

## 2026-04-02 — Bead ID formats coexist

Bead IDs use UUID format (`B-{8 hex chars}`); old sequential IDs (`B0001`) still coexist in storage and both formats are valid — do not assume one format.

## 2026-04-02 — Use unittest not pytest

Tests use `unittest`, not pytest — run a specific module with `uv run python -m unittest tests.<module> -v` rather than `uv run python -m unittest discover` to avoid timeout.

## 2026-04-02 — Config changes take effect immediately

The scheduler reads config at invocation time, not at startup — config changes in `.orchestrator/config.yaml` take effect on the next bead without restarting the scheduler.
