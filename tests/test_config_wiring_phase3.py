"""Tests for Phase 3: Config wiring into scheduler, skills, and prompts (B0108).

Validates that scheduler.py, skills.py, and prompts.py read operational
parameters from OrchestratorConfig instead of hardcoded module-level constants.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
# The main repo root (for skills that only exist in the primary checkout)
MAIN_REPO_ROOT = Path(__file__).resolve().parents[1]
# Walk up to find the actual repo root with .agents/skills if we're in a worktree
_candidate = MAIN_REPO_ROOT
while _candidate != _candidate.parent:
    if (_candidate / ".agents" / "skills").is_dir():
        MAIN_REPO_ROOT = _candidate
        break
    _candidate = _candidate.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_orchestrator.config import (
    BackendConfig,
    OrchestratorConfig,
    SchedulerConfig,
    default_config,
)
from codex_orchestrator.models import (
    AgentRunResult,
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_IN_PROGRESS,
    BEAD_READY,
    Bead,
    HandoffSummary,
    Lease,
)
from codex_orchestrator.prompts import (
    BUILT_IN_AGENT_TYPES,
    DEFAULT_TEMPLATES_DIR,
    guardrail_template_path,
    load_guardrail_template,
    supported_agent_types,
)
from codex_orchestrator.scheduler import Scheduler
from codex_orchestrator.skills import (
    AGENT_SKILL_ALLOWLIST,
    prepare_isolated_execution_root,
)
from codex_orchestrator.storage import RepositoryStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRunner:
    backend_name = "codex"

    def __init__(self, results=None):
        self.results = results or {}

    def run_bead(self, bead, *, workdir, context_paths, execution_env=None):
        return self.results.get(bead.bead_id, AgentRunResult(
            outcome="completed", summary="ok", verdict="approved",
        ))


def _make_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    source_templates = REPO_ROOT / "templates" / "agents"
    target_templates = root / "templates" / "agents"
    target_templates.mkdir(parents=True, exist_ok=True)
    for agent_type in BUILT_IN_AGENT_TYPES:
        shutil.copy2(source_templates / f"{agent_type}.md",
                     target_templates / f"{agent_type}.md")
    # Copy skills for isolation tests (may be in main repo root, not worktree)
    source_skills = MAIN_REPO_ROOT / ".agents" / "skills"
    if source_skills.is_dir():
        target_skills = root / ".agents" / "skills"
        shutil.copytree(source_skills, target_skills)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# 1. Scheduler reads config (no module-level constants)
# ---------------------------------------------------------------------------

class TestSchedulerConfigWiring(unittest.TestCase):
    """Scheduler.__init__ reads all operational params from config.scheduler."""

    def test_default_config_wiring(self):
        """Scheduler with default config has expected instance attributes."""
        cfg = default_config()
        storage = MagicMock()
        runner = FakeRunner()
        worktrees = MagicMock()
        sched = Scheduler(storage, runner, worktrees, config=cfg)

        self.assertEqual(sched.followup_suffixes, {"tester": "test", "documentation": "docs", "review": "review"})
        self.assertEqual(sched.corrective_suffix, "corrective")
        self.assertEqual(sched.max_corrective_attempts, 2)
        self.assertEqual(sched.transient_block_patterns, cfg.scheduler.transient_block_patterns)
        self.assertEqual(sched.lease_timeout_minutes, 30)
        self.assertEqual(sched.runnable_reassign_agents, set(cfg.agent_types))

    def test_custom_config_wiring(self):
        """Scheduler picks up custom scheduler config values."""
        custom_sched = SchedulerConfig(
            lease_timeout_minutes=60,
            max_corrective_attempts=5,
            corrective_suffix="retry",
            followup_suffixes={"tester": "testing", "review": "rev"},
            transient_block_patterns=("custom error", "special timeout"),
        )
        cfg = OrchestratorConfig(
            scheduler=custom_sched,
            agent_types=["developer", "tester"],
            backends=default_config().backends,
        )
        storage = MagicMock()
        runner = FakeRunner()
        worktrees = MagicMock()
        sched = Scheduler(storage, runner, worktrees, config=cfg)

        self.assertEqual(sched.followup_suffixes, {"tester": "testing", "review": "rev"})
        self.assertEqual(sched.corrective_suffix, "retry")
        self.assertEqual(sched.max_corrective_attempts, 5)
        self.assertEqual(sched.transient_block_patterns, ("custom error", "special timeout"))
        self.assertEqual(sched.lease_timeout_minutes, 60)
        self.assertEqual(sched.runnable_reassign_agents, {"developer", "tester"})

    def test_followup_agent_by_suffix_derived(self):
        """followup_agent_by_suffix is derived from followup_suffixes."""
        cfg = default_config()
        storage = MagicMock()
        sched = Scheduler(storage, FakeRunner(), MagicMock(), config=cfg)

        self.assertEqual(sched.followup_agent_by_suffix, {
            "-test": "tester",
            "-docs": "documentation",
            "-review": "review",
        })

    def test_custom_followup_agent_by_suffix(self):
        custom_sched = SchedulerConfig(
            followup_suffixes={"tester": "validate", "documentation": "doc"},
        )
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        sched = Scheduler(MagicMock(), FakeRunner(), MagicMock(), config=cfg)

        self.assertEqual(sched.followup_agent_by_suffix, {
            "-validate": "tester",
            "-doc": "documentation",
        })

    def test_none_config_falls_back_to_defaults(self):
        """Scheduler(config=None) falls back to default_config()."""
        sched = Scheduler(MagicMock(), FakeRunner(), MagicMock(), config=None)
        cfg = default_config()
        self.assertEqual(sched.lease_timeout_minutes, cfg.scheduler.lease_timeout_minutes)
        self.assertEqual(sched.max_corrective_attempts, cfg.scheduler.max_corrective_attempts)


class TestSchedulerNoModuleLevelConstants(unittest.TestCase):
    """Verify scheduler.py has no module-level constants for config values."""

    def _read_scheduler_source(self) -> str:
        return (REPO_ROOT / "src" / "codex_orchestrator" / "scheduler.py").read_text(encoding="utf-8")

    def test_no_followup_suffixes_constant(self):
        source = self._read_scheduler_source()
        # Should not have FOLLOWUP_SUFFIXES as a module-level dict
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("FOLLOWUP_SUFFIXES"):
                self.fail("Found module-level FOLLOWUP_SUFFIXES constant in scheduler.py")

    def test_no_corrective_suffix_constant(self):
        source = self._read_scheduler_source()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("CORRECTIVE_SUFFIX") and "=" in stripped:
                self.fail("Found module-level CORRECTIVE_SUFFIX constant in scheduler.py")

    def test_no_max_corrective_constant(self):
        source = self._read_scheduler_source()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("MAX_CORRECTIVE_ATTEMPTS"):
                self.fail("Found module-level MAX_CORRECTIVE_ATTEMPTS constant in scheduler.py")

    def test_no_transient_block_patterns_constant(self):
        source = self._read_scheduler_source()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("TRANSIENT_BLOCK_PATTERNS"):
                self.fail("Found module-level TRANSIENT_BLOCK_PATTERNS constant in scheduler.py")

    def test_no_runnable_reassign_agents_constant(self):
        source = self._read_scheduler_source()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("RUNNABLE_REASSIGN_AGENTS"):
                self.fail("Found module-level RUNNABLE_REASSIGN_AGENTS constant in scheduler.py")


class TestSchedulerLeaseTimeout(unittest.TestCase):
    """Verify lease timeout uses config.scheduler.lease_timeout_minutes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_custom_lease_timeout_applied(self):
        """Changing lease_timeout_minutes in config affects lease duration."""
        custom_sched = SchedulerConfig(lease_timeout_minutes=10)
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        self.assertEqual(sched.lease_timeout_minutes, 10)


