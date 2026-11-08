---
name: "Agent context: specs and ADRs by ID, ADR index and discovered issues"
id: spec-896c0f07
description: Push linked specs/ADRs (by ID) and an approved-ADR index into agent prompts, make reviewers enforce ADRs, and replace takt bead create with discovered_issues
dependencies: null
priority: high
complexity: null
status: draft
tags:
- agents
- prompts
- adr
- specs
- scheduler
scope:
  in: null
  out: null
feature_root_id: null
---
# Agent context: specs and ADRs by ID, ADR index and discovered issues

## Objective

Planner and worker agents do not reliably see the specs and ADRs that govern their work. Beads reference their spec by a path recorded at plan time (`specs/drafts/...`) that no longer exists once the spec moves folders; approved ADRs are never shown to agents, so nothing stops a bead from quietly undoing a recorded decision; and the guardrails tell agents to file unrelated issues with `takt bead create`, which inside a feature worktree writes to a throwaway `.takt/` and is lost. This spec makes takt **push** the binding context into prompts — linked specs and ADRs referenced by stable ID and resolved to a readable path when the prompt is built, plus a one-line index of all approved ADRs — tells agents how to **pull** more (`takt spec|adr show|search`, which already work from worktrees), makes the reviewer check changes against approved ADRs, and replaces `takt bead create` with a `discovered_issues` handoff field that the scheduler turns into standalone, untriaged beads.

## Problems to Fix

1. **Spec links are stale and anonymous.** The planner only receives the spec text, so the `linked_docs` paths it emits are guesses (e.g. `specs/drafts/scheduler-run-lifecycle-events.md` on every bead of feature B-f7558440); the spec moves to `specs/planned/` before the branch is created. `storage.resolve_linked_doc_path` (`storage.py`) finds moved files by a unique basename `rglob`, so the content is usually found, but: the stale path is what appears in the prompt's JSON payload (`prompts.py`), `render_context_snippets` shows only the file name for paths outside the worktree, and agents never see the document's ID, title or status.
2. **ADRs are invisible to agents.** `build_planner_prompt` (`src/agent_takt/prompts.py`) receives only the spec text and `build_worker_prompt` renders only bead fields, guardrails and dependency handoffs. Nothing mentions `adr/approved/`. The reviewer guardrail only suggests *writing* new ADRs.
3. **Lookup guidance is not guaranteed to reach agents.** `takt spec show/search` and `takt adr show/search` work from a worktree (search uses the main index via `document_index_db`). takt's own guardrail templates mention the search commands, but the prompt builder never does, and guardrail templates are user-owned in existing projects (Problem 5), so the guidance cannot be relied on.
4. **Discovered issues are lost or misplaced.** `templates/agents/developer.md` (corrective scope) and `templates/agents/defect.md` tell agents to run `takt bead create`, which inside a worktree writes to the worktree's own `.takt/beads/` (gitignored, never read by the scheduler). The existing `new_beads` handoff field is only honoured for non-corrective developer beads (`_create_followups` in `scheduler/followups.py` returns early for defect, corrective, tester, review and other types) and creates **ready** children inside the same feature tree, which is wrong for unrelated issues.
5. **Guardrail templates are user-owned in projects**, so guardrail-only instructions never reach existing projects after `takt upgrade`; essential instructions must live in the prompt builder.

## Changes

### 1. Document references by ID

- A **document reference** is a string `spec:<id>` or `adr:<id>` (e.g. `spec:spec-1cdb1b8a`, `adr:ADR-44e7453e`). Any other `linked_docs` entry is a plain path, as today.
- New module `src/agent_takt/doc_refs.py`:
  - `parse_ref(entry) -> DocRef | None` (`DocRef(kind, id)`).
  - `normalize_linked_docs(entries, root) -> list[str]`: converts entries that are paths to a spec or ADR file under `root`, or that name a missing spec/ADR file whose **exact filename stem** matches exactly one document, into references. It matches over `get_store(root, "spec"|"adr").load_all()` itself (it does not use `store.resolve()`, whose ADR resolution is ID-prefix only and whose spec resolution is substring-based). Ambiguous or unmatched entries are left unchanged. De-duplicates preserving order.
  - `resolve_ref(ref, workspace_root, main_root) -> ResolvedDoc(ref, id, title, status, path, source)`: resolve with `get_store(workspace_root, kind).resolve(id)` first (`source="worktree"`, path relative to the worktree); if not found there, with `main_root` (`source="main"`, absolute path); else `source="missing"`.
