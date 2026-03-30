# UUID-Based Bead Identifiers

## Objective

Replace sequential bead IDs (`B0001`, `B0002`, ...) with UUID-based IDs to eliminate collisions when multiple developers, machines, or forks work on the same project in parallel.

## Why This Matters

The current bead ID allocation reads all existing files under `.orchestrator/beads/` and picks `max + 1`. This assumes a single writer. When two developers work in parallel:

- Both create the same bead ID independently
- Bead JSON files, worktrees, branches, and child IDs all collide on merge
- Feature root references and dependency chains break
- No safe way to merge bead state from different forks

UUID-based IDs remove the coordination requirement entirely. Each developer, machine, or agent can create beads independently without risk of collision.

## Scope

In scope:

- Replace sequential ID allocation with short UUID generation
- Update child bead ID derivation (suffixes like `-test`, `-review`)
- Update branch naming to use new ID format
- Migrate all code and tests that reference the old ID format
- Ensure existing beads with sequential IDs continue to load (backward compatibility)

Out of scope:

- Migrating existing bead files to new IDs (they keep their old IDs)
- Distributed locking or multi-writer state management (separate concern)
- Central bead registry or allocation service

## ID Format

**Format**: `B-<8 hex chars>` — the `B-` prefix followed by the first 8 characters of a UUID4 hex string.

**Examples**: `B-a7bc3f91`, `B-04e2d1f8`, `B-ff12ab09`

**Child IDs**: `B-a7bc3f91-test`, `B-a7bc3f91-docs`, `B-a7bc3f91-review`, `B-a7bc3f91-corrective`

**Branch names**: `feature/B-a7bc3f91` (same pattern as today, just with new ID format)

**Collision probability**: 8 hex chars = 32 bits = ~4.3 billion possible IDs. For a project with 10,000 beads, collision probability is approximately 1 in 430,000. Acceptable for any practical use.

**Why not full UUID**: Full UUIDs (36 chars) are unwieldy for typing, branch names, and conversation. 8 hex chars is a good balance of uniqueness and usability.

## Functional Requirements

### 1. ID Generation (`storage.py`)

Replace `allocate_bead_id()` which currently scans files and returns `B{max+1:04d}`:

```python
import uuid

def allocate_bead_id() -> str:
    return f"B-{uuid.uuid4().hex[:8]}"
```

No file scanning needed. No sequential numbering.

### 2. Child Bead ID Derivation (`storage.py`, `scheduler.py`)

`allocate_child_bead_id(parent_id, suffix)` currently produces `B0130-test`. With new format: `B-a7bc3f91-test`.

The logic stays the same — append `-{suffix}` to the parent ID. No change in derivation, just the parent ID format changes.

### 3. Branch Naming (`gitutils.py`)

Currently: `feature/b0130` (lowercase).
New: `feature/B-a7bc3f91` (preserving the `B-` prefix).

Verify `WorktreeManager.ensure_worktree()` and `merge_branch()` work with the new format. Git branch names allow hyphens so no issues expected.

### 4. File Naming

Bead files: `.orchestrator/beads/B-a7bc3f91.json`
Telemetry: `.orchestrator/telemetry/B-a7bc3f91/1.json`
Agent runs: `.orchestrator/agent-runs/B-a7bc3f91/`
Worktrees: `.orchestrator/worktrees/B-a7bc3f91/`

All use the bead ID as a directory/file name. Hyphens in filenames are safe across all platforms.

### 5. Backward Compatibility

Existing beads with sequential IDs (`B0001`, `B0130`, etc.) must continue to load and function. The storage layer should accept any string as a bead ID — it's just a filename stem. No migration needed.

New beads get UUID-based IDs. Old beads keep their sequential IDs. They can coexist, reference each other in dependencies, and share feature roots.

### 6. Display and Sorting

Sequential IDs sort naturally (`B0001` < `B0002`). UUID-based IDs sort lexicographically but not chronologically. If chronological ordering matters (e.g. in `bead list`), sort by creation timestamp from the bead's `execution_history` first entry, falling back to ID sort.

### 7. CLI Usability

Typing `B-a7bc3f91` is harder than `B0130`. Consider:

- Tab completion support (if feasible in the CLI framework)
- Accepting unique prefixes: `orchestrator bead show B-a7b` resolves to `B-a7bc3f91` if it's the only match
- `bead list --plain` shows full IDs for copy-paste

Prefix matching is the highest value improvement for usability. It should work anywhere a bead ID is accepted (show, update, retry, merge, handoff).

## Files to Modify

| File | Change |
|------|--------|
| `src/codex_orchestrator/storage.py` | `allocate_bead_id()` — UUID generation instead of sequential scan |
| `src/codex_orchestrator/storage.py` | `allocate_child_bead_id()` — verify works with new format |
| `src/codex_orchestrator/storage.py` | `load_bead()` — add prefix matching for partial IDs |
| `src/codex_orchestrator/gitutils.py` | Verify branch naming works with hyphens |
| `src/codex_orchestrator/cli.py` | Anywhere bead IDs are parsed from args — use prefix resolution |
| `src/codex_orchestrator/tui.py` | Display and sorting adjustments |
| `tests/test_orchestrator.py` | Update tests that hardcode sequential IDs or assert on ID format |

## Acceptance Criteria

- New beads get UUID-based IDs in `B-xxxxxxxx` format
- Existing sequential beads continue to load and function
- Child beads use parent UUID + suffix (`B-a7bc3f91-test`)
- Branch names use new format (`feature/B-a7bc3f91`)
- Partial ID prefix resolves to full ID when unambiguous
- Ambiguous prefix produces a clear error listing matches
- `bead list` sorts by creation time, not ID
- All existing tests pass (with ID format updates where needed)
- Two developers creating beads simultaneously produce different IDs
