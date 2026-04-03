# Continuous Worker Pool Scheduler

## Objective

Replace the current batch-and-wait scheduler cycle with a continuous worker pool that feeds new beads as worker slots free up, eliminating idle time between bead completions within a cycle.

## Why This Matters

The current scheduler calls `run_once()` which selects up to N beads, executes them all, waits for all to finish, then returns. If one bead takes 5 minutes and two others take 1 minute, two workers sit idle for 4 minutes. With many beads queued, this idle time compounds significantly.

A continuous pool would start the next eligible bead as soon as any worker finishes, keeping all workers busy and reducing total wall-clock time for large feature roots.

## Current Behavior

1. `run_once()` gets all `ready` beads
2. Checks file-scope conflicts against `in_progress` beads
3. Selects up to `max_workers` non-conflicting beads
4. Executes all selected beads (via `ThreadPoolExecutor`)
5. Waits for ALL to complete
6. Returns — outer loop calls `run_once()` again

Beads that become ready mid-cycle (e.g. followup children created during finalization) are not picked up until the next cycle.

## Proposed Behavior

1. Maintain a pool of N worker slots
2. On startup and whenever a slot frees up:
   a. Re-read ready beads from storage (not a stale in-memory list)
   b. Check file-scope conflicts against currently active beads
   c. Select the next eligible bead and start it in the free slot
3. When a bead completes, finalize it (commit, state update, followup creation) BEFORE freeing the slot
4. Continue until no ready beads remain and all slots are idle

## Concurrency Safety Requirements

### 1. Serialized bead selection

Conflict detection and lease acquisition must be atomic — protected by a lock. If two workers finish simultaneously, the scheduler must not evaluate two candidates against the same stale snapshot of active beads. The sequence must be:

```
acquire selection lock
  → read active beads from storage
  → find next non-conflicting ready bead
  → acquire lease on it
  → update bead to in_progress
release selection lock
```

### 2. Finalization before slot release

When a bead completes, finalization must happen BEFORE the worker slot is freed for reuse. Finalization includes:

- Committing changes to the worktree
- Updating bead status (done/blocked)
- Creating followup beads (test/docs/review children)
- Writing telemetry

This ensures the next bead selected for the same feature root sees committed changes, and newly created followup beads are visible to the next selection pass.

### 3. Per-feature-root worktree serialization

Two beads in the same feature root share a worktree. Even if they have non-overlapping file scopes, concurrent writes to the same worktree can cause git conflicts. The scheduler should not run two beads from the same feature root simultaneously, OR acquire a per-feature-root lock around worktree operations.

The current `WorktreeManager` already has per-feature-root locks for worktree creation — this may need to extend to cover the full execution lifecycle.

### 4. Storage consistency

`RepositoryStorage` reads/writes bead JSON files. With concurrent workers finalizing and the selection logic reading, there's a risk of reading partially-written files. Options:
- Atomic writes (write to temp file, rename) — already used in some paths
- A storage-level read lock during selection
- Accept eventual consistency since JSON writes are small and fast

## Scope

In scope:

- New continuous pool execution mode in `scheduler.py`
- Lock-protected bead selection with fresh storage reads
- Finalization-before-slot-release guarantee
- Per-feature-root worktree safety

Out of scope:

- Distributed/multi-machine scheduling
- Priority-based bead selection (currently FIFO by bead ID)
- Dynamic worker count adjustment

## Files to Modify

| File | Change |
|------|--------|
| `src/codex_orchestrator/scheduler.py` | New `run_continuous()` method or refactored `run_once()` with pool semantics |
| `src/codex_orchestrator/cli.py` | Wire new mode into `command_run()` |

## Risks

- Increased complexity in the scheduler's core loop
- Harder to debug concurrent bead execution issues
- Potential for subtle race conditions if locks are not comprehensive
- Test coverage for concurrent scenarios is inherently difficult

## Migration

The batch mode (`run_once()`) should remain available as a fallback. The continuous pool could be opt-in via a flag (e.g. `--continuous`) or become the default when `--max-workers > 1`.
