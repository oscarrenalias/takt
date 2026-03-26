# Specialized Agent Guardrails

## Objective

Add explicit specialized agent guardrails so each agent type is instructed and constrained to perform only the work it is responsible for.

The system should reduce role drift by making agent responsibilities maintainable outside code, consistently injected into worker prompts, and visible in bead results when an agent is blocked for attempting out-of-scope work.

## Why This Matters

The current orchestration system already distinguishes between planner, developer, tester, documentation, and review agents, but the specialization is mostly represented as plain prompt text in Python.

That leaves too much room for agents to:

- implement code during review or documentation beads
- rewrite docs during tester beads
- broaden developer work beyond the assigned bead
- create ambiguous handoffs that do not match the intended role

If the orchestrator is going to build itself safely, each worker needs stronger role-specific guardrails than a short free-form instruction string, and those guardrails need to be editable without changing code.

## Scope

In scope:

- define file-based guardrail templates for each built-in agent type
- load those templates into worker prompts at runtime
- persist the applied guardrails in bead execution context or metadata
- allow worker results to indicate when work was blocked due to role-scope violations
- add tests for prompt generation and blocked role-violation handling

Out of scope:

- sandboxing or OS-level enforcement
- AST-based verification of whether a file edit was appropriate
- dynamic creation of entirely new agent types through the scheduler
- policy engines or external rule configuration systems beyond local template files

## B0004 Implementation Snapshot

This bead extends the earlier template-loading work with persisted guardrail metadata and explicit blocked handoff storage for role-scope violations.

Implemented behavior in the current code:

- built-in guardrails live in `templates/agents/` as Markdown files named after the fixed built-in agent types: `planner`, `developer`, `tester`, `documentation`, and `review`
- those files are the primary editable source of truth for built-in worker role boundaries; there is no separate hardcoded fallback policy for those agent types
- `src/codex_orchestrator/prompts.py` resolves template paths through `guardrail_template_path(agent_type)` and loads the file contents through `load_guardrail_template(agent_type)`
- `build_worker_prompt(...)` injects an `Agent guardrails:` section that includes both the resolved template path and the full Markdown template contents before the execution-context payload
- missing built-in templates raise `FileNotFoundError` with a path-specific message instead of silently falling back to an inline prompt string
- `src/codex_orchestrator/scheduler.py` loads the applied guardrail template before each worker run and records it through `RepositoryStorage.record_guardrail_context(...)`
- `src/codex_orchestrator/storage.py` persists guardrail state under `bead.metadata["guardrails"]` with `agent_type`, `template_path`, `template_text`, and `captured_at`
- the scheduler also persists the serialized worker prompt payload under `bead.metadata["worker_prompt_context"]` so operators can inspect the execution context that accompanied the guardrails
- applying guardrails appends a `guardrails_applied` entry to `execution_history`, and `orchestrator bead show <bead_id>` exposes both `metadata` and `execution_history` because it returns the full bead JSON
- worker results already support `outcome = "blocked"` with `summary`, `block_reason`, and `next_agent`; the scheduler now preserves those fields in both `handoff_summary` and `metadata["last_agent_result"]`
- blocked role-scope handoffs therefore remain actionable after execution: `completed`, `remaining`, `risks`, `next_action`, `next_agent`, and `block_reason` are all retained on the bead
- automated coverage verifies prompt generation, missing-template failure, guardrail metadata persistence, and blocked role-scope handoff preservation

Still tracked by the broader feature, but not delivered by this bead:

- dedicated CLI formatting for guardrail metadata beyond the raw JSON already available through `bead show`
- enforcement stronger than prompt instructions plus blocked-result reporting, such as policy validation of actual file edits

## Functional Requirements

### 1. External Agent Template Files

Each built-in agent type should have a dedicated prompt template file stored outside code.

Recommended layout:

- `templates/agents/planner.md`
- `templates/agents/developer.md`
- `templates/agents/tester.md`
- `templates/agents/documentation.md`
- `templates/agents/review.md`

Naming convention:

- filename must match `agent_type`
- format is Markdown
- one file per built-in agent type

Each template should define, in a human-editable format:

- primary responsibility
- allowed actions
- disallowed actions
- expected outputs

These files should become the primary source of truth for agent guardrails.

Initial built-in agent behavior:

- `planner`
  - may decompose specs into beads
  - must not implement code or edit runtime behavior
- `developer`
  - may implement the assigned bead and create follow-up beads for discovered work
  - must not perform final review signoff
- `tester`
  - may add or update tests and run validation
  - must not implement feature logic beyond minimal test-enablement fixes if explicitly allowed by guardrail text
- `documentation`
  - may update docs and examples relevant to the bead
  - must not change runtime feature behavior
- `review`
  - may inspect code, tests, docs, and acceptance criteria
  - must not implement feature work

### 2. Runtime Loading and Fallback

Worker prompt construction should load the template file for the current `agent_type` at runtime.

Behavior requirements:

- if the matching template exists, include its contents in the worker prompt
- include the resolved local template path in the prompt so operators can see which file supplied the guardrails
- if the template file is missing, fail safely with a clear error instead of silently dropping guardrails
- do not silently fall back to hardcoded role text for built-in agents; the template file is the policy source of truth

