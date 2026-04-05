---
name: Pipeline Efficiency Improvements
id: spec-6906322a
description: "SUPERSEDED — split into spec-3078243f (structured handoffs) and spec-230d82df (pytest + skill trimming). Do not plan this spec."
dependencies: null
priority: null
complexity: null
status: draft
tags:
- superseded
scope:
  in: null
  out: null
feature_root_id: null
---
# Pipeline Efficiency Improvements

> **Superseded.** This spec has been split into two:
> - **spec-3078243f** — [Pipeline Efficiency: Structured Handoffs and Schema](pipeline-efficiency-structured-handoffs-and-schema.md) — language-agnostic, do first
> - **spec-230d82df** — [Pipeline Efficiency: Test Parallelisation and Skill Trimming](pipeline-efficiency-test-parallelisation-and-skill-trimming.md) — Python-specific, do after Project Onboarding
>
> The original content is preserved below for reference.

---

# Pipeline Efficiency Improvements (original)

## Objective

Address three concrete inefficiencies identified from telemetry analysis:
1. **Tester agents are 2.4x slower** than any other type — full suite runs take 300-500s
2. **Developer prompt overhead** is the largest cost driver — static context is re-sent every run
3. **Review agents average 7.4 turns** — high back-and-forth due to thin developer handoffs

---

## Problem 1: Tester Parallelisation

### Current state
Tester beads run the full test suite as a single sequential process: `uv run python -m unittest discover -s tests`. With 1,005 tests, this takes 6-9 minutes. This is the single biggest wall-clock bottleneck in the pipeline.

### Fix: Switch to pytest with parallel execution

Add `pytest` and `pytest-xdist` as dev dependencies and update the test command to run tests in parallel across CPU cores:

```bash
uv run pytest tests/ -n auto -q
```

`-n auto` uses all available CPU cores. On a typical 8-core machine this should reduce the 6-9 minute suite to under 2 minutes.

**Files to change:**
- `pyproject.toml` — add `pytest` and `pytest-xdist` to `dev` dependencies
- `.orchestrator/config.yaml` — update `test_command` to use pytest
- Any tests that rely on `unittest`-specific features (test ordering, `addCleanup` patterns) should be verified to work under pytest — pytest is backwards-compatible with `unittest.TestCase` subclasses

**Acceptance criteria:**
- `uv run pytest tests/ -n auto -q` passes all existing tests
- Test suite wall-clock time is under 2 minutes on a standard laptop
- `config.yaml` `test_command` updated to use pytest
- No existing test is broken or skipped

---

## Problem 2: Reduce Developer Prompt Token Overhead

### Current state
Every developer bead receives the same static context on every run:
- Guardrail template (`templates/agents/developer.md`)
- Core skill (`core/base-orchestrator/SKILL.md`) — 65 lines
- Role skill (`role/developer-implementation/SKILL.md`) — 53 lines
- Capability skill (`capability/code-edit/SKILL.md`) — 44 lines
- Task skills (3 files, ~50 lines each)
- `CLAUDE.md` — auto-loaded by Claude Code from the worktree root

This static content is identical for every developer bead. Telemetry shows developers average 5,209 output tokens and consume 37% of total pipeline cost.

### Fix: Two-part approach

**Part A — Trim skill redundancy**

Several skills repeat concepts already covered by `core/base-orchestrator` (bead lifecycle, output schema requirements, file scope rules). Audit each developer-facing skill for content that duplicates the base skill or CLAUDE.md and remove it. Target: reduce total static skill content by ~30% without losing actionable guidance.

Skills to audit and trim:
- `core/base-orchestrator/SKILL.md` — remove anything already in CLAUDE.md
- `role/developer-implementation/SKILL.md` — focus on what's unique to developers vs. the base
- `capability/code-edit/SKILL.md` — keep only editing-specific guidance, remove general coding advice

**Part B — Move verbose context to linked docs**

Skills should contain pointers to docs rather than duplicating their content inline. Instead of embedding multi-paragraph explanations of bead lifecycle or scheduler behaviour in skill files, reference the authoritative source:

```
# Bead lifecycle and scheduler rules: see CLAUDE.md §Key Concepts
```

This reduces tokens sent to the agent while keeping the information accessible if needed.

