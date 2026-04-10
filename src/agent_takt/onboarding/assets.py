"""Asset-copying and installation helpers for the onboarding package.

This module provides low-level copy helpers and high-level install functions
that copy bundled package assets into a target project directory.  All
asset-loading logic delegates to :mod:`agent_takt._assets`, which wraps
``importlib.resources`` so helpers work correctly in both editable-install
and installed-wheel contexts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .._assets import (
    packaged_agents_skills_dir,
    packaged_claude_skills_dir,
    packaged_default_config,
    packaged_docs_memory_dir,
    packaged_skill_templates_dir,
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


def copy_asset_dir(src: Path, dest: Path, *, overwrite: bool = False) -> list[Path]:
    """Recursively copy a packaged asset directory into *dest*.

    The contents of *src* are merged into *dest* (i.e. *dest* itself is not
    removed first).  Individual files that already exist at the destination are
    skipped unless *overwrite* is ``True``.

    Args:
        src: Absolute path to the source directory.
        dest: Destination directory.  Created if it does not exist.
        overwrite: When ``True``, existing destination files are overwritten.

    Returns:
        List of destination paths that were written (skipped files are not included).

    Raises:
        FileNotFoundError: If *src* does not exist or is not a directory.
    """
    if not src.is_dir():
        raise FileNotFoundError(f"Packaged asset directory not found: {src}")
    dest.mkdir(parents=True, exist_ok=True)
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


def install_skill_templates(project_root: Path, *, overwrite: bool = False) -> list[Path]:
    """Copy bundled subagent skill templates into *<project_root>/templates/skills/*.

    These are Codex-compatible skill template files intended for agent subworkers.
    They live under ``templates/skills/`` so projects can customise them separately
    from the operator-facing exceptions in ``.agents/skills/``.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing skill template files when ``True``.

    Returns:
        List of destination paths that were written.
    """
    src = packaged_skill_templates_dir()
    dest = project_root / "templates" / "skills"
    return copy_asset_dir(src, dest, overwrite=overwrite)


def install_agents_skills(project_root: Path, *, overwrite: bool = False) -> list[Path]:
    """Copy the bundled ``.agents/skills/`` operator exceptions into *project_root*.

    This installs operator-facing skill overrides (e.g. ``memory``,
    ``task/spec-management``) that are not part of the subagent skill
    template catalog.  Subagent Codex skill templates are installed
    separately by :func:`install_skill_templates` into ``templates/skills/``.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing skill files when ``True``.

    Returns:
        List of destination paths that were written.
    """
    src = packaged_agents_skills_dir()
    dest = project_root / ".agents" / "skills"
    return copy_asset_dir(src, dest, overwrite=overwrite)


def install_claude_skills(project_root: Path, *, overwrite: bool = False) -> list[Path]:
    """Copy the bundled ``.claude/skills/`` catalog into *project_root*.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite existing skill files when ``True``.

    Returns:
        List of destination paths that were written.
    """
    src = packaged_claude_skills_dir()
    dest = project_root / ".claude" / "skills"
    return copy_asset_dir(src, dest, overwrite=overwrite)


def install_default_config(project_root: Path, *, overwrite: bool = False) -> Path:
    """Copy the bundled default ``config.yaml`` to *<project_root>/.takt/config.yaml*.

    Returns the destination path.

    Args:
        project_root: Root directory of the target project.
        overwrite: Overwrite an existing config file when ``True``.
    """
    src = packaged_default_config()
    dest = project_root / ".takt" / "config.yaml"
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
        raise FileNotFoundError(
            f"No bundled memory seed named '{name}' (looked in {packaged_docs_memory_dir()})"
        )
    return path
