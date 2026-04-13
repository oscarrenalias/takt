---
name: Improve takt init onboarding with stack selection menu
id: spec-302156c2
description: Replace free-text language prompt in takt init with a numbered stack
  menu that drives smart defaults for test and build commands
dependencies: null
priority: null
complexity: small
status: planned
tags:
- onboarding
- cli
- ux
scope:
  in: prompts.py, init.py, scaffold.py, tests for onboarding
  out: config generation, asset installation, scaffold_project pipeline
feature_root_id: null
---

# Improve takt init onboarding with stack selection menu

## Objective

The `takt init` interactive prompt asks for a free-text language/framework value, then always shows Python-specific defaults for the test and build commands regardless of what the user typed. This leads to confusion (what value do I enter? is "Node.js" right or "node"?) and extra manual correction. Replace the language question with a numbered menu of supported stacks and use the selection to pre-fill the test and build command defaults automatically.

## Problems to Fix

1. **Free-text language field is ambiguous.** "Node.js", "node", "Nodejs", "NodeJS" are all plausible; the system accepts any of them silently and the downstream string-matching in `scaffold.py:_language_specific_known_issues()` may or may not fire.
2. **Test and build defaults never change.** `"pytest"` and `"python -m py_compile"` are shown as defaults regardless of which language was entered. Users must manually correct both fields every time.
3. **Runner prompt is also free-text with manual validation.** Minor, but consistent with the same pattern.
4. **`--non-interactive` branch hardcodes Python strings.** If the STACKS table changes, the non-interactive defaults can drift silently.

## Changes

### New `STACKS` catalog in `prompts.py`

A module-level constant mapping a canonical key to display name, test command, and build/check command:

```python
STACKS: list[tuple[str, str, str]] = [
    # (display_name, test_command, build_check_command)
    ("Python",     "pytest",          "python -m py_compile"),
    ("Node.js",    "npm test",        "npm run build"),
    ("TypeScript", "npm test",        "tsc --noEmit"),
    ("Go",         "go test ./...",   "go build ./..."),
    ("Rust",       "cargo test",      "cargo build"),
    ("Java (Maven)", "mvn test",      "mvn compile -q"),
    ("Other",      "",                ""),   # free-text fallback
]
```

### New `_select_from_list()` helper in `prompts.py`

Prints a numbered list, reads a single integer (1-based), validates the range, and re-prompts on invalid input. Signature:

```python
def _select_from_list(
    prompt_text: str,
    options: list[str],
    default_index: int = 0,
    *,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> int:
    """Return the 0-based index of the chosen option."""
```

Default is shown as `[1]` etc. Returns 0-based index. Works with the existing stream-injection test model (no curses, no ANSI).

### Updated `collect_init_answers()` flow

1. **Runner**: replace the while-True free-text loop with `_select_from_list("Runner backend", ["claude", "codex"])`.
2. **Language/stack**: replace free-text `_prompt()` with `_select_from_list("Project stack", [s[0] for s in STACKS])`. If "Other" is selected, fall back to a free-text `_prompt()` for all three of language, test command, and build command (current behaviour).
3. **Test command**: derive default from `STACKS[selected_index][1]` then call `_prompt()` so the user can still override.
4. **Build/check command**: same — derive default from `STACKS[selected_index][2]`, then `_prompt()`.
5. **`InitAnswers.language`**: store the display name (e.g. `"TypeScript"`) so downstream code gets a canonical, consistent string.

### `scaffold.py` — tighten `_language_specific_known_issues()`

Replace fuzzy `"node" in lang_lower` / `"typescript" in lang_lower` checks with exact matches against the canonical display names from `STACKS` (`"Node.js"`, `"TypeScript"`). Add a Go entry if not already present.

### `init.py` — fix non-interactive defaults

Reference `STACKS[0]` (Python) instead of hardcoding `"pytest"` and `"python -m py_compile"`:

```python
from ...onboarding.prompts import STACKS as _STACKS
_py = _STACKS[0]
answers = InitAnswers(runner="claude", max_workers=1, language=_py[0], test_command=_py[1], build_check_command=_py[2])
```

## Files to Modify

| File | Change |
|---|---|
| `src/agent_takt/onboarding/prompts.py` | Add `STACKS`, add `_select_from_list()`, rewrite `collect_init_answers()` |
| `src/agent_takt/onboarding/scaffold.py` | Tighten `_language_specific_known_issues()` to use exact canonical names |
| `src/agent_takt/cli/commands/init.py` | Fix non-interactive defaults to reference `STACKS[0]` |
| `tests/test_orchestrator.py` or `tests/test_cli_init.py` | Update/add tests for the new prompt flow |

## Acceptance Criteria

- Running `takt init` (interactive) shows a numbered list for stack selection; entering the number for TypeScript selects it and pre-fills `npm test` / `tsc --noEmit` as defaults for the next two prompts.
- Entering the number for "Other" falls back to free-text prompts for language, test command, and build command (unchanged from current behaviour).
- An invalid entry (out of range, non-integer) re-prompts with an error message rather than crashing.
- Runner selection is also a numbered menu; invalid entry re-prompts.
- `takt init --non-interactive` still works and uses Python stack values sourced from `STACKS[0]`, not hardcoded strings.
- `_language_specific_known_issues()` returns the TypeScript/Node.js block for both `"Node.js"` and `"TypeScript"` canonical names, and returns empty string for `"Other"`.
- All existing `takt init` tests pass; new tests cover `_select_from_list()` boundary cases (default, valid choice, invalid then valid, out-of-range).
- `uv run pytest tests/ -n auto -q` passes.

## Pending Decisions

- ~~Arrow keys vs numbered list~~ — resolved: numbered list, compatible with stream-injection tests and non-TTY environments.
- ~~Which stacks to include~~ — resolved: Python, Node.js, TypeScript, Go, Rust, Java, Other. C# and Ruby excluded.
