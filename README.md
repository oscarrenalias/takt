# Codex Agent Orchestration

A local Python CLI for orchestrating specialized AI workers (Codex or Claude Code) against a Git-native task graph.

Workers operate on **beads** — discrete units of work with a defined lifecycle (`open` → `ready` → `in_progress` → `done` | `blocked`). Each bead runs in an isolated Git worktree with role-specific guardrails. Structured handoffs flow between `planner`, `developer`, `tester`, `documentation`, and `review` agents.

## Install

```bash
uv sync
```

## Working with Specs

The typical workflow is: write a spec, let the planner decompose it into beads, then run the scheduler to execute them.

```bash
# 1. Write a spec describing what you want built
#    e.g. specs/my-feature.md

# 2. Run the planner to turn the spec into a bead graph
uv run orchestrator plan specs/my-feature.md

# 3. Start the scheduler — workers pick up ready beads automatically
uv run orchestrator --runner claude run --max-workers 4

# 4. Monitor progress
uv run orchestrator summary
uv run orchestrator tui
```

The planner creates a feature root bead with developer child beads, each scoped to a focused change. When a developer bead completes, the scheduler automatically creates tester, documentation, and review followup beads.

When all beads in a feature are done, merge the feature branch:

```bash
uv run orchestrator merge <feature_root_bead_id>
```

### Merge Safety Workflow

The merge command runs two preflight checks before merging to main:

1. **Merge-main preflight** (skippable with `--skip-rebase`): Merges the current `main` branch into your feature branch to catch conflicts early. If conflicts are detected, the orchestrator creates a `merge-conflict` bead for you to resolve. After resolving, run the scheduler to process the conflict bead, then retry the merge.

2. **Test gate** (skippable with `--skip-tests`): Runs your configured test suite to validate the merge. Output is streamed to the terminal in real time. Test failures create a `merge-conflict` bead, allowing you to address failures via the normal development workflow.

Bead state changes (writes and deletions) are committed automatically by the storage layer as they happen. You do not need to stage or commit bead JSON files manually before running the merge preflight — the feature branch is always in a clean state with respect to bead metadata.

If conflicts are detected during either check, the merge is blocked. Resolve the conflict bead, then run:

```bash
uv run orchestrator --runner claude run --once
uv run orchestrator merge <feature_root_bead_id>
```

### Merge Conflict Beads

When the merge preflight or test gate detects issues, a `merge-conflict` bead is created as a child of your feature. These beads:
- Track the specific files involved in the conflict
- Appear as `open` and ready for a developer to fix
- Block the merge until resolved (status: `done`)

Note: TUI merge operations are disabled. Always use the CLI `merge` command.

### Configuration

Configure merge behavior in `.orchestrator/config.yaml` under the `common` block:

```yaml
common:
  test_command: "uv run pytest tests/ -n auto -q"
  test_timeout_seconds: 120
```

- `test_command`: Shell command to run during the test gate. If not set, tests are skipped. Required for `--skip-tests=false`.
- `test_timeout_seconds`: Timeout in seconds for test execution (default: 120).

## Key Commands

```bash
uv run orchestrator summary                        # counts + next actionable beads
uv run orchestrator summary --feature-root B0030   # scoped to one feature
uv run orchestrator bead list --plain              # all beads as table
uv run orchestrator bead show <id>                 # single bead details (JSON)
uv run orchestrator bead graph                     # Mermaid diagram of all beads
uv run orchestrator bead graph --feature-root <id> # scoped to one feature
uv run orchestrator bead graph --output graph.md   # write diagram to file
uv run orchestrator --runner claude run --once     # one scheduler cycle
uv run orchestrator --runner claude run --max-workers 4  # parallel workers
uv run orchestrator retry <bead_id>                # requeue a blocked bead
uv run orchestrator merge <bead_id>                # merge a done bead
uv run orchestrator merge <bead_id> --skip-rebase  # skip merge-main preflight
uv run orchestrator merge <bead_id> --skip-tests   # skip test gate
uv run orchestrator tui                            # interactive terminal UI
```

## Creating Beads Directly

For one-off tasks, create a bead without a spec:

```bash
uv run orchestrator bead create \
  --title "Add feature X" \
  --agent developer \
  --description "Implement X by modifying src/foo.py"
```

## Configuration

Runtime config lives in `.orchestrator/config.yaml`. The default backend is `codex`; switch to Claude Code:

```bash
uv run orchestrator --runner claude run
# or
export ORCHESTRATOR_RUNNER=claude
```

## Documentation

- [Onboarding guide](docs/onboarding.md) — installation, `orchestrator init`, and project setup
- [TUI reference](docs/tui.md) — keyboard bindings, panels, refresh modes
- [Development guide](docs/development.md) — layout, guardrails, testing, telemetry
- [Multi-backend agents](docs/multi-backend-agents.md) — Codex vs Claude Code configuration
- [Scheduler telemetry](docs/scheduler-telemetry.md) — telemetry schema and storage
