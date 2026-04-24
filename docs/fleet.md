# Fleet Manager

The Fleet Manager (`takt-fleet`) extends `agent-takt` to coordinate work across multiple local takt projects from a single operator terminal. It is installed as a sibling entry-point alongside the `takt` CLI.

> **v0.1.0 scope note** — All registered projects must live on the local filesystem. Cross-project spec management and remote/SSH project support are out of scope for v0.1.0.

---

## Concepts

**Registry** — a YAML file (`~/.config/agent-takt/fleet.yaml` by default, XDG-aware) that maps names and tags to project root directories. All `takt-fleet` commands operate against this registry.

**Health** — a lightweight per-project liveness check run before listing or querying. Possible values:

| Status | Meaning |
|---|---|
| `ok` | Directory exists, `.takt/config.yaml` present, `takt --version` succeeds |
| `no-takt` | Directory exists but is not a takt project (missing `.takt/config.yaml`) |
| `missing` | Registered path does not exist on disk |
| `takt-error` | Directory and config exist but `takt --version` failed or timed out |

**Fleet run** — a logged record of a `dispatch` or `run` command. Each run gets an ID in the format `FR-<8 hex chars>`. Records are written to `~/.local/share/agent-takt/fleet/runs/` and are queryable with `takt-fleet runs`.

**Filters** — most commands accept `--tag` and `--project` to scope which projects to target:
- `--tag TAG` is repeatable with AND semantics (all given tags must be present).
- `--project NAME` is repeatable with OR semantics (any listed name matches).

---

## Registry Setup

Register a project by pointing at its root directory:

```bash
takt-fleet register /path/to/project-a
takt-fleet register /path/to/project-b --name "backend" --tag api --tag prod
takt-fleet register /path/to/project-c --name "frontend" --tag ui

# List all registered projects (with health check)
takt-fleet list

# List only projects carrying the "prod" tag
takt-fleet list --tag prod

# Pipe-friendly output
takt-fleet list --plain

# Remove a project by name or path
takt-fleet unregister backend
```

The registry file format:

```yaml
version: 1
projects:
  - name: backend
    path: /path/to/project-b
    tags: [api, prod]
  - name: frontend
    path: /path/to/project-c
    tags: [ui]
```

Do not edit the file while `takt-fleet` commands are running — writes are atomic but concurrent access is not guarded.

---

## Command Reference

### `takt-fleet register`

```
takt-fleet register <path> [--name NAME] [--tag TAG ...]
```

Adds a project to the registry. If `--name` is omitted, the basename of `path` is used. Tags are free-form strings.

### `takt-fleet unregister`

```
takt-fleet unregister <path_or_name>
```

Removes a project from the registry. Does not touch the project directory.

### `takt-fleet list`

```
takt-fleet list [--tag TAG ...] [--plain]
```

Lists registered projects with a health check for each. Runs health checks sequentially. Use `--plain` for tab-separated output suitable for scripting.

### `takt-fleet dispatch`

```
takt-fleet dispatch --title TITLE --description DESC \
  [--agent developer|tester|documentation|review] \
  [--label LABEL ...] \
  [--max-parallel N] \
  [--tag TAG ...] [--project NAME ...]
```

Creates one bead in each target project concurrently. **Does not trigger execution** — follow up with `takt-fleet run` to run the scheduler across projects.

- Default agent type: `developer`
- Default concurrency: `min(projects, 4)` (override with `--max-parallel`)
- Results are printed as a per-project table and logged as a fleet run (`FR-…`).

Example:

```bash
# Fan out a bug fix bead to all "api" projects, then run
takt-fleet dispatch \
  --title "Fix null-pointer in auth middleware" \
  --description "Traced to line 42 in auth.py — add an early return on None." \
  --agent developer \
  --label hotfix \
  --tag api

takt-fleet run --tag api --runner claude
```

### `takt-fleet run`

```
takt-fleet run [--max-parallel N] [--runner codex|claude] \
  [--project-max-workers N] \
  [--tag TAG ...] [--project NAME ...]
```

Calls `uv run takt run` in each target project concurrently. Each project-level run is a subprocess call; its stdout/stderr are captured and summarised after all projects finish.

- `--runner` is forwarded as `--runner` to each `takt run` subprocess.
- `--project-max-workers` is forwarded as `--max-workers` to each `takt run` subprocess.
- Results are printed as a per-project table and logged as a fleet run (`FR-…`).

