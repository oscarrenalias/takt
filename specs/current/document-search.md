# document-search

## Requirement: Document index maintained by the document core
<!-- source: spec-57fe01fe -->
takt SHALL keep an index of every spec and ADR in the main checkout's memory database, chunked by section with the document's ID, type, status, title and section heading; the document core MUST mark a document stale (with its new metadata) or remove it from the index whenever it creates, changes, moves or removes that document in the main checkout, without embedding.

### Scenario: Status change updates the index row
- **WHEN** `takt spec set status planned <spec>` runs in a main checkout whose memory database exists
- **THEN** `doc_index_documents` has that spec with `status` `planned`, the new path and `content_hash` NULL
- **AND** after the next `takt spec search`, the spec has exactly one set of chunks and a non-NULL `content_hash`

### Scenario: ADR approval is searchable
- **WHEN** `takt adr approve <id>` runs in a main checkout whose memory database exists and `takt adr search` is then run on a phrase from that ADR's Decision section
- **THEN** the ADR is returned with status `approved`

### Scenario: Hook is inherited by the ADR store
- **WHEN** an `AdrStore` is created for a repository
- **THEN** its document type's `on_document_changed` is the document index hook

### Scenario: No takt state is created
- **WHEN** `takt spec create "X"` runs in a repository with `specs/` but no `.takt/`
- **THEN** the spec is created, the command exits 0 and no `.takt/` directory exists

### Scenario: Worktree edits do not touch the shared index
- **WHEN** `takt spec set status done <spec>` runs with the root inside `.takt/worktrees/<id>`
- **THEN** the main checkout's `doc_index_documents` row for that spec is unchanged

### Scenario: Hook failure does not fail the command
- **WHEN** `agent_takt.documents.index.mark_stale` is patched to raise during `takt spec set tags a,b <spec>`
- **THEN** the tags are written, the command exits 0 and stderr contains `warning: document index not updated`

## Requirement: Index reconciliation with files
<!-- source: spec-57fe01fe -->
Searching SHALL first reconcile the index with the main checkout's documents, re-indexing documents that are new, stale, or whose content hash, path or status changed, removing rows for deleted documents, and MUST NOT re-embed unchanged documents; concurrent reconciliations MUST NOT duplicate chunks.

### Scenario: Edit outside takt is picked up
- **WHEN** a spec file is edited directly on disk and `takt spec search` is run
- **THEN** the search reflects the new text and the old text no longer matches that spec

### Scenario: Move without edits is picked up
- **WHEN** a spec file is moved from `specs/drafts/` to `specs/planned/` with `git mv` (no content change) and `takt spec search` runs
- **THEN** `doc_index_documents.status` for that spec is `planned`

### Scenario: Deleted document disappears
- **WHEN** an ADR file is deleted from disk and `takt adr search --all` is run
- **THEN** no hit refers to that ADR

### Scenario: Unchanged documents are not re-embedded
- **WHEN** `takt spec search` runs twice with no file changes in between
- **THEN** the second run calls the embedding function only for the query

### Scenario: Concurrent indexing does not duplicate
- **WHEN** two threads call `index_document` for the same document at the same time
- **THEN** the document's chunk count equals the count from a single run

## Requirement: Hybrid ranked search with document identity
<!-- source: spec-57fe01fe -->
`takt spec search` and `takt adr search` SHALL rank documents by combining vector similarity and FTS5 keyword rank with Reciprocal Rank Fusion, each hit carrying ID, type, status, title, path, section and snippet, and MUST support status and tag filters.

### Scenario: Exact identifier found by keyword
- **WHEN** `takt spec search "merge=ours"` runs and exactly one spec contains that string
- **THEN** that spec is in the results and its snippet contains `merge=ours`

### Scenario: JSON output carries document identity
- **WHEN** `takt adr search "bead state" --json` runs
- **THEN** every element has `id`, `type`, `status`, `title`, `path`, `section`, `snippet` and `score`

### Scenario: Superseded ADRs hidden by default
- **WHEN** `takt adr search` runs on a phrase that only matches a superseded ADR
- **THEN** it prints `No matches.`, and with `--all` the superseded ADR is returned

### Scenario: Filters do not starve results
- **WHEN** 60 superseded ADRs and 1 approved ADR all match a query and `takt adr search` runs with default filters
- **THEN** the approved ADR is returned

### Scenario: Query with FTS syntax characters
- **WHEN** `takt spec search 'foo" OR (bar'` runs
- **THEN** it exits 0 without an FTS5 syntax error

### Scenario: Search from a feature worktree uses the main index
- **WHEN** `takt adr search` runs with the root inside `.takt/worktrees/<id>`
- **THEN** results come from the main checkout's index and files

## Requirement: Rebuild and inspection
<!-- source: spec-57fe01fe -->
takt SHALL provide `reindex` (with `--check`) for each document type, rebuilding that type's index from the main checkout's files, and `takt memory stats` MUST report per-type document, chunk and stale counts without creating index tables.

### Scenario: Reindex rebuilds
- **WHEN** `takt adr reindex` runs on a repository with 3 ADRs
- **THEN** it prints `indexed 3 adr documents` and the index holds exactly those 3 ADRs

### Scenario: Stats report the index
- **WHEN** `takt memory stats` runs after `takt spec reindex`
- **THEN** its JSON has `document_index.spec.documents` equal to the number of specs
