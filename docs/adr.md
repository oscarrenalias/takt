# Architecture Decision Records (ADRs)

## What ADRs are for

ADRs capture architectural decisions that aren't tied to a single feature or spec. They answer "we chose X over Y, and here's why" in a durable, searchable form.

- **Specs** are forward-looking — they describe *what to build*.
- **ADRs** are backward-looking — they record *what was decided* and the reasoning.

Use ADRs for:
- Technology or library choices
- Architectural principles that span features ("developer beads must not run tests")
- Project-wide constraints that future agents and operators should respect
- Decisions that have no single owning spec, or that outlive the feature that prompted them

Use specs for feature work. When a settled decision should survive across many features and sessions, it belongs in an ADR — not in CLAUDE.md, not in a bead handoff, not in tribal memory.

## Lifecycle States

ADRs live under the `adr/` directory, one file per decision, organised by status:

| State | Folder | Meaning |
|---|---|---|
| `draft` | `adr/drafts/` | Being authored; not yet binding |
| `approved` | `adr/approved/` | Accepted; binding on all future work |
| `superseded` | `adr/superseded/` | Replaced by a newer ADR; kept for historical context |
| `rejected` | `adr/rejected/` | Proposed but explicitly not adopted |

**Allowed transitions:**

- `draft → approved` — the decision is final and should be respected going forward
- `draft → rejected` — the proposal was reviewed and not adopted; the ADR is kept for historical context
- `approved → superseded` — a newer ADR replaces this one; the `superseded_by` pointer is set and the superseding ADR's `supersedes` list is updated

**Forbidden transitions:**

- `approved → draft` — corrections happen via supersession, not rollback
- `rejected → anything` — file a new ADR instead
- `draft → superseded` — supersession only applies to approved decisions
- `superseded → superseded` — an ADR that is already superseded cannot be re-superseded

Each transition is a git commit, so the full lifecycle history is recoverable from `git log adr/<file>`.

## CLI Walkthrough

### Create a new ADR

```bash
takt adr new "Defect beads bundle fix and regression test inline"
# ✓ Created adr/drafts/adr-a3f19c2b-defect-beads-bundle-fix-...md (ADR-a3f19c2b)
```

Optional flags:
```bash
takt adr new "Title" \
  --description "One-line summary for listings" \
  --tag storage --tag agents \
  --related-spec spec-92d4fb81 \
  --related-bead B-0e6dbf26 \
  --supersedes ADR-9b2cf01a   # declare intent to replace (existence validated; status not required)
```

`takt adr new` does **not** open an editor. It writes the file and exits — open it yourself and fill in the body sections.

### Edit the ADR body

Open the created file in your editor. Fill in all five mandatory sections before approving:

- `## Summary` — one structured sentence: "In the context of X, facing Y, we decided Z, to achieve Q, accepting W"
- `## Context` — the situation, constraints, and prior art motivating this decision
- `## Considered Options` — at least one `### Option X — name` subsection with bullet pros/cons for each option
- `## Decision` — the chosen option, stated directly and unambiguously
- `## Consequences` — at least one of `### Positive` or `### Negative` subsections

The `## Decision Drivers` section is optional but strongly recommended for non-trivial decisions.

### List ADRs

```bash
takt adr list                          # all ADRs, all statuses
takt adr list --status approved        # approved only
takt adr list --status draft --plain   # table output
takt adr list --tag storage --plain    # filter by tag (AND across multiple --tag flags)
takt adr list --json                   # raw JSON
```

### Inspect an ADR

```bash
takt adr show ADR-a3f19c2b             # full JSON metadata
takt adr show a3f1                     # prefix resolution (same as takt bead show)
takt adr show a3f1 --field status      # frontmatter field (bare value)
takt adr show a3f1 --field decision    # body section (reserved key; prints the ## Decision text)
takt adr show a3f1 --field authors[0]  # list element from frontmatter
```

Reserved `--field` keys for body sections: `summary`, `context`, `decision_drivers`, `considered_options`, `decision`, `consequences`, `consequences.positive`, `consequences.negative`. Any path not found exits non-zero with `field not found: <path>` on stderr — identical to `takt bead show --field`.

### Approve a draft

```bash
takt adr approve a3f1
# ✓ Transitioned ADR-a3f19c2b to approved
#   Moved adr/drafts/adr-a3f19c2b-...md → adr/approved/
#   Set accepted_at: 2026-06-30T17:14:09Z
```

Approval fails if any mandatory body section is missing or contains only placeholder text. Fix the ADR body and retry.

To approve a new ADR and simultaneously supersede an existing approved one:

```bash
takt adr approve ADR-7e3b1428 --supersedes ADR-a3f19c2b
# ✓ Transitioned ADR-7e3b1428 to approved
# ✓ Transitioned ADR-a3f19c2b to superseded (replaced by ADR-7e3b1428)
#   Moved adr/approved/adr-a3f19c2b-...md → adr/superseded/
#   Set ADR-a3f19c2b superseded_at: 2026-12-15T...
#   Set ADR-a3f19c2b superseded_by: ADR-7e3b1428
```

`--supersedes` is repeatable. The operation is all-or-nothing: if any target is not currently `approved`, the entire command aborts and no files are modified.

### Supersede an approved ADR (standalone)

When the replacement ADR was already approved separately:

```bash
takt adr supersede ADR-a3f19c2b --by ADR-7e3b1428
```

