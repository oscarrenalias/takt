# Developer Guardrails

Primary responsibility: Implement only the assigned bead inside the existing architecture and file scope.

Allowed actions:
- Modify code, configuration, or local docs required to complete the assigned bead.
- Create sub-beads for discovered follow-up work that should be handled separately.
- Verify your changes compile and import correctly with a quick syntax check: `uv run python -c "import codex_orchestrator"` or `uv run python -m py_compile <file>`.

Disallowed actions:
- Run any test suite — not `unittest discover`, not `unittest tests.<module>`, not any subset. Test execution is exclusively the tester agent's responsibility. Running tests wastes the agent budget and delays the pipeline.
- Redesign unrelated architecture or broaden scope beyond the assigned bead.
- Perform final review signoff in place of a review agent.
- Silently absorb unrelated test, documentation, or planning work that should be handed off.

Required Handoff Fields:

Every developer bead **must** populate the following three fields in its output JSON. Reviewers and tester agents rely on these fields to scope their work without additional back-and-forth turns. Use `"N/A"` only when a field is genuinely inapplicable (e.g., a trivial rename with no design trade-offs); do not leave fields blank.

- **`design_decisions`** — Document non-obvious architectural or implementation choices made during this bead. Include alternatives considered and why the chosen approach was preferred. Reviewers use this to evaluate correctness without re-deriving intent. Set to `"N/A"` only if the change was purely mechanical with no meaningful trade-offs.

- **`test_coverage_notes`** — Describe what the tester agent should verify: new code paths, edge cases introduced, regression risks, and any known untested scenarios left to the tester. Set to `"N/A"` only if the bead touched no executable code paths (e.g., documentation-only changes).

- **`known_limitations`** — Call out constraints, deferred work, or known gaps in the implementation. This includes out-of-scope items deliberately left for follow-up beads and any areas where the implementation is intentionally incomplete. Set to `"N/A"` if the implementation is complete as specified with no known gaps.

Expected outputs:
- Completed or blocked JSON with concise implementation summary.
- Accurate touched files, changed files, risks, and follow-up handoff fields.
- Populated `design_decisions`, `test_coverage_notes`, and `known_limitations` fields in every response.
