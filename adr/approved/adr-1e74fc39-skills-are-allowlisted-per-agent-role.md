---
id: ADR-1e74fc39
title: Skills are allowlisted per agent role
status: approved
created_at: '2026-11-08T15:47:08.143476+00:00'
authors:
- Renalias, Oscar
description: Each agent type receives a fixed, role-scoped skill bundle from AGENT_SKILL_ALLOWLIST,
  copied into an isolated execution root; templates/skills/ is the catalog.
accepted_at: '2026-11-08T15:52:52.328354+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- skills
- agents
- core
related_specs:
- spec-26849373
- spec-f423c232
- spec-230d82df
related_beads: []
review_after: null
---

# Skills are allowlisted per agent role

## Summary

> In the context of workers that get procedural guidance from a skill catalog, facing token overhead, role confusion and an operator workspace cluttered with subagent-only skills, we decided that each agent type receives a fixed, role-scoped skill bundle from `AGENT_SKILL_ALLOWLIST`, sourced from `templates/skills/` (with `.agents/skills/` reserved for operator-facing exceptions) and copied into an isolated per-bead execution root, to achieve focused, reproducible and auditable agent context, accepting that changing a role's skill set is a code change rather than a config edit.

## Context

Guardrail templates (`templates/agents/<type>.md`) say *what* an agent must and must not do. Skills say *how* to do it (`improve-agent-skills`, spec-26849373). The catalog is organised as `core/`, `role/`, `capability/` and `task/` skills, plus the shared `memory` skill. Giving every agent the whole catalog would inflate every prompt and blur roles: a tester does not need refactoring or release-notes guidance, and a reviewer should not get code-editing mechanics.

Three specs shaped where skills live and who gets them:

* `improve-agent-skills` (spec-26849373) rewrote the catalog into actionable, role-specific guidance with `core/base-orchestrator` assigned to all agent types.
* `pipeline-efficiency-test-parallelisation-and-skill-trimming` (spec-230d82df) found that static skill content was the largest per-bead token cost for developer beads, and trimmed duplication on the principle that skills point to authoritative sources rather than restating them.
* `move-subagent-codex-skills-to-templates-skills-with-operator-exceptions` (spec-f423c232) found that keeping the full subagent catalog in repo-root `.agents/skills/` made bootstrap assets look like operator skills and let the operator's own agent discover them. It moved the subagent catalog to `templates/skills/` and kept only operator-facing skills (`memory`, `spec-management`) in `.agents/skills/`.

## Considered Options

### Option A — Fixed per-role allowlist in code, copied into an isolated execution root

* Good: Each agent sees only the skills relevant to its role, which keeps prompts small and roles distinct.
* Good: `prepare_isolated_execution_root()` in `skills.py` copies exactly the allowlisted directories into `.takt/agent-runs/<bead_id>/<skills_dir>/skills/` (`.agents` for Codex, `.claude` for Claude Code), so what the agent can discover is fully determined.
* Good: Auditable. `loaded_skills` and a `skill_bundle_hash` over the copied `SKILL.md` files are recorded, and a `skills_loaded` execution-history event is appended (`scheduler/execution.py`).
* Good: A missing allowlisted skill fails loudly (`FileNotFoundError: Missing required skill directory`) instead of silently degrading.
* Bad: Changing a role's bundle means editing `AGENT_SKILL_ALLOWLIST` and shipping a release.

### Option B — Give every agent the full catalog and let it choose

* Good: Nothing to maintain. New skills are available to everyone at once.
* Bad: Every bead pays tokens for every skill, the cost spec-230d82df set out to cut.
* Bad: Agents may follow guidance meant for another role (for example a tester applying refactor advice).

### Option C — Externalise the allowlist to `.takt/config.yaml`

* Good: Operators could tune bundles per project without a release.
* Bad: The allowlist is tightly coupled to the catalog layout and to the guardrail templates. Externalising it would also mean externalising the catalog, which is a separate concern (the rationale is recorded in the comment above `AGENT_SKILL_ALLOWLIST`).
* Bad: A misconfigured bundle could silently strip `core/base-orchestrator` or `memory` from a role.

### Option D — Keep the subagent catalog in repo-root `.agents/skills/`

* Good: The source and the Codex runtime destination share one path.
* Bad: The operator's own agent discovers subagent bootstrap skills from the repo root, which conflates two audiences (spec-f423c232).

## Decision

**Skills are assigned per agent type by the fixed `AGENT_SKILL_ALLOWLIST` in `src/agent_takt/skills.py`, and a worker can see only its allowlisted skills.**

* Every worker type receives `core/base-orchestrator` plus one `role/` skill, and `memory` in all worker bundles except `recovery`. Most types also get `capability/` and `task/` skills.
* Exceptions: `planner` gets `task/spec-intake` and `task/dependency-graphing` but no `capability/` skill. `scheduler` gets only `core/base-orchestrator` and `role/scheduler-policy`, with no `memory`. `recovery` gets only `core/base-orchestrator`.
* `templates/skills/` is the primary catalog. Repo-root `.agents/skills/` holds operator-facing exceptions only, such as `memory` and `spec-management`, and must not hold the full subagent catalog. `_skill_path()` resolves `templates/skills/` first, then `.agents/skills/`, then the packaged copies.
* Per bead, the skill directory in the execution root is rebuilt from scratch and only the allowlisted skills are copied in. The workspace is reached through a `repo` symlink.
* The allowlist is intentionally not configurable. Adding a skill to a role is a reviewed code change.

## Consequences

### Positive

* Prompt context stays focused and smaller per bead, and roles stay distinct.
* Agent context can be reproduced and audited (`loaded_skills`, `skill_bundle_hash`, `skills_loaded` event).
* Operators' own agents no longer pick up subagent bootstrap skills from the repo root.
* Codex and Claude Code get the same bundle and differ only in the destination directory.

### Negative

* Operators cannot change a role's bundle per project without changing takt itself. They can only change a skill's *content*, by editing the project's copy under `templates/skills/`. A copy in `.agents/skills/` is used only when `templates/skills/` has no directory for that skill ID.
* Skill sources resolve from several locations (repo `templates/skills/`, repo `.agents/skills/`, packaged templates, packaged agents skills), which can surprise someone debugging why a particular copy was used.
* New capabilities need a code change to reach any agent, and a missing skill directory fails the bead.
