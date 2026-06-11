from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import AGENT_TYPES, BEAD_TYPES, MUTATING_AGENTS, Bead
from agent_takt.runner import AGENT_OUTPUT_SCHEMA, PLANNER_OUTPUT_SCHEMA


class DefectModelMembershipTests(unittest.TestCase):

    def test_defect_in_agent_types(self) -> None:
        self.assertIn("defect", AGENT_TYPES)

    def test_defect_in_bead_types(self) -> None:
        self.assertIn("defect", BEAD_TYPES)

    def test_defect_in_mutating_agents(self) -> None:
        self.assertIn("defect", MUTATING_AGENTS)

    def test_existing_agent_types_still_present(self) -> None:
        for t in ("planner", "developer", "tester", "documentation", "review", "recovery", "scheduler", "investigator"):
            self.assertIn(t, AGENT_TYPES, f"{t!r} should still be in AGENT_TYPES")

    def test_bead_construction_with_defect_agent_and_bead_type(self) -> None:
        bead = Bead(bead_id="B-deftest", title="Fix bug", agent_type="defect", description="fix it", bead_type="defect")
        self.assertEqual("defect", bead.agent_type)
        self.assertEqual("defect", bead.bead_type)

    def test_bead_default_status_is_open(self) -> None:
        bead = Bead(bead_id="B-deftest2", title="Fix", agent_type="defect", description="fix", bead_type="defect")
        self.assertEqual("open", bead.status)


class DefectSchemaEnumTests(unittest.TestCase):

    def _agent_output_new_beads_enum(self) -> list[str]:
        return AGENT_OUTPUT_SCHEMA["properties"]["new_beads"]["items"]["properties"]["agent_type"]["enum"]

    def _planner_output_plan_child_enum(self) -> list[str]:
        return PLANNER_OUTPUT_SCHEMA["$defs"]["plan_child"]["properties"]["agent_type"]["enum"]

    def test_agent_output_schema_new_beads_agent_type_includes_defect(self) -> None:
        self.assertIn("defect", self._agent_output_new_beads_enum())

    def test_planner_output_schema_plan_child_agent_type_includes_defect(self) -> None:
        self.assertIn("defect", self._planner_output_plan_child_enum())

    def test_agent_output_schema_new_beads_agent_type_excludes_unknown_value(self) -> None:
        self.assertNotIn("unknown_value", self._agent_output_new_beads_enum())

    def test_planner_output_schema_plan_child_agent_type_excludes_unknown_value(self) -> None:
        self.assertNotIn("unknown_value", self._planner_output_plan_child_enum())

    def test_agent_output_schema_new_beads_enum_contains_all_expected_types(self) -> None:
        enum = self._agent_output_new_beads_enum()
        for expected in ("planner", "developer", "tester", "documentation", "review", "recovery", "defect"):
            self.assertIn(expected, enum, f"Expected {expected!r} in AGENT_OUTPUT_SCHEMA new_beads enum")

    def test_planner_output_schema_plan_child_enum_contains_all_expected_types(self) -> None:
        enum = self._planner_output_plan_child_enum()
        for expected in ("planner", "developer", "tester", "documentation", "review", "recovery", "defect"):
            self.assertIn(expected, enum, f"Expected {expected!r} in PLANNER_OUTPUT_SCHEMA plan_child enum")


class DefectConfigTests(unittest.TestCase):

    def test_default_config_agent_types_includes_defect(self) -> None:
        from agent_takt.config import default_config
        cfg = default_config()
        self.assertIn("defect", cfg.agent_types)

    def test_orchestrator_config_field_default_includes_defect(self) -> None:
        from agent_takt.config import OrchestratorConfig
        cfg = OrchestratorConfig()
        self.assertIn("defect", cfg.agent_types)

    def test_default_config_existing_agent_types_unaffected(self) -> None:
        from agent_takt.config import default_config
        cfg = default_config()
        for t in ("planner", "developer", "tester", "documentation", "review", "recovery", "investigator"):
            self.assertIn(t, cfg.agent_types)

    def test_default_config_claude_allowed_tools_for_defect(self) -> None:
        from agent_takt.config import default_config
        cfg = default_config()
        tools = cfg.allowed_tools_for("claude", "defect")
        for expected in ("Bash", "Edit", "Write", "Read", "Glob", "Grep", "Skill", "WebFetch", "WebSearch"):
            self.assertIn(expected, tools, f"Expected {expected!r} in allowed tools for defect agent")

    def test_config_without_agent_types_key_still_has_defect(self) -> None:
        import tempfile
        from pathlib import Path
        from agent_takt.config import load_config
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            takt_dir = root / ".takt"
            takt_dir.mkdir()
            # Write a config.yaml that omits agent_types entirely
            (takt_dir / "config.yaml").write_text(
                "common:\n  commit_bead_state: false\n", encoding="utf-8"
            )
            cfg = load_config(root)
            self.assertIn("defect", cfg.agent_types)


if __name__ == "__main__":
    unittest.main()
