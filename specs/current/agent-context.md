# agent-context

## Requirement: Documents referenced by ID
<!-- source: spec-896c0f07 -->
`takt plan` SHALL store specs and ADRs in `linked_docs` as `spec:<id>` / `adr:<id>` references and MUST add the planned spec's reference to the feature root and every planned bead; worker prompts MUST resolve each reference to the document's title, status and a path readable from the bead's workspace.

### Scenario: Planned beads reference the spec by ID
- **WHEN** `takt plan --from-file` persists a plan whose `spec_path` is a spec with `id: spec-aaaa1111` (file `specs/planned/x.md`) and whose beads list `specs/drafts/x.md` in `linked_docs`
- **THEN** every persisted bead (epic, feature root and children) has `spec:spec-aaaa1111` in `linked_docs` and no `specs/drafts/x.md` entry

### Scenario: Reference resolves after the spec moved
- **WHEN** a bead links `spec:spec-aaaa1111`, the spec is in `specs/planned/` in the worktree, and the worker prompt is built
- **THEN** the Linked documents block shows `spec-aaaa1111`, its title, `[planned]` and `specs/planned/x.md`

### Scenario: Legacy path entry still resolves
- **WHEN** an existing bead links `specs/drafts/x.md`, which no longer exists, and `specs/done/x.md` has the same filename
- **THEN** the Linked documents block resolves it to the spec in `specs/done/x.md`

### Scenario: ADR only on main is reachable
- **WHEN** a bead links `adr:ADR-bbbb2222`, which exists in the main checkout but not in the worktree
- **THEN** the Linked documents block shows its absolute main-checkout path marked `(from main checkout)`

### Scenario: Audit resolves references
- **WHEN** the feature audit is built for a bead whose `linked_docs` contains `spec:spec-aaaa1111`
- **THEN** the audit entry for that link has the spec's current path, title and status rather than an unresolved result

## Requirement: Approved ADRs pushed to agents
<!-- source: spec-896c0f07 -->
Planner prompts and worker prompts (except `recovery`) SHALL include a one-line index of the main checkout's approved ADRs, and worker prompts MUST include the full text of up to 3 approved ADRs linked from the bead.

### Scenario: Worker sees the ADR index
- **WHEN** the main checkout has approved ADRs `ADR-11111111`, `ADR-22222222` and a superseded `ADR-33333333`, and a developer prompt is built
- **THEN** the prompt lists `ADR-11111111` and `ADR-22222222` under the approved-ADR heading and does not list `ADR-33333333`

### Scenario: Linked ADR text is included
- **WHEN** a bead links `adr:ADR-11111111` and its prompt is built
- **THEN** the prompt contains ADR-11111111's Decision section text

### Scenario: Planner is told to link ADRs
- **WHEN** `build_planner_prompt` is called for a repository with approved ADRs
- **THEN** the prompt contains the ADR index and the instruction to add `adr:<id>` references to `linked_docs`

### Scenario: Recovery prompt omits context blocks
- **WHEN** a recovery bead's prompt is built for a bead that links a spec and an ADR
- **THEN** it contains no Linked documents block, no ADR text, no ADR index and no lookup instructions

## Requirement: Reviewer enforces approved ADRs
<!-- source: spec-896c0f07 -->
The review prompt SHALL instruct the reviewer that a change contradicting an approved ADR, without a superseding ADR or spec, is a finding requiring verdict `needs_changes`, and this instruction MUST come from the prompt builder so it applies even when guardrail templates are user-owned.

### Scenario: Review prompt carries the ADR rule
- **WHEN** a review bead's prompt is built with a guardrail template that does not mention ADRs
- **THEN** the prompt still contains the ADR-conflict rule and the `ADR conflict:` reporting format

## Requirement: Discovered issues become untriaged beads
<!-- source: spec-896c0f07 -->
Worker handoffs SHALL carry a `discovered_issues` list, and the scheduler MUST turn each entry into a standalone bead with status `open`, label `discovered` and `discovered_issue_of` metadata, for every agent type, without dispatching it.

### Scenario: Corrective bead reports an unrelated issue
- **WHEN** a corrective developer bead completes with one `discovered_issues` entry titled `Fix flaky lock test`
- **THEN** a new root bead with that title exists with status `open`, label `discovered`, `metadata.discovered_issue_of` set to the corrective bead's ID, and an `issue_discovered` event is logged

### Scenario: Defect issue gets defect type
- **WHEN** a review bead reports a `discovered_issues` entry with `agent_type` `defect`
- **THEN** the created bead has `agent_type` `defect` and `bead_type` `defect`

### Scenario: Discovered beads are not dispatched
- **WHEN** the scheduler runs a cycle after a discovered bead was created
- **THEN** the discovered bead is still `open` and was not started

### Scenario: Blocked review still reports issues
- **WHEN** a review bead finishes with verdict `needs_changes` and one `discovered_issues` entry
- **THEN** the discovered bead is created

### Scenario: Retried bead does not duplicate
- **WHEN** a bead reports `discovered_issues` entry `Fix flaky lock test`, is retried, and reports ` fix flaky lock test ` again
- **THEN** exactly one discovered bead with that `discovered_issue_of` exists

### Scenario: Recovery creates discovered beads once
- **WHEN** a recovery bead's handoff with one `discovered_issues` entry is applied to the original bead
- **THEN** exactly one discovered bead exists, with `discovered_issue_of` set to the original bead's ID

### Scenario: Older handoffs without the field
- **WHEN** a handoff without `discovered_issues` is finalized
- **THEN** no bead is created and no error occurs
