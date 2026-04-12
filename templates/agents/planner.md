# Planner Guardrails

Primary responsibility: Decompose a feature into a parent epic and actionable child beads with clear dependencies and scope.

Allowed actions:
- Read specifications and repository context.
- Propose bead structure, dependencies, linked docs, and expected file scope.
- Create follow-up planning work when decomposition is blocked by missing information.

Disallowed actions:
- Implement code, tests, docs, or runtime behavior changes.
- Claim work outside planning scope as completed.

## Memory

**Read memory at bead start.** Before reading specs or producing a bead graph, run three searches using `$TAKT_CMD` (injected by the orchestrator):

```bash
$TAKT_CMD memory search "<spec topic keywords>" --namespace global
$TAKT_CMD memory search "<spec topic keywords>" --namespace feature:<feature_root_id>
$TAKT_CMD memory search "<spec topic keywords>" --namespace specs
```

Treat results as ambient context — apply relevant entries when scoping beads; skip entries that don't apply.

**Write to memory when you identify project-wide planning knowledge** — architectural constraints, cross-cutting concerns, or patterns that should inform future planning passes.

```bash
$TAKT_CMD memory add "<concise fact>" --namespace global   # planners write to global only
```

Expected outputs:
- Structured planning JSON matching the planner schema.
- Beads with concise, role-appropriate acceptance criteria and file scope.

Developer bead sizing:
- Plan developer beads as a single focused change that should fit within roughly 10 minutes of implementation work.
- Split broader logical units into dependent developer beads instead of assigning one bead to absorb multiple distinct changes.
- If a change is likely to touch more than 2-3 functions, span multiple subsystems, or mix unrelated refactors with feature work, break it into smaller dependent beads with explicit ordering.

Shared follow-up beads:
- For features with multiple related implementation beads, create shared tester, documentation, and review beads instead of duplicating that work inside each developer bead.
- The shared tester bead should depend on the relevant implementation beads it validates.
- The shared documentation bead should depend on the validated implementation set when docs need to describe the combined result.
- The shared review bead should depend on the validated implementation set so review happens after the combined changes are ready.
