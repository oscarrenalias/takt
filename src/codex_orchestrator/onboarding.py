"""Onboarding primitives for locating and copying packaged assets into a target project.

This module provides asset-resolution and copy helpers used by the ``orchestrator init``
command.  All asset-loading logic lives here so that the CLI prompt flow stays thin and
later onboarding steps can reuse these primitives independently.

Asset locations are resolved via :mod:`._assets`, which wraps ``importlib.resources``
so the helpers work correctly in both editable-install and installed-wheel contexts.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ._assets import (
    packaged_agents_skills_dir,
    packaged_claude_skills_dir,
    packaged_default_config,
    packaged_docs_memory_dir,
    packaged_templates_dir,
)


# ---------------------------------------------------------------------------
# Low-level copy helpers
# ---------------------------------------------------------------------------


def copy_asset_file(src: Path, dest: Path, *, overwrite: bool = False) -> None:
    """Copy a single packaged asset file to *dest*.

    Args:
        src: Absolute path to the source file (typically from a ``packaged_*`` helper).
        dest: Destination file path.  Parent directories are created automatically.
        overwrite: When ``False`` (default) and *dest* already exists, the file is
            left untouched.  Pass ``True`` to unconditionally overwrite.

    Raises:
        FileNotFoundError: If *src* does not exist.
    """
    if not src.is_file():
        raise FileNotFoundError(f"Packaged asset not found: {src}")
    if dest.exists() and not overwrite:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def copy_asset_dir(src: Path, dest: Path, *, overwrite: bool = False) -> None:
    """Recursively copy a packaged asset directory into *dest*.

    The contents of *src* are merged into *dest* (i.e. *dest* itself is not
    removed first).  Individual files that already exist at the destination are
    skipped unless *overwrite* is ``True``.

    Args:
        src: Absolute path to the source directory.
        dest: Destination directory.  Created if it does not exist.
        overwrite: When ``True``, existing destination files are overwritten.

    Raises:
        FileNotFoundError: If *src* does not exist or is not a directory.
    """
    if not src.is_dir():
        raise FileNotFoundError(f"Packaged asset directory not found: {src}")
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(src)
        copy_asset_file(item, dest / relative, overwrite=overwrite)


# ---------------------------------------------------------------------------
# High-level asset installation helpers
# ---------------------------------------------------------------------------


def install_templates(project_root: Path, *, overwrite: bool = False) -> list[Path]:
    """Copy bundled guardrail templates into *<project_root>/templates/agents/*.

    Returns a list of destination paths that were written (skipped files are not
    included).

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing template files when ``True``.
    """
    src = packaged_templates_dir()
    dest = project_root / "templates" / "agents"
    written: list[Path] = []
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(src)
        dest_file = dest / relative
        existed = dest_file.exists()
        copy_asset_file(item, dest_file, overwrite=overwrite)
        if not existed or overwrite:
            written.append(dest_file)
    return written


def install_agents_skills(project_root: Path, *, overwrite: bool = False) -> None:
    """Copy the bundled ``.agents/skills/`` catalog into *project_root*.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing skill files when ``True``.
    """
    src = packaged_agents_skills_dir()
    dest = project_root / ".agents" / "skills"
    copy_asset_dir(src, dest, overwrite=overwrite)


def install_claude_skills(project_root: Path, *, overwrite: bool = False) -> None:
    """Copy the bundled ``.claude/skills/`` catalog into *project_root*.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing skill files when ``True``.
    """
    src = packaged_claude_skills_dir()
    dest = project_root / ".claude" / "skills"
    copy_asset_dir(src, dest, overwrite=overwrite)


def install_default_config(project_root: Path, *, overwrite: bool = False) -> Path:
    """Copy the bundled default ``config.yaml`` to *<project_root>/.orchestrator/config.yaml*.

    Returns the destination path.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite an existing config file when ``True``.
    """
    src = packaged_default_config()
    dest = project_root / ".orchestrator" / "config.yaml"
    copy_asset_file(src, dest, overwrite=overwrite)
    return dest


def resolve_memory_seed(name: str) -> Path:
    """Return the packaged path for a memory seed file by name.

    Args:
        name: File name relative to the bundled ``docs/memory/`` directory,
            e.g. ``"conventions.md"`` or ``"known-issues.md"``.

    Returns:
        Absolute path to the packaged memory seed file.

    Raises:
        FileNotFoundError: If no such seed file is bundled.
    """
    path = packaged_docs_memory_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"No bundled memory seed named '{name}' (looked in {packaged_docs_memory_dir()})")
    return path


# ---------------------------------------------------------------------------
# Interactive prompt helpers
# ---------------------------------------------------------------------------

_GITIGNORE_ENTRIES = [
    ".orchestrator/worktrees/",
    ".orchestrator/telemetry/",
    ".orchestrator/logs/",
    ".orchestrator/agent-runs/",
]

_KNOWN_ISSUES_CONTENT = """\
---
name: Known Issues
description: Known issues and workarounds for this project
type: project
---

# Known Issues

## Agent Timeout Patterns

Long-running tasks (e.g. full test suites, large builds) may exceed the agent timeout.
Break work into smaller beads if a single bead consistently times out.
Each bead should represent roughly 1–3 hours of focused agent work.

## JSON Output Wrapping

Agents sometimes wrap their structured JSON output in markdown code fences.
The scheduler handles this automatically, but if a bead fails to parse output,
check for unexpected surrounding text in the agent run log.

## Worktree Directory Discipline

All code changes must happen inside the assigned worktree path.
Never edit files in the main repository root while a bead is in progress in a worktree,
as this can cause merge conflicts on the feature branch.
"""

_CONVENTIONS_CONTENT = """\
---
name: Conventions
description: Project conventions for bead orchestration
type: project
---

# Conventions

## Bead IDs

Bead IDs use the format `B-{8 hex chars}`. Child beads append suffixes:
`B-abc12def-test`, `B-abc12def-review`, `B-abc12def-docs`.

## Running Commands

All commands must be run from the project root. Never run commands from inside a
worktree unless the bead assignment explicitly requires it.

## Memory Append-Only Rule

New memory entries are appended; existing entries are never edited in place unless
explicitly correcting an error. This preserves the audit trail.

## Feature Branches

Each feature has a dedicated branch `feature/{feature-root-id-lowercase}` and a
worktree at `.orchestrator/worktrees/{feature-root-id}`.

## Bead Lifecycle

Beads move through: `open` → `ready` → `in_progress` → `done` | `blocked` | `handed_off`.
Only the scheduler transitions beads out of `in_progress`. Do not manually mark a
developer bead `done` — use `orchestrator merge` after work is complete.
"""

_SPECS_HOWTO_CONTENT = """\
# How to Write Specs

A spec is the input to the planner. The planner decomposes it into a graph of beads.

## Structure

```markdown
# Title

## Objective
One clear sentence describing the goal.

## Acceptance Criteria
- Testable, outcome-focused criteria (not implementation steps)
- Each criterion should be verifiable by an agent

## Scope
What is explicitly in scope and out of scope.

## Files to Add/Modify (optional)
Hints about which files will change. Helps the planner assign expected_files.
```

## Tips for Good Specs

- **One objective per spec.** Multi-objective specs produce tangled bead graphs.
- **Testable criteria only.** "The CLI prints X" is testable. "The code is clean" is not.
- **Keep it small.** A spec that maps to 3–5 developer beads is ideal. Larger specs
  risk scope creep and merge conflicts.
- **Don't prescribe implementation.** Say what the feature does, not how to build it.
  The developer agent decides the implementation.
- **Hint at file scope.** If you know the change touches `src/foo/bar.py`, say so.
  The planner uses this to avoid scheduling conflicts.

## Running the Planner

```bash
# Dry run — prints bead graph without creating beads
uv run orchestrator plan specs/drafts/my-spec.md

# Persist beads
uv run orchestrator plan --write specs/drafts/my-spec.md
```

## Bead Size Guidelines

A bead is too large if it:
- Touches more than 2–3 functions or multiple subsystems
- Would take a human more than a few hours
- Has acceptance criteria that require multiple distinct implementation steps

Split large beads at natural seams (e.g. data layer vs API layer, backend vs frontend).
"""


@dataclass
class InitAnswers:
    """Collected answers from the ``orchestrator init`` interactive prompts."""

    runner: str               # 'claude' or 'codex'
    max_workers: int          # >= 1
    language: str             # free text, e.g. "Python", "TypeScript/Node.js"
    test_command: str         # e.g. "pytest", "npm test"
    build_check_command: str  # e.g. "tsc --noEmit", "uv run python -m py_compile"


def _prompt(
    prompt_text: str,
    default: str,
    *,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> str:
    """Emit a prompt and read one line of input.

    Returns *default* when the user enters an empty line.
    """
    out = stream_out or sys.stdout
    inp = stream_in or sys.stdin
    display = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
    out.write(display)
    out.flush()
    line = inp.readline()
    value = line.rstrip("\n").strip()
    return value if value else default


def collect_init_answers(
    *,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> InitAnswers:
    """Run the interactive question flow and return collected answers.

    Prompts for runner backend, max workers, language/framework, test command,
    and build-check command, with sensible defaults and basic validation.

    Args:
        stream_in: Input stream (defaults to ``sys.stdin``).
        stream_out: Output stream (defaults to ``sys.stdout``).

    Returns:
        An :class:`InitAnswers` instance populated from user input.
    """
    out = stream_out or sys.stdout
    inp = stream_in or sys.stdin

    out.write("=== orchestrator init ===\n")
    out.write("Press Enter to accept the default shown in [brackets].\n\n")
    out.flush()

    # --- Runner backend ---
    while True:
        runner = _prompt(
            "Runner backend (claude/codex)",
            "claude",
            stream_in=inp,
            stream_out=out,
        )
        if runner in ("claude", "codex"):
            break
        out.write(f"  Invalid runner '{runner}'. Choose 'claude' or 'codex'.\n")
        out.flush()

    # --- Max workers ---
    while True:
        raw_workers = _prompt(
            "Max parallel workers",
            "1",
            stream_in=inp,
            stream_out=out,
        )
        try:
            max_workers = int(raw_workers)
            if max_workers >= 1:
                break
            out.write("  Max workers must be at least 1.\n")
            out.flush()
        except ValueError:
            out.write(f"  '{raw_workers}' is not a valid integer.\n")
            out.flush()

    # --- Language / framework ---
    language = _prompt(
        "Project language/framework (e.g. Python, TypeScript/Node.js, Go)",
        "Python",
        stream_in=inp,
        stream_out=out,
    )

    # --- Test command ---
    test_command = _prompt(
        "Test command (e.g. pytest, npm test, go test ./...)",
        "pytest",
        stream_in=inp,
        stream_out=out,
    )

    # --- Build / syntax check command ---
    build_check_command = _prompt(
        "Build/syntax check command (e.g. tsc --noEmit, go build ./...)",
        "python -m py_compile",
        stream_in=inp,
        stream_out=out,
    )

    out.write("\n")
    out.flush()

    return InitAnswers(
        runner=runner,
        max_workers=max_workers,
        language=language,
        test_command=test_command,
        build_check_command=build_check_command,
    )


# ---------------------------------------------------------------------------
# Config and template generation
# ---------------------------------------------------------------------------


def generate_config_yaml(answers: InitAnswers) -> str:
    """Return a ``config.yaml`` string reflecting *answers*.

    Writes the user-configurable ``common`` block plus the standard ``codex``
    and ``claude`` backend blocks (binary, skills_dir, flags, and timeout).
    Settings absent from this file fall back to orchestrator defaults at load
    time via :func:`config.load_config`.

    Args:
        answers: Collected answers from :func:`collect_init_answers`.

    Returns:
        A YAML string suitable for writing to ``.orchestrator/config.yaml``.
    """
    return (
        "# Orchestrator configuration — generated by `orchestrator init`.\n"
        "# Edit this file to customise settings. Missing keys use built-in defaults.\n"
        "\n"
        "common:\n"
        f"  default_runner: {answers.runner}\n"
        f"  test_command: {answers.test_command}\n"
        f"  # max_workers is a CLI flag: orchestrator run --max-workers {answers.max_workers}\n"
        "\n"
        "codex:\n"
        "  binary: codex\n"
        "  skills_dir: .agents\n"
        "  flags:\n"
        "    - \"--skip-git-repo-check\"\n"
        "    - \"--full-auto\"\n"
        "    - \"--color\"\n"
        "    - \"never\"\n"
        "\n"
        "claude:\n"
        "  binary: claude\n"
        "  skills_dir: .claude\n"
        "  flags:\n"
        "    - \"--dangerously-skip-permissions\"\n"
        "  timeout_seconds: 900\n"
        "  model_default: claude-sonnet-4-6\n"
    )


def substitute_template_placeholders(text: str, answers: InitAnswers) -> str:
    """Replace ``{{PLACEHOLDER}}`` tokens in *text* with values from *answers*.

    Substituted placeholders:

    * ``{{LANGUAGE}}`` → ``answers.language``
    * ``{{TEST_COMMAND}}`` → ``answers.test_command``
    * ``{{BUILD_CHECK_COMMAND}}`` → ``answers.build_check_command``

    Args:
        text: Template text containing placeholder tokens.
        answers: Collected answers from :func:`collect_init_answers`.

    Returns:
        The text with all recognised placeholders replaced.
    """
    text = text.replace("{{LANGUAGE}}", answers.language)
    text = text.replace("{{TEST_COMMAND}}", answers.test_command)
    text = text.replace("{{BUILD_CHECK_COMMAND}}", answers.build_check_command)
    return text


def install_templates_with_substitution(
    project_root: Path,
    answers: InitAnswers,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Copy bundled guardrail templates to *project_root* with placeholder substitution.

    Unlike :func:`install_templates`, this variant reads each template as text,
    substitutes ``{{LANGUAGE}}``, ``{{TEST_COMMAND}}``, and ``{{BUILD_CHECK_COMMAND}}``,
    then writes the substituted content to the destination.

    Args:
        project_root: Root directory of the target project.
        answers: Collected answers used for placeholder substitution.
        overwrite: Overwrite existing template files when ``True``.

    Returns:
        List of destination paths that were written.
    """
    src = packaged_templates_dir()
    dest = project_root / "templates" / "agents"
    written: list[Path] = []
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(src)
        dest_file = dest / relative
        if dest_file.exists() and not overwrite:
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        content = item.read_text(encoding="utf-8")
        content = substitute_template_placeholders(content, answers)
        dest_file.write_text(content, encoding="utf-8")
        written.append(dest_file)
    return written


# ---------------------------------------------------------------------------
# Memory seeding
# ---------------------------------------------------------------------------


def _language_specific_known_issues(language: str) -> str:
    """Return language-specific known-issues entries, or empty string if none."""
    lang_lower = language.lower()
    if "typescript" in lang_lower or "node" in lang_lower:
        return (
            "\n## TypeScript / Node.js\n\n"
            "Use `tsc --noEmit` (not `tsc`) to check types without emitting files.\n"
            "Ensure `node_modules/` is in `.gitignore` to avoid committing dependencies.\n"
        )
    if "go" in lang_lower:
        return (
            "\n## Go\n\n"
            "Use `go build ./...` for a syntax/type check without producing binaries.\n"
            "Run `go mod tidy` after adding or removing dependencies.\n"
        )
    return ""


def seed_memory_files(project_root: Path, answers: InitAnswers, *, overwrite: bool = False) -> list[Path]:
    """Create generic ``docs/memory/`` seed files tailored to *answers*.

    Unlike copying the bundled memory files (which contain orchestrator-specific
    content), this function generates new generic entries appropriate for any
    project.

    Args:
        project_root: Root directory of the target project.
        answers: Collected answers (used for language-specific content).
        overwrite: Overwrite existing files when ``True``.

    Returns:
        List of paths that were written.
    """
    memory_dir = project_root / "docs" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    files_to_write = {
        "known-issues.md": _KNOWN_ISSUES_CONTENT + _language_specific_known_issues(answers.language),
        "conventions.md": _CONVENTIONS_CONTENT,
    }
    for name, content in files_to_write.items():
        dest = memory_dir / name
        if dest.exists() and not overwrite:
            continue
        dest.write_text(content, encoding="utf-8")
        written.append(dest)
    return written


# ---------------------------------------------------------------------------
# .gitignore and specs/HOWTO.md
# ---------------------------------------------------------------------------


def update_gitignore(project_root: Path) -> bool:
    """Append orchestrator-specific entries to ``.gitignore`` if not already present.

    Creates ``.gitignore`` if it does not exist.

    Args:
        project_root: Root directory of the target project.

    Returns:
        ``True`` if any entries were appended, ``False`` if all were already present.
    """
    gitignore_path = project_root / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
    to_add = [entry for entry in _GITIGNORE_ENTRIES if entry not in existing]
    if not to_add:
        return False
    separator = "\n" if existing and not existing.endswith("\n") else ""
    addition = separator + "\n# orchestrator\n" + "\n".join(to_add) + "\n"
    with gitignore_path.open("a", encoding="utf-8") as fh:
        fh.write(addition)
    return True


def create_specs_howto(project_root: Path, *, overwrite: bool = False) -> Path | None:
    """Write ``specs/HOWTO.md`` with guidance on writing effective specs.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite an existing file when ``True``.

    Returns:
        The written path, or ``None`` if the file already existed and *overwrite* is ``False``.
    """
    dest = project_root / "specs" / "HOWTO.md"
    if dest.exists() and not overwrite:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_SPECS_HOWTO_CONTENT, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# High-level scaffold entry point
# ---------------------------------------------------------------------------


def scaffold_project(
    project_root: Path,
    answers: InitAnswers,
    *,
    overwrite: bool = False,
    stream_out: IO[str] | None = None,
) -> None:
    """Run all init steps: scaffold directories, install assets, generate config.

    This is the top-level entry point called by ``orchestrator init``.  It:

    1. Creates required ``.orchestrator/`` subdirectories.
    2. Writes a generated ``config.yaml`` from *answers*.
    3. Installs guardrail templates with placeholder substitution.
    4. Copies the agents and Claude skill catalogs.
    5. Seeds ``docs/memory/`` with generic entries.
    6. Updates ``.gitignore``.
    7. Creates ``specs/HOWTO.md`` and ``specs/done/`` directory.

    Args:
        project_root: Root of the target git repository.
        answers: Collected answers from :func:`collect_init_answers`.
        overwrite: When ``True``, overwrite existing files rather than skipping.
        stream_out: Output stream for progress messages (defaults to ``sys.stdout``).
    """
    out = stream_out or sys.stdout

    def _log(msg: str) -> None:
        out.write(f"  {msg}\n")
        out.flush()

    # 1. Create .orchestrator subdirectories
    for subdir in ("beads", "logs", "worktrees", "telemetry", "agent-runs"):
        d = project_root / ".orchestrator" / subdir
        d.mkdir(parents=True, exist_ok=True)
    _log("Created .orchestrator/ directories")

    # 2. Write config.yaml
    config_path = project_root / ".orchestrator" / "config.yaml"
    if not config_path.exists() or overwrite:
        config_path.write_text(generate_config_yaml(answers), encoding="utf-8")
        _log("Wrote .orchestrator/config.yaml")
    else:
        _log("Skipped .orchestrator/config.yaml (already exists)")

    # 3. Install guardrail templates with substitution
    written_templates = install_templates_with_substitution(project_root, answers, overwrite=overwrite)
    if written_templates:
        _log(f"Installed {len(written_templates)} guardrail template(s) into templates/agents/")
    else:
        _log("Skipped guardrail templates (already exist; use --overwrite to replace)")

    # 4. Copy skill catalogs
    install_agents_skills(project_root, overwrite=overwrite)
    _log("Installed .agents/skills/ catalog")
    install_claude_skills(project_root, overwrite=overwrite)
    _log("Installed .claude/skills/ catalog")

    # 5. Seed memory files
    written_mem = seed_memory_files(project_root, answers, overwrite=overwrite)
    if written_mem:
        _log(f"Seeded {len(written_mem)} memory file(s) in docs/memory/")
    else:
        _log("Skipped memory files (already exist; use --overwrite to replace)")

    # 6. Update .gitignore
    if update_gitignore(project_root):
        _log("Updated .gitignore with orchestrator entries")
    else:
        _log("Skipped .gitignore (entries already present)")

    # 7. Create specs/ structure and HOWTO
    (project_root / "specs" / "done").mkdir(parents=True, exist_ok=True)
    (project_root / "specs" / "drafts").mkdir(parents=True, exist_ok=True)
    howto = create_specs_howto(project_root, overwrite=overwrite)
    if howto:
        _log("Created specs/HOWTO.md")
    else:
        _log("Skipped specs/HOWTO.md (already exists)")

    out.write("\nDone. Run `orchestrator summary` to verify the setup.\n")
    out.flush()
