# Project Onboarding

## Objective

Make the orchestrator reusable across any project, not just itself. Today the system is self-hosting only — skills, guardrail templates, config, and memory files are all tightly coupled to this repo. This spec defines how to package the orchestrator as an installable CLI, onboard a new project, and give each project its own tunable agent steering.

## Scope

- Package the orchestrator as an installable Python CLI (`uv tool install` / `pip install`)
- Add an `orchestrator init` command that onboards a new project interactively
- Copy and localise skills, guardrail templates, and memory files into the target project
- Generate a project-specific config file based on user answers
- Seed project-specific memory files

Out of scope for this version:
- Multi-repo or monorepo support (single repo per orchestrator instance)
- Remote agent execution
- GUI / web interface

---

## Packaging

The orchestrator is published as a Python package: `codex-orchestrator`.

### Entry point

```
orchestrator
```

Installed via:
```bash
uv tool install codex-orchestrator
pip install codex-orchestrator
```

### Bundled assets

The following are included as package data (not just source files):

| Asset | Description |
|---|---|
| `templates/agents/*.md` | Default guardrail templates for all agent types |
| `.agents/skills/**` | Full default skill catalog |
| `.claude/skills/**` | Claude Code skill variants |
| `docs/memory/known-issues.md` | Default memory seeds (orchestrator-specific — replaced during init) |
| `docs/memory/conventions.md` | Default memory seeds (orchestrator-specific — replaced during init) |
| `.orchestrator/config.yaml` | Default config (used as template during init) |

Assets are resolved via `importlib.resources` or a `package_data` entry in `pyproject.toml`.

---

## `orchestrator init` Command

Initialises a new project. Run from the root of the target repository:

```bash
orchestrator init
```

### Interactive prompts

The command asks the user:

1. **Runner backend**: `claude` or `codex`? (default: `claude`)
2. **Max workers**: how many parallel agents? (default: 1)
3. **Project language/framework**: free text, used to tailor guardrail templates and memory seeds (e.g. "TypeScript/Node.js", "Python/FastAPI", "Go")
4. **Test command**: what command runs the test suite? (e.g. `npm test`, `go test ./...`, `pytest`)
5. **Build/syntax check command**: what command verifies the build without running tests? (e.g. `tsc --noEmit`, `go build ./...`, `uv run python -m py_compile`)

### What `init` creates

```
<project-root>/
  .orchestrator/
    config.yaml          # generated from prompts
    beads/               # empty
    logs/                # empty
    worktrees/           # empty
    telemetry/           # empty
    agent-runs/          # empty
  templates/
    agents/
      developer.md       # copied from package, test/build commands substituted
      tester.md          # copied from package, test command substituted
      review.md          # copied from package
      documentation.md   # copied from package
      planner.md         # copied from package
  .agents/
    skills/              # full skill catalog copied from package
  .claude/
    skills/              # Claude Code skill catalog copied from package
  docs/
    memory/
      known-issues.md    # seeded with project-language-appropriate entries
      conventions.md     # seeded with project-language-appropriate entries
  specs/                 # empty directory, where specs will live
  specs/done/            # empty
```

### Config file generation

`.orchestrator/config.yaml` is generated from the interactive answers:

```yaml
common:
  default_runner: claude        # from prompt
  scheduler:
    max_workers: 1              # from prompt (written as a comment/hint — max_workers is a CLI flag, not config)

claude:
  flags:
    - --dangerously-skip-permissions
  timeout_seconds: 600

codex:
  flags:
    - --full-auto
  timeout_seconds: 600
```

### Guardrail template substitution

The default guardrail templates reference Python-specific commands. During `init`, the following placeholders are substituted based on user answers:

| Placeholder | Replaced with |
|---|---|
| `{{TEST_COMMAND}}` | Answer to "test command" prompt |
| `{{BUILD_CHECK_COMMAND}}` | Answer to "build/syntax check command" prompt |
| `{{LANGUAGE}}` | Answer to "project language/framework" prompt |

Example — developer.md after substitution for a TypeScript project:
```
Verify your changes do not break the build: `tsc --noEmit`
Do not run tests. Use: `npm test` — that is the tester agent's responsibility.
```

### Memory file seeding

`docs/memory/known-issues.md` and `docs/memory/conventions.md` are seeded with generic entries appropriate to the detected language/framework rather than the orchestrator's own entries. The orchestrator's own memory entries (Python/uv-specific) are not copied.

