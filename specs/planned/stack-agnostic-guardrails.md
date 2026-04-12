---
name: Stack-Agnostic Guardrails and Skills
id: spec-42b24901
description: Remove hardcoded Python/uv commands from framework skills and guardrails; agents detect the test command themselves and write it to shared memory for reuse
dependencies: null
priority: medium
complexity: small
status: planned
tags:
- guardrails
- skills
- memory
scope:
  in: templates/skills/capability/test-execution/SKILL.md; templates/skills/role/tester-validation/SKILL.md; templates/agents/merge-conflict.md
  out: runner.py; config.yaml; AGENT_SKILL_ALLOWLIST; new skill files; developer.md; tester.md (already clean)
feature_root_id: B-78f2e704
---
# Stack-Agnostic Guardrails and Skills

## Objective

Three framework files still hardcode Python/uv-specific commands, preventing the orchestrator from working correctly on non-Python projects without editing core files. The fix is to remove the hardcoded commands and let agents determine the correct test command themselves — detecting it from project files on first encounter, then writing it to shared memory so every subsequent agent gets it immediately without re-detecting.

This approach requires no new skills, no env var injection, and no per-project config changes. It leverages the mandatory memory search already built into every agent's guardrail.

## Problems to Fix

1. **`templates/skills/capability/test-execution/SKILL.md`** hardcodes `uv run python -m unittest tests.<module_name> -v` as the example command. A tester on a TypeScript or Go project gets wrong instructions.

2. **`templates/skills/role/tester-validation/SKILL.md`** says "Do not run `unittest discover`" — Python-specific phrasing for a stack-agnostic concept (don't run broad discovery sweeps).

3. **`templates/agents/merge-conflict.md`** line 9 hardcodes two stale commands:
   - `uv run python -c "import codex_orchestrator"` — references the old project name and is Python-specific
   - `uv run python -m py_compile <file>` — Python-specific syntax check

## What Was Already Fixed

`templates/agents/developer.md` and `templates/agents/tester.md` no longer contain hardcoded Python/uv commands — they were cleaned up by earlier specs. Only the three files above remain.

## Changes

### 1. Rewrite `templates/skills/capability/test-execution/SKILL.md`

Replace the hardcoded command examples with memory-first guidance and a stack-detection fallback. The strategy sections (targeted over broad, scope rules, result reporting, blocking conditions) are stack-agnostic already and stay unchanged.

The command selection section becomes:

```markdown
## Command Selection

Before running anything:

1. Search shared memory for the project's test command:

       $TAKT_CMD memory search "test command" --namespace global --limit 3

   If a prior agent has recorded the command, use it directly.

2. If not in memory, detect from the project root (at `repo/`):
   - `repo/pyproject.toml` present → likely `uv run pytest` or `uv run python -m unittest`
   - `repo/package.json` present → likely `npm test` or `npx jest`
   - `repo/Cargo.toml` present → likely `cargo test`
   - `repo/go.mod` present → likely `go test ./...`
   - `repo/pom.xml` or `repo/build.gradle` present → likely `mvn test` or `./gradlew test`
   - Check `repo/README.md` or `repo/.takt/config.yaml` (`common.test_command`) for the authoritative command.

3. Once you have determined the command, write it to shared memory so future agents don't need to detect it:

       $TAKT_CMD memory add "Test command for this project: <command>" \
           --namespace global --source tester

4. For targeted runs, most frameworks accept a file path or module argument:
   `<test-command> <path/to/test_file>`. Use this form when the framework supports it.
   Fall back to the full command only when targeted runs are not supported.
```

### 2. Update `templates/skills/role/tester-validation/SKILL.md`

In the Scope Rules section, replace the Python-specific line:

**Before:**
```
- Do not run `unittest discover`, the full suite, or broad package-level sweeps when a narrower module-level command will answer the question.
```

**After:**
```
- Do not run broad discovery sweeps or the full suite when a narrower targeted command will answer the question.
```

### 3. Update `templates/agents/merge-conflict.md`

Remove the two stale Python lines and replace with stack-agnostic guidance:

**Before (line 9):**
```
- Run a quick syntax or import check after resolution: `uv run python -c "import codex_orchestrator"` or `uv run python -m py_compile <file>`.
```

**After:**
```
- After resolving conflicts, run the project's test suite to verify nothing is broken. Search memory for the test command (`$TAKT_CMD memory search "test command" --namespace global --limit 3`) or detect it from the project root as described in the `capability/test-execution` skill.
```

## Files to Modify

| File | Change |
|---|---|
| `templates/skills/capability/test-execution/SKILL.md` | Replace hardcoded `uv run python -m unittest` examples with memory-first lookup + stack-detection fallback |
| `templates/skills/role/tester-validation/SKILL.md` | Replace `unittest discover` with stack-agnostic "broad discovery sweep" phrasing |
| `templates/agents/merge-conflict.md` | Remove `uv run python -c "import codex_orchestrator"` and `uv run python -m py_compile`; replace with memory-lookup guidance |

## Acceptance Criteria

- `templates/skills/capability/test-execution/SKILL.md` contains no hardcoded `uv run python`, `unittest`, or `pytest` command strings.
- `templates/skills/role/tester-validation/SKILL.md` contains no reference to `unittest`.
- `templates/agents/merge-conflict.md` contains no reference to `codex_orchestrator`, `py_compile`, or `uv run python`.
- The `test-execution` skill instructs agents to search memory before detecting the test command, and to write the detected command to memory when first discovered.
- A tester agent on a non-Python project receives no Python-specific instructions from any framework file.
- All existing tests pass (no runtime behaviour changes — this is documentation-only).
