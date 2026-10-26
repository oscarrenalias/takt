---
name: Acceptance Criteria Accounting and Scope-Loss Detection
id: spec-e93e36bf
description: "Per-criterion met/unmet/descoped accounting, verdict override on unmet criteria, scenario test-citation and plan-time coverage checks"
dependencies: null
priority: high
complexity: null
status: draft
tags:
- handoff
- schema
- finalize
- planner
- tester
- governance
scope:
  in: null
  out: null
feature_root_id: null
---
# Acceptance Criteria Accounting and Scope-Loss Detection

## Objective

Stop work from going missing between the spec and the merge. Today a bead's `acceptance_criteria` are a list of strings that agents are asked to satisfy, but no agent reports against them item by item, and nothing checks. A developer can quietly skip one, a tester can skip whole categories of behaviour, and a reviewer approving the code that exists won't notice something that was never built. This spec makes every worker account for each acceptance criterion as `met`, `unmet` or `descoped` with evidence, and has the finalizer enforce that a verdict can't be `approved` while criteria are unmet. When a spec uses the structured `## Requirements` section from spec-ea87bef7 (Living Specs and Requirement Deltas), the spec's scenarios are checked twice: once at plan time to confirm each is assigned to a tester bead, and once at finalize to confirm each one cites a test that exists. The concepts come from the "feature loss" check and the mandatory `what-is-not-done` record in an internal Accenture SDD framework (myArchitecture4code). Here they are enforced by the scheduler rather than by prompt instructions.

## Problems to Fix

1. **Nobody reports against acceptance criteria individually.** `AGENT_OUTPUT_SCHEMA` (`src/agent_takt/runner.py:131`) has `completed` and `remaining` as free-form strings. There's no per-criterion status, so "which of the 6 criteria were actually met?" can't be answered without reading prose.
2. **Approval doesn't depend on criteria.** `_apply_review_test_verdict` (`src/agent_takt/scheduler/finalize.py:467`) trusts the `verdict` field alone. A reviewer or tester can return `approved` while its own `remaining` text lists unmet criteria.
3. **Lost scope disappears.** If a developer bead completes with a criterion silently dropped, the shared review bead sees only `design_decisions` from dependencies (`prompts.py:153`). Nothing carries "these criteria were not met" forward, and the feature can merge without it.
4. **Test coverage claims aren't checked.** The tester can claim a behaviour is covered without the cited test existing. Nothing verifies even that the test file and test name are real.
5. **Plans can drop requirements.** When a spec has structured requirement scenarios (spec-ea87bef7, change 5), the planner is asked to copy them into the shared tester bead, but nothing checks it did. A missing scenario is lost before any agent runs.

## Changes

### 1. Output schema: `criteria_status`

Add to `AGENT_OUTPUT_SCHEMA` in both `src/agent_takt/runner.py:131` and `src/agent_takt/prompts.py:15`, and to each schema's `required` list:

```python
"criteria_status": {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "status": {"type": "string", "enum": ["met", "unmet", "descoped"]},
            "evidence": {"type": "string"},
        },
        "required": ["index", "status", "evidence"],
    },
},
```

- `index` is the 0-based position in the bead's `acceptance_criteria`. The criterion text is not repeated; the bead's list is authoritative.
- `evidence`:
  - for `met`: where it was satisfied (file and function, test ID, doc section);
  - for `unmet`: why not;
  - for `descoped`: the reason, and the bead or agent type that owns it if it belongs elsewhere (e.g. "test writing is owned by the shared tester bead").

### 2. Models

- `AgentRunResult`: `criteria_status: list[dict] = field(default_factory=list)`.
- `HandoffSummary`: `criteria_status: list[dict] = field(default_factory=list)`. Each stored entry has `index`, `criterion` (copied from the bead at finalize), `status` and `evidence`.
- Old bead JSON without the field loads with an empty list.

### 3. Finalize: reconcile against the bead's criteria

