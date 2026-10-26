---
name: Living Specs and Requirement Deltas
id: spec-ea87bef7
description: "Structured requirement deltas in specs, merged into per-capability living specs under specs/current/ on done"
dependencies: null
priority: medium
complexity: null
status: draft
tags:
- specs
- spec-py
- tester
- openspec-concepts
scope:
  in: null
  out: null
feature_root_id: null
---
# Living Specs and Requirement Deltas

## Objective

Give takt projects a maintained record of how the system behaves *now*, built up from completed specs. Each spec gains a structured `## Requirements` section that states its behavioural changes as deltas (ADDED / MODIFIED / REMOVED requirements, each with WHEN/THEN scenarios). When a spec moves to `done`, `spec.py` merges those deltas into per-capability files under `specs/current/`. The concepts come from OpenSpec (living specs, delta specs, archive-merge); the tooling stays takt's own: stdlib-only `spec.py`, no Node dependency, no second task tracker alongside beads.

## Problems to Fix

1. **Nothing records current behaviour.** Specs are forward-looking and go stale once they reach `specs/done/`. To learn how a capability behaves today, an agent or operator has to read code or reconstruct it from a series of done specs that may contradict each other. ADRs record decisions, not behaviour.
2. **Acceptance criteria are free-form and often untested.** The template's `## Acceptance Criteria` section has no structure, so criteria vary from "the CLI prints X" to "works correctly". Nothing ties a criterion to a test, and tester agents can, and do, skip whole categories of behaviour (for example UI flows) without any check noticing.
3. **Specs can't be checked structurally.** `spec.py` manages frontmatter and lifecycle but never looks at the body. The spec-reviewer agent judges quality, but no tool can say "this spec is well-formed" or "this spec modifies a requirement that doesn't exist".
4. **The `spec.py` copies have drifted.** Five copies are in the repo, in three different versions:
   - `.claude/skills/skill-spec-management/spec.py` and `.agents/skills/skill-spec-management/spec.py` are identical and have `show --full` and `show --body-only`.
   - `src/agent_takt/_data/claude_skills/skill-spec-management/spec.py` (shipped by `takt init`/`upgrade`) has `--full` but not `--body-only`.
   - `src/agent_takt/_data/agents_skills/task/spec-management/spec.py` and `.agents/skills/task/spec-management/spec.py` (Codex) have neither, and use `_require_lifecycle_dirs()` in `cmd_create` instead of creating the dirs.

   `spec.py` has no unit tests. Adding features on top of three different versions would make the drift worse.

## Changes

### 0. Reconcile `spec.py` before changing it

- Make the packaged Claude copy (`src/agent_takt/_data/claude_skills/skill-spec-management/spec.py`) the canonical source. First bring it up to the superset of current behaviour, i.e. the `.claude/skills/...` version with both `show --full` and `show --body-only`.
- Make `src/agent_takt/_data/agents_skills/task/spec-management/spec.py` byte-identical to the canonical copy.
- Make the three repo-local copies (`.claude/skills/skill-spec-management/spec.py`, `.agents/skills/skill-spec-management/spec.py`, `.agents/skills/task/spec-management/spec.py`) byte-identical to it as well.
- Add a test that fails when the packaged copies differ (see Acceptance Criteria).

All later changes are made to the canonical copy and propagated to the other four.

### 1. Requirements section format (spec delta)

Add a `## Requirements` section to `SPEC_BODY_TEMPLATE` in `spec.py`, between `## Changes` and `## Files to Modify`. Each entry is a level-3 heading of the form:

```
### <OP>: <capability> / <requirement name>
```

- `<OP>` is exactly one of `ADDED`, `MODIFIED`, `REMOVED`.
- `<capability>` is a kebab-case slug matching `^[a-z0-9]+(-[a-z0-9]+)*$` (e.g. `bead-lifecycle`, `tui-tree-panel`, `ui-checkout`). It names the living spec file `specs/current/<capability>.md`.
- `<requirement name>` is free text, unique within its capability, compared case-insensitively after trimming whitespace.

Entry bodies:

