"""Scaffold orchestration helpers for the ``takt init`` command.

Responsible for memory seeding, gitignore management, specs directory setup,
git commit orchestration, and the top-level ``scaffold_project()`` entry point.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import IO

from ..console import BOLD, GREEN, RESET, ConsoleReporter
from .assets import install_agents_skills, install_claude_skills, install_skill_templates
from .config import generate_config_yaml, install_templates_with_substitution
from .prompts import InitAnswers
from .upgrade import _MANIFEST_FILENAME, write_assets_manifest


# ---------------------------------------------------------------------------
# Scaffold content constants
# ---------------------------------------------------------------------------

_GITIGNORE_ENTRIES = [
    ".takt/worktrees/",
    ".takt/telemetry/",
    ".takt/logs/",
    ".takt/agent-runs/",
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
worktree at `.takt/worktrees/{feature-root-id}`.

## Bead Lifecycle

Beads move through: `open` → `ready` → `in_progress` → `done` | `blocked` | `handed_off`.
Only the scheduler transitions beads out of `in_progress`. Do not manually mark a
developer bead `done` — use `takt merge` after work is complete.
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
uv run takt plan specs/drafts/my-spec.md

# Persist beads
uv run takt plan --write specs/drafts/my-spec.md
```

## Bead Size Guidelines

A bead is too large if it:
- Touches more than 2–3 functions or multiple subsystems
- Would take a human more than a few hours
- Has acceptance criteria that require multiple distinct implementation steps

Split large beads at natural seams (e.g. data layer vs API layer, backend vs frontend).
"""


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
    addition = separator + "\n# takt\n" + "\n".join(to_add) + "\n"
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
# Git commit helper
# ---------------------------------------------------------------------------