Add `_reconcile_criteria_status(criteria: list[str], raw) -> list[dict]` in `src/agent_takt/scheduler/finalize.py`. It is used for agent types `developer`, `defect`, `tester`, `documentation` and `review`, and:

- produces exactly one entry per item in `criteria`, in order, with `criterion` set to the bead's text;
- returns `[]` if `raw` is not a list, and skips non-dict entries;
- keeps the first entry when the agent reported an index more than once;
- drops indices outside the list's range;
- gives any criterion the agent didn't report `status: "unmet"`, `evidence: "not reported by agent"`;
- turns `descoped` with empty evidence into `unmet` with evidence `"descoped without reason"`;
- returns `[]` when `criteria` is empty.

**Ordering in `finalize()`.** Call `_reconcile_criteria_status(bead.acceptance_criteria, agent_result.criteria_status)` and assign the result back to `agent_result.criteria_status` **before** `_apply_review_test_verdict(bead, agent_result)`, so the verdict logic in change 4 sees the reconciled list. Store the same list on `handoff_summary.criteria_status`.

**Recovery path.** In `_handle_recovery_completion()`, reconcile against the **original** bead's `acceptance_criteria` (the bead named by the recovery bead's `recovery_for`), not the recovery bead's own criteria. `build_recovery_prompt` lists the original bead's acceptance criteria with 0-based indices and asks for `criteria_status` against them, based on what the original agent's prose and diff show. If the prose gives no evidence for a criterion, the recovery agent reports it `unmet` with evidence `"not evidenced in original output"`. When the original bead is a `tester` or `review` bead, `_handle_recovery_completion` also runs the change 4 checks (own criteria and, for review, dependency criteria) on the synthesised result before applying the handoff. This includes the `verdict_overridden` event and blocking the original bead, so recovery can't be used to get an approval past unmet criteria.

### 4. Enforce verdicts

In `_apply_review_test_verdict` (`finalize.py:467`), for `tester` and `review` beads, the check runs inside the `approved` branch **before** its early `return`. When the agent omits `verdict` (compat mode), the same check applies if the resulting outcome is `completed`.

- **Own criteria:** if any entry in the bead's own reconciled `criteria_status` is `unmet`, set `verdict = "needs_changes"`, `requires_followup = True`, `outcome = "blocked"`. Set `block_reason` to `"Approved with unmet acceptance criteria: #<i> <criterion text>; ..."`, truncating each criterion's text to 80 characters. A rerun of the bead (after its corrective completes, through `_requeue_parent_after_corrective_completion`) produces a fresh `criteria_status`, so this case clears itself once the gap is fixed.
- **Dependency criteria (review beads only):** collect every `unmet` entry in the latest `handoff_summary.criteria_status` of each **done dependency**, then drop the ones that are *resolved* (below). If any remain, apply the same override, with `block_reason` `"Dependency acceptance criteria unmet: <bead_id> #<i> <criterion>; ..."`. Store the remaining entries on the review bead as `metadata["unmet_dependency_criteria"] = [{"bead_id", "index", "criterion"}]`.

Compute this in one helper, `dependency_criteria_state(storage, review_bead) -> tuple[list[dict], list[dict]]`, which returns `(unresolved, resolved)`. Each resolved entry also carries `resolved_by` (the corrective bead ID) and that corrective's `evidence`. Both the gate here and the review prompt (change 5) call it, so what the reviewer sees and what the scheduler enforces can't diverge.

**Lifecycle of `unmet_dependency_criteria`:** it is overwritten with the current unresolved set on every override, and removed from the review bead's metadata when the review completes `approved`. A later corrective for the same review therefore inherits only entries that are still unresolved; if the set is empty or absent, no `Resolve unmet criterion ...` criteria are added.

**How a dependency's unmet criterion is resolved.** When the scheduler creates a corrective for a blocked review bead (`_create_corrective_bead`, `followups.py:85`), the corrective is a child of the *review* bead, not of the dependency. So:

