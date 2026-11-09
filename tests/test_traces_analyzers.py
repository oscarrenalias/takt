"""Tests for the trace analysers: tool_usage, commands, document_access (+ detection) and the registry."""
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

from agent_takt.documents.registry import get_store
from agent_takt.traces.analyzers import (
    REGISTRY,
    AnalyzerContext,
    analyzer_names,
    merge_totals,
    run_analyzers,
)
from agent_takt.traces.analyzers.commands import analyze_commands
from agent_takt.traces.analyzers.document_access import analyze_document_access
from agent_takt.traces.analyzers.document_access_detect import detect_document_accesses
from agent_takt.traces.analyzers.tool_usage import analyze_tool_usage


def call(tool: str, tool_input=None, *, call_id: str = "c", is_error: bool = False,
         duration_ms=None, result: str = "") -> dict:
    return {
        "kind": "tool_call", "ts": None, "call_id": call_id, "tool": tool, "input": tool_input,
        "result_ts": None, "duration_ms": duration_ms, "is_error": is_error,
        "result_excerpt": result, "result_chars": len(result),
    }


def bash(command: str, **kw) -> dict:
    return call("Bash", {"command": command}, **kw)


def session(cwd: str) -> dict:
    return {"kind": "session", "session_id": "s", "cwd": cwd, "started_at": None}


# ---------------------------------------------------------------------------
class ToolUsageTests(unittest.TestCase):
    def test_tool_counts_exclude_structured_output(self) -> None:
        # Scenario: agent-traces / Tool usage and command analysis / Tool counts exclude StructuredOutput
        events = [
            call("Bash", {"command": "ls"}, call_id="1", duration_ms=10),
            call("Bash", {"command": "ls"}, call_id="2", is_error=True, duration_ms=30),
            call("Read", {}, call_id="3"),  # None duration tolerated
            call("StructuredOutput", {}, call_id="4", is_error=True, duration_ms=99),
            {"kind": "usage"}, {"kind": "usage"},
            {"kind": "future_event"},
        ]
        out = analyze_tool_usage(events)
        self.assertEqual(3, out["tool_calls"])
        self.assertEqual(1, out["errors"])
        self.assertNotIn("StructuredOutput", out["by_tool"])
        self.assertEqual({"calls": 2, "errors": 1, "duration_ms": 40}, out["by_tool"]["Bash"])
        self.assertEqual({"calls": 1, "errors": 0, "duration_ms": 0}, out["by_tool"]["Read"])
        self.assertEqual(2, out["api_messages"])
        self.assertAlmostEqual(1 / 3, out["error_rate"])

    def test_error_rate_zero_without_calls(self) -> None:
        out = analyze_tool_usage([])
        self.assertEqual((0, 0.0, 0), (out["tool_calls"], out["error_rate"], out["api_messages"]))


class CommandsTests(unittest.TestCase):
    def test_repeated_command_detected_through_cd_prefix(self) -> None:
        # Scenario: agent-traces / Tool usage and command analysis / Repeated command detected through cd prefix
        events = [
            bash("cd /work/wt && uv run pytest tests/x.py", call_id="1"),
            bash("uv run pytest   tests/x.py", call_id="2"),
            bash("cd '/work dir' && uv run pytest tests/x.py", call_id="3", is_error=True),
            bash("ls", call_id="4"),
            bash("ls", call_id="5"),  # only twice: not repeated
        ]
        out = analyze_commands(events)
        self.assertEqual(5, out["commands"])
        self.assertEqual([{"command_excerpt": "uv run pytest tests/x.py", "count": 3}], out["repeated"])
        self.assertEqual(["3"], [f["call_id"] for f in out["failed"]])

    def test_only_bash_with_command_counted_and_unknown_kinds_ignored(self) -> None:
        events = [
            bash("echo hi"), call("Read", {"command": "x"}), call("Bash", None), call("Bash", {"x": 1}),
            call("Bash", {"command": "  "}), {"kind": "weird"},
        ]
        self.assertEqual(1, analyze_commands(events)["commands"])