Both the old and new ADR must exist; the new one must be currently `approved`.

### Reject a draft

```bash
takt adr reject a3f1
# ✓ Transitioned ADR-a3f19c2b to rejected
#   Moved adr/drafts/adr-a3f19c2b-...md → adr/rejected/
```

Rejection only applies to `draft` ADRs. The file is kept for historical context.

### Validate all ADRs

```bash
takt adr validate
# ✓ All ADRs valid.
```

On failure, one line per error:

```
ADR-a3f19c2b: missing required section: ## Decision
ADR-9b2cf01a: dangling superseded_by reference: 'ADR-deadbeef'
```

`validate` checks: required frontmatter fields, ID format, valid status values, mandatory body sections on approved ADRs (including the "at least one real option subsection" rule for `## Considered Options`), and referential integrity for `supersedes` / `superseded_by` chains. Exits non-zero on any failure.

## End-to-End Example

```bash
# 1. Author a decision
$ takt adr new "Defect beads bundle fix+test inline" \
    --description "Defect beads write their own regression test — no separate tester followup"
✓ Created adr/drafts/adr-a3f19c2b-defect-beads-bundle-fix-test-inline.md (ADR-a3f19c2b)

# 2. Open the file in your editor and fill in all five body sections.

# 3. Check what you have
$ takt adr list --status draft --plain
ADR-a3f19c2b  draft  Defect beads bundle fix+test inline  2026-06-30

# 4. Approve
$ takt adr approve a3f1
✓ Transitioned ADR-a3f19c2b to approved
  Moved adr/drafts/adr-a3f19c2b-...md → adr/approved/
  Set accepted_at: 2026-06-30T17:14:09Z

# 5. Months later: author a superseding decision
$ takt adr new "Defect beads spawn no followups; reviewer is operator" \
    --supersedes ADR-a3f19c2b
✓ Created adr/drafts/adr-7e3b1428-...md (ADR-7e3b1428)
  Declares supersession of: ADR-a3f19c2b (validated to exist, status=approved)

# 6. Edit the new ADR body.

# 7. Approve with simultaneous supersession
$ takt adr approve ADR-7e3b1428
✓ Transitioned ADR-7e3b1428 to approved
✓ Transitioned ADR-a3f19c2b to superseded (replaced by ADR-7e3b1428)
  Moved adr/approved/adr-a3f19c2b-...md → adr/superseded/
  Set ADR-a3f19c2b superseded_at: 2026-12-15T...
  Set ADR-a3f19c2b superseded_by: ADR-7e3b1428

# 8. Verify integrity
$ takt adr validate
✓ All ADRs valid.
```

## Body Section Conventions

Each ADR file follows a fixed structure enforced by `takt adr validate` and `takt adr approve`. The five mandatory sections and what to put in each:

**`## Summary`** — a single sentence in the Alexandrian pattern:

> In the context of `<use case>`, facing `<concern>`, we decided `<option>`, to achieve `<quality>`, accepting `<downside>`.

This is the executive line. Operators and (eventually) agents read it first. Keep it tight enough to fit in a list row.

**`## Context`** — the situation, pressure, and prior art motivating the decision. Name the constraints, stakeholders, and any failed prior attempts. Future readers use this to determine whether the decision still applies to their situation.

**`## Considered Options`** — at least one `### Option X — name` subsection. Each option gets bullet-point arguments for (`Good:`) and against (`Bad:`). This section exists specifically to prevent future agents from re-proposing rejected alternatives — keep the "Bad" bullets specific enough to argue against.

**`## Decision`** — the chosen option, stated directly. If the decision implies a binding constraint ("X is forbidden", "Y is always required"), state it as such. Agents will treat this section as a hard constraint.

**`## Consequences`** — at least one of `### Positive` or `### Negative`, each as a bullet list. Acknowledge both what improves and what you're accepting as the cost of the choice.

**`## Decision Drivers`** (optional) — one-line bullets naming the forces that shaped the decision: constraints, quality attributes, non-negotiables. Useful for non-trivial decisions; omit cleanly when the Context section alone is sufficient.

## IDs and Prefixes

ADR IDs are `ADR-<8 hex chars>` derived from a fresh UUID4 at creation time. No registry, no counter — no collision risk when multiple operators author ADRs on parallel branches.

All CLI commands accept a prefix instead of the full ID:

```bash
takt adr show a3f1       # resolves to ADR-a3f19c2b if unambiguous
takt adr approve a3f1    # same prefix resolution
```

An ambiguous prefix exits non-zero and lists the matches.

## Relationship to CLAUDE.md

CLAUDE.md captures project-wide conventions, workflow rules, and operational guidance — content that changes as the project evolves. ADRs capture discrete architectural decisions with an audit trail of when they were adopted and superseded.

When you find yourself adding a paragraph to CLAUDE.md that starts "we decided to..." or "the reason we use X is...", consider whether it belongs as an approved ADR instead. Active ADRs (status `approved`) are the canonical source of truth for settled decisions; CLAUDE.md should reference the ADR rather than duplicate its reasoning.

## Out-of-Scope: Pipeline Integration

The current ADR implementation is the **substrate** only. Planner and worker agents do not yet automatically load approved ADRs into their context, and the reviewer guardrail does not yet enforce ADR compliance. Those integrations are explicitly deferred to a follow-up spec. For now, ADRs are a human-facing governance tool; any agent awareness of their contents requires the operator to include the ADR text in a bead's description or linked docs manually.