- `_create_corrective_bead` appends one acceptance criterion per entry in the parent's `metadata["unmet_dependency_criteria"]`, worded exactly `Resolve unmet criterion #<i> from <bead_id>: <criterion>`, after its three standard criteria;
- a dependency entry `(bead_id, i, criterion)` counts as resolved when a **done** bead with `metadata["auto_corrective_for"] == <review bead_id>` has a `criteria_status` entry with status `met` and criterion text exactly `Resolve unmet criterion #<i> from <bead_id>: <criterion>`;
- an entry also clears naturally if the dependency bead itself is re-run and its latest `criteria_status` reports the criterion `met`.

The review is re-queued after the corrective completes (existing behaviour), the resolved entries are dropped, and the review can then approve. If correctives keep failing to resolve an entry, the existing `max_corrective_attempts` budget and escalation apply unchanged.

Append a `verdict_overridden` execution-history event with the reason whenever an override happens, so the override shows up in `takt bead history`.

`developer`, `defect` and `documentation` beads are **not** blocked for unmet criteria. They complete as today, and the unmet entries flow to the review bead (change 5), which can't approve past them. Downstream stages stay responsible for catching gaps, so no new stop is added in the middle of the run.

### 5. Pass accounting to the review bead

Extend `render_dep_handoff_context` (`prompts.py:153`) for `review` beads. Add an "Acceptance criteria accounting from dependencies" block built from `dependency_criteria_state` (change 4) plus the dependencies' `descoped` entries:

- unresolved: `[unmet] <bead_id> #<index>: <criterion> — <evidence>`
- resolved: `[resolved] <bead_id> #<index>: <criterion> — resolved by <corrective_id>: <evidence>`
- descoped: `[descoped] <bead_id> #<index>: <criterion> — <evidence>`

If there are no unresolved or descoped entries and nothing was resolved, write the single line `All dependency acceptance criteria reported met.`

Wiring: `render_dep_handoff_context` has no storage access, so the caller in `src/agent_takt/scheduler/execution.py` calls `dependency_criteria_state(storage, bead)` for review beads and passes the `(unresolved, resolved)` tuple to `render_dep_handoff_context` (through `build_worker_prompt`) as a new keyword argument `criteria_state`. It defaults to `None`, meaning no accounting block is rendered. This needs the dependency bead IDs; if spec-36cd4ad3 has landed, reuse its `(bead_id, HandoffSummary)` change, otherwise make that change here.

### 6. Guardrails and prompt rendering

In `build_worker_prompt` (`prompts.py:176`), add an `Acceptance criteria (0-based index):` block before the assigned-bead JSON, one line per criterion in the form `[0] <criterion>`, so the indices agents report can't be misread. The JSON payload is unchanged.

Add to `developer`, `defect`, `tester`, `documentation` and `review` guardrails (`templates/agents/` and `src/agent_takt/_data/templates/agents/`, identical copies):

> **Acceptance criteria accounting.** Return one `criteria_status` entry for every item in the bead's `acceptance_criteria`, using its 0-based `index`. Use `met` only when you can point to where it is satisfied, and put that location in `evidence`. Use `descoped` when the criterion is owned by another bead or agent type, or was deliberately left out, and give the reason. Use `unmet` otherwise. A criterion you don't report is recorded as unmet.

Additional review guardrail text:

> Treat every entry under "Acceptance criteria accounting from dependencies" as part of your review. An `[unmet]` dependency criterion prevents approval; the scheduler will override an `approved` verdict. A `[resolved]` entry does not block approval. Check that the cited fix is real; if it isn't, that is a finding. A `descoped` entry is acceptable only if its reason is legitimate. If a descoping drops required behaviour from the spec, that is a finding.

### 7. Scenario coverage (requires spec-ea87bef7)