# ---------------------------------------------------------------------------
class RegistryTests(unittest.TestCase):
    def test_registry_order_and_names(self) -> None:
        self.assertEqual(["tool_usage", "commands", "document_access"], [a.name for a in REGISTRY])
        self.assertEqual(["tool_usage", "commands", "document_access"], analyzer_names())

    def test_run_analyzers_names_order_and_unknown(self) -> None:
        ctx = AnalyzerContext()
        out = run_analyzers([], ctx, names=["document_access", "tool_usage"])
        self.assertEqual(["tool_usage", "document_access"], list(out))
        self.assertEqual(3, len(run_analyzers([], ctx)))
        with self.assertRaises(ValueError):
            run_analyzers([], ctx, names=["nope"])

    def test_empty_events_and_context_without_roots_do_not_raise(self) -> None:
        out = run_analyzers([], AnalyzerContext())
        self.assertEqual([], out["document_access"]["accesses"])
        self.assertEqual({"linked": 0, "proactive": 0, "search": 0, "unresolved": 0},
                         out["document_access"]["counts"])
        # a linked doc with no roots at all
        out = run_analyzers([], AnalyzerContext(linked_docs=["spec:spec-deadbeef"]))
        self.assertEqual(["spec-deadbeef"], out["document_access"]["linked_not_accessed"])

    def test_merge_totals(self) -> None:
        a = analyze_tool_usage([call("Bash", {}, is_error=True, duration_ms=5), call("Read", {})])
        b = analyze_tool_usage([call("Bash", {}, duration_ms=7)])
        merged = merge_totals("tool_usage", [a, b])
        self.assertEqual(3, merged["tool_calls"])
        self.assertEqual({"calls": 2, "errors": 1, "duration_ms": 12}, merged["by_tool"]["Bash"])
        self.assertEqual({"calls": 1, "errors": 0, "duration_ms": 0}, merged["by_tool"]["Read"])
        self.assertAlmostEqual(1 / 3, merged["error_rate"])
        c1 = {"commands": 1, "failed": [{"call_id": "1"}], "repeated": [{"command_excerpt": "a", "count": 3}]}
        c2 = {"commands": 2, "failed": [{"call_id": "2"}], "repeated": [{"command_excerpt": "a", "count": 4}]}
        m = merge_totals("commands", [c1, c2])
        self.assertEqual(3, m["commands"])
        self.assertEqual(2, len(m["failed"]))
        self.assertEqual(2, len(m["repeated"]))  # concatenated, not re-aggregated
        zero = merge_totals("tool_usage", [analyze_tool_usage([]), analyze_tool_usage([])])
        self.assertEqual(0.0, zero["error_rate"])


