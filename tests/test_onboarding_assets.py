"""Tests for asset-installation and copy helpers in agent_takt.onboarding.

Covers:
- copy_asset_file / copy_asset_dir (low-level helpers)
- install_templates, install_default_config
- install_templates_with_substitution
- install_agents_skills, install_claude_skills
- scaffold_project — skill installation

Upgrade evaluation, AssetDecision, and manifest read/write tests live in
test_onboarding_upgrade.py.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.onboarding import (
    InitAnswers,
    copy_asset_dir,
    copy_asset_file,
    install_agents_skills,
    install_claude_skills,
    install_default_config,
    install_templates,
    install_templates_with_substitution,
    scaffold_project,
)
from agent_takt.onboarding.assets import install_claude_agents
from agent_takt._assets import packaged_claude_agents_dir


def _make_answers(**kwargs) -> InitAnswers:
    defaults = dict(
        runner="claude",
        max_workers=2,
        language="Python",
        test_command="pytest",
        build_check_command="python -m py_compile",
    )
    defaults.update(kwargs)
    return InitAnswers(**defaults)


# ---------------------------------------------------------------------------
# copy_asset_file
# ---------------------------------------------------------------------------


class TestCopyAssetFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_copies_file(self):
        src = self.tmp / "src.txt"
        src.write_text("hello")
        dest = self.tmp / "sub" / "dest.txt"
        copy_asset_file(src, dest)
        self.assertEqual(dest.read_text(), "hello")

    def test_skips_existing_without_overwrite(self):
        src = self.tmp / "src.txt"
        src.write_text("new")
        dest = self.tmp / "dest.txt"
        dest.write_text("old")
        copy_asset_file(src, dest, overwrite=False)
        self.assertEqual(dest.read_text(), "old")

    def test_overwrites_when_flag_set(self):
        src = self.tmp / "src.txt"
        src.write_text("new")
        dest = self.tmp / "dest.txt"
        dest.write_text("old")
        copy_asset_file(src, dest, overwrite=True)
        self.assertEqual(dest.read_text(), "new")

    def test_raises_if_src_missing(self):
        with self.assertRaises(FileNotFoundError):
            copy_asset_file(self.tmp / "nonexistent.txt", self.tmp / "dest.txt")

    def test_creates_parent_dirs(self):
        src = self.tmp / "src.txt"
        src.write_text("x")
        dest = self.tmp / "a" / "b" / "c" / "dest.txt"
        copy_asset_file(src, dest)
        self.assertTrue(dest.is_file())


# ---------------------------------------------------------------------------
# copy_asset_dir
# ---------------------------------------------------------------------------


class TestCopyAssetDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_src(self) -> Path:
        src = self.tmp / "src_dir"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("a")
        (src / "sub" / "b.txt").write_text("b")
        return src

    def test_copies_recursively(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        copy_asset_dir(src, dest)
        self.assertEqual((dest / "a.txt").read_text(), "a")
        self.assertEqual((dest / "sub" / "b.txt").read_text(), "b")

    def test_skips_existing_without_overwrite(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        dest.mkdir()
        (dest / "a.txt").write_text("old")
        copy_asset_dir(src, dest, overwrite=False)
        self.assertEqual((dest / "a.txt").read_text(), "old")

    def test_overwrites_when_flag_set(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        dest.mkdir()
        (dest / "a.txt").write_text("old")
        copy_asset_dir(src, dest, overwrite=True)
        self.assertEqual((dest / "a.txt").read_text(), "a")

    def test_raises_if_src_missing(self):
        with self.assertRaises(FileNotFoundError):
            copy_asset_dir(self.tmp / "no_dir", self.tmp / "dest")

    def test_raises_if_src_is_file_not_dir(self):
        f = self.tmp / "file.txt"
        f.write_text("x")
        with self.assertRaises(FileNotFoundError):
            copy_asset_dir(f, self.tmp / "dest")


# ---------------------------------------------------------------------------
# install_templates
# ---------------------------------------------------------------------------


class TestInstallTemplates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_templates_dir(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("dev template")
        (d / "tester.md").write_text("tester template")
        return d

    def test_installs_templates(self):
        fake = self._make_fake_templates_dir()
        with patch("agent_takt.onboarding.assets.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root)
        dest = self.root / "templates" / "agents"
        self.assertIn(dest / "developer.md", written)
        self.assertIn(dest / "tester.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "dev template")

    def test_skips_existing_without_overwrite(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        with patch("agent_takt.onboarding.assets.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root, overwrite=False)
        self.assertNotIn(dest / "developer.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "original")

    def test_overwrites_when_flag_set(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        with patch("agent_takt.onboarding.assets.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root, overwrite=True)
        self.assertIn(dest / "developer.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "dev template")


# ---------------------------------------------------------------------------
# install_default_config
# ---------------------------------------------------------------------------


class TestInstallDefaultConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_config(self):
        fake_src = self.root / "_fake_config.yaml"
        fake_src.write_text("fake: config")
        with patch("agent_takt.onboarding.assets.packaged_default_config", return_value=fake_src):
            dest = install_default_config(self.root)
        self.assertEqual(dest, self.root / ".takt" / "config.yaml")
        self.assertEqual(dest.read_text(), "fake: config")

    def test_skips_existing_without_overwrite(self):
        fake_src = self.root / "_fake_config.yaml"
        fake_src.write_text("new")
        config_path = self.root / ".takt" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("old")
        with patch("agent_takt.onboarding.assets.packaged_default_config", return_value=fake_src):
            install_default_config(self.root, overwrite=False)
        self.assertEqual(config_path.read_text(), "old")


# ---------------------------------------------------------------------------
# install_templates_with_substitution
# ---------------------------------------------------------------------------


class TestInstallTemplatesWithSubstitution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_templates_dir(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}} test={{TEST_COMMAND}}")
        return d

    def test_substitutes_placeholders(self):
        fake = self._make_fake_templates_dir()
        answers = _make_answers(language="Go", test_command="go test ./...")
        with patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake):
            written = install_templates_with_substitution(self.root, answers)
        dest = self.root / "templates" / "agents" / "developer.md"
        self.assertIn(dest, written)
        self.assertEqual(dest.read_text(), "lang=Go test=go test ./...")

    def test_skips_existing_without_overwrite(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        answers = _make_answers()
        with patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake):
            written = install_templates_with_substitution(self.root, answers, overwrite=False)
        self.assertEqual(written, [])
        self.assertEqual((dest / "developer.md").read_text(), "original")


# ---------------------------------------------------------------------------
# install_agents_skills — spec-management skill bundling
# ---------------------------------------------------------------------------


class TestInstallAgentsSkillsSpecManagement(unittest.TestCase):
    """Verify that the spec-management task skill is bundled and installed correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_spec_management_files_installed(self):
        """install_agents_skills() copies SKILL.md, spec.py, and agents/openai.yaml."""
        install_agents_skills(self.root)
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file(), "SKILL.md not installed")
        self.assertTrue((skill_dir / "spec.py").is_file(), "spec.py not installed")
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file(), "agents/openai.yaml not installed")

    def test_spec_py_is_executable(self):
        """spec.py installed by install_agents_skills() retains executable bit."""
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        self.assertTrue(spec_py.is_file())
        self.assertTrue(os.access(spec_py, os.X_OK), "spec.py is not executable")

    def test_spec_py_init_subcommand(self):
        """spec.py init runs without error and creates specs/ structure."""
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        result = subprocess.run(
            [sys.executable, str(spec_py), "init"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py init failed: {result.stderr}")
        self.assertTrue((self.root / "specs").is_dir(), "specs/ directory not created by init")

    def test_spec_py_list_subcommand(self):
        """spec.py list runs without error (returns empty list on fresh init)."""
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        # initialise first so specs/ dir exists
        subprocess.run([sys.executable, str(spec_py), "init"], cwd=str(self.root), capture_output=True)
        result = subprocess.run(
            [sys.executable, str(spec_py), "list"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py list failed: {result.stderr}")

    def test_spec_py_create_subcommand(self):
        """spec.py create produces a spec file with expected frontmatter."""
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        subprocess.run([sys.executable, str(spec_py), "init"], cwd=str(self.root), capture_output=True)
        result = subprocess.run(
            [sys.executable, str(spec_py), "create", "Test spec"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py create failed: {result.stderr}")
        # A spec file should exist in specs/drafts/
        drafts = list((self.root / "specs" / "drafts").glob("*.md"))
        self.assertTrue(len(drafts) >= 1, "No spec file created in specs/drafts/")
        content = drafts[0].read_text()
        self.assertIn("name:", content)


# ---------------------------------------------------------------------------
# scaffold_project — spec-management skill end-to-end
# ---------------------------------------------------------------------------

class TestScaffoldProjectSpecManagementSkill(unittest.TestCase):
    """Verify scaffold_project installs the spec-management skill using real package data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_templates(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}}")
        return d

    def _fake_config(self) -> Path:
        f = self.root / "_fake_config.yaml"
        f.write_text("fake: true")
        return f

    def _run_scaffold(self, runner: str) -> None:
        answers = _make_answers(runner=runner)
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)

    def test_scaffold_claude_runner_installs_spec_management(self):
        """scaffold_project with claude runner installs task/spec-management into .agents/skills."""
        self._run_scaffold("claude")
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "spec.py").is_file())
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())

    def test_scaffold_codex_runner_installs_spec_management(self):
        """scaffold_project with codex runner installs task/spec-management into .agents/skills."""
        self._run_scaffold("codex")
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "spec.py").is_file())
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())


# ---------------------------------------------------------------------------
# install_claude_skills — takt operator skill
# ---------------------------------------------------------------------------


class TestInstallClaudeSkillsTaktSkill(unittest.TestCase):
    """Verify that the takt operator skill is bundled and installed correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_takt_skill_file_installed(self):
        """install_claude_skills() copies takt/SKILL.md to .claude/skills/takt/SKILL.md."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "takt/SKILL.md not installed by install_claude_skills()")

    def test_takt_skill_has_valid_yaml_frontmatter(self):
        """The installed takt/SKILL.md has parseable YAML frontmatter."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---"), "SKILL.md does not start with YAML frontmatter delimiter")
        end_idx = content.index("---", 3)
        frontmatter_text = content[3:end_idx].strip()
        import yaml
        parsed = yaml.safe_load(frontmatter_text)
        self.assertIsInstance(parsed, dict, "Frontmatter did not parse as a dict")
        self.assertIn("name", parsed, "Frontmatter missing 'name' field")
        self.assertIn("description", parsed, "Frontmatter missing 'description' field")

    def test_takt_skill_name_is_takt(self):
        """The takt skill's frontmatter name field is 'takt'."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        end_idx = content.index("---", 3)
        import yaml
        parsed = yaml.safe_load(content[3:end_idx].strip())
        self.assertEqual("takt", parsed["name"])

    def test_takt_skill_content_mentions_bead(self):
        """The installed takt/SKILL.md references bead concepts."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("bead", content.lower(), "SKILL.md does not mention beads")


# ---------------------------------------------------------------------------
# install_claude_skills — skill-spec-management bundling
# ---------------------------------------------------------------------------


class TestInstallClaudeSkillsSpecManagement(unittest.TestCase):
    """Verify that skill-spec-management is bundled and installed correctly by install_claude_skills."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_spec_management_skill_md_installed(self):
        """install_claude_skills() installs skill-spec-management/SKILL.md."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "skill-spec-management" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "skill-spec-management/SKILL.md not installed")

    def test_spec_management_spec_py_installed(self):
        """install_claude_skills() installs skill-spec-management/spec.py."""
        install_claude_skills(self.root)
        spec_py = self.root / ".claude" / "skills" / "skill-spec-management" / "spec.py"
        self.assertTrue(spec_py.is_file(), "skill-spec-management/spec.py not installed")

    def test_spec_management_no_test_files_installed(self):
        """install_claude_skills() does not install the deleted tests/ directory."""
        install_claude_skills(self.root)
        skill_dir = self.root / ".claude" / "skills" / "skill-spec-management"
        self.assertFalse(
            (skill_dir / "tests").exists(),
            "tests/ directory should not exist in installed skill-spec-management",
        )
        self.assertFalse(
            (skill_dir / "tests" / "test_spec.py").exists(),
            "tests/test_spec.py should not be installed",
        )

    def test_spec_management_only_expected_files(self):
        """install_claude_skills() installs exactly SKILL.md and spec.py for skill-spec-management."""
        install_claude_skills(self.root)
        skill_dir = self.root / ".claude" / "skills" / "skill-spec-management"
        installed_files = sorted(p.name for p in skill_dir.iterdir() if p.is_file())
        self.assertEqual(["SKILL.md", "spec.py"], installed_files)

    def test_spec_management_skill_md_frontmatter(self):
        """skill-spec-management/SKILL.md has a name field of 'spec-management'."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "skill-spec-management" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---"), "SKILL.md does not start with YAML frontmatter")
        end_idx = content.index("---", 3)
        import yaml
        parsed = yaml.safe_load(content[3:end_idx].strip())
        self.assertEqual("spec-management", parsed.get("name"), "SKILL.md frontmatter name != 'spec-management'")


# ---------------------------------------------------------------------------
# scaffold_project — takt operator skill end-to-end
# ---------------------------------------------------------------------------


class TestScaffoldProjectTaktSkill(unittest.TestCase):
    """Verify scaffold_project installs the takt operator skill using real package data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_templates(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}}")
        return d

    def _fake_config(self) -> Path:
        f = self.root / "_fake_config.yaml"
        f.write_text("fake: true")
        return f

    def test_scaffold_installs_takt_skill(self):
        """scaffold_project() installs takt/SKILL.md into .claude/skills/takt/."""
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "takt/SKILL.md not created by scaffold_project()")


# ---------------------------------------------------------------------------
# packaged_claude_agents_dir — helper
# ---------------------------------------------------------------------------


class TestPackagedClaudeAgentsDir(unittest.TestCase):
    """Verify packaged_claude_agents_dir() points at an existing bundled directory."""

    def test_returns_path_ending_in_claude_agents(self):
        """packaged_claude_agents_dir() must return a Path whose name is 'claude_agents'."""
        p = packaged_claude_agents_dir()
        self.assertEqual(p.name, "claude_agents")

    def test_directory_exists(self):
        """The path returned by packaged_claude_agents_dir() must exist as a directory."""
        p = packaged_claude_agents_dir()
        self.assertTrue(p.is_dir(), f"Expected directory at {p}")

    def test_contains_at_least_one_file(self):
        """The bundled claude_agents directory must contain at least one .md file."""
        p = packaged_claude_agents_dir()
        files = list(p.rglob("*.md"))
        self.assertGreater(len(files), 0, "No .md files found in bundled claude_agents/")

    def test_spec_reviewer_agent_present(self):
        """spec-reviewer.md must be present in the bundled claude_agents/ directory."""
        p = packaged_claude_agents_dir()
        self.assertTrue(
            (p / "spec-reviewer.md").is_file(),
            "spec-reviewer.md not found in bundled claude_agents/",
        )


# ---------------------------------------------------------------------------
# install_claude_agents — behavior tests
# ---------------------------------------------------------------------------


class TestInstallClaudeAgents(unittest.TestCase):
    """Tests for install_claude_agents() behavior."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_agents_dir(self, *filenames: str) -> Path:
        d = self.root / "_fake_claude_agents"
        d.mkdir(exist_ok=True)
        for name in filenames:
            (d / name).write_text(f"# agent {name}")
        return d

    def test_copies_files_to_dot_claude_agents(self):
        """install_claude_agents() copies files to <project_root>/.claude/agents/."""
        fake_src = self._fake_agents_dir("spec-reviewer.md")
        with patch("agent_takt.onboarding.assets.packaged_claude_agents_dir", return_value=fake_src):
            install_claude_agents(self.root)
        dest = self.root / ".claude" / "agents" / "spec-reviewer.md"
        self.assertTrue(dest.is_file(), ".claude/agents/spec-reviewer.md not created")

    def test_skips_existing_without_overwrite(self):
        """overwrite=False leaves pre-existing destination files untouched."""
        fake_src = self._fake_agents_dir("spec-reviewer.md")
        dest = self.root / ".claude" / "agents" / "spec-reviewer.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("existing content")
        with patch("agent_takt.onboarding.assets.packaged_claude_agents_dir", return_value=fake_src):
            result = install_claude_agents(self.root, overwrite=False)
        self.assertEqual("existing content", dest.read_text())
        self.assertEqual([], result)

    def test_overwrites_when_flag_set(self):
        """overwrite=True replaces pre-existing destination files."""
        fake_src = self._fake_agents_dir("spec-reviewer.md")
        dest = self.root / ".claude" / "agents" / "spec-reviewer.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("old content")
        with patch("agent_takt.onboarding.assets.packaged_claude_agents_dir", return_value=fake_src):
            result = install_claude_agents(self.root, overwrite=True)
        self.assertEqual("# agent spec-reviewer.md", dest.read_text())
        self.assertIn(dest, result)

    def test_return_value_lists_only_written_paths(self):
        """Return value contains only paths that were actually written."""
        fake_src = self._fake_agents_dir("new-agent.md", "existing-agent.md")
        dest_existing = self.root / ".claude" / "agents" / "existing-agent.md"
        dest_existing.parent.mkdir(parents=True, exist_ok=True)
        dest_existing.write_text("pre-existing")
        with patch("agent_takt.onboarding.assets.packaged_claude_agents_dir", return_value=fake_src):
            result = install_claude_agents(self.root, overwrite=False)
        result_names = {p.name for p in result}
        self.assertIn("new-agent.md", result_names)
        self.assertNotIn("existing-agent.md", result_names)

    def test_uses_real_bundled_catalog(self):
        """install_claude_agents() with real package data creates .claude/agents/ files."""
        install_claude_agents(self.root)
        dest_dir = self.root / ".claude" / "agents"
        self.assertTrue(dest_dir.is_dir(), ".claude/agents/ not created")
        installed = list(dest_dir.rglob("*.md"))
        self.assertGreater(len(installed), 0, "No agent files installed from real bundle")


# ---------------------------------------------------------------------------
# scaffold_project — Claude agents integration
# ---------------------------------------------------------------------------


class TestScaffoldProjectClaudeAgents(unittest.TestCase):
    """Verify scaffold_project installs Claude agents and wires them correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_templates(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir(exist_ok=True)
        (d / "developer.md").write_text("lang={{LANGUAGE}}")
        return d

    def _fake_config(self) -> Path:
        f = self.root / "_fake_config.yaml"
        f.write_text("fake: true")
        return f

    def test_scaffold_installs_claude_agents(self):
        """scaffold_project() installs bundled agents into .claude/agents/."""
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        dest_dir = self.root / ".claude" / "agents"
        self.assertTrue(dest_dir.is_dir(), ".claude/agents/ directory not created by scaffold_project()")
        installed = list(dest_dir.rglob("*.md"))
        self.assertGreater(len(installed), 0, "No agent files installed by scaffold_project()")

    def test_scaffold_emits_success_line_for_claude_agents(self):
        """scaffold_project() emits a success message for .claude/agents/ installation."""
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        output = out.getvalue()
        self.assertIn(".claude/agents/", output)

    def test_scaffold_includes_claude_agents_in_manifest(self):
        """scaffold_project() includes .claude/agents/ files in the assets manifest."""
        import json
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
            patch("agent_takt.onboarding.scaffold.commit_scaffold"),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        manifest_path = self.root / ".takt" / "assets-manifest.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text())
        agent_keys = [k for k in data["assets"] if k.startswith(".claude/agents/")]
        self.assertGreater(len(agent_keys), 0, "No .claude/agents/ entries in assets manifest")

    def test_commit_scaffold_includes_dot_claude_agents_path(self):
        """commit_scaffold() stages .claude/agents/ path in git add."""
        from agent_takt.onboarding import commit_scaffold

        (self.root / ".git").mkdir(parents=True, exist_ok=True)

        console_out = io.StringIO()
        from agent_takt.console import ConsoleReporter
        console = ConsoleReporter(stream=console_out)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
            commit_scaffold(self.root, console)

        add_call = mock_run.call_args_list[0]
        staged_paths = add_call[0][0]
        self.assertIn(".claude/agents/", staged_paths)

    def test_scaffold_idempotent_second_run_overwrites_agents(self):
        """Second scaffold_project run overwrites .claude/agents/ without error."""
        answers = _make_answers(runner="claude")
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()

        def run_scaffold():
            out = io.StringIO()
            with (
                patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
                patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
                patch("agent_takt.onboarding.scaffold.commit_scaffold"),
            ):
                scaffold_project(self.root, answers, stream_out=out)
            return out.getvalue()

        run_scaffold()
        out2 = run_scaffold()
        dest_dir = self.root / ".claude" / "agents"
        self.assertTrue(dest_dir.is_dir())
        installed = list(dest_dir.rglob("*.md"))
        self.assertGreater(len(installed), 0)


if __name__ == "__main__":
    unittest.main()
