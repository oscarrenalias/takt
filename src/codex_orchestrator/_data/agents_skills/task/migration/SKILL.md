---
name: migration
description: Handle scoped migrations and compatibility updates.
---

# migration

Use this skill when the assigned developer bead changes a schema, interface, config shape, or stored data contract.

## Procedure

- Identify the current state, target state, and the exact boundary being migrated.
- Keep the migration scoped to the bead's files and acceptance criteria.
- Prefer incremental compatibility-preserving changes unless the bead explicitly authorizes a breaking cutover.
- Update loaders, serializers, adapters, or call sites together so the transition is internally consistent.
- Keep one clear migration path; avoid introducing parallel abstractions unless compatibility requires them.
- Document any temporary compatibility behavior in local comments or nearby docs when future cleanup will depend on it.
- If the migration requires operator coordination, bulk data backfill, or cross-team rollout steps outside developer scope, block and hand off with the dependency called out.

## Output expectations

- Summarize the source state, target state, and compatibility posture after the change.
- List any deferred cleanup or removal work that should become a follow-up bead.
- Limit verification to the allowed compile or import check; do not run tests from this skill.