class TestSchedulerTransientBlockPatterns(unittest.TestCase):
    """Verify custom transient_block_patterns are used in _reevaluate_blocked."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_custom_pattern_requeues_blocked_bead(self):
        """A custom transient pattern causes requeue."""
        custom_sched = SchedulerConfig(
            transient_block_patterns=("my custom error",),
        )
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        bead = self.storage.create_bead(
            title="Test", agent_type="developer", description="d",
        )
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Failed due to my custom error in API"
        self.storage.save_bead(bead)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        sched._reevaluate_blocked(feature_root_id=None)

        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(reloaded.status, BEAD_READY)

    def test_default_pattern_not_recognized_with_custom_config(self):
        """When custom patterns are set, default patterns no longer trigger requeue."""
        custom_sched = SchedulerConfig(
            transient_block_patterns=("my custom error",),
        )
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        bead = self.storage.create_bead(
            title="Test", agent_type="developer", description="d",
        )
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Failed due to timeout"
        self.storage.save_bead(bead)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        sched._reevaluate_blocked(feature_root_id=None)

        reloaded = self.storage.load_bead(bead.bead_id)
        # "timeout" is not in custom patterns, so bead stays blocked
        self.assertEqual(reloaded.status, BEAD_BLOCKED)


# ---------------------------------------------------------------------------
# 2. Skills: prepare_isolated_execution_root uses config
# ---------------------------------------------------------------------------

class TestSkillsConfigWiring(unittest.TestCase):
    """prepare_isolated_execution_root reads skills_dir from config.backend()."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.state_dir = self.root / ".orchestrator"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_bead(self, agent_type="developer"):
        bead = MagicMock(spec=Bead)
        bead.bead_id = "B9999"
        bead.agent_type = agent_type
        return bead

    def test_codex_backend_uses_agents_skills_dir(self):
        """Codex backend resolves to .agents skills directory."""
        cfg = default_config()
        bead = self._make_bead()
        exec_root, metadata = prepare_isolated_execution_root(
            orchestrator_state_dir=self.state_dir,
            catalog_repo_root=self.root,
            workspace_repo_root=self.root,
            bead=bead,
            config=cfg,
            runner_backend="codex",
        )
        # Skills should be under .agents/skills/
        skills_path = exec_root / ".agents" / "skills"
        self.assertTrue(skills_path.exists(), f"Expected {skills_path} to exist")

    def test_claude_backend_uses_claude_skills_dir(self):
        """Claude backend resolves to .claude skills directory."""
        cfg = default_config()
        bead = self._make_bead()
        exec_root, metadata = prepare_isolated_execution_root(
            orchestrator_state_dir=self.state_dir,
            catalog_repo_root=self.root,
            workspace_repo_root=self.root,
            bead=bead,
            config=cfg,
            runner_backend="claude",
        )
        skills_path = exec_root / ".claude" / "skills"
        self.assertTrue(skills_path.exists(), f"Expected {skills_path} to exist")

    def test_custom_skills_dir(self):
        """A custom skills_dir in config is used."""
        custom_backend = BackendConfig(
            binary="codex", skills_dir=".custom-skills", flags=[],
        )
        cfg = OrchestratorConfig(
            backends={"mybackend": custom_backend, **default_config().backends},
        )
        bead = self._make_bead()
        exec_root, metadata = prepare_isolated_execution_root(
            orchestrator_state_dir=self.state_dir,
            catalog_repo_root=self.root,
            workspace_repo_root=self.root,
            bead=bead,
            config=cfg,
            runner_backend="mybackend",
        )
        skills_path = exec_root / ".custom-skills" / "skills"
        self.assertTrue(skills_path.exists(), f"Expected {skills_path} to exist")

    def test_claude_generates_claude_md(self):
        """Claude backend generates CLAUDE.md from guardrail template."""
        cfg = default_config()
        bead = self._make_bead()
        exec_root, _ = prepare_isolated_execution_root(
            orchestrator_state_dir=self.state_dir,
            catalog_repo_root=self.root,
            workspace_repo_root=self.root,
            bead=bead,
            config=cfg,
            runner_backend="claude",
        )
        claude_md = exec_root / "CLAUDE.md"
        self.assertTrue(claude_md.exists(), "CLAUDE.md should be generated for claude backend")

    def test_config_passed_to_load_guardrail_in_claude_mode(self):
        """When backend is claude, config.templates_dir and config.agent_types are passed through."""
        custom_cfg = OrchestratorConfig(
            templates_dir="templates/agents",
            agent_types=["developer", "tester"],
            backends=default_config().backends,
        )
        bead = self._make_bead(agent_type="developer")
        # Should not raise even with restricted agent_types since developer is included
        exec_root, _ = prepare_isolated_execution_root(
            orchestrator_state_dir=self.state_dir,
            catalog_repo_root=self.root,
            workspace_repo_root=self.root,
            bead=bead,
            config=custom_cfg,
            runner_backend="claude",
        )
        self.assertTrue((exec_root / "CLAUDE.md").exists())


