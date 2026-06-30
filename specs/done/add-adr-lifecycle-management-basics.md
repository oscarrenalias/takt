---
name: Add ADR lifecycle management — basics
id: spec-92d4fb81
description: "Introduce Architecture Decision Records (ADRs) as a first-class artefact in takt — metadata schema, lifecycle states (draft/approved/superseded/rejected), on-disk storage layout, and CLI surface. Pipeline integration (planner/worker/reviewer ADR awareness) is deliberately out of scope; this spec establishes the substrate that integration will build on."
dependencies: null
priority: medium
complexity: medium
status: done
tags:
- adr
- lifecycle
- cli
- governance
scope:
  in: null
  out: null
feature_root_id: null
---
# Add ADR lifecycle management — basics

## Objective

Takt currently captures design decisions in three scattered places: spec files (feature-level), CLAUDE.md (project-wide conventions), and the `design_decisions` field on individual bead handoffs. None of these are well-suited to **standalone decisions that aren't tied to a specific feature** — e.g. "we chose markdown over YAML for bead handoffs because parsing fragility outweighed schema rigour," "developer beads must not run tests; tester beads do," or "commit_bead_state defaults to false for new projects." These decisions get re-litigated by agents in future sessions because there's no canonical place for them.

Introduce **Architecture Decision Records (ADRs)** as a first-class takt artefact, parallel to specs in shape and lifecycle but distinct in purpose:

- **Specs** are forward-looking — they describe *what to build*.
- **ADRs** are backward-looking — they record *what was decided* and the reasoning.

This spec covers only the substrate: file format, frontmatter schema, lifecycle states, on-disk layout, and CLI for managing the lifecycle. A follow-up spec will wire ADRs into the planner/worker/reviewer prompts and the memory store so agents actually respect them — that integration is the eventual goal but is explicitly out of scope here.

## Problems to Fix

1. **No canonical place for non-feature decisions.** Today, a settled architectural choice has to either go into CLAUDE.md (which is becoming a kitchen sink) or be tribally remembered. Both options degrade as the project grows and agents re-litigate.
2. **No lifecycle for decisions.** When a decision is overturned, there's no record of the supersession, the reasoning for the change, or the chain of evolution. CLAUDE.md just gets edited and the previous decision is lost to git history.
3. **No CLI affordance.** Operators can't easily list active decisions, propose a new one, or accept a draft. The mental cost of "let me add an ADR" is high enough that it doesn't happen.
4. **No structured pointer to related work.** A decision often emerges from a specific spec or a specific bead. There's no machine-readable way to express "this ADR was authored in response to spec-9173b2da."

## Changes

### 1. On-disk layout

Mirror the spec convention — status-folder pattern under a top-level `adr/` directory:

```
adr/
  drafts/        # being authored, not yet binding
  approved/      # accepted, binding on future work
  superseded/    # replaced by a newer ADR; readable for history
  rejected/      # proposed but explicitly not adopted
```

Each ADR is a single Markdown file (`adr-<id>-<slug>.md`) with YAML frontmatter and a fixed body structure. Status transitions move the file between folders, matching how `spec.py set status` already works for specs. The on-disk parallelism with specs is intentional — operators don't have to learn a second mental model.

### 2. ID scheme

Use **hash-derived** IDs in the form `ADR-<8 hex chars>` — for example `ADR-a3f19c2b`. This is the same scheme `takt bead create` uses and avoids the merge-collision problem sequential IDs introduce when operators author ADRs on parallel branches.

ID allocation: on `takt adr new`, generate a fresh UUID4, take the first 8 hex chars, prefix with `ADR-`. No registry, no counter, no `max(existing) + 1` scan.

Prefix resolution on the CLI (`takt adr show a3f1`) mirrors `takt bead`'s behaviour: resolve the shortest unambiguous prefix, error if zero or multiple matches. Operators reference ADRs verbally as `ADR-a3f1` the same way they already say "B-a3f1" in commits and bead handoffs — internally consistent with the rest of takt's ID scheme.

### 3. Frontmatter schema

Required fields:

