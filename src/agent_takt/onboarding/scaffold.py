"""Scaffold orchestration helpers for the ``takt init`` command.

Responsible for gitignore management, specs directory setup,
git commit orchestration, and the top-level ``scaffold_project()`` entry point.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import IO

from ..config import load_config
from ..console import BOLD, GREEN, RESET, ConsoleReporter
from ..memory import init_db
from .assets import install_agents_skills, install_claude_agents, install_claude_skills, install_skill_templates
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
        ".claude/agents/",
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
    4. Copies the agents, Claude skill, and Claude agent catalogs.  Managed files
       (``.agents/skills/``, ``.claude/skills/``, and ``.claude/agents/``) are
       **always overwritten** regardless of *overwrite*, so that post-merge
       ``takt init`` re-runs propagate updated content automatically.
    5. Bootstraps the shared memory database at ``.takt/memory/memory.db``
       by calling :func:`~agent_takt.memory.init_db` (idempotent).
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
    # Managed skill files are always overwritten so that post-merge `takt init`
    # re-runs propagate updated skill content (e.g. memory skill) automatically.
    written_agents_skills = install_agents_skills(project_root, overwrite=True)
    console.success("Installed .agents/skills/ operator exceptions")
    written_claude_skills = install_claude_skills(project_root, overwrite=True)
    console.success("Installed .claude/skills/ catalog")
    written_claude_agents = install_claude_agents(project_root, overwrite=True)
    console.success("Installed .claude/agents/ catalog")

    # 5. Bootstrap shared memory database
    # Load the config that was just written (or already existed) so that a
    # custom memory_cache_dir is honoured during the initial model download.
    _scaffold_config = load_config(project_root)
    memory_db_path = project_root / ".takt" / "memory" / "memory.db"
    init_db(memory_db_path, model_cache_dir=_scaffold_config.common.memory_cache_dir)
    console.success("Bootstrapped shared memory database (.takt/memory/)")

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
            + written_claude_agents
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
