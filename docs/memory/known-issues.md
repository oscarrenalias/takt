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

Always `cd` back to the project root after any operation inside a worktree. Running takt commands from inside a worktree creates nested paths and corrupts state.

## 2026-04-02 — VIRTUAL_ENV must be cleared before spawning agent subprocesses

The `VIRTUAL_ENV` environment variable must be cleared before spawning agent subprocesses, otherwise `uv run` warns and may background long-running commands silently.

## 2026-04-06 — Tester agent must never use run_in_background

Using `run_in_background: true` in any Bash tool call inside a tester bead causes the structured JSON verdict to be emitted before test output is captured, resulting in a failed or empty bead result. Always run test commands synchronously. If a task-notification appears mid-response, it means a prior command ran in the background — re-run the test synchronously and emit the JSON verdict as the final output.
