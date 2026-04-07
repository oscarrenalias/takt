"""Tests for asset-installation and copy helpers in agent_takt.onboarding.

Covers:
- copy_asset_file / copy_asset_dir (low-level helpers)
- install_templates, install_default_config
- install_templates_with_substitution
- install_agents_skills, install_claude_skills
- write_assets_manifest / read_assets_manifest
- evaluate_upgrade_actions
- scaffold_project — skill installation and manifest creation
"""

from __future__ import annotations

import io
import json
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
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
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
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root, overwrite=False)
        self.assertNotIn(dest / "developer.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "original")

    def test_overwrites_when_flag_set(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
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
        with patch("agent_takt.onboarding.packaged_default_config", return_value=fake_src):
            dest = install_default_config(self.root)
        self.assertEqual(dest, self.root / ".takt" / "config.yaml")
        self.assertEqual(dest.read_text(), "fake: config")

    def test_skips_existing_without_overwrite(self):
        fake_src = self.root / "_fake_config.yaml"
        fake_src.write_text("new")
        config_path = self.root / ".takt" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("old")
        with patch("agent_takt.onboarding.packaged_default_config", return_value=fake_src):
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
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
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
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
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
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
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
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "takt/SKILL.md not created by scaffold_project()")


# ---------------------------------------------------------------------------
# write_assets_manifest / read_assets_manifest
# ---------------------------------------------------------------------------


class TestWriteAssetsManifest(unittest.TestCase):
    """Tests for write_assets_manifest() schema and field correctness."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_file(self, rel: str, content: str = "hello") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_json_structure_keys(self):
        """Manifest contains takt_version, installed_at, and assets at the top level."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("takt_version", data)
        self.assertIn("installed_at", data)
        self.assertIn("assets", data)
        self.assertIsInstance(data["assets"], dict)

    def test_sha256_matches_file_contents(self):
        """SHA-256 recorded in the manifest matches the actual file content."""
        import hashlib
        from agent_takt.onboarding import write_assets_manifest

        content = "some content for hashing"
        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md", content)
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sha = hashlib.sha256(content.encode()).hexdigest()
        recorded_sha = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]["sha256"]
        self.assertEqual(expected_sha, recorded_sha)

    def test_templates_marked_user_owned(self):
        """templates/agents/ files are recorded with user_owned: true."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file("templates/agents/developer.md", "template content")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"]["templates/agents/developer.md"]
        self.assertTrue(entry["user_owned"])
        self.assertEqual("bundled", entry["source"])

    def test_agents_skills_not_user_owned(self):
        """.agents/skills/ files are recorded with user_owned: false."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]
        self.assertFalse(entry["user_owned"])

    def test_excludes_docs_memory_files(self):
        """Files under docs/memory/ are excluded from the manifest even if passed in."""
        from agent_takt.onboarding import write_assets_manifest

        skill = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        mem = self._make_file("docs/memory/conventions.md")
        manifest_path = write_assets_manifest(self.root, [skill, mem])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".agents/skills/core/base-orchestrator/SKILL.md", data["assets"])
        self.assertNotIn("docs/memory/conventions.md", data["assets"])

    def test_manifest_path_resolves_correctly(self):
        """_MANIFEST_FILENAME resolves to .takt/assets-manifest.json relative to project root."""
        from agent_takt.onboarding import _MANIFEST_FILENAME, write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        self.assertEqual(manifest_path, self.root / ".takt" / "assets-manifest.json")
        self.assertEqual(_MANIFEST_FILENAME, ".takt/assets-manifest.json")

    def test_config_yaml_tracked(self):
        """.takt/config.yaml is included when passed in installed_files."""
        from agent_takt.onboarding import write_assets_manifest

        cfg = self._make_file(".takt/config.yaml", "fake: true")
        manifest_path = write_assets_manifest(self.root, [cfg])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".takt/config.yaml", data["assets"])


