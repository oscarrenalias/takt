---
name: Document index and search for specs and ADRs
id: spec-57fe01fe
description: Section-level index of specs and ADRs in the memory DB, kept current via the document core hook, with hybrid search and reindex commands
dependencies: null
priority: high
complexity: null
status: done
tags:
- documents
- search
- memory
- specs
- adr
scope:
  in: null
  out: null
feature_root_id: B-7a045748
---
# Document index and search for specs and ADRs

## Objective

Operators and agents cannot search specs or ADRs. The shared memory database holds an anonymous one-off backfill of spec text (namespace `specs`, source `backfill`) whose results do not say which document they came from; ADRs are not in it at all; and nothing keeps it current — a spec moved to `done` or an ADR approved is never re-indexed. This spec adds a **document index** maintained by the document core (spec-b1b9c11d): every spec and ADR is chunked by section and stored with its ID, type, status, title and section heading in dedicated tables of the existing memory database; the core's `on_document_changed` hook marks changed documents stale and every search first reconciles the index with the files (content hash, path, status), re-embedding only what changed; and `takt spec search` / `takt adr search` return ranked documents (ID, title, section, snippet) using combined semantic and keyword matching. A `reindex` command rebuilds it from scratch. Loading search results or ADRs into agent prompts is out of scope (follow-up spec).

## Problems to Fix

1. **Results are anonymous.** `takt memory search --namespace specs` returns chunk text with `source: backfill` (or `planner`) and no document ID, title, path or section, so a hit cannot be followed to its document.
2. **ADRs are not searchable.** No code path ingests ADRs into the database.
3. **The data goes stale.** `Planner._ingest_spec` (`src/agent_takt/planner.py`) ingests a spec once at `plan --write`, in whatever state it is then; later edits, status changes and new specs/ADRs are never reflected, and ingestion dedups by embedding similarity (`ingest_file`), so edited text accumulates instead of replacing old chunks.
4. **No exact-term matching.** Pure semantic search ranks names like `events.jsonl`, `merge=ours` or `commit_bead_state` poorly.
5. **No way to inspect or rebuild.** There is no command to see what is indexed or to rebuild the index.

## Changes

### 1. Index storage — `src/agent_takt/documents/index.py` (NEW)

**Database location** — one function, `document_index_db(root) -> Path`: `AGENT_MEMORY_DB` if set, else `main_checkout_root(root) / ".takt/memory/memory.db"`. It is used by the hook, search, reindex and the stats field. `main_checkout_root` and `find_project_root` move from `runner.py` (`_main_checkout_root`, `_find_project_root`) to a new `src/agent_takt/paths.py`; `runner.py` imports them and keeps `_main_checkout_root`/`_find_project_root` as aliases so existing imports (e.g. `tests/test_runner_memory_env.py`, `tests/test_memory_backend.py`) keep working (behaviour unchanged). For workers this resolves to the same main database as `takt memory`.

**Schema** (created idempotently by `ensure_index_schema(db_path)` in the existing memory database, separate from memory `entries`/`vectors`, so `takt memory` data and commands are unaffected):
- `doc_index_documents(doc_type TEXT, doc_id TEXT, path TEXT, status TEXT, title TEXT, tags TEXT /*JSON list*/, content_hash TEXT /*NULL = stale*/, indexed_at TEXT, PRIMARY KEY(doc_type, doc_id))`
- `doc_index_chunks(chunk_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT, doc_id TEXT, section TEXT, ord INTEGER, text TEXT)` — rowids are never reused.
- `doc_index_vectors` — `vec0` virtual table (`embedding FLOAT[384]`), rowid = `chunk_id`.
- `doc_index_fts` — a regular (not external-content) FTS5 table with columns `text`, `section`, `title`, default `unicode61` tokenizer, rowid = `chunk_id`.

**Embedding plumbing:** `index.py` calls embeddings through the module attribute (`from .. import memory` … `memory._embed(text)`), so tests can patch `agent_takt.memory._embed` and `agent_takt.memory._download_model` as existing memory tests do. Every code path that embeds first calls `memory.configure_model_cache_dir(load_config(main_root).common.memory_cache_dir)`. Tests never download the model.

