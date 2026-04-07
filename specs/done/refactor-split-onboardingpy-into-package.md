---
name: "Refactor: split onboarding.py into package"
id: spec-4bbbb88b
description: "Split onboarding.py (1182 lines) into a focused package with separate modules for asset installation, upgrade evaluation, config generation, prompt collection, and scaffold orchestration. Split test_onboarding.py accordingly."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- refactoring
- onboarding
scope:
  in: "src/agent_takt/onboarding.py, tests/test_onboarding.py"
  out: "cli.py, scheduler.py, tui.py"
feature_root_id: null
---
# Refactor: split onboarding.py into package

## Objective

`onboarding.py` has grown to 1182 lines covering asset installation, upgrade evaluation, manifest management, config generation, prompt collection, template substitution, memory seeding, gitignore updating, and scaffold orchestration — unrelated concerns bundled into one file. `test_onboarding.py` (1628 lines) covers all of it.

This spec splits both into focused units. No functional changes.

## Principles

- No functional changes. All tests pass after the split.
- Extract, don't rewrite. Move code as-is.
- Preserve public API. `from agent_takt.onboarding import scaffold_project` continues to work via `__init__.py` re-exports.
- Tests follow source.
- No circular imports between new modules.

## Proposed Module Split

`src/agent_takt/onboarding.py` → `src/agent_takt/onboarding/` package:

| New module | Responsibility | Approx lines |
|---|---|---|
| `onboarding/__init__.py` | `scaffold_project()` entry point, re-exports | ~20 |
| `onboarding/assets.py` | `copy_asset_file()`, `copy_asset_dir()`, `install_templates()`, `install_agents_skills()`, `install_claude_skills()`, `install_default_config()`, `resolve_memory_seed()` | ~200 |
| `onboarding/upgrade.py` | `evaluate_upgrade_actions()`, `_compute_bundled_catalog()`, `AssetDecision`, `read_assets_manifest()`, `write_assets_manifest()` | ~300 |
| `onboarding/config.py` | `generate_config_yaml()`, `merge_config_keys()`, `substitute_template_placeholders()`, `install_templates_with_substitution()` | ~130 |
| `onboarding/prompts.py` | `collect_init_answers()`, `_prompt()`, `InitAnswers` | ~130 |
| `onboarding/scaffold.py` | `seed_memory_files()`, `update_gitignore()`, `create_specs_howto()`, `commit_scaffold()`, `_language_specific_known_issues()` | ~250 |

## Proposed Test Split

`tests/test_onboarding.py` (1628 lines) → multiple files:

| New test file | Covers |
|---|---|
| `tests/test_onboarding_assets.py` | Asset installation functions, `copy_asset_file/dir` |
| `tests/test_onboarding_upgrade.py` | Upgrade evaluation, `AssetDecision`, manifest read/write |
| `tests/test_onboarding_config.py` | Config generation, key merging, template substitution |
| `tests/test_onboarding_scaffold.py` | Memory seeding, gitignore update, commit scaffold, `scaffold_project` integration |

`tests/test_onboarding.py` is deleted once all tests are migrated.

## Files to Modify

| Action | File |
|---|---|
| Replace with package | `src/agent_takt/onboarding.py` → `src/agent_takt/onboarding/` |
| New | `tests/test_onboarding_assets.py`, `test_onboarding_upgrade.py`, `test_onboarding_config.py`, `test_onboarding_scaffold.py` |
| Delete after migration | `tests/test_onboarding.py` |

## Acceptance Criteria

- `from agent_takt.onboarding import scaffold_project` works unchanged via re-export
- No onboarding module exceeds 500 lines
- No onboarding test file exceeds 600 lines
- No circular imports within the `onboarding/` package
- `uv run pytest tests/ -n auto -q` passes in full after the split
- `tests/test_onboarding.py` is deleted
