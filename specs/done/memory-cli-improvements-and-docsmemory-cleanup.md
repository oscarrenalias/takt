---
name: Memory CLI improvements and docs/memory cleanup
id: spec-279bf5b7
description: Remove the legacy docs/memory/ file-based memory system and add namespace introspection commands to the takt memory CLI.
dependencies: null
priority: medium
complexity: low
status: done
tags:
- cli
- memory
- cleanup
scope:
  in: "takt memory CLI, onboarding scaffold, _assets.py, docs/memory/ removal"
  out: "SQLite memory engine, embedding model, search behaviour"
feature_root_id: B-4be1edbe
---
# Memory CLI improvements and docs/memory cleanup

## Objective

`docs/memory/` was the original file-based memory store before the SQLite-backed `takt memory` system was introduced. It is now legacy clutter: agents write to `.takt/memory/memory.db` exclusively, and the two seed files (`conventions.md`, `known-issues.md`) are hardcoded scaffolding that no longer serve a purpose. This spec removes the file-based layer entirely and adds two namespace introspection commands so operators can inspect what is in the live database.

## Problems to Fix

1. **`docs/memory/` is dead weight** — `takt init` still seeds `conventions.md` and `known-issues.md` into `docs/memory/`, and the code to do so (`seed_memory_files`, `_CONVENTIONS_CONTENT`, `_KNOWN_ISSUES_CONTENT`, `_language_specific_known_issues`) lives in `scaffold.py`. It is committed to every onboarded project's repo and never updated again.
2. **`--migrate` flag on `takt memory ingest` is obsolete** — it existed solely to import `docs/memory/*.md` into the SQLite database during the migration from the old system. There is nothing left to migrate.
3. **No way to list namespaces** — operators cannot see which namespaces exist in the database without inspecting raw SQL. `takt memory stats` shows counts per namespace but buries them inside a larger JSON blob.
4. **No way to browse recent entries** — there is no command to show what has been recently added to a namespace, making it hard to verify that agents are writing useful memory entries.

## Changes

### 1. Remove `docs/memory/` seeding from the onboarding scaffold

**`src/agent_takt/onboarding/scaffold.py`**
- Delete `_KNOWN_ISSUES_CONTENT`, `_CONVENTIONS_CONTENT` string constants.
- Delete `_language_specific_known_issues(language)` function.
- Delete `seed_memory_files(project_root, answers, *, overwrite)` function.
- In `scaffold_project()`: remove step 6 (the `seed_memory_files` call and its console output).
- In `commit_scaffold()`: remove `"docs/memory/"` from `stage_paths`.

**`src/agent_takt/onboarding/assets.py`**
- Delete `resolve_memory_seed(name)` function.
- Remove `packaged_docs_memory_dir` from the import list.

**`src/agent_takt/_assets.py`**
- Delete `packaged_docs_memory_dir()` function.

**`src/agent_takt/_data/docs/memory/`**
- Delete `conventions.md`, `known-issues.md`, and the `docs/memory/` directory from the package data.

**`docs/memory/`** (project-level)
- Delete `conventions.md`, `known-issues.md`, and the directory itself.

### 2. Remove the `--migrate` flag from `takt memory ingest`

**`src/agent_takt/cli/parser.py`**
- Remove the `--migrate` argument from the `ingest` subparser.

**`src/agent_takt/cli/commands/memory.py`**
- Remove the `if args.migrate:` branch from `_cmd_ingest()`.
- Remove the `_MIGRATE_GLOB` constant.

### 3. Add `takt memory namespace list`

Lists all namespaces present in the database with their entry count.

```bash
uv run takt memory namespace list
```

Example output:
```json
[
  {"namespace": "global", "count": 12},
  {"namespace": "specs", "count": 35},
  {"namespace": "feature:B-2a7ec879", "count": 1}
]
```

