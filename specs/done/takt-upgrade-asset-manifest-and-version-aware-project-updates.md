---
name: "takt upgrade: asset manifest and version-aware project updates"
id: spec-539d4bbe
description: "Ship a manifest at init time so takt upgrade can distinguish takt-owned, user-modified, and user-owned files and update safely"
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- cli
- onboarding
- upgrade
scope:
  in: "takt init manifest write, takt upgrade command, config.yaml key-merge, user-customised skill detection"
  out: "automatic conflict resolution, GUI diff tool, downgrade support"
feature_root_id: null
---
# takt upgrade: asset manifest and version-aware project updates

## Objective

When a new version of takt is released it ships updated skills, guardrail templates, and config structure. Projects initialised with an older version have no upgrade path: re-running `takt init` either skips every existing file or blindly overwrites everything including user customisations. A manifest-based upgrade command lets takt update what it owns, preserve what users have changed, and surface new assets from the new release — without requiring the user to manually diff two versions.

## Problems to Fix

1. `takt init --overwrite` destroys user-customised guardrail templates and skill files.
2. `takt init` (without `--overwrite`) silently skips all files on a re-run, so new assets shipped in newer versions are never installed.
3. There is no mechanism to detect whether a shipped file has been edited by the user vs left as installed.
4. New takt versions may add config keys (e.g. new `transient_block_patterns`, new agent types) that existing projects never receive.
5. Users who extend skill files for their own stack (e.g. a Rails skill, a Rust skill, dedicated domain knowledge) need those changes preserved unconditionally across upgrades.

## Changes

### 1. `.takt/assets-manifest.json` — written at `takt init` time

After installing all assets, `scaffold_project()` writes `.takt/assets-manifest.json` containing:

```json
{
  "takt_version": "0.1.8",
  "installed_at": "2026-04-07T10:00:00Z",
  "assets": {
    "templates/agents/developer.md": {
      "sha256": "abc123...",
      "source": "bundled",
      "user_owned": false
    },
    ".agents/skills/core/base-orchestrator/SKILL.md": {
      "sha256": "def456...",
      "source": "bundled",
      "user_owned": false
    }
  }
}
```

Fields per entry:
- `sha256` — SHA-256 of the file content at install time (hex digest)
- `source` — `"bundled"` for takt-shipped assets; `"user"` for files added by the user (not currently in bundled catalog)
- `user_owned` — when `true`, the upgrade command unconditionally skips this file

The manifest covers:
- `templates/agents/*.md`
- `.agents/skills/**/*`
- `.claude/skills/**/*`
- `.takt/config.yaml`

It does NOT cover `docs/memory/`, `specs/`, or `CLAUDE.md` — those are always user-owned and never touched by upgrade.

### 2. `takt upgrade` command

New CLI subcommand. Reads the installed manifest, compares against the newly shipped assets, and for each file applies the following decision table:

| Condition | Action |
|---|---|
| `user_owned: true` in manifest | Skip unconditionally, print `[skipped — user-owned]` |
| File not in manifest (new asset in this takt version) | Install, add to manifest, print `[new]` |
| `current sha == manifest sha` (unmodified since install) | Overwrite with new bundled version, update manifest sha, print `[updated]` |
| `current sha != manifest sha` (user-modified) | Skip, print `[skipped — locally modified]`, include in end summary |
| File in manifest but missing on disk | Restore from bundled assets, update manifest, print `[restored]` |

After processing all assets, `takt upgrade` runs the config merge step (see below) and prints a summary: counts of updated / skipped / new / restored files, and a list of locally-modified files the user should review manually.

### 3. Config key merge for `.takt/config.yaml`

Config is not handled by hash comparison — it always contains user values (runner, test command, model overrides) that must be preserved. Instead, `takt upgrade` performs a deep key-merge:

1. Parse the user's existing `.takt/config.yaml` with PyYAML.
2. Parse the bundled `default_config.yaml`.
3. For every key present in the bundled config but absent in the user's config, insert the key with its default value (recursively, so nested keys like `scheduler.transient_block_patterns` are handled).
4. Never remove or overwrite keys the user already has.
5. Write the merged result back to `.takt/config.yaml`.
6. Print each added key: `[config] added missing key: common.scheduler.transient_block_patterns`.

This handles new scheduler options, new agent types, new transient block patterns, and new backend flags added in future takt versions.

### 4. `takt asset mark-owned <glob>` — user ownership marking

Users who add or customise skill files for their stack (e.g. a domain-specific skill with knowledge about their framework, a specialised test runner skill) can permanently protect those files from upgrades:

```bash
takt asset mark-owned ".agents/skills/capability/test-execution/**"
takt asset mark-owned "templates/agents/developer.md"
takt asset mark-owned ".agents/skills/custom/rails-conventions/**"
```

This sets `user_owned: true` in the manifest for all matched paths. `takt upgrade` skips these unconditionally, even if the bundled version has changed. The reverse command `takt asset unmark-owned <glob>` restores default upgrade behaviour for matched paths.