**Chunking** (`chunk_document(doc) -> list[Chunk]`): frontmatter is not chunk text. Chunk 0 is a summary: title + `description` when present. Then one chunk per `##` section; a `###` subsection is its own chunk with section `"<## heading> / <### heading>"`; text before the first heading is section `"(preamble)"`; headings inside fenced code blocks are not split points; a chunk larger than `memory._split_if_large`'s limit is split further with the same section name. The embedded text is prefixed `"<title> — <section>\n"`; the stored `text` is the raw section text. **Title:** frontmatter `title` (ADR) or `name` (spec), else the first `# ` heading, else the filename stem (legacy specs). If two documents share `(doc_type, doc_id)` (e.g. legacy specs with the same stem in different folders), the first in sorted path order is indexed and one warning is printed.

**API:**
- `index_document(db_path, doc_type, doc)`: (1) compute all chunk embeddings with no database connection open; (2) open via `memory._open_conn` and `BEGIN IMMEDIATE`; (3) if the stored row already has the same `content_hash`, `path` and `status`, roll back and return (a concurrent sync already did it); (4) delete the document's rows from `doc_index_vectors`, `doc_index_fts`, `doc_index_chunks`, then `doc_index_documents`; (5) insert the new rows (`content_hash` = SHA-256 of the file bytes) and commit.
- `mark_stale(db_path, doc_type, doc_id, path, status, title, tags)`: in one `BEGIN IMMEDIATE` transaction, upsert the documents row with the given metadata and `content_hash = NULL`; chunks are left in place until re-indexed. No embedding.
- `remove_document(db_path, doc_type, doc_id)`: delete all rows for the document in one `BEGIN IMMEDIATE` transaction.
- `sync_index(root, db_path, doc_types=("spec","adr"), dry_run=False) -> SyncResult(added, updated, removed, unchanged)`: load documents from the **main checkout** via `get_store(main_root, t).load_all()` (legacy specs included). A document needs re-indexing when it has no row, or its `content_hash` (file SHA-256), `path` or `status` differs from the row, or the row's `content_hash` is NULL. Rows for documents no longer on disk are removed. Unchanged documents are not re-embedded. With `dry_run`, only counts are computed.
- `search(db_path, query, doc_type, statuses=None, tags=(), limit=10, per_document=True) -> list[Hit]` — see §3.

### 2. Keeping the index current

- **Hook wiring:** `on_document_changed=document_index_hook` is set in the `SPEC_TYPE` definition (`spec_type.py`) and the `ADR_TYPE` definition (`adr_type.py`) themselves, so copies made with `dataclasses.replace` (e.g. `_ADR_DOC_TYPE` in `src/agent_takt/adr.py`) inherit it. `document_index_hook` is a thin function (e.g. in `documents/types.py` or a small `documents/hooks.py`) that imports `documents.index` lazily to avoid import cycles.
- **Hook behaviour:** derive the store root from the path (`<root>/<root_dir>/<folder>/<file>` → `root = path.parent.parent.parent`, valid for removed files too) and `main = main_checkout_root(root)`. Do nothing unless `root.resolve() == main.resolve()` **and** `document_index_db(root)` already exists. Then call `mark_stale(...)` with the document's current metadata — reloaded from the file with the core frontmatter reader, status derived from the parent folder via the document type's folder→status mapping, and the legacy fallbacks (title chain, stem ID) on a frontmatter error — or `remove_document(...)` if the file no longer exists. The hook never embeds, never loads the model and never creates `.takt/` or the database, so `takt spec`/`takt adr` commands stay fast and keep the "no `.takt/` created" guarantee.
- **Failure isolation:** `document_index_hook` wraps its lazy import and the index call in its own `try/except Exception` (`DocumentStore._notify` does not catch). Any error prints one `warning: document index not updated for <id>: <reason>` line to stderr and never changes the command's exit code or the document write.
- **Reconciliation:** `takt spec search`, `takt adr search` and `reindex --check` call `sync_index` for their type first, which re-embeds stale and changed documents and picks up edits made outside takt (`git pull`, merges, hand edits, `git mv`). A sync that re-indexes more than 10 documents prints `indexing <n> <type> documents…` to stderr first.
- **Planner:** `Planner._ingest_spec` and its call in `write_plan` are removed (and their tests in `tests/test_planner.py`). takt no longer writes the memory `specs` namespace.
- **Agents:** every instruction to search `--namespace specs` is replaced with `$TAKT_CMD spec search "<…>"`, plus `$TAKT_CMD adr search "<…>"` for decisions, in: the memory skill (`.claude/skills/memory/SKILL.md`, `.agents/skills/memory/SKILL.md`, `src/agent_takt/_data/claude_skills/memory/SKILL.md`, `src/agent_takt/_data/agents_skills/memory/SKILL.md`); the guardrail templates `templates/agents/{developer,tester,documentation,planner,review,defect}.md` and their `src/agent_takt/_data/templates/agents/` copies; and `docs/multi-backend-agents.md`. The spec-management and adr-management skills (`.claude/skills/skill-{spec,adr}-management/`, `.agents/skills/task/{spec,adr}-management/`, and both `_data` copies) document `search` and `reindex`.

