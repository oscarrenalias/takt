---
name: Move subagent Codex skills to templates-skills with operator exceptions
id: spec-f423c232
description: Move subagent-only Codex skill templates out of repo-root .agents/skills
  into templates/skills while preserving operator-only skills in .agents/skills.
dependencies: null
priority: medium
complexity: null
status: planned
tags:
- skills
- codex
- templates
- subagents
scope:
  in: null
  out: null
feature_root_id: B-86188f5b
---

# Move subagent Codex skills to templates-skills with operator exceptions

## Objective

Move the repository source of subagent-only Codex skills out of repo-root `.agents/skills/` and into `templates/skills/`, while keeping the subagent runtime behavior unchanged: Codex worker execution roots should still receive allowlisted skills under `exec_root/.agents/skills/`.

This change separates two concerns that are currently conflated. The orchestrator/operator should not auto-discover the full subagent skill catalog from the repository root, but subagents still need that catalog copied into their isolated execution roots. The operator-only skills `memory` and `spec-management` must remain in `.agents/skills/` and must not be moved into `templates/skills/`.

## Problems to Fix

1. The repository currently places the full Codex skill catalog under `.agents/skills/`, which makes subagent bootstrap assets look like operator-facing repo-root skills.
2. The orchestrator/operator can see the same repo-root `.agents/skills/` tree that is intended primarily as source material for subagent execution environments.
3. `skills.py`, onboarding, upgrade logic, tests, and docs currently treat `.agents/skills/` as both the repository source of truth and the runtime discovery destination, even though those are different concerns.
4. There is no explicit split between operator-only Codex skills and subagent-only Codex skill templates.
5. The exception set is important: `memory` and `spec-management` are intentionally operator-facing and must remain available under `.agents/skills/`.

## Changes

### 1. Introduce `templates/skills/` as the source catalog for subagent-only Codex skills

Create a new repository path:

```text
templates/skills/
  core/
  role/
  capability/
  task/
```

Move all Codex skills that exist only to bootstrap subagents from `.agents/skills/` into this new tree.

These moved skills remain authored in the repository and bundled as package data, but their source location is now `templates/skills/`, not `.agents/skills/`.

### 2. Preserve operator-only skills in `.agents/skills/`

Do not remove the operator-facing skills that are meant to stay available to the orchestrator/operator in the repository root:

- `.agents/skills/memory/`
- `.agents/skills/.../spec-management/`

The implementation should preserve the current `spec-management` skill exactly as-is in its current repo-root `.agents/skills/` location. It must not be renamed, moved, or re-namespaced as part of this change.

After this change, repo-root `.agents/skills/` is no longer the full subagent catalog. It becomes the operator-facing exception area that intentionally remains discoverable in the repository root.

### 3. Change Codex subagent source lookup to read from `templates/skills/`

In `src/agent_takt/skills.py`, change the source-side skill lookup used for Codex subagent bootstrapping:

- source lookup for subagent-only skills should resolve from `templates/skills/`
- source lookup for operator-only exceptions should still resolve from `.agents/skills/` when applicable
- the execution-time destination for Codex subagents remains `exec_root/.agents/skills/`

The key distinction is:

- **source in repo:** `templates/skills/` for subagent-only skills
- **destination in exec root:** `.agents/skills/` for Codex runtime discovery

This is not a change to Codex runtime discovery behavior. It is only a change to where the repository stores the source templates that get copied into the isolated execution root.

### 4. Keep allowlist semantics intact

`AGENT_SKILL_ALLOWLIST` should continue to describe which skills each subagent type receives. The behavior should remain:

- planner/developer/tester/review/documentation/investigator/recovery get the same subagent skill IDs they currently get
- those allowlisted IDs now map to source directories under `templates/skills/` for subagent-only skills
- `memory` remains a special case that is still sourced from `.agents/skills/`
- if `spec-management` is included in any future or existing operator-facing workflow, it remains sourced from `.agents/skills/`

The change should avoid duplicating the same skill definition in both locations unless a short transitional compatibility window is explicitly required.

### 5. Update onboarding, upgrade, and packaged asset wiring

The project currently treats `.agents/skills/` as a bundled/scaffolded asset root. That wiring must be split so that:

- `templates/skills/` is bundled and installed as the source catalog for subagent-only Codex skills
- `.agents/skills/` is scaffolded only for the operator-facing exceptions that remain there
- upgrade manifest logic tracks the new asset roots correctly
- generated project scaffolds and package-data helpers reflect the new location accurately

