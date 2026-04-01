# Codex-based Multi-Agent Orchestration MVP

## Objective

Build a Codex-based multi-agent orchestration system that provides basic functionality for agentic development using existing components:

- breaking down tasks into smaller activities manageable by agents
- dedicated agents that carry out one specific purpose only
- eliminate "context rot", provide agents across instantiations with shared memory that they can use to recall previous sessions
- parallelize work with multiple agents in parallel

This document captures the design thinking that will later be turned into a Codex prompt to build the MVP. The vision is that the MVP will be good enough to continue to develop itself from that point onwards.

---

# High-Level Architecture

## Concepts

The architecture is based on the following concepts:

- Specialized agents that carry out specific functions, e..g, tester agent only tests, developer agent only develops, etc.
- A Ralph Loop that ensures that agents will work non-stop, continuously picking up tasks until there is nothing left
- A mechanism to  manage agent activities in a deterministic manner, including dependencies, statuses, and others, that agents can use to manage, track and report on their progress (beads)
- Isolated workspaces for each agent, even in the same source code tree (via git worktrees)

## Components

### 1. Planner

Responsible for reading large feature specifications and decomposing them into beads.

Responsibilities:

- Create epic beads
- Create child beads
- Define dependencies
- Maintain the bead graph

Implemented by a planning agent.

---

### 2. Task Graph

Acts as the persistent coordination layer.

Responsibilities:

- Store tasks
- Track dependencies
- Determine which tasks are ready
- Provide shared memory for agents

Agents interact through beads commands such as:

- `bd create`
- `bd show`
- `bd ready`
- `bd dep add`
- `bd close`

---

### 3. Scheduler

Simple orchestration layer that assigns ready beads to workers.

Responsibilities:

- Poll ready tasks
- Allocate work to idle agents
- Create isolated git worktrees
- Launch worker agents

Key constraints:

- One bead per worker
- No shared working directories between "epic" beads (beads and the sub-beads still share a folder)

---

### 4. Worker Agents

Codex agents responsible for implementing beads.

Responsibilities:

- Read bead
- Locate relevant code
- Implement changes
- Hand off completed work to separate testing and documentation agents
- Commit results

Workers should not redesign the feature or modify unrelated beads, but are allowed to add new sub-beads to the current bead to notify that there is additional work that needs to be completed.



---

### 5. Agent Types

The system must support multiple specialized agent types. Each agent has a narrow responsibility and should only perform that function.

#### Planner Agent

Purpose:

- Convert feature specifications into beads
- Define dependencies
- Maintain the task graph

Responsibilities:

- Create epic beads
- Create child beads
- Update dependencies when new work is discovered

---

#### Developer Agent

Purpose:

- Implement code changes required by beads

Responsibilities:

- Read bead description
- Inspect relevant source files
- Implement code changes
- Ensure code compiles
- Commit changes

Developer agents should not:

- Redesign architecture
- Modify unrelated components

---

#### Test Agent

Purpose:

- Validate functionality implemented by developer agents

Responsibilities:

- Write or update automated tests
- Execute test suites
- Report failures
- Create new beads for defects if needed

---

#### Documentation Agent

Purpose:

- Maintain documentation consistency

Responsibilities:

- Update API documentation
- Update feature documentation
- Ensure examples remain correct

---

#### Review Agent

Purpose:

- Perform automated review of completed work

Responsibilities:

- Review code quality
- Validate that acceptance criteria are satisfied
- Confirm that tests and documentation exist

---

#### Scheduler / Orchestrator Agent

Purpose:

- Coordinate execution of agents

Responsibilities:

- Poll ready beads
- Assign work to agents
- Manage worker lifecycle
- Ensure deterministic execution

---

### 6. Repository Context

Persistent knowledge required to execute work.

Examples:

- Feature specifications
- Design documents
- API contracts
- Testing strategies

Beads reference these documents instead of embedding large context.

---

# Agent Memory Model

Agents rely on shared memory that must be both durable and consistently updated. The MVP should use a layered model rather than a single monolithic memory store.

## Memory Strategy

The recommended approach is:

- Beads as the canonical execution memory for task-level context and status
- Repository documents as the canonical source for long-form design and functional context
- AGENTS.md as the canonical source for behavioral and operating rules
- A small centralized shared memory store  for cross-bead summaries, decisions, and durable project-wide learnings

The default assumption for the MVP should be that Beads remains the primary shared memory layer, and a centralized memory store is introduced only for information that does not fit naturally into individual beads or repository documents.