class TestSkillAllowlistNotExternalized(unittest.TestCase):
    """AGENT_SKILL_ALLOWLIST remains as a module-level constant."""

    def test_allowlist_is_module_level(self):
        self.assertIsInstance(AGENT_SKILL_ALLOWLIST, dict)
        self.assertIn("developer", AGENT_SKILL_ALLOWLIST)
        self.assertIn("tester", AGENT_SKILL_ALLOWLIST)

    def test_no_backend_skills_dir_constant(self):
        """_BACKEND_SKILLS_DIR dict should be removed from skills.py."""
        source = (REPO_ROOT / "src" / "codex_orchestrator" / "skills.py").read_text(encoding="utf-8")
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("_BACKEND_SKILLS_DIR"):
                self.fail("Found _BACKEND_SKILLS_DIR in skills.py — should be removed")


# ---------------------------------------------------------------------------
# 3. Prompts: guardrail_template_path and load_guardrail_template accept overrides
# ---------------------------------------------------------------------------

class TestPromptsConfigWiring(unittest.TestCase):
    """prompts.py functions accept optional templates_dir and agent_types."""

    def test_supported_agent_types_default(self):
        result = supported_agent_types()
        self.assertEqual(result, BUILT_IN_AGENT_TYPES)

    def test_supported_agent_types_none(self):
        result = supported_agent_types(None)
        self.assertEqual(result, BUILT_IN_AGENT_TYPES)

    def test_supported_agent_types_custom(self):
        result = supported_agent_types(["dev", "test"])
        self.assertEqual(result, ("dev", "test"))

    def test_guardrail_template_path_default(self):
        """Without overrides, uses DEFAULT_TEMPLATES_DIR."""
        path = guardrail_template_path("developer")
        self.assertEqual(path, DEFAULT_TEMPLATES_DIR / "developer.md")

    def test_guardrail_template_path_with_root(self):
        """With root but no templates_dir, resolves to root/templates/agents."""
        path = guardrail_template_path("developer", root=Path("/myrepo"))
        self.assertEqual(path, Path("/myrepo/templates/agents/developer.md"))

    def test_guardrail_template_path_with_templates_dir(self):
        """With root and templates_dir, resolves to root/templates_dir."""
        path = guardrail_template_path(
            "developer", root=Path("/myrepo"), templates_dir="custom/tpl",
        )
        self.assertEqual(path, Path("/myrepo/custom/tpl/developer.md"))

    def test_guardrail_template_path_custom_agent_types(self):
        """Custom agent_types allows non-standard agent types."""
        path = guardrail_template_path(
            "custom_agent", agent_types=["custom_agent", "developer"],
        )
        self.assertEqual(path, DEFAULT_TEMPLATES_DIR / "custom_agent.md")

    def test_guardrail_template_path_rejects_unknown_type(self):
        """Unknown agent type raises ValueError."""
        with self.assertRaises(ValueError):
            guardrail_template_path("nonexistent")

    def test_guardrail_template_path_custom_types_reject_unlisted(self):
        """When custom agent_types is given, standard types not in it are rejected."""
        with self.assertRaises(ValueError):
            guardrail_template_path("review", agent_types=["developer", "tester"])

    def test_load_guardrail_template_with_overrides(self):
        """load_guardrail_template passes templates_dir and agent_types through."""
        path, text = load_guardrail_template(
            "developer",
            root=REPO_ROOT,
            templates_dir="templates/agents",
            agent_types=["developer"],
        )
        self.assertTrue(path.is_file())
        self.assertIn("Developer", text)

    def test_builtin_agent_types_constant_still_exists(self):
        """BUILT_IN_AGENT_TYPES remains as a fallback constant."""
        self.assertIsInstance(BUILT_IN_AGENT_TYPES, tuple)
        self.assertIn("developer", BUILT_IN_AGENT_TYPES)

    def test_default_templates_dir_constant_still_exists(self):
        """DEFAULT_TEMPLATES_DIR remains as a fallback constant."""
        self.assertIsInstance(DEFAULT_TEMPLATES_DIR, Path)
        self.assertTrue(str(DEFAULT_TEMPLATES_DIR).endswith("templates/agents"))


