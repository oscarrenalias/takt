---
name: memory
description: Read and update shared institutional memory across beads.
---

# memory

Shared memory accumulates knowledge across beads and features. Read it at the start of every bead; update it when you discover something the next agent would benefit from knowing.

## Memory Files

- `docs/memory/known-issues.md` — Recurring pitfalls, environment quirks, API behaviours, and things that look safe but aren't.
- `docs/memory/conventions.md` — Implicit patterns that emerged from agent experience: naming conventions, tool invocation habits, codebase choices not obvious from reading the code.

## At Bead Start

Read both files before touching any code. Treat their content as ambient context — apply relevant entries to your current task; ignore entries that don't apply.

## When to Append an Entry

Append a new dated entry when you discover something that is:

- Project-wide and reusable — not specific to the current bead
- Something that would have changed your approach if you had known it upfront
- Likely to recur across future beads or agent types
- Not already covered in CLAUDE.md or the guardrail templates

Do **not** append entries for:

- Anything bead-specific or ephemeral
- Information already present in CLAUDE.md or guardrail templates
- Details that belong in a spec or design document

## Entry Format

Append to the relevant file using a level-2 heading with the date:

```
## YYYY-MM-DD — Short title

One or two sentences. Be concrete. No padding.
```

## Append-Only

Never rewrite, reorganise, or delete existing entries. Memory is append-only; the history must remain intact.

## Access Control

| Agent type   | Read | Write                    |
|--------------|------|--------------------------|
| Planner      | yes  | `conventions.md` only    |
| Developer    | yes  | both files               |
| Tester       | yes  | both files               |
| Documentation| yes  | **read-only — do not append entries** |
| Review       | yes  | **read-only — do not append entries** |

Documentation and review agents must not modify either memory file.