- `takt plan` (`--write` and `--from-file`, in `PlanningService.persist_plan` or its caller) passes every persisted bead's `linked_docs` (epic, feature root and children) through `normalize_linked_docs(…, storage.root)`, and **adds `spec:<id>` of the planned spec to every persisted bead** when the spec has frontmatter `id`. The spec path comes from the plan command (`--write`) or the plan JSON `spec_path` (`--from-file`); if `spec_path` is absent or unreadable, the reference is skipped with the same kind of notice the coverage check prints, not an error.
- Existing beads are not migrated; resolution at prompt time (§2) handles their path entries too: an entry that is a path to a missing spec/ADR file is resolved via `normalize_linked_docs` semantics on the fly.
- **Other `linked_docs` consumers resolve references:** `storage.linked_context_paths` maps `spec:`/`adr:` refs to the resolved file path (worktree first, then main) instead of passing them to `resolve_linked_doc_path`; `audit._resolve_linked_docs` (`src/agent_takt/audit.py`) keeps the `{recorded, current}` entry shape (`current` = the resolved path) and adds `id`, `title` and `status` for references (absent for plain paths); the markdown renderer (`_fmt_linked_doc`) shows `<id> <title> [<status>] → <path>` for references; `takt bead show`, the CLI and the TUI keep displaying the raw entries.

### 2. Context pushed into prompts

`build_worker_prompt` (and the planner prompt where stated) gain the blocks below, built from `workspace_root` = the runner's `workdir` (the agent's cwd: `.takt/agent-runs/<id>/repo`, a symlink to the feature worktree, or the main root when there is no worktree) and `main_root = paths.main_checkout_root(workspace_root)` (resolves the symlink). Worktree paths are shown relative to `workspace_root`. Blocks are omitted when empty, and **none of the four blocks is rendered for `recovery` beads** (recovery runs without tools). The Linked documents block is the authoritative display of `linked_docs`; the raw `linked_docs` JSON in the payload and the existing "Available repository context files" section stay unchanged. All of this is rendered by `prompts.py`, not guardrail templates, so it reaches projects whose guardrails are user-owned.

1. **Linked documents** — one line per `linked_docs` entry:
   ```
   Linked documents:
   - spec-1cdb1b8a  Scheduler run lifecycle events [planned] → specs/planned/scheduler-run-lifecycle-events.md
   - ADR-44e7453e   Bead state never travels on feature branches [approved] → adr/approved/adr-44e7453e-....md
   - docs/events.md
   ```
   `source="main"` entries show the absolute path and the suffix `(from main checkout)`; `source="missing"` entries show `(not found)`.
2. **Linked ADR text** — the full body (without frontmatter) of every ADR referenced in `linked_docs` whose status **in the main checkout** is `approved`, at most 3 (in `linked_docs` order); further linked ADRs are listed only. The text is taken from the main checkout's copy (main is authoritative for which decisions bind). Each under a heading `ADR-xxxx — <title>`. Block 1 shows the copy it resolved (worktree first), so its status may differ from main's; that is expected.
3. **Approved ADR index** — one line per approved ADR in the **main checkout** (`get_store(main_root, "adr").list(status="approved")`), one line each `ADR-xxxx — <title> — <description truncated to 160 chars>` under the heading `Approved architecture decisions (binding; read with $TAKT_CMD adr show <id>):`. If there are more than 60, keep the 60 with the latest `accepted_at` (missing `accepted_at` sorts oldest) and add `… and <n> more: $TAKT_CMD adr list --status approved`; the kept lines are displayed sorted by ID. Not rendered for `recovery` beads.
4. **Lookup instructions** — a fixed short block for all worker types except `recovery`:
   ```
   Looking things up: `$TAKT_CMD spec show <id>` / `$TAKT_CMD adr show <id>` read the copy in your worktree;
   `$TAKT_CMD spec search "<query>"` / `$TAKT_CMD adr search "<query>"` search the main checkout's index.
   ```