| Field | Type | Description |
|---|---|---|
| `id` | str | `ADR-<8 hex chars>` — hash-derived, immutable once assigned |
| `title` | str | Short human-readable title |
| `status` | enum | `draft` \| `approved` \| `superseded` \| `rejected` |
| `created_at` | ISO-8601 timestamp | Set on `takt adr new`; immutable |
| `authors` | list[str] | Pulled from `git config user.name` at creation; editable |

The "last touched" time is intentionally **not** in the frontmatter — git already tracks it (`git log -1 --format=%cI -- adr/...`), and duplicating it forces every body edit to either go through a takt command or risk frontmatter staleness. Defer to git for mtime; surface it in `takt adr show` output if operators ask for it later.

Optional fields:

| Field | Type | Description |
|---|---|---|
| `description` | str | One-line summary for indexing and grep-friendly listing |
| `accepted_at` | ISO-8601 timestamp | Set when status transitions to `approved`; null otherwise |
| `superseded_at` | ISO-8601 timestamp | Set when status transitions to `superseded`; null otherwise |
| `superseded_by` | str | ADR ID that replaces this one; only set when status is `superseded` |
| `supersedes` | list[str] | ADR IDs this one replaces; set at draft-time, validated at `approve` |
| `tags` | list[str] | Free-form classifiers (e.g. `storage`, `agents`, `cli`) — same shape as bead labels |
| `related_specs` | list[str] | Spec IDs that motivated this ADR |
| `related_beads` | list[str] | Bead IDs that motivated or implemented this ADR |
| `review_after` | ISO-8601 date | Optional revisit-by date (decisions with a known shelf life) |

### 4. Template and body structure

`takt adr new` reads a template file, substitutes placeholder values for the frontmatter, and writes the result into `adr/drafts/`. The template lives in two places (operators can override per-project by editing the source location):

- `templates/adr/template.md` — operator-editable; this is the source-of-truth for the takt repo
- `src/agent_takt/_data/templates/adr/template.md` — packaged copy installed by `takt init` into new projects; kept byte-identical to the source via the existing parity test

The template's full contents (hybrid drawing from the Alexandrian Pattern, Michael Nygard's template, and MADR — see Pending Decisions for the rationale):

```markdown
---
id: {{id}}
title: {{title}}
status: draft
created_at: {{created_at}}
authors: {{authors}}
description: {{description}}
tags: {{tags}}
related_specs: {{related_specs}}
related_beads: {{related_beads}}
supersedes: {{supersedes}}
---
# {{title}}

## Summary

> In the context of <use case>, facing <concern>, we decided for <option>, to achieve <quality>, accepting <downside>.

One sentence in the structured form above. This is the executive line — operators and agents read it to grasp the decision without parsing the whole document.

## Context

What is the issue, situation, or pressure motivating this decision? Be concrete — name the constraints, the stakeholders, the prior art, and any failed prior attempts. Future agents will read this section to understand whether the decision still applies to their situation.

## Decision Drivers

*Optional but strongly recommended.* Specific forces pushing this decision — constraints, quality attributes, or non-negotiable requirements. Each driver is a one-line bullet.

* (driver 1)
* (driver 2)

## Considered Options

The options that were on the table. Each option gets its own subsection with bullet lists of arguments for and against. This is the section that prevents future agents from re-proposing rejected alternatives — keep the "Bad" bullets specific enough to actually argue against, not just hand-waved.

### Option A — (name)

* Good: …
* Good: …
* Bad: …

### Option B — (name)

* Good: …
* Bad: …
* Bad: …

## Decision

The chosen option, stated directly and unambiguously. If the decision has a binding implication ("X is forbidden", "Y is the default", "all Z must be Q"), state it as such. This is the section that future agents will treat as a constraint — keep it crisp.

## Consequences

### Positive

* (what becomes easier, safer, or otherwise improved)
* …

### Negative

* (what we accept as the cost of this decision)
* …
```

Placeholder substitution at `takt adr new` time uses simple `{{name}}` replacement — no templating engine. Unset optional fields render as empty YAML values (e.g. `tags:` with nothing after) so operators can fill them in via their editor.

**Mandatory body sections** for approved ADRs (enforced by `takt adr validate` and the `takt adr approve` transition; attempting to approve an ADR missing any of these fails with a clear error):

- `## Summary`
- `## Context`
- `## Considered Options` (with at least one option subsection)
- `## Decision`
- `## Consequences` (with at least one of `### Positive` or `### Negative`)