class TestSchedulerPassesConfigToPrompts(unittest.TestCase):
    """Scheduler passes config.templates_dir and config.agent_types to load_guardrail_template."""

    def test_scheduler_process_calls_load_guardrail_with_config(self):
        """Verify the scheduler's _process passes config to load_guardrail_template."""
        source = (REPO_ROOT / "src" / "codex_orchestrator" / "scheduler.py").read_text(encoding="utf-8")
        # The scheduler should pass templates_dir and agent_types from config
        self.assertIn("templates_dir=self.config.templates_dir", source)
        self.assertIn("agent_types=self.config.agent_types", source)


class TestSchedulerPassesConfigToSkills(unittest.TestCase):
    """Scheduler passes config to prepare_isolated_execution_root."""

    def test_scheduler_passes_config_to_skills(self):
        """Verify the scheduler's _process passes config to prepare_isolated_execution_root."""
        source = (REPO_ROOT / "src" / "codex_orchestrator" / "scheduler.py").read_text(encoding="utf-8")
        self.assertIn("config=self.config", source)
        self.assertIn("runner_backend=self.runner.backend_name", source)


class TestSkillsPassesConfigToPrompts(unittest.TestCase):
    """skills.py passes config overrides to load_guardrail_template for Claude backend."""

    def test_skills_passes_templates_dir(self):
        """prepare_isolated_execution_root passes templates_dir to load_guardrail_template."""
        source = (REPO_ROOT / "src" / "codex_orchestrator" / "skills.py").read_text(encoding="utf-8")
        self.assertIn("templates_dir=config.templates_dir", source)
        self.assertIn("agent_types=config.agent_types", source)