class TestReadAssetsManifest(unittest.TestCase):
    """Tests for read_assets_manifest() error handling and round-trip."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_file_returns_empty_structure(self):
        """When .takt/assets-manifest.json does not exist, return empty manifest."""
        from agent_takt.onboarding import read_assets_manifest

        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_malformed_json_returns_empty_structure(self):
        """When the manifest contains invalid JSON, return empty manifest without raising."""
        from agent_takt.onboarding import read_assets_manifest

        manifest_path = self.root / ".takt" / "assets-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{not valid json!!", encoding="utf-8")
        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_round_trip_write_then_read(self):
        """write_assets_manifest followed by read_assets_manifest returns equivalent data."""
        from agent_takt.onboarding import read_assets_manifest, write_assets_manifest

        f = self.root / ".agents" / "skills" / "core" / "base-orchestrator" / "SKILL.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("skill content")
        write_assets_manifest(self.root, [f])
        result = read_assets_manifest(self.root)
        self.assertIn(".agents/skills/core/base-orchestrator/SKILL.md", result["assets"])
        self.assertNotEqual("", result["takt_version"])
        self.assertNotEqual("", result["installed_at"])


# ---------------------------------------------------------------------------
# evaluate_upgrade_actions — all 8 action types
# ---------------------------------------------------------------------------


class TestEvaluateUpgradeActions(unittest.TestCase):
    """Tests for evaluate_upgrade_actions() covering all 8 AssetDecision action types."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_file(self, rel: str, content: str = "content") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _sha256(self, content: str) -> str:
        import hashlib
        return hashlib.sha256(content.encode()).hexdigest()

    def _bundled_entry(self, rel: str, sha: str, *, user_owned: bool = False) -> dict:
        return {"sha256": sha, "source": "bundled", "user_owned": user_owned}

    def test_empty_manifest_all_new(self):
        """With an empty manifest, all bundled entries should be action=new."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        manifest = {"takt_version": "", "installed_at": "", "assets": {}}
        decisions = evaluate_upgrade_actions(self.root, manifest)
        actions = {d.action for d in decisions}
        # There may be user_added (disk files) but no bundled entries are in the manifest
        # so all bundled files must be 'new'
        bundled_decisions = [d for d in decisions if d.action != "user_added"]
        self.assertTrue(all(d.action == "new" for d in bundled_decisions),
                        f"Expected all bundled to be 'new', got: {[d.action for d in bundled_decisions]}")

    def test_unchanged_action(self):
        """disk sha == manifest sha == bundled sha → action=unchanged."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        content = "unchanged content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "unchanged")

    def test_update_action(self):
        """disk sha == manifest sha but bundled sha differs → action=update."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        disk_content = "old content"
        bundled_content = "new bundled content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, disk_content)
        disk_sha = self._sha256(disk_content)

        # Create a separate temp file representing the bundled version
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as bf:
            bf.write(bundled_content)
            bundled_path = Path(bf.name)

        try:
            with patch("agent_takt.onboarding._compute_bundled_catalog",
                       return_value={rel: bundled_path}):
                manifest = {"takt_version": "", "installed_at": "", "assets": {
                    rel: self._bundled_entry(rel, disk_sha)
                }}
                decisions = evaluate_upgrade_actions(self.root, manifest)
        finally:
            bundled_path.unlink(missing_ok=True)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "update")

    def test_skipped_modified_action(self):
        """disk sha != manifest sha → action=skipped_modified."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, "user modified content")
        original_sha = self._sha256("original content from install")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, original_sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "skipped_modified")

    def test_skipped_user_owned_action(self):
        """user_owned: true in manifest → action=skipped_user_owned."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        content = "template content"
        rel = "templates/agents/developer.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: {"sha256": sha, "source": "bundled", "user_owned": True}
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "skipped_user_owned")

    def test_restored_action(self):
        """In manifest + bundle but missing from disk → action=restored."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        content = "bundled content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        # Do NOT create the disk file — it's missing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as bf:
            bf.write(content)
            bundled_path = Path(bf.name)

        try:
            with patch("agent_takt.onboarding._compute_bundled_catalog",
                       return_value={rel: bundled_path}):
                manifest = {"takt_version": "", "installed_at": "", "assets": {
                    rel: self._bundled_entry(rel, self._sha256(content))
                }}
                decisions = evaluate_upgrade_actions(self.root, manifest)
        finally:
            bundled_path.unlink(missing_ok=True)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "restored")

    def test_disabled_action(self):
        """In manifest, NOT in bundle → action=disabled."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/old/deprecated/SKILL.md"
        disk_file = self._make_file(rel, "old skill")
        sha = self._sha256("old skill")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={}):  # empty bundle — no bundled files
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "disabled")

    def test_user_added_action(self):
        """On disk under bundled prefix, not in manifest or bundle → action=user_added."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/custom/my-skill/SKILL.md"
        self._make_file(rel, "my custom skill")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {}}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "user_added")
        self.assertTrue(d.user_owned)