**Optional sections** (present in the template, not enforced):

- `## Decision Drivers` — strongly recommended for non-trivial decisions; some decisions have no explicit pressure beyond the Context and can omit this cleanly.

The Considered Options structure (per-option pros/cons) is deliberately heavier than a single Alternatives Considered paragraph. The cost is operator authoring time; the value is that future agents can see concretely *why* each rejected option was rejected, rather than glossing past a paragraph that dismisses them all at once. Since ADRs will eventually be inlined into worker prompts at execution time, the extra structure also makes them easier for agents to extract specific information from.

### 5. Lifecycle transitions

Allowed:

- `draft → approved` — operator decides the ADR is final
- `draft → rejected` — operator decides not to adopt; ADR is kept for historical context
- `approved → superseded` — operator authors a new ADR that explicitly replaces this one; the new ADR's `supersedes` list must include the old ID, and the transition sets `superseded_by` on the old ADR

Forbidden:

- `approved → draft` — once approved, an ADR is immutable in terms of decision content; corrections happen via supersession
- `rejected → approved` or `superseded → approved` — would mute the historical record; instead, author a new ADR
- `draft → superseded` — supersession only applies to previously-approved decisions
- `superseded → superseded` — an already-superseded ADR cannot be re-superseded. If the chain has evolved (X superseded by Y, then Y itself superseded by Z), the live decision operators and agents follow is Z; X's `superseded_by` continues to point at Y, and the chain is recoverable by walking `superseded_by` forward. The CLI rejects an attempt to supersede an ADR whose status is not `approved`.

Status changes are append-only in the audit trail: `created_at`, `accepted_at`, and `superseded_at` are immutable once set. Each transition is its own git commit (driven by the CLI), so the lifecycle history is recoverable from `git log adr/<file>` if anyone needs it.

### 6. CLI surface

All commands are subcommands of `takt adr`, mirroring the `takt bead` namespace:

```bash
# Create a new ADR (status=draft); writes the file from the template with frontmatter populated.
# Does NOT open an editor — operators open it themselves with whatever editor they use.
# --supersedes is repeatable; the new ADR can declare multiple ADRs it intends to replace.
takt adr new "Title of the decision" [--description "..."] [--tag X --tag Y] \
                                     [--related-spec spec-xxx] [--related-bead B-xxx] \
                                     [--supersedes ADR-a3f19c2b [--supersedes ADR-9b2cf01a ...]]

# List ADRs, with the same filter surface beads have
takt adr list [--status approved] [--tag X] [--plain] [--json]

# Show a single ADR
takt adr show ADR-a3f19c2b                     # full content + metadata
takt adr show a3f1                              # prefix resolution (same as takt bead show)
takt adr show ADR-a3f19c2b --field decision    # body section (reserved name; see below)
takt adr show ADR-a3f19c2b --field status      # frontmatter field (dotted-path like takt bead show)

# Transition status (each transition is its own git commit so lifecycle history is preserved)
takt adr approve ADR-a3f19c2b [--supersedes ADR-9b2cf01a [--supersedes ADR-7e3b1428 ...]]  # draft → approved; can declare supersession (repeatable)
takt adr reject ADR-a3f19c2b                                 # draft → rejected
takt adr supersede ADR-9b2cf01a --by ADR-a3f19c2b            # approved → superseded; requires the replacement to exist and be approved

# Validation
takt adr validate                                            # walk all ADRs; verify supersedes/superseded_by integrity, required-body-section presence for approved, no orphaned references
```

No `takt adr edit` command — operators edit ADR markdown files with their normal editor. Frontmatter is managed by the transition commands (`approve`, `reject`, `supersede`) which write the appropriate timestamps and pointers; body content is plain markdown that operators are trusted to maintain. `takt adr validate` is the catch-all for "did I break something while editing."

**ID resolution**: accepts prefixes (`takt adr show a3f1` resolves to `ADR-a3f19c2b` if unambiguous) — same affordance as `takt bead show <prefix>`.

