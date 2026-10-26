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

## Requirements Section

If the spec contains a `## Requirements` section, apply the following rule when populating the shared tester bead's acceptance criteria:

- For every `### ADDED:` and `### MODIFIED:` entry, copy every scenario into the shared tester bead's acceptance criteria without dropping any content. Include the full scenario: the `#### Scenario: <name>` heading, every `- **WHEN**` line, every `- **THEN**` line, and any `- **AND**` lines, in the criterion form below.
- Write each scenario as exactly one criterion, in this exact form (note the em dash, U+2014, surrounded by single spaces):

  `Scenario: <capability> / <requirement name> / <scenario name> — <WHEN/THEN text>`

  `<capability>` is the capability the requirement belongs to, `<requirement name>` is the `### ADDED:`/`### MODIFIED:` name, `<scenario name>` is the `#### Scenario:` name, and `<WHEN/THEN text>` is the scenario's WHEN, THEN and AND lines joined into one line. Do not reword, reorder or merge scenarios; the prefix is what lets later accounting match a criterion back to its scenario.
- Capability, requirement and scenario names must not contain ` — `, ` -- ` or ` - ` (space-delimited dashes), because those sequences are reserved as delimiters. If a name contains one, rename it in the spec rather than escaping it in the criterion.
- `### REMOVED:` entries carry no scenarios and are ignored at planning time.
- Scenarios from the Requirements section **supplement** — they do not replace — any explicit acceptance criteria the spec already provides for the tester bead.

Note (not implemented): `spec.py validate` could enforce the delimiter rule by rejecting capability, requirement or scenario names containing ` — `, ` -- ` or ` - `.
