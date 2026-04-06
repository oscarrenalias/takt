from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.config import (
    BackendConfig,
    OrchestratorConfig,
    SchedulerConfig,
    default_config,
    load_config,
)


class TestDefaultConfig(unittest.TestCase):
    """Verify default_config() returns values matching the hardcoded constants."""

    def setUp(self):
        self.cfg = default_config()

    def test_default_runner(self):
        self.assertEqual(self.cfg.default_runner, "codex")

    def test_templates_dir(self):
        self.assertEqual(self.cfg.templates_dir, "templates/agents")

    def test_agent_types(self):
        self.assertEqual(
            self.cfg.agent_types,
            ["planner", "developer", "tester", "documentation", "review"],
        )

    def test_scheduler_lease_timeout(self):
        self.assertEqual(self.cfg.scheduler.lease_timeout_minutes, 30)

    def test_scheduler_max_corrective(self):
        self.assertEqual(self.cfg.scheduler.max_corrective_attempts, 2)

    def test_scheduler_corrective_suffix(self):
        self.assertEqual(self.cfg.scheduler.corrective_suffix, "corrective")

    def test_scheduler_followup_suffixes(self):
        self.assertEqual(self.cfg.scheduler.followup_suffixes, {
            "tester": "test",
            "documentation": "docs",
            "review": "review",
        })

    def test_scheduler_transient_patterns(self):
        expected = (
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
        self.assertEqual(self.cfg.scheduler.transient_block_patterns, expected)

    def test_codex_backend(self):
        codex = self.cfg.backend("codex")
        self.assertEqual(codex.binary, "codex")
        self.assertEqual(codex.skills_dir, ".agents")
        self.assertEqual(
            codex.flags,
            ["--skip-git-repo-check", "--full-auto", "--color", "never"],
        )
        self.assertEqual(codex.allowed_tools_default, [])
        self.assertEqual(codex.allowed_tools_by_agent, {})

    def test_claude_backend(self):
        claude = self.cfg.backend("claude")
        self.assertEqual(claude.binary, "claude")
        self.assertEqual(claude.skills_dir, ".claude")
        self.assertEqual(claude.flags, ["--dangerously-skip-permissions"])
        self.assertEqual(claude.allowed_tools_default, [
            "Edit", "Write", "Read", "Bash", "Glob", "Grep",
            "Skill", "ToolSearch", "WebSearch", "WebFetch",
        ])

    def test_claude_allowed_tools_by_agent(self):
        claude = self.cfg.backend("claude")
        self.assertEqual(claude.allowed_tools_by_agent["developer"], [
            "Agent", "NotebookEdit",
            "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
        ])
        self.assertEqual(claude.allowed_tools_by_agent["tester"], [
            "Agent",
            "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
        ])
        self.assertEqual(claude.allowed_tools_by_agent["planner"], [])
        self.assertEqual(claude.allowed_tools_by_agent["review"], [])
        self.assertEqual(claude.allowed_tools_by_agent["documentation"], [
            "NotebookEdit",
        ])


class TestBackendTimeoutDefaults(unittest.TestCase):
    """Verify default timeout values in BackendConfig and default_config()."""

    def test_backend_config_default_timeout(self):
        b = BackendConfig()
        self.assertEqual(b.timeout_seconds, 600)

    def test_backend_config_default_retry_timeout(self):
        b = BackendConfig()
        self.assertEqual(b.retry_timeout_seconds, 300)

    def test_default_config_codex_timeout(self):
        cfg = default_config()
        codex = cfg.backend("codex")
        self.assertEqual(codex.timeout_seconds, 600)
        self.assertEqual(codex.retry_timeout_seconds, 300)

    def test_default_config_claude_timeout(self):
        cfg = default_config()
        claude = cfg.backend("claude")
        self.assertEqual(claude.timeout_seconds, 600)
        self.assertEqual(claude.retry_timeout_seconds, 300)

    def test_timeout_frozen(self):
        b = BackendConfig()
        with self.assertRaises(FrozenInstanceError):
            b.timeout_seconds = 999
        with self.assertRaises(FrozenInstanceError):
            b.retry_timeout_seconds = 999


class TestBackendLookup(unittest.TestCase):
    """Test OrchestratorConfig.backend() method."""

    def setUp(self):
        self.cfg = default_config()

    def test_backend_codex(self):
        b = self.cfg.backend("codex")
        self.assertIsInstance(b, BackendConfig)
        self.assertEqual(b.binary, "codex")

    def test_backend_claude(self):
        b = self.cfg.backend("claude")
        self.assertIsInstance(b, BackendConfig)
        self.assertEqual(b.binary, "claude")

    def test_backend_nonexistent_raises(self):
        with self.assertRaises(KeyError) as ctx:
            self.cfg.backend("nonexistent")
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("Valid backends", str(ctx.exception))


class TestAllowedToolsFor(unittest.TestCase):
    """Test OrchestratorConfig.allowed_tools_for() merge logic."""

    def setUp(self):
        self.cfg = default_config()

    def test_claude_developer_merged(self):
        tools = self.cfg.allowed_tools_for("claude", "developer")
        # Should contain all defaults + developer-specific
        for t in ["Edit", "Write", "Read", "Bash", "Glob", "Grep",
                   "Skill", "ToolSearch", "WebSearch", "WebFetch",
                   "Agent", "NotebookEdit",
                   "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"]:
            self.assertIn(t, tools)

    def test_claude_developer_deduplicated(self):
        tools = self.cfg.allowed_tools_for("claude", "developer")
        self.assertEqual(len(tools), len(set(tools)))

    def test_claude_planner_only_defaults(self):
        tools = self.cfg.allowed_tools_for("claude", "planner")
        expected = [
            "Edit", "Write", "Read", "Bash", "Glob", "Grep",
            "Skill", "ToolSearch", "WebSearch", "WebFetch",
        ]
        self.assertEqual(tools, expected)

    def test_claude_unknown_agent_returns_defaults(self):
        tools = self.cfg.allowed_tools_for("claude", "unknown_agent")
        expected = [
            "Edit", "Write", "Read", "Bash", "Glob", "Grep",
            "Skill", "ToolSearch", "WebSearch", "WebFetch",
        ]
        self.assertEqual(tools, expected)

    def test_codex_returns_empty(self):
        tools = self.cfg.allowed_tools_for("codex", "developer")
        self.assertEqual(tools, [])

    def test_codex_any_agent_empty(self):
        for agent in ["developer", "tester", "planner", "review", "documentation"]:
            tools = self.cfg.allowed_tools_for("codex", agent)
            self.assertEqual(tools, [], f"Expected empty for codex/{agent}")

    def test_nonexistent_backend_raises(self):
        with self.assertRaises(KeyError):
            self.cfg.allowed_tools_for("nonexistent", "developer")


class TestFrozenDataclasses(unittest.TestCase):
    """Verify config dataclasses are immutable."""

    def test_orchestrator_config_frozen(self):
        cfg = default_config()
        with self.assertRaises(FrozenInstanceError):
            cfg.default_runner = "claude"

    def test_scheduler_config_frozen(self):
        cfg = default_config()
        with self.assertRaises(FrozenInstanceError):
            cfg.scheduler.lease_timeout_minutes = 60

    def test_backend_config_frozen(self):
        cfg = default_config()
        b = cfg.backend("codex")
        with self.assertRaises(FrozenInstanceError):
            b.binary = "other"


class TestLoadConfigMissingFile(unittest.TestCase):
    """load_config() falls back to default_config() when no file exists."""

    def test_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(Path(tmp))
            default = default_config()
            self.assertEqual(cfg.default_runner, default.default_runner)
            self.assertEqual(cfg.templates_dir, default.templates_dir)
            self.assertEqual(cfg.agent_types, default.agent_types)
            self.assertEqual(
                cfg.scheduler.lease_timeout_minutes,
                default.scheduler.lease_timeout_minutes,
            )
            self.assertEqual(
                cfg.scheduler.transient_block_patterns,
                default.scheduler.transient_block_patterns,
            )
            self.assertEqual(
                cfg.backend("codex").binary,
                default.backend("codex").binary,
            )
            self.assertEqual(
                cfg.backend("claude").allowed_tools_default,
                default.backend("claude").allowed_tools_default,
            )


class TestLoadConfigFromYAML(unittest.TestCase):
    """load_config() correctly reads a YAML file."""

    def _write_config(self, tmp: Path, yaml_text: str):
        orch_dir = tmp / ".orchestrator"
        orch_dir.mkdir(parents=True, exist_ok=True)
        (orch_dir / "config.yaml").write_text(textwrap.dedent(yaml_text))

    def test_load_full_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                common:
                  default_runner: claude
                  templates_dir: custom/templates
                  agent_types:
                    - developer
                    - tester
                  scheduler:
                    lease_timeout_minutes: 60
                    max_corrective_attempts: 5
                    corrective_suffix: retry
                    followup_suffixes:
                      tester: testing
                    transient_block_patterns:
                      - "custom error"

                codex:
                  binary: /usr/local/bin/codex
                  skills_dir: ".custom-agents"
                  flags:
                    - "--full-auto"

                claude:
                  binary: /usr/local/bin/claude
                  skills_dir: ".custom-claude"
                  flags:
                    - "--skip-permissions"
                  allowed_tools_default:
                    - Read
                    - Write
                  allowed_tools_by_agent:
                    developer:
                      - Agent
            """)
            cfg = load_config(Path(tmp))
            self.assertEqual(cfg.default_runner, "claude")
            self.assertEqual(cfg.templates_dir, "custom/templates")
            self.assertEqual(cfg.agent_types, ["developer", "tester"])
            self.assertEqual(cfg.scheduler.lease_timeout_minutes, 60)
            self.assertEqual(cfg.scheduler.max_corrective_attempts, 5)
            self.assertEqual(cfg.scheduler.corrective_suffix, "retry")
            self.assertEqual(cfg.scheduler.followup_suffixes, {"tester": "testing"})
            self.assertEqual(cfg.scheduler.transient_block_patterns, ("custom error",))

            codex = cfg.backend("codex")
            self.assertEqual(codex.binary, "/usr/local/bin/codex")
            self.assertEqual(codex.skills_dir, ".custom-agents")
            self.assertEqual(codex.flags, ["--full-auto"])

            claude = cfg.backend("claude")
            self.assertEqual(claude.binary, "/usr/local/bin/claude")
            self.assertEqual(claude.skills_dir, ".custom-claude")
            self.assertEqual(claude.flags, ["--skip-permissions"])
            self.assertEqual(claude.allowed_tools_default, ["Read", "Write"])
            self.assertEqual(claude.allowed_tools_by_agent, {"developer": ["Agent"]})

    def test_load_timeout_overrides(self):
        """timeout_seconds and retry_timeout_seconds are loaded from YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                codex:
                  binary: codex
                  timeout_seconds: 1200
                  retry_timeout_seconds: 120

                claude:
                  binary: claude
                  timeout_seconds: 900
                  retry_timeout_seconds: 60
            """)
            cfg = load_config(Path(tmp))
            codex = cfg.backend("codex")
            self.assertEqual(codex.timeout_seconds, 1200)
            self.assertEqual(codex.retry_timeout_seconds, 120)
            claude = cfg.backend("claude")
            self.assertEqual(claude.timeout_seconds, 900)
            self.assertEqual(claude.retry_timeout_seconds, 60)

    def test_load_timeout_defaults_when_omitted(self):
        """Omitting timeout fields in YAML falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                codex:
                  binary: codex
            """)
            cfg = load_config(Path(tmp))
            codex = cfg.backend("codex")
            self.assertEqual(codex.timeout_seconds, 600)
            self.assertEqual(codex.retry_timeout_seconds, 300)

    def test_load_partial_config_falls_back(self):
        """Partial YAML should fill missing fields from defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), """\
                common:
                  default_runner: claude
            """)
            cfg = load_config(Path(tmp))
            self.assertEqual(cfg.default_runner, "claude")
            # templates_dir should fall back to default
            self.assertEqual(cfg.templates_dir, "templates/agents")
            # backends should fall back to defaults
            self.assertEqual(cfg.backend("codex").binary, "codex")
            self.assertEqual(
                cfg.backend("claude").allowed_tools_default,
                default_config().backend("claude").allowed_tools_default,
            )

    def test_malformed_yaml_not_dict(self):
        """Non-dict YAML should fall back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_config(Path(tmp), "just a string\n")
            cfg = load_config(Path(tmp))
            self.assertEqual(cfg.default_runner, default_config().default_runner)

    def test_empty_yaml_file(self):
        """Empty YAML file (parses as None) falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            orch_dir = Path(tmp) / ".orchestrator"
            orch_dir.mkdir(parents=True)
            (orch_dir / "config.yaml").write_text("")
            cfg = load_config(Path(tmp))
            self.assertEqual(cfg.default_runner, default_config().default_runner)