**`--field` resolution semantics**: `--field <name>` first tries the value as a frontmatter dotted path (`status`, `authors[0]`, `related_specs[1]`); if that fails, it tries `<name>` against a fixed set of reserved body section keys: `summary`, `context`, `decision_drivers`, `considered_options`, `decision`, `consequences`, `consequences.positive`, `consequences.negative`. The body keys correspond to the `## Summary`, `## Context`, etc. headings; matching is case-insensitive on the key but the underlying heading lookup is case-sensitive (`## Summary`, exact `## ` prefix). Any `--field` value that matches neither exits non-zero with `field not found: <path>` on stderr, identical to `takt bead show --field`.

**Body-section heading match for validation**: `takt adr validate` and `takt adr approve` look for headings using the regex `^## (Summary|Context|Decision Drivers|Considered Options|Decision|Consequences)\s*$` (case-sensitive, exact `## ` prefix). A section is considered **present** if its heading matches and the section body contains at least one non-whitespace character that is not part of the template's placeholder text (the `> In the context of …` blockquote, `* (driver 1)` bullets, `### Option A — (name)` headings with no following content, etc. are all treated as empty). The set of placeholder phrases is hard-coded in the validator alongside the template.

**Atomicity of `approve --supersedes`**: the operation must be all-or-nothing across both ADRs. Implementation should stage all changes — frontmatter writes for the new ADR and each superseded target, plus file moves — and validate every transition before committing any change to disk. Any failure (e.g. one of the `--supersedes` targets is not currently `approved`) aborts the entire operation, restoring all files to their pre-call state, and exits non-zero. When `common.commit_bead_state` semantics permit, all changes from one `approve --supersedes` invocation are recorded as a single git commit so `git log` shows the lifecycle event as one unit.

### 7. Implementation surface

| File | Change |
|---|---|
| `src/agent_takt/cli/commands/adr.py` | NEW — full command handler for `takt adr {new,list,show,approve,reject,supersede,validate}` |
| `src/agent_takt/cli/parser.py` | Register the `adr` subparser |
| `src/agent_takt/cli/__init__.py` | Dispatch to `command_adr` |
| `src/agent_takt/cli/formatting.py` | `format_adr_list_plain`, `format_adr_field` helpers (mirror existing bead formatters) |
| `src/agent_takt/adr.py` | NEW — `AdrStore` class (load/list/transition/move files), `Adr` dataclass with the frontmatter schema, ID allocation, supersession validation, body-section linting |
| `src/agent_takt/cli/services.py` | Wire `AdrStore` into `make_services()` so commands can access it |
| `templates/adr/template.md` | NEW — body template that `takt adr new` writes |
| `tests/test_adr.py` | NEW — store unit tests: ID allocation, status transitions, supersedes integrity, body lint, frontmatter round-tripping |
| `tests/test_cli_adr.py` | NEW — CLI tests: `new`, `list` filters, `show --field`, `approve --supersedes`, `supersede --by`, `validate`, `reject` |
| `CLAUDE.md` | New top-level "ADRs" subsection: when to use, how the lifecycle works, CLI invocation table, link to the docs page |
| `docs/adr.md` | NEW — operator guide: what ADRs are for, the four states, when to author/approve/supersede/reject, body conventions |
| `README.md` | One sentence in the Key Concepts area pointing at ADRs as the home for non-feature decisions |
| `src/agent_takt/_data/templates/adr/template.md` | Mirror of `templates/adr/template.md` for `takt init` to install into new projects (parity test will catch drift) |
| `src/agent_takt/onboarding/scaffold.py` | Create `adr/` directory tree (drafts/, approved/, superseded/, rejected/) during `takt init` |
| `src/agent_takt/onboarding/upgrade.py` | Create `adr/` directory tree on `takt upgrade` if missing (same behaviour as init) |

### 8. Examples — what the CLI looks like end to end

