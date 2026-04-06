"""Helpers for locating bundled package assets at runtime.

Assets are stored under ``codex_orchestrator/_data/`` and installed as
package data.  These helpers expose stable ``Path`` objects pointing at
the bundled copies so runtime code does not need to rely on the source
tree being present (e.g. after ``pip install`` or ``uv tool install``).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def _data_path(*parts: str) -> Path:
    """Return an absolute ``Path`` for a file under ``codex_orchestrator/_data/``."""
    ref = files("codex_orchestrator").joinpath("_data", *parts)
    return Path(str(ref))


def packaged_templates_dir() -> Path:
    """Path to the bundled ``templates/agents/`` directory."""
    return _data_path("templates", "agents")


def packaged_agents_skills_dir() -> Path:
    """Path to the bundled ``.agents/skills/`` catalog."""
    return _data_path("agents_skills")


def packaged_claude_skills_dir() -> Path:
    """Path to the bundled ``.claude/skills/`` catalog."""
    return _data_path("claude_skills")


def packaged_docs_memory_dir() -> Path:
    """Path to the bundled ``docs/memory/`` directory."""
    return _data_path("docs", "memory")


def packaged_default_config() -> Path:
    """Path to the bundled default ``config.yaml``."""
    return _data_path("default_config.yaml")
