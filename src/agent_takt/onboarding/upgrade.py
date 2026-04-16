"""Upgrade evaluation logic for the onboarding package.

This module provides upgrade decision logic, bundled asset catalog helpers,
and manifest read/write functions used by ``takt upgrade``.  Extracting
these here keeps the main :mod:`~agent_takt.onboarding` namespace thin and
avoids circular imports with the asset-copy and scaffold modules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Literal

from .._assets import (
    packaged_agents_skills_dir,
    packaged_claude_agents_dir,
    packaged_claude_skills_dir,
    packaged_default_config,
    packaged_skill_templates_dir,
    packaged_templates_dir,
)


# ---------------------------------------------------------------------------
# Asset manifest helpers
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = ".takt/assets-manifest.json"

# Relative path prefixes for bundled asset roots tracked by the manifest.
# docs/memory/, specs/, and CLAUDE.md are always user-owned and excluded.
_BUNDLED_ASSET_PREFIXES = (
    "templates/agents/",
    "templates/skills/",
    ".agents/skills/",
    ".claude/skills/",
    ".claude/agents/",
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
    (``templates/agents/``, ``templates/skills/``, ``.agents/skills/``,
    ``.claude/skills/``, ``.claude/agents/``, ``.takt/config.yaml``) are
    recorded.  Files under ``docs/memory/``, ``specs/``, or ``CLAUDE.md`` are
    always user-owned and intentionally excluded from the manifest.

    Guardrail templates (``templates/agents/``) are flagged ``user_owned: true``
    at install time because placeholder substitution produces on-disk content
    that differs from the bundled source; ``takt upgrade`` must never
    overwrite them automatically.  Skill templates (``templates/skills/``) are
    not placeholder-substituted and are therefore upgradeable.

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

    * ``templates/agents/`` — guardrail templates (user-owned; placeholder-substituted)
    * ``templates/skills/`` — subagent Codex skill templates (upgradeable)
    * ``.agents/skills/`` — repo-root operator exception assets
    * ``.claude/skills/`` — Claude Code skill catalog
    * ``.claude/agents/`` — Claude agents catalog
    * ``.takt/config.yaml`` — single bundled config file
    """
    catalog: dict[str, Path] = {}

    src = packaged_templates_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog["templates/agents/" + item.relative_to(src).as_posix()] = item

    src = packaged_skill_templates_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog["templates/skills/" + item.relative_to(src).as_posix()] = item

    src = packaged_agents_skills_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog[".agents/skills/" + item.relative_to(src).as_posix()] = item

    src = packaged_claude_skills_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog[".claude/skills/" + item.relative_to(src).as_posix()] = item

    src = packaged_claude_agents_dir()
    for item in src.rglob("*"):
        if item.is_file():
            catalog[".claude/agents/" + item.relative_to(src).as_posix()] = item

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
        ("templates/skills/", project_root / "templates" / "skills"),
        (".agents/skills/", project_root / ".agents" / "skills"),
        (".claude/skills/", project_root / ".claude" / "skills"),
        (".claude/agents/", project_root / ".claude" / "agents"),
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