# ---------------------------------------------------------------------------
# scaffold_project — manifest creation and rerun behavior
# ---------------------------------------------------------------------------


class TestScaffoldProjectManifest(unittest.TestCase):
    """Verify scaffold_project creates the assets manifest on first run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_dirs(self):
        fake_templates = self.root / "_fake_templates"
        fake_templates.mkdir(exist_ok=True)
        (fake_templates / "developer.md").write_text("lang={{LANGUAGE}}")

        fake_agents = self.root / "_fake_agents_skills"
        fake_agents.mkdir(exist_ok=True)
        (fake_agents / "core" / "base-orchestrator").mkdir(parents=True, exist_ok=True)
        (fake_agents / "core" / "base-orchestrator" / "SKILL.md").write_text("skill")

        fake_claude = self.root / "_fake_claude_skills"
        fake_claude.mkdir(exist_ok=True)
        (fake_claude / "skill.md").write_text("skill")

        fake_config = self.root / "_fake_config.yaml"
        fake_config.write_text("fake: true")
        return fake_templates, fake_agents, fake_claude, fake_config

    def _run_scaffold(self):
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        answers = _make_answers()
        out = io.StringIO()
        with (
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
            patch("agent_takt.onboarding.commit_scaffold"),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        return out.getvalue()

    def test_fresh_init_creates_manifest(self):
        """After scaffold_project, .takt/assets-manifest.json exists with non-empty assets."""
        self._run_scaffold()
        manifest_path = self.root / ".takt" / "assets-manifest.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertGreater(len(data["assets"]), 0)

    def test_fresh_init_takt_version_populated(self):
        """The manifest written at init has a non-empty takt_version."""
        self._run_scaffold()
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertNotEqual(data["takt_version"], "")

    def test_fresh_init_templates_user_owned(self):
        """Guardrail templates are marked user_owned: true in the init manifest."""
        self._run_scaffold()
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        template_entries = {k: v for k, v in data["assets"].items() if k.startswith("templates/agents/")}
        self.assertGreater(len(template_entries), 0)
        for k, v in template_entries.items():
            self.assertTrue(v["user_owned"], f"Expected user_owned=true for {k}")

    def test_second_run_prints_upgrade_notice(self):
        """Second scaffold_project run prints a notice to run 'takt upgrade'."""
        self._run_scaffold()
        # Second run
        out = self._run_scaffold()
        self.assertIn("takt upgrade", out)

    def test_install_agents_skills_second_run_returns_empty(self):
        """install_agents_skills() on a dir where skills exist returns empty list.

        Uses a fake bundled catalog so the second call installs from the same
        source and sees all files already present.
        """
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        with patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents):
            first = install_agents_skills(self.root, overwrite=False)
            second = install_agents_skills(self.root, overwrite=False)
        self.assertGreater(len(first), 0)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
