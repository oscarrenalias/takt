# Known Issues

Recurring pitfalls, traps, and things that broke. This file is **append-only shared memory** for the agent pool — not bead-specific notes. Append a new dated entry when you encounter something that would have helped you if you had known it upfront and is likely to recur across future beads. Never rewrite or delete existing entries.

---

## 2026-04-02 — unittest discover timeout

Running `unittest discover` takes 3+ minutes and often hits the agent timeout. Always target a specific module instead:

```bash
uv run python -m unittest tests.<module> -v
```

## 2026-04-02 — Claude Code JSON wrapped in markdown fences

Claude Code occasionally wraps JSON output in markdown code fences (` ```json ... ``` `), which causes structured output parsing to fail. The orchestrator's output parser must strip fences before deserialising, and agents should be aware that a `tool_use` stop reason with empty result is a symptom of this.

## 2026-04-02 — Always return to project root after worktree operations

Always `cd` back to the project root after any operation inside a worktree. Running orchestrator commands from inside a worktree creates nested paths and corrupts state.

## 2026-04-02 — VIRTUAL_ENV must be cleared before spawning agent subprocesses

The `VIRTUAL_ENV` environment variable must be cleared before spawning agent subprocesses, otherwise `uv run` warns and may background long-running commands silently.

## 2026-04-06 — Tester agent must never use run_in_background

Using `run_in_background: true` in any Bash tool call inside a tester bead causes the structured JSON verdict to be emitted before test output is captured, resulting in a failed or empty bead result. Always run test commands synchronously. If a task-notification appears mid-response, it means a prior command ran in the background — re-run the test synchronously and emit the JSON verdict as the final output.
