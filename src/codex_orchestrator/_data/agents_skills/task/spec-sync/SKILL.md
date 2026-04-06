---
name: spec-sync
description: Capture documentation follow-up guidance for synchronizing specification docs with implemented behavior.
---

# spec-sync

Use this skill when a documentation bead needs to update specification or design docs after implementation changed the source-of-truth behavior.

## Goal

Keep specs aligned with shipped behavior so future planners, developers, reviewers, and documentation agents can rely on the written contract.

## What to Capture

1. Identify the implemented behavior that changed the documented contract.
2. Update acceptance language, examples, and constraints that no longer match reality.
3. Preserve still-correct intent from the original spec instead of rewriting unrelated sections.
4. Call out any intentional divergence between the original plan and the shipped result.
5. Record unresolved follow-up work separately rather than folding unfinished behavior into the spec as if it already exists.

## Writing Rules

- Treat the spec as a maintained contract, not a changelog or retrospective.
- Prefer precise behavioral statements over implementation-detail narration.
- Keep terminology consistent with the rest of the repository unless the implementation established a new canonical term.
- If implementation and spec still disagree in a way you cannot resolve from repository evidence, flag the gap instead of guessing.
- Do not make runtime changes or broaden the bead into planning, testing, or feature implementation work.

## Quality Bar

- Another agent should be able to read the updated spec and plan follow-up work without re-deriving current behavior from code.
- The updated text should clearly separate shipped behavior from future or deferred work.
- Spec synchronization should reduce ambiguity, not introduce new design decisions without evidence.
