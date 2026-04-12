---
name: memory
description: Read and update shared institutional memory across beads.
---

# memory

Shared institutional memory persists knowledge across beads, features, and sessions using a local sqlite-vec database at `.takt/memory/memory.db`.

## Namespaces

Memory is partitioned into three namespaces:

- `global` — Project-wide conventions, pitfalls, and discoveries applicable to any agent or feature.
- `feature:<feature_root_id>` — Knowledge scoped to a specific feature tree (e.g. `feature:B-abc12def`).
- `specs` — Ingested spec content; automatically populated during planning.

## Retrieval

```bash
# Search across all namespaces (merged, ranked by relevance)
takt memory search "<query>"

# Search a specific namespace
takt memory search "<query>" --namespace global
takt memory search "<query>" --namespace feature:<feature_root_id>
takt memory search "<query>" --namespace specs

# Adjust result count and similarity threshold
takt memory search "<query>" --limit 10 --threshold 0.4
```

## Writing Entries

```bash
# Add a global entry
takt memory add "<fact or observation>" --namespace global

# Add a feature-scoped entry
takt memory add "<discovery>" --namespace feature:<feature_root_id>
```

## Ingesting Files

```bash
# Ingest a markdown or text file into memory
takt memory ingest path/to/file.md --namespace global

# Migrate legacy docs/memory/*.md files into the sqlite-vec store
takt memory ingest --migrate
```

## Administration

```bash
takt memory init                # Create the database and download the embedding model
takt memory stats               # Show entry counts by namespace
takt memory delete <entry_id>   # Remove a specific entry
```

## Agent Access Control

| Agent type    | Read | Write                          |
|---------------|------|--------------------------------|
| Planner       | yes  | `global` namespace only        |
| Developer     | yes  | `global` and `feature`         |
| Tester        | yes  | `global` and `feature`         |
| Documentation | yes  | **read-only — do not write**   |
| Review        | yes  | **read-only — do not write**   |
