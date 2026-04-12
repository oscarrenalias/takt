---
name: memory
description: Read and update shared institutional memory across beads.
---

# memory

Shared memory accumulates knowledge across beads and features. Search it at bead start; add entries when you discover reusable project knowledge.

## At Bead Start

Run three searches before touching any code. Use `$TAKT_CMD` (injected by the orchestrator):

```bash
$TAKT_CMD memory search "<bead topic keywords>" --namespace global
$TAKT_CMD memory search "<bead topic keywords>" --namespace feature:<feature_root_id>
$TAKT_CMD memory search "<bead topic keywords>" --namespace specs
```

Treat results as ambient context — apply relevant entries; ignore entries that don't apply.

## When to Write an Entry

Write a memory entry when you discover something that is:

- Project-wide and reusable — not specific to the current bead
- Something that would have changed your approach if you had known it upfront
- Likely to recur across future beads or agent types
- Not already covered in CLAUDE.md or the guardrail templates

Do **not** write entries for anything bead-specific, ephemeral, or already covered elsewhere.

## Writing an Entry

```bash
# Project-wide knowledge
$TAKT_CMD memory add "<concise fact>" --namespace global

# Feature-specific discovery
$TAKT_CMD memory add "<discovery>" --namespace feature:<feature_root_id>
```

Keep entries short: one or two sentences. Be concrete. No padding.

## Access Control

| Agent type    | Read | Write                          |
|---------------|------|--------------------------------|
| Planner       | yes  | `global` namespace only        |
| Developer     | yes  | `global` and `feature`         |
| Tester        | yes  | `global` and `feature`         |
| Documentation | yes  | **read-only — do not write**   |
| Review        | yes  | **read-only — do not write**   |
