"""End-to-end tests for structured assumptions in worker handoffs.

Covers _normalize_assumptions, model defaults/round-trip, runner payload mapping,
dependency-assumption prompt rendering, recovery prompt wording, guardrail
templates and the `bead show --field handoff_summary.assumptions` projection.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli.commands.bead import command_bead
from agent_takt.console import ConsoleReporter
from agent_takt.models import AgentRunResult, Bead, HandoffSummary
from agent_takt.prompts import (
    build_recovery_prompt,
    build_worker_prompt,
    render_dep_handoff_context,
)
from agent_takt.runner import PLANNER_OUTPUT_SCHEMA, _payload_to_run_result
from agent_takt.scheduler.finalize import _normalize_assumptions
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests  # noqa: E402


def _a(statement: str, rationale: str = "why", scope: str = "structural") -> dict[str, str]:
    return {"statement": statement, "rationale": rationale, "scope": scope}


class NormalizeAssumptionsTests(unittest.TestCase):
    def test_non_list_input_returns_empty(self) -> None:
        for raw in (None, "text", {"statement": "x"}, 5):
            self.assertEqual([], _normalize_assumptions(raw), repr(raw))

    def test_non_dict_entries_skipped(self) -> None:
        raw = ["str", None, 3, [1], _a("kept")]
        self.assertEqual([_a("kept")], _normalize_assumptions(raw))

    def test_empty_and_whitespace_statement_dropped(self) -> None:
        raw = [_a(""), _a("   \n\t"), {"rationale": "r"}, _a("real")]
        self.assertEqual([_a("real")], _normalize_assumptions(raw))

    def test_scope_mapping(self) -> None:
        for scope, expected in (("", "structural"), ("major", "structural"),
                                ("local", "local"), ("structural", "structural")):
            out = _normalize_assumptions([_a("s", scope=scope)])
            self.assertEqual(expected, out[0]["scope"], scope)

    def test_missing_scope_defaults_to_structural(self) -> None:
        out = _normalize_assumptions([{"statement": "s", "rationale": "r"}])
        self.assertEqual("structural", out[0]["scope"])

    def test_duplicates_keep_first_and_order_preserved(self) -> None:
        raw = [_a("b", "first", "local"), _a("a"), _a("b", "second", "structural")]
        out = _normalize_assumptions(raw)
        self.assertEqual(["b", "a"], [e["statement"] for e in out])
        self.assertEqual("first", out[0]["rationale"])
        self.assertEqual("local", out[0]["scope"])

    def test_output_keys_exact_and_strings(self) -> None:
        out = _normalize_assumptions([{"statement": 12, "rationale": None, "scope": 7, "extra": "x"}])
        self.assertEqual(["rationale", "scope", "statement"], sorted(out[0]))
        self.assertEqual({"statement": "12", "rationale": "", "scope": "structural"}, out[0])

    def test_whitespace_trimmed(self) -> None:
        out = _normalize_assumptions([{"statement": "  s  ", "rationale": " r ", "scope": " local "}])
        self.assertEqual({"statement": "s", "rationale": "r", "scope": "local"}, out[0])


class ModelAssumptionTests(unittest.TestCase):
    def test_defaults_are_empty_lists(self) -> None:
        self.assertEqual([], HandoffSummary().assumptions)
        self.assertEqual([], AgentRunResult(outcome="completed", summary="s").assumptions)

    def test_defaults_not_shared(self) -> None:
        a, b = HandoffSummary(), HandoffSummary()
        a.assumptions.append(_a("x"))
        self.assertEqual([], b.assumptions)

    def test_legacy_bead_json_without_key_loads_empty(self) -> None:
        bead = Bead(bead_id="B-legacy01", title="t", agent_type="developer", description="d")
        data = bead.to_dict()
        del data["handoff_summary"]["assumptions"]
        self.assertEqual([], Bead.from_dict(data).handoff_summary.assumptions)

    def test_to_dict_contains_key_and_round_trips(self) -> None:
        bead = Bead(bead_id="B-rt000001", title="t", agent_type="developer", description="d")
        self.assertEqual([], bead.to_dict()["handoff_summary"]["assumptions"])
        bead.handoff_summary.assumptions = [_a("one", scope="local"), _a("two")]
        data = json.loads(json.dumps(bead.to_dict()))
        self.assertEqual(bead.handoff_summary.assumptions, Bead.from_dict(data).handoff_summary.assumptions)


class RunnerPayloadTests(unittest.TestCase):
    def _payload(self, **extra) -> dict:
        return {"outcome": "completed", "summary": "s", **extra}

    def test_payload_with_assumptions_passes_through(self) -> None:
        items = [_a("x", scope="local")]
        result = _payload_to_run_result(self._payload(assumptions=items), "developer")
        self.assertEqual(items, result.assumptions)

    def test_payload_without_assumptions_defaults_empty(self) -> None:
        self.assertEqual([], _payload_to_run_result(self._payload(), "developer").assumptions)

    def test_investigator_payload_has_default_assumptions(self) -> None:
        result = _payload_to_run_result(self._payload(findings="f"), "investigator")
        self.assertEqual([], result.assumptions)


class SchemaScopeTests(unittest.TestCase):
    def test_planner_schema_unchanged(self) -> None:
        self.assertNotIn("assumptions", PLANNER_OUTPUT_SCHEMA["properties"])
        self.assertNotIn("assumptions", PLANNER_OUTPUT_SCHEMA.get("required", []))


class RecoveryPromptAssumptionTests(unittest.TestCase):
    def test_prompt_instructs_assumption_extraction(self) -> None:
        bead = Bead(bead_id="B-rp000001", title="t", agent_type="developer", description="d")
        prompt = build_recovery_prompt(bead, "prose", "")
        self.assertIn("assumptions", prompt)
        self.assertIn("local", prompt)
        self.assertIn("structural", prompt)
        self.assertIn("Use `structural` when unsure", prompt)
        self.assertIn("empty list `[]`", prompt)


class DepAssumptionPromptTests(OrchestratorTests):
    def _bead(self, agent_type: str) -> Bead:
        return self.storage.create_bead(title="x", agent_type=agent_type, description="d")

    def _deps(self):
        h1 = HandoffSummary(assumptions=[_a("loc one", "r1", "local"), _a("struct one", "r2", "structural")])
        h2 = HandoffSummary(assumptions=[_a("struct two", "", "structural")])
        return [("B-dep1", h1), ("B-dep2", h2)]

    def test_review_lists_all_structural_first_with_source_ids(self) -> None:
        out = render_dep_handoff_context("review", self._deps())
        self.assertIn("Assumptions from dependencies:", out)
        self.assertIn("[structural] struct one — r2 (from B-dep1)", out)
        self.assertIn("[local] loc one — r1 (from B-dep1)", out)
        self.assertLess(out.index("[structural] struct one"), out.index("[local] loc one"))
        self.assertLess(out.index("[structural] struct two"), out.index("[local] loc one"))

    def test_empty_rationale_drops_separator(self) -> None:
        out = render_dep_handoff_context("review", self._deps())
        self.assertIn("[structural] struct two (from B-dep2)", out)
        self.assertNotIn("struct two —", out)

    def test_tester_shows_only_structural(self) -> None:
        out = render_dep_handoff_context("tester", self._deps())
        self.assertIn("struct one", out)
        self.assertIn("struct two", out)
        self.assertNotIn("loc one", out)

    def test_tester_with_only_local_has_no_block(self) -> None:
        deps = [("B-d", HandoffSummary(assumptions=[_a("only local", scope="local")]))]
        self.assertNotIn("Assumptions from dependencies", render_dep_handoff_context("tester", deps))

    def test_no_assumptions_no_block(self) -> None:
        for agent in ("review", "tester"):
            out = render_dep_handoff_context(agent, [("B-d", HandoffSummary(completed="c"))])
            self.assertNotIn("Assumptions from dependencies", out)

    def test_plain_handoff_without_ids_renders(self) -> None:
        out = render_dep_handoff_context("review", [HandoffSummary(assumptions=[_a("s", "r")])])
        self.assertIn("[structural] s — r", out)
        self.assertNotIn("(from", out)

    def test_other_agent_prompts_have_no_block(self) -> None:
        for agent in ("developer", "documentation", "defect"):
            bead = self._bead(agent)
            prompt = build_worker_prompt(bead, [], self.root, dep_handoffs=self._deps())
            self.assertNotIn("Assumptions from dependencies", prompt, agent)

    def test_review_and_tester_worker_prompts_include_block(self) -> None:
        for agent in ("review", "tester"):
            bead = self._bead(agent)
            prompt = build_worker_prompt(bead, [], self.root, dep_handoffs=self._deps())
            self.assertIn("Assumptions from dependencies:", prompt, agent)
            self.assertIn("[structural] struct one — r2 (from B-dep1)", prompt, agent)


class GuardrailTemplateTests(unittest.TestCase):
    AGENTS = ("developer", "defect", "tester", "documentation", "review")

    def _read(self, base: Path, name: str) -> str:
        return (base / "agents" / f"{name}.md").read_text(encoding="utf-8")

    def test_assumptions_sections_present(self) -> None:
        base = REPO_ROOT / "templates"
        for name in ("developer", "defect", "tester", "documentation"):
            text = self._read(base, name)
            self.assertIn("## Assumptions", text, name)
            self.assertIn("`structural`", text, name)
            self.assertIn("public contract", text, name)
            self.assertIn("persisted format", text, name)
            self.assertIn("security", text, name)
            self.assertIn("hard to reverse", text, name)
            self.assertIn("interface between modules", text, name)

    def test_review_has_structural_check_rule(self) -> None:
        text = self._read(REPO_ROOT / "templates", "review")
        self.assertIn("Check every structural assumption", text)
        self.assertIn("Assumptions from dependencies", text)
        self.assertIn("Accepted structural assumption", text)
        self.assertIn("block_reason", text)

    def test_templates_byte_identical_to_data_copies(self) -> None:
        data = SRC_ROOT / "agent_takt" / "_data" / "templates"
        for name in self.AGENTS:
            src = (REPO_ROOT / "templates" / "agents" / f"{name}.md").read_bytes()
            dst = (data / "agents" / f"{name}.md").read_bytes()
            self.assertEqual(src, dst, name)


class BeadShowAssumptionsFieldTests(OrchestratorTests):
    def _show(self, bead_id: str) -> str:
        stream = io.StringIO()
        code = command_bead(
            Namespace(bead_command="show", bead_id=bead_id, field="handoff_summary.assumptions"),
            self.storage, ConsoleReporter(stream=stream),
        )
        self.assertEqual(0, code)
        return stream.getvalue()

    def test_prints_json_list_when_present(self) -> None:
        bead = self.storage.create_bead(title="a", agent_type="developer", description="d")
        bead.handoff_summary = HandoffSummary(assumptions=[_a("s1", "r1", "local")])
        self.storage.save_bead(bead)
        self.assertEqual([_a("s1", "r1", "local")], json.loads(self._show(bead.bead_id)))

    def test_prints_empty_list_when_absent(self) -> None:
        bead = self.storage.create_bead(title="b", agent_type="developer", description="d")
        self.assertEqual([], json.loads(self._show(bead.bead_id)))


if __name__ == "__main__":
    unittest.main()
