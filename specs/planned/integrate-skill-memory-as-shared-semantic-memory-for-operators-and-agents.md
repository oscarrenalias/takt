---
name: Integrate skill-memory as shared semantic memory for operators and agents
id: spec-cbb95a79
description: "Replace append-only markdown memory with native takt semantic memory (sqlite-vec + ONNX embeddings), shared across operator and workers, with mandatory guardrail enforcement and takt memory CLI"
dependencies: null
priority: medium
complexity: medium
status: planned
tags:
- memory
- search
- sqlite
- embeddings
- guardrails
scope:
  in: Native takt memory module; pyproject.toml deps; takt memory CLI; guardrail templates; takt init DB bootstrap; TAKT_CMD + AGENT_MEMORY_DB runner injection; spec ingestion at planning time; onboarding scaffold
  out: Operator personal cross-session memory (~/.claude/projects/.../memory/); agent output schema; scheduler/planner changes beyond spec ingestion; APM/external script management
feature_root_id: B-36010371
---
# Integrate skill-memory as shared semantic memory for operators and agents

## Objective

The current shared memory system stores institutional knowledge in two append-only markdown files (`docs/memory/known-issues.md`, `docs/memory/conventions.md`). Agents are instructed to read these files at bead start — but guardrails do not enforce this, memory usage is inconsistent, and there is no retrieval: agents receive all accumulated knowledge regardless of relevance.

This spec replaces the current skill with a native takt memory subsystem backed by SQLite + sqlite-vec with local ONNX embeddings (BAAI/bge-small-en-v1.5). Memory is a mandatory system feature: the required dependencies (`onnxruntime`, `sqlite-vec`, `tokenizers`, `numpy`) are added to takt's main `pyproject.toml`. Both worker agents and the operator share a single project-level database at `.takt/memory/memory.db`. All memory operations are exposed through the `takt memory` CLI — there is no external script to install, copy, or manage. Agents call `takt memory` via an injected `TAKT_CMD` env var. A namespace strategy isolates global knowledge from feature-specific findings and ingested specs.

## Problems to Fix

1. **Memory usage is advisory, not enforced.** The current `SKILL.md` says "read both files before touching any code" but guardrail templates contain no corresponding enforcement. Agents regularly skip memory.
2. **No retrieval quality.** Agents read both files in full. As content grows this wastes context budget on irrelevant entries.
3. **Operator and worker memories are disconnected.** The operator (Claude Code) has a separate personal memory under `~/.claude/projects/`. Worker agents share `docs/memory/`. There is no mechanism for institutional knowledge to cross that boundary.
4. **No migration path for existing content.** `known-issues.md` and `conventions.md` contain accumulated knowledge that would be lost when switching to the new system.
5. **Specs are not available to agents.** Agents have no way to retrieve the spec behind their feature during execution.

## Namespace Strategy

All memory operations are scoped to a namespace via `--namespace <name>`. Three namespaces are defined:

| Namespace | Contents | Who writes | Who reads |
|---|---|---|---|
| `global` | Project-wide conventions, architectural decisions, known issues | Developer, tester, planner | All agents |
| `feature:{feature_root_id}` | Feature-specific findings, scoped to one feature tree | Developer, tester | All agents in the same feature tree |
| `specs` | Spec documents ingested at planning time | Takt planner (automated) | All agents (read-only) |

**Searching.** At bead start agents search both `global` and their feature namespace:

```
$TAKT_CMD memory search "<task summary>" --namespace global --limit 5
$TAKT_CMD memory search "<task summary>" --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --limit 5
$TAKT_CMD memory search "<task summary>" --namespace specs --limit 3
```

**Writing.** Agents write to the namespace that matches the finding's scope:

```
# Project-wide finding
$TAKT_CMD memory add "Always use X when doing Y" --namespace global --source developer

# Feature-specific finding
$TAKT_CMD memory add "Z behaves unexpectedly here because W" \
    --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --source developer
```

**Env var injection.** The runner injects into every agent subprocess:
- `TAKT_CMD` — full invocation prefix: `uv run --directory /abs/path/to/project takt`
- `AGENT_MEMORY_DB` — absolute path to `.takt/memory/memory.db`
- `AGENT_TAKT_FEATURE_ROOT_ID` — the bead's `feature_root_id`, or `global` for standalone beads

