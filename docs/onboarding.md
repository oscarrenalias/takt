# Onboarding a New Project

This guide covers installing the agent-takt CLI and initialising a new project for agent-based development.

## Installation

The `takt` CLI is distributed as a standard Python package named `agent-takt`.

**Recommended — isolated tool install via uv:**

```bash
uv tool install agent-takt
```

This installs the `takt` CLI into an isolated environment managed by uv, keeping it separate from your project dependencies.

**Alternative — pip:**

```bash
pip install agent-takt
```

Both methods install the same `takt` entry point. Verify the install succeeded:

```bash
takt --help
```

## Prerequisites

Before running `takt init`, make sure the following are in place:

1. **Git repository** — the target directory must be a git repo (`git init` if needed).
2. **Agent runner CLI** — install the backend you plan to use:
   - Claude Code: `npm install -g @anthropic-ai/claude-code`
   - Codex: `npm install -g @openai/codex`

`takt init` checks that the chosen runner binary is on `PATH` and exits with an install hint if it is not found.

## Running `takt init`

From the root of your git repository:

```bash
takt init
```

This starts an interactive prompt session. Press **Enter** to accept the default shown in brackets, or type a value and press Enter.

### Prompts

`takt init` uses numbered menus for runner and stack selection. Press Enter to accept the default shown in brackets; enter a number to choose a different option.

**Runner backend** — numbered menu, default `1` (claude):

| # | Option |
|---|--------|
| 1 | claude |
| 2 | codex  |

**Max parallel workers** — free-text integer, default `1`. Must be ≥ 1.

**Project stack** — numbered menu sourced from the built-in STACKS catalog:

| # | Stack | Default test command | Default build check |
|---|-------|---------------------|---------------------|
| 1 | Python | `pytest` | `python -m py_compile` |
| 2 | Node.js | `npm test` | `npm run build` |
| 3 | TypeScript | `npm test` | `tsc --noEmit` |
| 4 | Go | `go test ./...` | `go build ./...` |
| 5 | Rust | `cargo test` | `cargo build` |
| 6 | Java (Maven) | `mvn test` | `mvn compile -q` |
| 7 | Other | _(free text)_ | _(free text)_ |

After choosing a predefined stack (1–6), `takt init` pre-fills the test command and build/syntax check command prompts with catalog defaults. Press Enter to accept the defaults or type a replacement.

Choosing **Other** (7) opens three additional free-text prompts:

| Prompt | Example |
|--------|---------|
| Project language/framework | `TypeScript/Node.js`, `Go` |
| Test command | `npm test`, `go test ./...` |
| Build/syntax check command | `tsc --noEmit`, `go build ./...` |

### Non-interactive mode

For scripting or CI environments, skip all prompts and use built-in defaults:

```bash
takt init --non-interactive
```

Non-interactive defaults are sourced from the first entry in the stack catalog (`STACKS[0]`), which is the Python stack:

| Setting | Default value |
|---------|--------------|
| Runner | `claude` |
| Max workers | `1` |
| Language | `Python` |
| Test command | `pytest` |
| Build/syntax check command | `python -m py_compile` |

Because these values are read from `STACKS[0]` at runtime rather than being hardcoded separately, they stay in sync with the interactive defaults automatically.

To replace any files that were already created by a previous init:

```bash
takt init --overwrite
```

## What `takt init` Creates

After a successful run the following structure is added to your repository:

```
.takt/
  config.yaml              # Generated from your prompt answers; edit to customise
  assets-manifest.json     # SHA-256 fingerprints of all installed bundled assets
  beads/                   # Bead JSON state (version-controlled)
  logs/                    # Event log (runtime, gitignored)
  worktrees/               # Feature worktrees (runtime, gitignored)
  telemetry/               # Telemetry artifacts (runtime, gitignored)
  agent-runs/              # Per-bead agent outputs (runtime, gitignored)

templates/
  agents/              # Guardrail templates: planner.md, developer.md, tester.md, …
                       # Placeholders ({{LANGUAGE}}, {{TEST_COMMAND}}, …) are
                       # substituted with your prompt answers during init.
  skills/              # Subagent skill catalog (core/, role/, capability/, task/)
                       # Primary skill source; copied into exec_root for Codex discovery.

.agents/
  skills/              # Operator-facing skill overrides (memory/, skill-spec-management/)
                       # Custom exceptions only; falls back to templates/skills/ for any
                       # skill not overridden here.

.claude/
  skills/              # Claude Code operator-facing skills

docs/
  memory/
    conventions.md     # Project conventions read by agents at runtime
    known-issues.md    # Known issues and workarounds; language-specific hints added
                       # for canonical stacks only: Node.js, TypeScript, Go.
                       # The "Other" free-text stack receives no language-specific block.

specs/
  HOWTO.md             # Guidance on writing effective specs
  done/                # Archive directory for completed specs
  drafts/              # Working directory for draft specs
```