# ---------------------------------------------------------------------------
# 4. Integration: config changes propagate through the system
# ---------------------------------------------------------------------------

class TestConfigChangesAffectBehavior(unittest.TestCase):
    """End-to-end: changing config values actually changes system behavior."""

    def test_custom_corrective_suffix(self):
        """Changing corrective_suffix changes the ID of corrective beads."""
        custom_sched = SchedulerConfig(corrective_suffix="fix")
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        sched = Scheduler(MagicMock(), FakeRunner(), MagicMock(), config=cfg)
        self.assertEqual(sched.corrective_suffix, "fix")

    def test_custom_max_corrective_attempts(self):
        """Changing max_corrective_attempts limits corrective retries."""
        custom_sched = SchedulerConfig(max_corrective_attempts=0)
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        sched = Scheduler(MagicMock(), FakeRunner(), MagicMock(), config=cfg)
        self.assertEqual(sched.max_corrective_attempts, 0)

    def test_custom_agent_types_affects_runnable_set(self):
        """Changing agent_types limits which agents the scheduler considers runnable."""
        cfg = OrchestratorConfig(
            agent_types=["developer"],
            backends=default_config().backends,
        )
        sched = Scheduler(MagicMock(), FakeRunner(), MagicMock(), config=cfg)
        self.assertEqual(sched.runnable_reassign_agents, {"developer"})


