---
name: planner-decomposition
description: How to decompose a spec into a well-ordered bead graph.
---

# planner-decomposition

## Bead Sizing

Each developer bead should be completable in ~10 minutes of implementation work. Heuristics:
- More than 2-3 functions changed -> split into dependent beads
- Work spans multiple subsystems -> split
- Mix of refactor + feature work -> split

Err toward smaller beads. Dependent beads run sequentially anyway; there is no cost to splitting.

## agent_type Values

Use exactly these values — no abbreviations or variations:
- `developer` — code implementation
- `tester` — test writing and validation
- `documentation` — doc updates
- `review` — review and signoff
- `planner` — planning only (rarely needed as a child bead)

## Shared Followup Beads

For features with 2+ developer beads, create **one shared** tester, documentation, and review bead — not one per developer bead. The shared bead must list all developer bead titles in its `dependencies`.

## Dependency Ordering

- Tester depends on: all developer beads it validates
- Documentation depends on: the validated developer bead set (or tester, if docs describe test outcomes)
- Review depends on: tester + documentation

Do not create circular dependencies. Every bead must eventually be reachable from the feature root.

## expected_files

Use repo-relative paths (e.g. `src/agent_takt/scheduler.py`). Do not use absolute paths.