### 3. Search — `takt spec search` / `takt adr search`

```
takt spec search "<query>" [--status S]... [--tag T]... [--limit N] [--chunks] [--json] [--plain]
takt adr search  "<query>" [--status S]... [--tag T]... [--limit N] [--chunks] [--json] [--plain] [--all]
```

- **Root and database:** root is `--root` or the cwd; database via `document_index_db(root)`; documents synced from `main_checkout_root(root)`. From a feature worktree, search therefore reads and syncs the main checkout's index and files. If `<main root>/.takt/` does not exist, exit 1 with `error: no takt project at <root>; document search needs .takt/`. If `.takt/` exists but the database does not, create it with `memory.init_db` (search is a takt-state command, unlike the other `takt spec` subcommands).
- **Keyword leg:** split the query on whitespace, wrap each token in double quotes (internal `"` doubled) and join with `OR`; run against `doc_index_fts` joined to `doc_index_documents` with the type, `--status` and `--tag` filters in SQL; take the top `max(limit*5, 50)` by `bm25`. Queries never raise FTS syntax errors.
- **Vector leg:** embed the query, take the top `k = max(limit*5, 50)` chunks from `doc_index_vectors`, post-filter by type/status/tags, and while fewer than `limit` documents survive and k is below the chunk count, double k and retry.
- **Fusion:** Reciprocal Rank Fusion, `score = Σ 1/(60 + rank)` over the legs a chunk appears in. By default keep the best chunk per document; `--chunks` returns individual chunks. Truncate to `--limit` (default 10).
- **Filters:** `--status` repeatable (OR), `--tag` repeatable (AND). `takt adr search` excludes `superseded` and `rejected` unless `--status` is given or `--all` is passed.
- **Snippet:** for chunks found by the keyword leg, `snippet(doc_index_fts, 0, '', '', '…', 16)` (no highlight markers); otherwise the first 160 characters of the chunk; newlines collapsed to spaces.
- **Output (default):** one block per hit:
  ```
  ADR-44e7453e  approved  Bead state never travels on feature branches
    § Decision · 0.032
    …the fallback auto-resolves .takt/beads/ conflicts in favour of the current branch…
  ```
  `--plain`: tab-separated `id\tstatus\tsection\tscore\ttitle`, no snippet. `--json`: array of `{id, type, status, title, path, section, snippet, score}` (`path` relative to the main root). No hits: `No matches.`, exit 0.

### 4. Inspect and rebuild

- `takt spec reindex` / `takt adr reindex`: delete every index row of that type and re-index all its documents from the main checkout; print `indexed <n> <type> documents (<m> chunks)`. `--check` calls `sync_index(..., dry_run=True)` and prints `would add <a>, update <u>, remove <r> (<k> unchanged)`; exit 0.
- `takt memory stats` gains `document_index`: `{"spec": {"documents": N, "chunks": M, "stale": S}, "adr": {...}}` — zeros when the tables are absent; stats never creates them.
- Legacy memory data: reindex does not touch memory `entries`. `docs/documents.md` states that the old `specs` memory namespace is obsolete and how to inspect it (`takt memory namespace show specs`); deleting it is an operator decision.

### 5. Documentation

- `docs/documents.md`: new "Document index and search" section (location, schema, chunking, hook vs sync, worktree behaviour, ranking, commands, troubleshooting).
- `CLAUDE.md`, `docs/adr.md`, `docs/multi-backend-agents.md`: document `search`/`reindex` and the end of plan-time spec ingestion.

## Requirements

### ADDED: document-search / Document index maintained by the document core
takt SHALL keep an index of every spec and ADR in the main checkout's memory database, chunked by section with the document's ID, type, status, title and section heading; the document core MUST mark a document stale (with its new metadata) or remove it from the index whenever it creates, changes, moves or removes that document in the main checkout, without embedding.

#### Scenario: Status change updates the index row
- **WHEN** `takt spec set status planned <spec>` runs in a main checkout whose memory database exists
- **THEN** `doc_index_documents` has that spec with `status` `planned`, the new path and `content_hash` NULL
- **AND** after the next `takt spec search`, the spec has exactly one set of chunks and a non-NULL `content_hash`

