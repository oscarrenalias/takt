"""
Boundary enforcement tests for the agent_takt_fleet package.

These tests enforce the fleet project interaction contract documented in
spec-0ba9d2a3 (Fleet Manager v0.1.0).  Fleet must never write to any file
under a project's .takt/ directory, import agent_takt domain-logic modules,
or spawn subprocess calls with cwd= outside the dedicated adapter module.

Three invariants are checked via AST analysis (no fleet code is imported or
executed by these tests):

1. Write-path centralisation: only registry.py and runlog.py may perform
   filesystem writes (open write-mode, Path.write_text/bytes, shutil write
   functions, yaml.safe_dump/dump to a stream, json.dump to a file).  All
   other fleet modules must call save_registry() or runlog APIs to persist
   data.

   Fleet-owned write targets:
     registry.py  →  $XDG_CONFIG_HOME/agent-takt/fleet.yaml
     runlog.py    →  $XDG_DATA_HOME/agent-takt/fleet/runs/<run_id>.json

2. Forbidden domain-logic imports: no fleet module may import from
   agent_takt.storage, .scheduler, .runner, .gitutils, .planner, or
   agent_takt.cli.*.  Importing agent_takt utilities/helpers/models
   (e.g. agent_takt.console, agent_takt.models) is allowed.

3. Subprocess CWD confinement: subprocess calls with cwd= are only allowed
   in adapter.py (TaktAdapter).  All other fleet modules must route
   project-path subprocess calls through TaktAdapter methods.
"""
from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Package root
# ---------------------------------------------------------------------------

FLEET_SRC = Path(__file__).resolve().parents[2] / "src" / "agent_takt_fleet"


def _py_files() -> list[Path]:
    """All .py files under src/agent_takt_fleet/, sorted for determinism."""
    return sorted(FLEET_SRC.rglob("*.py"))


def _rel(path: Path) -> str:
    """Module-relative path string for error messages."""
    return path.relative_to(FLEET_SRC).as_posix()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


# ---------------------------------------------------------------------------
# Check 1: Write-path centralisation
# ---------------------------------------------------------------------------

_WRITE_ALLOWED_FILES = frozenset({"registry.py", "runlog.py"})

_SHUTIL_WRITE_FUNCS = frozenset({
    "copy", "copy2", "copyfile", "copyfileobj", "move", "copytree",
})


def _extract_open_mode(node: ast.Call) -> str | None:
    """Return the literal mode string from an open() call, or None."""
    if len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _write_call_desc(node: ast.Call) -> str | None:
    """Return a short description if `node` is a filesystem write call, else None."""
    func = node.func

    # open() / io.open() / Path.open() with a write-mode argument
    if isinstance(func, ast.Name):
        func_name: str | None = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        func_name = None

    if func_name == "open":
        mode = _extract_open_mode(node)
        if mode is not None and any(c in mode for c in "wax"):
            return f"open(mode={mode!r})"

    if not isinstance(func, ast.Attribute):
        return None

    attr = func.attr

    # Path.write_text() / Path.write_bytes()
    if attr in ("write_text", "write_bytes"):
        return f".{attr}(...)"

    # shutil write functions
    if (
        isinstance(func.value, ast.Name)
        and func.value.id == "shutil"
        and attr in _SHUTIL_WRITE_FUNCS
    ):
        return f"shutil.{attr}(...)"

    # yaml.safe_dump / yaml.dump called with a stream argument
    if (
        isinstance(func.value, ast.Name)
        and func.value.id == "yaml"
        and attr in ("safe_dump", "dump")
    ):
        has_stream = len(node.args) >= 2 or any(
            kw.arg == "stream" for kw in node.keywords
        )
        if has_stream:
            return f"yaml.{attr}(..., stream)"

    # json.dump called with a file argument
    if (
        isinstance(func.value, ast.Name)
        and func.value.id == "json"
        and attr == "dump"
    ):
        has_fp = len(node.args) >= 2 or any(kw.arg == "fp" for kw in node.keywords)
        if has_fp:
            return "json.dump(obj, fp)"

    return None


