---
name: 'Pipeline Efficiency: Test Parallelisation and Skill Trimming'
id: spec-230d82df
description: Switch to parallel pytest execution, trim redundant skill content, and
  generalise the tester guardrail template to remove Python-specific commands. Depends
  on project onboarding defining the default skill catalog story.
dependencies:
- spec-5db8d0ec
priority: medium
complexity: medium
status: planned
tags:
- pipeline
- efficiency
- python-specific
scope:
  in: pyproject.toml, config.yaml, .agents/skills core/role/capability files, templates/agents/tester.md
  out: models.py, prompts.py, runner.py, TUI, onboarding
feature_root_id: B-7fbb44e7
---

# Pipeline Efficiency: Test Parallelisation and Skill Trimming

## Objective

Reduce three concrete costs in the Python/uv stack:
1. **Test suite wall-clock time** — tester beads are 2.4x slower than any other agent type (300–500s for the full suite). Parallel pytest execution should bring this under 2 minutes.
2. **Developer prompt token overhead** — static skill content is the largest per-bead cost driver. Several skills repeat content already in CLAUDE.md or each other; trimming them reduces tokens sent on every developer bead run.
3. **Tester guardrail template is Python-specific** — `templates/agents/tester.md` embeds `uv run python -m unittest` commands, making it unsuitable as a language-agnostic default for new projects onboarded via `orchestrator init`.

> **Dependency:** This spec modifies the default skill catalog that `orchestrator init` will copy into new projects. It should be planned and executed **after** the Project Onboarding spec (spec-5db8d0ec) is merged, so that skill trimming decisions are made in the context of what good language-agnostic defaults look like. Doing it before risks optimising for Python in ways that make the defaults worse for other stacks.

---

## Problems to Fix

1. **Tester agents are 2.4x slower than any other type** — full suite runs take 300–500s. `uv run python -m unittest discover -s tests` is single-process sequential. With 1,000+ tests this is the single biggest wall-clock bottleneck.
2. **Developer skill files have significant redundancy** — `core/base-orchestrator/SKILL.md`, `role/developer-implementation/SKILL.md`, and `capability/code-edit/SKILL.md` repeat concepts already covered in CLAUDE.md (bead lifecycle, output schema, file scope rules). Every developer bead pays this overhead on every run.
3. **Tester guardrail template hardcodes Python** — `templates/agents/tester.md` references `uv run python -m unittest tests.<module_name> -v` directly. The template defines the tester *persona and behavioral constraints* (what to do, what not to do, output format); it should be silent on which test runner to use. The `capability/test-execution` skill is the correct place for runner-specific mechanics — it is project-specific by design and already contains the Python/uv instructions. The template should defer to that skill rather than duplicating runner syntax.

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

### 3. Generalise `templates/agents/tester.md`

Remove the two Python-specific command references from the tester guardrail template and replace them with language-agnostic guidance that defers to the `capability/test-execution` skill for runner mechanics.

**Current (Python-specific):**
```
- Run only the test files related to the bead's changed files, not the full test suite.
  Use `uv run python -m unittest tests.<module_name> -v` to target individual test files
  rather than `discover`.
```
```
- Run the full test suite with `discover`. Always target the specific module:
  `uv run python -m unittest tests.<module_name> -v`.
```

**Replacement (language-agnostic):**
```
- Run only the tests related to the bead's changed files — never the full suite.
  Refer to the `capability/test-execution` skill for the correct command syntax for this project.
```
```
- Run the full test suite. Always scope to the modules or files touched by this bead.
  Refer to the `capability/test-execution` skill for targeted invocation syntax.
```

The `capability/test-execution` skill is intentionally project-specific and already contains the correct Python/uv invocation patterns. The template must not duplicate that content — it defines the *what* (run targeted tests, not the full suite), the skill defines the *how* (which command to use).

---

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | Add `pytest`, `pytest-xdist` to dev dependencies |
| `.orchestrator/config.yaml` | Update `test_command` to `uv run pytest tests/ -n auto -q` |
| `.agents/skills/core/base-orchestrator/SKILL.md` | Trim content duplicated in CLAUDE.md |
| `.agents/skills/role/developer-implementation/SKILL.md` | Trim content duplicated in base-orchestrator or CLAUDE.md |
| `.agents/skills/capability/code-edit/SKILL.md` | Trim generic coding advice; keep edit-specific guidance only |
| `templates/agents/tester.md` | Remove Python-specific command references; replace with language-agnostic guidance deferring to `capability/test-execution` skill |

---

## Acceptance Criteria

- `uv run pytest tests/ -n auto -q` passes all existing tests
- Test suite wall-clock time is under 2 minutes on a standard laptop
- `.orchestrator/config.yaml` `test_command` updated to use pytest
- Total character count of the three trimmed skill files reduced by ≥25% from baseline
- No agent behaviour regression introduced by skill trimming (review bead must approve)
- Trimmed content is either genuinely redundant or replaced by a pointer to the authoritative source
- CLAUDE.md is not lengthened to compensate for trimmed skills
- `templates/agents/tester.md` contains no references to `uv`, `python`, `unittest`, or any other language/runner-specific syntax
- The tester template defers all runner-specific command guidance to the `capability/test-execution` skill

---

## Pending Decisions

### 1. Pytest migration risk
Some tests may rely on `unittest` execution ordering. Should we run a dry-run with `-n0` (single process, no ordering changes) first before enabling parallelism? **Yes — two-phase rollout as described above.**

### 2. Skill trimming scope
The ≥25% target is an estimate. Agent decides what to cut; review bead approves. If the review bead rejects the trim as too aggressive, the target should be revisited rather than forcing thin skill files.

### 3. Skill defaults and onboarding
Once Project Onboarding (spec-5db8d0ec) ships, the trimmed skill files become the defaults that `orchestrator init` copies. Trimming decisions made here will affect all new projects. Ensure the trimmed content is genuinely language-agnostic before finalising.
