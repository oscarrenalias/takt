# Document Core

The document core is a generic, file-based store for lifecycle-managed markdown documents. Specs and ADRs are both built on it. Each document is a markdown file with YAML frontmatter, stored in a folder named after its status.

Code lives in `src/agent_takt/documents/`:

| Module | Contents |
|---|---|
| `types.py` | `DocumentType`, `Document`, `ValidationError`, `noop_on_document_changed` |
| `store.py` | `DocumentStore` (read and write API), `DocumentNotFound`, `AmbiguousDocument` |
| `frontmatter.py` | Frontmatter parsing and the two writer styles (`SPEC_STYLE`, `ADR_STYLE`) |
| `spec_type.py` | `SPEC_TYPE` and its hooks, plus requirement-delta parsing and merging |
| `adr_type.py` | `ADR_TYPE` and its hooks |
| `registry.py` | `DOCUMENT_TYPES`, `get_type(name)`, `get_store(root, name)` |

CLI front ends: `takt spec` (`cli/commands/spec.py`) and `takt adr` (`cli/commands/adr.py`, a thin wrapper over the store).

## The two document types

| | `spec` | `adr` |
|---|---|---|
| Root directory | `specs/` | `adr/` |
| ID format | `spec-<8 hex>` | `ADR-<8 hex>` |
| Filename | `{slug}.md` | `adr-{hex}-{slug}.md` |
| Statuses and folders | `draft` → `drafts/`, `planned` → `planned/`, `done` → `done/` | `draft` → `drafts/`, `approved` → `approved/`, `superseded` → `superseded/`, `rejected` → `rejected/` |
| Custom statuses | Allowed (`^[a-zA-Z0-9-]+$`, folder = status name, e.g. `cancelled`) | Not allowed |
| Reserved status | `current` (holds the living capability specs; never a status) | none |
| Transitions | Any status to any other | `draft→approved`, `draft→rejected`, `approved→superseded` only |
| Resolution | ID prefix or filename substring | ID prefix |
| Frontmatter style | Line-preserving (`SPEC_STYLE`) | Re-serialised (`ADR_STYLE`) |
| Auto-commit | No | Yes |

### Spec hooks

- `on_create`: uses `specs/spec-template.md` as the body (with `{name}` substituted) when that file exists. Otherwise the built-in template applies.
- `before_transition`: when a spec moves to `done`, merges its `## Requirements` deltas into `specs/current/<capability>.md`. Invalid deltas raise `ValueError` and nothing is changed. A spec already `done` is not re-merged.
- `validate_document`: checks `id` (missing is an error, duplicate is an error, a non-`spec-<8 hex>` format is a warning), `name` (warning), that frontmatter `status` matches the folder, and the requirement-delta syntax and consistency against `specs/current/`. A legacy spec without frontmatter is a warning pointing at `takt spec migrate`.

### ADR hooks

- `on_create`: fills `created_at` and `authors`.
- `before_transition`: blocks approval when required body sections are missing or empty.
- `after_transition`: sets `accepted_at` on approval and `superseded_at` on supersession.
- `validate_document`: checks frontmatter and body sections; see [adr.md](adr.md).

## Resolution

`DocumentStore.resolve(query)` maps a user-supplied string to exactly one document, in this order:

1. Case-insensitive exact ID match.
2. Case-insensitive ID prefix match. For `id_prefix_or_filename` types, the type's `id_prefix` is also tried in front of the query, so `a3f19c2b` finds `spec-a3f19c2b`.
3. For `id_prefix_or_filename` types only: filename-stem substring match.

Zero matches raise `DocumentNotFound`. More than one match at any step raises `AmbiguousDocument`, whose `matches` attribute lists every candidate ID. `store.get(id)` is the exact-ID variant.

## References in beads and prompts

Beads link to documents with `spec:<id>` and `adr:<id>` entries in `linked_docs` (`src/agent_takt/doc_refs.py`: `parse_ref`, `resolve_ref`, `resolve_entry`, `normalize_linked_docs`). Legacy spec/ADR path entries are converted to references and deduplicated in order; other entries are left as they are.