When the planner has copied spec scenarios into a tester bead's acceptance criteria (spec-ea87bef7, change 5), each such criterion has the form `Scenario: <capability> / <requirement name> / <scenario name> — <WHEN/THEN text>`. The part before ` — ` is the scenario's **prefix**. A criterion *matches* a prefix when it equals the prefix exactly or starts with the prefix followed by ` — ` (space, em dash, space). Plain `startswith` is not enough: `Scenario: a / b / Login` must not match `Scenario: a / b / Login fails — ...`. Update the spec-intake skill text from spec-ea87bef7 to require this exact form. Because planners often write `-` or `--` instead of an em dash, normalise ` -- ` and ` - ` to ` — ` in **both** the criterion text and the prefix before matching, at plan time and at finalize. For this to be unambiguous, capability, requirement and scenario names must not contain ` — `, ` -- ` or ` - `. `parse_requirement_scenarios` reports any name that does, and the plan-time check (change 8) fails with `error: scenario name contains a reserved delimiter: <prefix>` (not downgraded by `--allow-uncovered-scenarios`). Add the same restriction to the spec-intake skill text, and suggest it as a rule for spec-ea87bef7's `spec.py validate`.

Add a separate pass, `_verify_scenario_citations(entries: list[dict], worktree: Path) -> list[dict]`. `finalize()` and `_handle_recovery_completion()` call it for tester beads right after `_reconcile_criteria_status` and before the change 4 checks. `worktree` is `bead.execution_worktree_path`, or the repo root if that is empty. It checks every `met` entry whose criterion matches a `Scenario:` prefix:

- `evidence` must contain at least one reference of the form `<relative/path>::<test_name>`;
- each referenced path must exist under `worktree`, and the file must contain `<test_name>` as a substring;
- if any check fails, the entry becomes `unmet` with evidence `"cited test not found: <ref>"` (or `"no test reference in evidence"`). Change 4 then turns an `approved` verdict into `needs_changes`.

This is a cheap existence check, not proof that the test exercises the scenario. Its purpose is to catch fabricated or stale citations. Update the tester guardrail to require the `path::test_name` format for scenario criteria.

### 8. Plan-time scenario coverage (requires spec-ea87bef7)

Add `src/agent_takt/spec_requirements.py` with `parse_requirement_scenarios(spec_text: str) -> list[tuple[str, str, str]]`. It returns `(capability, requirement, scenario)` for every `#### Scenario:` under an `### ADDED:` or `### MODIFIED:` entry in the `## Requirements` section, using the heading format defined in spec-ea87bef7, change 1. It only parses headings; it doesn't validate the spec (that's `spec.py validate`'s job).

In `takt plan --write <spec>` (and in `takt plan --output`, to give an early warning), after the proposal is produced and before beads are persisted:

- compute the expected prefixes `Scenario: <capability> / <requirement> / <scenario>`;
- check that each one *matches* (as defined in change 7) some acceptance criterion on a tester bead in the proposal;
- if any are missing, print `error: plan does not cover scenario: <prefix>` for each one, exit 1, and persist nothing. `--output` prints the same lines as warnings and still writes the file.

Add a flag `--allow-uncovered-scenarios` to `takt plan` that downgrades the error to a warning and persists. `--from-file` runs the check only if the plan JSON records the spec path; otherwise it skips the check and says so. A spec with no `## Requirements` section skips the check silently.

### 9. Feature-level visibility

`takt summary --feature-root <id>` (which prints JSON via `console.dump_json`) gains a `criteria` object, `{"met": n, "unmet": n, "descoped": n}`, aggregated over the latest `criteria_status` of every bead in the tree. Beads with no recorded `criteria_status` are not counted. The full per-criterion listing goes in the feature audit report (spec-4967f1b0).

### 10. Operator events

`finalize()` and `_handle_recovery_completion()` append to `.takt/logs/events.jsonl` (through `storage.record_event`), so the data is in the operator event stream from day one. Rendering in the TUI and in `takt run` is covered by spec-c8db74e5 (Operator Surfaces).