## 1. Beads

Beads should contain sufficient information for agents to be able to operate independently.

Stores:

- task description
- scope
- acceptance criteria
- dependencies
- task status
- handoff notes between agents
- short execution summaries
- references to relevant files and documents

Beads should be updated by agents whenever one of the following happens:

- work starts
- scope changes within the current task
- new sub-beads are discovered
- the agent completes its part of the work
- the task is handed off to another specialized agent
- the agent is blocked

Beads should not be used to store large design documents or broad architectural discussions.

---

## 2. Repository Documents

Stores:

- design context
- architecture decisions
- long-form specifications
- API contracts
- testing strategies
- project conventions not specific to a single bead

These documents remain the durable source of truth for information that must survive beyond one task or one agent session.

---

## 3. Agent Operating Rules

Stored in:

```
AGENTS.md
```

Defines:

- how to claim work
- commit conventions
- testing requirements
- documentation expectations
- when agents must update beads
- when agents are allowed to create new beads
- handoff rules between developer, tester, documentation, and review agents

---

## 4. Optional Centralized Shared Memory

A centralized shared memory layer may be useful for information that is cross-cutting and repeatedly needed by many agents.

Examples:

- project-wide decisions
- resolved ambiguities
- important implementation conventions
- recurring pitfalls discovered during execution
- summaries of large feature specs

This should not replace Beads. It should act as a lightweight project memory index or summary layer.

For the MVP, this can be implemented very simply, for example as versioned markdown or JSON files in the repository.

Example locations:

- `docs/memory/project-decisions.md`
- `docs/memory/feature-summaries/`
- `docs/memory/known-issues.md`

---

## Memory Update Rules

To ensure shared memory stays current, the system should define explicit rules.

### Agent responsibilities

Each specialized agent must update memory relevant to its responsibility:

- Developer Agent updates bead progress, implementation notes, discovered sub-beads, and handoff notes
- Test Agent updates bead test status, failures, and defect follow-up beads
- Documentation Agent updates documentation status and references updated documents in the bead
- Review Agent updates final validation status and closes the bead when complete
- Planner Agent updates dependencies, scope, and decomposition structure

### Scheduler responsibilities

The scheduler should ensure that:

- agents always receive the bead plus linked context documents
- agents cannot start work without the latest bead state
- handoffs preserve the updated bead state for the next agent

### Handoff requirement

Every handoff between specialized agents should include a structured summary in the bead.

Minimum handoff content:

- what was completed
- what remains
- known issues or risks
- links to changed files
- links to updated docs
- recommended next agent action

---

## Availability of Shared Memory

Shared memory is available to agents by making it part of the execution contract.

Each agent invocation should include access to:

- the assigned bead
- linked repository documents
- AGENTS.md
- optional project memory files if they exist

This means the orchestrator must inject or expose this context consistently at runtime rather than expecting agents to discover it on their own.

---

## Recommended MVP Decision

For the MVP:

- use Beads as the primary shared execution memory
- store rich and durable context in repository documents
- require every agent to update bead state at handoff points
- optionally add a very small centralized shared memory directory in the repository for project-wide summaries and decisions

This keeps the design simple, transparent, versioned, and compatible with git-based workflows.

---

# Execution Model

## Scheduler Loop

1. query ready beads
2. assign work
3. spawn worker
4. monitor completion

---

## Work Lifecycle

Worker receives bead id.

Steps:

1. read bead
2. read linked documents
3. inspect code
4. implement task
5. commit implementation changes
6. mark bead ready for testing
7. hand off to Test Agent and Documentation Agent
8. Test Agent runs tests and reports results
9. Documentation Agent updates documentation
10. Review Agent validates completion and closes bead

---

# Parallel Work Strategy

Parallelization achieved through:

- dependency-aware scheduling
- git worktrees
- independent agent sessions

Constraints:

- workers cannot modify the same files simultaneously
- large beads should be avoided

---

# Failure Handling

Possible failure cases:

- agent crashes
- task incomplete
- dependency deadlocks

Recovery mechanisms to consider:

- stale worker detection
- task reassignment
- manual intervention

---

# MVP Scope

Initial MVP should implement only:

- planner (manual or assisted)
- bead creation
- scheduler
- worker execution
- git worktrees
- implementation of all agent types

Out of scope for MVP:

- advanced memory systems



---

# Notes

Use this document to refine the architecture and eventually convert it into a prompt or specification for Codex to implement the MVP orchestration system.