At prompt time a reference resolves to the copy in the bead's worktree when present, otherwise to the main checkout (marked "from main checkout" in the "Linked documents" block). Worker prompts also include a lookup hint: `takt spec show <id>` / `takt adr show <id>` read the worktree copy, while `takt spec search` / `takt adr search` query the main checkout's index.

Approved ADRs are pushed into prompts: an index of approved ADRs plus the body of up to 3 linked approved ADRs. Recovery beads get neither. See [adr.md](adr.md#agent-context-and-reviewer-enforcement) for the reviewer enforcement rule.

## Legacy documents

A file without valid frontmatter, or with unparseable frontmatter, never makes `load_all()` raise. It is returned as a `Document` with `legacy=True` and status `legacy`. Parse errors are kept in `store.errors` (keyed by path) and surface from `validate()` as errors. Write operations (`set_field`, `set_status`, `apply`) reject legacy documents. For specs, `takt spec migrate <spec>` adds frontmatter.

## Validation

`store.validate(doc_id=None)` validates one document or all of them and returns a list of `ValidationError(path, message, severity)`, where `severity` is `error` or `warning`. Malformed files become errors. Legacy documents are skipped by the type's hook. An exception inside a type's `validate_document` hook is reported as an error for that file rather than stopping the run. `takt spec validate [spec]` and `takt adr validate` print these findings and exit non-zero when any error is present.

## Commit behaviour

Commits are controlled per type by `DocumentType.auto_commit`:

- ADRs (`auto_commit=True`): every create, field change, status change, removal and `apply()` runs `git add -A -- <root dir>` followed by one `git commit` with the message `ADR lifecycle: <what happened>`.
- Specs (`auto_commit=False`): the store never commits. Commit spec changes yourself, together with related bead or code changes.

A failed commit (not a git repo, nothing to commit, hook failure, timeout) is non-fatal: the file changes stay on disk and the operation still succeeds.

## Frontmatter styles

`DocumentType.frontmatter_style` decides how files are written.

- `SPEC_STYLE` is line-preserving. `update()` rewrites only the lines that belong to a changed key, so comments, key order, quoting and line endings elsewhere in the frontmatter survive. New keys are appended.
- `ADR_STYLE` parses the frontmatter and re-serialises the whole mapping with `yaml.dump` (insertion order kept, no key sorting), followed by a blank line and the body.

Both styles implement `render(frontmatter, body)` and `update(text, updates, path)`. A new style subclasses `FrontmatterStyle` in `frontmatter.py`.

## Store write API

| Method | Behaviour |
|---|---|
| `create(title, **fields)` | Generates an ID, builds frontmatter from `default_frontmatter` plus `fields`, renders the template body, runs `on_create`, writes into the initial status folder. Raises `FileExistsError` on a filename collision. |
| `set_field(id, field, value)` | Updates one frontmatter field. A no-op when the value is unchanged. `status` is rejected; use `set_status`. |
| `set_status(id, status)` | Validates the status, checks the transition table, runs `before_transition`, rewrites frontmatter, moves the file to the status folder, runs `after_transition`. A no-op when the status is unchanged. |
| `remove(id)` | Deletes the file. |
| `apply(changes)` | Atomic multi-document change; see below. |
| `validate(id=None)` | See [Validation](#validation). |

## `apply()`

```python
store.apply([
    ("spec-a3f19c2b", "planned", {"feature_root_id": "B-1234abcd"}),
    ("spec-b7c8d9e0", None, {"priority": "high"}),
])
```

Each change is `(id, new_status_or_None, field_updates_or_None)`. The `fields` dict may not contain `status`; use the second element. `apply()` is all-or-nothing:

1. **Validate everything first**: ID resolution, duplicate IDs in the batch, legacy documents, status and transition rules, `before_transition` hooks, and path collisions. Nothing is written if any check fails.
2. **Write with rollback**: new files are written, then originals removed, then `after_transition` hooks run. Any error restores every original byte-for-byte, deletes newly created files and directories, and re-raises.
3. **Notify and commit once**: after success, `on_document_changed` fires once per affected document and a single commit covers the batch (for types with `auto_commit`). Documents whose change is a no-op are returned but not notified.

## `on_document_changed`

`DocumentType.on_document_changed(doc_type, doc_id, path)` is a notification callback invoked by the store after a document is created, a field or status changes, a document is removed, or an `apply()` batch succeeds. For removals the `path` no longer exists. The default is `noop_on_document_changed`, which does nothing. It exists so that callers (for example a memory or index sync) can react to changes without the store depending on them. Set it by passing `on_document_changed=your_callback` when constructing a `DocumentType`.

## Adding a document type

1. Create `src/agent_takt/documents/<name>_type.py` and define a `DocumentType`:
   - `name`, `id_prefix`, `root_dir`, `filename_pattern` (or a `filename_builder`);
   - `statuses` as a status-to-folder mapping; the first entry is the initial status;
   - `transitions` (`None` for unrestricted) and, if needed, `allow_custom_statuses` and `reserved_statuses`;
   - `resolution`: `id_prefix` or `id_prefix_or_filename`;
   - `frontmatter_style`, `default_frontmatter`, `template`;
   - `auto_commit`.
2. Add optional hooks as plain functions, with these signatures:
   - `on_create(store, doc)`
   - `before_transition(store, doc, new_status)`: raise `ValueError` to reject.
   - `after_transition(store, doc, old_status)`
   - `validate_document(store, doc) -> list[ValidationError]`
   - `on_document_changed(doc_type, doc_id, path)`
3. Register it in `DOCUMENT_TYPES` in `registry.py`. `get_store(root, "<name>")` then returns a ready `DocumentStore`.
4. If it needs a CLI, add a command module under `cli/commands/` and a parser section in `cli/parser.py`.
5. Add tests under `tests/` covering creation, transitions, resolution, validation and rollback.

## Migrating an existing project

Projects initialised before the document core carry a standalone copy of `spec.py` and the old spec skill. To move to `takt spec`:

1. Install the new takt version.
2. Run `takt upgrade`. It disables the unmodified installed `spec.py` copies by renaming them to `spec.py.disabled`, rewrites the spec skills to call `takt spec`, and adds the ADR skills. Locally modified copies are skipped and reported.
3. Replace any instructions in your own `CLAUDE.md` or `AGENTS.md` that call `spec.py` with `takt spec` (for example `takt spec set status planned <spec-id>`). `takt upgrade` lists the files that still mention `spec.py`.
4. Run `takt spec validate` and fix the reported errors. Specs without frontmatter show up as warnings; fix them with `takt spec migrate <spec>`.

Until `takt upgrade` runs, the old `spec.py` keeps working because it is standalone.

## Document index and search

Specs and ADRs are searchable with a hybrid keyword + semantic index. It replaces the old plan-time ingestion of specs into the `specs` memory namespace.

### Location and schema

The index lives in the shared memory database, always the **main checkout's** `.takt/memory/memory.db` (or the path in `AGENT_MEMORY_DB` when set). It uses four dedicated tables and never touches the memory `entries`/`vectors` tables:

| Table | Contents |
|---|---|
| `doc_index_documents` | One row per document: `doc_type`, `doc_id`, `path`, `status`, `title`, `tags`, `content_hash`, `indexed_at`. A NULL `content_hash` means the document is stale. |
| `doc_index_chunks` | Text chunks: `doc_type`, `doc_id`, `section`, `ord`, `text`. |
| `doc_index_vectors` | 384-dimension embeddings (sqlite-vec `vec0`), one per chunk. |
| `doc_index_fts` | FTS5 keyword index over chunk `text`, `section` and `title`. |

The database is local-only (`.takt/memory/` is gitignored). It is created on demand by `takt spec|adr search` and `reindex`.

### Chunking

Frontmatter is never indexed. Each document yields:

- a `summary` chunk with the title and the frontmatter `description`;
- one chunk per `##` section, named after the heading;
- one chunk per `###` subsection, named `<## heading> / <### heading>`;
- a `(preamble)` chunk for text before the first `##`.

Headings inside fenced code blocks are ignored. Sections larger than the memory chunk limit are split at sentence boundaries. The title is the frontmatter `title` or `name`, else the first `# ` heading, else the filename stem.

### Keeping the index fresh: hook vs sync

- **Hook (cheap).** `on_document_changed` fires on create, field or status change, and removal. It only marks the document stale (or removes its rows) in an existing index database on the main checkout. It never embeds, loads the model, or creates `.takt/` or the database. Failures print one stderr warning and never affect the document write.
- **Sync (does the work).** `takt spec|adr search` runs a sync before querying, and `reindex` runs it explicitly. Sync (re-)indexes documents with no row, a stale or differing content hash, or a different path or status, and drops rows for documents no longer on disk. Unchanged documents are never re-embedded.

### Worktree behaviour

The index is anchored on the main checkout, not on a feature worktree or isolated execution root. Documents are read from the main checkout, so a spec edited only inside a feature worktree is not indexed until it reaches main. The hook does nothing when the document's root is not the main checkout.

### Ranking

Search runs a keyword leg (FTS5 bm25) and a vector leg, then fuses them with reciprocal-rank fusion (`1 / (60 + rank)` summed across legs). By default one hit (the best chunk) is returned per document; `--chunks` returns individual chunks.

### Commands

```bash
uv run takt spec search "query" [--status S] [--tag T] [--limit N] [--chunks] [--plain|--json]
uv run takt adr search "query" [--status S] [--tag T] [--limit N] [--chunks] [--all] [--plain|--json]
uv run takt spec reindex [--check]
uv run takt adr reindex [--check]
uv run takt memory stats                     # includes a document_index block
```

- `--status` is repeatable (OR); `--tag` is repeatable (AND).
- `adr search` hides superseded and rejected ADRs unless you pass `--status` or `--all`.
- `--plain` prints tab-separated `id, status, section, score, title`; `--json` includes `path` (relative to the main checkout), `section`, `snippet` and `score`.
- `reindex` rebuilds the index for one type from scratch. `reindex --check` reports `would add N, update N, remove N` without writing.
- `memory stats` reports per-type `documents`, `chunks` and `stale` counts.

### Troubleshooting

- **No matches for a document you just created.** Run `takt spec reindex` (or `adr reindex`), or check `takt memory stats` for a non-zero `stale` count.
- **First search is slow.** The first embedding downloads the ONNX model into `common.memory_cache_dir`.
- **`error: no takt project`.** Run from a directory under a project with `.takt/`.
- **Results look out of date after a worktree edit.** The index follows the main checkout; merge first or run `reindex` on main.
- **Corrupt or missing index.** Run `reindex` for each type; it recreates the tables.

### Obsolete `specs` memory namespace

`takt plan --write` no longer ingests specs into memory, and agents should use `takt spec search` and `takt adr search` rather than `takt memory search --namespace specs`. Entries written to the `specs` namespace by earlier versions stay in the database but are obsolete and no longer updated. Inspect them with:

```bash
uv run takt memory namespace show specs
```

Deleting them is an operator decision; takt does not remove them automatically.

## `takt spec` quick reference

```bash
uv run takt spec init                                  # create specs/ lifecycle folders and template
uv run takt spec create "Title"                        # new draft spec
uv run takt spec list [--status S] [--tag T] [--priority P] [--plain|--json]
uv run takt spec show <spec> [--full|--body-only|--field PATH|--json]
uv run takt spec set status <value> <spec>             # draft|planned|done|<custom>
uv run takt spec set feature-root|tags|priority|description <value> <spec>
uv run takt spec migrate <spec>                        # add frontmatter to a legacy spec
uv run takt spec remove <spec> [--force]
uv run takt spec validate [<spec>]
```

`<spec>` is an ID, an ID prefix or a filename substring. Moving a spec to `done` merges its `## Requirements` deltas into `specs/current/`.
