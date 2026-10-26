---
name: "Operator Surfaces for Assumptions, Criteria and Merge Records"
id: spec-c8db74e5
description: "TUI badges, detail sections, event-log lines, run output, audit popup and summary fields for assumptions, criteria and merge records"
dependencies: null
priority: medium
complexity: null
status: draft
tags:
- tui
- cli
- governance
- observability
scope:
  in: null
  out: null
feature_root_id: null
---
# Operator Surfaces for Assumptions, Criteria and Merge Records

## Objective

Show the governance data produced by three other specs in the places operators actually watch: the TUI tree, the detail panel, the dashboard event log, and `takt run` output. The three specs are spec-36cd4ad3 (structured assumptions), spec-e93e36bf (acceptance criteria accounting) and spec-4967f1b0 (feature audit and merge records). On their own they surface mostly through `takt bead show --field`, `takt audit` and the pre-merge summary, so an operator watching a run in the TUI gets no early signal when structural assumptions or descoped criteria pile up. This spec adds read-only rendering: badges, detail sections, event-log lines, a run-output line and an audit popup. It changes no scheduling behaviour.

## Problems to Fix

1. **The TUI tree shows nothing about governance state.** Rows carry status, elapsed, stale, deferred and telemetry badges (`src/agent_takt/tui/render.py:345–352`). A bead with three structural assumptions or an unmet criterion looks the same as a clean one.
2. **The detail panel omits the new fields, and some existing ones.** `_detail_section_body` (`render.py:54`) shows acceptance criteria as plain strings with no status. The Handoff section shows neither `design_decisions`, `test_coverage_notes` nor `known_limitations`, nor the new `assumptions` and `criteria_status`. There's nowhere to see a feature root's merge records.
3. **The dashboard log drops the new events.** `_format_event` (`src/agent_takt/tui/state.py:525`) formats a fixed set of event types and silently skips unknown ones (`state.py:584`). The events added by the other specs would never appear: `structural_assumption_recorded` (spec-36cd4ad3 change 7), `verdict_overridden` and `criteria_gap_recorded` (spec-e93e36bf change 10), and `feature_merged` and `merge_gate_failed` (spec-4967f1b0 change 3).
4. **`takt run` output is silent about them.** `CliSchedulerReporter.bead_completed` (`src/agent_takt/cli/commands/run.py:49`) prints the summary and created children only.
5. **The audit report is CLI-only.** An operator in the TUI has to leave it to run `takt audit`.
6. **`takt summary` lacks assumption and merge state.** spec-e93e36bf adds `criteria` counts to the summary JSON, but not assumptions or merge state.

## Changes

All rendering must work on beads that predate the other specs, and never errors. A loaded bead can't show whether a list was never recorded or was recorded empty (both load as `[]`), so the rules match spec-4967f1b0:

- **Assumptions:** an empty list renders as `None recorded.`. There is no separate "not recorded" state.
- **Criteria:** a bead with `acceptance_criteria` but an empty `criteria_status` is treated as not recorded, and the Acceptance section falls back to the plain list.
- **Badges** are shown only for non-zero counts, so beads with empty lists show no badge.

### 1. TUI tree badges

Add `_governance_badge(bead: Bead) -> str` in `render.py`. Call it in the tree row renderer and append its output after `{deferred}` and before the telemetry `{badge}` in `suffix`. The width budget already accounts for `suffix` length. The output is the concatenation of:

- ` ◆<n>` when the bead has `n ≥ 1` `structural` entries in `handoff_summary.assumptions`;
- ` ✗<n>` when the bead has `n ≥ 1` `unmet` entries in `handoff_summary.criteria_status`;
- ` ~<n>` when the bead has `n ≥ 1` `descoped` entries;
- ` ✓merged` on a feature root bead whose `metadata["merge_records"]` is non-empty.

It returns `""` when none apply. Badges are plain text with no colour markup, like the existing `⊘ deferred` marker. This keeps `fixed_len`, which is computed with `len(suffix)`, accurate.

The badges must also appear in the dual-layout tree, which uses `_telemetry_badge` in `app.py:85`. Use the same helper there.

### 2. Detail panel

In `render.py` and `tui/constants.py`:

- **Acceptance section** (`DETAIL_SECTION_ACCEPTANCE`): when `handoff_summary.criteria_status` is non-empty, render each criterion as `[met] <criterion> — <evidence>` (and likewise `[unmet]` / `[descoped]`), keeping the order. Otherwise render the plain list as today.
- **New section `DETAIL_SECTION_DECISIONS`** ("Decisions & Assumptions"), placed after Handoff in `DETAIL_SECTION_ORDER`:
  - `design_decisions`, `test_coverage_notes` and `known_limitations` (or `-`);
  - then `Assumptions:`, with structural entries first in the form `[structural] <statement> — <rationale>`;
  - `None recorded.` when the bead's `assumptions` list is empty.
- **New section `DETAIL_SECTION_MERGES`** ("Merges"), last in `DETAIL_SECTION_ORDER`:
  - for a feature root with `merge_records`, one block per record, in list order: `merged_at`, `approver`, `branch`, `feature_head_sha` (first 10 characters), `main_sha_after` (first 10 characters), `preflight`, `test_gate.status`, `audit_path`;
  - otherwise `No merge records.`

`format_detail_panel` (`render.py:222`), the single-panel rendering, gains the same Decisions & Assumptions and Merges content as indented blocks, following its existing style.

`n` / `N` section cycling and the `Enter` `DetailPopup` (which iterates `DETAIL_SECTION_ORDER`) both pick up the new sections automatically; no keybinding changes are needed.

### 3. Dashboard event log

Extend `_format_event` (`state.py:525`) with these formats. `<ts>` follows the existing timestamp convention, and every payload value falls back to `?` when missing, the same way `bead_id` does today:

| Event | Line | Style |
|---|---|---|
| `structural_assumption_recorded` | `[<ts>] <bead_id> recorded <count> structural assumption(s): <first>`, where `<count> = payload["count"]` and `<first> = payload["statements"][0]`, truncated to 80 chars (`?` if the list is empty) | `[yellow]` |
| `criteria_gap_recorded` | `[<ts>] <bead_id> criteria gap: <unmet> unmet, <descoped> descoped` (payload counts) | `[yellow]` |
| `verdict_overridden` | `[<ts>] <bead_id> verdict overridden (<reason>): <k> unmet criteria`, with `<k> = len(payload["unmet"])` | `[bold red]` (as `bead_failed`) |
| `feature_merged` | `[<ts>] <feature_root_id> merged by <approver> (tests: <status>)`, with `<status> = payload.get("test_gate", {}).get("status", "?")` | `[green]` (as `bead_completed`) |
| `merge_gate_failed` | `[<ts>] <feature_root_id> merge gate failed (<gate>): <reason>` | `[bold red]` |


### 4. `takt run` output

In `CliSchedulerReporter.bead_completed` (`run.py:49`), after the summary, print one `console.detail` line per condition that applies, reading the just-finalized bead's handoff:

- `<n> structural assumption(s) recorded` when there are any structural assumptions;
- `criteria: <unmet> unmet, <descoped> descoped` when either count is non-zero.

`bead_blocked` needs no change: the override reason is already in the bead's `block_reason`, which reaches `summary`. With no new fields present, nothing extra is printed.

### 5. Audit popup in the TUI

`DetailPopup` (`app.py:133`) takes a `Bead` and renders its sections, so it can't show arbitrary text. Add a `TextPopup(ModalScreen[None])` in `app.py` that takes `title: str, body: str`. It renders `body` in a `VerticalScroll` with the same CSS as `DetailPopup` and closes with `escape`.

Add a keybinding `A` ("Audit") in `app.py`. For the selected bead, it calls `build_feature_audit(storage, feature_root_id)` and `render_feature_audit_markdown` from `src/agent_takt/audit.py` (spec-4967f1b0, change 1; the module is UI-neutral, so the TUI imports nothing from `cli/`). It then shows the result in `TextPopup` with title `Audit: <feature_root_id>`. If the selected bead has no feature root, it shows `No feature tree for this bead.` in the status bar. Add the line `A           Open feature audit popup` to `format_help_overlay()` (`render.py:290`), which builds the help overlay text that `HelpOverlay` displays.

### 6. `takt summary`

`storage.summary(feature_root_id=...)` (`storage.py:647`) gains, when a feature root is given:

- `assumptions`: `{"structural": n, "local": n}` over the tree (zeros when there are none; never `null`, see the rules at the top of Changes);
- `last_merge`: the last element of the feature root's `metadata["merge_records"]` (records are appended in merge order), with the full object from spec-4967f1b0, or `null`.

The `criteria` object from spec-e93e36bf change 9 stays as specified there. Without `--feature-root`, the output is unchanged.

### 7. Documentation