### The Assets Manifest

`takt init` records a SHA-256 fingerprint of every bundled file it installs into `.takt/assets-manifest.json`. This manifest is the reference point that `takt upgrade` uses to determine what has changed between takt versions.

Each tracked entry records three fields:

| Field | Description |
|-------|-------------|
| `sha256` | SHA-256 of the file as installed |
| `source` | `"bundled"` (installed by takt) or `"user"` (added directly by you) |
| `user_owned` | `true` means the file will never be overwritten by `takt upgrade` |

**Guardrail templates** (`templates/agents/*.md`) are marked `user_owned: true` at install time because `takt init` substitutes project-specific placeholders into them. Their on-disk content always differs from the bundled source, so automatic upgrades would overwrite your customisations. You can customise them freely without risk of a future `takt upgrade` reverting your changes.

If the manifest already exists when you run `takt init` again (for example to add a new file), the manifest is left untouched and a notice is printed directing you to run `takt upgrade` instead.

`.gitignore` is updated automatically with entries for the runtime-only `.takt/` subdirectories. Specifically, `takt init` appends the following block (skipping any lines already present):

```
# takt
.takt/worktrees/
.takt/telemetry/
.takt/logs/
.takt/agent-runs/
```

If `.gitignore` does not exist it is created. If all four entries are already present no changes are made.

### Generated config.yaml

The `.takt/config.yaml` produced by `generate_config_yaml` reflects your prompt answers in the `common` block. The `codex` and `claude` blocks are written with standard defaults:

```yaml
# Orchestrator configuration — generated by `orchestrator init`.
# Edit this file to customise settings. Missing keys use built-in defaults.

common:
  default_runner: claude         # from "Runner backend" prompt
  test_command: pytest           # from "Test command" prompt
  # max_workers is a CLI flag: takt run --max-workers 1
  # memory_cache_dir: /path/to/model-cache   # optional; defaults to ~/.cache/agent-takt/models

codex:
  binary: codex
  skills_dir: .agents
  flags:
    - "--skip-git-repo-check"
    - "--full-auto"
    - "--color"
    - "never"

claude:
  binary: claude
  skills_dir: .claude
  flags:
    - "--dangerously-skip-permissions"
  timeout_seconds: 900
  model_default: claude-sonnet-4-6
```

Key points:
- `max_workers` is intentionally **not** a config file key — it is a CLI flag passed to `takt run --max-workers N`. The comment in the generated file serves as a reminder of the value you chose.
- `memory_cache_dir` sets the directory where the ONNX embedding model is cached. When omitted, the model is stored at `~/.cache/agent-takt/models`. Override this in environments where the home directory is not writable (e.g. CI), or to share the model cache across projects. The value is applied process-wide so that all `takt memory` subcommands and `takt init` use the same location.
- Any key omitted from this file falls back to takt's built-in defaults at load time (see `config.py`).
- The `codex` and `claude` blocks are always written, regardless of which runner you selected; you can use either backend at any time by passing `--runner codex` or `--runner claude` to `takt run`.

### Automatic Git Commit

After all files are written, `takt init` stages the scaffolded paths and creates a single commit:

```
chore: takt init scaffold
```

The following paths are staged and committed:

| Path | Notes |
|------|-------|
| `templates/` | Guardrail templates and subagent skill catalog |
| `.agents/skills/` | Operator skill overrides |
| `.claude/skills/` | Claude Code operator-facing skills |
| `docs/memory/` | Memory seed files |
| `specs/` | `HOWTO.md` + `.gitkeep` sentinels in `drafts/` and `done/` |
| `.takt/config.yaml` | Generated config |
| `.takt/beads/.gitkeep` | Sentinel so the empty beads directory is tracked |
| `.gitignore` | Updated with takt entries |

If nothing has changed (e.g. `--overwrite` was not passed and all files already existed), git will report nothing to commit and the commit step is skipped with a warning — this is expected and harmless.

## Keeping Assets Up to Date

When you update the `agent-takt` package, bundled skill files and other assets may have changed. Running `takt init` again is **not** the right way to pick up these changes — it skips files that already exist and would silently leave you on older versions.

Use `takt upgrade` instead:

```bash
takt upgrade
```

This reads `.takt/assets-manifest.json`, compares every tracked file against the current bundled catalog, and applies the appropriate action for each file.

### What `takt upgrade` Does