The **planner prompt** (`build_planner_prompt(spec_text, *, root=None, spec_id=None)`; the `AgentRunner.propose_plan` ABC and both runners become `propose_plan(spec_text, *, root: Path | None = None, spec_id: str | None = None)`, defaults keep today's prompt; `PlanningService.propose` passes the storage root and the spec's frontmatter `id`; `FakeRunner.propose_plan` in `tests/helpers.py` accepts and ignores the new keywords) includes the ADR index (block 3) and the instruction: *"For each bead, add `adr:<id>` to `linked_docs` for every approved ADR that constrains that bead's work, and never plan work that contradicts an approved ADR unless the spec explicitly says it supersedes it. Use `spec:<id>` references, not file paths, for specs and ADRs."*

### 3. Reviewer checks against ADRs

- `render_agent_output_requirements("review")` (prompt builder, so it applies regardless of guardrail ownership) adds: *"Check the change against every linked ADR and the approved ADR index. A change that contradicts an approved ADR is a finding and the verdict must be `needs_changes`, unless the bead's spec explicitly supersedes that ADR (name the superseding ADR or spec). Report each such conflict in `risks` prefixed `ADR conflict: ADR-xxxx — `."*
- `templates/agents/review.md` and `templates/skills/role/reviewer-signoff/SKILL.md` (+ `_data` copies) get the same rule in their own words; the existing non-blocking "suggest a new ADR" hint stays.

### 4. Discovered issues

