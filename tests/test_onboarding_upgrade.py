"""Tests for upgrade evaluation, AssetDecision, and manifest read/write in agent_takt.onboarding.

Covers:
- write_assets_manifest / read_assets_manifest (schema, round-trip, error handling)
- evaluate_upgrade_actions (all 8 AssetDecision action types)
- scaffold_project — manifest creation and rerun behavior
"""

from __future__ import annotations

import io
import json
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
    evaluate_upgrade_actions,
    read_assets_manifest,
    scaffold_project,
    write_assets_manifest,
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
# write_assets_manifest
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

        content = "some content for hashing"
        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md", content)
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sha = hashlib.sha256(content.encode()).hexdigest()
        recorded_sha = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]["sha256"]
        self.assertEqual(expected_sha, recorded_sha)

    def test_templates_marked_user_owned(self):
        """templates/agents/ files are recorded with user_owned: true."""
        f = self._make_file("templates/agents/developer.md", "template content")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"]["templates/agents/developer.md"]
        self.assertTrue(entry["user_owned"])
        self.assertEqual("bundled", entry["source"])

    def test_agents_skills_not_user_owned(self):
        """.agents/skills/ files are recorded with user_owned: false."""
        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]
        self.assertFalse(entry["user_owned"])

    def test_excludes_docs_memory_files(self):
        """Files under docs/memory/ are excluded from the manifest even if passed in."""
        skill = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        mem = self._make_file("docs/memory/conventions.md")
        manifest_path = write_assets_manifest(self.root, [skill, mem])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".agents/skills/core/base-orchestrator/SKILL.md", data["assets"])
        self.assertNotIn("docs/memory/conventions.md", data["assets"])

    def test_manifest_path_resolves_correctly(self):
        """_MANIFEST_FILENAME resolves to .takt/assets-manifest.json relative to project root."""
        from agent_takt.onboarding import _MANIFEST_FILENAME

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        self.assertEqual(manifest_path, self.root / ".takt" / "assets-manifest.json")
        self.assertEqual(_MANIFEST_FILENAME, ".takt/assets-manifest.json")

    def test_config_yaml_tracked(self):
        """.takt/config.yaml is included when passed in installed_files."""
        cfg = self._make_file(".takt/config.yaml", "fake: true")
        manifest_path = write_assets_manifest(self.root, [cfg])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".takt/config.yaml", data["assets"])


# ---------------------------------------------------------------------------
# read_assets_manifest
# ---------------------------------------------------------------------------


class TestReadAssetsManifest(unittest.TestCase):
    """Tests for read_assets_manifest() error handling and round-trip."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_file_returns_empty_structure(self):
        """When .takt/assets-manifest.json does not exist, return empty manifest."""
        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_malformed_json_returns_empty_structure(self):
        """When the manifest contains invalid JSON, return empty manifest without raising."""
        manifest_path = self.root / ".takt" / "assets-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{not valid json!!", encoding="utf-8")
        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_round_trip_write_then_read(self):
        """write_assets_manifest followed by read_assets_manifest returns equivalent data."""
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
        manifest = {"takt_version": "", "installed_at": "", "assets": {}}
        decisions = evaluate_upgrade_actions(self.root, manifest)
        # There may be user_added (disk files) but no bundled entries are in the manifest
        # so all bundled files must be 'new'
        bundled_decisions = [d for d in decisions if d.action != "user_added"]
        self.assertTrue(all(d.action == "new" for d in bundled_decisions),
                        f"Expected all bundled to be 'new', got: {[d.action for d in bundled_decisions]}")

    def test_unchanged_action(self):
        """disk sha == manifest sha == bundled sha → action=unchanged."""
        content = "unchanged content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
            with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, "user modified content")
        original_sha = self._sha256("original content from install")

        with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
        content = "template content"
        rel = "templates/agents/developer.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
        content = "bundled content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        # Do NOT create the disk file — it's missing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as bf:
            bf.write(content)
            bundled_path = Path(bf.name)

        try:
            with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
        rel = ".agents/skills/old/deprecated/SKILL.md"
        disk_file = self._make_file(rel, "old skill")
        sha = self._sha256("old skill")

        with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
        rel = ".agents/skills/custom/my-skill/SKILL.md"
        self._make_file(rel, "my custom skill")

        with patch("agent_takt.onboarding.upgrade._compute_bundled_catalog",
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
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.assets.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.assets.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
            patch("agent_takt.onboarding.scaffold.commit_scaffold"),
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
        from agent_takt.onboarding import install_agents_skills

        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        with patch("agent_takt.onboarding.assets.packaged_agents_skills_dir", return_value=fake_agents):
            first = install_agents_skills(self.root, overwrite=False)
            second = install_agents_skills(self.root, overwrite=False)
        self.assertGreater(len(first), 0)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
