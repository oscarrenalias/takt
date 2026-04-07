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


if __name__ == "__main__":
    unittest.main()
