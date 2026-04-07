---
name: Upgrade shared memory to sqlite-vec semantic search
id: spec-d8555ac0
description: Replace the flat append-only markdown memory files with a sqlite-vec backed semantic search system, while keeping markdown files as the git-committed source of truth.
dependencies:
priority: medium
complexity: medium
status: draft
tags: [memory, search, sqlite, embeddings]
scope:
  in: Shared in-repo worker memory (docs/memory/); takt memory CLI subcommand; memory skill update; fastembed or equivalent embedding engine
  out: Operator personal memory (~/.claude/projects/.../memory/); agent output schema changes; planner/scheduler changes
feature_root_id:
---
# Upgrade shared memory to sqlite-vec semantic search

## Objective

The current shared memory system stores institutional knowledge in two append-only markdown files (`docs/memory/known-issues.md`, `docs/memory/conventions.md`). Agents read both files in full at bead start. As these files grow, this becomes expensive, noisy, and increasingly unreliable — agents receive all memories regardless of relevance.

This spec replaces the full-file read with semantic retrieval: a sqlite-vec vector index over the same content, backed by local embeddings. The markdown files remain the source of truth (committed to git, shared across users and machines); the SQLite DB is a derived index, git-ignored and rebuildable. A new `takt memory` CLI subcommand provides the write and search interface. The memory skill is updated to use the CLI instead of direct file appends.

## Background and Design Decisions

### Why sqlite-vec over alternatives

Several options were evaluated:

- **mempalace** — good CLI, reasonable retrieval quality (validated via prototype), but depends on chromadb which pulls in onnxruntime + a 79MB model download. The project is also relatively new. Useful as a prototype reference.
- **txtai** — auditable SQLite storage but requires torch (~500MB), actually heavier than chromadb.
- **mem0** — requires OpenAI embedding APIs; not viable for local/offline use.
- **sqlite-vec** — a SQLite extension (~1MB). Zero server, fully auditable `.db` file, portable, inspectable with standard tools. Brings no runtime of its own; the embedding engine is a separate concern. **Chosen.**
- **BM25 (rank-bm25)** — pure keyword search, 30KB, no model needed. Good on consistent technical vocabulary but misses semantic matches. Used as a fallback when fastembed is not installed.

### Embedding engine

Embedding generation is the main dependency concern. Options evaluated:

- **fastembed** (Qdrant) — uses ONNX models, no torch required. Ships `all-MiniLM-L6-v2` or `BAAI/bge-small-en-v1.5` (~80-130MB one-time download, cached). Fast ONNX inference (~50-100ms per batch). `pip install fastembed` is the full install. **Recommended.**
- **sentence-transformers** — same models, but requires torch (~500MB). Too heavy.
- **BM25 fallback** — `rank-bm25`, 30KB, no download. Activates when fastembed is not installed.

Default model: **`BAAI/bge-small-en-v1.5`** — better quality than all-MiniLM-L6-v2 on technical text, 384 dimensions, ONNX-based. Configurable via `.takt/config.yaml`.

Retrieval quality was validated via prototype: with 440 drawers mined from the spec corpus, queries like "merge conflict resolution strategy", "TUI terminal interface", and "scheduler followup beads" all returned the correct document as the top result with positive similarity scores.

### Files are the source of truth; DB is a derived cache

The SQLite DB is binary and cannot be meaningfully committed to git (unresolvable merge conflicts). Markdown files remain primary:

```
git tracks:     docs/memory/entries/*.md   ← text, diffable, mergeable
git ignores:    docs/memory/memory.db      ← local cache, rebuildable
```

After `git pull` (if peers added new memory files), run `takt memory rebuild` to sync the local index. This is analogous to how compiled artifacts are not committed — source is versioned, derived form is rebuilt locally.

### Concurrent writes from parallel agents

Worker agents run in parallel (up to N workers). Multiple agents may call `takt memory add` concurrently. SQLite in WAL mode serializes concurrent writers safely — the lock is held only for the duration of the INSERT (~1ms). Embedding generation happens before opening the transaction (outside the lock). Contention is negligible in practice for the memory write workload.

### Single write path: CLI only