def commit_scaffold(project_root: Path, console: "ConsoleReporter") -> None:
    """Commit the scaffolded files to git.

    Adds ``.gitkeep`` sentinels to directories that would otherwise be empty
    (and thus untracked), stages all scaffolded paths, and creates a single
    ``chore: takt init scaffold`` commit.

    If ``git add`` or ``git commit`` fails (e.g. nothing to stage, or the
    repository has no changes), a warning is printed and the function returns
    normally without raising.

    Args:
        project_root: Root of the target git repository.
        console: Reporter used for progress and warning messages.
    """
    # Write .gitkeep files into directories git would not otherwise track.
    gitkeep_dirs = [
        project_root / ".takt" / "beads",
        project_root / "specs" / "drafts",
        project_root / "specs" / "done",
    ]
    for d in gitkeep_dirs:
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()

    # Paths to stage.
    stage_paths = [
        "templates/",
        ".agents/skills/",
        ".claude/skills/",
        "docs/memory/",
        "specs/",
        ".takt/config.yaml",
        ".takt/assets-manifest.json",
        ".takt/beads/.gitkeep",
        ".gitignore",
    ]

    root_str = str(project_root)

    add_result = subprocess.run(
        ["git", "-C", root_str, "add", "--"] + stage_paths,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_result.returncode != 0:
        console.warn(
            f"git add failed (returncode={add_result.returncode}): {add_result.stderr.strip()}"
        )
        return

    commit_result = subprocess.run(
        ["git", "-C", root_str, "commit", "-m", "chore: takt init scaffold"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit_result.returncode != 0:
        console.warn(
            f"git commit skipped (returncode={commit_result.returncode}): {commit_result.stderr.strip() or commit_result.stdout.strip()}"
        )
        return

    console.success("Committed scaffold files to git (chore: takt init scaffold)")


# ---------------------------------------------------------------------------
# High-level scaffold entry point
# ---------------------------------------------------------------------------


def scaffold_project(
    project_root: Path,
    answers: InitAnswers,
    *,
    overwrite: bool = False,
    stream_out: IO[str] | None = None,
    console: "ConsoleReporter | None" = None,
) -> None:
    """Run all init steps: scaffold directories, install assets, generate config.

    This is the top-level entry point called by ``takt init``.  It:

    1. Creates required ``.takt/`` subdirectories.
    2. Writes a generated ``config.yaml`` from *answers*.
    3. Installs guardrail templates with placeholder substitution.
    4. Copies the agents and Claude skill catalogs.
    5. Seeds ``docs/memory/`` with generic entries.
    6. Updates ``.gitignore``.
    7. Creates ``specs/HOWTO.md`` and ``specs/done/`` directory.
    8. Writes ``.takt/assets-manifest.json`` recording installed asset paths and
       SHA-256 hashes.  If the manifest already exists (i.e. this is a re-run),
       the existing manifest is left untouched and a notice is printed instead.
    9. Commits all scaffolded files to git via :func:`commit_scaffold`.

    Args:
        project_root: Root of the target git repository.
        answers: Collected answers from :func:`collect_init_answers`.
        overwrite: When ``True``, overwrite existing files rather than skipping.
        stream_out: Output stream for progress messages (defaults to ``sys.stdout``).
            Ignored when *console* is provided.
        console: :class:`~.console.ConsoleReporter` for coloured output.  When
            provided, *stream_out* is ignored.  When ``None`` a reporter is
            constructed from *stream_out* (or ``sys.stdout``).
    """
    if console is None:
        console = ConsoleReporter(stream=stream_out or sys.stdout)

    # 1. Create .takt subdirectories
    for subdir in ("beads", "logs", "worktrees", "telemetry", "agent-runs"):
        d = project_root / ".takt" / subdir
        d.mkdir(parents=True, exist_ok=True)
    console.success("Created .takt/ directories")

    # 2. Write config.yaml
    config_path = project_root / ".takt" / "config.yaml"
    config_written: list[Path] = []
    if not config_path.exists() or overwrite:
        config_path.write_text(generate_config_yaml(answers), encoding="utf-8")
        config_written = [config_path]
        console.success("Wrote .takt/config.yaml")
    else:
        console.warn("Skipped .takt/config.yaml (already exists)")

    # 3. Install guardrail templates with substitution
    written_templates = install_templates_with_substitution(project_root, answers, overwrite=overwrite)
    if written_templates:
        console.success(f"Installed {len(written_templates)} guardrail template(s) into templates/agents/")
    else:
        console.warn("Skipped guardrail templates (already exist; use --overwrite to replace)")

    # 4. Install subagent skill templates and operator skill exceptions
    written_skill_templates = install_skill_templates(project_root, overwrite=overwrite)
    if written_skill_templates:
        console.success(
            f"Installed {len(written_skill_templates)} subagent skill template(s) into templates/skills/"
        )
    else:
        console.warn("Skipped templates/skills/ (already exist; use --overwrite to replace)")
    written_agents_skills = install_agents_skills(project_root, overwrite=overwrite)
    console.success("Installed .agents/skills/ operator exceptions")
    written_claude_skills = install_claude_skills(project_root, overwrite=overwrite)
    console.success("Installed .claude/skills/ catalog")

    # 5. Seed memory files
    written_mem = seed_memory_files(project_root, answers, overwrite=overwrite)
    if written_mem:
        console.success(f"Seeded {len(written_mem)} memory file(s) in docs/memory/")
    else:
        console.warn("Skipped memory files (already exist; use --overwrite to replace)")

    # 6. Update .gitignore
    if update_gitignore(project_root):
        console.success("Updated .gitignore with takt entries")
    else:
        console.warn("Skipped .gitignore (entries already present)")

    # 7. Create specs/ structure and HOWTO
    (project_root / "specs" / "done").mkdir(parents=True, exist_ok=True)
    (project_root / "specs" / "drafts").mkdir(parents=True, exist_ok=True)
    howto = create_specs_howto(project_root, overwrite=overwrite)
    if howto:
        console.success("Created specs/HOWTO.md")
    else:
        console.warn("Skipped specs/HOWTO.md (already exists)")

    # 8. Write assets manifest (skipped if one already exists — use `takt upgrade` instead)
    manifest_path = project_root / _MANIFEST_FILENAME
    if manifest_path.is_file():
        console.warn(
            "assets-manifest.json already exists \u2014 run 'takt upgrade' to update assets"
        )
    else:
        all_installed = (
            written_templates
            + written_skill_templates
            + written_agents_skills
            + written_claude_skills
            + config_written
        )
        write_assets_manifest(project_root, all_installed)
        console.success("Wrote .takt/assets-manifest.json")

    # 9. Commit all scaffolded files to git
    commit_scaffold(project_root, console)

    console.emit(
        f"\n{console._c(BOLD)}{console._c(GREEN)}Done.{console._c(RESET)}"
        " Run `takt summary` to verify the setup."
    )
