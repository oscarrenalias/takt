---
name: Structured Assumptions in Worker Handoffs
id: spec-36cd4ad3
description: "Structured local/structural assumptions in worker output, passed to review/tester and checked by the reviewer"
dependencies: null
priority: high
complexity: null
status: planned
tags:
- handoff
- schema
- guardrails
- review
- governance
scope:
  in: null
  out: null
feature_root_id: B-41065767
---
# Structured Assumptions in Worker Handoffs

## Objective

Make the decisions agents take on their own during a run visible and reviewable, without pausing the run. Workers often hit gaps in the spec or bead description (an unspecified error message, a default value, the shape of a new config key) and fill them in silently. Today those choices end up, if anywhere, in the free-form `design_decisions` string, mixed with implementation trade-offs, and nothing downstream treats them differently. This spec adds a structured `assumptions` list to the worker output schema, classifies each assumption as `local` or `structural`, passes them to the review and tester beads, and requires the reviewer to check structural ones explicitly. The idea comes from the "assumed decisions are logged, structural decisions need an owner" pattern in an internal Accenture SDD framework (myArchitecture4code). Here it is adapted to takt's hands-off model: agents still decide and keep going, but every decision leaves a record that is checked and audited.

## Problems to Fix

1. **Agent-made decisions aren't visible.** When a spec doesn't say what a new CLI flag's default is, or what happens on an empty input, the developer agent picks something. That choice is invisible unless the agent happens to mention it in `design_decisions` (`src/agent_takt/models.py:60`), a free-form string the developer guardrail defines as "non-obvious architectural or implementation choices ... alternatives considered". Filling a gap in the spec and choosing between implementation options are different things, and the current field merges them.
2. **The reviewer has no prompt to challenge them.** `render_dep_handoff_context` (`src/agent_takt/prompts.py:153`) passes only `design_decisions` to review beads. Nothing tells the reviewer "these are things the developer assumed; check them against the spec". The review guardrail (`templates/agents/review.md`) has no rule about assumptions.
3. **High-impact and low-impact guesses look the same.** Guessing a log message wording and guessing a persisted JSON field name carry very different risks, but nothing separates them. An operator reading handoffs after the run can't find the guesses worth a second look.
4. **Nothing can be aggregated.** Because the information is prose, it can't be counted, listed per feature, or surfaced at merge time (see spec-4967f1b0, Feature Audit Report).

## Changes

### 1. Output schema: `assumptions` field

Add an `assumptions` property to `AGENT_OUTPUT_SCHEMA` in **both** `src/agent_takt/runner.py:131` and `src/agent_takt/prompts.py:15`, and add it to each schema's `required` list:

```python
"assumptions": {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "statement": {"type": "string"},
            "rationale": {"type": "string"},
            "scope": {"type": "string", "enum": ["local", "structural"]},
        },
        "required": ["statement", "rationale", "scope"],
    },
},
```

- `statement`: what was assumed, phrased as a fact about intended behaviour ("Empty `--label` values are rejected with exit code 2").
- `rationale`: why this was the reasonable reading (what in the spec, code or conventions pointed to it).
- `scope`: `structural` or `local`, defined in change 4.

An empty array is valid and expected for most beads. `INVESTIGATOR_OUTPUT_SCHEMA` and `PLANNER_OUTPUT_SCHEMA` are unchanged.

`tests/test_recovery_flow.py` already asserts that the two `AGENT_OUTPUT_SCHEMA` definitions are structurally identical. Extend that test so it covers `assumptions` explicitly.

### 2. Models

- `AgentRunResult` (`src/agent_takt/models.py:191`): add `assumptions: list[dict[str, str]] = field(default_factory=list)`.
- `HandoffSummary` (`src/agent_takt/models.py:44`): add `assumptions: list[dict[str, str]] = field(default_factory=list)`.
- Existing bead JSON without the field must load unchanged (`HandoffSummary(**data.get("handoff_summary", {}))` already tolerates missing keys; add a test).
- `AgentRunResult` objects built directly, not from a parsed payload (the fallback in `runner.py` and the failure path at `scheduler/execution.py:181`), keep `assumptions` at its default `[]`. Nothing changes there beyond the dataclass default.

### 3. Finalize: normalise and persist

