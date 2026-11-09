"""Tests for ``takt trace`` (list/show/analyze/backfill), audit trace summaries, delete and scaffold hygiene."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "traces" / "claude_session.jsonl"

from agent_takt.audit import build_feature_audit, render_feature_audit_markdown
from agent_takt.cli.commands.bead import command_bead
from agent_takt.cli.commands.trace import command_trace
from agent_takt.cli.parser import build_parser
from agent_takt.console import ConsoleReporter
from agent_takt.documents.registry import get_store
from agent_takt.models import BEAD_DONE
from agent_takt.onboarding import update_gitignore
from agent_takt.storage import RepositoryStorage
from agent_takt.traces.normalize import count_tool_calls, normalize_claude_transcript
from agent_takt.traces.store import TraceStore

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests  # noqa: E402

SID = "11111111-1111-4111-8111-111111111111"


def _fixture_events() -> list[dict]:
    with FIXTURE.open("rb") as handle:
        return normalize_claude_transcript(handle).events


class TraceCliBase(OrchestratorTests):
    def setUp(self) -> None:
        super().setUp()
        self.err = io.StringIO()
        self.console = ConsoleReporter(stream=self.err)
        self.store = TraceStore(self.root)
        self._cfg = tempfile.TemporaryDirectory()
        self.addCleanup(self._cfg.cleanup)
        self.config_dir = Path(self._cfg.name)
        patcher = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers
    def trace(self, *argv: str) -> tuple[int, str]:
        args = build_parser().parse_args(["trace", *argv])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = command_trace(args, self.storage, self.console)
        return code, out.getvalue()

    def info(self) -> str:
        return self.err.getvalue()

    def add_record(self, bead_id: str | None, events: list[dict] | None = None, **meta) -> int:
        meta.setdefault("backend", "claude")
        meta.setdefault("status", "captured")
        meta.setdefault("role", "main")
        record = self.store.new_record(bead_id, **meta)
        if events is not None:
            self.store.write_events(bead_id, record.seq, events)
            self.store.update_meta(bead_id, record.seq, tool_calls=count_tool_calls(events))
        return record.seq

    def put_transcript(self, session_id: str) -> Path:
        dest = self.config_dir / "projects" / "p" / f"{session_id}.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE, dest)
        return dest

    def make_tree(self):
        root = self.storage.create_bead(title="Root", agent_type="planner", description="r")
        dev = self.storage.create_bead(title="Dev", agent_type="developer", description="d",
                                       parent_id=root.bead_id, status=BEAD_DONE)
        rev = self.storage.create_bead(title="Rev", agent_type="review", description="v",
                                       parent_id=root.bead_id, status=BEAD_DONE)
        return root, dev, rev


# ---------------------------------------------------------------------------
class TraceListTests(TraceCliBase):
    def test_list_with_no_traces_prints_notice(self) -> None:
        code, out = self.trace("list")
        self.assertEqual(0, code)
        self.assertIn("No trace records", self.info())

    def test_list_feature_root_includes_matching_planner_records(self) -> None:
        root, dev, _ = self.make_tree()
        other = self.storage.create_bead(title="Other", agent_type="developer", description="o")
        self.add_record(dev.bead_id, [])
        self.add_record(other.bead_id, [])
        self.add_record(None, [], role="planner", agent_type="planner", feature_root_id=root.bead_id)
        self.add_record(None, [], role="planner", agent_type="planner", feature_root_id="B-zzzzzzzz")
        code, out = self.trace("list", "--feature-root", root.bead_id, "--json")
        self.assertEqual(0, code)
        rows = json.loads(out)
        self.assertEqual({dev.bead_id, None}, {r["bead_id"] for r in rows})
        self.assertEqual(2, len(rows))
        self.assertNotIn(other.bead_id, [r["bead_id"] for r in rows])

    def test_list_bead_partial_id_status_and_plain(self) -> None:
        _, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, [], status="captured")
        self.add_record(dev.bead_id, status="missing")
        code, out = self.trace("list", "--bead", dev.bead_id[:6], "--plain")
        self.assertEqual(0, code)
        self.assertEqual(2, len(out.strip().splitlines()))
        code, out = self.trace("list", "--bead", dev.bead_id, "--status", "missing", "--json")
        self.assertEqual(["missing"], [r["status"] for r in json.loads(out)])
        code, out = self.trace("list", "--bead", dev.bead_id)
        self.assertIn("BEAD", out.splitlines()[0])

    def test_list_unknown_bead_exits_one(self) -> None:
        code, _ = self.trace("list", "--bead", "B-nonexist")
        self.assertEqual(1, code)


class TraceShowTests(TraceCliBase):
    def setUp(self) -> None:
        super().setUp()
        self.bead = self.storage.create_bead(title="Dev", agent_type="developer", description="d")
        self.events = _fixture_events()

    def test_show_latest_trace(self) -> None:
        # Scenario: agent-traces / Trace CLI / Show the latest trace
        self.add_record(self.bead.bead_id, [{"kind": "assistant_text", "ts": "t0", "text_excerpt": "OLD"}])
        self.add_record(self.bead.bead_id, self.events)
        self.add_record(self.bead.bead_id, [{"kind": "assistant_text", "ts": "t9", "text_excerpt": "RETRY"}],
                        role="structured_output_retry")
        code, out = self.trace("show", self.bead.bead_id)
        self.assertEqual(0, code)
        self.assertNotIn("OLD", out)  # latest *main*, not seq 1
        self.assertNotIn("RETRY", out)  # retry is not main
        self.assertIn("tool_call Bash", out)
        self.assertIn("ls", out)

    def test_show_seq_kind_errors_full_json(self) -> None:
        self.add_record(self.bead.bead_id, [{"kind": "assistant_text", "ts": "t0", "text_excerpt": "FIRST"}])
        self.add_record(self.bead.bead_id, self.events)
        _, out = self.trace("show", self.bead.bead_id, "--seq", "1")
        self.assertIn("FIRST", out)
        code, _ = self.trace("show", self.bead.bead_id, "--seq", "9")
        self.assertEqual(1, code)
        _, out = self.trace("show", self.bead.bead_id, "--kind", "usage")
        self.assertTrue(out.strip() and all("usage" in ln for ln in out.splitlines()))
        _, out = self.trace("show", self.bead.bead_id, "--errors")
        self.assertEqual(1, len(out.strip().splitlines()))
        self.assertIn("✗", out)
        _, out = self.trace("show", self.bead.bead_id, "--errors", "--full")
        self.assertIn("boom", out)
        _, out = self.trace("show", self.bead.bead_id, "--json", "--kind", "tool_call")
        self.assertEqual(3, len(json.loads(out)))
        _, out = self.trace("show", self.bead.bead_id, "--json", "--errors")
        self.assertEqual(["toolu_2"], [e["call_id"] for e in json.loads(out)])

    def test_record_without_events_prints_notice(self) -> None:
        self.add_record(self.bead.bead_id, None, status="missing")
        code, out = self.trace("show", self.bead.bead_id)
        self.assertEqual(0, code)
        self.assertEqual("", out)
        self.assertIn("no events", self.info())
        self.assertIn("missing", self.info())

    def test_bead_without_traces_and_bad_ids(self) -> None:
        code, out = self.trace("show", self.bead.bead_id)
        self.assertEqual((0, ""), (code, out))
        self.assertIn("No traces recorded", self.info())
        self.assertEqual(1, self.trace("show", "B-nonexist")[0])
        a = self.storage.create_bead(title="A", agent_type="developer", description="d")
        b = self.storage.create_bead(title="B", agent_type="developer", description="d")
        common = os.path.commonprefix([a.bead_id, b.bead_id])
        self.assertEqual(1, self.trace("show", common)[0])


class TraceAnalyzeTests(TraceCliBase):
    def _doc_events(self, spec_id: str) -> list[dict]:
        # one proactive spec access per call -> two per record, via the CLI form
        def bash(cid, cmd):
            return {"kind": "tool_call", "call_id": cid, "tool": "Bash", "input": {"command": cmd},
                    "is_error": False, "duration_ms": 1, "result_excerpt": "", "result_chars": 0}
        return [bash("a", f"takt spec show {spec_id}"), bash("b", f"takt spec show {spec_id}")]

    def test_two_beads_two_proactive_accesses_each_total_four(self) -> None:
        # Scenario: agent-traces / Trace CLI / Feature-level analysis
        spec = get_store(self.root, "spec").create("A spec")
        root, dev, rev = self.make_tree()
        self.add_record(dev.bead_id, self._doc_events(spec.id))
        self.add_record(rev.bead_id, self._doc_events(spec.id))
        code, out = self.trace("analyze", "--feature-root", root.bead_id, "--json")
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(4, data["totals"]["document_access"]["counts"]["proactive"])
        self.assertEqual({dev.bead_id, rev.bead_id}, set(data["beads"]))
        self.assertIn(root.bead_id, data["no_traces"])
        self.assertEqual(4, data["totals"]["tool_usage"]["tool_calls"])

    def test_feature_root_includes_linked_planner_records(self) -> None:
        root, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, _fixture_events())
        self.add_record(None, _fixture_events(), role="planner", agent_type="planner", feature_root_id=root.bead_id)
        self.add_record(None, _fixture_events(), role="planner", agent_type="planner", feature_root_id="B-other000")
        _, out = self.trace("analyze", "--feature-root", root.bead_id, "--json")
        data = json.loads(out)
        self.assertIn("_planner", data["beads"])
        self.assertEqual(4, data["totals"]["tool_usage"]["tool_calls"])  # 2 + 2 (StructuredOutput excluded)

    def test_by_agent_and_analyzer_filters(self) -> None:
        root, dev, rev = self.make_tree()
        self.add_record(dev.bead_id, _fixture_events())
        self.add_record(rev.bead_id, _fixture_events())
        _, out = self.trace("analyze", "--feature-root", root.bead_id, "--by-agent", "--json")
        data = json.loads(out)
        self.assertEqual({"developer", "review"}, set(data["by_agent"]))
        self.assertEqual(2, data["by_agent"]["developer"]["tool_usage"]["tool_calls"])
        _, out = self.trace("analyze", "--feature-root", root.bead_id, "--analyzer", "commands", "--json")
        self.assertEqual(["commands"], list(json.loads(out)["totals"]))
        _, out = self.trace("analyze", "--feature-root", root.bead_id, "--analyzer", "commands",
                            "--analyzer", "tool_usage", "--json")
        self.assertEqual(["tool_usage", "commands"], [k for k in json.loads(out)["totals"]
                                                       if k in ("tool_usage", "commands")])
        self.assertEqual(2, len(json.loads(out)["totals"]))

    def test_unknown_analyzer_exits_one(self) -> None:
        _, dev, _ = self.make_tree()
        self.assertEqual(1, self.trace("analyze", dev.bead_id, "--analyzer", "bogus")[0])
        self.assertIn("Unknown analyzer", self.info())

    def test_bead_without_traces_exits_zero_plain_and_json(self) -> None:
        # Scenario: agent-traces / Trace CLI / Bead without traces
        _, dev, _ = self.make_tree()
        code, out = self.trace("analyze", dev.bead_id)
        self.assertEqual(0, code)
        self.assertIn("No traces", self.info())
        code, out = self.trace("analyze", dev.bead_id, "--json")
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual({}, data["beads"])
        self.assertEqual([dev.bead_id], data["no_traces"])
        self.assertEqual(0, self.trace("show", dev.bead_id)[0])
        self.assertEqual(0, self.trace("list", "--bead", dev.bead_id)[0])

    def test_too_large_records_with_events_are_analysed(self) -> None:
        _, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, _fixture_events(), status="too_large")
        self.add_record(dev.bead_id, _fixture_events(), status="missing")
        _, out = self.trace("analyze", dev.bead_id, "--json")
        self.assertEqual(2, json.loads(out)["totals"]["tool_usage"]["tool_calls"])

    def test_requires_exactly_one_of_bead_or_feature_root(self) -> None:
        root, dev, _ = self.make_tree()
        self.assertEqual(1, self.trace("analyze")[0])
        self.assertEqual(1, self.trace("analyze", dev.bead_id, "--feature-root", root.bead_id)[0])

    def test_plain_output_has_totals_and_table(self) -> None:
        _, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, _fixture_events())
        code, out = self.trace("analyze", dev.bead_id)
        self.assertEqual(0, code)
        self.assertIn("TOTALS", out)
        self.assertIn("TOOL_CALLS", out)


class TraceBackfillTests(TraceCliBase):
    def _artifact(self, bead_id: str, attempt: int = 1, *, session_id: str | None = SID, response_text=None,
                  agent_type="developer") -> Path:
        metrics = {"session_id": session_id} if session_id else {}
        path = self.storage.write_telemetry_artifact(
            bead_id=bead_id, agent_type=agent_type, attempt=attempt, started_at="a", finished_at="b",
            outcome="completed", prompt_text=None, response_text=response_text, parsed_result=None,
            metrics=metrics, error=None,
        )
        return path

    def test_backfill_captures_then_skips(self) -> None:
        # Scenario: agent-traces / Trace CLI / Backfill from existing telemetry
        _, dev, _ = self.make_tree()
        self.put_transcript(SID)
        self._artifact(dev.bead_id)
        code, out = self.trace("backfill")
        self.assertEqual(0, code)
        self.assertEqual({"captured": 1, "missing": 0, "skipped": 0, "dry_run": False}, json.loads(out))
        record = self.store.list_records(dev.bead_id)[0]
        self.assertEqual(("captured", 1, SID, "completed"),
                         (record.status, record.telemetry_attempt, record.session_id, record.outcome))
        code, out = self.trace("backfill")
        self.assertEqual({"captured": 0, "missing": 0, "skipped": 1, "dry_run": False}, json.loads(out))
        self.assertEqual(1, len(self.store.list_records(dev.bead_id)))

    def test_session_id_only_in_response_text(self) -> None:
        _, dev, _ = self.make_tree()
        self.put_transcript(SID)
        self._artifact(dev.bead_id, session_id=None, response_text=json.dumps({"session_id": SID, "result": "x"}))
        _, out = self.trace("backfill")
        self.assertEqual(1, json.loads(out)["captured"])

    def test_missing_transcript_and_dry_run_write_nothing(self) -> None:
        _, dev, _ = self.make_tree()
        self._artifact(dev.bead_id)
        _, out = self.trace("backfill")
        self.assertEqual({"captured": 0, "missing": 1, "skipped": 0, "dry_run": False}, json.loads(out))
        self.put_transcript(SID)
        _, out = self.trace("backfill", "--dry-run")
        self.assertEqual({"captured": 1, "missing": 0, "skipped": 0, "dry_run": True}, json.loads(out))
        self.assertFalse((self.root / ".takt" / "traces").exists() and any((self.root / ".takt" / "traces").rglob("*")))
        self.assertEqual([], self.store.list_records(dev.bead_id))

    def test_planner_artifacts_are_ignored(self) -> None:
        root, _, _ = self.make_tree()
        self.put_transcript(SID)
        self._artifact(root.bead_id, agent_type="planner")
        _, out = self.trace("backfill")
        self.assertEqual({"captured": 0, "missing": 0, "skipped": 0, "dry_run": False}, json.loads(out))

    def test_bead_and_feature_root_filters(self) -> None:
        root, dev, rev = self.make_tree()
        other = self.storage.create_bead(title="Other", agent_type="developer", description="o")
        sid2 = "22222222-2222-4222-8222-222222222222"
        self.put_transcript(SID)
        self.put_transcript(sid2)
        self._artifact(dev.bead_id)
        self._artifact(other.bead_id, session_id=sid2)
        _, out = self.trace("backfill", "--bead", other.bead_id)
        self.assertEqual(1, json.loads(out)["captured"])
        self.assertEqual([], self.store.list_records(dev.bead_id))
        _, out = self.trace("backfill", "--feature-root", root.bead_id)
        self.assertEqual({"captured": 1, "missing": 0, "skipped": 0, "dry_run": False}, json.loads(out))
        self.assertEqual(1, len(self.store.list_records(dev.bead_id)))
        self.assertEqual(1, len(self.store.list_records(other.bead_id)))  # untouched by the second run


# ---------------------------------------------------------------------------
class AuditTraceTests(TraceCliBase):
    def _events(self, tool_calls: int, adr_id: str | None) -> list[dict]:
        def bash(cid, cmd):
            return {"kind": "tool_call", "call_id": cid, "tool": "Bash", "input": {"command": cmd},
                    "is_error": False, "duration_ms": 1, "result_excerpt": "", "result_chars": 0}
        events = [bash(f"c{i}", f"echo {i}") for i in range(tool_calls - (1 if adr_id else 0))]
        if adr_id:
            events.append(bash("adr", f"takt adr show {adr_id}"))
        return events

    def test_audit_includes_agent_activity(self) -> None:
        # Scenario: agent-traces / Traces in the feature audit / Audit includes agent activity
        adr = get_store(self.root, "adr").create("Some decision")
        root, dev, rev = self.make_tree()
        self.add_record(dev.bead_id, self._events(4, adr.id))
        audit = build_feature_audit(self.storage, root.bead_id)
        by_id = {b["bead_id"]: b for b in audit["beads"]}
        self.assertEqual(4, by_id[dev.bead_id]["trace"]["tool_calls"])
        self.assertEqual(1, by_id[dev.bead_id]["trace"]["documents"]["proactive"])
        self.assertIsNone(by_id[rev.bead_id]["trace"])
        self.assertEqual(4, audit["trace"]["tool_calls"])
        md = render_feature_audit_markdown(audit)
        self.assertIn("## Agent activity", md)
        self.assertIn(f"- {dev.bead_id}: 1 trace(s), 4 tool calls", md)
        self.assertIn(f"- {rev.bead_id}: no traces", md)
        self.assertIn("- Feature total: 1 trace(s), 4 tool calls", md)

    def test_feature_total_null_without_traces(self) -> None:
        root, _, _ = self.make_tree()
        audit = build_feature_audit(self.storage, root.bead_id)
        self.assertIsNone(audit["trace"])
        self.assertTrue(all(b["trace"] is None for b in audit["beads"]))
        self.assertIn("- Feature total: no traces", render_feature_audit_markdown(audit))

    def test_totals_sum_across_beads_and_records(self) -> None:
        root, dev, rev = self.make_tree()
        self.add_record(dev.bead_id, self._events(3, None))
        self.add_record(dev.bead_id, self._events(2, None), role="structured_output_retry")
        self.add_record(rev.bead_id, self._events(1, None))
        audit = build_feature_audit(self.storage, root.bead_id)
        by_id = {b["bead_id"]: b["trace"] for b in audit["beads"]}
        self.assertEqual((2, 5), (by_id[dev.bead_id]["traces"], by_id[dev.bead_id]["tool_calls"]))
        self.assertEqual((3, 6), (audit["trace"]["traces"], audit["trace"]["tool_calls"]))

    def test_failing_analyser_makes_trace_null_without_raising(self) -> None:
        root, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, self._events(2, None))
        with patch("agent_takt.traces.analyzers.run_analyzers", side_effect=RuntimeError("bad")):
            audit = build_feature_audit(self.storage, root.bead_id)
        self.assertTrue(all(b["trace"] is None for b in audit["beads"]))
        self.assertIsNone(audit["trace"])

    def test_failing_trace_store_makes_trace_null(self) -> None:
        root, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, self._events(2, None))
        with patch("agent_takt.traces.store.TraceStore.list_records", side_effect=OSError("x")):
            audit = build_feature_audit(self.storage, root.bead_id)
        self.assertIsNone(audit["trace"])

    def test_trace_present_in_merge_audit_snapshot_json(self) -> None:
        root, dev, _ = self.make_tree()
        self.add_record(dev.bead_id, self._events(2, None))
        snapshot = json.loads(json.dumps(build_feature_audit(self.storage, root.bead_id)))
        self.assertEqual(2, snapshot["trace"]["tool_calls"])


# ---------------------------------------------------------------------------
class TraceLifecycleTests(TraceCliBase):
    def _delete(self, bead_id: str) -> tuple[int, str]:
        err = io.StringIO()
        console = ConsoleReporter(stream=err)
        code = command_bead(Namespace(bead_command="delete", bead_id=bead_id, force=True), self.storage, console)
        return code, err.getvalue()

    def test_delete_removes_traces_and_leaves_planner(self) -> None:
        # Scenario: agent-traces / Trace lifecycle and hygiene / Delete removes traces
        bead = self.storage.create_bead(title="Del", agent_type="developer", description="d")
        self.add_record(bead.bead_id, [])
        self.add_record(None, [], role="planner")
        self.assertTrue(self.store.bead_dir(bead.bead_id).exists())
        code, text = self._delete(bead.bead_id)
        self.assertEqual(0, code)
        self.assertFalse(self.store.bead_dir(bead.bead_id).exists())
        self.assertTrue((self.root / ".takt" / "traces" / "_planner" / "1.meta.json").exists())
        self.assertIn("Removed", text)

    def test_delete_without_traces_dir_reports_no_artifact_directory(self) -> None:
        bead = self.storage.create_bead(title="Del", agent_type="developer", description="d")
        code, text = self._delete(bead.bead_id)
        self.assertEqual(0, code)
        self.assertIn(f"No artifact directory at {self.store.bead_dir(bead.bead_id).resolve()}", text)

    def test_scaffold_ignores_traces(self) -> None:
        # Scenario: agent-traces / Trace lifecycle and hygiene / Scaffold ignores traces
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            update_gitignore(root)
            lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn(".takt/traces/", lines)
            self.assertFalse(update_gitignore(root))  # idempotent
            self.assertEqual(1, (root / ".gitignore").read_text(encoding="utf-8").count(".takt/traces/"))
