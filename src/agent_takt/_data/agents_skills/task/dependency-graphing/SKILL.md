---
name: dependency-graphing
description: How to construct a valid dependency graph for a bead plan.
---

# dependency-graphing

## Rules

1. A bead may only depend on beads that are siblings or ancestors in the same feature tree
2. No circular dependencies — if A depends on B, B cannot depend on A
3. Developer beads that touch the same file must be ordered sequentially (file-scope conflicts prevent parallel execution)
4. Tester, documentation, and review beads must depend on the developer beads they cover

## Parallelism Opportunities

Beads with no shared files and no dependency relationship can run in parallel. Plan for this:
- Independent subsystem changes -> separate developer beads with no dependency between them
- Shared file changes -> sequential dependency

## Validation Check

Before finalising: trace every leaf bead to the feature root. If any bead is unreachable, it has a missing dependency or was accidentally disconnected.