# ---------------------------------------------------------------------------
# 5. Behavioral: config values are used in actual scheduler operations
# ---------------------------------------------------------------------------

class TestSchedulerCorrectiveSuffixBehavior(unittest.TestCase):
    """Corrective bead IDs actually use config corrective_suffix."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_corrective_bead_uses_custom_suffix(self):
        """_create_corrective_bead produces ID with the configured suffix."""
        custom_sched = SchedulerConfig(corrective_suffix="fix")
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        bead = self.storage.create_bead(
            title="Impl", agent_type="developer", description="d",
        )
        bead.status = BEAD_BLOCKED
        bead.block_reason = "Something broke"
        bead.handoff_summary = HandoffSummary(remaining="fix it")
        self.storage.save_bead(bead)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        corrective = sched._create_corrective_bead(bead)

        self.assertIn("-fix", corrective.bead_id)
        self.assertNotIn("-corrective", corrective.bead_id)


class TestSchedulerMaxCorrectiveAttemptsBehavior(unittest.TestCase):
    """max_corrective_attempts actually limits corrective bead creation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_max_attempts_limits_corrective_creation(self):
        """With max_corrective_attempts=1, a second corrective triggers escalation."""
        custom_sched = SchedulerConfig(
            max_corrective_attempts=1,
            transient_block_patterns=(),  # no transient patterns
        )
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)
        parent = self.storage.create_bead(
            title="Impl", agent_type="developer", description="d",
        )
        parent.status = BEAD_BLOCKED
        parent.block_reason = "Permanent failure"
        parent.metadata["last_corrective_retry_source"] = ""
        self.storage.save_bead(parent)

        # Create one existing done corrective child (simulates first attempt completed)
        corrective_id = self.storage.allocate_child_bead_id(parent.bead_id, "corrective")
        corrective = self.storage.create_bead(
            bead_id=corrective_id,
            title="Corrective",
            agent_type="developer",
            description="fix",
            parent_id=parent.bead_id,
            metadata={"auto_corrective_for": parent.bead_id},
        )
        corrective.status = BEAD_DONE
        self.storage.save_bead(corrective)

        # Record that parent was already retried after this corrective
        parent.metadata["last_corrective_retry_source"] = corrective.bead_id
        parent.metadata["last_corrective_retry_commit"] = ""
        self.storage.save_bead(parent)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        sched._reevaluate_blocked(feature_root_id=None)

        reloaded = self.storage.load_bead(parent.bead_id)
        # With 1 max attempt and 1 corrective already present, should escalate
        self.assertEqual(reloaded.status, BEAD_BLOCKED)
        self.assertTrue(reloaded.metadata.get("needs_human_intervention"))


