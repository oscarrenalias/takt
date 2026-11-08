---
id: ADR-d2b1f2b4
title: The framework stays language- and stack-agnostic
status: draft
created_at: 2026-11-08T15:47:07.954331+00:00
authors: ['Renalias, Oscar']
description: Framework guardrails and skills contain no hardcoded toolchain commands; the test command comes from config or agent detection stored in memory.
tags: [foundational, guardrails, onboarding]
related_specs: [spec-42b24901, spec-302156c2, spec-230d82df]
related_beads: 
supersedes: 
---
# The framework stays language- and stack-agnostic

## Summary

> In the context of a framework that drives AI workers on arbitrary target projects, facing guardrails and skills that kept picking up Python/uv-specific commands from takt's own codebase, we decided that framework files carry no hardcoded toolchain commands — the test command comes from `common.test_command` or is detected by agents and recorded in shared memory, and `takt init` only seeds config from a stack menu — to achieve correct behaviour on any language without editing core files, accepting that agents sometimes spend a step detecting the toolchain and that stack-specific advice must live in per-project config or memory.

## Context

takt is written in Python and is self-hosting, so the files that shape agent behaviour (guardrail templates in `templates/agents/`, the skill catalog in `templates/skills/`) were originally authored against takt's own toolchain. Several specs found the same problem: those files named Python commands directly, and a tester or merge-conflict agent on a TypeScript, Go or Rust project received instructions that were wrong for its repo.

* `stack-agnostic-guardrails` (spec-42b24901) found `uv run python -m unittest tests.<module_name> -v` in `capability/test-execution/SKILL.md`, "Do not run `unittest discover`" in `role/tester-validation/SKILL.md`, and `uv run python -c "import codex_orchestrator"` / `uv run python -m py_compile` in `templates/agents/merge-conflict.md`.
* `pipeline-efficiency-test-parallelisation-and-skill-trimming` (spec-230d82df) found `templates/agents/tester.md` hardcoding the unittest invocation, and stated the rule that the template defines the *what* (persona, constraints, output contract) while `capability/test-execution` defines the *how*. It explicitly said no commands, placeholders or runner names belong in the tester template, "not even `{{TEST_COMMAND}}`".
* `improve-takt-init-onboarding-with-stack-selection-menu` (spec-302156c2) found `takt init` asking for free-text language and always offering `pytest` / `python -m py_compile` as defaults, whatever the user typed.

These templates are copied into every new project by `takt init` and refreshed by `takt upgrade`, so a Python assumption in them becomes a defect in every non-Python project.

## Considered Options

### Option A — No toolchain commands in framework files; config, agent detection and shared memory supply them

* Good: One set of guardrails and skills works for any stack; no per-language forks of the catalog.
* Good: The merge gate reads `config.common.test_command` (`cli/commands/merge.py`), so the authoritative command is a per-project config value, not framework code.
* Good: Agents follow a memory-first lookup in `capability/test-execution/SKILL.md` (`$TAKT_CMD memory search "test command" --namespace global`), fall back to detecting from `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`/`build.gradle` or `common.test_command`, and then `memory add` the result, so detection happens once per project.
* Good: Needs no new skills, env var injection or runner changes (spec-42b24901 scope excludes `runner.py` and `AGENT_SKILL_ALLOWLIST`).
* Bad: The first agent on a fresh project may spend a step detecting the command, and can guess wrong if the project is unusual.
* Bad: Correctness depends on agents actually honouring the memory-first instruction.

### Option B — Inject the configured test command into prompts or env vars at run time

* Good: Deterministic; agents never guess.
* Bad: Needs runner and prompt plumbing that spec-42b24901 deliberately avoided.
* Bad: Covers only the test command. Build checks, targeted-run syntax and discovery rules would still need stack-specific text somewhere.

### Option C — Ship per-language guardrail and skill variants

* Good: Rich, stack-tuned advice out of the box.
* Bad: Multiplies the catalog by the number of stacks and makes `takt upgrade` diffs harder to manage.
* Bad: Any stack not on the list gets nothing, and the variants drift from each other.

### Option D — Keep Python defaults and let operators edit core files

* Good: Zero work for takt's own (Python) project.
* Bad: Every non-Python operator must patch framework files, and `takt upgrade` then reports them as locally modified.

## Decision

**Framework guardrails and skills must not hardcode language- or toolchain-specific commands.** Templates under `templates/agents/` state behaviour and constraints and defer command mechanics to `capability/test-execution`. That skill describes how to find the command (shared memory first, then project-file detection or `common.test_command`) rather than naming one, and agents record what they find in global shared memory for later agents.

The project's test command is per-project configuration: `common.test_command` in `.takt/config.yaml` (`CommonConfig.test_command`, default `None` in `config.py`). `takt merge` uses it for the test gate and skips the gate with a warning when it is unset.

`takt init` may offer stack-specific defaults, but only to seed configuration. The `STACKS` catalog in `onboarding/prompts.py` (Python, Node.js, TypeScript, Go, Rust, Java (Maven), Other) pre-fills the test and build-check prompts. The answers are written to `common.test_command` and substituted into `{{LANGUAGE}}`, `{{TEST_COMMAND}}` and `{{BUILD_CHECK_COMMAND}}` placeholders in the installed templates (`substitute_template_placeholders` in `onboarding/config.py`). "Other" falls back to free text, so no stack is excluded. Illustrative multi-stack examples (as in `templates/agents/defect.md`) are acceptable. A single-stack instruction is not.

## Consequences

### Positive

* takt works on non-Python projects without editing core files, and `takt upgrade` can refresh templates cleanly.
* The test command has one authoritative home per project (`common.test_command`) plus a shared-memory cache that agents maintain.
* Adding a stack to onboarding is a one-line `STACKS` entry. It never touches guardrails or skills.
* Reviewers have a clear rule for rejecting changes that reintroduce stack-specific commands into framework files.

### Negative

* Agents on a fresh project pay a detection step, and a wrong memory entry can mislead later agents until someone corrects it.
* Stack-specific know-how (targeted-run syntax, build checks) is thinner than tuned per-language guidance would be.
* The rule needs ongoing vigilance. takt's own Python toolchain is the obvious default for authors, and the packaged `_data/default_config.yaml` still ships a Python `test_command` that `takt init` overwrites from the user's answer.
