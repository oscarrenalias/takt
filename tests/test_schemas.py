from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.runner import AGENT_OUTPUT_SCHEMA, PLANNER_OUTPUT_SCHEMA


class SchemaTests(unittest.TestCase):

    def test_agent_output_schema_requires_all_new_bead_fields(self) -> None:
        required = AGENT_OUTPUT_SCHEMA["properties"]["new_beads"]["items"]["required"]
        self.assertEqual(
            ["title", "agent_type", "description", "acceptance_criteria", "dependencies", "linked_docs", "expected_files", "expected_globs"],
            required,
        )

    def test_agent_output_schema_requires_every_top_level_property(self) -> None:
        # Structured handoff fields (design_decisions, test_coverage_notes, known_limitations)
        # are intentionally optional (have defaults), so they appear in properties but not required.
        optional_fields = {"design_decisions", "test_coverage_notes", "known_limitations"}
        required_properties = [
            k for k in AGENT_OUTPUT_SCHEMA["properties"].keys()
            if k not in optional_fields
        ]
        self.assertEqual(required_properties, AGENT_OUTPUT_SCHEMA["required"])

    def test_agent_output_schema_new_beads_agent_type_has_valid_enum(self) -> None:
        agent_type_schema = AGENT_OUTPUT_SCHEMA["properties"]["new_beads"]["items"]["properties"]["agent_type"]
        self.assertIn("enum", agent_type_schema)
        self.assertEqual(
            sorted(agent_type_schema["enum"]),
            ["developer", "documentation", "planner", "review", "tester"],
        )

    def test_planner_output_schema_plan_child_agent_type_has_valid_enum(self) -> None:
        agent_type_schema = PLANNER_OUTPUT_SCHEMA["$defs"]["plan_child"]["properties"]["agent_type"]
        self.assertIn("enum", agent_type_schema)
        self.assertEqual(
            sorted(agent_type_schema["enum"]),
            ["developer", "documentation", "planner", "review", "tester"],
        )


if __name__ == "__main__":
    unittest.main()