class TestLoadConfigFromRepo(unittest.TestCase):
    """Load config from the actual repo config.yaml and verify it matches defaults."""

    def test_repo_config_matches_defaults(self):
        # This test only checks structural/non-tunable fields.
        # Operator-tunable fields (max_corrective_attempts, timeout_seconds,
        # retry_timeout_seconds, transient_block_patterns) are intentionally
        # overridden in .orchestrator/config.yaml and are NOT asserted here.
        cfg = load_config(REPO_ROOT)
        default = default_config()
        self.assertEqual(cfg.default_runner, default.default_runner)
        self.assertEqual(cfg.templates_dir, default.templates_dir)
        self.assertEqual(cfg.agent_types, default.agent_types)
        self.assertEqual(
            cfg.scheduler.lease_timeout_minutes,
            default.scheduler.lease_timeout_minutes,
        )
        self.assertEqual(
            cfg.scheduler.corrective_suffix,
            default.scheduler.corrective_suffix,
        )
        self.assertEqual(
            cfg.scheduler.followup_suffixes,
            default.scheduler.followup_suffixes,
        )
        # transient_block_patterns is intentionally extended in .orchestrator/config.yaml
        # Check backends loaded from YAML match defaults
        for name in ("codex", "claude"):
            self.assertEqual(
                cfg.backend(name).binary,
                default.backend(name).binary,
                f"binary mismatch for {name}",
            )
            self.assertEqual(
                cfg.backend(name).skills_dir,
                default.backend(name).skills_dir,
                f"skills_dir mismatch for {name}",
            )
            self.assertEqual(
                cfg.backend(name).flags,
                default.backend(name).flags,
                f"flags mismatch for {name}",
            )
            self.assertEqual(
                cfg.backend(name).allowed_tools_default,
                default.backend(name).allowed_tools_default,
                f"allowed_tools_default mismatch for {name}",
            )
            self.assertEqual(
                cfg.backend(name).allowed_tools_by_agent,
                default.backend(name).allowed_tools_by_agent,
                f"allowed_tools_by_agent mismatch for {name}",
            )
            # timeout_seconds and retry_timeout_seconds are operator-tunable;
            # not asserted here.


class TestAllowedToolsMergeOrder(unittest.TestCase):
    """Ensure merge preserves order: defaults first, then per-agent additions."""

    def test_developer_order(self):
        cfg = default_config()
        tools = cfg.allowed_tools_for("claude", "developer")
        # defaults should appear first in order
        defaults = cfg.backend("claude").allowed_tools_default
        self.assertEqual(tools[:len(defaults)], defaults)
        # agent-specific tools after
        extra = cfg.backend("claude").allowed_tools_by_agent["developer"]
        for t in extra:
            self.assertIn(t, tools[len(defaults):])


if __name__ == "__main__":
    unittest.main()