In `BeadFinalizer.finalize()` (`src/agent_takt/scheduler/finalize.py:95`) and `_handle_recovery_completion()` (`finalize.py:351`), pass assumptions into the `HandoffSummary` through a helper `_normalize_assumptions(raw: list) -> list[dict[str, str]]`:

- if `raw` is not a list, return `[]`;
- skip entries that are not dicts;
- treat a missing `statement` or `rationale` key as an empty string;
- strip whitespace on all three fields;
- drop entries with an empty `statement`;
- set any `scope` value other than `local` or `structural` (including empty or missing) to `structural`. When unsure, err on the side of more scrutiny;
- de-duplicate entries with identical `statement` text (keep the first).

The recovery prompt (`build_recovery_prompt`, `prompts.py:245`) asks the recovery agent to extract any assumptions the original agent stated in its prose. Otherwise it returns an empty list.

### 4. Guardrails: what to record and how to classify it

Add an **Assumptions** section to the developer, defect, tester and documentation guardrails. Apply the text to both `templates/agents/<type>.md` and `src/agent_takt/_data/templates/agents/<type>.md`, keeping the two copies identical:

> **Assumptions.** Record in `assumptions` every point where the bead, spec or linked docs did not say what to do and you chose. This differs from `design_decisions`: a design decision is a choice between implementation options you evaluated, while an assumption is something you took as given about *intended behaviour, inputs, environment or contracts* because nobody specified it. Record only assumptions that would change observable behaviour if they were wrong; don't record trivia.
>
> Classify each one as `structural` if a wrong guess would affect any of:
> - a public contract: CLI flags, arguments, exit codes, output formats, config keys, a public Python API;
> - a persisted format: bead JSON fields, files written to disk, data other tools read;
> - security: authentication, permissions, secrets, handling untrusted input;
> - something hard to reverse after merge: migrations, deletions, renames of persisted things;
> - an interface between modules owned by different beads or features.
>
> Otherwise classify it as `local`. When unsure, use `structural`.
>
> Keep going after recording an assumption. Do not block just because something was unspecified. Block only when no reasonable assumption lets you complete the bead.

The tester guardrail gets one extra sentence: "When a dependency's structural assumption describes testable behaviour and is within this bead's file scope, prefer adding a test that pins it, so a future change to that behaviour is deliberate."

### 5. Pass assumptions to review and tester beads

Extend `render_dep_handoff_context` (`prompts.py:153`):

- **review**: in addition to `design_decisions`, render an "Assumptions from dependencies" block listing every assumption from **every done dependency**, structural ones first. This includes tester and documentation dependencies of a shared review bead, matching the dependency set `_populate_shared_followup_touched_files` uses. Each line has the form `[structural] <statement> — <rationale> (from <bead_id>)`. If `rationale` is empty, leave out the ` — <rationale>` segment.
- **tester**: render only the `structural` assumptions, in the same line format.

Each rendered line needs the originating bead ID, so the dependency loader must give the renderer the bead ID alongside each `HandoffSummary`. Changing `_load_dep_handoffs` in `src/agent_takt/scheduler/execution.py` to return `(bead_id, HandoffSummary)` pairs, or an equivalent, is acceptable. Update all callers.

### 6. Review guardrail

Add to `templates/agents/review.md` (and its `_data` copy):

> **Check every structural assumption** listed under "Assumptions from dependencies" against the bead description, acceptance criteria and linked docs:
> - If it **contradicts** them, it is a finding: `verdict=needs_changes`, with the assumption quoted in `block_reason`.
> - If it is **consistent** with them, or they are silent and the assumption is reasonable, accept it, and list it in `risks` as "Accepted structural assumption: <statement> (<bead_id>)" so it remains visible.
> - If it is **unreasonable** even though nothing contradicts it (e.g. it widens a public contract the spec never mentions), treat it as a finding.
>
> Local assumptions need no explicit check unless one is clearly wrong.

Review beads themselves return `assumptions: []` unless the reviewer had to assume something to reach a verdict.

### 7. Operator event

After persisting a handoff with at least one `structural` assumption, `finalize()` and `_handle_recovery_completion()` call `storage.record_event("structural_assumption_recorded", {"bead_id", "agent_type", "feature_root_id", "count": n, "statements": [<structural statements>]})`, so the signal is in the operator event stream from day one. No event is written for beads with only local assumptions or none. Rendering in the TUI and in `takt run` is covered by spec-c8db74e5 (Operator Surfaces).

### 8. Documentation

