from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Sequence

import yaml

from .adapter import AdapterError, TaktAdapter
from .models import Project
from .paths import registry_path

_CURRENT_VERSION = 1


class RegistryError(Exception):
    pass


def load_registry() -> list[Project]:
    """Load the fleet registry from disk.

    Returns an empty list if the file does not exist. Raises RegistryError
    for version mismatches or malformed files.
    """
    path = registry_path()
    if not path.exists():
        return []

    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(
            f"Registry file {path} contains invalid YAML: {exc}"
        ) from exc

    if "version" not in data:
        raise RegistryError(
            f"Registry file {path} is missing a 'version' field. "
            "Add 'version: 1' to the top of the file, then try again."
        )

    version = data["version"]
    if not isinstance(version, int):
        raise RegistryError(
            f"Registry 'version' must be an integer, got {version!r}."
        )

    if version > _CURRENT_VERSION:
        raise RegistryError(
            f"Registry file {path} was written by a newer takt-fleet "
            f"(version {version}); this installation only understands version "
            f"{_CURRENT_VERSION}. Upgrade takt-fleet to continue."
        )

    if version < 1:
        raise RegistryError(
            f"Registry version {version} is not supported. "
            f"Only version {_CURRENT_VERSION} is understood."
        )

    projects: list[Project] = []
    for entry in data.get("projects") or []:
        projects.append(
            Project(
                name=entry["name"],
                path=Path(entry["path"]),
                tags=tuple(entry.get("tags") or []),
            )
        )

    return projects


def save_registry(projects: list[Project]) -> None:
    """Atomically write the registry to disk."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "version": _CURRENT_VERSION,
        "projects": [
            {
                "name": p.name,
                "path": str(p.path),
                "tags": list(p.tags),
            }
            for p in projects
        ],
    }

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def filter_projects(
    projects: list[Project],
    tags: Sequence[str] = (),
    names: Sequence[str] = (),
) -> list[Project]:
    """Return projects matching ALL of `tags` AND ANY of `names` (if given)."""
    result = list(projects)
    if tags:
        tag_set = set(tags)
        result = [p for p in result if tag_set.issubset(set(p.tags))]
    if names:
        name_set = set(names)
        result = [p for p in result if p.name in name_set]
    return result


def compute_health(project: Project) -> str:
    """Return health for a project: missing | no-takt | takt-error | ok."""
    if not project.path.exists():
        return "missing"

    takt_dir = project.path / ".takt"
    config_file = takt_dir / "config.yaml"
    if not takt_dir.exists() or not config_file.exists():
        return "no-takt"

    try:
        TaktAdapter(project_path=project.path, timeout=5).version()
    except (AdapterError, OSError):
        return "takt-error"

    return "ok"
