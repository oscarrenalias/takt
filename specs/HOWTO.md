# How to Write Specs

A spec is the input to the planner. The planner decomposes it into a graph of beads.

## Managing Specs with `takt spec`

```bash
uv run takt spec create "My feature"                 # new draft in specs/drafts/
uv run takt spec list --plain                        # list specs
uv run takt spec set status planned <spec-id>        # move after planning
uv run takt spec set status done <spec-id>           # move after merge; merges Requirements deltas
uv run takt spec validate                            # check frontmatter and deltas
```

Do not move spec files or edit managed frontmatter by hand. See `docs/documents.md`.

## Structure

```markdown
# Title

## Objective
One clear sentence describing the goal.

## Acceptance Criteria
- Testable, outcome-focused criteria (not implementation steps)
- Each criterion should be verifiable by an agent

## Scope
What is explicitly in scope and out of scope.

## Files to Add/Modify (optional)
Hints about which files will change. Helps the planner assign expected_files.

## Requirements (optional)
Behavioural deltas that update the living specs under specs/current/ when this
spec moves to done. Each entry is a level-3 heading:

  ### ADDED|MODIFIED|REMOVED: <capability> / <requirement name>

ADDED and MODIFIED entries need a SHALL/MUST sentence and at least one
#### Scenario: sub-heading with - **WHEN** and - **THEN** lines.
REMOVED entries need a Reason: line and no scenarios.

### ADDED: <capability> / <requirement name>
The system SHALL ...

#### Scenario: <name>
- **WHEN** ...
- **THEN** ...

### REMOVED: <capability> / <old requirement name>
Reason: ...
```

## Tips for Good Specs

- **One objective per spec.** Multi-objective specs produce tangled bead graphs.
- **Testable criteria only.** "The CLI prints X" is testable. "The code is clean" is not.
- **Keep it small.** A spec that maps to 3–5 developer beads is ideal. Larger specs
  risk scope creep and merge conflicts.
- **Don't prescribe implementation.** Say what the feature does, not how to build it.
  The developer agent decides the implementation.
- **Hint at file scope.** If you know the change touches `src/foo/bar.py`, say so.
  The planner uses this to avoid scheduling conflicts.

## Running the Planner

```bash
# Dry run — prints bead graph without creating beads
uv run takt plan specs/drafts/my-spec.md

# Persist beads
uv run takt plan --write specs/drafts/my-spec.md
```

## Bead Size Guidelines

A bead is too large if it:
- Touches more than 2–3 functions or multiple subsystems
- Would take a human more than a few hours
- Has acceptance criteria that require multiple distinct implementation steps

Split large beads at natural seams (e.g. data layer vs API layer, backend vs frontend).
