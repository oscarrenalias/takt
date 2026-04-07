"""Onboarding primitives for locating and copying packaged assets into a target project.

This module provides asset-resolution and copy helpers used by the ``orchestrator init``
command.  All asset-loading logic lives here so that the CLI prompt flow stays thin and
later onboarding steps can reuse these primitives independently.

Asset locations are resolved via :mod:`._assets`, which wraps ``importlib.resources``
so the helpers work correctly in both editable-install and installed-wheel contexts.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import IO, Literal

from .._assets import (
    packaged_agents_skills_dir,
    packaged_claude_skills_dir,
    packaged_default_config,
    packaged_templates_dir,
)
from ..console import BOLD, GREEN, RESET, ConsoleReporter
from .assets import (
    copy_asset_dir,
    copy_asset_file,
    install_agents_skills,
    install_claude_skills,
    install_default_config,
    install_templates,
    resolve_memory_seed,
)
from .prompts import InitAnswers, _prompt, collect_init_answers


# ---------------------------------------------------------------------------
# Asset manifest helpers
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = ".takt/assets-manifest.json"

# Relative path prefixes for bundled asset roots tracked by the manifest.
# docs/memory/, specs/, and CLAUDE.md are always user-owned and excluded.
_BUNDLED_ASSET_PREFIXES = (
    "templates/agents/",
    ".agents/skills/",
    ".claude/skills/",
    ".takt/config.yaml",
)

# Guardrail templates are installed with placeholder substitution, so the
# on-disk content differs from the bundled source.  Mark them user_owned at
# init time so that ``takt upgrade`` never attempts to overwrite them.
_USER_OWNED_PREFIXES = ("templates/agents/",)


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*'s contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_user_owned(rel_path: str) -> bool:
    """Return ``True`` if *rel_path* should be marked ``user_owned`` at install time."""
    return any(rel_path.startswith(prefix) for prefix in _USER_OWNED_PREFIXES)


def _empty_manifest() -> dict:
    """Return an empty manifest structure (used when the manifest file is absent)."""
    return {"takt_version": "", "installed_at": "", "assets": {}}


def write_assets_manifest(project_root: Path, installed_files: list[Path]) -> Path:
    """Compute SHA-256 hashes for *installed_files* and write ``.takt/assets-manifest.json``.

    Only files whose project-relative paths fall under a bundled asset root
    (``templates/agents/``, ``.agents/skills/``, ``.claude/skills/``,
    ``.takt/config.yaml``) are recorded.  Files under ``docs/memory/``,
    ``specs/``, or ``CLAUDE.md`` are always user-owned and intentionally
    excluded from the manifest.

    Guardrail templates (``templates/agents/``) are flagged ``user_owned: true``
    at install time because placeholder substitution produces on-disk content
    that differs from the bundled source; ``takt upgrade`` must never
    overwrite them automatically.

    Args:
        project_root: Root directory of the target project.
        installed_files: Absolute paths of files that were installed.  Each
            path must lie inside *project_root*.

    Returns:
        The path to the written manifest file.
    """
    assets: dict[str, dict] = {}
    for abs_path in installed_files:
        if not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(project_root)
        except ValueError:
            continue
        rel_str = rel.as_posix()
        # Only track bundled asset roots.
        if not any(
            rel_str.startswith(prefix) or rel_str == prefix.rstrip("/")
            for prefix in _BUNDLED_ASSET_PREFIXES
        ):
            continue
        assets[rel_str] = {
            "sha256": _sha256_file(abs_path),
            "source": "bundled",
            "user_owned": _is_user_owned(rel_str),
        }

    manifest = {
        "takt_version": _pkg_version("agent-takt"),
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "assets": assets,
    }

    manifest_path = project_root / _MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def read_assets_manifest(project_root: Path) -> dict:
    """Load and parse ``.takt/assets-manifest.json``.

    Args:
        project_root: Root directory of the target project.

    Returns:
        The parsed manifest dictionary, or an empty manifest structure
        (``{"takt_version": "", "installed_at": "", "assets": {}}``) when the
        manifest file is absent or unreadable.
    """
    manifest_path = project_root / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return _empty_manifest()
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_manifest()


# ---------------------------------------------------------------------------
# Upgrade state evaluation helpers
# ---------------------------------------------------------------------------

AssetActionType = Literal[
    "update",              # unmodified since install; bundled version differs → safe to overwrite
    "unchanged",           # unmodified since install; bundled version matches disk → no write needed
    "new",                 # present in bundle but absent from manifest → new in this takt version
    "restored",            # in manifest + still in bundle, but missing from disk → restore
    "skipped_user_owned",  # manifest entry has user_owned: true → skip unconditionally
    "skipped_modified",    # disk sha differs from manifest sha → user has edited the file
    "disabled",            # in manifest but no longer in the bundle → rename to .disabled
    "user_added",          # on disk under a bundled prefix, but not in manifest or bundle
]


@dataclass
class AssetDecision:
    """Upgrade decision for a single asset file."""

    rel_path: str
    """Project-relative POSIX path (matches manifest key format)."""
    action: str
    """One of the :data:`AssetActionType` literals."""
    current_sha: str | None
    """SHA-256 hex digest of the on-disk file, or ``None`` if the file is absent."""
    manifest_sha: str | None
    """SHA-256 hex digest recorded in the installed manifest, or ``None`` if not tracked."""
    bundled_sha: str | None
    """SHA-256 hex digest of the file in the current bundle, or ``None`` if not bundled."""
    user_owned: bool
    """``True`` when the manifest entry carries ``user_owned: true``, or when the file is user-added."""


def _compute_bundled_catalog() -> dict[str, Path]:
    """Enumerate every file in the bundled asset catalog.

    Returns a mapping of project-relative POSIX paths (matching the manifest
    key format) to the absolute :class:`~pathlib.Path` of the corresponding
    bundled file.

    Covered asset roots:

    * ``templates/agents/`` — guardrail templates
    * ``.agents/skills/`` — Codex/OpenAI skill catalog
    * ``.claude/skills/`` — Claude Code skill catalog
    * ``.takt/config.yaml`` — single bundled config file
    """
    catalog: dict[str, Path] = {}

    src = packaged_templates_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog["templates/agents/" + item.relative_to(src).as_posix()] = item

    src = packaged_agents_skills_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog[".agents/skills/" + item.relative_to(src).as_posix()] = item

    src = packaged_claude_skills_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog[".claude/skills/" + item.relative_to(src).as_posix()] = item

    catalog[".takt/config.yaml"] = packaged_default_config()

    return catalog


def evaluate_upgrade_actions(project_root: Path, manifest: dict) -> list[AssetDecision]:
    """Compute the upgrade decision for every relevant asset.

    Applies the following decision table for each file:

    +---------------------------------------------------+--------------------+
    | Condition                                         | Action             |
    +===================================================+====================+
    | ``user_owned: true`` in manifest                  | skipped_user_owned |
    +---------------------------------------------------+--------------------+
    | In bundle, not in manifest                        | new                |
    +---------------------------------------------------+--------------------+
    | disk sha == manifest sha, bundled sha differs     | update             |
    +---------------------------------------------------+--------------------+
    | disk sha == manifest sha == bundled sha           | unchanged          |
    +---------------------------------------------------+--------------------+
    | disk sha != manifest sha                          | skipped_modified   |
    +---------------------------------------------------+--------------------+
    | In manifest + bundle, missing from disk           | restored           |
    +---------------------------------------------------+--------------------+
    | In manifest, NOT in bundle                        | disabled           |
    +---------------------------------------------------+--------------------+
    | On disk under bundled prefix, not in manifest     | user_added         |
    | or bundle                                         |                    |
    +---------------------------------------------------+--------------------+

    Args:
        project_root: Root of the target project.
        manifest: Parsed manifest dict (from :func:`read_assets_manifest`).

    Returns:
        List of :class:`AssetDecision` objects, one per relevant file path.
    """
    bundled = _compute_bundled_catalog()
    manifest_assets: dict[str, dict] = manifest.get("assets", {})
    decisions: list[AssetDecision] = []
    handled: set[str] = set()

    # 1. Evaluate every file present in the current bundled catalog.
    for rel_path, bundled_abs in bundled.items():
        handled.add(rel_path)
        bundled_sha = _sha256_file(bundled_abs)
        manifest_entry = manifest_assets.get(rel_path)
        disk_file = project_root / rel_path
        current_sha = _sha256_file(disk_file) if disk_file.is_file() else None

        if manifest_entry is None:
            # Not yet tracked → new asset shipped in this takt version.
            decisions.append(AssetDecision(
                rel_path=rel_path,
                action="new",
                current_sha=current_sha,
                manifest_sha=None,
                bundled_sha=bundled_sha,
                user_owned=False,
            ))
            continue

        manifest_sha = manifest_entry.get("sha256")
        user_owned = manifest_entry.get("user_owned", False)

        if user_owned:
            action: str = "skipped_user_owned"
        elif current_sha is None:
            # Tracked, still bundled, but absent from disk → restore it.
            action = "restored"
        elif current_sha != manifest_sha:
            # Disk differs from what was installed → user has edited the file.
            action = "skipped_modified"
        elif current_sha == bundled_sha:
            # Disk matches both manifest and bundle → nothing to do.
            action = "unchanged"
        else:
            # Disk matches manifest but bundle has a newer version → safe to update.
            action = "update"

        decisions.append(AssetDecision(
            rel_path=rel_path,
            action=action,
            current_sha=current_sha,
            manifest_sha=manifest_sha,
            bundled_sha=bundled_sha,
            user_owned=user_owned,
        ))

    # 2. Manifest entries that are no longer present in the bundle → disable.
    for rel_path, entry in manifest_assets.items():
        if rel_path in handled:
            continue
        handled.add(rel_path)
        disk_file = project_root / rel_path
        current_sha = _sha256_file(disk_file) if disk_file.is_file() else None
        decisions.append(AssetDecision(
            rel_path=rel_path,
            action="disabled",
            current_sha=current_sha,
            manifest_sha=entry.get("sha256"),
            bundled_sha=None,
            user_owned=entry.get("user_owned", False),
        ))

    # 3. Files on disk under bundled prefixes that are not in the manifest or
    #    bundle → user-added assets that should be tracked.
    _disk_prefix_dirs = [
        ("templates/agents/", project_root / "templates" / "agents"),
        (".agents/skills/", project_root / ".agents" / "skills"),
        (".claude/skills/", project_root / ".claude" / "skills"),
    ]
    for prefix, disk_dir in _disk_prefix_dirs:
        if not disk_dir.is_dir():
            continue
        for item in disk_dir.rglob("*"):
            if not item.is_file():
                continue
            rel_path = prefix + item.relative_to(disk_dir).as_posix()
            if rel_path in handled:
                continue
            handled.add(rel_path)
            decisions.append(AssetDecision(
                rel_path=rel_path,
                action="user_added",
                current_sha=_sha256_file(item),
                manifest_sha=None,
                bundled_sha=None,
                user_owned=True,
            ))

    return decisions


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
# Config and template generation
# ---------------------------------------------------------------------------


def merge_config_keys(
    user_config: dict,
    bundled_config: dict,
    *,
    _prefix: str = "",
) -> tuple[dict, list[str]]:
    """Recursively merge *bundled_config* keys into *user_config*, skipping existing keys.

    Only keys that are present in *bundled_config* but absent from *user_config*
    are inserted.  Existing user keys and values are never removed or overwritten.
    When both sides have a mapping at the same key, the merge recurses into that
    mapping so that nested new keys are inserted without disturbing sibling keys.

    Args:
        user_config: The user's current config dict.  Mutated in-place and
            returned as the first element of the result tuple.
        bundled_config: The bundled default config dict used as the source of
            missing keys.
        _prefix: Internal — dotted path prefix used during recursion.  Callers
            should not set this argument.

    Returns:
        A tuple ``(merged_config, added_keys)`` where *merged_config* is the
        updated *user_config* (same object, mutated in-place) and *added_keys*
        is a list of dotted key paths that were inserted, e.g.
        ``["scheduler.max_corrective_attempts", "claude.timeout_seconds"]``.
        The list is empty when no keys were added.
    """
    added_keys: list[str] = []
    for key, bundled_value in bundled_config.items():
        dotted = f"{_prefix}{key}" if _prefix else key
        if key not in user_config:
            user_config[key] = bundled_value
            added_keys.append(dotted)
        elif isinstance(bundled_value, dict) and isinstance(user_config[key], dict):
            _, child_added = merge_config_keys(
                user_config[key],
                bundled_value,
                _prefix=f"{dotted}.",
            )
            added_keys.extend(child_added)
    return user_config, added_keys


def generate_config_yaml(answers: InitAnswers) -> str:
    """Return a ``config.yaml`` string reflecting *answers*.

    Reads the bundled ``default_config.yaml`` — the single source of truth for
    config structure and defaults — and substitutes only the user-provided runner
    and test command.  All other settings (``allowed_tools_default``,
    ``allowed_tools_by_agent``, ``scheduler``, etc.) come directly from the
    bundled file and remain complete and correct.

    .. note::
        If :func:`config.default_config` in ``config.py`` is updated with new
        keys, the bundled ``_data/default_config.yaml`` must be kept in sync so
        that ``takt init`` generates a config that matches the runtime defaults.

    Args:
        answers: Collected answers from :func:`collect_init_answers`.

    Returns:
        A YAML string suitable for writing to ``.takt/config.yaml``.
    """
    text = packaged_default_config().read_text(encoding="utf-8")
    # Replace only the specific YAML key values to avoid corrupting other fields.
    text = re.sub(r'(default_runner:\s*)\S+', rf'\g<1>{answers.runner}', text)
    text = re.sub(r'(test_command:\s*).*', rf'\g<1>{answers.test_command}', text)
    return text


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

    # 4. Copy skill catalogs
    written_agents_skills = install_agents_skills(project_root, overwrite=overwrite)
    console.success("Installed .agents/skills/ catalog")
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