`takt memory add` is the only supported way to write a memory. It atomically:
1. Writes a `.md` file to `docs/memory/entries/`
2. Generates embedding via fastembed
3. INSERTs text + vector into the local DB

Direct file edits bypass the DB and require `takt memory rebuild` to re-sync. This is documented but not enforced.

### Agents call the CLI directly during execution

Agents are given `takt memory search` and `takt memory add` as CLI tools they call during bead execution — not as a structured output field at the end. This is consistent with how agents already use `spec.py` and `git`. It allows agents to write memories at the point of discovery rather than as a bureaucratic final step, and avoids requiring schema changes to the agent output format.

### Scope: shared worker memory only

This spec covers only the **shared in-repo worker memory** (`docs/memory/`). The operator's personal cross-session memory (`~/.claude/projects/.../memory/`) is managed by Claude Code's built-in auto-memory system and is out of scope. The current state where the operator memory skill and worker memory skill are identical files is a known issue but addressed separately.

## Problems to Fix

1. **Agents read all memories regardless of relevance.** Both files are read in full at bead start. As the corpus grows, this wastes context budget on irrelevant entries.
2. **No retrieval quality.** There is no way to fetch the 3 most relevant memories for a given task — it's all or nothing.
3. **Append-only files grow unbounded.** No mechanism to supersede or replace stale entries. Old entries accumulate noise.
4. **Operator and worker skills are identical.** Both `claude_skills/memory/SKILL.md` and `agents_skills/memory/SKILL.md` are the same file, pointing to the same shared files. There is no meaningful distinction between operator and worker memory.
5. **Write path is fragile.** Agents append directly to files, with no validation or structure beyond a date heading convention.

## Changes

### New: `takt memory` CLI subcommand (`cli.py`)

```bash
takt memory add --type <type> "<content>"   # write memory entry
takt memory search "<query>" [--limit N]    # semantic search, default limit from config
takt memory rebuild                         # rebuild DB from all .md files
takt memory list                            # list all entries as plain table
```

`--type` values: `convention`, `known-issue`, `decision`, `warning`.

`takt memory add` prints the created file path and confirms the DB write.
`takt memory search` prints ranked results with score, type, date, and content.

### New: `src/agent_takt/memory.py`

New module:

```python
def add_entry(content: str, type: str, project_root: Path, config: MemoryConfig) -> str
    # writes .md file + embeds + INSERTs; returns entry id

def search(query: str, project_root: Path, config: MemoryConfig, limit: int) -> list[MemoryResult]
    # returns ranked (score, content, type, date, source_file)

def rebuild(project_root: Path, config: MemoryConfig) -> int
    # scans entries/*.md, re-embeds all, repopulates DB; returns count

def _embed(text: str, model: str) -> np.ndarray
    # fastembed or BM25 fallback

def _db_path(project_root: Path) -> Path
    # returns docs/memory/memory.db
```

DB schema:
```sql
CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    date TEXT NOT NULL,
    source_file TEXT NOT NULL
);
CREATE VIRTUAL TABLE vec_entries USING vec0(embedding float[384]);
PRAGMA journal_mode=WAL;
```

### New: `docs/memory/entries/` directory

One `.md` file per memory entry, replacing the two monolithic files. Filename: `{YYYY-MM-DD}-{slug}.md`.

```markdown
---
type: convention
date: 2026-04-07
---
Always use -s resolve when merging feature branches with a criss-cross history.
```

Existing `known-issues.md` and `conventions.md` are migrated: each dated entry (`## YYYY-MM-DD — Title`) becomes a separate file; type inferred from source file (`known-issues` → `known-issue`, `conventions` → `convention`).

### Updated: memory skill (`SKILL.md`)

Replace direct-file-append instructions with CLI calls:

```bash
# At bead start — fetch relevant context
uv run takt memory search "<brief description of current task>" --limit 5

# When you discover something reusable
uv run takt memory add --type convention "Always use X when Y"
uv run takt memory add --type known-issue "Z behaves unexpectedly because W"
```

Remove: "read both files in full at bead start", append-only format section, direct file write instructions. Retain: access control table (developer/tester can write; docs/review read-only), guidance on what is worth recording.

