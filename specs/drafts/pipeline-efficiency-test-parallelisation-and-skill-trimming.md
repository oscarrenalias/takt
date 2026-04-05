---
name: "Pipeline Efficiency: Test Parallelisation and Skill Trimming"
id: spec-230d82df
description: Switch to parallel pytest execution and trim redundant Python-specific skill content. Depends on project onboarding defining the default skill catalog story.
dependencies:
- spec-5db8d0ec
priority: medium
complexity: medium
status: draft
tags:
- pipeline
- efficiency
- python-specific
scope:
  in: pyproject.toml, config.yaml, .agents/skills core/role/capability files
  out: models.py, prompts.py, runner.py, TUI, onboarding
feature_root_id: null
---

# Pipeline Efficiency: Test Parallelisation and Skill Trimming

## Objective

Reduce two concrete costs in the Python/uv stack:
1. **Test suite wall-clock time** — tester beads are 2.4x slower than any other agent type (300–500s for the full suite). Parallel pytest execution should bring this under 2 minutes.
2. **Developer prompt token overhead** — static skill content is the largest per-bead cost driver. Several skills repeat content already in CLAUDE.md or each other; trimming them reduces tokens sent on every developer bead run.

> **Dependency:** This spec modifies the default skill catalog that `orchestrator init` will copy into new projects. It should be planned and executed **after** the Project Onboarding spec (spec-5db8d0ec) is merged, so that skill trimming decisions are made in the context of what good language-agnostic defaults look like. Doing it before risks optimising for Python in ways that make the defaults worse for other stacks.

---

## Problems to Fix

1. **Tester agents are 2.4x slower than any other type** — full suite runs take 300–500s. `uv run python -m unittest discover -s tests` is single-process sequential. With 1,000+ tests this is the single biggest wall-clock bottleneck.
2. **Developer skill files have significant redundancy** — `core/base-orchestrator/SKILL.md`, `role/developer-implementation/SKILL.md`, and `capability/code-edit/SKILL.md` repeat concepts already covered in CLAUDE.md (bead lifecycle, output schema, file scope rules). Every developer bead pays this overhead on every run.

---

## Changes

### 1. Switch to pytest with parallel execution

Add `pytest` and `pytest-xdist` as dev dependencies and update `test_command` to run in parallel:

```bash
uv run pytest tests/ -n auto -q
```

`-n auto` uses all available CPU cores. On a typical 8-core machine this should reduce the 300–500s suite to under 2 minutes.

**Two-phase rollout** (to manage migration risk):
- Phase 1: run `uv run pytest tests/ -n0 -q` (no parallelism) to verify pytest compatibility with all existing `unittest.TestCase` subclasses
- Phase 2: enable `-n auto` once phase 1 is green

**Files to change:**
- `pyproject.toml` — add `pytest` and `pytest-xdist` to `dev` dependencies
- `.orchestrator/config.yaml` — update `test_command` to `uv run pytest tests/ -n auto -q`

Note: pytest is backwards-compatible with `unittest.TestCase` subclasses; `setUp`/`tearDown`, `addCleanup`, and `assertX` methods all work. Verify any tests that rely on test execution ordering.

### 2. Trim redundant skill content

Audit and reduce the three developer-facing skill files. Target: ≥25% reduction in total character count without losing actionable guidance.

**Skills to trim:**

| File | What to remove |
|---|---|
| `.agents/skills/core/base-orchestrator/SKILL.md` | Anything that duplicates CLAUDE.md §Key Concepts (bead lifecycle, agent types, branch naming) |
| `.agents/skills/role/developer-implementation/SKILL.md` | Generic "write clean code" advice; anything covered by base-orchestrator |
| `.agents/skills/capability/code-edit/SKILL.md` | General coding advice; keep only edit-specific mechanics |

**Guiding principle:** skills should contain *pointers* to authoritative sources rather than duplicating their content. Replace multi-paragraph explanations with a single reference line:

```
# Bead lifecycle and scheduler rules: see CLAUDE.md §Key Concepts
```

**Review gate:** the trimmed skill files must pass a review bead before merge. Skill quality directly affects agent behaviour — the reviewer must confirm no actionable guidance was removed.

---

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | Add `pytest`, `pytest-xdist` to dev dependencies |
| `.orchestrator/config.yaml` | Update `test_command` to `uv run pytest tests/ -n auto -q` |
| `.agents/skills/core/base-orchestrator/SKILL.md` | Trim content duplicated in CLAUDE.md |
| `.agents/skills/role/developer-implementation/SKILL.md` | Trim content duplicated in base-orchestrator or CLAUDE.md |
| `.agents/skills/capability/code-edit/SKILL.md` | Trim generic coding advice; keep edit-specific guidance only |

---

## Acceptance Criteria

- `uv run pytest tests/ -n auto -q` passes all existing tests
- Test suite wall-clock time is under 2 minutes on a standard laptop
- `.orchestrator/config.yaml` `test_command` updated to use pytest
- Total character count of the three trimmed skill files reduced by ≥25% from baseline
- No agent behaviour regression introduced by skill trimming (review bead must approve)
- Trimmed content is either genuinely redundant or replaced by a pointer to the authoritative source
- CLAUDE.md is not lengthened to compensate for trimmed skills

---

## Pending Decisions

### 1. Pytest migration risk
Some tests may rely on `unittest` execution ordering. Should we run a dry-run with `-n0` (single process, no ordering changes) first before enabling parallelism? **Yes — two-phase rollout as described above.**

### 2. Skill trimming scope
The ≥25% target is an estimate. Agent decides what to cut; review bead approves. If the review bead rejects the trim as too aggressive, the target should be revisited rather than forcing thin skill files.

### 3. Skill defaults and onboarding
Once Project Onboarding (spec-5db8d0ec) ships, the trimmed skill files become the defaults that `orchestrator init` copies. Trimming decisions made here will affect all new projects. Ensure the trimmed content is genuinely language-agnostic before finalising.