#### Scenario: ADR approval is searchable
- **WHEN** `takt adr approve <id>` runs in a main checkout whose memory database exists and `takt adr search` is then run on a phrase from that ADR's Decision section
- **THEN** the ADR is returned with status `approved`

#### Scenario: Hook is inherited by the ADR store
- **WHEN** an `AdrStore` is created for a repository
- **THEN** its document type's `on_document_changed` is the document index hook

#### Scenario: No takt state is created
- **WHEN** `takt spec create "X"` runs in a repository with `specs/` but no `.takt/`
- **THEN** the spec is created, the command exits 0 and no `.takt/` directory exists

#### Scenario: Worktree edits do not touch the shared index
- **WHEN** `takt spec set status done <spec>` runs with the root inside `.takt/worktrees/<id>`
- **THEN** the main checkout's `doc_index_documents` row for that spec is unchanged

#### Scenario: Hook failure does not fail the command
- **WHEN** `agent_takt.documents.index.mark_stale` is patched to raise during `takt spec set tags a,b <spec>`
- **THEN** the tags are written, the command exits 0 and stderr contains `warning: document index not updated`

### ADDED: document-search / Index reconciliation with files
Searching SHALL first reconcile the index with the main checkout's documents, re-indexing documents that are new, stale, or whose content hash, path or status changed, removing rows for deleted documents, and MUST NOT re-embed unchanged documents; concurrent reconciliations MUST NOT duplicate chunks.

#### Scenario: Edit outside takt is picked up
- **WHEN** a spec file is edited directly on disk and `takt spec search` is run
- **THEN** the search reflects the new text and the old text no longer matches that spec

#### Scenario: Move without edits is picked up
- **WHEN** a spec file is moved from `specs/drafts/` to `specs/planned/` with `git mv` (no content change) and `takt spec search` runs
- **THEN** `doc_index_documents.status` for that spec is `planned`

#### Scenario: Deleted document disappears
- **WHEN** an ADR file is deleted from disk and `takt adr search --all` is run
- **THEN** no hit refers to that ADR

#### Scenario: Unchanged documents are not re-embedded
- **WHEN** `takt spec search` runs twice with no file changes in between
- **THEN** the second run calls the embedding function only for the query

#### Scenario: Concurrent indexing does not duplicate
- **WHEN** two threads call `index_document` for the same document at the same time
- **THEN** the document's chunk count equals the count from a single run

### ADDED: document-search / Hybrid ranked search with document identity
`takt spec search` and `takt adr search` SHALL rank documents by combining vector similarity and FTS5 keyword rank with Reciprocal Rank Fusion, each hit carrying ID, type, status, title, path, section and snippet, and MUST support status and tag filters.

#### Scenario: Exact identifier found by keyword
- **WHEN** `takt spec search "merge=ours"` runs and exactly one spec contains that string
- **THEN** that spec is in the results and its snippet contains `merge=ours`

#### Scenario: JSON output carries document identity
- **WHEN** `takt adr search "bead state" --json` runs
- **THEN** every element has `id`, `type`, `status`, `title`, `path`, `section`, `snippet` and `score`

#### Scenario: Superseded ADRs hidden by default
- **WHEN** `takt adr search` runs on a phrase that only matches a superseded ADR
- **THEN** it prints `No matches.`, and with `--all` the superseded ADR is returned

#### Scenario: Filters do not starve results
- **WHEN** 60 superseded ADRs and 1 approved ADR all match a query and `takt adr search` runs with default filters
- **THEN** the approved ADR is returned

#### Scenario: Query with FTS syntax characters
- **WHEN** `takt spec search 'foo" OR (bar'` runs
- **THEN** it exits 0 without an FTS5 syntax error

#### Scenario: Search from a feature worktree uses the main index
- **WHEN** `takt adr search` runs with the root inside `.takt/worktrees/<id>`
- **THEN** results come from the main checkout's index and files

### ADDED: document-search / Rebuild and inspection
takt SHALL provide `reindex` (with `--check`) for each document type, rebuilding that type's index from the main checkout's files, and `takt memory stats` MUST report per-type document, chunk and stale counts without creating index tables.

#### Scenario: Reindex rebuilds
- **WHEN** `takt adr reindex` runs on a repository with 3 ADRs
- **THEN** it prints `indexed 3 adr documents` and the index holds exactly those 3 ADRs