Update both `src/agent_takt/_data/agents_skills/memory/SKILL.md` and `src/agent_takt/_data/claude_skills/memory/SKILL.md`.

### Updated: `src/agent_takt/config.py`

Add `MemoryConfig` dataclass:

```python
@dataclass(frozen=True)
class MemoryConfig:
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    search_limit: int = 5
    fallback_bm25: bool = True
```

Wired into `OrchestratorConfig`.

### Updated: `src/agent_takt/_data/default_config.yaml`

```yaml
memory:
  embedding_model: BAAI/bge-small-en-v1.5
  search_limit: 5
  fallback_bm25: true
```

### Updated: `src/agent_takt/onboarding.py`

`scaffold_project()` creates `docs/memory/entries/` and seeds an empty `memory.db` with WAL mode enabled.

### Updated: `.gitignore` (repo + bundled)

Add `docs/memory/memory.db`.

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/cli.py` | Add `takt memory` subcommand (add, search, rebuild, list) |
| `src/agent_takt/memory.py` | New module: embed, store, search, rebuild |
| `src/agent_takt/config.py` | Add `MemoryConfig` dataclass |
| `src/agent_takt/onboarding.py` | Create `docs/memory/entries/`, seed empty DB |
| `src/agent_takt/_data/default_config.yaml` | Add `memory:` block |
| `src/agent_takt/_data/agents_skills/memory/SKILL.md` | Replace file-append with CLI calls |
| `src/agent_takt/_data/claude_skills/memory/SKILL.md` | Same update |
| `.gitignore` | Add `docs/memory/memory.db` |
| `docs/memory/known-issues.md` | Migrate entries to `docs/memory/entries/*.md` |
| `docs/memory/conventions.md` | Migrate entries to `docs/memory/entries/*.md` |

## Acceptance Criteria

- `takt memory add --type convention "..."` writes a `.md` file to `docs/memory/entries/` AND inserts into the local DB. If the DB insert fails, the `.md` file is removed (atomic).
- `takt memory search "merge conflict"` returns the top-N most semantically relevant entries ranked by cosine similarity, within 2 seconds on a corpus of 100 entries.
- `takt memory rebuild` correctly reconstructs the DB from all `.md` files in `docs/memory/entries/`. Running it twice produces the same DB (idempotent).
- `docs/memory/memory.db` is listed in `.gitignore` and is not tracked by git.
- Concurrent calls to `takt memory add` from 4 parallel worker processes do not corrupt the DB (WAL mode, tested explicitly).
- The memory skill instructs agents to call `takt memory search` at bead start and `takt memory add` when they discover something reusable. Agents no longer read the full memory files.
- Existing entries in `known-issues.md` and `conventions.md` are auto-migrated to `docs/memory/entries/` on first `takt memory rebuild` if the legacy files exist.
- `takt init` on a new project creates `docs/memory/entries/` and an empty `memory.db` with WAL mode enabled.
- When fastembed is not installed, `takt memory search` falls back to BM25 keyword search and prints a warning indicating semantic search is unavailable.
- All existing tests pass. New tests cover: add/search/rebuild round-trip, concurrent writes (4 workers), BM25 fallback, auto-migration of legacy files, idempotent rebuild.

## Pending Decisions

- **fastembed as hard or optional dependency**: if hard (`install_requires`), always available but adds ~80MB to the base install. If optional (`extras_require = {"memory": ["fastembed"]}`), BM25 fallback must be good enough for degraded use. Recommend optional: `pip install agent-takt[memory]`.
- **Should operator and worker skills diverge?** Currently identical. The worker skill should use `takt memory search` for retrieval at bead start. The operator (Claude Code) already has session context loaded and may query on demand. Could leave them identical for now and split in a follow-up spec.
- **Embedding dimensionality in schema**: `BAAI/bge-small-en-v1.5` produces 384 dimensions. Changing model after initial setup requires a full `takt memory rebuild`. The schema should record which model was used to detect model mismatch on search/add.
- **What happens to `docs/memory/known-issues.md` and `docs/memory/conventions.md` after migration?** Options: delete them, keep as read-only archives, or redirect with a note. Recommend keeping with a header note pointing to the new system.