```bash
# Author a new ADR
$ takt adr new "Defect beads bundle fix+test inline"
✓ Created adr/drafts/adr-a3f19c2b-defect-beads-bundle-fix-test-inline.md (ADR-a3f19c2b)

# Operator edits the file in their editor, fleshing out Summary, Context, Considered Options, Decision, and Consequences.

# List drafts
$ takt adr list --status draft --plain
ADR-a3f19c2b  draft  Defect beads bundle fix+test inline   2026-06-30
ADR-9b2cf01a  draft  Use git mtime instead of last_updated_at  2026-06-30

# Approve
$ takt adr approve a3f1
✓ Transitioned ADR-a3f19c2b to approved
  Moved adr/drafts/adr-a3f19c2b-...md → adr/approved/
  Set accepted_at: 2026-06-30T17:14:09Z

# Months later, author a superseding decision
$ takt adr new "Defect beads spawn no followups; reviewer is operator" --supersedes ADR-a3f19c2b
✓ Created adr/drafts/adr-7e3b1428-...md (ADR-7e3b1428)
  Declares supersession of: ADR-a3f19c2b (validated to exist, status=approved)

$ takt adr approve ADR-7e3b1428
✓ Transitioned ADR-7e3b1428 to approved
✓ Transitioned ADR-a3f19c2b to superseded (replaced by ADR-7e3b1428)
  Moved adr/approved/adr-a3f19c2b-...md → adr/superseded/
  Set ADR-a3f19c2b superseded_at: 2026-12-15T...
  Set ADR-a3f19c2b superseded_by: ADR-7e3b1428
```

## Files to Modify

(See §7 above for the full table.)

## Acceptance Criteria

- `takt adr new "Title"` creates a file at `adr/drafts/adr-<8hex>-slug.md` with a valid frontmatter block (all required fields populated) and the standard body template (Summary, Context, Decision Drivers *(optional)*, Considered Options, Decision, Consequences with Positive/Negative subsections). The assigned ID is `ADR-<first 8 hex chars of a fresh UUID4>` — no registry, no scan, no collision risk on parallel branches.
- `takt adr list` defaults to all statuses; `--status approved` (repeatable for OR), `--tag` (repeatable for AND), and `--plain` / `--json` flags work the same way they do for `takt bead list`.
- `takt adr show ADR-<id> --field decision` prints only the `## Decision` section's body. `--field status` prints `approved` (lowercase, bare). Missing field exits non-zero with `field not found: <path>` on stderr — same UX as `takt bead show --field`.
- `takt adr show <prefix>` resolves to the matching ADR if the prefix is unambiguous; errors with the list of matches otherwise. Mirrors `takt bead show <prefix>`.
- `takt adr approve ADR-<id>` transitions a draft to approved, sets `accepted_at`, and moves the file from `adr/drafts/` to `adr/approved/`. Rejects with a clear error if status is not currently `draft`, or if any of the five mandatory body sections (Summary, Context, Considered Options with at least one option subsection, Decision, Consequences with at least one of Positive/Negative) are missing or empty.
- `takt adr approve ADR-<id> --supersedes ADR-<other>` additionally transitions the superseded ADR to `superseded`, sets its `superseded_at` and `superseded_by`, and moves its file. Fails atomically if the superseded target is not currently `approved` or does not exist.
- `takt adr supersede ADR-<old> --by ADR-<new>` is the standalone-transition equivalent (when the new ADR was already approved by a separate command). Same validation. Rejects if `ADR-<old>` is not currently `approved` (i.e. attempts to supersede a `draft`, `rejected`, or already-`superseded` ADR exit non-zero with a clear error).
- `takt adr reject ADR-<id>` transitions a draft to rejected. Rejects if status is not `draft`.
- **Atomicity:** `takt adr approve --supersedes` is all-or-nothing across all involved files. If any superseded target is invalid (missing, not `approved`, or already `superseded`), the entire command aborts with all files in their pre-call state and exits non-zero — the new ADR is **not** moved to `adr/approved/`, no `accepted_at` is written, and none of the target ADRs are mutated.
- **Multi-supersession:** `--supersedes` is repeatable on both `takt adr new` and `takt adr approve`. When N ADRs are declared, all N must be currently `approved` at approve time; all N transition to `superseded` atomically and each one's `superseded_by` is set to the new ADR's ID. The new ADR's frontmatter `supersedes` list contains all N IDs in declaration order.
- **Declaration vs transition:** at `takt adr new --supersedes ADR-X` time, ADR-X must exist (file present in `adr/**/`) but its current status is **not** required to be `approved` — the flag declares intent only. Actual transition of ADR-X happens at `takt adr approve` time, which re-validates that ADR-X is currently `approved` and aborts the whole approval if not.
- The takt repo's `templates/adr/template.md` and `src/agent_takt/_data/templates/adr/template.md` are byte-identical, verified by extending the existing template-parity test (the one shipped under B-af5e9477) so any drift fails CI.
- `takt upgrade` on an existing project creates the `adr/{drafts,approved,superseded,rejected}/.gitkeep` tree if missing, matching the behaviour of `takt init` on a fresh project.
- `takt adr validate` walks all ADRs and reports: missing required frontmatter fields, dangling `superseded_by` or `supersedes` references, body-section structure violations on `approved` ADRs (must have Summary, Context, Considered Options with at least one option subsection, Decision, and Consequences headings), and ID-format violations. Exits non-zero on any failure.
- `takt init` creates the `adr/` directory tree (with `.gitkeep` files in each subfolder) so the structure is in place for the first `takt adr new`.
- All existing tests pass: `uv run pytest tests/ -n auto -q`.