- **Both copies** of `AGENT_OUTPUT_SCHEMA` (`src/agent_takt/prompts.py` and `src/agent_takt/runner.py`; `tests/test_recovery_flow.py` asserts they match) gain a required array `discovered_issues` (items: `title` string, `description` string, `agent_type` enum `developer|defect`, `expected_files` array of strings; all required). Item properties are all required and have no defaults (strict structured output marks every property required). Workers return `[]` when there is nothing to report. `AgentRunResult` (`models.py`) gains the field with default `[]` (so `_payload_to_run_result`'s `AgentRunResult(**payload)` accepts it); it is **not** added to `HandoffSummary`, so it never appears in later prompts or dependency context. Missing in older payloads ⇒ `[]`.
- `render_agent_output_requirements` (all worker types except `recovery`) instructs: *"If you notice an unrelated problem, do not fix it and do not run `takt bead create`; describe it in `discovered_issues`. The operator triages these."*
- At finalize, for **every outcome** (completed, blocked, `needs_changes`, verdict-overridden — i.e. before the early blocked branch in `scheduler/finalize.py`) and any agent type (developer incl. corrective, defect, tester, review, documentation), the scheduler creates one bead per entry via `storage.create_bead` with: a new root bead ID (no `parent_id`, no feature root other than itself), `status="open"` (never dispatched until an operator marks it ready), `bead_type` `defect` when `agent_type` is `defect` (else `task`), label `discovered`, `title`/`description`/`agent_type`/`expected_files` copied from the entry, `linked_docs` inherited from the reporting bead, no priority, and `metadata` `{discovered_issue_of: <reporting bead_id>, discovered_in_feature: <feature_root_id>}` (`discovered_by` is already used for `new_beads` children and is not reused). Entries with an empty title are ignored. **De-duplication:** an entry is skipped when a bead labelled `discovered` with the same `discovered_issue_of` and the same title (case-insensitive, whitespace-trimmed) already exists, so retries and repeated reports create one bead. Each creation appends a `discovered_issue_created` execution-history entry on the reporting bead and an `issue_discovered` event to `.takt/logs/events.jsonl` (`bead_id` of the new bead, `discovered_issue_of`, `feature_root_id`, `title`, `agent_type`), documented in `docs/events.md`.
- Guardrails: replace every `takt bead create` instruction in `templates/agents/developer.md` and `templates/agents/defect.md` (+ `_data` copies) with the `discovered_issues` instruction. `new_beads` keeps its current meaning (in-scope sub-work for developer beads).
- The scheduler already never dispatches `open` beads (`storage.ready_beads()` selects only `ready`; operators move `open → ready` via `takt bead update`). No code change; add a regression test.
- **Recovery:** the recovery prompt (`build_recovery_prompt`, which embeds the schema) instructs the recovery agent to copy any unrelated issues mentioned in the prose output into `discovered_issues`, else `[]`. Discovered beads are created **once**, when the recovery handoff is applied to the original bead in `_handle_recovery_completion` (with `discovered_issue_of` = the original bead), never when the recovery bead itself finalizes.

## Requirements

### ADDED: agent-context / Documents referenced by ID
`takt plan` SHALL store specs and ADRs in `linked_docs` as `spec:<id>` / `adr:<id>` references and MUST add the planned spec's reference to the feature root and every planned bead; worker prompts MUST resolve each reference to the document's title, status and a path readable from the bead's workspace.

#### Scenario: Planned beads reference the spec by ID
- **WHEN** `takt plan --from-file` persists a plan whose `spec_path` is a spec with `id: spec-aaaa1111` (file `specs/planned/x.md`) and whose beads list `specs/drafts/x.md` in `linked_docs`
- **THEN** every persisted bead (epic, feature root and children) has `spec:spec-aaaa1111` in `linked_docs` and no `specs/drafts/x.md` entry

#### Scenario: Reference resolves after the spec moved
- **WHEN** a bead links `spec:spec-aaaa1111`, the spec is in `specs/planned/` in the worktree, and the worker prompt is built
- **THEN** the Linked documents block shows `spec-aaaa1111`, its title, `[planned]` and `specs/planned/x.md`

#### Scenario: Legacy path entry still resolves
- **WHEN** an existing bead links `specs/drafts/x.md`, which no longer exists, and `specs/done/x.md` has the same filename
- **THEN** the Linked documents block resolves it to the spec in `specs/done/x.md`

#### Scenario: ADR only on main is reachable
- **WHEN** a bead links `adr:ADR-bbbb2222`, which exists in the main checkout but not in the worktree
- **THEN** the Linked documents block shows its absolute main-checkout path marked `(from main checkout)`

#### Scenario: Audit resolves references
- **WHEN** the feature audit is built for a bead whose `linked_docs` contains `spec:spec-aaaa1111`
- **THEN** the audit entry for that link has the spec's current path, title and status rather than an unresolved result

### ADDED: agent-context / Approved ADRs pushed to agents
Planner prompts and worker prompts (except `recovery`) SHALL include a one-line index of the main checkout's approved ADRs, and worker prompts MUST include the full text of up to 3 approved ADRs linked from the bead.

#### Scenario: Worker sees the ADR index
- **WHEN** the main checkout has approved ADRs `ADR-11111111`, `ADR-22222222` and a superseded `ADR-33333333`, and a developer prompt is built
- **THEN** the prompt lists `ADR-11111111` and `ADR-22222222` under the approved-ADR heading and does not list `ADR-33333333`

#### Scenario: Linked ADR text is included
- **WHEN** a bead links `adr:ADR-11111111` and its prompt is built
- **THEN** the prompt contains ADR-11111111's Decision section text

#### Scenario: Planner is told to link ADRs
- **WHEN** `build_planner_prompt` is called for a repository with approved ADRs
- **THEN** the prompt contains the ADR index and the instruction to add `adr:<id>` references to `linked_docs`

#### Scenario: Recovery prompt omits context blocks
- **WHEN** a recovery bead's prompt is built for a bead that links a spec and an ADR
- **THEN** it contains no Linked documents block, no ADR text, no ADR index and no lookup instructions

### ADDED: agent-context / Reviewer enforces approved ADRs
The review prompt SHALL instruct the reviewer that a change contradicting an approved ADR, without a superseding ADR or spec, is a finding requiring verdict `needs_changes`, and this instruction MUST come from the prompt builder so it applies even when guardrail templates are user-owned.

#### Scenario: Review prompt carries the ADR rule
- **WHEN** a review bead's prompt is built with a guardrail template that does not mention ADRs
- **THEN** the prompt still contains the ADR-conflict rule and the `ADR conflict:` reporting format

### ADDED: agent-context / Discovered issues become untriaged beads
Worker handoffs SHALL carry a `discovered_issues` list, and the scheduler MUST turn each entry into a standalone bead with status `open`, label `discovered` and `discovered_issue_of` metadata, for every agent type, without dispatching it.

#### Scenario: Corrective bead reports an unrelated issue
- **WHEN** a corrective developer bead completes with one `discovered_issues` entry titled `Fix flaky lock test`
- **THEN** a new root bead with that title exists with status `open`, label `discovered`, `metadata.discovered_issue_of` set to the corrective bead's ID, and an `issue_discovered` event is logged

#### Scenario: Defect issue gets defect type
- **WHEN** a review bead reports a `discovered_issues` entry with `agent_type` `defect`
- **THEN** the created bead has `agent_type` `defect` and `bead_type` `defect`

#### Scenario: Discovered beads are not dispatched
- **WHEN** the scheduler runs a cycle after a discovered bead was created
- **THEN** the discovered bead is still `open` and was not started

#### Scenario: Blocked review still reports issues
- **WHEN** a review bead finishes with verdict `needs_changes` and one `discovered_issues` entry
- **THEN** the discovered bead is created

#### Scenario: Retried bead does not duplicate
- **WHEN** a bead reports `discovered_issues` entry `Fix flaky lock test`, is retried, and reports ` fix flaky lock test ` again
- **THEN** exactly one discovered bead with that `discovered_issue_of` exists

#### Scenario: Recovery creates discovered beads once
- **WHEN** a recovery bead's handoff with one `discovered_issues` entry is applied to the original bead
- **THEN** exactly one discovered bead exists, with `discovered_issue_of` set to the original bead's ID

#### Scenario: Older handoffs without the field
- **WHEN** a handoff without `discovered_issues` is finalized
- **THEN** no bead is created and no error occurs

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/doc_refs.py` | NEW — parse, normalize, resolve document references |
| `src/agent_takt/prompts.py` | Linked documents, linked ADR text, ADR index, lookup blocks; planner prompt ADR index + instructions (`root`, `spec_id` params); review ADR rule and `discovered_issues` instructions in `render_agent_output_requirements`; `discovered_issues` in `AGENT_OUTPUT_SCHEMA` |
| `src/agent_takt/runner.py` | `AGENT_OUTPUT_SCHEMA` copy gains `discovered_issues`; `propose_plan(spec_text, *, root=None, spec_id=None)` in the ABC and both runners; pass what the prompt builders need |
| `src/agent_takt/storage.py` | `linked_context_paths` resolves `spec:`/`adr:` refs |
| `src/agent_takt/audit.py` | `_resolve_linked_docs` resolves refs |
| `src/agent_takt/planner.py` | `PlanningService`: read spec ID, pass `root`/`spec_id` to `propose_plan`; normalize `linked_docs` and add `spec:<id>` on persist |
| `src/agent_takt/cli/commands/misc.py` | Plan command passes spec path/ID through for `--write` and `--from-file` |
| `src/agent_takt/models.py` | `AgentRunResult.discovered_issues` (default `[]`); not on `HandoffSummary` |
| `src/agent_takt/scheduler/finalize.py` | Create discovered beads for every outcome (before the blocked branch) and in `_handle_recovery_completion`; de-duplication; history entry; event |
| `templates/agents/developer.md`, `templates/agents/defect.md`, `templates/agents/review.md` (+ `src/agent_takt/_data/templates/agents/` copies) | Replace `takt bead create`; ADR review rule |
| `templates/skills/role/reviewer-signoff/SKILL.md`, `templates/skills/core/base-orchestrator/SKILL.md` (+ `_data` copies) | ADR review rule; lookup and `discovered_issues` guidance |
| `docs/events.md` | `issue_discovered` event |
| `docs/adr.md`, `CLAUDE.md`, `docs/documents.md` | Document references by ID, pushed ADR context, reviewer rule, discovered issues; remove the "Pipeline integration is out of scope" note from `CLAUDE.md` and the "Out-of-Scope: Pipeline Integration" section of `docs/adr.md` |
| `tests/test_doc_refs.py`, `tests/test_prompts_context.py`, `tests/test_discovered_issues.py` | NEW |
| `tests/helpers.py` | `FakeRunner.propose_plan` accepts `root`/`spec_id` |
| `tests/test_recovery_flow.py`, `tests/test_prompts.py`, `tests/test_planner.py`, `tests/test_criteria_accounting.py`, `tests/test_models.py`, `tests/test_config_wiring.py` | Adjust for the new schema field, prompt blocks and `propose_plan` signature |

## Acceptance Criteria

- `linked_docs` written by `takt plan` (`--write`, `--from-file`) use `spec:`/`adr:` references for specs and ADRs and include the planned spec's reference on every persisted bead (skipped with a notice when `spec_path` is unavailable).
- `storage.linked_context_paths` and `audit._resolve_linked_docs` resolve references.
- Worker prompts render the Linked documents block with ID, title, status and a resolvable path (worktree first, then main checkout, else `(not found)`), including for legacy path entries whose file moved.
- Worker prompts (except `recovery`, which gets none of the blocks) include the approved ADR index from the main checkout (capped at 60) and lookup instructions; up to 3 linked ADRs approved in the main checkout are included in full.
- The planner prompt includes the ADR index and the instructions to link ADRs by reference and not contradict approved ADRs.
- The review prompt contains the ADR-conflict rule from `render_agent_output_requirements`, independent of guardrail template content.
- `discovered_issues` is a required field in both schema copies; entries from any agent type and any outcome become `open`, `discovered`-labelled root beads with `discovered_issue_of`/`discovered_in_feature` metadata, a history entry and an `issue_discovered` event (documented in `docs/events.md`), de-duplicated across retries; recovery creates them once on the original bead; `open` beads are never dispatched (regression test).
- No guardrail template or skill in the repo or package tells agents to run `takt bead create`.
- Neither `CLAUDE.md` nor `docs/adr.md` says pipeline integration is out of scope.
- Prompt size: the added blocks for a bead with one linked spec and no linked ADRs add no more than 6,000 characters (test with 18 fixture ADRs of realistic length).

## Pending Decisions

1. ~~Should ADR conflicts block review?~~ **Resolved: yes.** Approved ADRs are binding; a contradicting change without a superseding ADR/spec is `needs_changes`.
2. ~~Where does the ADR index come from — worktree or main?~~ **Resolved: main checkout**, so ADRs approved after a branch was created still apply; full text of linked ADRs is included only for ADRs approved in the main checkout and taken from the main checkout's copy; the Linked documents block resolves worktree-first, main fallback.
3. ~~Reuse `new_beads` for unrelated issues?~~ **Resolved: no.** `new_beads` stays in-scope sub-work inside the feature tree; `discovered_issues` creates standalone, untriaged (`open`) beads.
4. ~~Put `discovered_issues` on `HandoffSummary`?~~ **Resolved: no**, only on `AgentRunResult`, so it never leaks into later prompts.
5. ~~Migrate existing beads' `linked_docs`?~~ **Resolved: no.** Legacy path entries are resolved at prompt time.
6. ~~Out of scope:~~ per-ADR `applies_to` file patterns, recording which documents agents consulted, and pushing `specs/current/` capability files. These are follow-ups once this lands.
