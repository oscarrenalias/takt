---
id: ADR-0de1fbd1
title: Hash-derived 8-hex IDs for all takt identifiers
status: approved
created_at: '2026-07-01T07:40:28.245494+00:00'
authors:
- Renalias, Oscar
description: All takt-managed identifiers (beads, ADRs) use hash-derived 8-hex suffixes
  with a type prefix; sequential numbering is deliberately avoided.
accepted_at: '2026-07-01T09:54:39.305961+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- foundational
- ids
related_specs: []
related_beads: []
review_after: null
---

# Hash-derived 8-hex IDs for all takt identifiers

## Summary

In the context of coordinating work across parallel Git worktrees and feature branches, facing the risk of ID collisions when operators author entities concurrently, we decided to use hash-derived 8-hex IDs (`B-a3f19c2b`, `ADR-d160fd37`) for all takt-managed identifiers, to achieve collision-free allocation without a central registry, accepting that verbal references are less mnemonic than sequential numbers.

## Context

Takt operates on Git worktrees where multiple feature branches can be authored in parallel, potentially by different operators or CI runners. Any ID allocation strategy that reads shared state (a counter file, a `max(existing) + 1` scan) can produce collisions when two branches simultaneously create entities.

Sequential numeric IDs (`ADR-001`, `ADR-002`) are more mnemonic in verbal reference ("see ADR-7"), but they require coordination — either a registry that all writers consult, or merge-time conflict resolution when two branches picked the same number. Neither is acceptable given takt's design constraint that operators should be able to fan out work across feature branches without stepping on each other.

Beads already used hash-derived IDs from very early in the project's history. The question when introducing ADRs was whether to keep that consistency or diverge to sequential for prose-reference ergonomics.

## Considered Options

### Option A — Hash-derived 8-hex IDs everywhere

* Good: No coordination required — every write picks a fresh UUID4 prefix, collision probability is negligible.
* Good: Consistent with existing bead IDs; operators already read and reference `B-a3f19c2b`-shaped IDs in commits, handoffs, and skill docs.
* Good: Prefix-based CLI resolution (`takt adr show a3f1`) gives back most of the sequential ergonomics without the coordination cost.
* Bad: Verbal reference in a meeting ("we should look at ADR-a3f19c2b again") is less natural than "ADR-7".
* Bad: Hex prefixes are less memorable than small integers.

### Option B — Sequential numeric IDs with merge-time collision resolution

* Good: Mnemonic in prose and speech.
* Good: Traditional ADR practice; matches many existing corpora in the wider industry.
* Bad: Two parallel branches can both allocate `ADR-007`, producing an ambiguous merge that has to be manually resolved.
* Bad: Diverges from the bead ID convention — operators learn two ID schemes for what are conceptually similar artefacts.
* Bad: Rebasing / cherry-picking across branches can renumber ADRs, breaking references in commit messages.

### Option C — Sequential IDs with a lockfile / registry

* Good: Ergonomic in prose like Option B.
* Good: Coordination via lockfile prevents collisions.
* Bad: Introduces shared mutable state that every writer must contend on — the exact problem takt has designed around.
* Bad: Doesn't work for offline / disconnected operators.
* Bad: Adds a new file that has to be committed with every bead/ADR — reintroduces the `[bead]` commit noise problem that motivated the `commit_bead_state: false` default.

## Decision

**All takt-managed identifiers use the form `<TYPE>-<8 hex chars>`** where `<8 hex chars>` are the first 8 characters of a fresh UUID4. Current prefixes: `B-` for beads, `ADR-` for Architecture Decision Records. Future takt entities that need a unique identifier will follow the same pattern.

The CLI must accept prefix resolution across the ID space (e.g. `takt adr show a3f1` resolves to `ADR-a3f19c2b` if unambiguous; errors on zero or multiple matches). Operators reference IDs in prose using the shortest unambiguous prefix; documentation and commit messages should include the full 8-hex ID for durability.

## Consequences

### Positive

* Parallel branches allocate IDs independently with negligible collision probability.
* Consistent scheme across all takt entities — one mental model.
* Prefix-based CLI resolution recovers most of the ergonomic benefits of short IDs.
* No lockfile, no counter file, no `max()+1` scan — allocation is O(1) and offline-safe.

### Negative

* Verbal reference in meetings is less natural than short integers; operators must adapt to either using prefixes ("ADR-a3f1") or the descriptive title.
* Grep-friendly listings must include the title alongside the ID to be scannable — the ID alone is not memorable.
* Migration cost if a project ever wants to import an external ADR corpus that uses sequential numbering (would need to be rewritten to hash-derived IDs on ingest).