- `docs/tui.md`: badges legend (`◆`, `✗`, `~`, `✓merged`), the new detail sections, the new event-log lines, the `A` binding.
- `CLAUDE.md`: one line in the TUI description pointing to the badges and the audit popup. The layout line for `render.py` mentions the governance badge.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/tui/render.py` | `_governance_badge`; Acceptance section status rendering; Decisions & Assumptions and Merges sections; `format_detail_panel` blocks; `A` line in `format_help_overlay()` |
| `src/agent_takt/tui/constants.py` | `DETAIL_SECTION_DECISIONS`, `DETAIL_SECTION_MERGES`; updated `DETAIL_SECTION_ORDER` |
| `src/agent_takt/tui/state.py` | `_format_event` handles the five new event types |
| `src/agent_takt/tui/app.py` | Badge in dual-layout tree; `TextPopup`; `A` binding |
| `src/agent_takt/tui/actions.py` | `action_open_audit_popup` (or equivalent) |
| `src/agent_takt/tui/__init__.py` | Re-export new public helpers if the module convention requires it |
| `src/agent_takt/cli/commands/run.py` | Extra `console.detail` lines in `bead_completed` |
| `src/agent_takt/storage.py` | `assumptions` and `last_merge` in `summary()` |
| `docs/tui.md`, `CLAUDE.md` | Documentation |
| `tests/test_tui_app.py` | Help overlay contains the `A` line; audit popup content |
| `tests/test_tui_*.py` / TUI tests in `tests/test_orchestrator.py`, `tests/test_cli_run.py`, `tests/test_cli_summary*.py` | Tests per Acceptance Criteria |

## Acceptance Criteria

- `_governance_badge` returns:
  - `""` for a bead with no assumptions, criteria or merge records;
  - ` ◆2` for 2 structural assumptions (local ones are not counted);
  - ` ✗1 ~2` for 1 unmet and 2 descoped criteria;
  - ` ✓merged` for a feature root with one merge record.

  Each case has a unit test.
- The tree row for a bead with badges still fits the panel width: the title is truncated, not the badges. This is tested at a narrow width with a bead showing `◆2 ✗1 ~2`. `_governance_badge` output contains no markup.
- With `criteria_status` present, the Acceptance detail section renders `[met] …`, `[unmet] …`, `[descoped] …` lines in criterion order. Without it, the plain list is rendered as before.
- The Decisions & Assumptions section shows `design_decisions`, `test_coverage_notes`, `known_limitations` and the assumptions, structural first. It shows `None recorded.` for a bead with an empty `assumptions` list.
- The `Enter` `DetailPopup` includes the Decisions & Assumptions and Merges sections.
- The Merges section shows each record's fields for a merged feature root, and `No merge records.` otherwise. `n` / `N` cycling reaches both new sections.
- `_format_event` returns the formatted line, with the markup from change 3, for each of the five new event types. A payload with every key missing renders with `?` placeholders and doesn't raise.
- `takt run` prints `<n> structural assumption(s) recorded` and `criteria: …` detail lines for a completed bead whose handoff has them, and prints neither for a bead without them.
- Pressing `A` on a bead in a feature tree opens `TextPopup`. Its body equals `render_feature_audit_markdown(build_feature_audit(storage, root_id))`. `format_help_overlay()` output contains the `A` line. No module under `src/agent_takt/tui/` imports from `agent_takt.cli`.
- `takt summary --feature-root <id>` JSON includes `assumptions` counts (zeros when none) and `last_merge` (the last record, or `null`). `takt summary` without `--feature-root` is byte-for-byte unchanged on the same storage.
- All rendering works on storage containing only beads that predate spec-36cd4ad3, spec-e93e36bf and spec-4967f1b0. A test loads such beads and renders the tree, every detail section and the summary without errors.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

1. **Glyphs.** `◆` / `✗` / `~` / `✓merged` follow the existing use of `⊘` for deferred. If terminal compatibility is a concern, an ASCII set (`A2`, `U1`, `D2`, `merged`) could be used instead. Recommendation: Unicode, consistent with the existing deferred marker.
2. **Ordering against the other specs.** This spec reads fields and events defined in spec-36cd4ad3, spec-e93e36bf and spec-4967f1b0, and change 5 calls `build_feature_audit` from spec-4967f1b0. Recommendation: plan it after spec-36cd4ad3 and spec-4967f1b0 are done. The criteria parts degrade to nothing until spec-e93e36bf lands, so this spec doesn't need to wait for it.
3. **Fleet roll-up** of the same counts in `takt-fleet summary`. Out of scope.