#### Scenario: Stats report the index
- **WHEN** `takt memory stats` runs after `takt spec reindex`
- **THEN** its JSON has `document_index.spec.documents` equal to the number of specs

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/documents/index.py` | NEW — schema, chunking, index/mark_stale/remove/sync, hybrid search, `document_index_db` |
| `src/agent_takt/paths.py` | NEW — `main_checkout_root`, `find_project_root` (moved from `runner.py`) |
| `src/agent_takt/runner.py` | Import the moved helpers |
| `src/agent_takt/documents/spec_type.py`, `src/agent_takt/documents/adr_type.py` | Set `on_document_changed` to the index hook |
| `src/agent_takt/documents/types.py` (or `documents/hooks.py`) | `document_index_hook` with lazy import |
| `src/agent_takt/adr.py` | Verify `_ADR_DOC_TYPE` inherits the hook (no change expected) |
| `src/agent_takt/documents/__init__.py` | Export index API |
| `src/agent_takt/memory.py` | Expose helpers if needed (no schema change to `entries`/`vectors`) |
| `src/agent_takt/cli/commands/spec.py`, `src/agent_takt/cli/commands/adr.py`, `src/agent_takt/cli/parser.py` | `search` and `reindex` subcommands |
| `src/agent_takt/cli/commands/memory.py` | `document_index` in stats |
| `src/agent_takt/planner.py` | Remove `_ingest_spec` and its call |
| Memory skill (4 copies), guardrail templates (12 files), spec/adr-management skills (8 locations), `docs/multi-backend-agents.md` | Replace `--namespace specs`; document search/reindex |
| `tests/test_documents_index.py`, `tests/test_cli_spec_search.py`, `tests/test_cli_adr_search.py` | NEW |
| `tests/test_planner.py` | Remove `_ingest_spec` tests |
| `tests/test_cli_plan.py`, `tests/test_cli_memory.py`, `tests/test_runner_memory_env.py` | Adjust for removed ingestion, stats field, moved helpers |
| `docs/documents.md`, `CLAUDE.md`, `docs/adr.md` | Document the index and commands |

## Acceptance Criteria

- `doc_index_*` tables are created idempotently in the existing memory database; memory `entries`/`vectors` and `takt memory search` results are unchanged by indexing.
- `SPEC_TYPE`, `ADR_TYPE` and `AdrStore`'s document type all carry the index hook; the hook only marks stale/removes, never embeds, never creates `.takt/` or the database, and acts only in a main checkout with an existing database.
- Hook errors produce one warning line and never change exit codes or document writes.
- `sync_index` re-indexes on missing row, NULL/changed `content_hash`, changed `path` or `status`; removes rows for deleted files; does not re-embed unchanged documents (counting fake embedder); `dry_run` writes nothing.
- `index_document` embeds before opening a write transaction, uses `BEGIN IMMEDIATE`, and concurrent calls never duplicate chunks.
- Hybrid ranking uses the specified keyword and vector legs with RRF (k=60) and filter handling; `--status`, `--tag`, `--limit`, `--chunks`, `--json`, `--plain`, `--all` behave as specified; snippets have no highlight markers.
- Queries with FTS5 syntax characters never raise.
- Search, reindex and stats resolve the database with `document_index_db` and work from a feature worktree against the main checkout.
- `takt spec reindex` / `takt adr reindex` (with `--check`) and `takt memory stats` `document_index` work as specified.
- `Planner._ingest_spec` is removed; `takt plan --write` no longer writes memory entries.
- No skill, guardrail template or doc instructs agents to search `--namespace specs`; memory, spec-management and adr-management skills document `takt spec search` / `takt adr search`.
- Tests patch `agent_takt.memory._embed` / `_download_model` and never download the model.
- Manual (operator, not a test): on this repository after `takt spec reindex` and `takt adr reindex`, `takt adr search "bead state on feature branches"` returns ADR-44e7453e in the top 3.

## Pending Decisions

1. ~~Embed in the hook?~~ **Resolved: no.** The hook marks documents stale; `sync_index` (run before every search) is the only place that embeds, keeping `takt spec`/`takt adr` commands fast and model-free.
2. ~~Separate database or tables?~~ **Resolved:** same database file as memory, separate `doc_index_*` tables.
3. ~~Index worktree copies?~~ **Resolved: no.** The shared index reflects the main checkout only; worktree writes skip the hook and worktree searches read main.
4. ~~Include `specs/current/` and `docs/`?~~ **Resolved: no**, specs and ADRs only. Other document kinds can be added as document types later.
5. ~~Delete the legacy `specs` memory namespace automatically?~~ **Resolved: no.** It is documented as obsolete; deletion is an operator decision.
6. ~~One `takt docs search` across types?~~ **Resolved: no.** Search is per type (`takt spec search`, `takt adr search`), consistent with the document-core design.
