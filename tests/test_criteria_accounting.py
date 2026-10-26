"""End-to-end tests for acceptance criteria accounting (spec-e93e36bf).

Covers models compat, runner payload mapping, reconciliation, finalize wiring,
own/dependency verdict overrides, metadata lifecycle, corrective inheritance and
the full resolve cycle, prompt blocks, scenario citations, recovery path,
events.jsonl payloads, summary aggregate and guardrail parity.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.gitutils import WorktreeManager
from agent_takt.models import (
    BEAD_BLOCKED,
    BEAD_DONE,
    BEAD_READY,
    AgentRunResult,
    Bead,
    HandoffSummary,
)
from agent_takt.prompts import (
    AGENT_OUTPUT_SCHEMA as PROMPTS_SCHEMA,
    build_recovery_prompt,
    build_worker_prompt,
    render_dep_handoff_context,
)
from agent_takt.runner import (
    AGENT_OUTPUT_SCHEMA as RUNNER_SCHEMA,
    NO_STRUCTURED_OUTPUT_SENTINEL,
    _payload_to_run_result,
)
from agent_takt.scheduler import Scheduler
from agent_takt.scheduler.criteria import (
    dependency_criteria_state,
    dependency_descoped_entries,
    resolve_criterion_text,
)
from agent_takt.scheduler.finalize import (
    _reconcile_criteria_status,
    _verify_scenario_citations,
)
from agent_takt.spec_requirements import criterion_matches_prefix
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import FakeRunner, OrchestratorTests  # noqa: E402


def _entry(index: int, criterion: str, status: str, evidence: str = "e") -> dict:
    return {"index": index, "criterion": criterion, "status": status, "evidence": evidence}


def _met_all(criteria: list[str]) -> list[dict]:
    return [{"index": i, "status": "met", "evidence": f"done {i}"} for i in range(len(criteria))]


# ---------------------------------------------------------------------------
# Schemas, models, runner payloads
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    EXPECTED_ITEM = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "status": {"type": "string", "enum": ["met", "unmet", "descoped"]},
            "evidence": {"type": "string"},
        },
        "required": ["index", "status", "evidence"],
    }

    def test_both_schemas_list_criteria_status_as_required(self) -> None:
        for schema in (RUNNER_SCHEMA, PROMPTS_SCHEMA):
            self.assertIn("criteria_status", schema["properties"])
            self.assertIn("criteria_status", schema["required"])

    def test_both_schemas_use_identical_item_schema(self) -> None:
        for schema in (RUNNER_SCHEMA, PROMPTS_SCHEMA):
            prop = schema["properties"]["criteria_status"]
            self.assertEqual("array", prop["type"])
            self.assertEqual(self.EXPECTED_ITEM, prop["items"])
        self.assertEqual(
            RUNNER_SCHEMA["properties"]["criteria_status"],
            PROMPTS_SCHEMA["properties"]["criteria_status"],
        )

    def test_status_enum_and_minimum_index_constraints(self) -> None:
        item = RUNNER_SCHEMA["properties"]["criteria_status"]["items"]
        self.assertEqual(["met", "unmet", "descoped"], item["properties"]["status"]["enum"])
        self.assertEqual(0, item["properties"]["index"]["minimum"])
        self.assertNotIn("bogus", item["properties"]["status"]["enum"])


class ModelCompatTests(OrchestratorTests):
    def test_defaults_are_empty_and_not_shared(self) -> None:
        self.assertEqual([], AgentRunResult(outcome="completed", summary="s").criteria_status)
        a, b = HandoffSummary(), HandoffSummary()
        a.criteria_status.append({"index": 0})
        self.assertEqual([], b.criteria_status)

    def test_legacy_bead_json_without_criteria_status_loads_empty(self) -> None:
        bead = Bead(bead_id="B-legacy01", title="t", agent_type="developer", description="d")
        data = bead.to_dict()
        del data["handoff_summary"]["criteria_status"]
        self.assertEqual([], Bead.from_dict(data).handoff_summary.criteria_status)

    def test_criteria_status_round_trips_through_storage(self) -> None:
        bead = self.storage.create_bead(title="t", agent_type="developer", description="d")
        bead.handoff_summary.criteria_status = [_entry(0, "a", "met"), _entry(1, "b", "unmet", "why")]
        self.storage.save_bead(bead)
        loaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(bead.handoff_summary.criteria_status, loaded.handoff_summary.criteria_status)

    def test_legacy_bead_in_storage_never_triggers_override(self) -> None:
        review = self.storage.create_bead(
            title="r", agent_type="review", description="d", acceptance_criteria=[]
        )
        data = review.to_dict()
        del data["handoff_summary"]["criteria_status"]
        path = self.storage.bead_path(review.bead_id)
        path.write_text(json.dumps(data), encoding="utf-8")
        runner = FakeRunner(results={review.bead_id: AgentRunResult(
            outcome="completed", summary="ok", verdict="approved", requires_followup=False)})
        Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir)).run_once()
        self.assertEqual(BEAD_DONE, self.storage.load_bead(review.bead_id).status)

    def test_payload_with_criteria_status_passes_through(self) -> None:
        items = [{"index": 0, "status": "met", "evidence": "x"}]
        result = _payload_to_run_result(
            {"outcome": "completed", "summary": "s", "criteria_status": items}, "developer"
        )
        self.assertEqual(items, result.criteria_status)

    def test_payload_without_criteria_status_defaults_empty(self) -> None:
        result = _payload_to_run_result({"outcome": "completed", "summary": "s"}, "developer")
        self.assertEqual([], result.criteria_status)


# ---------------------------------------------------------------------------
# Reconciliation unit cases
# ---------------------------------------------------------------------------

class ReconcileTests(unittest.TestCase):
    def test_three_criteria_example(self) -> None:
        out = _reconcile_criteria_status(
            ["a", "b", "c"],
            [
                {"index": 0, "status": "met", "evidence": "src/x.py::f"},
                {"index": 2, "status": "descoped", "evidence": "owned by tester bead"},
            ],
        )
        self.assertEqual(
            [
                _entry(0, "a", "met", "src/x.py::f"),
                _entry(1, "b", "unmet", "not reported by agent"),
                _entry(2, "c", "descoped", "owned by tester bead"),
            ],
            out,
        )

    def test_duplicate_indices_keep_first(self) -> None:
        out = _reconcile_criteria_status(
            ["a"],
            [{"index": 0, "status": "met", "evidence": "first"},
             {"index": 0, "status": "unmet", "evidence": "second"}],
        )
        self.assertEqual([_entry(0, "a", "met", "first")], out)

    def test_invalid_indices_dropped(self) -> None:
        for bad in (5, -1, True, "0", 1.0, None):
            out = _reconcile_criteria_status(
                ["a"], [{"index": bad, "status": "met", "evidence": "x"}]
            )
            self.assertEqual([_entry(0, "a", "unmet", "not reported by agent")], out, repr(bad))

    def test_descoped_without_reason_becomes_unmet(self) -> None:
        for ev in ("", "   \n", None):
            out = _reconcile_criteria_status(
                ["a"], [{"index": 0, "status": "descoped", "evidence": ev}]
            )
            self.assertEqual([_entry(0, "a", "unmet", "descoped without reason")], out, repr(ev))

    def test_unknown_status_is_unreported(self) -> None:
        out = _reconcile_criteria_status(["a"], [{"index": 0, "status": "maybe", "evidence": "x"}])
        self.assertEqual([_entry(0, "a", "unmet", "not reported by agent")], out)

    def test_empty_criteria_returns_empty(self) -> None:
        self.assertEqual([], _reconcile_criteria_status([], [{"index": 0, "status": "met", "evidence": "x"}]))

    def test_non_list_raw_returns_empty(self) -> None:
        for raw in (None, "text", {"index": 0}, 3):
            self.assertEqual([], _reconcile_criteria_status(["a"], raw), repr(raw))

    def test_non_dict_entries_skipped(self) -> None:
        out = _reconcile_criteria_status(["a"], ["x", None, 4, [0], {"index": 0, "status": "met", "evidence": "e"}])
        self.assertEqual([_entry(0, "a", "met", "e")], out)

    def test_output_keys_exact(self) -> None:
        out = _reconcile_criteria_status(
            ["a"], [{"index": 0, "status": "met", "evidence": "e", "criterion": "spoof", "extra": 1}]
        )
        self.assertEqual(["criterion", "evidence", "index", "status"], sorted(out[0]))
        self.assertEqual("a", out[0]["criterion"])


# ---------------------------------------------------------------------------
# Shared scheduler helpers
# ---------------------------------------------------------------------------

class SchedulerCase(OrchestratorTests):
    def _scheduler(self, runner: FakeRunner) -> Scheduler:
        return Scheduler(self.storage, runner, WorktreeManager(self.root, self.storage.worktrees_dir))

    def _run_all(self, runner: FakeRunner, max_cycles: int = 8) -> None:
        """Run scheduler cycles until a cycle starts nothing (reactive follow-ups may need several)."""
        scheduler = self._scheduler(runner)
        for _ in range(max_cycles):
            if not scheduler.run_once(max_workers=1).started:
                break

    def _events(self, event_type: str) -> list[dict]:
        path = self.storage.logs_dir / "events.jsonl"
        if not path.exists():
            return []
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        return [r["payload"] for r in records if r["event_type"] == event_type]

    def _history(self, bead_id: str, event: str) -> list:
        return [r for r in self.storage.load_bead(bead_id).execution_history if r.event == event]

    def _run_bead(self, agent_type: str, criteria: list[str], reported, **result_kwargs):
        bead = self.storage.create_bead(
            title="B", agent_type=agent_type, description="d", acceptance_criteria=criteria
        )
        kwargs = dict(outcome="completed", summary="done", completed="x", requires_followup=False)
        kwargs.update(result_kwargs)
        runner = FakeRunner(results={bead.bead_id: AgentRunResult(criteria_status=reported, **kwargs)})
        self._scheduler(runner).run_once()
        return self.storage.load_bead(bead.bead_id)

    def _done_dev_with_status(self, entries: list[dict], criteria: list[str] | None = None) -> Bead:
        criteria = criteria or [e["criterion"] for e in entries]
        dev = self.storage.create_bead(
            title="Dev", agent_type="developer", description="d", acceptance_criteria=criteria
        )
        dev.status = BEAD_DONE
        dev.handoff_summary.criteria_status = entries
        self.storage.save_bead(dev)
        return dev

    def _review(self, dep_ids: list[str], criteria: list[str] | None = None) -> Bead:
        return self.storage.create_bead(
            title="Review", agent_type="review", description="d",
            dependencies=dep_ids, acceptance_criteria=criteria or [],
            status=BEAD_READY,
        )


# ---------------------------------------------------------------------------
# Finalize wiring and own-criteria overrides
# ---------------------------------------------------------------------------

class FinalizeWiringTests(SchedulerCase):
    def test_developer_persists_reconciled_status_with_criterion_text(self) -> None:
        bead = self._run_bead(
            "developer", ["a", "b", "c"],
            [{"index": 0, "status": "met", "evidence": "x"},
             {"index": 2, "status": "descoped", "evidence": "tester owns it"}],
        )
        status = bead.handoff_summary.criteria_status
        self.assertEqual(["met", "unmet", "descoped"], [s["status"] for s in status])
        self.assertEqual(["a", "b", "c"], [s["criterion"] for s in status])
        self.assertEqual("not reported by agent", status[1]["evidence"])

    def test_bead_without_criteria_persists_empty_list(self) -> None:
        bead = self._run_bead("developer", [], [{"index": 0, "status": "met", "evidence": "x"}])
        self.assertEqual([], bead.handoff_summary.criteria_status)

    def test_planner_beads_keep_empty_criteria_status(self) -> None:
        bead = self._run_bead("planner", ["a"], [{"index": 0, "status": "met", "evidence": "x"}])
        self.assertEqual([], bead.handoff_summary.criteria_status)

    def test_developer_with_unmet_criterion_ends_done(self) -> None:
        bead = self._run_bead("developer", ["a"], [{"index": 0, "status": "unmet", "evidence": "no"}])
        self.assertEqual(BEAD_DONE, bead.status)
        self.assertEqual([], self._history(bead.bead_id, "verdict_overridden"))

    def test_reconciliation_happens_before_verdict_logic(self) -> None:
        # Agent reports nothing: reconciliation makes the criterion unmet, and the
        # override therefore fires on the reconciled list.
        bead = self._run_bead("tester", ["a"], [], verdict="approved")
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual("needs_changes", bead.handoff_summary.verdict)
        self.assertEqual("unmet", bead.handoff_summary.criteria_status[0]["status"])

    def test_tester_approved_with_unmet_criterion_is_blocked(self) -> None:
        bead_id = None
        bead = self.storage.create_bead(
            title="T", agent_type="tester", description="d", acceptance_criteria=["first", "second"]
        )
        bead_id = bead.bead_id
        runner = FakeRunner(
            results={
                bead_id: AgentRunResult(
                    outcome="completed", summary="ok", verdict="approved", requires_followup=False,
                    criteria_status=[
                        {"index": 0, "status": "met", "evidence": "x"},
                        {"index": 1, "status": "unmet", "evidence": "not done"},
                    ],
                ),
                f"{bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
            },
        )
        self._scheduler(runner).run_once()
        loaded = self.storage.load_bead(bead_id)
        self.assertEqual(BEAD_BLOCKED, loaded.status)
        self.assertEqual("needs_changes", loaded.handoff_summary.verdict)
        self.assertTrue(loaded.block_reason.startswith("Approved with unmet acceptance criteria:"))
        self.assertIn("#1 second", loaded.block_reason)
        events = self._history(bead_id, "verdict_overridden")
        self.assertEqual(1, len(events))
        self.assertEqual(
            {"reason": "own_criteria", "unmet": [{"bead_id": bead_id, "index": 1}]},
            events[0].details,
        )
        correctives = [b for b in self.storage.list_beads() if b.metadata.get("auto_corrective_for") == bead_id]
        self.assertEqual(1, len(correctives))

    def test_review_behaves_like_tester_for_own_criteria(self) -> None:
        bead = self._run_bead(
            "review", ["a"], [{"index": 0, "status": "unmet", "evidence": "no"}], verdict="approved"
        )
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertTrue(bead.block_reason.startswith("Approved with unmet acceptance criteria:"))

    def test_compat_mode_without_verdict_is_overridden(self) -> None:
        bead = self._run_bead("tester", ["a"], [{"index": 0, "status": "unmet", "evidence": "no"}])
        self.assertEqual(BEAD_BLOCKED, bead.status)
        self.assertEqual("needs_changes", bead.handoff_summary.verdict)
        self.assertEqual(1, len(self._history(bead.bead_id, "verdict_overridden")))

    def test_long_criterion_text_truncated_to_80_chars(self) -> None:
        long = "x" * 200
        bead = self._run_bead(
            "tester", [long], [{"index": 0, "status": "unmet", "evidence": "no"}], verdict="approved"
        )
        self.assertIn("#0 " + "x" * 80, bead.block_reason)
        self.assertNotIn("x" * 81, bead.block_reason)

    def test_all_met_or_descoped_stays_approved(self) -> None:
        bead = self._run_bead(
            "tester", ["a", "b"],
            [{"index": 0, "status": "met", "evidence": "x"},
             {"index": 1, "status": "descoped", "evidence": "docs bead owns"}],
            verdict="approved",
        )
        self.assertEqual(BEAD_DONE, bead.status)
        self.assertEqual([], self._history(bead.bead_id, "verdict_overridden"))

    def test_legacy_empty_criteria_never_overridden(self) -> None:
        bead = self._run_bead("tester", [], [], verdict="approved")
        self.assertEqual(BEAD_DONE, bead.status)

    def test_override_event_in_events_jsonl_has_documented_payload(self) -> None:
        bead = self._run_bead(
            "review", ["a"], [{"index": 0, "status": "unmet", "evidence": "no"}], verdict="approved"
        )
        events = self._events("verdict_overridden")
        self.assertEqual(1, len(events))
        self.assertEqual(
            {
                "bead_id": bead.bead_id,
                "agent_type": "review",
                "feature_root_id": bead.feature_root_id,
                "reason": "own_criteria",
                "unmet": [{"bead_id": bead.bead_id, "index": 0}],
            },
            events[0],
        )


# ---------------------------------------------------------------------------
# criteria.py helpers and dependency-criteria gate
# ---------------------------------------------------------------------------

class DependencyCriteriaStateTests(SchedulerCase):
    def test_resolve_criterion_text_wording(self) -> None:
        self.assertEqual(
            "Resolve unmet criterion #1 from B-dep: do the thing",
            resolve_criterion_text("B-dep", 1, "do the thing"),
        )

    def test_unresolved_unmet_entry(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "met"), _entry(1, "b", "unmet", "missing")])
        review = self._review([dep.bead_id])
        unresolved, resolved = dependency_criteria_state(self.storage, review)
        self.assertEqual([], resolved)
        self.assertEqual(
            [{"bead_id": dep.bead_id, "index": 1, "criterion": "b", "evidence": "missing"}], unresolved
        )

    def _corrective(self, review: Bead, status: str, entries: list[dict], *, for_bead: str | None = None) -> Bead:
        corr = self.storage.create_bead(
            title="Corr", agent_type="developer", description="d", parent_id=review.bead_id,
            metadata={"auto_corrective_for": for_bead or review.bead_id},
        )
        corr.status = status
        corr.handoff_summary.criteria_status = entries
        self.storage.save_bead(corr)
        return corr

    def test_resolved_via_done_corrective_with_exact_text(self) -> None:
        dep = self._done_dev_with_status([_entry(1, "b", "unmet", "missing")])
        review = self._review([dep.bead_id])
        text = resolve_criterion_text(dep.bead_id, 1, "b")
        corr = self._corrective(review, BEAD_DONE, [_entry(3, text, "met", "src/fix.py")])
        unresolved, resolved = dependency_criteria_state(self.storage, review)
        self.assertEqual([], unresolved)
        self.assertEqual(1, len(resolved))
        self.assertEqual(corr.bead_id, resolved[0]["resolved_by"])
        self.assertEqual("src/fix.py", resolved[0]["evidence"])

    def test_corrective_not_done_is_ignored(self) -> None:
        dep = self._done_dev_with_status([_entry(1, "b", "unmet")])
        review = self._review([dep.bead_id])
        text = resolve_criterion_text(dep.bead_id, 1, "b")
        self._corrective(review, BEAD_READY, [_entry(0, text, "met")])
        unresolved, resolved = dependency_criteria_state(self.storage, review)
        self.assertEqual(1, len(unresolved))
        self.assertEqual([], resolved)

    def test_corrective_for_other_review_is_ignored(self) -> None:
        dep = self._done_dev_with_status([_entry(1, "b", "unmet")])
        review = self._review([dep.bead_id])
        other = self._review([dep.bead_id])
        text = resolve_criterion_text(dep.bead_id, 1, "b")
        self._corrective(review, BEAD_DONE, [_entry(0, text, "met")], for_bead=other.bead_id)
        unresolved, _ = dependency_criteria_state(self.storage, review)
        self.assertEqual(1, len(unresolved))

    def test_corrective_reporting_unmet_does_not_resolve(self) -> None:
        dep = self._done_dev_with_status([_entry(1, "b", "unmet")])
        review = self._review([dep.bead_id])
        text = resolve_criterion_text(dep.bead_id, 1, "b")
        self._corrective(review, BEAD_DONE, [_entry(0, text, "unmet")])
        unresolved, resolved = dependency_criteria_state(self.storage, review)
        self.assertEqual(1, len(unresolved))
        self.assertEqual([], resolved)

    def test_missing_dependency_is_skipped(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        review = self._review([dep.bead_id])
        path = self.storage.bead_path(review.bead_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["dependencies"].append("B-doesnotexist")
        path.write_text(json.dumps(data), encoding="utf-8")
        review = self.storage.load_bead(review.bead_id)
        unresolved, _ = dependency_criteria_state(self.storage, review)
        self.assertEqual([dep.bead_id], [u["bead_id"] for u in unresolved])

    def test_non_done_dependency_is_ignored(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        dep.status = BEAD_BLOCKED
        self.storage.save_bead(dep)
        review = self._review([dep.bead_id])
        self.assertEqual(([], []), dependency_criteria_state(self.storage, review))
        self.assertEqual([], dependency_descoped_entries(self.storage, review))

    def test_descoped_entries_returned(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "met"), _entry(1, "b", "descoped", "tester owns")])
        review = self._review([dep.bead_id])
        self.assertEqual(
            [{"bead_id": dep.bead_id, "index": 1, "criterion": "b", "evidence": "tester owns"}],
            dependency_descoped_entries(self.storage, review),
        )

    def test_dependency_rerun_reporting_met_clears_entry(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        review = self._review([dep.bead_id])
        self.assertEqual(1, len(dependency_criteria_state(self.storage, review)[0]))
        dep.handoff_summary.criteria_status = [_entry(0, "a", "met")]
        self.storage.save_bead(dep)
        self.assertEqual(([], []), dependency_criteria_state(self.storage, review))


class DependencyOverrideTests(SchedulerCase):
    def _run_review(self, dep_entries: list[dict], *, verdict: str = "approved", extra_results=None):
        dep = self._done_dev_with_status(dep_entries)
        review = self._review([dep.bead_id])
        results = {
            review.bead_id: AgentRunResult(
                outcome="completed", summary="ok", verdict=verdict or "", requires_followup=False
            ),
            f"{review.bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
        }
        results.update(extra_results or {})
        self._scheduler(FakeRunner(results=results)).run_once(max_workers=1)
        return dep, self.storage.load_bead(review.bead_id)

    def test_review_blocked_on_dependency_unmet_criterion(self) -> None:
        dep, review = self._run_review([_entry(0, "a", "met"), _entry(1, "b", "unmet", "no")])
        self.assertEqual(BEAD_BLOCKED, review.status)
        self.assertEqual("needs_changes", review.handoff_summary.verdict)
        self.assertIn(dep.bead_id, review.block_reason)
        self.assertTrue(review.block_reason.startswith("Dependency acceptance criteria unmet:"))
        self.assertEqual(
            [{"bead_id": dep.bead_id, "index": 1, "criterion": "b"}],
            review.metadata["unmet_dependency_criteria"],
        )
        events = self._events("verdict_overridden")
        self.assertEqual("dependency_criteria", events[0]["reason"])
        self.assertEqual([{"bead_id": dep.bead_id, "index": 1}], events[0]["unmet"])
        history = self._history(review.bead_id, "verdict_overridden")
        self.assertEqual("dependency_criteria", history[0].details["reason"])

    def test_all_met_or_descoped_review_stays_approved_without_metadata(self) -> None:
        _, review = self._run_review(
            [_entry(0, "a", "met"), _entry(1, "b", "descoped", "docs bead owns")]
        )
        self.assertEqual(BEAD_DONE, review.status)
        self.assertNotIn("unmet_dependency_criteria", review.metadata)
        self.assertEqual([], self._events("verdict_overridden"))

    def test_compat_mode_review_overridden_for_dependency_criteria(self) -> None:
        dep, review = self._run_review([_entry(0, "a", "unmet")], verdict="")
        self.assertEqual(BEAD_BLOCKED, review.status)
        self.assertIn(dep.bead_id, review.block_reason)

    def test_tester_beads_unaffected_by_dependency_criteria(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        tester = self.storage.create_bead(
            title="T", agent_type="tester", description="d", dependencies=[dep.bead_id], status=BEAD_READY
        )
        runner = FakeRunner(results={tester.bead_id: AgentRunResult(
            outcome="completed", summary="ok", verdict="approved", requires_followup=False)})
        self._scheduler(runner).run_once(max_workers=1)
        self.assertEqual(BEAD_DONE, self.storage.load_bead(tester.bead_id).status)
        self.assertNotIn("unmet_dependency_criteria", self.storage.load_bead(tester.bead_id).metadata)

    def test_second_override_overwrites_metadata(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet"), _entry(1, "b", "unmet")])
        review = self._review([dep.bead_id])
        finalizer = self._scheduler(FakeRunner())._executor._finalizer

        def approved() -> AgentRunResult:
            return AgentRunResult(outcome="completed", summary="ok", verdict="approved", requires_followup=False)

        self.assertTrue(finalizer._override_on_unmet_dependency_criteria(review, approved()))
        self.assertEqual(2, len(review.metadata["unmet_dependency_criteria"]))
        dep.handoff_summary.criteria_status = [_entry(0, "a", "met"), _entry(1, "b", "unmet")]
        self.storage.save_bead(dep)
        self.assertTrue(finalizer._override_on_unmet_dependency_criteria(review, approved()))
        self.assertEqual(
            [{"bead_id": dep.bead_id, "index": 1, "criterion": "b"}],
            review.metadata["unmet_dependency_criteria"],
        )
        dep.handoff_summary.criteria_status = [_entry(0, "a", "met"), _entry(1, "b", "met")]
        self.storage.save_bead(dep)
        self.assertFalse(finalizer._override_on_unmet_dependency_criteria(review, approved()))
        self.assertNotIn("unmet_dependency_criteria", review.metadata)

    def test_metadata_absent_after_approved_review(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        review = self._review([dep.bead_id])
        review.metadata["unmet_dependency_criteria"] = [
            {"bead_id": dep.bead_id, "index": 0, "criterion": "a"}
        ]
        self.storage.save_bead(review)
        dep.handoff_summary.criteria_status = [_entry(0, "a", "met")]
        self.storage.save_bead(dep)
        runner = FakeRunner(results={review.bead_id: AgentRunResult(
            outcome="completed", summary="ok", verdict="approved", requires_followup=False)})
        self._scheduler(runner).run_once(max_workers=1)
        loaded = self.storage.load_bead(review.bead_id)
        self.assertEqual(BEAD_DONE, loaded.status)
        self.assertNotIn("unmet_dependency_criteria", loaded.metadata)

    def test_prompt_and_gate_agree_for_same_storage_state(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet", "missing"), _entry(1, "b", "met")])
        review = self._review([dep.bead_id])
        expected_state = dependency_criteria_state(self.storage, review)
        runner = FakeRunner(results={
            review.bead_id: AgentRunResult(
                outcome="completed", summary="ok", verdict="approved", requires_followup=False),
            f"{review.bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
        })
        self._scheduler(runner).run_once(max_workers=1)
        # Executor passed the same state to the runner (and hence the prompt) as the gate used.
        self.assertEqual(expected_state, runner.criteria_state_by_bead[review.bead_id])
        prompt_ctx = render_dep_handoff_context(
            "review", [(dep.bead_id, dep.handoff_summary)], criteria_state=runner.criteria_state_by_bead[review.bead_id]
        )
        loaded = self.storage.load_bead(review.bead_id)
        gate_refs = [e["bead_id"] + f" #{e['index']}" for e in loaded.metadata["unmet_dependency_criteria"]]
        self.assertEqual([f"{dep.bead_id} #0"], gate_refs)
        self.assertIn(f"[unmet] {dep.bead_id} #0: a", prompt_ctx)
        self.assertIn(gate_refs[0], loaded.block_reason)

    def test_non_review_beads_do_not_receive_criteria_state(self) -> None:
        bead = self.storage.create_bead(title="D", agent_type="developer", description="d")
        runner = FakeRunner(results={bead.bead_id: AgentRunResult(outcome="completed", summary="ok")})
        self._scheduler(runner).run_once(max_workers=1)
        self.assertIsNone(runner.criteria_state_by_bead[bead.bead_id])


# ---------------------------------------------------------------------------
# Corrective inheritance and the full resolve cycle
# ---------------------------------------------------------------------------

class CorrectiveInheritanceTests(SchedulerCase):
    def _blocked_review(self, metadata: dict | None = None) -> Bead:
        review = self.storage.create_bead(title="Review", agent_type="review", description="d")
        review.status = BEAD_BLOCKED
        review.block_reason = "Needs corrective implementation."
        review.metadata.update(metadata or {})
        self.storage.save_bead(review)
        return review

    def _corrective_criteria(self, review: Bead) -> list[str]:
        followups = Scheduler(
            self.storage, FakeRunner(), WorktreeManager(self.root, self.storage.worktrees_dir)
        )._executor._followups
        return followups._create_corrective_bead(self.storage.load_bead(review.bead_id)).acceptance_criteria

    def test_corrective_gets_extra_criterion_after_three_standard(self) -> None:
        review = self._blocked_review(
            {"unmet_dependency_criteria": [{"bead_id": "B-dep", "index": 1, "criterion": "C"}]}
        )
        criteria = self._corrective_criteria(review)
        self.assertEqual(4, len(criteria))
        self.assertEqual("Resolve unmet criterion #1 from B-dep: C", criteria[3])

    def test_no_extra_criteria_without_or_with_empty_metadata(self) -> None:
        for meta in ({}, {"unmet_dependency_criteria": []}):
            review = self._blocked_review(meta)
            criteria = self._corrective_criteria(review)
            self.assertEqual(3, len(criteria), meta)
            self.assertFalse(any(c.startswith("Resolve unmet criterion") for c in criteria))

    def test_multiple_entries_keep_order(self) -> None:
        review = self._blocked_review({"unmet_dependency_criteria": [
            {"bead_id": "B-d1", "index": 0, "criterion": "first"},
            {"bead_id": "B-d2", "index": 3, "criterion": "second"},
        ]})
        criteria = self._corrective_criteria(review)
        self.assertEqual(
            ["Resolve unmet criterion #0 from B-d1: first", "Resolve unmet criterion #3 from B-d2: second"],
            criteria[3:],
        )


class ResolveCycleTests(SchedulerCase):
    def _setup(self):
        dep = self._done_dev_with_status([_entry(0, "ok", "met"), _entry(1, "dropped scope", "unmet", "never built")])
        review = self._review([dep.bead_id])
        return dep, review

    def _run(self, dep, review, corrective_status: str):
        """Stage 1: gate blocks the review. Stage 2: corrective completes, review is re-queued."""
        corrective_id = f"{review.bead_id}-corrective"
        def approved() -> AgentRunResult:
            # finalize mutates the result, so each stage needs its own instance
            return AgentRunResult(
                outcome="completed", summary="review", verdict="approved", requires_followup=False)

        stage1 = FakeRunner(results={
            review.bead_id: approved(),
            corrective_id: AgentRunResult(outcome="blocked", summary="n/a", block_reason="pending"),
        })
        self._run_all(stage1)
        blocked = self.storage.load_bead(review.bead_id)
        self.assertEqual(BEAD_BLOCKED, blocked.status)
        corrective = self.storage.load_bead(corrective_id)
        # The corrective completes and reports every criterion, including the inherited one.
        corrective.status = BEAD_DONE
        corrective.handoff_summary.criteria_status = [
            _entry(i, c, "met", f"fixed {i}") for i, c in enumerate(corrective.acceptance_criteria[:3])
        ] + [_entry(3, corrective.acceptance_criteria[3], corrective_status, "src/fix.py::fixed")]
        self.storage.save_bead(corrective)
        runner = FakeRunner(results={review.bead_id: approved()})
        self._run_all(runner, max_cycles=1)
        return runner, corrective_id

    def test_full_resolve_cycle_ends_done_with_no_override_on_rerun(self) -> None:
        dep, review = self._setup()
        runner, corrective_id = self._run(dep, review, "met")
        corrective = self.storage.load_bead(corrective_id)
        expected = resolve_criterion_text(dep.bead_id, 1, "dropped scope")
        self.assertEqual(expected, corrective.acceptance_criteria[3])
        self.assertEqual(BEAD_DONE, corrective.status)
        self.assertEqual("met", corrective.handoff_summary.criteria_status[3]["status"])
        loaded = self.storage.load_bead(review.bead_id)
        self.assertEqual(BEAD_DONE, loaded.status)
        self.assertNotIn("unmet_dependency_criteria", loaded.metadata)
        # The override fired exactly once (first run); the re-run was not overridden.
        self.assertEqual(1, len(self._events("verdict_overridden")))
        # The re-queued review's prompt state shows the entry as resolved, not unmet.
        unresolved, resolved = runner.criteria_state_by_bead[review.bead_id]
        self.assertEqual([], unresolved)
        self.assertEqual(corrective_id, resolved[0]["resolved_by"])
        text = render_dep_handoff_context(
            "review", [(dep.bead_id, dep.handoff_summary)], criteria_state=(unresolved, resolved)
        )
        self.assertIn(f"[resolved] {dep.bead_id} #1: dropped scope — resolved by {corrective_id}: src/fix.py::fixed", text)
        self.assertNotIn("[unmet]", text)

    def test_corrective_reporting_unmet_overrides_review_again(self) -> None:
        dep, review = self._setup()
        self._run(dep, review, "unmet")
        overrides = self._events("verdict_overridden")
        self.assertGreaterEqual(len(overrides), 2)
        self.assertTrue(all(e["reason"] == "dependency_criteria" for e in overrides))
        loaded = self.storage.load_bead(review.bead_id)
        self.assertNotEqual(BEAD_DONE, loaded.status)
        self.assertEqual(
            [{"bead_id": dep.bead_id, "index": 1, "criterion": "dropped scope"}],
            loaded.metadata["unmet_dependency_criteria"],
        )

    def test_unrelated_corrective_after_approval_has_no_resolve_criteria(self) -> None:
        review = self.storage.create_bead(title="R", agent_type="review", description="d")
        review.metadata["unmet_dependency_criteria"] = [{"bead_id": "B-x", "index": 0, "criterion": "c"}]
        self.storage.save_bead(review)
        runner = FakeRunner(results={review.bead_id: AgentRunResult(
            outcome="completed", summary="ok", verdict="approved", requires_followup=False)})
        self._scheduler(runner).run_once(max_workers=1)
        loaded = self.storage.load_bead(review.bead_id)
        self.assertNotIn("unmet_dependency_criteria", loaded.metadata)
        loaded.status = BEAD_BLOCKED
        loaded.block_reason = "unrelated block"
        self.storage.save_bead(loaded)
        followups = self._scheduler(FakeRunner())._executor._followups
        corrective = followups._create_corrective_bead(self.storage.load_bead(review.bead_id))
        self.assertFalse(any(c.startswith("Resolve unmet criterion") for c in corrective.acceptance_criteria))


# ---------------------------------------------------------------------------
# Prompt blocks
# ---------------------------------------------------------------------------

class PromptBlockTests(OrchestratorTests):
    def _bead(self, agent_type: str, criteria: list[str]) -> Bead:
        return Bead(
            bead_id="B-prompt001", title="t", agent_type=agent_type, description="d",
            acceptance_criteria=criteria,
        )

    def test_worker_prompt_has_indexed_criteria_block_before_assigned_bead(self) -> None:
        prompt = build_worker_prompt(self._bead("developer", ["first", "second"]), [], self.root)
        self.assertIn("Acceptance criteria (0-based index):", prompt)
        self.assertIn("[0] first\n[1] second\n", prompt)
        self.assertLess(prompt.index("[0] first"), prompt.index("Assigned bead:"))
        self.assertIn('"acceptance_criteria"', prompt)

    def test_worker_prompt_without_criteria_has_no_header(self) -> None:
        prompt = build_worker_prompt(self._bead("developer", []), [], self.root)
        self.assertNotIn("Acceptance criteria (0-based index):", prompt)

    def test_review_prompt_renders_unmet_resolved_and_descoped_formats(self) -> None:
        handoff = HandoffSummary(criteria_status=[_entry(2, "crit c", "descoped", "tester owns")])
        unresolved = [{"bead_id": "B-d1", "index": 0, "criterion": "crit a", "evidence": "missing"}]
        resolved = [{"bead_id": "B-d1", "index": 1, "criterion": "crit b", "evidence": "fix.py",
                     "resolved_by": "B-r-corrective"}]
        text = render_dep_handoff_context(
            "review", [("B-d1", handoff)], criteria_state=(unresolved, resolved)
        )
        self.assertIn("Acceptance criteria accounting from dependencies:", text)
        self.assertIn("  [unmet] B-d1 #0: crit a — missing", text)
        self.assertIn("  [resolved] B-d1 #1: crit b — resolved by B-r-corrective: fix.py", text)
        self.assertIn("  [descoped] B-d1 #2: crit c — tester owns", text)

    def test_review_prompt_all_met_line(self) -> None:
        text = render_dep_handoff_context(
            "review", [("B-d1", HandoffSummary())], criteria_state=([], [])
        )
        self.assertIn("All dependency acceptance criteria reported met.", text)
        self.assertNotIn("[unmet]", text)

    def test_no_criteria_state_renders_no_block(self) -> None:
        handoff = HandoffSummary(design_decisions="dd", criteria_status=[_entry(0, "c", "descoped", "r")])
        text = render_dep_handoff_context("review", [("B-d1", handoff)], criteria_state=None)
        self.assertNotIn("Acceptance criteria accounting", text)

    def test_tester_and_developer_prompts_unchanged_by_criteria_state(self) -> None:
        handoff = HandoffSummary(test_coverage_notes="n", known_limitations="k")
        state = ([{"bead_id": "B-d", "index": 0, "criterion": "c", "evidence": "e"}], [])
        with_state = render_dep_handoff_context("tester", [("B-d", handoff)], criteria_state=state)
        without = render_dep_handoff_context("tester", [("B-d", handoff)])
        self.assertEqual(without, with_state)
        self.assertEqual("", render_dep_handoff_context("developer", [("B-d", handoff)], criteria_state=state))

    def test_worker_prompt_for_review_includes_accounting_block(self) -> None:
        state = ([{"bead_id": "B-d", "index": 0, "criterion": "c", "evidence": "e"}], [])
        prompt = build_worker_prompt(
            self._bead("review", []), [], self.root, dep_handoffs=[("B-d", HandoffSummary())],
            criteria_state=state,
        )
        self.assertIn("[unmet] B-d #0: c — e", prompt)

    def test_recovery_prompt_lists_original_criteria_with_indices(self) -> None:
        prompt = build_recovery_prompt(self._bead("developer", ["alpha", "beta"]), "prose", "")
        self.assertIn("[0] alpha", prompt)
        self.assertIn("[1] beta", prompt)
        self.assertIn("not evidenced in original output", prompt)
        self.assertIn("criteria_status", prompt)

    def test_recovery_prompt_without_criteria_has_no_section(self) -> None:
        prompt = build_recovery_prompt(self._bead("developer", []), "prose", "")
        self.assertNotIn("Original acceptance criteria", prompt)
        self.assertNotIn("not evidenced in original output", prompt)

    def test_worker_prompt_unchanged_by_recovery_wording(self) -> None:
        prompt = build_worker_prompt(self._bead("developer", ["a"]), [], self.root)
        self.assertNotIn("not evidenced in original output", prompt)


# ---------------------------------------------------------------------------
# Scenario citations and finalize-time prefix matching
# ---------------------------------------------------------------------------

class ScenarioCitationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_x.py").write_text("def test_y():\n    pass\n", encoding="utf-8")

    def _verify(self, evidence: str, criterion: str = "Scenario: a / b / c — WHEN x"):
        return _verify_scenario_citations([_entry(0, criterion, "met", evidence)], self.root)[0]

    def test_valid_reference_stays_met(self) -> None:
        out = self._verify("covered by tests/test_x.py::test_y")
        self.assertEqual("met", out["status"])

    def test_missing_file_is_unmet(self) -> None:
        out = self._verify("tests/test_nope.py::test_y")
        self.assertEqual(("unmet", "cited test not found: tests/test_nope.py::test_y"), (out["status"], out["evidence"]))

    def test_missing_name_is_unmet(self) -> None:
        out = self._verify("tests/test_x.py::test_missing")
        self.assertEqual(("unmet", "cited test not found: tests/test_x.py::test_missing"), (out["status"], out["evidence"]))

    def test_no_reference_is_unmet(self) -> None:
        out = self._verify("I wrote tests, trust me")
        self.assertEqual(("unmet", "no test reference in evidence"), (out["status"], out["evidence"]))

    def test_absolute_path_is_unmet(self) -> None:
        out = self._verify(f"{self.root}/tests/test_x.py::test_y")
        self.assertEqual("unmet", out["status"])

    def test_parent_escape_is_unmet(self) -> None:
        outside = self.root.parent / "outside_test.py"
        outside.write_text("def test_y(): pass\n", encoding="utf-8")
        self.addCleanup(outside.unlink)
        out = self._verify(f"../{outside.name}::test_y")
        self.assertEqual("unmet", out["status"])

    def test_multiple_refs_second_bad_is_unmet(self) -> None:
        out = self._verify("tests/test_x.py::test_y and tests/test_x.py::test_gone")
        self.assertEqual("unmet", out["status"])
        self.assertIn("test_gone", out["evidence"])

    def test_non_scenario_criteria_untouched(self) -> None:
        entry = _entry(0, "plain criterion", "met", "no ref")
        self.assertEqual([entry], _verify_scenario_citations([entry], self.root))

    def test_non_met_entries_untouched(self) -> None:
        entries = [
            _entry(0, "Scenario: a / b / c — x", "unmet", "why"),
            _entry(1, "Scenario: a / b / d — x", "descoped", "owned elsewhere"),
        ]
        self.assertEqual(entries, _verify_scenario_citations(entries, self.root))

    def test_finalize_prefix_matching(self) -> None:
        # The finalize-time check and the plan-time check share this matcher.
        prefix = "Scenario: a / b / Login"
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login", prefix))
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login — WHEN x", prefix))
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login - WHEN x", prefix))
        self.assertFalse(criterion_matches_prefix("Scenario: a / b / Login fails — WHEN x", prefix))


class ScenarioCitationFinalizeTests(SchedulerCase):
    SCENARIO = "Scenario: cap / Req / Name — WHEN x THEN y"

    def test_tester_with_uncited_scenario_is_blocked_on_normal_path(self) -> None:
        bead = self.storage.create_bead(
            title="T", agent_type="tester", description="d", acceptance_criteria=[self.SCENARIO]
        )
        runner = FakeRunner(results={
            bead.bead_id: AgentRunResult(
                outcome="completed", summary="ok", verdict="approved", requires_followup=False,
                criteria_status=[{"index": 0, "status": "met", "evidence": "tests/test_nope.py::test_y"}],
            ),
            f"{bead.bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
        })
        self._scheduler(runner).run_once(max_workers=1)
        loaded = self.storage.load_bead(bead.bead_id)
        self.assertEqual(BEAD_BLOCKED, loaded.status)
        self.assertEqual("unmet", loaded.handoff_summary.criteria_status[0]["status"])
        self.assertEqual(
            "cited test not found: tests/test_nope.py::test_y",
            loaded.handoff_summary.criteria_status[0]["evidence"],
        )
        self.assertEqual(1, len(self._events("verdict_overridden")))


# ---------------------------------------------------------------------------
# Recovery path
# ---------------------------------------------------------------------------

class RecoveryPathTests(SchedulerCase):
    def _scenario(self, agent_type: str, criteria: list[str], dependencies: list[str] | None = None):
        original = self.storage.create_bead(
            title="Original", agent_type=agent_type, description="d",
            acceptance_criteria=criteria, dependencies=dependencies or [],
        )
        original.status = BEAD_BLOCKED
        original.block_reason = NO_STRUCTURED_OUTPUT_SENTINEL
        self.storage.save_bead(original)
        recovery = self.storage.create_bead(
            bead_id=self.storage.allocate_child_bead_id(original.bead_id, "recovery"),
            title="Recover", agent_type="recovery", bead_type="recovery", description="d",
            parent_id=original.bead_id, feature_root_id=original.feature_root_id,
            recovery_for=original.bead_id,
        )
        original.metadata["auto_recovery_bead_id"] = recovery.bead_id
        self.storage.save_bead(original)
        return original, recovery

    def _run(self, original, recovery, result: AgentRunResult):
        runner = FakeRunner(results={
            recovery.bead_id: result,
            f"{original.bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
        })
        self._scheduler(runner).run_once(max_workers=1)
        return self.storage.load_bead(original.bead_id)

    # Regression: recovery path must keep the recovery report and allow a corrective (B-856b4aa2).
    def test_recovered_developer_reconciled_against_original_criteria(self) -> None:
        original, recovery = self._scenario("developer", ["a", "b"])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "met", "evidence": "x"}],
        ))
        self.assertEqual(BEAD_DONE, loaded.status)
        self.assertEqual(
            [_entry(0, "a", "met", "x"), _entry(1, "b", "unmet", "not reported by agent")],
            loaded.handoff_summary.criteria_status,
        )

    def test_recovered_tester_approved_with_unmet_criterion_is_blocked(self) -> None:
        original, recovery = self._scenario("tester", ["a"])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "unmet", "evidence": "not evidenced in original output"}],
        ))
        self.assertEqual(BEAD_BLOCKED, loaded.status)
        self.assertEqual("needs_changes", loaded.handoff_summary.verdict)
        self.assertTrue(loaded.block_reason.startswith("Approved with unmet acceptance criteria:"))
        self.assertEqual(1, len(self._history(original.bead_id, "verdict_overridden")))
        events = [e for e in self._events("verdict_overridden") if e["bead_id"] == original.bead_id]
        self.assertEqual(1, len(events))

    # Regression: recovery path must keep the recovery report and allow a corrective (B-856b4aa2).
    def test_recovered_tester_override_creates_corrective(self) -> None:
        original, recovery = self._scenario("tester", ["a"])
        self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "unmet", "evidence": "not evidenced in original output"}],
        ))
        corrective = [b for b in self.storage.list_beads() if b.metadata.get("auto_corrective_for") == original.bead_id]
        self.assertEqual(1, len(corrective))

    def test_recovered_tester_scenario_without_real_citation_downgraded(self) -> None:
        original, recovery = self._scenario("tester", ["Scenario: c / r / n — WHEN x"])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "met", "evidence": "tests/test_ghost.py::test_ghost"}],
        ))
        self.assertEqual("unmet", loaded.handoff_summary.criteria_status[0]["status"])
        self.assertEqual(BEAD_BLOCKED, loaded.status)

    def test_recovered_review_with_unresolved_dependency_criteria_is_overridden(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        original, recovery = self._scenario("review", [], dependencies=[dep.bead_id])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False))
        self.assertEqual(BEAD_BLOCKED, loaded.status)
        self.assertIn(dep.bead_id, loaded.block_reason)
        self.assertEqual(1, len(self._history(original.bead_id, "verdict_overridden")))

    def test_happy_path_all_met_marks_original_done_and_creates_followups(self) -> None:
        original, recovery = self._scenario("developer", ["a"])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "met", "evidence": "x"}],
        ))
        self.assertEqual(BEAD_DONE, loaded.status)
        children = {b.bead_id for b in self.storage.list_beads() if b.parent_id == original.bead_id}
        self.assertTrue(any(c.endswith("-test") for c in children), children)

    # Regression: recovery path must keep the recovery report and allow a corrective (B-856b4aa2).
    def test_recovered_needs_changes_verdict_blocks_and_creates_corrective(self) -> None:
        original, recovery = self._scenario("review", ["a"])
        loaded = self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="needs_changes", requires_followup=True,
            block_reason="findings",
            criteria_status=[{"index": 0, "status": "met", "evidence": "x"}],
        ))
        self.assertEqual(BEAD_BLOCKED, loaded.status)
        corrective = [b for b in self.storage.list_beads() if b.metadata.get("auto_corrective_for") == original.bead_id]
        self.assertEqual(1, len(corrective))

    # Regression: recovery path must keep the recovery report and allow a corrective (B-856b4aa2).
    def test_recovery_gap_event_emitted_under_original_bead_id(self) -> None:
        original, recovery = self._scenario("developer", ["a", "b"])
        self._run(original, recovery, AgentRunResult(
            outcome="completed", summary="s", verdict="approved", requires_followup=False,
            criteria_status=[{"index": 0, "status": "met", "evidence": "x"}],
        ))
        events = self._events("criteria_gap_recorded")
        self.assertEqual([original.bead_id], [e["bead_id"] for e in events])
        self.assertEqual(1, events[0]["unmet"])


# ---------------------------------------------------------------------------
# criteria_gap_recorded events and summary aggregate
# ---------------------------------------------------------------------------

class CriteriaGapEventTests(SchedulerCase):
    def test_developer_with_unmet_and_descoped_writes_one_event(self) -> None:
        bead = self._run_bead(
            "developer", ["a", "b", "c"],
            [{"index": 0, "status": "unmet", "evidence": "no"},
             {"index": 1, "status": "descoped", "evidence": "docs own it"},
             {"index": 2, "status": "met", "evidence": "x"}],
        )
        events = self._events("criteria_gap_recorded")
        self.assertEqual(
            [{"bead_id": bead.bead_id, "agent_type": "developer",
              "feature_root_id": bead.feature_root_id, "unmet": 1, "descoped": 1}],
            events,
        )

    def test_all_met_writes_no_event(self) -> None:
        self._run_bead("developer", ["a"], [{"index": 0, "status": "met", "evidence": "x"}])
        self.assertEqual([], self._events("criteria_gap_recorded"))

    def test_empty_criteria_writes_no_event(self) -> None:
        self._run_bead("developer", [], [])
        self.assertEqual([], self._events("criteria_gap_recorded"))

    def test_overridden_tester_writes_no_gap_event(self) -> None:
        self._run_bead("tester", ["a"], [{"index": 0, "status": "unmet", "evidence": "no"}], verdict="approved")
        self.assertEqual([], self._events("criteria_gap_recorded"))
        self.assertEqual(1, len(self._events("verdict_overridden")))

    def test_dependency_overridden_review_writes_no_gap_event(self) -> None:
        dep = self._done_dev_with_status([_entry(0, "a", "unmet")])
        review = self._review([dep.bead_id])
        runner = FakeRunner(results={
            review.bead_id: AgentRunResult(
                outcome="completed", summary="ok", verdict="approved", requires_followup=False),
            f"{review.bead_id}-corrective": AgentRunResult(outcome="blocked", summary="n/a", block_reason="x"),
        })
        self._scheduler(runner).run_once(max_workers=1)
        self.assertEqual([], self._events("criteria_gap_recorded"))

    def test_blocked_bead_writes_no_gap_event(self) -> None:
        bead = self.storage.create_bead(
            title="D", agent_type="developer", description="d", acceptance_criteria=["a"]
        )
        runner = FakeRunner(results={
            bead.bead_id: AgentRunResult(outcome="blocked", summary="s", block_reason="stuck"),
        })
        self._scheduler(runner).run_once(max_workers=1)
        self.assertEqual([], self._events("criteria_gap_recorded"))


class SummaryCriteriaTests(SchedulerCase):
    def test_mixed_entries_counted_across_tree(self) -> None:
        root = self.storage.create_bead(title="Root", agent_type="planner", description="d", status=BEAD_DONE)
        child = self.storage.create_bead(
            title="C", agent_type="developer", description="d", parent_id=root.bead_id, status=BEAD_DONE
        )
        child.handoff_summary.criteria_status = [
            _entry(0, "a", "met"), _entry(1, "b", "unmet"), _entry(2, "c", "descoped"), _entry(3, "d", "met"),
        ]
        self.storage.save_bead(child)
        self.assertEqual(
            {"met": 2, "unmet": 1, "descoped": 1},
            self.storage.summary(feature_root_id=root.bead_id)["criteria"],
        )

    def test_no_criteria_data_gives_zeros(self) -> None:
        root = self.storage.create_bead(title="Root", agent_type="planner", description="d")
        self.assertEqual(
            {"met": 0, "unmet": 0, "descoped": 0},
            self.storage.summary(feature_root_id=root.bead_id)["criteria"],
        )

    def test_unscoped_summary_has_no_criteria_key(self) -> None:
        self.storage.create_bead(title="Root", agent_type="planner", description="d")
        self.assertNotIn("criteria", self.storage.summary())

    def test_other_trees_not_counted(self) -> None:
        a = self.storage.create_bead(title="A", agent_type="developer", description="d", status=BEAD_DONE)
        b = self.storage.create_bead(title="B", agent_type="developer", description="d", status=BEAD_DONE)
        b.handoff_summary.criteria_status = [_entry(0, "x", "unmet")]
        self.storage.save_bead(b)
        self.assertEqual(
            {"met": 0, "unmet": 0, "descoped": 0},
            self.storage.summary(feature_root_id=a.bead_id)["criteria"],
        )


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

class GuardrailParityTests(unittest.TestCase):
    AGENTS = ("developer", "defect", "tester", "documentation", "review")
    DATA = REPO_ROOT / "src" / "agent_takt" / "_data" / "templates"

    def test_accounting_rule_present_in_five_guardrails_and_mirrored(self) -> None:
        for agent in self.AGENTS:
            src = (REPO_ROOT / "templates" / "agents" / f"{agent}.md").read_text(encoding="utf-8")
            self.assertIn("Acceptance criteria accounting", src, agent)
            self.assertIn("`criteria_status`", src, agent)
            copy = (self.DATA / "agents" / f"{agent}.md").read_bytes()
            self.assertEqual((REPO_ROOT / "templates" / "agents" / f"{agent}.md").read_bytes(), copy, agent)

    def test_tester_path_test_name_rule(self) -> None:
        text = (REPO_ROOT / "templates" / "agents" / "tester.md").read_text(encoding="utf-8")
        self.assertIn("<relative/path>::<test_name>", text)

    def test_review_dependency_accounting_rule(self) -> None:
        text = (REPO_ROOT / "templates" / "agents" / "review.md").read_text(encoding="utf-8")
        self.assertIn("Acceptance criteria accounting from dependencies", text)
        self.assertIn("[unmet]", text)
        self.assertIn("[resolved]", text)

    def test_spec_intake_skill_scenario_form_and_parity(self) -> None:
        rel = Path("skills") / "task" / "spec-intake" / "SKILL.md"
        src = (REPO_ROOT / "templates" / rel).read_bytes()
        self.assertEqual(src, (self.DATA / rel).read_bytes())
        self.assertIn(
            "Scenario: <capability> / <requirement name> / <scenario name> — <WHEN/THEN text>",
            src.decode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
