---
name: investigator
description: Open-ended read-only codebase analysis and structured report production.
---

# investigator

Use this skill as the primary workflow for investigator beads. It defines how to conduct free-range codebase exploration, gather findings, and produce a structured report without mutating any source files.

## Objective

Investigate the assigned area of the codebase thoroughly, surface concrete findings and prioritised recommendations, and write a single durable report file. Do not fix anything — document what you find.

## Working Pattern

1. Read the bead description and acceptance criteria to understand the investigation scope.
2. Identify the report file path from `expected_files[0]` or the bead description.
3. Explore the codebase freely: read source files, run `git log`, search with `grep`, count lines, trace call paths.
4. Accumulate findings as you explore. Note file locations and line numbers where specific issues appear.
5. Write the report to the declared path with the required structure: Executive Summary, Findings, Recommendations, Risk Areas.
6. Return structured output with `findings`, `recommendations`, `risk_areas`, and `report_path` populated.

## Scope Rules

- Read scope is unconstrained. You may read any file in the repository.
- Write scope is strictly one file: the declared report file. No other files may be created or modified.
- Do not run the test suite, build commands, or any command that mutates state.

## Report Quality Standards

- Findings should be concrete: identify the file, location, and observable behavior rather than general impressions.
- Recommendations should be actionable and prioritised. Each recommendation should map to one or more findings.
- Risk areas should connect unaddressed findings to observable downstream consequences.
- The executive summary should be readable without the detail sections — one paragraph, highest-signal items only.

## Handoff Expectations

Always populate all four structured output fields:

- `findings` — condensed summary of what was found (the report has full detail)
- `recommendations` — prioritised action list
- `risk_areas` — what happens if findings are not addressed
- `report_path` — relative path to the written report file