Agents never construct takt's path themselves; they always use `$TAKT_CMD`.

## Single Database Guarantee

All agents (regardless of worktree, bead, or runner backend) and the operator share one file: `{project_root}/.takt/memory/memory.db`.

- **Worker agents**: `AGENT_MEMORY_DB` is injected as an absolute path by `runner.py` before subprocess launch. No agent can accidentally create an isolated DB.
- **Operator**: `takt memory` resolves the project root via the standard `.takt/` directory traversal (same mechanism as all other takt commands) and uses `{project_root}/.takt/memory/memory.db`.
- **Concurrency**: the DB is opened in WAL mode by `takt memory init`. Parallel workers writing simultaneously is safe.
- **Git**: `.takt/` is already in `.gitignore`; no additional exclusion entry is needed.

## Changes

### 1. Add memory dependencies to `pyproject.toml`

Memory is a core feature. Add to the main `[project.dependencies]` section:

```toml
"onnxruntime>=1.17",
"sqlite-vec>=0.1",
"tokenizers>=0.19",
"numpy>=1.26",
```

No optional extras. `takt memory init` (called by `takt init`) creates the DB and eagerly downloads the ONNX model to `~/.cache/agent-takt/models/`. There is no flag to skip the download — the model is required for all memory operations and must be present before any agent runs.

### 2. Implement `src/agent_takt/memory.py`

New module implementing the memory backend. Public API:

```python
def init_db(db_path: Path) -> None:
    """Create DB, enable sqlite-vec extension, create tables in WAL mode. Idempotent."""

def add_entry(
    db_path: Path,
    text: str,
    namespace: str = "global",
    source: str = "",
    metadata: dict | None = None,
) -> str:
    """Embed text and insert into DB. Returns UUID of the new entry."""

def search(
    db_path: Path,
    query: str,
    namespace: str | None = None,
    limit: int = 5,
    threshold: float | None = None,
) -> list[dict]:
    """Semantic search. If namespace is None, searches all namespaces and returns
    a merged, distance-sorted result set. Returns list of
    {id, text, namespace, source, distance, metadata} dicts."""

def ingest_file(
    db_path: Path,
    path: Path,
    namespace: str = "global",
    source: str = "",
) -> int:
    """Chunk and ingest a .md/.txt/.json/.csv file. Returns number of entries added."""

def delete_entry(db_path: Path, entry_id: str) -> None: ...

def stats(db_path: Path) -> dict: ...
```

**Embedding model**: BAAI/bge-small-en-v1.5 ONNX (384-dim, ~24 MB). Cache location: `~/.cache/agent-takt/models/` (user-level, shared across projects; downloaded once per machine). The cache path is resolved via `platformdirs` or hardcoded as `Path.home() / ".cache" / "agent-takt" / "models"`.

**Chunking** (for `ingest_file`): paragraph boundaries for plain text and JSON; level-2 headings as hard breaks for Markdown; 1000-character target chunk size with sentence-boundary fallback for oversized paragraphs.

**Deduplication**: `ingest_file` skips chunks whose embedding is within distance 0.05 of an existing entry in the same namespace (idempotent re-ingestion).

### 3. Add `takt memory` CLI subcommand

New module `src/agent_takt/cli/commands/memory.py` implementing:

```
takt memory init                              # create DB + eagerly download ONNX model; called by takt init
takt memory add "<text>" [--namespace <ns>] [--source <tag>]
takt memory search "<query>" [--namespace <ns>] [--limit N] [--threshold F]
                                              # omit --namespace to search all namespaces (results merged + sorted by distance)
takt memory ingest <path> [--namespace <ns>] [--source <tag>]
takt memory ingest --migrate                  # ingest docs/memory/known-issues.md + conventions.md → global
takt memory delete <uuid>
takt memory stats
```

All subcommands resolve `db_path` as `project_root / ".takt" / "memory" / "memory.db"` using `find_project_root()` (standard takt root traversal). No `AGENT_MEMORY_DB` is read from the environment for the operator-facing CLI — the path is always derived from the project root.