- `verdict_overridden`, whenever change 4 overrides a verdict: `{"bead_id", "agent_type", "feature_root_id", "reason": "own_criteria" | "dependency_criteria", "unmet": [{"bead_id", "index"}]}`.
- `criteria_gap_recorded`, once per finalized bead that has at least one `unmet` or `descoped` entry and was **not** overridden: `{"bead_id", "agent_type", "feature_root_id", "unmet": n, "descoped": n}`.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/runner.py`, `src/agent_takt/prompts.py` | `criteria_status` in `AGENT_OUTPUT_SCHEMA`; review dependency accounting block in `render_dep_handoff_context`; indexed criteria block in `build_worker_prompt`; recovery prompt lists original criteria |
| `src/agent_takt/models.py` | `criteria_status` on `AgentRunResult` and `HandoffSummary` |
| `src/agent_takt/scheduler/finalize.py` | `_reconcile_criteria_status`; scenario evidence check; verdict override + `verdict_overridden` event |
| `src/agent_takt/scheduler/execution.py` | Dependency handoffs carry bead IDs (if not already done by spec-36cd4ad3) |
| `src/agent_takt/scheduler/followups.py` | `_create_corrective_bead` appends `Resolve unmet criterion ...` criteria from `metadata["unmet_dependency_criteria"]` |
| `src/agent_takt/spec_requirements.py` (new) | `parse_requirement_scenarios` |
| `src/agent_takt/planner.py`, `src/agent_takt/cli/commands/misc.py`, `src/agent_takt/cli/parser.py` | Plan-time scenario coverage check; `--allow-uncovered-scenarios` |
| `src/agent_takt/storage.py` (`summary`) and summary formatting | `criteria` aggregate |
| `templates/agents/{developer,defect,tester,documentation,review}.md` + `_data` copies | Accounting rules; tester `path::test_name` rule; review dependency rule |
| `templates/skills/task/spec-intake/SKILL.md` + `_data` copy | Exact `Scenario:` prefix for copied scenarios |
| `CLAUDE.md` | Document criteria accounting, verdict override, plan-time coverage check |
| `tests/test_scheduler_finalize.py`, `tests/test_cli_*.py`, `tests/test_spec_requirements.py` (new) | Tests per Acceptance Criteria |

## Acceptance Criteria

- Both `AGENT_OUTPUT_SCHEMA` definitions include `criteria_status` with the item schema in change 1, listed in `required`.
- For a developer bead with 3 acceptance criteria where the agent reports index 0 `met` and index 2 `descoped` with a reason, the persisted `handoff_summary.criteria_status` has 3 entries in order: `met`, `unmet` ("not reported by agent"), `descoped`. Each entry's `criterion` text matches the bead.
- Duplicate indices keep the first entry; an out-of-range index is dropped; `descoped` with empty evidence becomes `unmet` ("descoped without reason"). Each case has a unit test.
- A bead with no acceptance criteria persists `criteria_status == []`.
- A tester bead returning `verdict=approved` with one of its own criteria `unmet` ends `blocked`, with `handoff_summary.verdict == "needs_changes"`, a `block_reason` beginning `Approved with unmet acceptance criteria:`, and a `verdict_overridden` event in its history.
- A review bead returning `verdict=approved` while a done dependency has an `unmet` criterion is overridden the same way, with the dependency bead ID in `block_reason`. With all dependency criteria `met` or `descoped`, the same review bead stays `approved`.
- A developer bead completing with an `unmet` criterion ends `done` (not blocked).
- Corrective resolution:
  - a review bead blocked for a dependency's unmet criterion #1 stores it in `metadata["unmet_dependency_criteria"]`;
  - the corrective created for that review has the extra criterion `Resolve unmet criterion #1 from <dep_id>: <criterion>`;
  - after the corrective completes, reporting that criterion `met`, and the review is re-queued and returns `approved`, the review ends `done` with no override.
  - If the corrective reports it `unmet`, the review is overridden again.
