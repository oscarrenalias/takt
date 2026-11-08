---
id: ADR-b7c7679f
title: Agent backends run as CLI subprocesses and beads are backend-agnostic
status: draft
created_at: 2026-11-08T15:46:59.062012+00:00
authors: ['Renalias, Oscar']
description: Codex and Claude Code are invoked as CLI subprocesses behind AgentRunner; any bead can run or be retried on either backend.
tags: [runners, agents, architecture]
related_specs: [spec-186ab872]
related_beads: 
supersedes: 
---
# Agent backends run as CLI subprocesses and beads are backend-agnostic

## Summary

> In the context of driving AI coding agents from the takt scheduler, facing more than one capable agent product (OpenAI Codex, Anthropic Claude Code), each with its own tool loop, skills discovery and permission model, we decided to invoke each backend's own CLI (`codex exec`, `claude -p`) as a subprocess behind a common `AgentRunner` interface, and to keep beads free of backend state, to achieve interchangeable backends that reuse each vendor's agent harness, accepting coupling to vendor CLI flags and output formats and uneven telemetry across backends.

## Context

Takt workers need a full agentic loop: file edits, shell commands, tests and skills, all inside a feature worktree, ending in structured JSON that matches `AGENT_OUTPUT_SCHEMA` / `PLANNER_OUTPUT_SCHEMA`. Codex and Claude Code both ship a non-interactive CLI mode that provides this loop, sandbox and permission handling, and native discovery of skills (`.agents/skills/` for Codex, `.claude/skills/` plus `CLAUDE.md` for Claude Code).

Spec-186ab872 (Externalize Configuration Phase 2) moved the hardcoded binary paths, flags and allowed-tools lists out of `runner.py` and the CLI and into `OrchestratorConfig` / `BackendConfig` (`config.py`), loaded from the `codex:` and `claude:` blocks of `.takt/config.yaml`. Since then, backend differences are configuration rather than code branches.

## Decision Drivers

* Reuse the vendors' agent harnesses rather than build a tool-execution loop.
* Switch backends per run without migrating work.
* Keep per-backend differences in config, not in scheduler logic.
* Enforce structured output and per-agent tool allowlists.

## Considered Options

### Option A — Vendor CLIs as subprocesses behind `AgentRunner`

* Good: Each vendor's tool loop, sandboxing and skills discovery come for free. `CodexAgentRunner` runs `codex exec` with `--full-auto` (from `BackendConfig.flags`) and `--output-schema`. `ClaudeCodeAgentRunner` runs `claude -p --dangerously-skip-permissions --allowedTools ... --output-format json --json-schema ...`.
* Good: Process isolation. Each bead gets its own execution root (`.takt/agent-runs/<bead_id>/`) with an isolated `home/`, and a subprocess timeout (`timeout_seconds`) bounds runaway agents.
* Good: Binaries, flags, models (`model_by_agent`) and tool allowlists are config keys.
* Bad: Coupled to CLI flag names and output envelopes that vendors can change.
* Bad: Output sometimes comes back as prose. This needs `_extract_json_from_text` fallbacks and a Claude-only `_retry_structured_output`.

### Option B — Call model APIs or agent SDKs directly

* Good: Typed responses, finer control and no stdout parsing.
* Bad: Takt would have to implement and maintain its own tool loop, file and shell tools, permission model and skills loading for each vendor.
* Bad: Vendor-specific code would spread through the runner instead of staying in a thin adapter.

### Option C — Support a single backend only

* Good: Simplest. One code path, one telemetry shape.
* Bad: Ties the whole pipeline to one vendor's availability, pricing and capability.

## Decision

**Agent backends are external CLIs invoked as subprocesses through `AgentRunner` implementations in `runner.py` (`CodexAgentRunner`, `ClaudeCodeAgentRunner`), registered by name in `cli/services.py`.** Binding rules:

* The runner is resolved per invocation in this order: `--runner` flag, then `AGENT_TAKT_RUNNER`, then legacy `ORCHESTRATOR_RUNNER`, then `common.default_runner` (default `codex`).
* Both backends share prompt construction (`prompts.py`), output schemas and the bead lifecycle. Backend-specific behaviour lives in `BackendConfig` (`binary`, `flags`, `skills_dir`, `timeout_seconds`, models, allowed tools) and in `prepare_isolated_execution_root()` (`skills.py`). That function copies the same allowlisted skills into `<skills_dir>/skills/` and, for Claude Code only, writes the guardrail as `CLAUDE.md`.
* Beads hold no backend-specific state. Any bead can be run or retried on either backend (e.g. `takt --runner claude retry <id>`). Runner telemetry is stored on the bead as opaque data.

## Consequences

### Positive

* Adding or switching a backend means writing a runner adapter and a config block. Scheduler, storage and bead models do not change.
* Operators can mix backends: plan with one, implement with another, or retry a blocked bead on the other vendor.
* Vendor improvements to the agent harness reach takt without code changes.

### Negative

* Vendor CLI changes (flags, JSON envelope, permission switches) can break runs and must be tracked through config or runner updates.
* Telemetry is uneven. Codex reports only measured metrics (`source: "measured"`), while Claude Code adds cost and tokens (`source: "provider"`), so cost reporting is incomplete for Codex beads.
* Structured output is best-effort and needs parse fallbacks plus a recovery-bead path for no-structured-output failures.
* Both CLIs run with permission prompts disabled (`--full-auto`, `--dangerously-skip-permissions`), so safety depends on worktree isolation, allowlists and guardrails, not on the vendor's interactive approval.
