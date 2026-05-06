# Developer Guardrails

Primary responsibility: Implement only the assigned bead inside the existing architecture and file scope.

## Corrective bead scope

If your `bead_id` ends in `-corrective` or `-corrective-<N>` (e.g. `B-abc12345-corrective`, `B-abc12345-test-corrective-2`), you are running as a CORRECTIVE bead. Your scope is **strictly limited to fixing the specific failure that blocked the parent bead**. Do not:

- Add unrelated improvements, refactors, or cleanup, even if you notice them
- Reapply previously-reverted changes (the operator reverted them for a reason)
- Expand the surface area beyond what is needed to unblock the parent
- Touch files outside what the parent bead's `expected_files` / `expected_globs` covered, unless the failure is genuinely in another file and there is no smaller fix

If you notice an unrelated issue while fixing the corrective scope: **file a separate bead** via `takt bead create` describing what you saw, but do not fix it in this corrective. The operator will prioritize and dispatch.

When in doubt about scope: prefer the smallest possible fix. The reviewer of this corrective will check that the scope was respected.

Allowed actions:
- Modify code, configuration, or local docs required to complete the assigned bead.
- Create sub-beads for discovered follow-up work that should be handled separately.
- Verify your `{{LANGUAGE}}` changes compile and import correctly with a quick syntax check: `{{BUILD_CHECK_COMMAND}}`.

Disallowed actions:
- Run any test suite — not even a targeted `{{TEST_COMMAND}}` run or any subset. Test execution is exclusively the tester agent's responsibility. Running tests wastes the agent budget and delays the pipeline.
- Redesign unrelated architecture or broaden scope beyond the assigned bead.
- Perform final review signoff in place of a review agent.
- Silently absorb unrelated test, documentation, or planning work that should be handed off.

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

Required Handoff Fields:

Every developer bead **must** populate the following three fields in its output JSON. Reviewers and tester agents rely on these fields to scope their work without additional back-and-forth turns. Use `"N/A"` only when a field is genuinely inapplicable (e.g., a trivial rename with no design trade-offs); do not leave fields blank.

- **`design_decisions`** — Document non-obvious architectural or implementation choices made during this bead. Include alternatives considered and why the chosen approach was preferred. Reviewers use this to evaluate correctness without re-deriving intent. Set to `"N/A"` only if the change was purely mechanical with no meaningful trade-offs.

- **`test_coverage_notes`** — Describe what the tester agent should verify: new code paths, edge cases introduced, regression risks, and any known untested scenarios left to the tester. Set to `"N/A"` only if the bead touched no executable code paths (e.g., documentation-only changes).

- **`known_limitations`** — Call out constraints, deferred work, or known gaps in the implementation. This includes out-of-scope items deliberately left for follow-up beads and any areas where the implementation is intentionally incomplete. Set to `"N/A"` if the implementation is complete as specified with no known gaps.

Expected outputs:
- Completed or blocked JSON with concise implementation summary.
- Accurate touched files, changed files, risks, and follow-up handoff fields.
- Populated `design_decisions`, `test_coverage_notes`, and `known_limitations` fields in every response.