### `takt-fleet summary`

```
takt-fleet summary [--json] [--plain] [--tag TAG ...] [--project NAME ...]
```

Prints an aggregated bead-count table across target projects. Columns: `PROJECT`, `DONE`, `READY`, `IN_PROGRESS`, `BLOCKED`, `HANDED_OFF`, `HEALTH`.

Use `--json` for machine-readable output, `--plain` for tab-separated output.

### `takt-fleet watch`

```
takt-fleet watch [--since DURATION] [--tag TAG ...] [--project NAME ...]
```

Tails `.takt/logs/events.jsonl` in each target project and prints a merged live stream prefixed with the project name. Ctrl-C stops the watch.

- `--since DURATION` replays events from the given window before streaming live (e.g. `5m`, `1h`, `2d`). Without `--since`, starts from the current end of the file.
- Duration units: `s` (seconds), `m` (minutes), `h` (hours), `d` (days).
- A warning is printed when the requested window is older than the earliest event in the log.

Example output:

```
[backend]  2026-04-24T12:01:00Z  started             Worker started
[backend]  2026-04-24T12:01:45Z  completed           Bead B-abc12def done
[frontend] 2026-04-24T12:01:50Z  started             Worker started
```

### `takt-fleet runs list`

```
takt-fleet runs list [--limit N] [--since DURATION] \
  [--status success|error|partial|in_progress] \
  [--command dispatch|run] \
  [--plain]
```

Lists recent fleet run records, most recent first. Default limit: 20.

Run status values:

| Status | Meaning |
|---|---|
| `in_progress` | Run has not yet finished (`finished_at` is null) |
| `success` | All projects succeeded |
| `error` | All projects failed |
| `partial` | Some projects succeeded, some failed |

### `takt-fleet runs show`

```
takt-fleet runs show <run_id> [--json]
```

Shows details for a fleet run. Run IDs can be shortened to an unambiguous prefix (e.g. `FR-a1b2`).

- If the run is still in progress, the command tails the live run log and prints each project result as it arrives.
- If the run is finished, a full breakdown (per-project status, bead IDs for dispatches, duration, aggregate) is printed.
- Use `--json` to dump the raw run log record without live-tailing.

---

## Operator Workflow

### Daily driver pattern

```bash
# See the health and bead counts across all projects
takt-fleet summary

# Fan out a task to all projects tagged "backend", then run
takt-fleet dispatch --title "…" --description "…" --tag backend
takt-fleet run --tag backend --runner claude --project-max-workers 4

# Watch progress live
takt-fleet watch --tag backend

# Review results after completion
takt-fleet runs list --limit 5
takt-fleet runs show FR-<id>
```

### Targeting a subset

```bash
# Run only specific projects by name
takt-fleet run --project "project-a" --project "project-c"

# Combine tags and names (tags AND-filter; names OR-filter)
takt-fleet dispatch --title "…" --description "…" --tag prod --project "project-a"
```

### Scripting

```bash
# Machine-readable summary
takt-fleet summary --json | jq '.projects[] | select(.health != "ok")'

# Pipe-friendly run log
takt-fleet runs list --plain | awk -F'\t' '$8 == "error" {print $1}'
```

---

## Run Log

Fleet runs are persisted to `~/.local/share/agent-takt/fleet/runs/` (XDG-aware; override with `XDG_DATA_HOME`). Each file is named `<run_id>.json` and is written atomically.

Run records survive process crashes — if the fleet process is killed mid-run, the existing partial record remains on disk with `crashed: true` and whatever project results had been written before the crash. The next `runs show <id>` will display the partial results.

Run files from newer versions of `takt-fleet` that the current installation does not understand are silently skipped with a logged warning.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `XDG_CONFIG_HOME` | `~/.config` | Registry location: `$XDG_CONFIG_HOME/agent-takt/fleet.yaml` |
| `XDG_DATA_HOME` | `~/.local/share` | Run log location: `$XDG_DATA_HOME/agent-takt/fleet/runs/` |

---

## Out of Scope (v0.1.0)

- Cross-project spec planning or shared bead graphs across multiple repositories.
- Remote or SSH-accessed projects.
- Push-based event streaming (watch uses polling at 1-second intervals).
- Automatic execution after `dispatch` — operators must run `takt-fleet run` explicitly.
