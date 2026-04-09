from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.runner import (
    AGENT_OUTPUT_SCHEMA,
    INVESTIGATOR_OUTPUT_SCHEMA,
    PLANNER_OUTPUT_SCHEMA,
    _payload_to_run_result,
    _worker_schema_for,
)


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
            ["developer", "documentation", "planner", "recovery", "review", "tester"],
        )

    def test_planner_output_schema_plan_child_agent_type_has_valid_enum(self) -> None:
        agent_type_schema = PLANNER_OUTPUT_SCHEMA["$defs"]["plan_child"]["properties"]["agent_type"]
        self.assertIn("enum", agent_type_schema)
        self.assertEqual(
            sorted(agent_type_schema["enum"]),
            ["developer", "documentation", "planner", "recovery", "review", "tester"],
        )


class InvestigatorSchemaTests(unittest.TestCase):

    def test_investigator_schema_requires_investigation_fields(self) -> None:
        required = INVESTIGATOR_OUTPUT_SCHEMA["required"]
        for field in ("outcome", "summary", "findings", "recommendations", "risk_areas", "report_path"):
            self.assertIn(field, required)

    def test_investigator_schema_excludes_worker_only_fields(self) -> None:
        props = INVESTIGATOR_OUTPUT_SCHEMA["properties"]
        for excluded in ("verdict", "changed_files", "next_agent"):
            self.assertNotIn(excluded, props)

    def test_investigator_schema_outcome_enum(self) -> None:
        outcome_schema = INVESTIGATOR_OUTPUT_SCHEMA["properties"]["outcome"]
        self.assertIn("enum", outcome_schema)
        self.assertEqual(sorted(outcome_schema["enum"]), ["blocked", "completed"])

    def test_investigator_schema_is_no_additional_properties(self) -> None:
        self.assertTrue(INVESTIGATOR_OUTPUT_SCHEMA.get("additionalProperties") is False)

    def test_investigator_schema_valid_payload_has_all_required_fields(self) -> None:
        valid_payload = {
            "outcome": "completed",
            "summary": "Investigated the scheduler package.",
            "findings": "Several complexity hotspots identified.",
            "recommendations": "Extract helper utilities.",
            "risk_areas": "High cyclomatic complexity in core.py.",
            "report_path": "docs/investigator/scheduler-audit.md",
        }
        required = INVESTIGATOR_OUTPUT_SCHEMA["required"]
        for field in required:
            self.assertIn(field, valid_payload)

    def test_investigator_schema_blocked_outcome_is_valid(self) -> None:
        blocked_payload = {
            "outcome": "blocked",
            "summary": "Cannot proceed.",
            "findings": "Access denied.",
            "recommendations": "Fix permissions.",
            "risk_areas": "N/A",
            "report_path": "docs/investigator/blocked.md",
            "block_reason": "Missing read access to secrets dir.",
        }
        required = INVESTIGATOR_OUTPUT_SCHEMA["required"]
        for field in required:
            self.assertIn(field, blocked_payload)
        self.assertIn("block_reason", INVESTIGATOR_OUTPUT_SCHEMA["properties"])

    def test_standard_schema_still_has_verdict_and_changed_files(self) -> None:
        """Regression: ensure AGENT_OUTPUT_SCHEMA is not affected by the investigator variant."""
        self.assertIn("verdict", AGENT_OUTPUT_SCHEMA["properties"])
        self.assertIn("changed_files", AGENT_OUTPUT_SCHEMA["properties"])
        self.assertIn("next_agent", AGENT_OUTPUT_SCHEMA["properties"])


class WorkerSchemaRoutingTests(unittest.TestCase):

    def test_worker_schema_for_investigator_returns_investigator_schema(self) -> None:
        self.assertIs(_worker_schema_for("investigator"), INVESTIGATOR_OUTPUT_SCHEMA)

    def test_worker_schema_for_developer_returns_agent_schema(self) -> None:
        self.assertIs(_worker_schema_for("developer"), AGENT_OUTPUT_SCHEMA)

    def test_worker_schema_for_tester_returns_agent_schema(self) -> None:
        self.assertIs(_worker_schema_for("tester"), AGENT_OUTPUT_SCHEMA)

    def test_worker_schema_for_review_returns_agent_schema(self) -> None:
        self.assertIs(_worker_schema_for("review"), AGENT_OUTPUT_SCHEMA)

    def test_worker_schema_for_unknown_returns_agent_schema(self) -> None:
        self.assertIs(_worker_schema_for("unknown"), AGENT_OUTPUT_SCHEMA)


class PayloadToRunResultTests(unittest.TestCase):

    def test_investigator_payload_maps_investigation_fields(self) -> None:
        payload = {
            "outcome": "completed",
            "summary": "Analysis complete.",
            "findings": "Several hotspots found.",
            "recommendations": "Refactor core.py.",
            "risk_areas": "High cyclomatic complexity.",
            "report_path": "docs/investigator/report.md",
        }
        result = _payload_to_run_result(payload, "investigator")
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.summary, "Analysis complete.")
        self.assertEqual(result.findings, "Several hotspots found.")
        self.assertEqual(result.recommendations, "Refactor core.py.")
        self.assertEqual(result.risk_areas, "High cyclomatic complexity.")
        self.assertEqual(result.report_path, "docs/investigator/report.md")

    def test_investigator_payload_maps_optional_block_reason(self) -> None:
        payload = {
            "outcome": "blocked",
            "summary": "Cannot proceed.",
            "findings": "Missing data.",
            "recommendations": "Provide access.",
            "risk_areas": "N/A",
            "report_path": "docs/investigator/blocked.md",
            "block_reason": "Read permission denied.",
        }
        result = _payload_to_run_result(payload, "investigator")
        self.assertEqual(result.block_reason, "Read permission denied.")

    def test_investigator_result_has_empty_verdict_and_changed_files(self) -> None:
        payload = {
            "outcome": "completed",
            "summary": "Done.",
            "findings": "None.",
            "recommendations": "None.",
            "risk_areas": "None.",
            "report_path": "docs/investigator/empty.md",
        }
        result = _payload_to_run_result(payload, "investigator")
        self.assertEqual(result.verdict, "")
        self.assertEqual(result.changed_files, [])
        self.assertEqual(result.next_agent, "")


if __name__ == "__main__":
    unittest.main()
