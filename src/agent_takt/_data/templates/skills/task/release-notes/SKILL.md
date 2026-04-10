---
name: release-notes
description: Capture documentation follow-up guidance for release notes after completed behavior changes.
---

# release-notes

Use this skill when a documentation bead needs to record user-visible changes for operators, users, or downstream maintainers after implementation has landed.

## Goal

Produce release-note updates that explain what changed, who is affected, and any required operator action without re-describing internal implementation details.

## What to Capture

1. Summarize the user-visible behavior change in plain language.
2. Name the affected workflow, command, surface area, or audience.
3. Call out required migration, configuration, rollout, or upgrade steps when they exist.
4. Note important limitations, caveats, or follow-up expectations that a reader needs for safe adoption.
5. Reference validation evidence or acceptance outcomes when that context helps readers trust the change.

## Writing Rules

- Focus on externally relevant behavior, not internal refactors unless they change operator expectations.
- Keep the wording stable and factual; avoid speculative roadmap language.
- Group related changes together when they ship as one documented capability.
- If no user-visible behavior changed, say so plainly instead of inventing release-note content.
- Do not change runtime behavior, implementation scope, or test scope while doing this documentation follow-up.

## Quality Bar

- A reader should understand whether they need to take action after the release.
- The notes should help future documentation agents distinguish shipped behavior from planned work.
- The final text should remain concise, scannable, and grounded in completed behavior.
