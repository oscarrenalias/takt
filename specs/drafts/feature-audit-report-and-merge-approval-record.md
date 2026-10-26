---
name: Feature Audit Report and Merge Approval Record
id: spec-4967f1b0
description: "takt audit per-feature report plus merge approver record, audit snapshot and merge events"
dependencies: null
priority: medium
complexity: null
status: draft
tags:
- cli
- merge
- audit
- governance
scope:
  in: null
  out: null
feature_root_id: null
---
# Feature Audit Report and Merge Approval Record

## Objective

Give operators, and anyone they answer to, one place to see what happened in a feature tree and who signed off on merging it. takt already records most of the trail: `execution_history` on every bead, `.takt/logs/events.jsonl`, telemetry, verdicts and handoffs. It's spread across dozens of bead files and has to be reassembled by hand. Meanwhile `takt merge` records nothing about the merge itself: not who ran it, which commit was merged, whether the test gate ran or was skipped. This spec adds a `takt audit` command that renders a per-feature report (markdown or JSON), and makes `takt merge` print a condensed version, snapshot the full report, and write a merge approval record. Together these answer "what did the agents do, what did they assume, what was left out, and who accepted it on what evidence?" without adding any checkpoint during the run.

## Problems to Fix

1. **No single view of a feature.** Answering "what happened in B-xxxx?" means running `bead list --feature-root`, then `bead show` and `bead history` per bead, then `takt telemetry`. That isn't a reviewable record.
2. **Merges leave no record.** `command_merge` (`src/agent_takt/cli/commands/merge.py:73`) runs the preflight, the test gate and `merge_branch`, then returns `0`. It records no approver, no commit SHAs, no test outcome and no event, and nothing notes when `--skip-tests` or `--skip-rebase` was used.
3. **Skipped gates are invisible afterwards.** A merge with `--skip-tests` looks identical to one that passed the tests.
4. **The structured data from spec-36cd4ad3 (assumptions) and spec-e93e36bf (criteria accounting) has no consumer aimed at operators.** Without a report, those fields are only reachable one bead at a time.

## Changes

### 1. `takt audit <bead_id>`

New command `command_audit` in a new module `src/agent_takt/cli/commands/audit.py`, registered in `cli/parser.py` and dispatched from `cli/__init__.py`. The report builders live in a separate, UI-neutral module, `src/agent_takt/audit.py`, so the TUI (spec-c8db74e5) can use them without importing from `cli/`.

```
takt audit <bead_id> [--json] [--output FILE]
```

- `<bead_id>` accepts any bead in the tree, or a prefix. The report always covers the whole feature tree, using `storage.feature_root_id_for()`.
- The default output is markdown on stdout. `--json` emits the JSON document (change 2). `--output FILE` writes to a file instead of stdout, in whichever format was selected.
- Read-only: it never modifies beads or writes events.

In `src/agent_takt/audit.py`, build the report with `build_feature_audit(storage, feature_root_id) -> dict`, which is used by `takt audit`, `takt merge` and the TUI, and render markdown with `render_feature_audit_markdown(audit: dict) -> str`. Reuse the existing telemetry helpers in `cli/commands/telemetry.py` (`_bead_cost_usd`, `_bead_wall_clock_seconds`), moving them to a shared location if needed, rather than reimplementing them.

### 2. Report content

The JSON document has `audit_schema_version: 1` and these top-level keys. The markdown renders the same sections in the same order:

1. **`feature`**: root bead ID, title, linked docs (the spec), branch name, worktree path, `generated_at` (UTC ISO-8601), takt version.
2. **`status`**: bead counts by status, plus the IDs and titles of beads not in `done`.
3. **`beads`**: one row per bead in creation order, with `bead_id`, `agent_type`, `bead_type`, `status`, `verdict`, `findings_count`, number of corrective children, number of recovery children, wall-clock seconds and cost USD (`null` if unknown).
4. **`gates`**: for each `tester` and `review` bead, the final verdict, the number of `needs_changes` results before the final one (from `execution_history`), and any `verdict_overridden` events (spec-e93e36bf).
5. **`assumptions`**: all `handoff_summary.assumptions` (spec-36cd4ad3), each tagged with its bead ID, structural first, plus counts by scope.
6. **`criteria`**: totals of `met`/`unmet`/`descoped` from `handoff_summary.criteria_status` (spec-e93e36bf), plus a full list of every `unmet` and `descoped` entry with bead ID, criterion and evidence.
7. **`changed_files`**: sorted union of `changed_files` across the tree.
8. **`cost`**: total cost USD, total wall-clock seconds, and the number of beads with telemetry.
9. **`merges`**: the feature root's merge records (change 3), oldest first.

Handling missing data. A loaded bead can't show whether a list field was never recorded or was recorded empty, because both load as `[]`. So:

- **`criteria`** is `null` (markdown: `Not recorded.`) when no bead in the tree that has `acceptance_criteria` also has a non-empty `criteria_status`. Once spec-e93e36bf is live, reconciliation always produces one entry per criterion, so a bead with criteria and an empty status list predates it.
- **`assumptions`** is never `null`. It always reports counts, and the markdown prints `None recorded.` when there are none. An empty list could mean "none" or "predates spec-36cd4ad3", and the wording covers both honestly.
- **`merges`** is `[]` with markdown `No merge records.` when the root has none.
- **`cost`** fields are `null` when no bead has telemetry. This command must work, and be tested, with neither spec-36cd4ad3 nor spec-e93e36bf implemented.

### 3. Merge approval record

In `command_merge`, the full sequence is as follows.

**Step 0: determine the approver**, before the preflight:
- `--approver "Name <email>"` (new flag) takes precedence;
- otherwise use `git config user.name` / `user.email` in the repository root;
- if neither yields a name, exit 1 with `error: cannot determine approver; set git user.name or pass --approver`, before any preflight or test command runs.

**Preflight and test gate** run as today. Record their outcome for the record:
- `preflight`:
  - `"passed"`;
  - `"skipped"` when `--skip-rebase` is given;
  - `"not_run"` when the feature worktree path is empty or missing, which today silently skips the preflight.
- `test_gate`:
  - when the gate ran: `{"status": "passed", "command": "<cmd>", "duration_seconds": <float>}`;
  - with `--skip-tests`: `{"status": "skipped", "command": null, "duration_seconds": null}`;
  - when no `test_command` is configured: `{"status": "not_configured", "command": null, "duration_seconds": null}`.

**After the test gate passes**, in this order:

1. Build the audit with `build_feature_audit`, print the pre-merge summary (change 4), and write the snapshot to `.takt/audits/<feature_root_id>/<UTC timestamp, colons replaced by hyphens>.json`, with a markdown rendering next to it using the same stem. The snapshot's `merges` section contains only **earlier** merge records. The merge it documents points back to the snapshot through `audit_path` and `audit_sha256`, so the snapshot never contains itself. `.takt/audits/` is already covered by the existing `.takt/*` rule in `.gitignore`.
2. Capture `main_sha_before` and `feature_head_sha`, then run `worktrees.merge_branch(branch_name)`.
3. If `merge_branch` raises `GitError`: append a `merge_gate_failed` event with `gate: "merge"`, keep the snapshot on disk (it documents the state that was accepted, and its path appears in the event), write no merge record, and exit 1 with the git error.
4. On success, capture `main_sha_after` and append a record to `feature_root.metadata["merge_records"]` (a list; create it if missing):

   ```json
   {
     "merged_at": "2026-10-26T14:03:11+00:00",
     "approver": "Jane Doe <jane@example.com>",
     "approver_source": "flag | git-config",
     "branch": "feature/b-a7bc3f91",
     "feature_head_sha": "<sha of branch head that was merged>",
     "main_sha_before": "<main HEAD before merge>",
     "main_sha_after": "<main HEAD after merge>",
     "preflight": "passed | skipped | not_run",
     "test_gate": {"status": "passed | skipped | not_configured", "command": "... | null", "duration_seconds": "41.2 | null"},
     "audit_path": ".takt/audits/B-a7bc3f91/2026-10-26T14-03-11Z.json",
     "audit_sha256": "<sha256 of the JSON file>"
   }
   ```

5. Save the feature root through `storage.update_bead(feature_root, event="merged", summary="Merged <branch> into main by <approver>")`, so a `merged` entry appears in `execution_history`. This goes through normal storage on main. With `commit_bead_state: true` it produces the usual `[bead]` commit on main after the merge commit. With `false` it writes the bead file only. Either is expected, and the record is not included in the merge commit itself.
6. Call `storage.record_event("feature_merged", {...})` with the same record plus `feature_root_id`.

**On a preflight or test-gate failure** (conflict, test failure, timeout, failure to start), call `storage.record_event("merge_gate_failed", {"feature_root_id", "gate": "preflight" | "tests", "reason", "approver", "at"})` in addition to today's behaviour. `reason` is a one-line summary (e.g. `"tests failed (exit 1)"`, `"conflict in 2 files"`), not the test output. The full output stays in the merge-conflict bead's description, as today. No snapshot and no merge record are written.

`--skip-tests` and `--skip-rebase` stay allowed. They are recorded, so they no longer leave no trace.

### 4. Pre-merge summary

After the test gate passes and before `merge_branch`, print a condensed summary built from the same audit dict:

```
Merge summary for B-a7bc3f91 "Add rate limiting"
  beads: 7 done, 0 other
  gates: tester approved, review approved (1 prior needs_changes)
  assumptions: 2 structural, 5 local
  criteria: 18 met, 0 unmet, 2 descoped
  test gate: passed (41.2s)    preflight: passed
  approver: Jane Doe <jane@example.com>
```

- Lines whose data is `null` print `not recorded`.
- Lines with `skipped`, `not_run` or `not_configured` gates, `unmet` criteria, structural assumptions or non-done beads are highlighted with the existing console warning style.
- The summary is informational. `takt merge` stays non-interactive and doesn't prompt.

### 5. Documentation