Register `memory` as a top-level subcommand in `src/agent_takt/cli/parser.py` and wire `command_memory` into the dispatch in `src/agent_takt/cli/__init__.py`.

### 4. Inject `TAKT_CMD`, `AGENT_MEMORY_DB`, `AGENT_TAKT_FEATURE_ROOT_ID` in `runner.py`

In `runner.py`, when constructing the subprocess environment for any agent, add:

```python
env["TAKT_CMD"] = _resolve_takt_cmd(workspace_root)
env["AGENT_MEMORY_DB"] = str(workspace_root / ".takt" / "memory" / "memory.db")
env["AGENT_TAKT_FEATURE_ROOT_ID"] = bead.feature_root_id or "global"
```

`TAKT_CMD` is resolved once at runner startup via:

```python
def _resolve_takt_cmd(workspace_root: Path) -> str:
    # Self-hosting: the orchestrated project itself depends on agent-takt.
    # Use uv run to guarantee the project-pinned version is used, not a
    # global binary that may be at a different version.
    pyproject = workspace_root / "pyproject.toml"
    if pyproject.exists() and "agent-takt" in pyproject.read_text(encoding="utf-8"):
        return f"uv run --directory {workspace_root} takt"
    # Non-self-hosting: prefer a global install if present.
    takt_bin = shutil.which("takt")
    if takt_bin:
        return takt_bin
    # Last resort: uv run (will fail with a clear uv error if takt is absent).
    return f"uv run --directory {workspace_root} takt"
```

