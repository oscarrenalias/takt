# Defect Guardrails

Primary responsibility: Fix the specific bug described in the bead, add a regression test, and verify the fix with focused tests and a build sanity step. Do not exceed this scope.

## Scope

A defect bead is operator-filed for a bug discovered **after** the originating feature has merged to main. It is standalone — there is no parent bead. Fix only the issue named in the bead description. If you discover an unrelated problem while working, file a separate bead via `takt bead create` and do not fix it here.

A defect bead is **not** the same as a corrective bead:
- **Corrective** — scheduler-created, always has a parent bead, retries a transient failure of the parent's work.
- **Defect** — operator-filed, standalone, fixes a real bug that escaped to main.

## Permit: Focused test runs

You **may** run tests scoped to the affected module or file. Defer command mechanics (runner, flags, discovery) to `templates/skills/capability/test-execution/SKILL.md`. Examples:

```bash
pytest tests/test_affected_module.py -v
npm test -- tests/affected.test.ts
cargo test affected_module
```

Run only the narrowest scope that verifies the fix. Do not broaden to unrelated test files.

## Permit: Build sanity

You **may** run a build sanity step using heuristic detection:

| File found at worktree root | Action |
|---|---|
| `package.json` with a `build` script | `npm run build` (or `yarn build` / `pnpm build` as appropriate) |
| `pyproject.toml` | No build step needed; `python -m py_compile <changed_file>` is sufficient |
| `Cargo.toml` | `cargo check` |
| None of the above | Note `build_verification: skipped — <rationale>` in the `design_decisions` output field |

When no build target is detected, the agent **must** note the outcome in `design_decisions` (e.g. `"build_verification: skipped — no build target detected"`). Do not silently omit the step — the omission must be auditable.

## Mandate: Regression test

You **must** add or update an automated test that:

1. **Fails** without the fix (demonstrate the bug is real and reproducible).
2. **Passes** with the fix (demonstrate the bug is resolved).

If the bug is genuinely untestable by automated means (e.g. pixel-level CSS rendering, hardware-specific timing, third-party service absence), you **must** explicitly document this in the `test_coverage_notes` field of the structured output (see Structured Output Fields below). Do not silently omit the test.

## Forbid: Full-suite runs

**Do not run the full test suite.** Full-suite verification belongs to the `-review` followup or to the `takt merge` test gate. Running the full suite wastes agent budget, often exceeds the agent timeout, and delays the pipeline. Run only targeted, focused commands scoped to the affected module.

## Forbid: Scope creep

**Strictly limit changes to what is needed to fix the described bug.** Do not:

- Add unrelated improvements, refactors, or cleanup, even if you notice them.
- Touch files outside what is needed for the specific fix, unless the bug genuinely lives in another file and there is no smaller fix.
- Reapply previously-reverted changes.
- Expand the surface area beyond the defect's stated scope.

If you notice an unrelated issue: **file a separate bead** via `takt bead create` describing what you saw. Do not fix it here.

When in doubt about scope: prefer the smallest possible fix. The reviewer checks that scope was respected.

## Forbid: Review signoff

Do not perform review signoff. The scheduler auto-creates a `-review` followup when this defect bead completes. Let the review agent do its job.

## Memory

**Read memory at bead start.** Before touching any code, run three searches using `$TAKT_CMD` (injected by the orchestrator):

```bash
$TAKT_CMD memory search "<bead topic keywords>" --namespace global
$TAKT_CMD memory search "<bead topic keywords>" --namespace feature:<feature_root_id>
$TAKT_CMD memory search "<bead topic keywords>" --namespace specs
```

Treat results as ambient context — apply relevant entries; skip entries that don't apply.

**Write to memory when you discover reusable project knowledge** — something that would have changed your approach if you had known it upfront, and is not already in CLAUDE.md or the guardrails.

```bash
$TAKT_CMD memory add "<concise fact>" --namespace global               # project-wide knowledge
$TAKT_CMD memory add "<discovery>" --namespace feature:<feature_root_id>  # feature-scoped
```

## Structured Output Fields

Every defect bead **must** populate the following fields. Reviewers rely on them to scope their work without additional back-and-forth turns. Use `"N/A"` only when a field is genuinely inapplicable; do not leave fields blank.

- **`design_decisions`** — Non-obvious implementation choices: root cause identified, alternatives considered, why the chosen fix was preferred. Also include the build verification outcome here (e.g. `"build: python -m py_compile — OK"` or `"build_verification: skipped — no build target detected"`). Reviewers use this field to evaluate correctness without re-deriving intent. Set to `"N/A"` only for purely mechanical changes with no meaningful trade-offs.

- **`test_coverage_notes`** — Describe the regression test added: what it exercises, what the pre-fix failure looked like, and any edge cases left to the reviewer. **If the bug is genuinely untestable by automated means**, explain why here rather than silently omitting a test. The reviewer checks this field to verify that automated coverage exists or that the omission is justified.

- **`known_limitations`** — Constraints, deferred work, or known gaps. Includes out-of-scope issues deliberately left for follow-up beads and any areas where the fix is intentionally incomplete. Set to `"N/A"` if the fix is complete as specified.

Expected outputs:
- Completed or blocked JSON with concise fix summary.
- Accurate touched files, changed files, risks, and follow-up handoff fields.
- Populated `design_decisions` (including build verification outcome), `test_coverage_notes`, and `known_limitations` fields in every response.
