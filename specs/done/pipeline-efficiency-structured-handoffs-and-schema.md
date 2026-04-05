---
name: 'Pipeline Efficiency: Structured Handoffs and Schema'
id: spec-3078243f
description: Extend HandoffSummary with structured fields so reviewers have the context
  they need without iterating back to the developer.
dependencies: null
priority: high
complexity: small
status: done
tags:
- pipeline
- efficiency
scope:
  in: models.py, runner.py, prompts.py, templates/agents/developer.md, tests
  out: skill trimming, pytest migration, TUI changes
feature_root_id: B-043cd67d
---

# Pipeline Efficiency: Structured Handoffs and Schema

## Objective

Review agents currently average 7.4 conversation turns — the highest of any agent type — despite having the lowest wall-clock time (73s avg). High turn count with low wall-clock means the reviewer is spending turns asking "what did you change and why?" rather than reviewing. This is wasted LLM budget and a fixable model problem.

The fix is to require developers to populate three structured fields in their handoff that reviewers currently have to extract through back-and-forth.

This spec is language-agnostic — it changes the data model and prompt wiring, not any Python- or project-specific tooling.

---

## Problems to Fix

1. **Reviewers iterate to extract basic context** — `HandoffSummary` has no structured field for design rationale, test coverage, or known gaps. Reviewers ask. The developer answers. That is the source of the 7.4-turn average.
2. **No schema enforcement** — nothing requires a developer to explain trade-offs or test coverage. The output schema accepts free-form narrative with no structure the reviewer can rely on.
3. **Review and tester prompts are context-thin** — the prompts pass `completed` and `remaining` but not the richer "why" information that would let a reviewer assess the change in one pass.

---

## Changes

### 1. Extend `HandoffSummary` in `models.py`

Add three optional fields to `HandoffSummary`:

```python
design_decisions: str = ""    # Why this approach; key trade-offs made
test_coverage_notes: str = "" # What the tests verify; what is NOT covered and why
known_limitations: str = ""   # Edge cases not handled; follow-up work deferred
```

Also add them to `AgentRunResult` so they flow through the pipeline alongside the existing handoff fields.

### 2. Extend `AGENT_OUTPUT_SCHEMA` in `runner.py`

Add the three fields as optional string properties in the JSON schema so the agent output parser captures them:

```json
"design_decisions": {"type": "string", "default": ""},
"test_coverage_notes": {"type": "string", "default": ""},
"known_limitations": {"type": "string", "default": ""}
```

Fields are optional (default empty string) — schema validation does not fail if absent. This preserves backward compatibility with existing bead runs.

### 3. Surface fields in review and tester prompts in `prompts.py`

When building the context block for a `review` or `tester` bead, include the new fields from each dependency bead's `AgentRunResult`:

```
## Developer Handoff: B-abc12345

**Design decisions:** <design_decisions>
**Test coverage:** <test_coverage_notes>
**Known limitations:** <known_limitations>
```

If a field is empty, omit its line rather than showing a blank value.

### 4. Require fields in `templates/agents/developer.md`

Add a section to the developer guardrail requiring all three fields to be populated:

```
## Required Handoff Fields

Your output must include all three of the following. Write "N/A — <reason>" if genuinely not applicable:

- **design_decisions**: Why you chose this approach. What alternatives you considered and rejected. Key trade-offs.
- **test_coverage_notes**: What your tests verify. What is deliberately NOT tested and why (e.g. "async paths not covered — separate bead").
- **known_limitations**: Edge cases this implementation does not handle. Technical debt introduced. Follow-up work explicitly deferred.

Reviewers use these to assess your change without asking follow-up questions. Thin entries here increase review turns and cost.
```

Fields are not schema-enforced (to avoid blocking trivial beads) but the guardrail language is strong.

---

## Files to Modify

| File | Change |
|---|---|
| `src/codex_orchestrator/models.py` | Add 3 fields to `HandoffSummary` and `AgentRunResult` |
| `src/codex_orchestrator/runner.py` | Add fields to `AGENT_OUTPUT_SCHEMA` |
| `src/codex_orchestrator/prompts.py` | Include new fields in review and tester context blocks |
| `templates/agents/developer.md` | Require all three fields with explicit N/A escape hatch |
| `tests/test_orchestrator.py` | Cover schema parsing and prompt context injection for the new fields |

---

## Acceptance Criteria

- `HandoffSummary` and `AgentRunResult` include `design_decisions`, `test_coverage_notes`, `known_limitations`
- `AGENT_OUTPUT_SCHEMA` accepts all three as optional strings with empty-string defaults
- Review and tester prompts include the three fields from each dependency's handoff when non-empty
- Developer guardrail explicitly requires all three with a documented N/A escape hatch
- Existing tests pass; new tests cover schema parsing and prompt context injection
- No existing bead JSON breaks on load (backward-compatible defaults)

---

## Pending Decisions

### 1. Field enforcement strength
Should the guardrail say "must" or "should"? "Must" risks padding on trivial beads. "Should" risks empty entries on complex ones. **Recommendation: "must" with an explicit N/A escape hatch — forces a deliberate decision either way.**