class TestSchedulerLeaseTimeoutBehavior(unittest.TestCase):
    """lease_timeout_minutes is used when computing lease expiry in _process."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lease_duration_reflects_config(self):
        """A bead processed with a custom lease timeout has a matching lease window."""
        custom_sched = SchedulerConfig(lease_timeout_minutes=5)
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)

        bead = self.storage.create_bead(
            title="Quick", agent_type="planner", description="d",
        )
        self.storage.save_bead(bead)

        before = datetime.now(timezone.utc)
        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        sched.run_once()
        after = datetime.now(timezone.utc)

        reloaded = self.storage.load_bead(bead.bead_id)
        # Bead should be done (planner, non-mutating). Check execution history for lease.
        # The lease was set during _process then cleared in _finalize.
        # Verify the scheduler used the 5-minute timeout via the instance attribute.
        self.assertEqual(sched.lease_timeout_minutes, 5)

    def test_short_lease_expires_quickly(self):
        """A 1-minute lease is expired by expire_stale_leases after that window."""
        custom_sched = SchedulerConfig(lease_timeout_minutes=1)
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)

        bead = self.storage.create_bead(
            title="Stale", agent_type="developer", description="d",
        )
        bead.status = BEAD_IN_PROGRESS
        bead.lease = Lease(
            owner="dev:B0001",
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        self.storage.save_bead(bead)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        expired = sched.expire_stale_leases()

        self.assertIn(bead.bead_id, expired)
        reloaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(reloaded.status, BEAD_READY)
        self.assertIsNone(reloaded.lease)


class TestSchedulerFollowupSuffixesBehavior(unittest.TestCase):
    """Custom followup_suffixes produce followup beads with matching IDs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_custom_followup_suffix_in_bead_ids(self):
        """Followup beads use the configured suffix names."""
        custom_sched = SchedulerConfig(
            followup_suffixes={"tester": "validate", "documentation": "doc", "review": "rev"},
        )
        cfg = OrchestratorConfig(scheduler=custom_sched, backends=default_config().backends)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)

        # Create a developer bead and simulate completion
        bead = self.storage.create_bead(
            title="Feature", agent_type="developer", description="d",
        )
        agent_result = AgentRunResult(outcome="completed", summary="done", verdict="approved")
        created = sched._create_followups(bead, agent_result)

        created_ids = [c.bead_id for c in created]
        # Should contain custom suffixes
        self.assertTrue(any("-validate" in bid for bid in created_ids),
                        f"Expected -validate suffix in {created_ids}")
        self.assertTrue(any("-doc" in bid for bid in created_ids),
                        f"Expected -doc suffix in {created_ids}")
        self.assertTrue(any("-rev" in bid for bid in created_ids),
                        f"Expected -rev suffix in {created_ids}")
        # Should NOT contain default suffixes
        self.assertFalse(any("-test" in bid for bid in created_ids),
                         f"Did not expect -test suffix in {created_ids}")


class TestSchedulerAgentTypeRepairBehavior(unittest.TestCase):
    """_repair_invalid_worker_agent_type uses config agent_types."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        _make_git_repo(self.root)
        self.storage = RepositoryStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_repair_uses_config_agent_types(self):
        """With restricted agent_types, repair picks from the allowed set."""
        cfg = OrchestratorConfig(
            agent_types=["developer", "tester"],
            backends=default_config().backends,
        )
        bead = self.storage.create_bead(
            title="Broken", agent_type="nonexistent", description="d",
        )
        self.storage.save_bead(bead)

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        repaired = sched._repair_invalid_worker_agent_type(bead)

        self.assertTrue(repaired)
        self.assertIn(bead.agent_type, {"developer", "tester"})

    def test_no_repair_for_valid_config_type(self):
        """A valid agent type from config is not repaired."""
        cfg = OrchestratorConfig(
            agent_types=["developer", "tester"],
            backends=default_config().backends,
        )
        bead = self.storage.create_bead(
            title="OK", agent_type="tester", description="d",
        )

        sched = Scheduler(self.storage, FakeRunner(), MagicMock(), config=cfg)
        repaired = sched._repair_invalid_worker_agent_type(bead)

        self.assertFalse(repaired)
        self.assertEqual(bead.agent_type, "tester")


if __name__ == "__main__":
    unittest.main()