def _write_violations(path: Path) -> list[str]:
    if path.name in _WRITE_ALLOWED_FILES:
        return []
    violations = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Call):
            desc = _write_call_desc(node)
            if desc:
                violations.append(f"{_rel(path)}:{node.lineno}: {desc}")
    return violations


def test_write_path_centralisation() -> None:
    """Filesystem writes must only appear in registry.py and runlog.py.

    These two modules own the fleet-controlled write paths:
      registry.py  →  $XDG_CONFIG_HOME/agent-takt/fleet.yaml
      runlog.py    →  $XDG_DATA_HOME/agent-takt/fleet/runs/<run_id>.json

    All other fleet modules must call save_registry() or runlog APIs rather
    than writing to disk directly.
    """
    assert FLEET_SRC.exists(), f"Fleet package not found: {FLEET_SRC}"
    violations: list[str] = []
    for path in _py_files():
        violations.extend(_write_violations(path))
    assert not violations, (
        "Forbidden filesystem writes found outside registry.py / runlog.py:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Check 2: Forbidden domain-logic imports
# ---------------------------------------------------------------------------

_FORBIDDEN_MODULES = frozenset({
    "agent_takt.storage",
    "agent_takt.scheduler",
    "agent_takt.runner",
    "agent_takt.gitutils",
    "agent_takt.planner",
})

# agent_takt.cli itself and any sub-module (commands, parser, …)
_FORBIDDEN_PREFIXES = ("agent_takt.cli",)


def _is_forbidden_import(module: str) -> bool:
    if module in _FORBIDDEN_MODULES:
        return True
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in _FORBIDDEN_PREFIXES
    )


def _import_violations(path: Path) -> list[str]:
    violations = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_import(alias.name):
                    violations.append(
                        f"{_rel(path)}:{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and _is_forbidden_import(node.module):
                violations.append(
                    f"{_rel(path)}:{node.lineno}: from {node.module} import ..."
                )
    return violations


def test_no_forbidden_domain_imports() -> None:
    """Fleet modules must not import agent_takt domain-logic internals.

    Forbidden: agent_takt.storage, .scheduler, .runner, .gitutils, .planner,
    and agent_takt.cli (including all sub-modules).

    Importing agent_takt utilities, helpers, formatters, or shared models
    (e.g. agent_takt.console, agent_takt.models) is allowed — the restriction
    targets modules that read or mutate takt project state directly.
    """
    assert FLEET_SRC.exists(), f"Fleet package not found: {FLEET_SRC}"
    violations: list[str] = []
    for path in _py_files():
        violations.extend(_import_violations(path))
    assert not violations, (
        "Forbidden imports from agent_takt domain modules:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Check 3: Subprocess CWD confinement
# ---------------------------------------------------------------------------

_SUBPROCESS_ALLOWED_FILE = "adapter.py"
_SUBPROCESS_CALL_NAMES = frozenset({
    "run", "Popen", "call", "check_call", "check_output",
})


def _subprocess_cwd_violations(path: Path) -> list[str]:
    if path.name == _SUBPROCESS_ALLOWED_FILE:
        return []
    violations = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_CALL_NAMES
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        if any(kw.arg == "cwd" for kw in node.keywords):
            violations.append(
                f"{_rel(path)}:{node.lineno}: subprocess.{func.attr}(..., cwd=...)"
            )
    return violations


def test_subprocess_cwd_confined_to_adapter() -> None:
    """subprocess calls with cwd= must only appear in adapter.py.

    Only TaktAdapter (adapter.py) may use a registered project path as a
    subprocess working directory.  All other fleet modules that need to run
    takt commands against a project must do so through TaktAdapter methods
    (version(), summary(), create_bead(), run()).
    """
    assert FLEET_SRC.exists(), f"Fleet package not found: {FLEET_SRC}"
    violations: list[str] = []
    for path in _py_files():
        violations.extend(_subprocess_cwd_violations(path))
    assert not violations, (
        "subprocess calls with cwd= found outside adapter.py:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
