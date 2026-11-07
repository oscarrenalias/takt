# fleet-scheduler-visibility

## Requirement: Scheduler column in fleet summary
<!-- source: spec-1cdb1b8a -->
`takt-fleet summary` SHALL show each project's scheduler state in a `SCHEDULER` column and MUST show `-` when the project's summary has no `scheduler` key.

### Scenario: Mixed fleet
- **WHEN** one project reports `scheduler.state` `running` and another project's summary lacks `scheduler`
- **THEN** the table shows `running` and `-` respectively in the `SCHEDULER` column

## Requirement: Scheduler run history
<!-- source: spec-1cdb1b8a -->
`takt-fleet scheduler-runs list` SHALL list scheduler runs from each target project's event log, newest first, honouring `--project`, `--tag`, `--since` and `--limit`.

### Scenario: Finished and running runs across projects
- **WHEN** project A's log has a finished run and project B's log has a started run with an alive active marker
- **THEN** the output lists both, A's with its `exit_reason` and B's with status `running`, newest first

### Scenario: Legacy log without run events
- **WHEN** a project's log contains only cycle events without `run_id`
- **THEN** that project contributes no rows and the command exits 0