This handles both install modes:
- **Self-hosting** (project's `pyproject.toml` contains `agent-takt`): always `uv run --directory <project_root> takt`, ensuring the project-pinned version is used regardless of what global binaries are on PATH.
- **Non-self-hosting, global install** (`uv tool install agent-takt`): `takt` resolved via PATH.
- **Non-self-hosting, no global install**: `uv run --directory <project_root> takt` as last resort.

`workspace_root` is the main project checkout path (already available in the runner). This applies to both `CodexAgentRunner` and `ClaudeCodeAgentRunner`.

### 5. Update memory `SKILL.md` files

Replace the current append-only markdown instructions in both skill directories with instructions that reference `$TAKT_CMD`.

**Worker skill** (`src/agent_takt/_data/agents_skills/memory/SKILL.md`):

```markdown
# memory

Shared semantic memory stores institutional knowledge accumulated across all beads.
Use it to avoid re-learning what the team already knows, and to record findings for future agents.

## At Bead Start (mandatory — do not skip)

Before reading any code or planning your approach, search for relevant context:

    $TAKT_CMD memory search "<brief description of your task>" --namespace global --limit 5
    $TAKT_CMD memory search "<brief description of your task>" --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --limit 5
    $TAKT_CMD memory search "<brief description of your task>" --namespace specs --limit 3

Apply relevant results. **Do not proceed until you have run all three searches.**

## When to Write a Memory Entry

Write a new entry when you discover something that:
- Would have changed your approach if you had known it upfront
- Is reusable beyond the current bead
- Is not already in CLAUDE.md or your guardrail template

Choose the namespace that matches the finding's scope:

    # Project-wide finding
    $TAKT_CMD memory add "<finding>" --namespace global --source <your-agent-type>

    # Feature-specific finding
    $TAKT_CMD memory add "<finding>" --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --source <your-agent-type>

Do NOT write to the `specs` namespace — it is managed by the takt planner.

## Access Control

| Agent type    | Read all namespaces | Write global | Write feature |
|---------------|---------------------|--------------|---------------|
| Planner       | yes | yes | yes |
| Developer     | yes | yes | yes |
| Tester        | yes | yes | yes |
| Documentation | yes | no  | no  |
| Review        | yes | no  | no  |
```

**Operator skill** (`src/agent_takt/_data/claude_skills/memory/SKILL.md`) — shorter, on-demand retrieval:

```markdown
# memory

Shared semantic memory accumulates institutional knowledge from all agents across all bead runs.

Use `takt memory` for all operations. If takt is not on your PATH, prefix with `uv run`.

## Retrieval

    takt memory search "<topic>" --namespace global --limit 5
    takt memory search "<topic>" --namespace feature:<feature_root_id> --limit 5
    takt memory search "<topic>"              # search all namespaces

## Writing

    takt memory add "<finding>" --namespace global --source operator
    takt memory add "<finding>" --namespace feature:<feature_root_id> --source operator

## Bulk ingestion

    takt memory ingest <path>              # ingest a file into global namespace
    takt memory ingest --migrate           # migrate docs/memory/ markdown files
```

### 6. Update guardrail templates

Add a mandatory memory section to all relevant agent guardrail templates. Language is prescriptive — "do not proceed" is not advisory.

**`templates/agents/developer.md`** — add after "Allowed actions":

```markdown
## Memory (mandatory — do not skip)

**At bead start**: before reading any code, run all three memory searches:

    $TAKT_CMD memory search "<summary of your assigned task>" --namespace global --limit 5
    $TAKT_CMD memory search "<summary of your assigned task>" --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --limit 5
    $TAKT_CMD memory search "<summary of your assigned task>" --namespace specs --limit 3

Apply relevant results. Do not proceed until all three searches are complete.

**At bead end**: if you discovered something reusable, write it to memory:

    $TAKT_CMD memory add "<finding>" --namespace global --source developer
    $TAKT_CMD memory add "<finding>" --namespace feature:$AGENT_TAKT_FEATURE_ROOT_ID --source developer
```

Apply the equivalent block to:
- `templates/agents/tester.md` — same read+write, source=tester
- `templates/agents/planner.md` — same read+write, source=planner
- `templates/agents/documentation.md` — read (three searches) only, no write block
- `templates/agents/review.md` — read (three searches) only, no write block

`recovery.md` and `merge-conflict.md` — no memory section needed.

### 7. Spec ingestion at planning time

In `planner.py`, after `--write` successfully persists beads, ingest the spec into the `specs` namespace:

```python
from .memory import ingest_file
from .storage import find_project_root  # or however root is available

db_path = project_root / ".takt" / "memory" / "memory.db"
try:
    count = ingest_file(db_path, spec_path, namespace="specs", source="planner")
    logger.info("Ingested %d chunks from spec into memory (namespace=specs)", count)
except Exception as exc:
    logger.warning("Spec memory ingestion failed (non-fatal): %s", exc)
```

Failure is non-fatal: log a warning, do not abort planning.

### 8. Update `scaffold_project()` in `onboarding.py`

Two changes:

**a) Always overwrite installed skill files.** `scaffold_project()` currently skips files that already exist. Change `install_agents_skills()` and `install_claude_skills()` to overwrite unconditionally. This is safe because `.agents/skills/` and `.claude/skills/` are managed outputs of `takt init`, not user-edited files. This is the mechanism by which `.claude/skills/memory/SKILL.md` gets updated — the bead cannot write there directly at runtime, so the update is deferred to `takt init`. After merging this feature, the user runs `takt init` once to propagate new skill content to both installed locations.

**b) Bootstrap the memory DB and ONNX model.** After installing skills:

```python
from .memory import init_db

db_path = project_root / ".takt" / "memory" / "memory.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
init_db(db_path)  # idempotent; downloads ONNX model on first run
```

### 9. No changes to `prepare_isolated_execution_root`

Skills are copied as before (SKILL.md only for the memory skill — no script to copy). All memory operations go through `$TAKT_CMD memory`, which points back to the host takt installation via `uv run --directory <project_root>`. No venv management, no script extraction.

## Files to Modify

