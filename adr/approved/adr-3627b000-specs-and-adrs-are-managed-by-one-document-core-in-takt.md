---
id: ADR-3627b000
title: Specs and ADRs are managed by one document core in takt
status: approved
created_at: '2026-11-08T20:37:32.899322+00:00'
authors:
- Renalias, Oscar
description: Specs describe changes, specs/current records behaviour, ADRs record
  decisions; both document types are managed by takt's shared document core via takt
  spec and takt adr
accepted_at: '2026-11-08T20:41:00.893731+00:00'
superseded_at: null
superseded_by: null
supersedes:
- ADR-a188353c
tags:
- documents
- specs
- adr
- architecture
related_specs:
- spec-b1b9c11d
- spec-ea87bef7
- spec-92d4fb81
related_beads:
- B-36a6c19e
review_after: null
---

# Specs and ADRs are managed by one document core in takt

## Summary

> In the context of agents and operators needing durable written context for a self-hosting codebase, facing stale done specs, untestable acceptance criteria, re-litigated decisions, and two diverging implementations of the same document lifecycle (a standalone `spec.py` and `takt adr`), we decided to keep the three-way split — specs describe changes, `specs/current/<capability>.md` records current behaviour, ADRs record cross-cutting decisions — and to manage both specs and ADRs through one document core inside takt (`takt spec`, `takt adr`), to achieve one implementation of frontmatter, IDs, lifecycle and validation that later indexing and agent-context work can build on, accepting that spec management now requires takt and no longer works as a standalone script.

## Context

ADR-a188353c established the three-way split and chose a stdlib-only `spec.py`, shipped as the `skill-spec-management` skill (also an external APM package, `oscarrenalias/skill-spec-management`), so spec tooling would be portable to projects without takt. ADRs, added later, were built into takt (`src/agent_takt/adr.py`, `takt adr`).

That left the same concept implemented twice, plus a third time for the plan-time scenario coverage check:

* `spec.py` (1,560 lines, five identical copies in the repo and package) and `AdrStore` each had their own frontmatter parser and writer, ID resolution, status-folder moves, listing and validation. Fixes landed in one and not the other — e.g. unquoted ADR descriptions broke YAML (B-16889ef8) while `spec.py` used a hand-written YAML subset.
* `spec_requirements.py` re-parsed `## Requirements` for `takt plan` with slightly different rules from `spec.py validate`.
* takt itself could not resolve a spec ID to a file, which blocks letting agents look up specs and ADRs by ID, and a future search index needs one write path to hook into.

Spec spec-b1b9c11d (feature B-36a6c19e, takt 0.1.69) implemented a shared document core and moved spec management into takt.

## Decision Drivers

* One implementation per concept: frontmatter, IDs, resolution, lifecycle, validation, `## Requirements` parsing.
* Stable document IDs that takt can resolve, for upcoming agent-context and search work.
* No change to on-disk formats; existing specs and ADRs keep working unmodified.
* Preserve the behavioural guarantees of the three-way split (living current-behaviour records, scenario-to-test traceability, decisions via supersession).

## Considered Options

### Option A — One document core in takt; specs and ADRs are two configurations of it

* Good: Single implementation of parsing, resolution, lifecycle and validation; fixes apply to both types.
* Good: takt can resolve any spec or ADR by ID, and every write passes through one `on_document_changed` hook for future indexing.
* Good: Consistent UX (`takt spec` gains prefix resolution, `--field`, `--json`), and skills become plain documentation of CLI commands.
* Bad: Spec management requires takt; non-takt projects must use the external package separately.
* Bad: A larger refactor touching ADR code that already worked.

### Option B — Keep `spec.py` standalone and leave ADRs in takt (ADR-a188353c as-is)

* Good: Spec tooling stays portable to any repo.
* Bad: Two implementations keep diverging, and takt still cannot resolve specs by ID.

### Option C — Make the external `spec.py` package the shared implementation for both

* Good: One implementation, still portable.
* Bad: takt's core lifecycle would depend on an external script with no access to takt internals (git commits, services, the future index), and ADRs would leave takt.

### Option D — Store documents in the sqlite memory database as the source of truth

* Good: Indexing and search come for free.
* Bad: Not reviewable or mergeable in git, per-machine and gitignored, invisible to agents working on files in worktrees; git operations change files outside takt anyway, so the database would still have to follow the files.

## Decision

**Each kind of knowledge keeps exactly one home, and takt manages the documents through one core:**

1. **Specs are forward-looking change documents** in `specs/<status>/`, managed only through `takt spec` (`create`, `set status|feature-root|tags|priority|description`, `show`, `list`, `migrate`, `remove`, `validate`). Operators and agents do not use `mv` or hand-edit managed frontmatter. The optional `## Requirements` section of `### ADDED|MODIFIED|REMOVED: <capability> / <requirement>` deltas keeps its rules: ADDED/MODIFIED need a `SHALL`/`MUST` sentence and `#### Scenario:` blocks with `- **WHEN**`/`- **THEN**`; REMOVED needs a `Reason:` line.
2. **`specs/current/<capability>.md` is the living record of current behaviour.** `takt spec set status done` validates the deltas and merges them with a `<!-- source: spec-<id> -->` line per requirement; any invalid delta exits 1 and changes nothing. `current` is a reserved status and is never listed as a spec.
3. **Scenarios are carried through to tests.** `takt plan` refuses to persist (`--write`/`--from-file`) unless every ADDED/MODIFIED scenario is covered by a tester criterion `Scenario: <capability> / <requirement> / <scenario>` (`--allow-uncovered-scenarios` downgrades to a warning), and tester `met` claims must cite a `path::test_name`.
4. **ADRs are backward-looking records of cross-cutting decisions** in `adr/<status>/`, managed through `takt adr`; corrections happen by supersession, not edits.
5. **Both document types are configurations of one document core** (`src/agent_takt/documents/`): a `DocumentType` declares folders, statuses, transitions, resolution rules, frontmatter style, templates, validation hooks and commit behaviour (ADR transitions commit; spec commands do not), and a single `DocumentStore` implements them. There is exactly one frontmatter reader/writer and one `## Requirements` parser, shared by `takt spec validate` and the plan-time coverage check.
6. **takt does not ship or depend on a standalone `spec.py`.** The `skill-spec-management` and `skill-adr-management` skills document the CLI only. The external package is left as-is for non-takt projects.

## Consequences

### Positive

* Fixes and new behaviour (quoting, validation, resolution) apply to specs and ADRs at once.
* takt can resolve specs and ADRs by ID, enabling agent lookup by ID and a search index wired through `on_document_changed`.
* The living-specs guarantees of ADR-a188353c are unchanged: current-behaviour records, scenario coverage at plan time, tester citation checks.
* Spec frontmatter and folder stay in sync because only `takt spec` moves files; spec edits change only the edited field's lines.

### Negative

* Projects need `takt upgrade` (which disables the old `spec.py` copies) and must update hand-written CLAUDE.md/AGENTS.md instructions that call `spec.py`; upgrade lists them but does not edit them.
* Spec management is no longer usable without takt.
* Authors still judge whether something is a spec, a requirement delta or an ADR; `## Requirements` remains optional, so `specs/current/` fills in only as specs complete, and concurrent MODIFIED deltas remain last-writer-wins.
* Neither `specs/current/` nor approved ADRs are loaded into agent context automatically yet; that is follow-up work on top of this core.