- `CLAUDE.md`, Key Concepts: a short **Assumptions** paragraph describing the field, the `local`/`structural` split, and that reviewers check structural assumptions.
- `takt bead show <id> --field handoff_summary.assumptions` works through the existing field projection. Add it as an example in CLAUDE.md's field projection list.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/runner.py` | Add `assumptions` to `AGENT_OUTPUT_SCHEMA` properties and `required` |
| `src/agent_takt/prompts.py` | Same schema change; `render_dep_handoff_context` renders assumptions for review (all) and tester (structural); recovery prompt mentions assumptions |
| `src/agent_takt/models.py` | `assumptions` field on `AgentRunResult` and `HandoffSummary` |
| `src/agent_takt/scheduler/finalize.py` | `_normalize_assumptions`; persist in `finalize()` and `_handle_recovery_completion()` |
| `src/agent_takt/scheduler/execution.py` | Dependency handoff loading returns the bead ID with each handoff |
| `templates/agents/{developer,defect,tester,documentation,review}.md` + `src/agent_takt/_data/templates/agents/` copies | Assumptions section (change 4); review check rule (change 6) |
| `CLAUDE.md` | Assumptions paragraph and field-projection example |
| `tests/test_recovery_flow.py` | Extend the existing schema-identity test to cover `assumptions` |
| `tests/test_scheduler_finalize.py`, `tests/test_scheduler_execution.py`, prompt tests in `tests/test_orchestrator.py` | Tests per Acceptance Criteria |

## Acceptance Criteria

- Both `AGENT_OUTPUT_SCHEMA` definitions contain `assumptions` with the item schema in change 1 and list it in `required`. The schema-identity test in `tests/test_recovery_flow.py` covers the field.
- A `FakeRunner` result with two assumptions (one `local`, one `structural`) on a developer bead results in `bead.handoff_summary.assumptions` holding both entries, in order, after finalize.
- `_normalize_assumptions` drops an entry with an empty or whitespace-only `statement`, maps `scope: ""` and `scope: "major"` to `structural`, and de-duplicates identical statements. Each case has a unit test.
- A bead JSON file written before this change (no `assumptions` key in `handoff_summary`) loads, and `handoff_summary.assumptions == []`.
- A recovery completion persists any assumptions returned by the recovery agent onto the original bead's handoff.
- The output of `build_recovery_prompt` contains an instruction to fill `assumptions` from any assumptions stated in the original agent's prose.
- `_normalize_assumptions` returns `[]` for a non-list input and skips a non-dict entry. Each case has a unit test.
- The worker prompt for a review bead whose done dependency has a structural assumption contains the line `[structural] <statement> — <rationale> (from <dep_bead_id>)`, and it appears before any local assumptions.
- The worker prompt for a tester bead contains the dependency's structural assumptions and none of its local ones.
- Prompts for developer, documentation and defect beads contain no "Assumptions from dependencies" block.
- `templates/agents/developer.md`, `defect.md`, `tester.md` and `documentation.md` contain the Assumptions section with the structural criteria list. `review.md` contains the structural-assumption check rule. Each file is byte-identical to its `src/agent_takt/_data/templates/agents/` copy.
- `uv run takt bead show <id> --field handoff_summary.assumptions` prints the list as JSON for a bead with assumptions, and `[]` for one without.
- A developer bead with two structural assumptions and one local one appends exactly one `structural_assumption_recorded` event, with `count: 2` and both statements. A bead with only local assumptions appends none.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

1. **Should structural assumptions ever block automatically?** Recommendation: no. The reviewer decides (change 6), and the feature audit report (spec-4967f1b0) surfaces them at merge. Blocking on the mere presence of a structural assumption would bring back the human pacing takt avoids.
2. **Injecting approved ADRs into worker and reviewer context**, so the reviewer can check assumptions against recorded decisions as well as the spec. Recommendation: out of scope here, and worth its own spec. ADR pipeline integration is explicitly out of scope today (CLAUDE.md, ADRs section), and it needs a way to link a feature to its ADRs (`related_specs` exists on `Adr`, `src/agent_takt/adr.py:69`). Until then, operators can add ADR paths to a bead's `linked_docs`.
3. ~~**TUI display.**~~ Resolved: handled by spec-c8db74e5 (Operator Surfaces). This spec only emits the `structural_assumption_recorded` event (change 7).