If the implementation keeps copying a bundled skill catalog into initialized projects, that copied catalog should land in `templates/skills/` for subagent-only Codex skills, not in `.agents/skills/`.

### 6. Update docs and wording to reflect the split model

Documentation should clearly distinguish:

- operator-facing repo-root skills in `.agents/skills/`
- subagent skill templates in `templates/skills/`
- execution-root Codex discovery in `exec_root/.agents/skills/`

Any doc that currently says “Codex skills live in `.agents/skills/`” should be revised to describe the new split accurately.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/skills.py` | Change subagent skill source lookup from repo-root `.agents/skills/` to `templates/skills/`, while preserving operator-only exceptions in `.agents/skills/` and keeping execution-root destination as `.agents/skills/`. |
| `src/agent_takt/_assets.py` | Add/update packaged asset helpers so bundled subagent Codex skill templates resolve from `templates/skills/` rather than the current `.agents/skills/` bundle path. |
| `pyproject.toml` | Update package-data entries so the new bundled subagent Codex skill template tree is included. |
| `src/agent_takt/onboarding/assets.py` | Update asset installation helpers to install subagent Codex skill templates into `templates/skills/` and leave only operator exceptions under `.agents/skills/`. |
| `src/agent_takt/onboarding/scaffold.py` | Update scaffolded paths, success messages, and staged git paths to reflect the new split between `templates/skills/` and `.agents/skills/`. |
| `src/agent_takt/onboarding/upgrade.py` | Update bundled asset catalog generation, manifest prefixes, and upgrade evaluation logic for the new asset roots. |
| `tests/test_assets.py` | Update packaged asset helper expectations and bundled-skill fallback tests for the new subagent source location and operator exceptions. |
| `tests/test_config_wiring_phase3.py` | Update tests that create/copy skill catalogs or assert source layout assumptions. |
| `tests/test_onboarding_assets.py` | Update scaffold/install expectations for the new skill template location and preserved operator exceptions. |
| `tests/test_onboarding_upgrade.py` | Update upgrade-manifest expectations and asset-prefix assertions. |
| `tests/test_cli_upgrade.py` | Update asset ownership and upgrade command expectations where paths move from `.agents/skills/**` to `templates/skills/**`. |
| `docs/multi-backend-agents.md` | Document the new split: repo source templates in `templates/skills/`, operator exceptions in `.agents/skills/`, execution-root Codex discovery still under `.agents/skills/`. |
| `docs/onboarding.md` | Update initialized project layout and upgrade guidance. |
| `docs/development.md` | Update repository layout descriptions for Codex skill assets. |
| `CLAUDE.md` | Update project layout and architecture notes so they no longer describe the entire subagent Codex catalog as living in repo-root `.agents/skills/`. |
| `templates/agents/tester.md` | Update any explicit references that point to repo-root `.agents/skills/...` for subagent-only capability skills. |
| `templates/skills/**` | New location for subagent-only Codex skill templates. |
| `.agents/skills/memory/**` | Preserve as operator-facing skill content. |
| `.agents/skills/**/spec-management/**` | Preserve exactly where it already lives today as operator-facing skill content. |

## Acceptance Criteria

- All subagent-only Codex skills that were previously sourced from repo-root `.agents/skills/` are instead sourced from `templates/skills/`.
- Codex subagent execution roots still contain allowlisted skills under `exec_root/.agents/skills/`; there is no behavior change to runtime skill discovery for subagents.
- Repo-root `.agents/skills/` no longer contains the full subagent-only catalog.
- Repo-root `.agents/skills/` still contains the operator-only exceptions for `memory` and `spec-management`.
- The existing repo-root `spec-management` skill remains exactly where it is today and is still available to the top-level operator agent.
- `prepare_isolated_execution_root()` or its equivalent source-resolution path copies subagent-only skills from `templates/skills/` into the Codex execution root.
- Packaging, onboarding, and upgrade flows correctly track `templates/skills/` as the subagent Codex skill template source and do not regress the preserved `.agents/skills/` exceptions.
- Tests are updated to cover the new split source model and pass under the project’s normal test command.
- Documentation consistently describes the new model without claiming that the full subagent Codex catalog lives in repo-root `.agents/skills/`.

## Pending Decisions

- ~~Should the Codex subagent runtime destination also move away from `.agents/skills/`?~~ Resolved: no. Only the repository source location moves. Codex subagents should still receive skills under `exec_root/.agents/skills/`.
- ~~What should happen to the existing repo-root `spec-management` skill?~~ Resolved: keep it exactly where it is today in repo-root `.agents/skills/`; do not rename or move it.