| Condition | Action | Output label |
|-----------|--------|--------------|
| File unchanged since install; bundle matches disk | Skip silently | `[up-to-date]` in dry-run only |
| File unchanged since install; bundle has a newer version | Overwrite with bundle | `[updated]` |
| File present in bundle but absent from manifest (new in this release) | Install | `[new]` |
| File tracked in manifest, still in bundle, but deleted from disk | Restore from bundle | `[restored]` |
| File tracked in manifest but **removed** from the current bundle | Rename to `.disabled` | `[disabled — removed from bundle]` |
| File on disk under a bundled prefix, not in manifest or bundle | Record in manifest as user-owned | `[tracked — user-owned]` |
| `user_owned: true` in manifest | Skip unconditionally | `[skipped — user-owned]` |
| Disk SHA differs from manifest SHA (you edited the file) | Skip | `[skipped — locally modified]` |

Files you have edited locally are never overwritten. If the bundle has a newer version of a file you have modified, it is skipped and listed at the end of the output so you can review the difference manually.

After a successful run, `upgraded_at` is written into the manifest.

### Automatic Git Commit

After applying all file changes, `takt upgrade` stages the modified paths and creates a single commit (using the same helper as `takt init`):

```
chore: takt upgrade scaffold
```

This mirrors the behavior of `takt init` and keeps your repository state consistent with the applied asset versions. If no files were changed during the upgrade (all assets were already up-to-date or skipped), git will report nothing to commit and the commit step is silently skipped.

### Dry-Run Mode

To preview what an upgrade would do without writing any files:

```bash
takt upgrade --dry-run
```

Dry-run prints the full action plan — including `[up-to-date]` entries that are silently skipped in normal mode — but makes no changes to disk, does not update the manifest, and does not create a git commit. The output will include a `[dry-run] would commit upgraded assets` line confirming that a commit would be created on a real run.

### Config Key Merging

`takt upgrade` also performs a non-destructive merge of `.takt/config.yaml`. Any keys present in the bundled default config that are missing from your file are added with their default values. Keys you have already set are never overwritten. New keys are reported in a separate "Config additions" section at the end of the output.

### Removed Bundled Assets

When takt removes a file from the bundled catalog in a new release, `takt upgrade` renames the on-disk copy to `<filename>.disabled` rather than deleting it. This prevents silent data loss if you had customised the file. Review `.disabled` files after upgrading and delete them once you are satisfied the change is intentional.

### Asset Ownership

Use `takt asset mark-owned` to tell the upgrade command to permanently skip a file, even if the bundle has a newer version:

```bash
# Protect all skill files from automatic upgrades
takt asset mark-owned ".agents/skills/**"

# Protect a single guardrail template
takt asset mark-owned "templates/agents/developer.md"
```

Ownership is stored in `.takt/assets-manifest.json`. Once marked, the file receives the `[skipped — user-owned]` treatment on every future `takt upgrade` run.

To re-enable upgrade management for a file:

```bash
takt asset unmark-owned ".agents/skills/core/**"
```

Note: Files with `source: user` (files you added directly, not installed by takt) always remain user-owned and cannot be unmarked.

### Listing Asset Status

To see the current status of all tracked assets:

```bash
takt asset list
```

This prints a table with four columns:

| Column | Description |
|--------|-------------|
| `PATH` | Project-relative path |
| `STATUS` | Current upgrade status (up-to-date, update available, locally modified, etc.) |
| `SOURCE` | `bundled` (installed by takt) or `user` (added by you) |
| `OWNED` | `yes` if `user_owned: true`; `no` otherwise |

## Post-Init Project Ownership

After `takt init` copies assets into your repository, **those files belong to your project**. This means:

- **Templates** (`templates/agents/*.md`) — edit these to tune agent guardrails for your stack. Changes take effect on the next scheduler run. Templates are marked `user_owned: true` in the manifest and are never overwritten by `takt upgrade`.
- **Subagent skills** (`templates/skills/`) — the primary skill catalog used by worker agents. Customise these to change what each agent type can do. To protect a skill from automatic upgrades, run `takt asset mark-owned "<glob>"`.
- **Operator skill overrides** (`.agents/skills/` and `.claude/skills/`) — local exceptions that override a bundled skill without touching `templates/skills/`. Only skills that differ from the bundled defaults need to be present here.
- **Memory files** (`docs/memory/conventions.md`, `docs/memory/known-issues.md`) — keep these up to date as your project evolves. Agents read them at runtime for project-specific context. These files are not tracked in the manifest and are never touched by `takt upgrade`.
- **Config** (`.takt/config.yaml`) — adjust runner settings, timeouts, test commands, parallel worker count, and the ONNX model cache directory (`common.memory_cache_dir`) here. `takt upgrade` will add missing keys from new releases but will not overwrite values you have set.

Running `takt init --overwrite` will re-copy bundled defaults on top of any local changes, so avoid that after you have customised your files. Use `takt upgrade` for routine asset updates after a package upgrade.

## Verifying the Setup

After init, confirm everything is in place:

```bash
takt summary
```

This should print bead counts (all zeros on a fresh project) without errors. You are ready to plan your first spec.