**Acceptance criteria:**
- Total character count of developer skill files reduced by ≥25% from current baseline
- No existing agent behaviour regresses (review agent must still approve)
- Trimmed content is either genuinely redundant or replaced with a pointer to the authoritative source
- CLAUDE.md is not lengthened to compensate

---

## Problem 3: Structured Developer Handoff Summaries

### Current state
The `HandoffSummary` model has:
- `summary: str` — free-form narrative
- `remaining: str` — narrative context only
- `touched_files` / `changed_files` — file lists

Review agents average 7.4 turns — the highest of any agent type — despite being fast (73s avg). High turn count with low wall-clock time indicates the reviewer is iterating to extract basic information about the change that should have been in the handoff. A reviewer asking "what did you change and why?" is wasted turns.

### Fix: Add structured handoff fields for developers

Extend `HandoffSummary` and `AgentRunResult` with three new optional fields, required for developer beads:

```python
design_decisions: str = ""   # Why this approach was chosen; key trade-offs
test_coverage_notes: str = "" # What the tests verify; what is NOT covered and why
known_limitations: str = ""  # Edge cases not handled; follow-up work deferred
```

Update:
1. `src/codex_orchestrator/models.py` — add fields to `HandoffSummary` and `AgentRunResult`
2. `src/codex_orchestrator/runner.py` — add fields to `AGENT_OUTPUT_SCHEMA` so they're parsed from agent output
3. `templates/agents/developer.md` — require the agent to populate all three fields
4. `src/codex_orchestrator/prompts.py` — include the new fields in the context passed to review/tester beads

The review agent prompt should surface these fields prominently so the reviewer can assess the change in fewer turns.

**Acceptance criteria:**
- `HandoffSummary` and `AgentRunResult` include the three new fields
- `AGENT_OUTPUT_SCHEMA` validates them (optional strings, default empty)
- Developer guardrail template requires all three to be populated
- Review prompt surfaces `design_decisions`, `test_coverage_notes`, and `known_limitations` from the dependency bead's handoff
- Existing tests pass; new tests cover schema parsing and prompt injection

---

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | Add `pytest`, `pytest-xdist` dev deps |
| `.orchestrator/config.yaml` | Update `test_command` to pytest |
| `src/codex_orchestrator/models.py` | Add 3 fields to `HandoffSummary` and `AgentRunResult` |
| `src/codex_orchestrator/runner.py` | Add fields to `AGENT_OUTPUT_SCHEMA` |
| `src/codex_orchestrator/prompts.py` | Surface new handoff fields in review/tester context |
| `templates/agents/developer.md` | Require `design_decisions`, `test_coverage_notes`, `known_limitations` |
| `.agents/skills/core/base-orchestrator/SKILL.md` | Trim redundant content |
| `.agents/skills/role/developer-implementation/SKILL.md` | Trim redundant content |
| `.agents/skills/capability/code-edit/SKILL.md` | Trim redundant content |
| `tests/test_orchestrator.py` | Update tests for new handoff fields |

---

## Acceptance Criteria (Overall)

- Full test suite runs in under 2 minutes with pytest -n auto
- Developer skill content reduced by ≥25% without behavioural regression
- New handoff fields are populated by developer agents and visible to reviewers
- All existing tests pass under pytest
- No new permanent blocks or corrective rates introduced

---

## Pending Decisions

### 1. Pytest migration risk
Some tests use `unittest`-specific patterns (e.g. `addCleanup`, `setUp`/`tearDown` ordering). pytest is generally compatible but edge cases exist. Should we do a dry run first with `pytest tests/ -n0` (no parallelism) before enabling `-n auto`? **Recommendation: yes, two-phase rollout.**

### 2. Skill trimming scope
The "reduce by 25%" target is an estimate. Should trimming be done by a developer bead (agent decides what to cut) or manually reviewed first? Given skill quality directly affects agent behaviour, **recommendation: agent trims + review bead must approve before merge.**

### 3. Handoff field enforcement
Should `design_decisions` etc. be required (non-empty validation) or optional? Making them required could block developer beads that legitimately have nothing to say (e.g. trivial config changes). **Recommendation: optional with strong guardrail encouragement, not schema-enforced.**
