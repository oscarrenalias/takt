from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SchedulerConfig:
    lease_timeout_minutes: int = 30
    max_corrective_attempts: int = 2
    corrective_suffix: str = "corrective"
    followup_suffixes: dict[str, str] = field(default_factory=lambda: {
        "tester": "test",
        "documentation": "docs",
        "review": "review",
    })
    transient_block_patterns: tuple[str, ...] = (
        "high demand",
        "internal server error",
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "temporarily unavailable",
        "service unavailable",
        "missing bearer",
        "unauthorized",
    )


@dataclass(frozen=True)
class BackendConfig:
    binary: str = ""
    skills_dir: str = ""
    flags: list[str] = field(default_factory=list)
    allowed_tools_default: list[str] = field(default_factory=list)
    allowed_tools_by_agent: dict[str, list[str]] = field(default_factory=dict)
    model_default: str | None = None
    model_by_agent: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 600
    retry_timeout_seconds: int = 300


@dataclass(frozen=True)
class OrchestratorConfig:
    default_runner: str = "codex"
    templates_dir: str = "templates/agents"
    agent_types: list[str] = field(default_factory=lambda: [
        "planner", "developer", "tester", "documentation", "review",
    ])
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    backends: dict[str, BackendConfig] = field(default_factory=dict)

    def backend(self, name: str) -> BackendConfig:
        try:
            return self.backends[name]
        except KeyError:
            valid = ", ".join(sorted(self.backends))
            raise KeyError(
                f"Unknown backend {name!r}. Valid backends: {valid}"
            ) from None

    def model_for(self, backend: str, agent_type: str) -> str | None:
        cfg = self.backend(backend)
        return cfg.model_by_agent.get(agent_type, cfg.model_default)

    def allowed_tools_for(self, backend: str, agent_type: str) -> list[str]:
        cfg = self.backend(backend)
        if not cfg.allowed_tools_default:
            return []
        merged = list(cfg.allowed_tools_default)
        for tool in cfg.allowed_tools_by_agent.get(agent_type, []):
            if tool not in merged:
                merged.append(tool)
        return merged


def default_config() -> OrchestratorConfig:
    return OrchestratorConfig(
        default_runner="codex",
        templates_dir="templates/agents",
        agent_types=["planner", "developer", "tester", "documentation", "review"],
        scheduler=SchedulerConfig(
            lease_timeout_minutes=30,
            max_corrective_attempts=2,
            corrective_suffix="corrective",
            followup_suffixes={
                "tester": "test",
                "documentation": "docs",
                "review": "review",
            },
            transient_block_patterns=(
                "high demand",
                "internal server error",
                "timeout",
                "timed out",
                "connection reset",
                "connection refused",
                "temporarily unavailable",
                "service unavailable",
                "missing bearer",
                "unauthorized",
            ),
        ),
        backends={
            "codex": BackendConfig(
                binary="codex",
                skills_dir=".agents",
                flags=["--skip-git-repo-check", "--full-auto", "--color", "never"],
            ),
            "claude": BackendConfig(
                binary="claude",
                skills_dir=".claude",
                flags=["--dangerously-skip-permissions"],
                allowed_tools_default=[
                    "Edit", "Write", "Read", "Bash", "Glob", "Grep",
                    "Skill", "ToolSearch", "WebSearch", "WebFetch",
                ],
                allowed_tools_by_agent={
                    "developer": [
                        "Agent", "NotebookEdit",
                        "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                    ],
                    "tester": [
                        "Agent",
                        "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
                    ],
                    "planner": [],
                    "review": [],
                    "documentation": ["NotebookEdit"],
                },
                model_default="claude-sonnet-4-6",
                model_by_agent={
                    "developer": "claude-sonnet-4-6",
                    "tester": "claude-sonnet-4-6",
                    "planner": "claude-sonnet-4-6",
                    "review": "claude-haiku-4-5-20251001",
                    "documentation": "claude-haiku-4-5-20251001",
                },
            ),
        },
    )


def _build_scheduler(raw: dict) -> SchedulerConfig:
    sched = raw.get("scheduler", {})
    kwargs: dict = {}
    if "lease_timeout_minutes" in sched:
        kwargs["lease_timeout_minutes"] = sched["lease_timeout_minutes"]
    if "max_corrective_attempts" in sched:
        kwargs["max_corrective_attempts"] = sched["max_corrective_attempts"]
    if "corrective_suffix" in sched:
        kwargs["corrective_suffix"] = sched["corrective_suffix"]
    if "followup_suffixes" in sched:
        kwargs["followup_suffixes"] = dict(sched["followup_suffixes"])
    if "transient_block_patterns" in sched:
        kwargs["transient_block_patterns"] = tuple(sched["transient_block_patterns"])
    defaults = SchedulerConfig()
    return SchedulerConfig(
        lease_timeout_minutes=kwargs.get("lease_timeout_minutes", defaults.lease_timeout_minutes),
        max_corrective_attempts=kwargs.get("max_corrective_attempts", defaults.max_corrective_attempts),
        corrective_suffix=kwargs.get("corrective_suffix", defaults.corrective_suffix),
        followup_suffixes=kwargs.get("followup_suffixes", dict(defaults.followup_suffixes)),
        transient_block_patterns=kwargs.get("transient_block_patterns", defaults.transient_block_patterns),
    )


def _build_backend(raw: dict) -> BackendConfig:
    defaults = BackendConfig()
    return BackendConfig(
        binary=raw.get("binary", ""),
        skills_dir=raw.get("skills_dir", ""),
        flags=list(raw.get("flags", [])),
        allowed_tools_default=list(raw.get("allowed_tools_default", [])),
        allowed_tools_by_agent={
            k: list(v) for k, v in raw.get("allowed_tools_by_agent", {}).items()
        },
        model_default=raw.get("model_default"),
        model_by_agent=dict(raw.get("model_by_agent", {})),
        timeout_seconds=raw.get("timeout_seconds", defaults.timeout_seconds),
        retry_timeout_seconds=raw.get("retry_timeout_seconds", defaults.retry_timeout_seconds),
    )


def load_config(root: Path) -> OrchestratorConfig:
    config_path = root / ".orchestrator" / "config.yaml"
    if not config_path.is_file():
        return default_config()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return default_config()

    common = raw.get("common", {})
    defaults = default_config()

    scheduler_raw = common if common else {}
    scheduler = _build_scheduler(scheduler_raw)

    backends: dict[str, BackendConfig] = {}
    for name in ("codex", "claude"):
        if name in raw:
            backends[name] = _build_backend(raw[name])
        elif name in defaults.backends:
            backends[name] = defaults.backends[name]

    return OrchestratorConfig(
        default_runner=common.get("default_runner", defaults.default_runner),
        templates_dir=common.get("templates_dir", defaults.templates_dir),
        agent_types=list(common.get("agent_types", defaults.agent_types)),
        scheduler=scheduler,
        backends=backends,
    )
