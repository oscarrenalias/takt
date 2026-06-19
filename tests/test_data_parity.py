"""Parity test: every file under templates/ must have an identical twin in _data/templates/.

This catches the class of bug where a new agent guardrail or skill is added to the
source templates/ tree but never mirrored into the packaged _data/ tree, causing
installed takt to fail with "Unsupported agent type" or "Missing required skill
directory" errors.

The test walks the canonical source (templates/) and asserts that each relative path
exists under src/agent_takt/_data/templates/ with byte-identical content.  Adding a
new file to templates/ without mirroring it will make a test in this module fail
before the code ships.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ROOT = REPO_ROOT / "templates"
DATA_TEMPLATES_ROOT = REPO_ROOT / "src" / "agent_takt" / "_data" / "templates"
DEFAULT_CONFIG_PATH = REPO_ROOT / "src" / "agent_takt" / "_data" / "default_config.yaml"


class TestDataTemplateParity(unittest.TestCase):
    """Every file in templates/ must exist with identical content in _data/templates/."""

    def _source_files(self):
        return sorted(
            p.relative_to(TEMPLATES_ROOT)
            for p in TEMPLATES_ROOT.rglob("*")
            if p.is_file()
        )

    def test_no_source_only_files(self):
        """No file should exist in templates/ without a twin in _data/templates/."""
        missing = []
        for rel in self._source_files():
            mirror = DATA_TEMPLATES_ROOT / rel
            if not mirror.exists():
                missing.append(str(rel))
        self.assertEqual(
            missing,
            [],
            f"Files in templates/ that are absent from _data/templates/:\n"
            + "\n".join(f"  {f}" for f in missing),
        )

    def test_all_mirrored_files_have_identical_content(self):
        """Mirror files must be byte-for-byte identical to their source counterparts."""
        diffs = []
        for rel in self._source_files():
            mirror = DATA_TEMPLATES_ROOT / rel
            if not mirror.exists():
                continue  # already caught by test_no_source_only_files
            src_bytes = (TEMPLATES_ROOT / rel).read_bytes()
            mirror_bytes = mirror.read_bytes()
            if src_bytes != mirror_bytes:
                diffs.append(str(rel))
        self.assertEqual(
            diffs,
            [],
            f"Files whose _data/templates/ mirror differs from templates/ source:\n"
            + "\n".join(f"  {f}" for f in diffs),
        )

    def test_defect_agent_template_present(self):
        """Regression: templates/agents/defect.md must be mirrored (was missing in v0.1.54)."""
        mirror = DATA_TEMPLATES_ROOT / "agents" / "defect.md"
        self.assertTrue(mirror.is_file(), f"Bundled defect.md missing: {mirror}")
        source = TEMPLATES_ROOT / "agents" / "defect.md"
        self.assertEqual(mirror.read_bytes(), source.read_bytes(), "defect.md content mismatch")

    def test_recovery_agent_template_present(self):
        """Regression: templates/agents/recovery.md must be mirrored (was missing in v0.1.54)."""
        mirror = DATA_TEMPLATES_ROOT / "agents" / "recovery.md"
        self.assertTrue(mirror.is_file(), f"Bundled recovery.md missing: {mirror}")
        source = TEMPLATES_ROOT / "agents" / "recovery.md"
        self.assertEqual(mirror.read_bytes(), source.read_bytes(), "recovery.md content mismatch")

    def test_defect_fix_skill_present(self):
        """Regression: templates/skills/role/defect-fix/ must be mirrored (was missing in v0.1.54)."""
        mirror = DATA_TEMPLATES_ROOT / "skills" / "role" / "defect-fix" / "SKILL.md"
        self.assertTrue(mirror.is_file(), f"Bundled defect-fix/SKILL.md missing: {mirror}")
        source = TEMPLATES_ROOT / "skills" / "role" / "defect-fix" / "SKILL.md"
        self.assertEqual(mirror.read_bytes(), source.read_bytes(), "defect-fix/SKILL.md content mismatch")


class TestDefaultConfigAgentTypesParity(unittest.TestCase):
    """default_config.yaml agent_types must exactly match BUILT_IN_AGENT_TYPES.

    This catches the class of bug where a new agent type is added to BUILT_IN_AGENT_TYPES
    in prompts.py but not added to the scaffold template, causing fresh projects created
    with `takt init` to reject the new agent type with "Unsupported agent type" errors.
    """

    def _load_built_in_agent_types(self) -> set[str]:
        from agent_takt.prompts import BUILT_IN_AGENT_TYPES
        return set(BUILT_IN_AGENT_TYPES)

    def _load_config_agent_types(self) -> set[str]:
        raw = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        return set(data["common"]["agent_types"])

    def test_agent_types_match_built_in(self):
        """default_config.yaml common.agent_types must equal set(BUILT_IN_AGENT_TYPES)."""
        built_in = self._load_built_in_agent_types()
        config_types = self._load_config_agent_types()
        self.assertEqual(
            config_types,
            built_in,
            f"Mismatch between default_config.yaml agent_types and BUILT_IN_AGENT_TYPES.\n"
            f"  In config only (add to BUILT_IN_AGENT_TYPES or remove from config): {config_types - built_in}\n"
            f"  In BUILT_IN_AGENT_TYPES only (add to default_config.yaml): {built_in - config_types}",
        )


if __name__ == "__main__":
    unittest.main()