| File | Change |
|---|---|
| `pyproject.toml` | Add `onnxruntime`, `sqlite-vec`, `tokenizers`, `numpy` to `[project.dependencies]` |
| `src/agent_takt/memory.py` | New — native memory backend (init_db, add_entry, search, ingest_file, delete_entry, stats) |
| `src/agent_takt/cli/commands/memory.py` | New — `takt memory` subcommand (init, add, search, ingest, delete, stats) |
| `src/agent_takt/cli/parser.py` | Register `memory` subcommand |
| `src/agent_takt/cli/__init__.py` | Wire `command_memory` into dispatch |
| `src/agent_takt/runner.py` | Inject `TAKT_CMD`, `AGENT_MEMORY_DB`, `AGENT_TAKT_FEATURE_ROOT_ID` into subprocess env |
| `src/agent_takt/onboarding.py` | Call `init_db` post-install to bootstrap DB |
| `src/agent_takt/planner.py` | Call `ingest_file` after `--write` to ingest spec into `specs` namespace |
| `src/agent_takt/_data/agents_skills/memory/SKILL.md` | Replace with `$TAKT_CMD`-based instructions (source for `takt init`) |
| `.agents/skills/memory/SKILL.md` | Replace with same content — updated directly by the bead since `_skill_path()` resolves this before `_data/` |
| `src/agent_takt/_data/claude_skills/memory/SKILL.md` | Replace with operator instructions (source for `takt init`) |
| `.claude/skills/memory/SKILL.md` | **Not modified by the bead** — Claude Code blocks writes here at runtime. Updated by `takt init` post-merge via the overwrite behaviour added to `scaffold_project()`. |
| `templates/agents/developer.md` | Add mandatory memory section |
| `templates/agents/tester.md` | Add mandatory memory section |
| `templates/agents/planner.md` | Add mandatory memory section |
| `templates/agents/documentation.md` | Add read-only memory section |
| `templates/agents/review.md` | Add read-only memory section |

`prepare_isolated_execution_root` in `skills.py` — **no changes needed**.

## Acceptance Criteria

- `pyproject.toml` lists `onnxruntime`, `sqlite-vec`, `tokenizers`, `numpy` as required dependencies. `uv sync` on a clean environment installs them without extras flags.
- `takt init` on a new project creates `.takt/memory/memory.db` in WAL mode and eagerly downloads the ONNX model to `~/.cache/agent-takt/models/` (one-time per machine; subsequent inits skip the download if the model is already cached).
- `takt memory add "test" --namespace global` and `takt memory search "test" --namespace global` round-trip correctly.
- `takt memory search "test"` (no `--namespace`) returns results from all namespaces, merged and sorted by distance. Each result includes a `namespace` field indicating its origin.
- All agent subprocesses receive `TAKT_CMD`, `AGENT_MEMORY_DB`, and `AGENT_TAKT_FEATURE_ROOT_ID` in their environment. `echo $TAKT_CMD` from inside an execution root returns a valid invocation string. When `workspace_root/pyproject.toml` contains `agent-takt` (self-hosting), `TAKT_CMD` is `uv run --directory <project_root> takt`. Otherwise it is the absolute path to the global `takt` binary if present.
- An agent calling `$TAKT_CMD memory search "..."` from its execution root reaches the same `.takt/memory/memory.db` as the operator running `takt memory search "..."` from the project root.
- All writes from ≥4 parallel agents land in the same DB without corruption (WAL mode tested).
- Standalone beads (no feature root) receive `AGENT_TAKT_FEATURE_ROOT_ID=global`.
- `takt plan --write <spec-file>` ingests the spec into the `specs` namespace. `takt memory search "<keyword from spec>" --namespace specs` returns relevant chunks. Ingestion failure does not abort planning.
- `takt memory ingest --migrate` ingests `docs/memory/known-issues.md` and `docs/memory/conventions.md` into the `global` namespace. Running it twice does not produce duplicate entries (deduplication by embedding distance).
- Guardrail templates for developer, tester, planner, documentation, and review agents contain the mandatory memory section using `$TAKT_CMD` and `$AGENT_TAKT_FEATURE_ROOT_ID`. Developer/tester/planner templates include write instructions; documentation/review are read-only.
- Running `takt init` after merging this feature overwrites `.claude/skills/memory/SKILL.md` and `.agents/skills/memory/SKILL.md` with the new content from `_data/`. `scaffold_project()` no longer skips existing skill files.
- All existing tests pass. New tests cover: `init_db` idempotency, `add_entry`+`search` round-trip, `ingest_file` chunking and deduplication, `TAKT_CMD`/`AGENT_MEMORY_DB`/`AGENT_TAKT_FEATURE_ROOT_ID` injection in both runner backends, `takt plan --write` spec ingestion (including non-fatal failure path).

## Resolved Decisions

- **ONNX model download**: eager download during `takt init`. No skip flag. The model is mandatory; all memory operations require it.
- **`takt memory search` without `--namespace`**: searches all namespaces, merges results, sorts by distance. Each result includes a `namespace` field.
