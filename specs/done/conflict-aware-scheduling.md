# Conflict-Aware Scheduling

## Objective

Add conflict-aware scheduling to the Codex multi-agent orchestration system so the scheduler can safely run multiple beads in parallel without assigning overlapping work.

The system should prevent concurrent execution of beads that are likely to touch the same files, while still allowing unrelated work to proceed in parallel.

## Why This Matters

The current MVP can schedule ready beads and launch workers in isolated Git worktrees, but it does not yet have a reliable model for determining whether two beads are safe to run at the same time.

Without conflict awareness, the scheduler may:

- assign overlapping implementation beads in parallel
- create unnecessary merge conflicts
- waste agent time on work that will need to be redone
- reduce confidence in autonomous execution

Conflict-aware scheduling is the next core capability needed to make the orchestration system practical for self-hosted multi-agent development.

## Scope

In scope:

- extend bead state to include file claims and discovered touched files
- allow planner and worker agents to record expected file scope
- make the scheduler block or defer beads with overlapping active claims
- expose conflict information through the CLI
- ensure handoff summaries preserve discovered file scope

Out of scope:

- semantic merge conflict detection
- AST-level or symbol-level conflict tracking
- automatic conflict resolution
- distributed or multi-machine locking

## Functional Requirements

### 1. Bead File Scope

Each bead should support the following optional scope fields:

- `expected_files`: explicit file paths the bead expects to modify
- `expected_globs`: file patterns the bead expects to modify
- `touched_files`: concrete files actually modified during execution

These fields should be persisted in the bead state and visible via bead inspection commands.

### 2. Planner Support

When a feature spec is decomposed into child beads, the planner agent should include an initial expected file scope for each child bead whenever it can infer one from the spec.

The planner does not need to be perfect, but it should attempt to narrow the scope of implementation beads enough for the scheduler to make better decisions.

### 3. Worker Updates

Developer, tester, and documentation agents should update the bead with actual touched files and any newly discovered scope changes during execution.

If an agent discovers that the task requires changes outside the current expected scope, it should record the newly discovered files and note the scope expansion in the bead handoff summary.

### 4. Scheduler Conflict Detection

Before starting a ready bead, the scheduler should compare its expected or known file scope against all active beads.

The scheduler should:

- allow execution when there is no overlap
- defer execution when overlap exists
- record a clear block or defer reason that identifies the conflicting active bead

The scheduler should use the best available scope for each bead in this order:

1. `touched_files`
2. `expected_files`
3. `expected_globs`

If a bead has no scope information at all, the scheduler should treat it conservatively and avoid running it in parallel with another unsafely scoped implementation bead in the same epic.

### 5. CLI Support

The CLI should make conflict state visible.

Required capabilities:

- show file claims on `bead show`
- include conflict/block reasons on blocked beads
- support a way to inspect active file claims across running beads

This can be a new command or an extension of existing commands.

### 6. Handoff Integrity

Every handoff between specialized agents should preserve:

- the latest touched files
- any updated expected scope
- any known conflict risks for downstream agents

The review agent should confirm that the final bead state reflects the actual changed file set before the bead is closed.

## Non-Functional Requirements

- scheduler decisions must remain deterministic
- conflict detection must be explainable from bead state alone
- the feature must remain Git-native and repository-backed
- behavior should degrade safely when scope data is incomplete

## Acceptance Criteria

The feature is complete when all of the following are true:

1. Two ready developer beads with overlapping claimed files are not started at the same time.
2. Two ready beads with non-overlapping claims can run in parallel when worker capacity allows.
3. A bead with no scope information is handled conservatively rather than scheduled unsafely.
4. Agents can persist touched files and scope updates into bead state during execution.
5. `bead show` exposes the scope fields and conflict reasons clearly.
6. Tests cover overlapping claims, non-overlapping claims, missing scope, and scope updates during handoff.

## Suggested Implementation Notes

- keep the implementation repository-backed; do not introduce a database
- prefer extending the existing bead schema instead of creating a separate locking system
- keep conflict checks in the scheduler so the behavior stays centralized and deterministic
- represent conflict reasons in a way that humans and agents can both consume easily

## Example Scenario

Given these two ready beads:

- Bead A: expected files `src/codex_orchestrator/scheduler.py`
- Bead B: expected files `src/codex_orchestrator/scheduler.py`

The scheduler should start only one of them and defer the other with a reason referencing the conflicting bead.

Given these two ready beads:

- Bead C: expected files `src/codex_orchestrator/planner.py`
- Bead D: expected files `src/codex_orchestrator/storage.py`

The scheduler may run both in parallel if worker capacity allows.

## Deliverables

- updated bead schema with scope fields
- planner output support for expected file scope
- scheduler conflict detection and defer/block logic
- CLI visibility for claims and conflicts
- automated tests covering the new behavior
