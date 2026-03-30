# Multi-Backend Agent Support

The orchestrator supports multiple agent backends (Codex and Claude Code) side by side. Both execute beads identically through the scheduler, but differ in how they discover skills and receive steering context.

## Selecting a Backend

CLI flag (takes precedence):

```bash
orchestrator --runner claude run --once
orchestrator --runner codex plan spec.md
```

Environment variable (default when no flag is given):

```bash
export ORCHESTRATOR_RUNNER=claude
```

Falls back to `codex` if neither is set.

## Runner Architecture

`AgentRunner` is the abstract base in `src/codex_orchestrator/runner.py`. Each backend implements:

| Method | Purpose |
|---|---|
| `backend_name` | Returns `"codex"` or `"claude"` |
| `run_bead()` | Invokes the agent CLI and parses structured JSON output |
| `propose_plan()` | Invokes the agent CLI for planning |

Both runners share the same prompt construction (`prompts.py`), output schemas (`AGENT_OUTPUT_SCHEMA`, `PLANNER_OUTPUT_SCHEMA`), and bead lifecycle.

## Isolated Execution Root

Before each bead runs, `prepare_isolated_execution_root()` in `skills.py` creates a per-bead directory under `.orchestrator/agent-runs/{bead_id}/`. The layout varies by backend:

### Codex

```
exec_root/
  .agents/skills/          # Codex auto-discovers skills here
  repo/                    # Symlink to feature worktree
  home/                    # Isolated home directory
```

### Claude Code

```
exec_root/
  .claude/skills/          # Claude Code auto-discovers skills here
  CLAUDE.md                # Guardrail template (agent steering)
  repo/                    # Symlink to feature worktree
  home/                    # Isolated home directory
```

## Skills

Skills live in the repository under `.agents/skills/` and follow the [Agent Skills](https://agentskills.io) open standard. Each skill directory contains a `SKILL.md` with YAML frontmatter (`name`, `description`) and brief behavioural instructions.

The `AGENT_SKILL_ALLOWLIST` in `skills.py` controls which skills each agent type can access. Only allowed skills are copied into the execution root.

| Aspect | Codex | Claude Code |
|---|---|---|
| Target directory | `exec_root/.agents/skills/` | `exec_root/.claude/skills/` |
| Discovery | Auto (Codex reads `.agents/skills/`) | Auto (Claude Code reads `.claude/skills/`) |
| Policy file | `agents/openai.yaml` per skill | SKILL.md frontmatter (`allowed-tools`, `disable-model-invocation`) |

The same source SKILL.md files are used for both backends. The `openai.yaml` policy files are Codex-specific and ignored by Claude Code.

## Agent Steering (Guardrail Templates)

Role-specific guardrail templates live in `templates/agents/{agent_type}.md` and define what each agent type is allowed and disallowed to do.

| Aspect | Codex | Claude Code |
|---|---|---|
| Delivery | Embedded in the worker prompt by `build_worker_prompt()` | Written to `exec_root/CLAUDE.md` (auto-loaded natively) |
| Prompt inclusion | Yes (always) | Yes (also included in prompt) |

For Claude Code, the guardrail template is written as a `CLAUDE.md` file in the execution root during skill isolation setup. Claude Code loads `CLAUDE.md` automatically from the working directory tree, so the agent receives role-specific constraints without additional prompt tokens.

## CLI Invocation Differences

| | Codex | Claude Code |
|---|---|---|
| Command | `codex exec` | `claude -p` |
| Auto-approve | `--full-auto` | `--dangerously-skip-permissions` |
| Schema | `--output-schema <file>` (temp file) | `--json-schema '<json>'` (inline) |
| Output | `--output-last-message <file>` (temp file) | `--output-format json` (stdout) |
| Working dir | `-C <path>` flag | `cwd=` on subprocess |
| Prompt input | stdin (`-`) | stdin (piped) |
