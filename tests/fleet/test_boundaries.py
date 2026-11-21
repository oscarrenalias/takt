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

_WRITE_ALLOWED_FILES = frozenset({"registry.py", "runlog.py", "web/demo.py"})

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
    if _rel(path) in _WRITE_ALLOWED_FILES:
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
# Check 2b: Transitive forbidden imports (pure AST, nothing is imported)
# ---------------------------------------------------------------------------

AGENT_TAKT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent_takt"


def _resolve_module_file(
    module: str, package_root: Path, package_name: str
) -> Path | None:
    """Map a dotted module name to its source file under package_root, or None."""
    if module != package_name and not module.startswith(package_name + "."):
        return None
    parts = module.split(".")[1:]
    base = package_root.joinpath(*parts)
    if base.with_suffix(".py").is_file() and parts:
        return base.with_suffix(".py")
    init = base / "__init__.py"
    if init.is_file():
        return init
    return None


def _expand_with_parents(module: str, package_name: str) -> list[str]:
    """Importing a.b.c also executes a and a.b; return all of them, outermost first."""
    parts = module.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _direct_package_imports(
    path: Path,
    own_module: str | None,
    is_package: bool,
    package_root: Path,
    package_name: str,
) -> list[str]:
    """Modules under `package_name` imported anywhere in `path` (incl. nested scopes).

    `own_module` is the dotted name of `path` when it belongs to the package
    (needed to resolve relative imports); for fleet files it is None and
    relative imports are ignored (they point at fleet modules).
    """
    found: list[str] = []
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return found

    def in_pkg(name: str) -> bool:
        return name == package_name or name.startswith(package_name + ".")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if in_pkg(alias.name):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if own_module is None:
                    continue
                pkg_parts = own_module.split(".")
                if not is_package:
                    pkg_parts = pkg_parts[:-1]
                up = node.level - 1
                if up >= len(pkg_parts):
                    continue
                if up:
                    pkg_parts = pkg_parts[:-up]
                base = ".".join(pkg_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if not in_pkg(base):
                continue
            found.append(base)
            # `from pkg import name` may import the submodule pkg.name
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}"
                if _resolve_module_file(candidate, package_root, package_name):
                    found.append(candidate)
    return found


def find_transitive_violations(
    fleet_root: Path,
    package_root: Path,
    package_name: str = "agent_takt",
) -> list[str]:
    """Return import-chain strings from fleet files to forbidden modules.

    Each fleet .py file under `fleet_root` is parsed with `ast`; its
    `package_name` imports (absolute/relative, module-level or nested) are
    followed transitively through the sources under `package_root` (BFS,
    cycle-safe). Importing a submodule also executes its parent packages'
    __init__, so those are followed too. Nothing is imported or executed.

    Forbidden modules are given by the module-level _FORBIDDEN_MODULES /
    _FORBIDDEN_PREFIXES. A chain looks like:
        agent_takt_fleet/x.py -> agent_takt.audit -> agent_takt.storage
    """
    violations: list[str] = []
    imports_cache: dict[Path, list[str]] = {}

    def module_imports(module: str) -> list[str]:
        file = _resolve_module_file(module, package_root, package_name)
        if file is None:
            return []
        if file not in imports_cache:
            imports_cache[file] = _direct_package_imports(
                file, module, file.name == "__init__.py", package_root, package_name
            )
        return imports_cache[file]

    for fleet_file in sorted(fleet_root.rglob("*.py")):
        label = f"{fleet_root.name}/{fleet_file.relative_to(fleet_root).as_posix()}"
        roots = _direct_package_imports(
            fleet_file, None, False, package_root, package_name
        )
        # BFS with predecessor map so the shortest chain is reported.
        pred: dict[str, str | None] = {}
        queue: list[str] = []

        def visit(mod: str, parent: str | None) -> None:
            for m in _expand_with_parents(mod, package_name):
                if m not in pred:
                    pred[m] = parent
                    queue.append(m)

        for r in roots:
            visit(r, None)
        reported: set[str] = set()
        i = 0
        while i < len(queue):
            mod = queue[i]
            i += 1
            if _is_forbidden_import(mod):
                if mod not in reported:
                    reported.add(mod)
                    chain = [mod]
                    while pred[chain[-1]] is not None:
                        chain.append(pred[chain[-1]])  # type: ignore[arg-type]
                    chain.reverse()
                    violations.append(" -> ".join([label, *chain]))
                continue  # do not descend into forbidden modules
            for nxt in module_imports(mod):
                visit(nxt, mod)
    return violations


