# Takt

![agent-takt](docs/assets/takt.png)

**Takt** is an agentic system for orchestrating specialized AI coding workers (Codex or Claude Code) against a Git-native task graph.

AI coding agents are powerful but undisciplined — they skip tests, forget documentation, and lose context across long tasks. Takt enforces a structured development process: every feature is decomposed into discrete units of work called **beads**, each assigned to a specialized agent type with a defined role and guardrails. Nothing gets skipped because the scheduler won't let it.

Takt is intentionally opinionated. Spec-driven development — writing a human-readable spec before any code is written — is optional but strongly recommended: it forces clarity upfront and gives agents the context they need to make good decisions. The [spec-management skill](https://github.com/oscarrenalias/skill-spec-management) complements the process by providing a structured workflow for writing, planning, and tracking specs through to completion. It is included in the skill catalog installed by `takt init` for Claude Code projects.

## Key features
- **Structured pipeline** — every feature flows through planning, implementation, testing, documentation, and review. Agents cannot skip steps or drift out of scope.
- **Git-native isolation** — each bead runs in its own Git worktree, so parallel agents never step on each other.
- **Self-healing** — blocked beads, merge conflicts, and test failures automatically create corrective work items rather than silently failing.
- **Backend-agnostic** — works with Claude Code or Codex; switch with a flag or environment variable.
- **Observable** — a terminal UI, telemetry, and structured JSON handoffs give full visibility into what agents are doing and why.
- **Git-native** – beads are json files that live within the repository; they sit right next to the code that they modify, and their history and upates are tracked just like any other file in the repositoy

---

## For Users

### Install

**With `uv` (recommended):**
```bash
uv tool install <release-url>
```

**With `pip`:**
```bash
pip install <release-url>
```

Download `<release-url>` from the [releases page](https://github.com/oscarrenalias/takt/releases/latest). Pick the `.whl` file for your platform.

Once installed, initialise a new project in your repository:

```bash
takt init
```

This sets up the project structure, copies guardrail templates, and installs the recommended skill catalog — including the [spec-management skill](https://github.com/oscarrenalias/skill-spec-management) for Claude Code projects.

### Working with Specs

The typical workflow: write a spec, let the planner decompose it into beads, run the scheduler to execute them.

```bash
# 1. Write a spec describing what you want built
#    e.g. specs/my-feature.md

# 2. Run the planner to turn the spec into a bead graph
takt plan specs/my-feature.md

# 3. Start the scheduler — workers pick up ready beads automatically
takt --runner claude run --max-workers 4

# 4. Monitor progress
takt summary
takt tui
```

The planner creates a feature root bead with developer child beads, each scoped to a focused change. When a developer bead completes, the scheduler automatically creates tester, documentation, and review followup beads.

When all beads in a feature are done, merge the feature branch:

```bash
takt merge <feature_root_bead_id>
```

### Key Commands

```bash
takt --version                           # print installed version
takt summary                             # counts + next actionable beads
takt summary --feature-root B0030        # scoped to one feature
takt bead list --plain                   # all beads as table
takt bead show <id>                      # single bead details (JSON)
takt bead graph                          # Mermaid diagram of all beads
takt bead graph --feature-root <id>      # scoped to one feature
takt bead graph --output graph.md        # write diagram to file
takt --runner claude run --once          # one scheduler cycle
takt --runner claude run --max-workers 4 # parallel workers
takt retry <bead_id>                     # requeue a blocked bead
takt merge <bead_id>                     # merge a done feature
takt merge <bead_id> --skip-rebase       # skip merge-main preflight
takt merge <bead_id> --skip-tests        # skip test gate
takt tui                                 # interactive terminal UI
```

### Creating Beads Directly

For one-off tasks, create a bead without a spec:

```bash
takt bead create \
  --title "Add feature X" \
  --agent developer \
  --description "Implement X by modifying src/foo.py"
```

### Merge Safety

The `takt merge` command runs two preflight checks before merging to main:

1. **Merge-main preflight** (skippable with `--skip-rebase`): Merges the current `main` branch into your feature branch to catch conflicts early. If conflicts are detected, a `merge-conflict` bead is created for you to resolve.

2. **Test gate** (skippable with `--skip-tests`): Runs your configured test suite to validate the merge. Test failures also create a `merge-conflict` bead.

If a conflict bead is created, run the scheduler to resolve it, then retry:

```bash
takt --runner claude run --once
takt merge <feature_root_bead_id>
```

Merge-conflict beads track the specific files involved, appear as `open` and ready for a developer to fix, and block the merge until resolved.

Configure the test gate in `.takt/config.yaml`:

```yaml
common:
  test_command: "uv run pytest tests/ -n auto -q"
  test_timeout_seconds: 120
```

### Configuration

Runtime config lives in `.takt/config.yaml`. The default backend is `codex`; switch to Claude Code:

```bash
takt --runner claude run
# or
export AGENT_TAKT_RUNNER=claude
```

### Current Limitations

- **Single stack per project** — `takt` assumes one language, one test command, and one build pipeline per repository. Monorepos with multiple stacks or frameworks (e.g. a Python backend and a JavaScript frontend) are not well supported: the test command is global, and agent guardrails are not stack-aware.
- **Local execution only** — agents run as local subprocesses. There is no support for remote or cloud-based execution environments.
- **Claude Code and Codex only** — no support for other AI backends (GPT-4, Gemini, etc.).
- **Fixed followup pipeline** — every developer bead automatically generates tester, documentation, and review followups. This pipeline is not configurable per bead or per feature; you cannot opt individual beads out of specific followup types.
- **No human-in-the-loop gates** — there is no built-in mechanism to pause the pipeline and require explicit human approval before proceeding. Operator actions in the TUI can block beads manually, but this is not automated.
- **Context window limits** — very large changes may exceed agent context limits. Beads need to be sized accordingly; the planner helps but cannot guarantee this automatically.
- **No rollback** — if a merged feature introduces a regression, there is no automated rollback mechanism. Recovery goes through the normal bead pipeline.

---

## For Contributors

### Install from Source

```bash
git clone https://github.com/oscarrenalias/takt
cd takt
uv sync
```

### Running Tests

```bash
uv run pytest tests/ -n auto -q
```

See [docs/development.md](docs/development.md) for project layout, guardrails, telemetry, and contribution guidelines.

---

## Documentation

- [Onboarding guide](docs/onboarding.md) — `takt init` and project setup
- [TUI reference](docs/tui.md) — keyboard bindings, panels, refresh modes
- [Development guide](docs/development.md) — layout, guardrails, testing, telemetry
- [Multi-backend agents](docs/multi-backend-agents.md) — Codex vs Claude Code configuration
- [Scheduler telemetry](docs/scheduler-telemetry.md) — telemetry schema and storage
