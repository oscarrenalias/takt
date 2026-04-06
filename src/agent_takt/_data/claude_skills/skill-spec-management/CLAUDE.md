# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`skill-spec-management` is a skill packaged as an APM (Agent Package Manager) module. It provides a spec-driven development workflow via `spec.py` — a CLI tool for managing spec files through a lifecycle of `draft → planned → done`.

The skill is declared in `SKILL.md` (frontmatter + usage docs) and implemented in `spec.py`. The `apm.yml` file is the APM package manifest.

## Running Tests

```bash
python3 -m unittest discover -s tests/
# or
uv run python -m unittest discover -s tests/
```

To run a single test class:

```bash
python3 -m unittest tests.test_spec.TestCmdCreate
```

## Architecture

### `spec.py`

Standalone Python CLI with no external dependencies. Key layers:

- **Frontmatter parsing** (`_split_frontmatter`, `parse_frontmatter`, `write_frontmatter`) — reads/writes YAML frontmatter from `.md` files while preserving the body.
- **Spec discovery** (`find_all_specs`) — scans `specs/drafts/`, `specs/planned/`, `specs/done/` for `.md` files.
- **Resolution** (`resolve_spec`) — accepts a full spec ID (e.g. `spec-a3f19c2b`) or a partial filename substring; exits with a clear error on zero or ambiguous matches.
- **Command implementations** (`cmd_init`, `cmd_create`, `cmd_list`, `cmd_show`, `cmd_set`, `cmd_migrate`) — one function per subcommand, wired up via `argparse`.

`set status` is the only supported way to move a spec between lifecycle folders — it atomically updates the frontmatter and calls `shutil.move`.

### Spec lifecycle

```
specs/drafts/    →  specs/planned/  →  specs/done/
  (draft)            (planned)           (done)
```

Transition to `planned` requires beads to be persisted by `orchestrator plan --write`. Transition to `done` requires all beads merged to main via `orchestrator merge`.

### Frontmatter schema

New specs are created with: `name`, `id` (`spec-<8-hex>`), `description`, `dependencies`, `priority`, `complexity`, `status`, `tags`, `scope` (`in`/`out`), `feature_root_id`.

Legacy specs (no frontmatter) are supported — use `spec.py migrate` to add frontmatter.

### `tests/test_spec.py`

Tests use `_TempDirTest` as a base class, which `chdir`s into a fresh `tempfile.mkdtemp()` for each test so the global `SPECS_DIR` constant resolves correctly without touching the real filesystem.