- After the corrective reports the inherited criterion `met`, the re-queued review's prompt shows it as `[resolved] <dep_id> #1: ... — resolved by <corrective_id>: ...` and not as `[unmet]`. The prompt and the gate both use `dependency_criteria_state` (a test asserts they agree for the same storage state).
- `metadata["unmet_dependency_criteria"]` is overwritten on each override and absent after the review completes `approved`. A corrective created afterwards for an unrelated block has no `Resolve unmet criterion ...` criteria.
- A recovered tester bead whose synthesised result is `verdict=approved` with an unmet criterion ends blocked with `needs_changes` and a `verdict_overridden` event.
- `_verify_scenario_citations` runs on both the normal and the recovery finalize paths for tester beads.
- A tester criterion written `Scenario: a / b / Login - WHEN ...` (hyphen) matches the prefix `Scenario: a / b / Login`.
- A spec whose scenario name contains ` - ` (e.g. `Login - admin`) makes `takt plan --write` exit 1 with the reserved-delimiter error, even with `--allow-uncovered-scenarios`.
- A compat-mode tester result (no `verdict`, outcome `completed`) with an unmet own criterion is overridden to `needs_changes`.
- For a recovered developer bead, `criteria_status` is reconciled against the original bead's acceptance criteria, and `build_recovery_prompt` output lists those criteria with `[0]`-style indices.
- The worker prompt contains an `Acceptance criteria (0-based index):` block with one `[i] <criterion>` line per criterion.
- The review bead's prompt lists dependency `unmet` and `descoped` entries in the format given in change 5, or the line `All dependency acceptance criteria reported met.`
- For a tester criterion prefixed `Scenario:` and reported `met`:
  - evidence citing `tests/test_x.py::test_y`, where the file exists and contains `test_y`, stays `met`;
  - a missing file, a missing name, or no reference makes it `unmet` with the evidence text from change 7.
- Prefix matching: `Scenario: a / b / Login` matches `Scenario: a / b / Login` and `Scenario: a / b / Login — WHEN ...`, but not `Scenario: a / b / Login fails — WHEN ...`. Unit tested for both the plan-time check and the finalize check.
- `parse_requirement_scenarios` returns `(capability, requirement, scenario)` tuples for ADDED and MODIFIED entries, ignores REMOVED entries, and returns `[]` for a spec with no `## Requirements` section.
- `takt plan --write` on a spec with 2 scenarios whose proposal covers only 1 exits 1, prints `error: plan does not cover scenario: Scenario: <...>` for the missing one, and creates no beads. With `--allow-uncovered-scenarios` it prints a warning and persists.
- `takt summary --feature-root <id>` JSON includes the `criteria` counts object.
- `verdict_overridden` and `criteria_gap_recorded` events are appended to `events.jsonl` with the payloads in change 10. No `criteria_gap_recorded` event is written for a bead whose criteria are all `met`.
- Bead JSON written before this change loads with `criteria_status == []` and never triggers a verdict override.
- Guardrail files contain the rules from change 6 (and the tester `path::test_name` rule), and each is byte-identical to its `_data` copy.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

1. **Dependency on spec-ea87bef7.** Changes 7 and 8 need the `## Requirements` format from the Living Specs spec. Recommendation: plan this spec after spec-ea87bef7 is done. If it needs to go first, deliver changes 1–6 and 9 and move 7–8 to a follow-up spec. They're separable.
2. **Should `takt merge` refuse when unmet criteria remain?** Change 4 already stops the review bead approving, so a tree normally can't reach merge with unmet criteria unless an operator intervenes. Recommendation: no extra merge gate in this spec. The feature audit report (spec-4967f1b0) shows unmet and descoped counts at merge.
3. ~~**Corrective beads and the parent's unmet criteria.**~~ Resolved: correctives for a blocked review bead are children of the review, not of the dependency (`followups.py:100`), so a check keyed on the dependency's correctives would never match. Instead, the review's corrective inherits the unmet dependency criteria as explicit acceptance criteria, and the review dependency check treats them as resolved when that corrective reports them `met` (change 4).
4. **Scope creep (code with no backing criterion).** Not addressed here; it can't be checked cheaply and deterministically. The reviewer's existing scope checks remain the only guard.
