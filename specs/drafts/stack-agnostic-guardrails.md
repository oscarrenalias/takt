---
name: Stack-Agnostic Guardrails and Skills
id: spec-42b24901
description: null
dependencies: null
priority: null
complexity: null
status: draft
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---
# Stack-Agnostic Guardrails and Skills

## Objective

The orchestrator's guardrail templates and skill catalog currently hardcode Python/uv conventions (`uv run python -m unittest`, `uv run python -c "import codex_orchestrator"`, `uv run python -m py_compile`). This prevents the orchestrator from being used on non-Python projects without editing core framework files. By extracting the language-specific instructions into two overridable project skills — `capability/run-tests` and `capability/verify-syntax` — the guardrails become stack-agnostic and projects can drop in their own skill files for TypeScript, Java, or any other stack.

---

## Problems to Fix

1. **`templates/agents/tester.md`** hardcodes `uv run python -m unittest tests.<module_name> -v` in both Allowed actions and Disallowed actions. A TypeScript project's tester agent would get the wrong instructions.
2. **`templates/agents/developer.md`** hardcodes `uv run python -c "import codex_orchestrator"` and `uv run python -m py_compile <file>` as the syntax verification command.
3. **`templates/agents/merge-conflict.md`** hardcodes the same Python syntax check.
4. **`.agents/skills/capability/test-execution/SKILL.md`** (Codex skill catalog) hardcodes `uv run python -m unittest` throughout. The equivalent Claude skill does not exist yet.
5. No `capability/verify-syntax` skill exists in either catalog.

---

## Changes

### 1. New default skill: `capability/run-tests`

Create default implementations in both catalogs:

**`.agents/skills/capability/run-tests/SKILL.md`** (Codex):
```markdown
---
name: run-tests
description: How to run targeted tests for this project.
---

# run-tests

Run targeted tests for the changed files. Do not run the full test suite.

## Command

uv run python -m unittest tests.<module_name> -v

## Rules
- Always target the narrowest module that covers the changed files.
- Never use `discover` — it runs the full suite and wastes agent budget.
- Run synchronously — never use run_in_background.
```

**`.claude/skills/capability/run-tests/SKILL.md`** (Claude Code — same content):
Same as above.

### 2. New default skill: `capability/verify-syntax`

**`.agents/skills/capability/verify-syntax/SKILL.md`** (Codex):
```markdown
---
name: verify-syntax
description: How to verify code compiles or parses correctly for this project.
---

# verify-syntax

After making changes, run a quick syntax/import check to catch obvious errors before handing off.

## Command

uv run python -c "import codex_orchestrator"
# or for individual files:
uv run python -m py_compile <file>

## Rules
- This is a sanity check only — do not run tests.
- Run synchronously.
```

**`.claude/skills/capability/verify-syntax/SKILL.md`** (Claude Code — same content):
Same as above.

### 3. Update `templates/agents/tester.md`

Replace the hardcoded command references with a reference to the `run-tests` skill:

- In Allowed actions: replace `Use \`uv run python -m unittest tests.<module_name> -v\` to target individual test files` with `Consult the \`capability/run-tests\` skill for the correct test command for this project.`
- In Disallowed actions: replace `Always target the specific module: \`uv run python -m unittest tests.<module_name> -v\`` with `Always target specific modules as described in the \`capability/run-tests\` skill.`

### 4. Update `templates/agents/developer.md`

Replace the hardcoded syntax check in Allowed actions:

- Replace `\`uv run python -c "import codex_orchestrator"\` or \`uv run python -m py_compile <file>\`` with `the command described in the \`capability/verify-syntax\` skill`

### 5. Update `templates/agents/merge-conflict.md`

Same replacement as developer.md:

- Replace the hardcoded syntax check in Allowed actions with a reference to `capability/verify-syntax`.
- Replace the hardcoded `unittest` references in Disallowed actions with references to the `run-tests` skill.

### 6. Update `.agents/skills/capability/test-execution/SKILL.md`

Replace hardcoded `uv run python -m unittest` references with a note that the actual command is defined in the `run-tests` skill for this project, and the `test-execution` skill governs the strategy (targeting, scope, rules) rather than the specific command.

---

## Files to Modify

| File | Change |
|---|---|
| `templates/agents/tester.md` | Replace hardcoded Python/unittest commands with references to `capability/run-tests` skill |
| `templates/agents/developer.md` | Replace hardcoded Python syntax check with reference to `capability/verify-syntax` skill |
| `templates/agents/merge-conflict.md` | Replace hardcoded Python syntax check and unittest references |
| `.agents/skills/capability/test-execution/SKILL.md` | Decouple command from strategy; defer command to `run-tests` skill |
| `.agents/skills/capability/run-tests/SKILL.md` | New file — default Python/uv implementation |
| `.agents/skills/capability/verify-syntax/SKILL.md` | New file — default Python/uv implementation |
| `.claude/skills/capability/run-tests/SKILL.md` | New file — default Python/uv implementation |
| `.claude/skills/capability/verify-syntax/SKILL.md` | New file — default Python/uv implementation |

---

## Acceptance Criteria

- `templates/agents/tester.md` contains no hardcoded `uv run python` or `unittest` command strings
- `templates/agents/developer.md` contains no hardcoded `uv run python` or `py_compile` command strings
- `templates/agents/merge-conflict.md` contains no hardcoded `uv run python` or `unittest` command strings
- Both `capability/run-tests` and `capability/verify-syntax` skills exist in both `.agents/skills/` and `.claude/skills/`
- The default skill content correctly describes Python/uv commands (so existing behaviour is preserved for this project)
- A project using TypeScript could replace `.claude/skills/capability/run-tests/SKILL.md` with `npm test -- <file>` instructions and the tester agent would follow them without any other changes

---

## Pending Decisions

### 1. Skill loading for tester agent
The tester agent currently loads `capability/test-execution` (Codex) via `AGENT_SKILL_ALLOWLIST` in `skills.py`. Should `run-tests` replace `test-execution` in the allowlist, or be added alongside it? **Recommendation: replace — `test-execution` governs strategy, which can be merged into `run-tests` to reduce skill count.**

### 2. Claude skill allowlist
The Claude backend's tester allowlist in `config.yaml` does not explicitly list capability skills (they come from the base skill bundle). Verify that `capability/run-tests` and `capability/verify-syntax` will be loaded for tester and developer agents respectively, and update `AGENT_SKILL_ALLOWLIST` in `skills.py` if needed.