## Pending Decisions

These are intentionally left for follow-up discussion or for the implementer to resolve during the work — they don't block planning, but each will need a concrete answer:

All items below have been resolved. They are preserved here as an audit trail of decisions made during spec authoring.

- ~~**Should the ID format be `ADR-NNN` or `ADR-NNNN`?**~~ **Resolved:** moved to hash-derived IDs (`ADR-<8 hex>`) to match the rest of takt and eliminate parallel-branch collisions. Sequential is gone.
- ~~**Should `takt adr edit` validate the frontmatter on save?**~~ **Resolved:** dropped `takt adr edit` entirely. Operators edit ADR markdown files with their normal editor; `takt adr validate` is the catch-all integrity check. `last_updated_at` is dropped from the frontmatter — defer to `git log` for that.
- ~~**Should `takt adr new --supersedes` also auto-approve the new ADR atomically?**~~ **Resolved:** no. `new` always creates `draft`; `approve` is a deliberate separate step. Preserves the audit trail and keeps the lifecycle linear. A `--auto-approve` convenience flag is not planned; if operators ask for it later, file a separate spec.
- ~~**Should ADR titles be allowed to contain colons, slashes, or other characters that complicate the slug?**~~ **Resolved:** sanitise to ASCII-safe slug, lowercase, hyphenated, on `takt adr new`; preserve the original title verbatim in the frontmatter `title` field. Operators can rename the file manually for cosmetic reasons if they want a different slug.
- ~~**`review_after` enforcement.**~~ **Resolved:** the field is stored as frontmatter, no scheduler logic acts on it in this spec. A future spec can add `takt adr review-due` listing ADRs past their review date.
- ~~**MCP / rac-core schema compatibility.**~~ **Resolved:** explicitly **not** a goal. Takt's ADR schema is our own; we do not constrain field names or structure to align with rac-core or Google's Open Knowledge Format. If we ever want MCP interop, that will be a separate spec with explicit translation, not implicit schema coupling.
- ~~**Frontmatter `description` vs body `## Summary` — duplication risk.**~~ **Resolved:** keep both. `description` is a one-line frontmatter field for grep-friendly indexing, used by `takt adr list --plain` and similar tooling. `## Summary` is the Alexandrian-pattern structured one-sentence at the top of the body, for human and agent reading. The expectation is that `description` is an even shorter version of `## Summary` — typically the title plus a half-sentence of context, suitable for a list row.

## Explicitly out of scope (will be a separate spec)

- **Planner ADR awareness** — planner prompt loads accepted ADRs when decomposing a spec; emitted beads gain an `adr_ids` field.
- **Worker bead prompt injection** — when a bead with `adr_ids` runs, those ADRs are inlined into the worker's prompt as a "Binding Decisions" section.
- **Reviewer enforcement** — reviewer guardrail template gains a mandatory ADR-compliance check.
- **Memory store ingestion** — on transition to `approved`, the ADR is ingested into the project's sqlite-vec memory store so any agent can semantic-search for relevant decisions.
- **MCP server** — exposing ADRs to external clients via Model Context Protocol.
- **`takt adr review-due`** — listing ADRs past their `review_after` date.
- **Cross-project ADRs via `takt-fleet`** — fleet-wide decision propagation.

These are all real and worth doing, but adding them to this spec would explode the scope. The substrate this spec establishes is exactly what the integration spec(s) will hook into.
