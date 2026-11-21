# fleet-boundaries

## Requirement: Documented project file contract
<!-- source: spec-a2cb3aed -->
takt SHALL document, in `docs/project-files.md`, every `.takt/` file family that ADR-1897d241 allows the fleet to read, and a test MUST fail when a top-level bead JSON key is missing from the bead section of that document.

### Scenario: Undocumented bead field fails the drift test
- **WHEN** the serialised bead contains a top-level key that the bead section of `docs/project-files.md` does not mention
- **THEN** the drift test fails and its message names that key

### Scenario: Current bead fields are all documented
- **WHEN** the drift test runs against the current `Bead` model and the shipped document
- **THEN** it passes

## Requirement: Indirect forbidden imports are rejected
<!-- source: spec-a2cb3aed -->
The fleet boundary test SHALL fail when fleet code imports an `agent_takt` module that directly or indirectly imports a forbidden module, and MUST report the full import chain.

### Scenario: Indirect import through an allowed-looking module
- **WHEN** a synthetic fleet module imports a synthetic helper module that imports the forbidden storage module
- **THEN** the checker reports a violation whose message contains the chain from the fleet file through the helper to the forbidden module

### Scenario: Current fleet code passes
- **WHEN** the transitive check runs over the real `src/agent_takt_fleet` tree
- **THEN** it reports no violations
