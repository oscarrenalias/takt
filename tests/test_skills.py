from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.skills import AGENT_SKILL_ALLOWLIST, allowed_skill_ids

_EXPECTED_DEFECT_SKILLS = frozenset({
    "core/base-orchestrator",
    "role/defect-fix",
    "capability/code-edit",
    "capability/test-execution",
    "task/corrective-implementation",
    "memory",
})


class DefectSkillAllowlistTests(unittest.TestCase):

    def test_defect_in_agent_skill_allowlist(self) -> None:
        self.assertIn("defect", AGENT_SKILL_ALLOWLIST)

    def test_allowed_skill_ids_defect_returns_six_skills(self) -> None:
        skills = allowed_skill_ids("defect")
        self.assertEqual(6, len(skills))

    def test_allowed_skill_ids_defect_exact_bundle(self) -> None:
        skills = set(allowed_skill_ids("defect"))
        self.assertEqual(_EXPECTED_DEFECT_SKILLS, skills)

    def test_allowed_skill_ids_defect_includes_base_orchestrator(self) -> None:
        self.assertIn("core/base-orchestrator", allowed_skill_ids("defect"))

    def test_allowed_skill_ids_defect_includes_defect_fix_role(self) -> None:
        self.assertIn("role/defect-fix", allowed_skill_ids("defect"))

    def test_allowed_skill_ids_defect_includes_code_edit(self) -> None:
        self.assertIn("capability/code-edit", allowed_skill_ids("defect"))

    def test_allowed_skill_ids_defect_includes_test_execution(self) -> None:
        self.assertIn("capability/test-execution", allowed_skill_ids("defect"))

    def test_allowed_skill_ids_defect_includes_corrective_implementation(self) -> None:
        self.assertIn("task/corrective-implementation", allowed_skill_ids("defect"))

    def test_allowed_skill_ids_defect_includes_memory(self) -> None:
        self.assertIn("memory", allowed_skill_ids("defect"))

    def test_existing_developer_skills_unaffected(self) -> None:
        dev_skills = allowed_skill_ids("developer")
        self.assertIn("role/developer-implementation", dev_skills)
        self.assertNotIn("role/defect-fix", dev_skills)

    def test_existing_tester_skills_unaffected(self) -> None:
        tester_skills = allowed_skill_ids("tester")
        self.assertIn("role/tester-validation", tester_skills)
        self.assertNotIn("role/defect-fix", tester_skills)

    def test_defect_skill_directory_exists(self) -> None:
        skill_dir = REPO_ROOT / "templates" / "skills" / "role" / "defect-fix"
        self.assertTrue(skill_dir.is_dir(), f"Expected skill dir at {skill_dir}")

    def test_defect_skill_directory_has_skill_md(self) -> None:
        skill_md = REPO_ROOT / "templates" / "skills" / "role" / "defect-fix" / "SKILL.md"
        self.assertTrue(skill_md.is_file(), f"Expected SKILL.md at {skill_md}")


if __name__ == "__main__":
    unittest.main()