- New `docs/audit.md`: the `takt audit` usage, the report sections, the merge record fields, and where snapshots are stored.
- `CLAUDE.md`, "Merging a Feature": the approver requirement and `--approver`, the merge summary, the merge record and audit snapshot, and the `takt audit` command in the Quick Reference.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/audit.py` (new) | `build_feature_audit`, `render_feature_audit_markdown` (UI-neutral) |
| `src/agent_takt/cli/commands/audit.py` (new) | `command_audit` |
| `src/agent_takt/cli/parser.py`, `src/agent_takt/cli/__init__.py` | Register `audit` command; `--approver` on `merge` |
| `src/agent_takt/cli/commands/merge.py` | Approver resolution; SHA capture; audit snapshot; merge record; `merged` history event; `feature_merged` / `merge_gate_failed` events; pre-merge summary |
| `src/agent_takt/cli/commands/telemetry.py` | Expose cost and wall-clock helpers for reuse (move to a shared module if cleaner) |
| `src/agent_takt/gitutils.py` | Helper(s) to read HEAD SHA of a ref and git user identity, if not already present |
| `docs/audit.md` (new), `CLAUDE.md` | Documentation |
| `tests/test_cli_audit.py` (new), `tests/test_cli_merge.py` | Tests per Acceptance Criteria |

## Acceptance Criteria

- `takt audit <id>` on a feature tree prints markdown with the nine sections of change 2, in order. Passing any bead ID in the tree, or a unique prefix, produces the same report.
- `takt audit <id> --json` emits valid JSON with `audit_schema_version == 1` and the nine top-level keys. `--output FILE` writes the same content to FILE and nothing to stdout.
- `takt audit` modifies no bead file and appends nothing to `events.jsonl`. A test compares bead files and the event log before and after.
- With beads that have acceptance criteria but empty `criteria_status`, `criteria` is `null` and renders as `Not recorded.`. With no assumptions on any bead, `assumptions` has zero counts and renders `None recorded.`. With the fields present (set directly on test beads), counts and listings are correct, and structural assumptions are listed before local ones.
- `gates` reports the count of prior `needs_changes` results for a review bead whose history contains one `needs_changes` before an `approved`.
- `takt merge` with no `--approver` and no git `user.name` exits 1 with the approver error, before any preflight or test command runs.
- A successful `takt merge`:
  - appends exactly one entry to the feature root's `metadata["merge_records"]` with every field in change 3. `main_sha_after` equals main's HEAD after the merge, and `feature_head_sha` equals the merged branch's head;
  - writes `.takt/audits/<feature_root_id>/<stamp>.json` and `.md`, and `audit_sha256` matches the JSON file's SHA-256;
  - adds a `merged` event to the root's `execution_history`;
  - appends a `feature_merged` event to `events.jsonl`.
- `--approver "X <x@y>"` is recorded with `approver_source: "flag"`. Otherwise the git identity is recorded with `approver_source: "git-config"`.
- `takt merge --skip-tests --skip-rebase` records `preflight: "skipped"` and `test_gate.status: "skipped"`, and the pre-merge summary highlights both.
- A failing test gate appends a `merge_gate_failed` event with `gate: "tests"`, writes no merge record, creates no audit snapshot, and keeps today's merge-conflict bead behaviour.
- When `merge_branch` raises `GitError` (simulated in a test), a `merge_gate_failed` event with `gate: "merge"` is appended, no merge record is written, the snapshot file remains, and the command exits 1.
- A merge of a feature whose worktree path is missing records `preflight: "not_run"`, and the summary highlights it.
- With `--skip-tests`, `test_gate` is `{"status": "skipped", "command": null, "duration_seconds": null}`.
- The snapshot's `merges` section doesn't contain the merge record that references it. On a second merge of the same feature root, the second snapshot contains the first record.
- A successful merge works with both `commit_bead_state: true` and `commit_bead_state: false`, and is tested with both.
- The pre-merge summary is printed before the merge step and contains the lines shown in change 4 (with `not recorded` where data is absent). `takt merge` never waits for input.
- Existing `tests/test_cli_merge.py` tests pass, with fixtures updated to configure a git identity where needed.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

1. **Should audit snapshots be committed?** Options:
   - (a) Leave `.takt/audits/` untracked; it is already covered by `.takt/*` in `.gitignore`. This is the v1 recommendation: no change to merge commits, and the snapshot's hash is preserved in the merge record and `events.jsonl`.
   - (b) Commit the snapshot onto the feature branch before `merge_branch`, so it lands in main with the merge. This gives a tamper-evident record in git history, but adds a commit to every merge and interacts with `commit_bead_state: false` repos.

   Revisit (b) if audit evidence needs to live in git for compliance.
2. **Approver identity strength.** Git `user.name`/`user.email` is self-asserted. Recommendation: accept that for v1. The record is about accountability ("who ran the merge"), not authentication. Signed commits or CI identity can come later.
3. **Fleet aggregation.** `takt-fleet` could roll up merge records across projects. Out of scope.
4. **Dependencies.** This spec doesn't strictly depend on spec-36cd4ad3 or spec-e93e36bf (missing criteria data renders as `Not recorded.`, and assumptions render `None recorded.`), but the assumptions and criteria sections are only useful once those land. Recommendation: plan it after spec-36cd4ad3. It can run in parallel with spec-e93e36bf.