def test_no_transitive_forbidden_imports() -> None:
    """No fleet module may reach a forbidden agent_takt module through any chain.

    Catches e.g. fleet -> agent_takt.audit -> agent_takt.storage, which the
    direct-import check cannot see.
    """
    assert FLEET_SRC.exists(), f"Fleet package not found: {FLEET_SRC}"
    assert AGENT_TAKT_SRC.exists(), f"agent_takt package not found: {AGENT_TAKT_SRC}"
    violations = find_transitive_violations(FLEET_SRC, AGENT_TAKT_SRC)
    assert not violations, (
        "Fleet reaches forbidden agent_takt modules transitively:\n"
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


# ---------------------------------------------------------------------------
# Check 4: the web package is covered (transitive imports: Check 2b)
# ---------------------------------------------------------------------------

def test_web_package_is_scanned() -> None:
    """agent_takt_fleet.web (and event_summary) must be inside the checked set."""
    rels = {_rel(p) for p in _py_files()}
    for expected in ("web/app.py", "web/readers.py", "web/viewmodels.py",
                     "web/stream.py", "web/demo.py", "event_summary.py",
                     "cli/commands/serve.py"):
        assert expected in rels, f"{expected} not covered by boundary checks"


def test_web_write_allowance_is_only_demo() -> None:
    """Only web/demo.py (fixtures under a caller-supplied dir) may write besides
    registry.py and runlog.py; the server modules themselves must stay read-only."""
    assert _WRITE_ALLOWED_FILES == {"registry.py", "runlog.py", "web/demo.py"}
    for rel in ("web/app.py", "web/readers.py", "web/viewmodels.py", "web/stream.py"):
        assert _rel(FLEET_SRC / rel) not in _WRITE_ALLOWED_FILES
        assert _write_violations(FLEET_SRC / rel) == []


# ---------------------------------------------------------------------------
# Self-tests for the transitive checker (synthetic package trees in tmp_path)
# ---------------------------------------------------------------------------


def _make_tree(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """Write {relative path: source} under tmp_path; return (fleet_root, package_root)."""
    for rel, src in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
    return tmp_path / "agent_takt_fleet", tmp_path / "agent_takt"


_PKG_INIT = {"agent_takt/__init__.py": ""}
_STORAGE = {"agent_takt/storage.py": "X = 1\n"}


def test_selftest_indirect_import_through_allowed_looking_module(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/helper.py": "from agent_takt import storage\n",
        "agent_takt_fleet/reader.py": "import agent_takt.helper\n",
    })
    assert find_transitive_violations(fleet, pkg) == [
        "agent_takt_fleet/reader.py -> agent_takt.helper -> agent_takt.storage"
    ]


def test_selftest_direct_violation(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt_fleet/reader.py": "import agent_takt.storage\n",
    })
    assert find_transitive_violations(fleet, pkg) == [
        "agent_takt_fleet/reader.py -> agent_takt.storage"
    ]


def test_selftest_two_hop_chain(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/a.py": "import agent_takt.b\n",
        "agent_takt/b.py": "import agent_takt.storage\n",
        "agent_takt_fleet/reader.py": "from agent_takt.a import thing\n",
    })
    assert find_transitive_violations(fleet, pkg) == [
        "agent_takt_fleet/reader.py -> agent_takt.a -> agent_takt.b -> agent_takt.storage"
    ]


def test_selftest_relative_import_inside_package(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/helper.py": "from . import storage\n",
        "agent_takt_fleet/reader.py": "import agent_takt.helper\n",
    })
    violations = find_transitive_violations(fleet, pkg)
    assert violations == [
        "agent_takt_fleet/reader.py -> agent_takt.helper -> agent_takt.storage"
    ]


def test_selftest_import_nested_in_function(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/helper.py": "def f():\n    from .storage import X\n    return X\n",
        "agent_takt_fleet/reader.py": "def g():\n    import agent_takt.helper\n",
    })
    assert len(find_transitive_violations(fleet, pkg)) == 1


def test_selftest_from_package_import_resolves_to_submodule(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt_fleet/reader.py": "from agent_takt import storage\n",
    })
    assert find_transitive_violations(fleet, pkg) == [
        "agent_takt_fleet/reader.py -> agent_takt.storage"
    ]


def test_selftest_parent_init_importing_forbidden_module(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_STORAGE,
        "agent_takt/__init__.py": "from agent_takt import storage\n",
        "agent_takt/models.py": "Y = 1\n",
        "agent_takt_fleet/reader.py": "import agent_takt.models\n",
    })
    violations = find_transitive_violations(fleet, pkg)
    assert violations and all(v.endswith("agent_takt.storage") for v in violations)


def test_selftest_import_cycle_terminates(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/a.py": "import agent_takt.b\n",
        "agent_takt/b.py": "import agent_takt.a\nimport agent_takt.storage\n",
        "agent_takt_fleet/reader.py": "import agent_takt.a\n",
    })
    assert find_transitive_violations(fleet, pkg) == [
        "agent_takt_fleet/reader.py -> agent_takt.a -> agent_takt.b -> agent_takt.storage"
    ]


def test_selftest_clean_tree_has_no_violations(tmp_path: Path) -> None:
    fleet, pkg = _make_tree(tmp_path, {
        **_PKG_INIT, **_STORAGE,
        "agent_takt/models.py": "import os\n",
        "agent_takt_fleet/reader.py": "import agent_takt.models\nimport json\n",
    })
    assert find_transitive_violations(fleet, pkg) == []


def test_selftest_real_tree_audit_reaches_storage(tmp_path: Path) -> None:
    """Against the real agent_takt sources: importing agent_takt.audit is flagged
    (it imports agent_takt.storage), while a pure helper is not."""
    fleet = tmp_path / "agent_takt_fleet"
    fleet.mkdir()
    (fleet / "bad.py").write_text("from agent_takt import audit\n", encoding="utf-8")
    (fleet / "good.py").write_text(
        "from agent_takt.bead_metrics import bead_cost_usd\n", encoding="utf-8"
    )
    violations = find_transitive_violations(fleet, AGENT_TAKT_SRC)
    assert violations, "importing agent_takt.audit must be flagged"
    assert all(v.startswith("agent_takt_fleet/bad.py -> ") for v in violations)
    assert any(v.endswith("agent_takt.storage") for v in violations)