```markdown
## Requirements

### ADDED: bead-lifecycle / Recovery bead on missing structured output
The scheduler SHALL create a `{bead_id}-recovery` bead when a worker
finishes without structured output.

#### Scenario: Developer bead returns prose only
- **WHEN** a developer bead's runner output contains no parseable JSON
- **THEN** a bead `<id>-recovery` with `agent_type=recovery` is created
- **AND** the original bead's `recovery_for` link is set on the recovery bead

### MODIFIED: bead-lifecycle / Corrective retry limit
The scheduler SHALL create at most `scheduler.max_corrective_attempts`
corrective beads per parent bead.

#### Scenario: Limit reached
- **WHEN** a bead has failed transiently `max_corrective_attempts` times
- **THEN** no further corrective bead is created and the bead is `blocked`

### REMOVED: bead-lifecycle / Manual retry required after parse failure
Reason: superseded by automatic recovery beads.
```

Structural rules (checked by `spec.py validate`, change 2):

- **ADDED and MODIFIED** entries must contain:
  - at least one normative sentence using `SHALL` or `MUST` (uppercase);
  - at least one `#### Scenario: <name>` sub-heading;
  - in every scenario, at least one `- **WHEN**` line and one `- **THEN**` line. `- **AND**` lines are optional.
- **MODIFIED** entries carry the *complete* replacement text and scenarios for the requirement, not a diff.
- **REMOVED** entries must contain a line starting with `Reason:` and must not contain scenarios.
- Renames are written as a REMOVED entry plus an ADDED entry. There is no RENAMED operation.
- The `## Requirements` section is **optional**. A spec without one is valid, and its merge is a no-op (see Pending Decision 1).

### 2. `spec.py validate <spec>`

New subcommand that checks one spec's `## Requirements` section:

- every `###` heading under `## Requirements` matches the entry format above;
- every entry body meets the structural rules above;
- there are no duplicate `<capability> / <requirement name>` pairs within the spec;
- each entry is consistent with `specs/current/`:
  - `ADDED` fails if the requirement already exists in `specs/current/<capability>.md`;
  - `MODIFIED` and `REMOVED` fail if the capability file or the requirement doesn't exist.

Behaviour:

- Prints `OK: <path> (<n> requirement deltas)` and exits 0 when valid.
- Prints `OK: <path> (no Requirements section)` and exits 0 when the section is absent.
- Otherwise prints one line per error, `error: <path>: <heading or line>: <message>`, to stderr and exits 1.
- Reads files only; `validate` writes nothing.

### 3. Living spec files under `specs/current/`

One file per capability at `specs/current/<capability>.md`:

```markdown
# bead-lifecycle

## Requirement: Recovery bead on missing structured output
<!-- source: spec-ea87bef7 -->
The scheduler SHALL create a `{bead_id}-recovery` bead when a worker
finishes without structured output.

### Scenario: Developer bead returns prose only
- **WHEN** ...
- **THEN** ...
```

- Capability files have no frontmatter. The level-1 heading is the capability slug.
- Inside a capability file, requirements are level-2 (`## Requirement: <name>`) and scenarios are level-3 (`### Scenario: <name>`). When merging, `spec.py` shifts headings up one level from the delta format.
- The `<!-- source: spec-<id> -->` line records the spec that last ADDED or MODIFIED the requirement.
- Requirements appear in the order they were added. MODIFIED replaces a requirement in place, and REMOVED deletes the whole block (heading through the line before the next `## Requirement:` or end of file).
- `specs/current/` is reserved:
  - `find_all_specs()` (spec.py:423) skips it, so `list`, `show`, `resolve_spec` and `set` never treat capability files as specs;
  - `set status current` is rejected with `error: "current" is a reserved status name`.

### 4. Merge on transition to `done`

`spec.py set status done <spec>` changes only when the spec's current status is *not* already `done`:

1. Run the same checks as `validate`. If any fail, print the errors, exit 1, and change nothing: the spec stays where it is and no capability file is touched.
2. Apply every delta to an in-memory copy of the affected capability files, in the order the entries appear in the spec. ADDED appends (creating `specs/current/<capability>.md` if needed), MODIFIED replaces, REMOVED deletes.
3. Write all affected capability files, then update the spec's frontmatter and move it to `specs/done/`, as today.
4. Print one line per delta, `merged: <OP> <capability> / <requirement name>`, then the new spec path.

Further rules:

- If a REMOVED delta leaves a capability file with no requirements, the file is deleted.
- Running `set status done` on a spec that is already `done` keeps today's behaviour (frontmatter rewrite, no move) and does **not** merge again.
- Transitions to any status other than `done` never touch `specs/current/`.

The operator workflow is unchanged ("Moving a Spec to Done" in CLAUDE.md): after `takt merge`, run `set status done`, then `git add specs/` picks up both the moved spec and the updated capability files in the same commit.

### 5. Scenarios flow into tester work

- `templates/skills/task/spec-intake/SKILL.md`: when a spec has a `## Requirements` section, the planner copies every ADDED/MODIFIED scenario (name + WHEN/THEN/AND lines, verbatim) into the shared tester bead's acceptance criteria, grouped by requirement.
- `templates/agents/tester.md`: add an allowed-action rule: "Every scenario listed in the bead's acceptance criteria requires at least one automated test that exercises it. In `completed`, list each scenario name with the test file and test name covering it. If a scenario cannot be automated within tester scope, list it in `remaining` with the reason and return `verdict=needs_changes`."
- Change only this rule. The tester's targeted-test rule ("never run the full suite") stays as it is. Running the tests that cover the listed scenarios counts as targeted.

### 6. Spec reviewer

In `spec-reviewer.md`, extend the **Verifiability** dimension: when a `## Requirements` section is present, the reviewer checks that each scenario's WHEN is a concrete trigger and each THEN is an observable outcome (output, state, file, UI element), and flags scenarios that can't be tested as written. Structural correctness is left to `spec.py validate`; the reviewer still receives spec text only and runs no commands.

### 7. Documentation

- `SKILL.md` for the spec-management skill (packaged and repo-local copies): document the Requirements format, `validate`, merge-on-done, and the reserved `specs/current/` folder.
- `specs/HOWTO.md`: add the Requirements section to the structure example.
- CLAUDE.md and `src/agent_takt/_data/claude_skills/takt/SKILL.md`, "Moving a Spec to Done": add that `set status done` merges requirement deltas into `specs/current/` and fails without moving the spec if they're invalid, and that `specs/current/` changes are committed together with the spec move.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/_data/claude_skills/skill-spec-management/spec.py` | Canonical copy: reconcile to superset (change 0); Requirements template; `validate` subcommand; `specs/current/` exclusion and reserved status; merge on `set status done` |
| `src/agent_takt/_data/agents_skills/task/spec-management/spec.py` | Byte-identical copy of the canonical `spec.py` |
| `.claude/skills/skill-spec-management/spec.py`, `.agents/skills/skill-spec-management/spec.py`, `.agents/skills/task/spec-management/spec.py` | Byte-identical copies of the canonical `spec.py` |
| `src/agent_takt/_data/claude_skills/skill-spec-management/SKILL.md`, `src/agent_takt/_data/agents_skills/task/spec-management/SKILL.md`, and their repo-local copies | Document Requirements format, `validate`, merge-on-done, reserved folder |
| `templates/skills/task/spec-intake/SKILL.md` + `src/agent_takt/_data/templates/skills/task/spec-intake/SKILL.md` | Copy scenarios into shared tester bead acceptance criteria |
| `templates/agents/tester.md` + `src/agent_takt/_data/templates/agents/tester.md` | Scenario-coverage rule |
| `.claude/agents/spec-reviewer.md` + `src/agent_takt/_data/claude_agents/spec-reviewer.md` (+ `.codex/agents/spec-reviewer.toml`, `.github/agents/spec-reviewer.agent.md`) | Verifiability check for scenarios |
| `specs/HOWTO.md` | Requirements section in structure example |
| `CLAUDE.md`, `src/agent_takt/_data/claude_skills/takt/SKILL.md` | "Moving a Spec to Done" notes |
| `tests/test_spec_py.py` (new) | Unit tests for `spec.py` (see Acceptance Criteria) |