### 3. Prompt Injection

Worker prompts should include the loaded guardrails for the current agent in a clear, compact format.

The prompt should make it obvious that:

- the agent is only responsible for its specialization
- it should block rather than proceed if the bead requires work outside that specialization
- it should recommend the next appropriate agent when blocked for scope reasons

### 4. Role-Scope Blocking

Agent worker results should support a clear blocked outcome for role violations.

When a worker determines that the bead requires work outside its specialization, it should return:

- `outcome = "blocked"`
- a concise `summary`
- a `block_reason` explaining the role mismatch
- a recommended `next_agent`

In the implemented flow, the scheduler must also preserve the blocked handoff details on the bead itself:

- `handoff_summary.block_reason` and `handoff_summary.next_agent`
- `metadata["last_agent_result"]` with the final `outcome`, `summary`, `next_agent`, and `block_reason`
- the bead status transitioning to `blocked`

This allows the scheduler and operator to see that the failure was due to role boundaries rather than runtime failure.

### 5. Guardrail Visibility

The applied guardrails should be discoverable when inspecting the system.

Minimum visibility requirement:

- the worker prompt payload should include the loaded guardrail template content or template path
- bead metadata or execution history should preserve enough information to understand which guardrails were applied during execution

Current implementation details:

- `metadata["guardrails"]` stores the applied template path and full template text
- `metadata["worker_prompt_context"]` stores the serialized execution payload that was sent alongside the guardrails
- `execution_history` records a `guardrails_applied` event with the template path
- `orchestrator bead show <bead_id>` exposes this state because it dumps the full persisted bead JSON, including `metadata`, `handoff_summary`, `block_reason`, `status`, and `execution_history`

This does not need a separate CLI command if the information is already visible via `bead show`.

### 6. Minimal Handoff Integrity

If an agent blocks because the task belongs to another specialization, the handoff should remain actionable.

At minimum, the result should preserve:

- what the current agent was allowed to do
- why the task exceeded that scope
- which agent should take over next

## Non-Functional Requirements

- guardrails must remain deterministic and code-defined
- prompt construction should stay simple and readable
- the feature should fit the current repository-backed architecture
- the implementation should avoid introducing a large policy framework
- template loading should use the local filesystem only

## Acceptance Criteria

The feature is complete when all of the following are true:

1. Worker prompts include structured guardrails for the active agent type.
2. Guardrails are stored in external template files under a predictable folder and naming convention.
3. If an agent template file is missing, prompt construction fails with a clear error.
4. A worker can return a blocked result due to role-scope mismatch with a clear `block_reason` and `next_agent`.
5. `bead show` exposes enough information to understand the applied guardrails or template context.
6. Tests cover prompt generation for at least two agent types, template loading, missing-template failure, and blocked role-violation handling.

For the earlier B0003 slice specifically, the delivered acceptance signal was narrower:

1. `build_worker_prompt(...)` loads guardrails from `templates/agents/<agent_type>.md`.
2. The worker prompt includes both the template path and rendered Markdown body.
3. Missing built-in templates fail with a clear `FileNotFoundError`.
4. Tests cover prompt generation for at least two built-in agent types and the missing-template failure path.

For the B0004 slice, the additional delivered acceptance signal is:

1. The scheduler persists applied guardrails into bead metadata before worker execution.
2. The persisted guardrail record includes template path, template text, and captured prompt context.
3. `bead show` exposes the guardrail metadata and `guardrails_applied` execution event via the bead JSON.
4. Blocked role-scope results preserve `block_reason` and `next_agent` in the stored handoff state.
5. Tests cover guardrail metadata persistence and blocked role-scope handoff preservation.

## Suggested Implementation Notes

- replace the current flat role instruction strings with template loading from `templates/agents/`
- keep the public agent types unchanged
- prefer small extensions to existing prompt payloads and result handling over new subsystems
- store guardrail context in `metadata` unless a stronger typed field is clearly needed
- keep the template format simple Markdown rather than inventing a richer DSL in v1

## Example Scenario

Given a `review` bead that clearly requires implementation changes:

- the review agent should not silently implement the fix
- it should return a blocked result that explains the bead requires developer work
- the result should recommend `developer` as `next_agent`

Given a `documentation` bead:

- the worker prompt should clearly state that runtime feature changes are out of scope
- the agent should update docs only, or block if code changes are required first

Example inspection flow after a blocked role-scope handoff:

- run `orchestrator bead show B0006`
- inspect `status: "blocked"` to confirm the worker stopped on role boundaries
- inspect `handoff_summary.block_reason` and `handoff_summary.next_agent` for the actionable handoff
- inspect `metadata["guardrails"].template_path` and `metadata["worker_prompt_context"].agent_type` to confirm which guardrail template and execution payload were applied
- inspect the `guardrails_applied` item in `execution_history` to see when the template context was captured

## Deliverables

- external prompt template files for each built-in agent type
- worker prompt updates to load and include template contents
- blocked-result support for role-scope violations
- bead visibility for applied guardrail template context
- automated tests covering template loading, prompt guardrails, and role-mismatch blocking