# ---------------------------------------------------------------------------
class DetectTests(unittest.TestCase):
    """Raw candidate detection, covering every invocation form."""

    def _detect(self, events, main="/main"):
        return detect_document_accesses(events, main)

    def _one(self, command: str) -> dict:
        found = self._detect([bash(command)])
        self.assertEqual(1, len(found), found)
        return found[0]

    def test_all_prefix_forms_for_show_search_list(self) -> None:
        prefixes = ["takt", "$TAKT_CMD", "${TAKT_CMD}", "/opt/venv/bin/takt", "uv run takt",
                    "uv run --project /x takt", "FOO=1 uv run takt"]
        for prefix in prefixes:
            for doc_type in ("spec", "adr"):
                show = self._one(f"{prefix} {doc_type} show abc123")
                self.assertEqual(("cli_show", doc_type, "abc123"), (show["method"], show["doc_type"], show["target"]), prefix)
                search = self._one(f'{prefix} {doc_type} search "stack agnostic" --limit 3 --tag x')
                self.assertEqual(("cli_search", "stack agnostic"), (search["method"], search["query"]), prefix)
                listing = self._one(f"{prefix} {doc_type} list --status approved --plain")
                self.assertEqual("cli_list", listing["method"], prefix)

    def test_show_with_field_flag_value_not_a_target(self) -> None:
        cand = self._one("takt adr show ADR-d2b1f2b4 --field decision")
        self.assertEqual("ADR-d2b1f2b4", cand["target"])

    def test_chained_commands(self) -> None:
        events = [bash("cd /wt && takt spec show aaa; takt adr show bbb | head -5 && echo done", call_id="x")]
        found = self._detect(events)
        self.assertEqual([("cli_show", "spec", "aaa"), ("cli_show", "adr", "bbb")],
                         [(f["method"], f["doc_type"], f["target"]) for f in found])

    def test_read_under_cwd_and_main_checkout(self) -> None:
        events = [session("/work/wt"),
                  call("Read", {"file_path": "/work/wt/specs/planned/a.md"}, call_id="1"),
                  call("Read", {"file_path": "/main/adr/approved/adr-aaaaaaaa-x.md"}, call_id="2"),
                  call("Read", {"file_path": "/elsewhere/specs/b.md"}, call_id="3"),
                  call("Read", {"file_path": "/work/wt/src/x.py"}, call_id="4")]
        found = self._detect(events)
        self.assertEqual([("read", "spec", "specs/planned/a.md"), ("read", "adr", "adr/approved/adr-aaaaaaaa-x.md")],
                         [(f["method"], f["doc_type"], f["target"]) for f in found])

    def test_shell_reads(self) -> None:
        events = [session("/work/wt")]
        for command in ("cat specs/planned/a.md", "sed -n 1,20p adr/approved/adr-aaaaaaaa-x.md",
                        "head -n 5 /work/wt/specs/b.md", "grep -n foo specs/c.md"):
            events.append(bash(command, call_id=command))
        found = self._detect(events)
        self.assertEqual(4, len(found))
        self.assertTrue(all(f["method"] == "shell_read" for f in found))
        self.assertEqual(["specs/planned/a.md", "adr/approved/adr-aaaaaaaa-x.md", "specs/b.md", "specs/c.md"],
                         [f["target"] for f in found])
        self.assertEqual([], self._detect([bash("cat src/x.py"), bash("sed s/a/b/ specs/a.md")]))

    def test_grep_and_glob(self) -> None:
        events = [call("Grep", {"pattern": "foo", "path": "specs/planned"}, call_id="1"),
                  call("Glob", {"pattern": "adr/approved/*.md"}, call_id="2"),
                  call("Glob", {"pattern": "src/**/*.py"}, call_id="3"),
                  call("Grep", {"pattern": "foo", "path": "src"}, call_id="4")]
        found = self._detect(events)
        self.assertEqual([("grep", "spec"), ("grep", "adr")], [(f["method"], f["doc_type"]) for f in found])
        self.assertEqual("foo", found[0]["query"])

    def test_ignored_inputs(self) -> None:
        events = [call("Bash", "not a dict"), call("Bash", {}), call("Bash", {"command": 5}),
                  call("Read", None), {"kind": "usage"}, bash("ls -la")]
        self.assertEqual([], self._detect(events))


class DocumentAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.main = base / "main"
        self.ws = base / "ws"
        self.main.mkdir()
        self.ws.mkdir()
        self.spec = get_store(self.main, "spec").create("Linked feature")
        self.other_spec = get_store(self.main, "spec").create("Other feature")
        self.adr = get_store(self.main, "adr").create("Unlinked decision")
        self.rel_adr = str(self.adr.path.relative_to(self.main))
        self.rel_spec = str(self.spec.path.relative_to(self.main))

    def _ctx(self, linked=None) -> AnalyzerContext:
        return AnalyzerContext(
            bead_id="B-1", agent_type="developer", linked_docs=linked if linked is not None else [f"spec:{self.spec.id}"],
            repo_root=self.ws, main_checkout_root=self.main,
        )

    def _by_class(self, out: dict) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for a in out["accesses"]:
            grouped.setdefault(a["class"], []).append(a)
        return grouped

    def test_linked_spec_fetched_by_id(self) -> None:
        # Scenario: agent-traces / Document access analysis / Linked spec fetched by ID
        events = [bash(f"takt spec show {self.spec.id}", call_id="a"),
                  bash(f"takt spec show {self.spec.id[len('spec-'):]}", call_id="b")]  # bare hex prefix
        out = analyze_document_access(events, self._ctx())
        self.assertEqual(2, out["counts"]["linked"])
        self.assertTrue(all(a["doc_id"] == self.spec.id for a in out["accesses"]))
        self.assertEqual([], out["linked_not_accessed"])

    def test_unlinked_adr_read_proactively(self) -> None:
        # Scenario: agent-traces / Document access analysis / Unlinked ADR read proactively
        events = [session(str(self.ws)), call("Read", {"file_path": str(self.main / self.rel_adr)}, call_id="r")]
        out = analyze_document_access(events, self._ctx())
        self.assertEqual(1, out["counts"]["proactive"])
        access = out["accesses"][0]
        self.assertEqual((self.adr.id, "proactive", "read"), (access["doc_id"], access["class"], access["method"]))

    def test_read_of_spec_file_gives_its_id(self) -> None:
        events = [session(str(self.main)), call("Read", {"file_path": str(self.main / self.rel_spec)})]
        out = analyze_document_access(events, self._ctx())
        self.assertEqual((self.spec.id, "linked"), (out["accesses"][0]["doc_id"], out["accesses"][0]["class"]))

    def test_access_through_takt_cmd(self) -> None:
        # Scenario: agent-traces / Document access analysis / Access through $TAKT_CMD
        events = [bash(f"cd /somewhere && $TAKT_CMD adr show {self.adr.id}", call_id="t"),
                  bash(f"${{TAKT_CMD}} adr show {self.adr.id[len('ADR-'):]}", call_id="u")]
        out = analyze_document_access(events, self._ctx())
        self.assertEqual(2, out["counts"]["proactive"])
        self.assertEqual({self.adr.id}, {a["doc_id"] for a in out["accesses"]})
        self.assertEqual({"cli_show"}, {a["method"] for a in out["accesses"]})

    def test_search_counted_with_query_and_result_count(self) -> None:
        # Scenario: agent-traces / Document access analysis / Search is counted with its query
        events = [
            bash('takt spec search "agent traces" --limit 5', call_id="s", result=json.dumps([{"id": 1}, {"id": 2}])),
            bash("takt adr list --status approved", call_id="l"),
        ]
        out = analyze_document_access(events, self._ctx())
        self.assertEqual(2, out["counts"]["search"])
        first, second = out["accesses"]
        self.assertEqual(("search", "agent traces", 2), (first["class"], first["query"], first["result_count"]))
        self.assertEqual("search", second["class"])
        self.assertNotIn("result_count", second)

    def test_linked_document_never_accessed(self) -> None:
        # Scenario: agent-traces / Document access analysis / Linked document never accessed
        events = [bash(f"takt spec show {self.other_spec.id}")]
        out = analyze_document_access(events, self._ctx([f"spec:{self.spec.id}", f"adr:{self.adr.id}"]))
        self.assertEqual({"proactive": 1}, {k: v for k, v in out["counts"].items() if v})
        self.assertEqual([self.spec.id, self.adr.id], out["linked_not_accessed"])

    def test_unknown_target_is_unresolved(self) -> None:
        out = analyze_document_access([bash("takt spec show zzzz9999")], self._ctx())
        access = out["accesses"][0]
        self.assertEqual((None, "unresolved", "zzzz9999"), (access["doc_id"], access["class"], access["target"]))

    def test_ambiguous_spec_stem_is_unresolved(self) -> None:
        events = [bash("takt spec show spec-")]
        out = analyze_document_access(events, self._ctx())
        self.assertEqual("unresolved", out["accesses"][0]["class"])

    def test_adr_prefix_forms_resolve(self) -> None:
        hexpart = self.adr.id.split("-", 1)[1]
        for target in (self.adr.id, self.adr.id[:8], hexpart, hexpart[:4]):
            out = analyze_document_access([bash(f"takt adr show {target}")], self._ctx())
            self.assertEqual(self.adr.id, out["accesses"][0]["doc_id"], target)

    def test_class_counts_cover_every_form(self) -> None:
        events = [
            bash(f"takt spec show {self.spec.id}", call_id="1"),
            bash('takt adr search "x"', call_id="2"),
            call("Grep", {"pattern": "x", "path": "specs"}, call_id="3"),
            bash(f"cat {self.rel_adr}", call_id="4"),
        ]
        out = analyze_document_access(events, self._ctx())
        methods = [a["method"] for a in out["accesses"]]
        self.assertEqual(["cli_show", "cli_search", "grep", "shell_read"], methods)


if __name__ == "__main__":
    unittest.main()
