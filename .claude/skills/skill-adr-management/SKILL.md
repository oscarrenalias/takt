---
name: adr-management
description: Manage Architecture Decision Records (ADRs) with `takt adr` — creating, listing, showing, approving, rejecting, superseding and validating ADRs. Use when a non-feature architectural decision (technology choice, project-wide constraint, cross-feature principle) needs to be recorded, or when asked to look up or change an existing decision. Do not use for feature work; that belongs in a spec.
tools: Read, Write, Edit, Glob, Grep, Bash
license: MIT
---

# adr-management

`takt adr` is the canonical tool for ADR lifecycle operations. Never use `mv` or edit frontmatter by hand; use the CLI so file location and status stay in sync.

## ADR or spec?

- **Spec** — forward-looking: what to build. Use for feature work.
- **ADR** — backward-looking: what was decided and why. Use for technology choices, architectural principles spanning features, and project-wide constraints that outlive a single feature.

If a paragraph you are about to add to CLAUDE.md starts "we decided to…", it probably belongs in an ADR.

## Lifecycle

| State | Folder | Meaning |
|---|---|---|
| `draft` | `adr/drafts/` | Being authored; not binding |
| `approved` | `adr/approved/` | Accepted; binding on future work |
| `superseded` | `adr/superseded/` | Replaced by a newer ADR |
| `rejected` | `adr/rejected/` | Proposed, not adopted |

Allowed: `draft → approved`, `draft → rejected`, `approved → superseded`. Forbidden: `approved → draft` (supersede instead), anything from `rejected` (file a new ADR), `draft → superseded`, re-superseding.

## Commands

```bash
takt adr new "Title" [--description "..."] [--tag X] [--related-spec ID] [--related-bead ID] [--supersedes ADR-xxx]
takt adr list [--status approved] [--tag X] [--plain] [--json]
takt adr show ADR-a3f19c2b [--field status] [--field decision]
takt adr approve a3f1 [--supersedes ADR-other]
takt adr reject a3f1
takt adr supersede ADR-old --by ADR-new
takt adr validate
```

IDs are `ADR-<8 hex>`; any unambiguous prefix works. `takt adr new` does not open an editor — it writes the file under `adr/drafts/`; open it and fill in the body.

## Body sections

Fill in all mandatory sections before approving (approval fails on missing or placeholder text):

- `## Summary` — one sentence: "In the context of X, facing Y, we decided Z, to achieve Q, accepting W."
- `## Context` — situation, constraints, prior art.
- `## Considered Options` — at least one `### Option X — name` subsection with `Good:`/`Bad:` bullets. Keep the "Bad" bullets specific so rejected options are not re-proposed.
- `## Decision` — the chosen option, stated directly; binding constraints stated as such.
- `## Consequences` — at least one of `### Positive` / `### Negative`.
- `## Decision Drivers` — optional; one-line bullets of the forces involved.

## Workflow

1. `takt adr new "Title" ...`
2. Edit the draft body.
3. `takt adr approve <id>` (add `--supersedes <old>` to replace an approved ADR atomically).
4. `takt adr validate` and commit.

Approved ADRs are binding: before proposing a change that conflicts with one, supersede it with a new ADR rather than ignoring it. See `docs/adr.md` for the full guide.