User-added skill files (present on disk, not in the bundled catalog) are automatically recorded in the manifest with `source: "user"` and `user_owned: true` when `takt upgrade` first encounters them, so they are never deleted or overwritten.

`takt asset list` prints all tracked assets with their ownership and modification status:

```
templates/agents/developer.md      unmodified   bundled
templates/agents/tester.md         modified     bundled    ← locally modified
.agents/skills/core/base-...       unmodified   bundled
.agents/skills/custom/rails/...    —            user-owned
```

### 5. `takt init` manifest integration

`scaffold_project()` writes `.takt/assets-manifest.json` as its final step, after all assets are installed. If the manifest already exists (re-running `takt init` on an existing project), `takt init` prints a notice: `assets-manifest.json already exists — run 'takt upgrade' to update assets` and skips manifest creation.

### 6. New helpers in `onboarding.py` and `cli.py`

**`onboarding.py`:**
- `write_assets_manifest(project_root, installed_files)` — compute sha256 for each installed file and write the manifest
- `read_assets_manifest(project_root)` — load and parse the manifest; return empty dict if missing
- `merge_config_keys(user_config_path, bundled_config_path)` — deep-merge bundled defaults into user config, return (merged_dict, list_of_added_keys)

**`cli.py`:**
- `command_upgrade(args, storage, console)` — implements the upgrade decision table and config merge
- `command_asset(args, storage, console)` — dispatches `mark-owned`, `unmark-owned`, `list` subsubcommands
- Argument parser entries for `takt upgrade` (`--dry-run`) and `takt asset <subcommand>`

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/onboarding.py` | Add `write_assets_manifest()`, `read_assets_manifest()`, `merge_config_keys()`; call manifest write at end of `scaffold_project()` |
| `src/agent_takt/cli.py` | Add `upgrade` and `asset` subcommands with argument parsers; implement `command_upgrade()` and `command_asset()` |

## Acceptance Criteria

- `takt init` on a fresh project produces `.takt/assets-manifest.json` with sha256 entries for all installed assets and the current takt version
- `takt upgrade` on an unmodified project updates all bundled files to the latest versions and prints `[updated]` for each changed file
- `takt upgrade` skips a file the user has edited (sha mismatch) and prints `[skipped — locally modified]`; the user's edits are intact after the run
- `takt upgrade` installs a file present in the new takt version but absent from the manifest, prints `[new]`
- `takt upgrade` adds missing config keys from the bundled default without touching existing user values; prints each added key
- `takt asset mark-owned <glob>` causes `takt upgrade` to skip matched files unconditionally on all future runs
- A user-added skill file not in the bundled catalog is auto-recorded as `user_owned: true` when first encountered by `takt upgrade` and is never deleted
- `takt asset list` shows each tracked asset with modification status and ownership
- `takt upgrade --dry-run` prints what would change without writing any files
- Re-running `takt init` on an existing project does not overwrite the manifest; prints notice to run `takt upgrade`
- All manifest operations are idempotent

## Pending Decisions

- For user-modified bundled files, should `takt upgrade` offer to show a diff (`takt asset diff <file>`) between the user's version and the new bundled version? Useful but out of scope for v1.

## Resolved Decisions

- **`takt upgrade` vs `takt init --upgrade`**: `takt upgrade` as a dedicated top-level command. Init and upgrade are distinct operations — init is first-time setup, upgrade is version migration. Combining them under `takt init --upgrade` creates awkward flag interactions and obscures the separation of concerns. `takt upgrade` is also CI-friendly as a standalone post-install step.
- **`--dry-run`**: Confirmed. `takt upgrade --dry-run` prints the full list of what would be updated, skipped, added, and restored — including which config keys would be added — without writing any files. Non-interactive by default (no prompts); `--dry-run` is the preview mechanism.
- **Substituted guardrail templates**: Templates are written to the manifest with `user_owned: true` at init time. `takt upgrade` never touches them. This avoids the sha mismatch problem caused by placeholder substitution (the installed content differs from the bundled content). Users who want updated guardrails must explicitly re-run `takt init --overwrite` or manually copy from the new bundled version.
- **Deleted bundled assets**: When a skill or asset file is present in the manifest but no longer exists in the bundled catalog, `takt upgrade` renames it to `<filename>.disabled` (e.g. `SKILL.md` → `SKILL.md.disabled`). The content is preserved for reference but the file is no longer loaded by the skill catalog. Prints `[disabled — removed from bundle]`.
- **Exit code**: Exit 0 when upgrade completes, even if files were skipped due to local modifications or user ownership — skips are intentional behaviour, not errors. Exit non-zero only on actual failures (unreadable manifest, file write errors). The printed summary communicates which files need manual attention without blocking CI pipelines.
- **Manifest committed to git**: Yes. The manifest records install-time state and must be visible in worktrees so agents can reason about asset provenance. It is included in the `takt init` git commit (see B-165b0761).