## Acceptance Criteria

- `src/agent_takt/_data/claude_skills/skill-spec-management/spec.py`, `src/agent_takt/_data/agents_skills/task/spec-management/spec.py` and the three repo-local copies are byte-identical. `tests/test_spec_py.py` asserts the two packaged copies are identical.
- The canonical `spec.py` supports `show --full` and `show --body-only`.
- `spec.py create` produces a body containing a `## Requirements` section between `## Changes` and `## Files to Modify`.
- `spec.py validate` exits 0 on:
  - a spec with no Requirements section;
  - a spec whose ADDED/MODIFIED/REMOVED entries are well-formed and consistent with `specs/current/`.
- `spec.py validate` exits 1 with an `error:` line naming the offending heading for each of:
  - a malformed heading;
  - a non-kebab-case capability;
  - an ADDED/MODIFIED entry without SHALL/MUST;
  - an ADDED/MODIFIED entry with no scenario;
  - a scenario missing WHEN or THEN;
  - a REMOVED entry without `Reason:`;
  - a duplicate requirement within the spec;
  - ADDED for an existing requirement;
  - MODIFIED or REMOVED for a missing requirement.
- `spec.py validate` never creates or modifies files.
- `spec.py set status done` on a valid draft or planned spec:
  - creates or updates the right `specs/current/<capability>.md` files, using level-2 requirement and level-3 scenario headings and a `<!-- source: spec-<id> -->` line;
  - moves the spec to `specs/done/`;
  - prints one `merged:` line per delta.
- On an invalid spec, `spec.py set status done` exits 1, leaves the spec in its original folder with its original status, and leaves every file under `specs/current/` byte-for-byte unchanged.
- MODIFIED replaces the requirement block in place (other requirements keep their order). REMOVED deletes the block, and deletes the capability file if it ends up empty.
- Running `set status done` twice on the same spec merges once; the second run leaves `specs/current/` unchanged.
- `spec.py list` and `spec.py show` never list or match files under `specs/current/`, and `spec.py set status current <spec>` exits 1 with the reserved-name error.
- Specs already in `specs/done/` and drafts without a Requirements section keep working with every existing subcommand unchanged.
- The spec-intake skill, tester guardrail (packaged and repo copies) and spec-reviewer agent contain the scenario rules from changes 5 and 6. The tester guardrail still contains its targeted-test and no-full-suite rules.
- `tests/test_spec_py.py` covers every `validate` and merge criterion above by running the canonical `spec.py` as a subprocess in a temporary directory.
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

1. **Should `## Requirements` be mandatory?** Recommendation: optional for now, so existing drafts and specs with no behavioural content (refactors, tooling) don't break. Revisit once a few specs have used it. If it becomes mandatory later, `validate` gains a `--require-requirements` flag rather than changing default behaviour.
2. **Two specs modifying the same requirement at once.** If spec A and spec B both MODIFY the same requirement, whichever moves to `done` last silently overwrites the other. Recommendation: accept last-writer-wins for v1. The `source:` comment makes it auditable, and specs are reviewed before planning. A later version could add an optional `Base: spec-<id>` line to MODIFIED entries and fail the merge when the current `source:` differs.
3. **Location of living specs.** Options: `specs/current/` (recommended; keeps everything spec-related under `specs/` and needs the exclusion in change 3) or a separate top-level folder such as `capabilities/` (no exclusion needed, but a second root to document).
4. **Back-filling capability files for existing behaviour.** Recommendation: out of scope. `specs/current/` fills in as new specs complete. Operators can add hand-written capability files at any time, as long as they follow the format in change 3.
5. **Should agents load `specs/current/` automatically?** E.g. the planner reading the relevant capability files when decomposing a spec. Recommendation: out of scope for this spec, consistent with how ADRs are handled today. Agents can be pointed at capability files explicitly in a bead description.
