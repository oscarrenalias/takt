---
name: Known Issues
description: Known issues and workarounds for this project
type: project
---

# Known Issues

## Agent Timeout Patterns

Long-running tasks (e.g. full test suites, large builds) may exceed the agent timeout.
Break work into smaller beads if a single bead consistently times out.
Each bead should represent roughly 1–3 hours of focused agent work.

## JSON Output Wrapping

Agents sometimes wrap their structured JSON output in markdown code fences.
The scheduler handles this automatically, but if a bead fails to parse output,
check for unexpected surrounding text in the agent run log.

## Worktree Directory Discipline

All code changes must happen inside the assigned worktree path.
Never edit files in the main repository root while a bead is in progress in a worktree,
as this can cause merge conflicts on the feature branch.
