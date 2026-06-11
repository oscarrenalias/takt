---
name: Add defect bead type for lightweight bugfix workflow
id: spec-f21c4b6b
description: "Introduce a defect bead type and matching agent type so post-merge bugs can be filed and fixed in one fix-plus-test bead followed only by a shared review — no per-defect spec, no spawned tester or documentation children."
dependencies: null
priority: medium
complexity: medium
status: planned
tags:
- agent-types
- bead-types
- workflow
- skills
- followups
scope:
  in: null
  out: null
feature_root_id: null
---
# Add defect bead type for lightweight bugfix workflow

## Objective

Operators routinely surface bugs *after* a feature merges to main — particularly UI/runtime issues that only show up once the code is built and exercised in a browser or on a device. Today's two options for handling these are both wrong-shaped:

1. **Write a fix spec.** Heavyweight: specs are the planning artefact for features, not for one-line bug fixes. The plan → run → merge cycle for a single regression is several beads of overhead.
2. **File a standalone developer bead.** Right shape, wrong fan-out: the existing standalone-developer auto-followup logic spawns `-test`, `-docs`, and `-review` children for every defect, producing four beads per bug.

Introduce a third option — a **`defect` bead type** paired with a new **`defect` agent type** — that bundles fix + regression test + build sanity in one bead, and then spawns only a single shared `-review` followup. This keeps the review safety net (which earns its keep — see spec-9173b2da, where the reviewer caught a rename-path inversion the developer missed) while cutting per-defect overhead from four beads to two.

## Problems to Fix

1. **Spec lifecycle is the wrong tool for individual bug reports.** Operators end up creating "specs" that are really one-paragraph defect descriptions, polluting the spec inventory.
2. **Standalone developer beads fan out into four beads per defect** via the existing per-developer auto-followup logic (`-test`, `-docs`, `-review`). For a single CSS or off-by-one fix this is pure ceremony.
3. **Developer guardrail forbids running tests.** `templates/agents/developer.md` is explicit: *"Do not run any test suite or test runner. Test execution is exclusively the tester agent's responsibility."* Even if we collapsed the bead graph, the agent itself would refuse to verify its own fix. A defect workflow needs an agent type whose guardrail permits and *mandates* a focused regression test.
4. **No CLI affordance for filing a defect.** `takt bead create` accepts `--agent` and the existing standalone developer flow, but there's no way to flag a bead as a defect so that followup logic can treat it differently.
5. **Build verification is missing entirely.** For TypeScript/UI projects, "the code looks right" is not the same as "the bundle builds." Today no agent type runs `npm run build` / `vite build` as a sanity gate. The defect agent should.

## Changes

### 1. New `defect` agent type

Add `defect` to the agent_type enum across the model, JSON schemas, and config:

- `src/agent_takt/models.py` — `defect` joins the `agent_type` Literal/enum. Like `developer`, `tester`, and `documentation`, the defect agent is permitted to mutate code; update any "code-mutating agent types" allowlists to include it.
- `src/agent_takt/runner.py` (or wherever `AGENT_OUTPUT_SCHEMA` / `PLANNER_OUTPUT_SCHEMA` live) — add `defect` to the `agent_type` enum constraint in both schemas.
- `src/agent_takt/config.py` — `OrchestratorConfig.common.agent_types` default must include `defect`; `allowed_tools_by_agent.defect` should at minimum include `Bash`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `Skill`, `WebFetch`, `WebSearch` (matching developer plus what's needed to run tests).

### 1a. Planner prompt — `defect` vs `corrective` boundary

The planner must understand the difference between the two single-bead "fix" types so it never conflates them:

- **`corrective`** — scheduler-created, always has a parent bead, exists to retry transient failures of the parent's work. Operators do not file correctives.
- **`defect`** — operator-filed (or, rarely, planner-emitted for known post-merge regressions), standalone (no parent bead), exists to fix a bug discovered after the originating feature has merged.

Update `src/agent_takt/prompts.py` (planner prompt builder) to document both types and their distinguishing trigger. The planner should not emit `defect` beads when planning new features; defects are reactive, not predictive.

### 2. New guardrail template — `templates/agents/defect.md`

Mandatory file (per the existing convention; missing template must fail the bead with `FileNotFoundError`). The guardrail must:

- **Permit** running focused tests (`pytest <path>`, `npm test -- <path>`, etc.) for the affected module only.
- **Permit** running a build sanity step when the project has one. Detection is heuristic: inspect the worktree root for `package.json` (Node) → look for a `build` script; `pyproject.toml` (Python) → no build step needed beyond `py_compile`; `Cargo.toml` (Rust) → `cargo check` or `cargo build`. If no obvious build target is detected, the agent must record `build_verification: "skipped"` in the structured output (with a short rationale) rather than silently omitting the step — this makes the decision auditable.
- **Mandate** adding a regression test that fails without the fix and passes with it. If the bug is genuinely untestable in an automated way (e.g. pixel-level CSS), the agent must explicitly call this out in the structured output's `test_coverage_notes` field rather than silently skipping.
- **Forbid** running the full test suite — focused tests only. Full-suite verification belongs to the `-review` reviewer or to `takt merge`'s test gate.
- **Forbid** scope creep — same strict scope guardrail as corrective beads. Touch only files needed for the specific fix; file separate defects for unrelated issues discovered along the way.
- **Forbid** taking over reviewer signoff — the `-review` followup still happens.

Use the existing `templates/agents/developer.md` and the corrective-implementation skill as drafting references; defect-fix is closest in shape to corrective.

### 3. New `defect` bead type

Add `defect` to the bead_type vocabulary:

- `src/agent_takt/models.py` — extend the `bead_type` Literal.
- The bead type is set at creation. Standalone defect beads have `bead_type="defect"`; the auto-spawned review child has whatever bead type the existing review followup machinery uses.

### 4. Followup behaviour

In `src/agent_takt/scheduler/followups.py`:

- **Suppress the standard `-test`/`-docs` auto-followups for `bead_type="defect"`.** Extend the existing suppression check that already covers `corrective` and `merge-conflict`.
- **Spawn exactly one `-review` followup** when a defect bead completes successfully. This is the only difference from the corrective/merge-conflict path (which spawns nothing).
- The shared-followup scope population (`_populate_shared_followup_touched_files`) should pull from the defect bead's `touched_files` / `changed_files` exactly as it does for developer beads.

### 5. Skill bundle — `src/agent_takt/skills.py`

Add an entry to `AGENT_SKILL_ALLOWLIST` for the defect agent type. Bundle:

| Slot | Skill |
|---|---|
| core | `core/base-orchestrator` |
| role | `role/defect-fix` *(new — see §6)* |
| capability | `capability/code-edit` |
| capability | `capability/test-execution` |
| task | `task/corrective-implementation` *(reused — same fix+test+scope-discipline shape)* |
| memory | `memory` |

### 6. New role skill — `templates/skills/role/defect-fix/SKILL.md`

A small role skill that frames the defect-fix workflow:

- Read the bead description, reproduce the issue mentally, identify root cause.
- Make the minimal fix.
- Add a regression test that fails without the fix and passes with it.
- Run focused tests for the affected file/module.
- Run the project build sanity step if applicable.
- Return structured output with `verdict`, `findings_count`, and explicit `test_coverage_notes`.
- Do **not** broaden scope, do **not** run the full suite, do **not** edit unrelated files.

If `task/corrective-implementation` already covers most of this, the role skill can be intentionally thin — focused on the "this is a post-merge defect, the operator filed it, the bug is real" framing rather than the fix mechanics.

### 7. CLI affordance

In `src/agent_takt/cli/commands/bead.py`:

- `takt bead create --agent defect --type defect --title "..." --description "..." [--label …]` must work.
- **Validation rule (both directions, strict):** `--agent defect` requires `--type defect` and `--type defect` requires `--agent defect`. Any mismatch (`--agent defect --type developer`, `--agent developer --type defect`, `--agent defect` without `--type`, etc.) exits non-zero with a clear error message identifying both flags.
- Update the `--agent` choices help text and the `--type` validator to include `defect`.
- Document the workflow in the CLI help where appropriate.

### 7a. Reuse audit for `task/corrective-implementation`

Before reusing `task/corrective-implementation` in the defect skill bundle, audit its content (`templates/skills/task/corrective-implementation/SKILL.md`) for references to "parent bead", "the parent's failure", or similar parent-scoped framing. Defects are standalone; correctives have a parent. If parent-bead language is present:

- Either parameterise it in the existing skill (preferred — single source of truth), or
- Note the divergence in `role/defect-fix/SKILL.md` so the defect agent overrides the parent-bead reading with the defect bead's own description.

This audit is a deliverable of the same developer bead that adds the new skill bundle in `skills.py` — implementer must confirm and record the outcome.

### 8. Documentation

- `CLAUDE.md` — add a new "Defect beads" subsection under **Key Concepts** describing the type, the agent's guardrail, the single-`-review` followup, and the CLI invocation. Add a one-liner to the **Agent types** list and the **Followup beads** paragraph mentioning the suppression.
- The takt skill SKILL.md (all three copies — `src/agent_takt/_data/claude_skills/takt/SKILL.md`, `.claude/skills/takt/SKILL.md`, `.agents/skills/takt/SKILL.md`) — add a "Filing a defect" subsection under the Bead Lifecycle / CLI sections, showing the `takt bead create --agent defect --type defect …` invocation and noting that the only auto-followup is `-review`.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/models.py` | Add `defect` to agent_type and bead_type enums; update code-mutating-types allowlist |
| `src/agent_takt/runner.py` | Add `defect` to AGENT_OUTPUT_SCHEMA / PLANNER_OUTPUT_SCHEMA enum constraints |
| `src/agent_takt/prompts.py` | Update planner prompt builder to document the `defect` vs `corrective` boundary (operator-filed standalone vs scheduler-created with parent) |
| `src/agent_takt/config.py` | Default `allowed_tools_by_agent.defect`; ensure `agent_types` default includes `defect` |
| `src/agent_takt/skills.py` | New `AGENT_SKILL_ALLOWLIST` entry for `defect` |
| `src/agent_takt/scheduler/followups.py` | Suppress `-test`/`-docs` followups for `bead_type="defect"`; spawn single `-review` |
| `src/agent_takt/cli/commands/bead.py` | Accept `--agent defect` and `--type defect`; validation |
| `templates/agents/defect.md` | NEW mandatory guardrail template |
| `templates/skills/role/defect-fix/SKILL.md` | NEW role skill |
| `CLAUDE.md` | Document defect bead workflow + agent type |
| `src/agent_takt/_data/claude_skills/takt/SKILL.md` | Operator-facing "Filing a defect" doc (canonical copy) |
| `.claude/skills/takt/SKILL.md` | Mirror of above (runtime copy) |
| `.agents/skills/takt/SKILL.md` | Mirror of above (Codex runtime copy) |
| `tests/test_models.py` | Schema/enum acceptance for defect agent_type and bead_type |
| `tests/test_skills.py` | AGENT_SKILL_ALLOWLIST loads correctly for defect |
| `tests/test_scheduler_followups.py` | Defect bead spawns only `-review`; no `-test`/`-docs` |
| `tests/test_cli_bead.py` | `--agent defect --type defect` accepted; invalid combinations rejected |

## Acceptance Criteria

- `takt bead create --agent defect --type defect --title "X" --description "Y"` creates a bead with `agent_type=defect` and `bead_type=defect`.
- **CLI rejects every mismatched combination of `--agent` and `--type`** at parse time with a non-zero exit and an error that names both flags. Specifically: `--agent defect --type developer`, `--agent developer --type defect`, `--agent defect` alone, and `--type defect` alone are all rejected. Tests must enumerate these cases.
- Running a defect bead through the scheduler invokes the new `templates/agents/defect.md` guardrail. Missing template fails the bead with `FileNotFoundError` (same pattern as developer).
- The defect bundle from `AGENT_SKILL_ALLOWLIST` (base-orchestrator + defect-fix + code-edit + test-execution + corrective-implementation + memory) loads when the bead starts; loaded skill count appears in the `skills_loaded` execution event.
- When a defect bead completes successfully, **exactly one** followup is created: a `-review` bead. No `-test` and no `-docs` are created. The review bead's `touched_files`/`changed_files` include the defect bead's reported changes.
- The defect agent's guardrail template `templates/agents/defect.md` contains, at minimum, the following directives (verifiable by grep/test rather than read-through): a "Permit" bullet for focused test runs, a "Permit" bullet for build sanity, a "Mandate" bullet for adding a regression test, a "Forbid" bullet for full-suite runs, and a "Forbid" bullet for scope creep.
- **Schema acceptance:** a `PLANNER_OUTPUT_SCHEMA`-validated planner output containing `"agent_type": "defect"` validates; a `AGENT_OUTPUT_SCHEMA`-validated bead with `agent_type: defect` validates. Both rejected for `agent_type: unknown_value`.
- **Corrective-skill reuse audit completed.** Either `task/corrective-implementation` is confirmed to contain no parent-bead-specific assumptions (recorded in the developer bead's `design_decisions`), or the divergences are documented in `role/defect-fix/SKILL.md` so the defect agent reads its bead description instead of looking for a parent.
- Existing agent types (`developer`, `tester`, `documentation`, `review`, `recovery`, `planner`) are unchanged. Their guardrails, skill bundles, followup behaviour, and JSON schemas are untouched.
- All existing tests pass: `uv run pytest tests/ -n auto -q`.
- CLAUDE.md and all three takt SKILL.md copies document the defect workflow with the CLI invocation and the single-review followup expectation.

## Pending Decisions

- ~~Should the `-review` followup be skippable for trivial defects (CSS-only, copy-only)?~~ **Resolved:** no opt-out for now. Every defect gets a review. We can add a `defect-trivial` tier later if review overhead proves wasteful.
- ~~Should `task/corrective-implementation` be reused as-is, or forked into `task/defect-fix`?~~ **Resolved:** reuse for the first cut, with a mandatory audit (see §7a). Fork later if defect semantics diverge from corrective in a way the audit can't paper over.
- ~~Build-step detection.~~ **Resolved:** heuristic detection in the guardrail (§2), with `build_verification: "skipped"` recorded in structured output when no target is detected. No config field added; revisit if operators need declarative build commands.
- ~~Planner-vs-corrective boundary.~~ **Resolved:** §1a documents the trigger difference in the planner prompt.
- **Operator UX for filing many defects at once.** Out of scope here. If batched defect intake becomes a real workflow (e.g. importing from a Linear or GitHub Issues list), file a separate spec for `takt bead create-batch` or similar.
