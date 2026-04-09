"""Tests for _assets.py — bundled package data path helpers.

Validates that all packaged_*() helpers return correct Path types,
point at subdirectories of _data/, and that the bundled assets
actually exist on disk in the source checkout.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt._assets import (
    packaged_agents_skills_dir,
    packaged_claude_skills_dir,
    packaged_default_config,
    packaged_docs_memory_dir,
    packaged_templates_dir,
)


class TestPackagedHelperReturnTypes(unittest.TestCase):
    """All helpers return Path objects."""

    def test_templates_dir_is_path(self):
        self.assertIsInstance(packaged_templates_dir(), Path)

    def test_agents_skills_dir_is_path(self):
        self.assertIsInstance(packaged_agents_skills_dir(), Path)

    def test_claude_skills_dir_is_path(self):
        self.assertIsInstance(packaged_claude_skills_dir(), Path)

    def test_docs_memory_dir_is_path(self):
        self.assertIsInstance(packaged_docs_memory_dir(), Path)

    def test_default_config_is_path(self):
        self.assertIsInstance(packaged_default_config(), Path)


class TestPackagedHelperSuffixes(unittest.TestCase):
    """Returned paths end in the expected sub-path components."""

    def test_templates_dir_suffix(self):
        p = packaged_templates_dir()
        self.assertTrue(
            str(p).endswith("templates/agents") or str(p).endswith("templates\\agents"),
            f"Expected path ending in templates/agents, got: {p}",
        )

    def test_agents_skills_dir_suffix(self):
        p = packaged_agents_skills_dir()
        self.assertTrue(
            str(p).endswith("agents_skills"),
            f"Expected path ending in agents_skills, got: {p}",
        )

    def test_claude_skills_dir_suffix(self):
        p = packaged_claude_skills_dir()
        self.assertTrue(
            str(p).endswith("claude_skills"),
            f"Expected path ending in claude_skills, got: {p}",
        )

    def test_docs_memory_dir_suffix(self):
        p = packaged_docs_memory_dir()
        self.assertTrue(
            str(p).endswith("docs/memory") or str(p).endswith("docs\\memory"),
            f"Expected path ending in docs/memory, got: {p}",
        )

    def test_default_config_suffix(self):
        p = packaged_default_config()
        self.assertEqual(p.name, "default_config.yaml")


class TestPackagedAssetsExistOnDisk(unittest.TestCase):
    """Bundled _data/ assets are present in the source checkout."""

    def test_templates_dir_exists(self):
        p = packaged_templates_dir()
        self.assertTrue(p.is_dir(), f"packaged_templates_dir() not found: {p}")

    def test_templates_contain_all_agent_types(self):
        p = packaged_templates_dir()
        for agent_type in ("developer", "tester", "documentation", "review", "planner", "investigator"):
            md = p / f"{agent_type}.md"
            self.assertTrue(md.is_file(), f"Missing bundled template: {md}")

    def test_agents_skills_dir_exists(self):
        p = packaged_agents_skills_dir()
        self.assertTrue(p.is_dir(), f"packaged_agents_skills_dir() not found: {p}")

    def test_agents_skills_contains_core(self):
        p = packaged_agents_skills_dir()
        core = p / "core" / "base-orchestrator" / "SKILL.md"
        self.assertTrue(core.is_file(), f"Missing core skill: {core}")

    def test_claude_skills_dir_exists(self):
        p = packaged_claude_skills_dir()
        self.assertTrue(p.is_dir(), f"packaged_claude_skills_dir() not found: {p}")

    def test_claude_skills_contains_memory(self):
        p = packaged_claude_skills_dir()
        memory = p / "memory" / "SKILL.md"
        self.assertTrue(memory.is_file(), f"Missing claude memory skill: {memory}")

    def test_docs_memory_dir_exists(self):
        p = packaged_docs_memory_dir()
        self.assertTrue(p.is_dir(), f"packaged_docs_memory_dir() not found: {p}")

    def test_default_config_exists(self):
        p = packaged_default_config()
        self.assertTrue(p.is_file(), f"packaged_default_config() not found: {p}")

    def test_default_config_is_yaml(self):
        p = packaged_default_config()
        content = p.read_text(encoding="utf-8")
        self.assertTrue(len(content) > 0, "default_config.yaml is empty")
        # Minimal YAML sanity: should contain at least one section key
        self.assertIn(":", content, "default_config.yaml has no key-value pairs")


class TestPackagedDataUnderDataDir(unittest.TestCase):
    """All helpers return paths inside agent_takt/_data/."""

    def _data_dir(self) -> Path:
        # Locate _data/ relative to _assets.py
        from agent_takt import _assets
        return Path(_assets.__file__).parent / "_data"

    def test_templates_under_data(self):
        data = self._data_dir()
        p = packaged_templates_dir()
        self.assertTrue(str(p).startswith(str(data)), f"{p} not under {data}")

    def test_agents_skills_under_data(self):
        data = self._data_dir()
        p = packaged_agents_skills_dir()
        self.assertTrue(str(p).startswith(str(data)), f"{p} not under {data}")

    def test_claude_skills_under_data(self):
        data = self._data_dir()
        p = packaged_claude_skills_dir()
        self.assertTrue(str(p).startswith(str(data)), f"{p} not under {data}")

    def test_docs_memory_under_data(self):
        data = self._data_dir()
        p = packaged_docs_memory_dir()
        self.assertTrue(str(p).startswith(str(data)), f"{p} not under {data}")

    def test_default_config_under_data(self):
        data = self._data_dir()
        p = packaged_default_config()
        self.assertTrue(str(p).startswith(str(data)), f"{p} not under {data}")


class TestPromptsUseBundledTemplates(unittest.TestCase):
    """prompts.py DEFAULT_TEMPLATES_DIR points at the bundled templates."""

    def test_default_templates_dir_uses_bundled(self):
        from agent_takt.prompts import DEFAULT_TEMPLATES_DIR
        bundled = packaged_templates_dir()
        self.assertEqual(DEFAULT_TEMPLATES_DIR, bundled)

    def test_guardrail_template_path_no_root_uses_bundled(self):
        from agent_takt.prompts import guardrail_template_path
        bundled = packaged_templates_dir()
        path = guardrail_template_path("developer")
        self.assertEqual(path, bundled / "developer.md")

    def test_load_guardrail_no_root_reads_bundled(self):
        from agent_takt.prompts import load_guardrail_template
        path, text = load_guardrail_template("developer")
        self.assertTrue(path.is_file())
        self.assertTrue(len(text) > 0)


class TestSkillsFallbackToBundled(unittest.TestCase):
    """skills._skill_path falls back to bundled assets when not in project."""

    def test_missing_project_skill_falls_back_to_bundled(self):
        import tempfile
        from agent_takt.skills import _skill_path
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            # No .agents/skills in this temp repo
            result = _skill_path(repo_root, "core/base-orchestrator")
            bundled = packaged_agents_skills_dir() / "core" / "base-orchestrator"
            self.assertEqual(result, bundled)

    def test_project_skill_takes_priority_over_bundled(self):
        import tempfile
        from agent_takt.skills import _skill_path
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            # Create a project skill
            project_skill = repo_root / ".agents" / "skills" / "core" / "base-orchestrator"
            project_skill.mkdir(parents=True)
            result = _skill_path(repo_root, "core/base-orchestrator")
            self.assertEqual(result, project_skill)

    def test_role_investigator_bundled_skill_exists(self):
        bundled = packaged_agents_skills_dir() / "role" / "investigator"
        self.assertTrue(bundled.is_dir(), f"Bundled role/investigator skill missing: {bundled}")
        skill_md = bundled / "SKILL.md"
        self.assertTrue(skill_md.is_file(), f"Bundled role/investigator SKILL.md missing: {skill_md}")

    def test_role_investigator_fallback_to_bundled_when_absent_in_project(self):
        import tempfile
        from agent_takt.skills import _skill_path
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            result = _skill_path(repo_root, "role/investigator")
            bundled = packaged_agents_skills_dir() / "role" / "investigator"
            self.assertEqual(result, bundled)


class TestInvestigatorSkillIds(unittest.TestCase):
    """skills.allowed_skill_ids returns the correct entries for investigator."""

    def test_allowed_skill_ids_for_investigator(self):
        from agent_takt.skills import allowed_skill_ids
        ids = allowed_skill_ids("investigator")
        self.assertEqual(ids, ["core/base-orchestrator", "role/investigator", "memory"])

    def test_investigator_skill_ids_count(self):
        from agent_takt.skills import allowed_skill_ids
        ids = allowed_skill_ids("investigator")
        self.assertEqual(len(ids), 3)


if __name__ == "__main__":
    unittest.main()
