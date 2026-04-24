from __future__ import annotations

import os
from pathlib import Path


def xdg_config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(xdg) if xdg else Path.home() / ".config"


def xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def registry_path() -> Path:
    return xdg_config_home() / "agent-takt" / "fleet.yaml"


def runs_dir() -> Path:
    return xdg_data_home() / "agent-takt" / "fleet" / "runs"
