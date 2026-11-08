from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path) -> Path:
    """Walk up from *start* to find the directory containing the ``.takt`` state dir."""
    path = start.resolve()
    for candidate in [path, *path.parents]:
        if (candidate / ".takt").is_dir():
            return candidate
    return path


def main_checkout_root(workdir: Path) -> Path:
    """Return the main checkout root for *workdir*.

    Feature worktrees (``<main>/.takt/worktrees/<id>``) and isolated execution
    roots (``<main>/.takt/agent-runs/<id>/repo``) carry their own ``.takt``
    directory, so the shared memory DB must be anchored on the outermost
    ``.takt`` ancestor instead.  Falls back to :func:`find_project_root`.
    """
    path = workdir.resolve()
    parts = path.parts
    for i, part in enumerate(parts):
        if part == ".takt" and i + 1 < len(parts) and parts[i + 1] in ("worktrees", "agent-runs"):
            return Path(*parts[:i]) if i else Path(path.anchor)
    return find_project_root(workdir)
