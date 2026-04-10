from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ._assets import packaged_agents_skills_dir, packaged_skill_templates_dir
from .config import OrchestratorConfig
from .models import Bead
from .prompts import load_guardrail_template


# Intentionally not externalized to config. The skill allowlist is tightly coupled
# to the skill directory structure and guardrail templates; externalizing it to YAML
# would require also externalizing the skill catalog, which is a separate concern.
AGENT_SKILL_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "planner": (
        "core/base-orchestrator",
        "role/planner-decomposition",
        "task/spec-intake",
        "task/dependency-graphing",
        "memory",
    ),
    "developer": (
        "core/base-orchestrator",
        "role/developer-implementation",
        "capability/code-edit",
        "task/corrective-implementation",
        "task/refactor-safe",
        "task/migration",
        "memory",
    ),
    "tester": (
        "core/base-orchestrator",
        "role/tester-validation",
        "capability/test-execution",
        "task/defect-bead-creation",
        "task/regression-triage",
        "memory",
    ),
    "review": (
        "core/base-orchestrator",
        "role/reviewer-signoff",
        "capability/code-review",
        "task/corrective-bead-creation",
        "task/risk-assessment",
        "memory",
    ),
    "documentation": (
        "core/base-orchestrator",
        "role/docs-agent",
        "capability/docs-edit",
        "task/release-notes",
        "task/spec-sync",
        "memory",
    ),
    "scheduler": (
        "core/base-orchestrator",
        "role/scheduler-policy",
    ),
    "recovery": (
        "core/base-orchestrator",
    ),
    "investigator": (
        "core/base-orchestrator",
        "role/investigator",
        "memory",
    ),
}


def allowed_skill_ids(agent_type: str) -> list[str]:
    return list(AGENT_SKILL_ALLOWLIST.get(agent_type, ()))


def _skills_root(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills"


def _template_skills_root(repo_root: Path) -> Path:
    return repo_root / "templates" / "skills"


def _skill_path(repo_root: Path, skill_id: str) -> Path:
    # Subagent-only Codex skills now live under templates/skills. Keep
    # .agents/skills as the fallback for operator-facing exceptions.
    template_path = _template_skills_root(repo_root) / skill_id
    if template_path.is_dir():
        return template_path

    project_path = _skills_root(repo_root) / skill_id
    if project_path.is_dir():
        return project_path

    bundled_template_path = packaged_skill_templates_dir() / skill_id
    if bundled_template_path.is_dir():
        return bundled_template_path

    return packaged_agents_skills_dir() / skill_id


def _read_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _bundle_hash(repo_root: Path, skill_ids: list[str]) -> str:
    payload: dict[str, dict[str, str]] = {}
    for skill_id in skill_ids:
        base = _skill_path(repo_root, skill_id)
        payload[skill_id] = {
            "skill_md": _read_if_exists(base / "SKILL.md"),
            "openai_yaml": _read_if_exists(base / "agents" / "openai.yaml"),
        }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def prepare_isolated_execution_root(
    *,
    orchestrator_state_dir: Path,
    catalog_repo_root: Path,
    workspace_repo_root: Path,
    bead: Bead,
    config: OrchestratorConfig,
    runner_backend: str = "codex",
) -> tuple[Path, dict[str, object]]:
    skill_ids = allowed_skill_ids(bead.agent_type)
    if not skill_ids:
        raise RuntimeError(f"No skills configured for agent type: {bead.agent_type}")

    skills_parent = config.backend(runner_backend).skills_dir
    exec_root = orchestrator_state_dir / "agent-runs" / bead.bead_id
    skills_root = exec_root / skills_parent / "skills"
    repo_link = exec_root / "repo"
    home_dir = exec_root / "home"

    if skills_root.exists():
        shutil.rmtree(skills_root)
    skills_root.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)

    for skill_id in skill_ids:
        source = _skill_path(catalog_repo_root, skill_id)
        if not source.is_dir():
            raise FileNotFoundError(f"Missing required skill directory: {source}")
        target = skills_root / skill_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)

    if repo_link.is_symlink() or repo_link.exists():
        repo_link.unlink()
    repo_link.symlink_to(workspace_repo_root, target_is_directory=True)

    # For Claude Code, generate a CLAUDE.md from the guardrail template so
    # the agent picks up role-specific steering natively from the execution root.
    if runner_backend == "claude":
        try:
            _, guardrail_text = load_guardrail_template(
                bead.agent_type,
                root=catalog_repo_root,
                templates_dir=config.templates_dir,
                agent_types=config.agent_types,
            )
            (exec_root / "CLAUDE.md").write_text(guardrail_text + "\n", encoding="utf-8")
        except FileNotFoundError:
            pass  # No guardrail template available; proceed without CLAUDE.md

    metadata: dict[str, object] = {
        "execution_root": str(exec_root),
        "workspace_repo_path": "repo",
        "loaded_skills": skill_ids,
        "skill_bundle_hash": _bundle_hash(catalog_repo_root, skill_ids),
        "isolated_home": str(home_dir),
        "runner_backend": runner_backend,
    }
    return exec_root, metadata