Generic seed entries for any project:
- `known-issues.md`: agent timeout patterns, JSON output wrapping issues, worktree directory discipline
- `conventions.md`: bead ID format, how to run commands, append-only memory rules

Language-specific entries are added where known (e.g. for TypeScript: `tsc` vs `tsc --noEmit`, `node_modules` gitignore).

### `.gitignore` additions

`init` appends to `.gitignore` (or creates it):

```
.orchestrator/worktrees/
.orchestrator/telemetry/
.orchestrator/logs/
.orchestrator/agent-runs/
```

---

## Skills Portability

Skills are copied into the project during `init` so each project can tune them independently. Changes to skills in one project do not affect others.

After `init`, the project owns its skill files. The installed package's skills are only used as the initial template.

Projects may:
- Edit existing SKILL.md files to match their language/framework/process
- Add new skills under `.agents/skills/` and `.claude/skills/`
- Remove skills that are not relevant

The `AGENT_SKILL_ALLOWLIST` in `skills.py` controls which skills are bundled per agent type. For project-specific skills not in the allowlist, projects must update their local copy of `skills.py` — or this allowlist should be moved to config (see Pending Decisions).

---

## Spec Guidance

`init` drops a `specs/HOWTO.md` explaining how to write specs that the planner can decompose effectively:

- One objective per spec
- Acceptance criteria that are testable, not prescriptive about implementation
- File scope hints where known
- What makes a bead too large (more than 2–3 functions, multiple subsystems)
- Link to the planner guardrail template for reference

---

## Files to Add/Modify

| File | Change |
|---|---|
| `pyproject.toml` | Add package data entries for assets, verify entry point |
| `src/codex_orchestrator/cli.py` | Add `init` subcommand |
| `src/codex_orchestrator/onboarding.py` | New module: interactive prompts, folder creation, template substitution, memory seeding |
| `templates/agents/*.md` | Replace hardcoded commands with `{{PLACEHOLDER}}` substitution tokens |
| `docs/onboarding.md` | User-facing guide: installation, first run, customisation |

---

## Acceptance Criteria

- `pip install codex-orchestrator` installs a working `orchestrator` CLI with all assets bundled
- `orchestrator init` runs interactively and creates all required folders and files
- Generated `config.yaml` reflects user answers
- Guardrail templates have language/framework-specific commands substituted
- Memory files are seeded with generic entries, not orchestrator-specific ones
- `.gitignore` is updated correctly
- `specs/HOWTO.md` is created
- Running `orchestrator summary` in the new project works immediately after `init`
- All existing tests pass

---

## Pending Decisions

### 1. AGENT_SKILL_ALLOWLIST portability
Currently hardcoded in `skills.py`. Projects that add custom skills cannot include them in the allowlist without editing source. Should this move to `.orchestrator/config.yaml`? That would let projects declare their own skill-to-agent-type mappings. **Leans yes — needed for real portability.**

### 2. Guardrail template ownership
Should projects own a full copy of `templates/agents/*.md`, or should there be a fallback chain (project template → package default)? Full copy gives maximum flexibility but means projects miss improvements to the defaults. **Undecided.**

### 3. Language/framework detection
Should `init` try to auto-detect the language (e.g. check for `package.json`, `go.mod`, `pyproject.toml`) and pre-fill prompts, or always prompt? Auto-detection is friendlier but fragile. **Leans toward detect-and-confirm.**

### 4. Multi-language projects
A project with both Python and TypeScript (e.g. a backend + frontend monorepo) needs different test/build commands for different parts. The current single-command model doesn't cover this. **Deferred — single language per init for now.**

### 5. Upgrading skills/templates
When a new version of the orchestrator ships with improved skills or guardrail templates, how does a project get the update? Manual copy? A `orchestrator upgrade` command? **Undecided — needs a story before v1.**

### 6. `max_workers` in config vs CLI
Currently `max_workers` is a CLI flag only. Should it have a config default per project? Useful so projects don't have to remember to pass `--max-workers 4` every time. **Leans yes.**

### 7. Spec format enforcement
Should `init` create a spec linter (or planner dry-run) that validates a spec before planning? Useful for catching bad specs early. **Deferred.**

### 8. Git prerequisites
The system requires git. Should `init` validate that the target directory is a git repo and fail with a clear message if not? Should it offer to `git init`? **Leans toward validate + clear error, no auto-init.**

### 9. Claude Code / Codex installation check
Should `init` verify that the selected runner binary is installed and accessible, and fail early with install instructions if not? **Yes — clear failure at init time is much better than a cryptic error on first run.**
