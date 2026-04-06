---
name: spec-intake
description: How to extract implementation-ready scope from a spec document.
---

# spec-intake

## What to Extract

From the spec, identify:
1. **Objective** — one sentence: what changes and why
2. **Acceptance criteria** — the testable conditions from the spec's acceptance criteria section
3. **Files in scope** — files explicitly named or clearly implied
4. **Dependencies** — other features or beads this work depends on
5. **Out of scope** — what the spec explicitly defers

## Ambiguity

If the spec is ambiguous on a critical implementation detail, note it in the epic description. Do not invent decisions — leave them for the developer bead.

## Do Not Over-Specify

Acceptance criteria in child beads should be verifiable, not prescriptive about implementation. "The planner output schema rejects invalid agent_type values" is good. "The `build_planner_prompt` function adds an enum field to the JSON schema at line 42" is too specific.
