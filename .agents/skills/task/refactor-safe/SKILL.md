---
name: refactor-safe
description: Perform safe refactors with behavior preservation.
---

# refactor-safe

Use this skill when a developer bead needs internal code cleanup without changing the intended external behavior.

## Procedure

- State the behavior that must remain unchanged before making structural edits.
- Prefer small, reviewable moves that preserve names, interfaces, and data flow unless the bead says otherwise.
- Separate pure refactoring from bug fixes; if a real behavior change is needed, treat it as explicit scope or handoff work.
- Keep existing call patterns and side effects intact while improving readability or structure.
- Avoid broad file churn when a narrower extraction, rename, or reorganization will do.
- Preserve comments, docs, and configuration that still describe the unchanged behavior.
- If the refactor exposes a larger architectural problem that exceeds the bead, stop at the safe boundary and record follow-up work.

## Output expectations

- Describe the structural improvement in plain terms and confirm intended behavior stayed the same.
- Note any area that still needs dedicated corrective or migration work.
- Use only the allowed compile or import verification for developer beads.
