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

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ROOT = REPO_ROOT / "templates"
DATA_TEMPLATES_ROOT = REPO_ROOT / "src" / "agent_takt" / "_data" / "templates"


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


if __name__ == "__main__":
    unittest.main()