**`src/agent_takt/memory.py`**
- Add `list_namespaces(db_path) -> list[dict]` returning namespace + count rows, ordered by count descending.

**`src/agent_takt/cli/commands/memory.py`**
- Add `_cmd_namespace_list(db_path, console)` handler.

**`src/agent_takt/cli/parser.py`**
- Add `namespace` subparser under `memory_subparsers` with `list` sub-subcommand.

**`src/agent_takt/cli/__init__.py`**
- Wire dispatch for `memory_command == "namespace"` → `namespace_command == "list"`.

### 4. Add `takt memory namespace show <namespace>`

Shows the most recent N entries in a given namespace, ordered by insertion time descending.

```bash
uv run takt memory namespace show global
uv run takt memory namespace show "feature:B-2a7ec879" --limit 10
```

Default `--limit` is 5. Output is a JSON array of entries, each with `id`, `namespace`, `text`, and `created_at`.

Example output:
```json
[
  {"id": "uuid-...", "namespace": "global", "text": "Always run uv run pytest ...", "created_at": "2026-04-15T10:00:00Z"},
  ...
]
```

**`src/agent_takt/memory.py`**
- Add `recent_entries(db_path, namespace, *, limit=5) -> list[dict]` returning the most recent entries for the namespace.

**`src/agent_takt/cli/commands/memory.py`**
- Add `_cmd_namespace_show(args, db_path, console)` handler.

**`src/agent_takt/cli/parser.py`**
- Add `show` sub-subcommand under `namespace` with positional `namespace` argument and optional `--limit` (int, default 5).

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/onboarding/scaffold.py` | Remove `seed_memory_files`, content constants, `_language_specific_known_issues`; update `scaffold_project` and `commit_scaffold` |
| `src/agent_takt/onboarding/assets.py` | Remove `resolve_memory_seed` and `packaged_docs_memory_dir` import |
| `src/agent_takt/_assets.py` | Remove `packaged_docs_memory_dir` function |
| `src/agent_takt/_data/docs/memory/conventions.md` | Delete |
| `src/agent_takt/_data/docs/memory/known-issues.md` | Delete |
| `docs/memory/conventions.md` | Delete |
| `docs/memory/known-issues.md` | Delete |
| `src/agent_takt/cli/parser.py` | Remove `--migrate` from `ingest`; add `namespace list` and `namespace show` subcommands |
| `src/agent_takt/cli/commands/memory.py` | Remove migrate branch and `_MIGRATE_GLOB`; add `_cmd_namespace_list` and `_cmd_namespace_show` |
| `src/agent_takt/memory.py` | Add `list_namespaces` and `recent_entries` functions |
| `src/agent_takt/cli/__init__.py` | Wire `namespace` dispatch |
| `tests/test_cli_memory.py` | Add tests for `namespace list` and `namespace show`; remove migrate tests |
| `tests/test_onboarding_upgrade.py` | Remove references to `docs/memory/` seeding |
| `tests/test_assets.py` | Remove references to `packaged_docs_memory_dir` |

## Acceptance Criteria

- `takt init` no longer creates `docs/memory/` or any files within it.
- `packaged_docs_memory_dir()` no longer exists; importing it raises `AttributeError`.
- `resolve_memory_seed()` no longer exists.
- `takt memory ingest` no longer accepts `--migrate`; passing it exits with an argument error.
- `takt memory namespace list` outputs a JSON array of `{namespace, count}` objects for all namespaces in the database, ordered by count descending.
- `takt memory namespace show <ns>` outputs a JSON array of the 5 most recent entries in `<ns>`, each with `id`, `namespace`, `text`, `created_at`.
- `takt memory namespace show <ns> --limit N` returns up to N entries.
- `takt memory namespace show` on a non-existent namespace returns an empty array (no error).
- All existing `takt memory` subcommands (`init`, `add`, `search`, `delete`, `stats`) continue to work unchanged.
- All tests pass.

## Pending Decisions

- None.
