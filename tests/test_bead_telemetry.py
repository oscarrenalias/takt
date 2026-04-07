"""Tests for the telemetry CLI command (B-715e8f90 feature).

Covers:
- _filter_beads_by_days (--days filtering)
- aggregate_telemetry: corrective bead detection, merge-conflict detection,
  wall-clock calculation, retry rate, agent-type/status counters
- command_telemetry: --agent-type, --feature-root prefix matching,
  --json output mode, empty-state behavior, status filter
- _format_telemetry_table: human-readable report rendering
- CLI help output includes the `telemetry` subcommand
"""
from __future__ import annotations

import json
import sys
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import (
    _bead_cost_usd,
    _bead_wall_clock_seconds,
    _filter_beads_by_days,
    _format_telemetry_table,
    aggregate_telemetry,
    build_parser,
    command_telemetry,
)
from agent_takt.console import ConsoleReporter
from agent_takt.models import Bead, ExecutionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_bead(
    bead_id: str = "B-aabbccdd",
    title: str = "Test bead",
    agent_type: str = "developer",
    status: str = "done",
    bead_type: str = "task",
    block_reason: str = "",
    retries: int = 0,
    feature_root_id: str | None = None,
    history: list[ExecutionRecord] | None = None,
    cost_usd: float | None = None,
) -> Bead:
    metadata: dict = {}
    if cost_usd is not None:
        metadata["telemetry"] = {"cost_usd": cost_usd}
    return Bead(
        bead_id=bead_id,
        title=title,
        agent_type=agent_type,
        description="",
        status=status,
        bead_type=bead_type,
        block_reason=block_reason,
        retries=retries,
        feature_root_id=feature_root_id,
        execution_history=history or [],
        metadata=metadata,
    )


def _make_history(
    created_at: datetime,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> list[ExecutionRecord]:
    records = [ExecutionRecord(timestamp=_ts(created_at), event="created", agent_type="scheduler", summary="", details={})]
    if started_at:
        records.append(ExecutionRecord(timestamp=_ts(started_at), event="started", agent_type="developer", summary="", details={}))
    if completed_at:
        records.append(ExecutionRecord(timestamp=_ts(completed_at), event="completed", agent_type="developer", summary="", details={}))
    return records


def _make_storage(beads: list[Bead], feature_root_map: dict[str, str | None] | None = None) -> MagicMock:
    storage = MagicMock()
    storage.list_beads.return_value = beads
    storage.telemetry_dir = Path("/tmp/fake-telemetry")

    def _feature_root_id_for(bead: Bead) -> str | None:
        if feature_root_map:
            return feature_root_map.get(bead.bead_id)
        return bead.feature_root_id or bead.bead_id

    storage.feature_root_id_for.side_effect = _feature_root_id_for

    def _load_bead(bead_id: str) -> Bead:
        for b in beads:
            if b.bead_id == bead_id:
                return b
        raise KeyError(bead_id)

    storage.load_bead.side_effect = _load_bead
    return storage


def _make_args(**kwargs) -> Namespace:
    defaults = {
        "days": 7,
        "feature_root": None,
        "agent_type": None,
        "status": None,
        "output_json": False,
        "root": "/tmp/fake-root",
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


# ---------------------------------------------------------------------------
# _filter_beads_by_days
# ---------------------------------------------------------------------------

class TestFilterBeadsByDays(unittest.TestCase):

    def test_includes_recent_bead(self):
        now = _now()
        bead = _make_bead(history=_make_history(created_at=now - timedelta(days=2)))
        result = _filter_beads_by_days([bead], days=7)
        self.assertEqual(result, [bead])

    def test_excludes_old_bead(self):
        now = _now()
        bead = _make_bead(history=_make_history(created_at=now - timedelta(days=10)))
        result = _filter_beads_by_days([bead], days=7)
        self.assertEqual(result, [])

    def test_excludes_bead_without_history(self):
        bead = _make_bead(history=[])
        result = _filter_beads_by_days([bead], days=7)
        self.assertEqual(result, [])

    def test_boundary_bead_on_cutoff_is_included(self):
        # Exactly at the cutoff moment should be included (>=)
        now = _now()
        bead = _make_bead(history=_make_history(created_at=now - timedelta(days=7, seconds=-1)))
        result = _filter_beads_by_days([bead], days=7)
        self.assertEqual(result, [bead])

    def test_days_1_filters_correctly(self):
        now = _now()
        new_bead = _make_bead(bead_id="B-new", history=_make_history(created_at=now - timedelta(hours=6)))
        old_bead = _make_bead(bead_id="B-old", history=_make_history(created_at=now - timedelta(hours=30)))
        result = _filter_beads_by_days([new_bead, old_bead], days=1)
        self.assertIn(new_bead, result)
        self.assertNotIn(old_bead, result)


# ---------------------------------------------------------------------------
# _bead_wall_clock_seconds
# ---------------------------------------------------------------------------

class TestBeadWallClockSeconds(unittest.TestCase):

    def test_returns_none_for_no_history(self):
        bead = _make_bead(history=[])
        self.assertIsNone(_bead_wall_clock_seconds(bead))

    def test_returns_none_for_no_terminal_event(self):
        now = _now()
        bead = _make_bead(history=_make_history(created_at=now, started_at=now))
        self.assertIsNone(_bead_wall_clock_seconds(bead))

    def test_computes_wall_clock(self):
        now = _now()
        started = now
        completed = now + timedelta(seconds=120)
        history = _make_history(created_at=now, started_at=started, completed_at=completed)
        bead = _make_bead(history=history)
        wc = _bead_wall_clock_seconds(bead)
        self.assertAlmostEqual(wc, 120.0, places=1)

    def test_handles_blocked_terminal_event(self):
        now = _now()
        history = [
            ExecutionRecord(timestamp=_ts(now), event="started", agent_type="developer", summary="", details={}),
            ExecutionRecord(timestamp=_ts(now + timedelta(seconds=60)), event="blocked", agent_type="developer", summary="", details={}),
        ]
        bead = _make_bead(history=history)
        wc = _bead_wall_clock_seconds(bead)
        self.assertAlmostEqual(wc, 60.0, places=1)


# ---------------------------------------------------------------------------
# aggregate_telemetry
# ---------------------------------------------------------------------------

class TestAggregateTelemetry(unittest.TestCase):

    def _storage(self) -> MagicMock:
        storage = MagicMock()
        storage.telemetry_dir = Path("/tmp/fake-telemetry")
        return storage

    def test_empty_bead_list(self):
        agg = aggregate_telemetry([], self._storage())
        self.assertEqual(agg["total_beads"], 0)
        self.assertIsNone(agg["avg_wall_clock_seconds"])
        self.assertIsNone(agg["retry_rate"])
        self.assertEqual(agg["corrective_bead_count"], 0)
        self.assertEqual(agg["merge_conflict_bead_count"], 0)

    def test_counts_by_status(self):
        beads = [
            _make_bead(bead_id="B-1", status="done"),
            _make_bead(bead_id="B-2", status="done"),
            _make_bead(bead_id="B-3", status="blocked"),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["by_status"]["done"], 2)
        self.assertEqual(agg["by_status"]["blocked"], 1)

    def test_counts_by_agent_type(self):
        beads = [
            _make_bead(bead_id="B-1", agent_type="developer"),
            _make_bead(bead_id="B-2", agent_type="tester"),
            _make_bead(bead_id="B-3", agent_type="developer"),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["by_agent_type"]["developer"], 2)
        self.assertEqual(agg["by_agent_type"]["tester"], 1)

    def test_detects_corrective_beads(self):
        beads = [
            _make_bead(bead_id="B-aabbccdd-corrective"),
            _make_bead(bead_id="B-aabbccdd"),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["corrective_bead_count"], 1)

    def test_detects_merge_conflict_beads(self):
        beads = [
            _make_bead(bead_id="B-1", bead_type="merge-conflict"),
            _make_bead(bead_id="B-2", bead_type="task"),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["merge_conflict_bead_count"], 1)

    def test_retry_rate(self):
        beads = [
            _make_bead(bead_id="B-1", retries=1),
            _make_bead(bead_id="B-2", retries=0),
            _make_bead(bead_id="B-3", retries=2),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        # 2 out of 3 beads have retries > 0
        self.assertAlmostEqual(agg["retry_rate"], 2 / 3, places=3)

    def test_timeout_block_count(self):
        beads = [
            _make_bead(bead_id="B-1", status="blocked", block_reason="Agent timed out after 300s"),
            _make_bead(bead_id="B-2", status="blocked", block_reason="Something else"),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["timeout_block_count"], 1)

    def test_transient_block_count(self):
        beads = [
            _make_bead(bead_id="B-1", status="blocked", block_reason="rate limit exceeded"),
            _make_bead(bead_id="B-2", status="blocked", block_reason="regular block"),
        ]
        agg = aggregate_telemetry(beads, self._storage(), transient_patterns=("rate limit",))
        self.assertEqual(agg["transient_block_count"], 1)

    def test_wall_clock_average_and_p95(self):
        now = _now()

        def _bead_with_wc(bead_id: str, seconds: float) -> Bead:
            started = now
            completed = now + timedelta(seconds=seconds)
            history = _make_history(created_at=now, started_at=started, completed_at=completed)
            return _make_bead(bead_id=bead_id, history=history)

        beads = [_bead_with_wc(f"B-{i}", float(i * 10)) for i in range(1, 6)]
        # wall_clock values: 10, 20, 30, 40, 50
        agg = aggregate_telemetry(beads, self._storage())
        self.assertAlmostEqual(agg["avg_wall_clock_seconds"], 30.0, places=0)
        self.assertIsNotNone(agg["p95_wall_clock_seconds"])


# ---------------------------------------------------------------------------
# command_telemetry
# ---------------------------------------------------------------------------

class TestCommandTelemetry(unittest.TestCase):

    def _make_bead_with_history(self, bead_id: str, **kwargs) -> Bead:
        now = _now()
        history = _make_history(created_at=now - timedelta(days=1))
        return _make_bead(bead_id=bead_id, history=history, **kwargs)

    def _run_command(self, args: Namespace, beads: list[Bead], feature_root_map: dict | None = None):
        storage = _make_storage(beads, feature_root_map)
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            rc = command_telemetry(args, storage, console)
        return rc, storage, console

    def test_returns_0_on_success(self):
        beads = [self._make_bead_with_history("B-aabb1122")]
        args = _make_args()
        rc, _, _ = self._run_command(args, beads)
        self.assertEqual(rc, 0)

    def test_empty_results_returns_0(self):
        args = _make_args()
        rc, _, console = self._run_command(args, [])
        self.assertEqual(rc, 0)

    def test_filter_by_agent_type(self):
        beads = [
            self._make_bead_with_history("B-dev1", agent_type="developer"),
            self._make_bead_with_history("B-tst1", agent_type="tester"),
        ]
        args = _make_args(agent_type="tester")
        rc, storage, _ = self._run_command(args, beads)
        self.assertEqual(rc, 0)

    def test_filter_by_status(self):
        beads = [
            self._make_bead_with_history("B-d1", status="done"),
            self._make_bead_with_history("B-b1", status="blocked"),
        ]
        args = _make_args(status="done")
        rc, _, _ = self._run_command(args, beads)
        self.assertEqual(rc, 0)

    def test_json_output_mode(self):
        beads = [self._make_bead_with_history("B-aabb1122")]
        args = _make_args(output_json=True)
        storage = _make_storage(beads)
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            rc = command_telemetry(args, storage, console)
        self.assertEqual(rc, 0)
        console.dump_json.assert_called_once()
        dumped = console.dump_json.call_args[0][0]
        self.assertIn("aggregates", dumped)
        self.assertIn("filters", dumped)
        self.assertIn("bead_count", dumped)

    def test_feature_root_prefix_matching(self):
        """--feature-root resolves via storage.resolve_bead_id and filters."""
        root_bead = self._make_bead_with_history("B-aabbccdd", agent_type="developer")
        child_bead = self._make_bead_with_history("B-11223344", agent_type="tester", feature_root_id="B-aabbccdd")
        unrelated = self._make_bead_with_history("B-99887766", agent_type="developer")

        storage = _make_storage([root_bead, child_bead, unrelated])
        storage.resolve_bead_id.return_value = "B-aabbccdd"

        def _feature_root_id_for(bead: Bead) -> str | None:
            if bead.bead_id == "B-aabbccdd":
                return "B-aabbccdd"
            if bead.bead_id == "B-11223344":
                return "B-aabbccdd"
            return bead.bead_id

        storage.feature_root_id_for.side_effect = _feature_root_id_for

        args = _make_args(feature_root="B-aabb")
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            rc = command_telemetry(args, storage, console)

        self.assertEqual(rc, 0)
        storage.resolve_bead_id.assert_called_once_with("B-aabb")
        # dump_json not called (non-JSON mode)
        console.dump_json.assert_not_called()

    def test_feature_root_invalid_prefix_returns_1(self):
        """When resolve_bead_id raises ValueError, command returns 1."""
        beads = [self._make_bead_with_history("B-aabbccdd")]
        storage = _make_storage(beads)
        storage.resolve_bead_id.side_effect = ValueError("No match")
        args = _make_args(feature_root="B-nope")
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            rc = command_telemetry(args, storage, console)
        self.assertEqual(rc, 1)
        console.error.assert_called_once()

    def test_json_output_includes_beads_list(self):
        """JSON output includes per-bead details."""
        bead = self._make_bead_with_history("B-aabb1122")
        args = _make_args(output_json=True)
        storage = _make_storage([bead])
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            command_telemetry(args, storage, console)
        dumped = console.dump_json.call_args[0][0]
        self.assertIn("beads", dumped)
        self.assertIsInstance(dumped["beads"], list)

    def test_days_filter_applied(self):
        """Beads older than --days are excluded."""
        old_bead = _make_bead(
            bead_id="B-old1old1",
            history=_make_history(created_at=_now() - timedelta(days=30)),
        )
        recent = _make_bead(
            bead_id="B-new1new1",
            history=_make_history(created_at=_now() - timedelta(days=1)),
        )
        args = _make_args(days=7, output_json=True)
        storage = _make_storage([old_bead, recent])
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            command_telemetry(args, storage, console)
        dumped = console.dump_json.call_args[0][0]
        bead_ids = [b["bead_id"] for b in dumped["beads"]]
        self.assertIn("B-new1new1", bead_ids)
        self.assertNotIn("B-old1old1", bead_ids)


# ---------------------------------------------------------------------------
# _format_telemetry_table
# ---------------------------------------------------------------------------

class TestFormatTelemetryTable(unittest.TestCase):

    def _agg_empty(self) -> dict:
        return {
            "total_beads": 0,
            "by_status": {},
            "by_agent_type": {},
            "avg_wall_clock_seconds": None,
            "p95_wall_clock_seconds": None,
            "avg_turns": None,
            "retry_rate": None,
            "corrective_bead_count": 0,
            "merge_conflict_bead_count": 0,
            "timeout_block_count": 0,
            "transient_block_count": 0,
        }

    def _make_data(self, agg_overrides: dict | None = None, filters: dict | None = None) -> dict:
        agg = self._agg_empty()
        if agg_overrides:
            agg.update(agg_overrides)
        return {
            "filters": filters or {"days": 7, "feature_root": None, "agent_type": None, "status": None},
            "aggregates": agg,
            "feature_roots": [],
        }

    def test_empty_state_emits_no_beads_found(self):
        console = MagicMock(spec=ConsoleReporter)
        _format_telemetry_table(self._make_data(), console)
        output = console.emit.call_args[0][0]
        self.assertIn("No beads found", output)

    def test_header_includes_days(self):
        console = MagicMock(spec=ConsoleReporter)
        _format_telemetry_table(self._make_data(filters={"days": 14, "feature_root": None, "agent_type": None, "status": None}), console)
        output = console.emit.call_args[0][0]
        self.assertIn("14 days", output)

    def test_header_includes_feature_root_filter(self):
        console = MagicMock(spec=ConsoleReporter)
        _format_telemetry_table(self._make_data(filters={"days": 7, "feature_root": "B-aabb", "agent_type": None, "status": None}), console)
        output = console.emit.call_args[0][0]
        self.assertIn("feature_root=B-aabb", output)

    def test_header_includes_agent_type_filter(self):
        console = MagicMock(spec=ConsoleReporter)
        _format_telemetry_table(self._make_data(filters={"days": 7, "feature_root": None, "agent_type": "tester", "status": None}), console)
        output = console.emit.call_args[0][0]
        self.assertIn("agent_type=tester", output)

    def test_non_empty_shows_totals(self):
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(agg_overrides={
            "total_beads": 5,
            "by_status": {"done": 3, "blocked": 2},
            "by_agent_type": {"developer": 5},
            "avg_wall_clock_seconds": 45.0,
            "p95_wall_clock_seconds": 90.0,
            "avg_turns": 8.5,
            "retry_rate": 0.2,
            "corrective_bead_count": 1,
            "merge_conflict_bead_count": 0,
            "timeout_block_count": 1,
            "transient_block_count": 0,
        })
        _format_telemetry_table(data, console)
        output = console.emit.call_args[0][0]
        self.assertIn("Total beads: 5", output)
        self.assertIn("done", output)
        self.assertIn("45.0s", output)
        self.assertIn("20.0%", output)
        self.assertIn("Corrective beads  : 1", output)

    def test_na_shown_when_metrics_absent(self):
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(agg_overrides={
            "total_beads": 1,
            "by_status": {"done": 1},
            "by_agent_type": {"developer": 1},
        })
        _format_telemetry_table(data, console)
        output = console.emit.call_args[0][0]
        self.assertIn("N/A", output)


# ---------------------------------------------------------------------------
# CLI help includes telemetry subcommand
# ---------------------------------------------------------------------------

class TestCliHelpIncludesTelemetry(unittest.TestCase):

    def _find_command_subparsers(self, parser):
        """Return the subparsers action whose choices include command names."""
        for action in parser._actions:
            if hasattr(action, "choices") and "telemetry" in (action.choices or {}):
                return action
        return None

    def test_telemetry_in_help(self):
        """Build the parser and ensure 'telemetry' appears in choices."""
        parser = build_parser()
        subparsers_action = self._find_command_subparsers(parser)
        self.assertIsNotNone(subparsers_action, "No subparsers action with 'telemetry' found in parser")
        self.assertIn("telemetry", subparsers_action.choices)

    def test_telemetry_has_days_flag(self):
        parser = build_parser()
        subparsers_action = self._find_command_subparsers(parser)
        self.assertIsNotNone(subparsers_action)
        tel_parser = subparsers_action.choices["telemetry"]
        option_strings = [
            opt
            for action in tel_parser._actions
            for opt in action.option_strings
        ]
        self.assertIn("--days", option_strings)
        self.assertIn("--feature-root", option_strings)
        self.assertIn("--agent-type", option_strings)
        self.assertIn("--json", option_strings)


# ---------------------------------------------------------------------------
# _bead_cost_usd
# ---------------------------------------------------------------------------

class TestBeadCostUsd(unittest.TestCase):

    def test_returns_none_when_no_metadata(self):
        bead = _make_bead()
        self.assertIsNone(_bead_cost_usd(bead))

    def test_returns_none_when_telemetry_missing_cost(self):
        bead = _make_bead()
        bead.metadata["telemetry"] = {}
        self.assertIsNone(_bead_cost_usd(bead))

    def test_returns_float_when_cost_present(self):
        bead = _make_bead(cost_usd=0.1234)
        self.assertAlmostEqual(_bead_cost_usd(bead), 0.1234, places=4)

    def test_returns_none_when_metadata_is_empty_dict(self):
        bead = _make_bead()
        self.assertEqual(bead.metadata, {})
        self.assertIsNone(_bead_cost_usd(bead))

    def test_handles_integer_cost(self):
        bead = _make_bead()
        bead.metadata["telemetry"] = {"cost_usd": 1}
        self.assertEqual(_bead_cost_usd(bead), 1.0)
        self.assertIsInstance(_bead_cost_usd(bead), float)


# ---------------------------------------------------------------------------
# aggregate_telemetry — cost fields
# ---------------------------------------------------------------------------

class TestAggregateTelemetryCost(unittest.TestCase):

    def _storage(self) -> MagicMock:
        storage = MagicMock()
        storage.telemetry_dir = Path("/tmp/fake-telemetry")
        return storage

    def test_total_and_avg_cost_none_when_no_cost_data(self):
        beads = [_make_bead(bead_id="B-1"), _make_bead(bead_id="B-2")]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertIsNone(agg["total_cost_usd"])
        self.assertIsNone(agg["avg_cost_usd_per_bead"])

    def test_total_cost_sums_beads_with_cost(self):
        beads = [
            _make_bead(bead_id="B-1", cost_usd=0.10),
            _make_bead(bead_id="B-2", cost_usd=0.20),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertAlmostEqual(agg["total_cost_usd"], 0.30, places=4)

    def test_avg_cost_excludes_beads_without_cost(self):
        beads = [
            _make_bead(bead_id="B-1", cost_usd=0.10),
            _make_bead(bead_id="B-2"),  # no cost
            _make_bead(bead_id="B-3", cost_usd=0.30),
        ]
        agg = aggregate_telemetry(beads, self._storage())
        # avg only over B-1 and B-3
        self.assertAlmostEqual(agg["avg_cost_usd_per_bead"], 0.20, places=4)

    def test_cost_rounded_to_4_decimal_places(self):
        beads = [_make_bead(bead_id="B-1", cost_usd=0.123456789)]
        agg = aggregate_telemetry(beads, self._storage())
        self.assertEqual(agg["total_cost_usd"], round(0.123456789, 4))


# ---------------------------------------------------------------------------
# _format_telemetry_table — cost lines and per-bead breakdown
# ---------------------------------------------------------------------------

class TestFormatTelemetryTableCost(unittest.TestCase):

    def _agg(self, total_cost=None, avg_cost=None) -> dict:
        return {
            "total_beads": 2,
            "by_status": {"done": 2},
            "by_agent_type": {"developer": 2},
            "avg_wall_clock_seconds": None,
            "p95_wall_clock_seconds": None,
            "avg_turns": None,
            "retry_rate": None,
            "corrective_bead_count": 0,
            "merge_conflict_bead_count": 0,
            "timeout_block_count": 0,
            "transient_block_count": 0,
            "total_cost_usd": total_cost,
            "avg_cost_usd_per_bead": avg_cost,
        }

    def _make_data(self, agg, feature_root=None) -> dict:
        return {
            "filters": {"days": 7, "feature_root": feature_root, "agent_type": None, "status": None},
            "aggregates": agg,
            "feature_roots": [],
        }

    def test_cost_na_when_no_cost_data(self):
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(self._agg())
        _format_telemetry_table(data, console)
        output = console.emit.call_args[0][0]
        self.assertIn("Total cost        : N/A", output)
        self.assertIn("Avg cost/bead     : N/A", output)

    def test_cost_shown_when_data_present(self):
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(self._agg(total_cost=0.5000, avg_cost=0.2500))
        _format_telemetry_table(data, console)
        output = console.emit.call_args[0][0]
        self.assertIn("Total cost        : $0.5000", output)
        self.assertIn("Avg cost/bead     : $0.2500", output)

    def test_per_bead_breakdown_shown_with_feature_root_and_beads(self):
        now = _now()
        b1 = _make_bead(bead_id="B-aabbccdd11223344", agent_type="developer", status="done", cost_usd=0.1000)
        b1.execution_history = _make_history(
            created_at=now - timedelta(seconds=60),
            started_at=now - timedelta(seconds=60),
            completed_at=now,
        )
        b2 = _make_bead(bead_id="B-aabbccdd99887766", agent_type="tester", status="done")
        b2.execution_history = _make_history(created_at=now)
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(self._agg(total_cost=0.1000, avg_cost=0.1000), feature_root="B-aabb")
        _format_telemetry_table(data, console, beads=[b1, b2])
        output = console.emit.call_args[0][0]
        self.assertIn("Per-bead breakdown:", output)
        self.assertIn("B-aabbccdd11223344", output)
        self.assertIn("$0.1000", output)
        # b2 has no cost, should show "-"
        self.assertIn("-", output)

    def test_per_bead_breakdown_not_shown_without_feature_root(self):
        console = MagicMock(spec=ConsoleReporter)
        now = _now()
        b = _make_bead(bead_id="B-aabbccdd11223344", cost_usd=0.05)
        data = self._make_data(self._agg(total_cost=0.05, avg_cost=0.05), feature_root=None)
        _format_telemetry_table(data, console, beads=[b])
        output = console.emit.call_args[0][0]
        self.assertNotIn("Per-bead breakdown:", output)

    def test_per_bead_breakdown_not_shown_when_beads_none(self):
        console = MagicMock(spec=ConsoleReporter)
        data = self._make_data(self._agg(), feature_root="B-aabb")
        _format_telemetry_table(data, console, beads=None)
        output = console.emit.call_args[0][0]
        self.assertNotIn("Per-bead breakdown:", output)


# ---------------------------------------------------------------------------
# command_telemetry JSON — cost_usd in per-bead entries
# ---------------------------------------------------------------------------

class TestCommandTelemetryCostJson(unittest.TestCase):

    def _make_bead_with_history(self, bead_id: str, **kwargs) -> Bead:
        now = _now()
        history = _make_history(created_at=now - timedelta(days=1))
        return _make_bead(bead_id=bead_id, history=history, **kwargs)

    def _run_command(self, args, beads):
        storage = _make_storage(beads)
        console = MagicMock(spec=ConsoleReporter)
        with patch("agent_takt.cli.commands.telemetry.load_config") as mock_config:
            mock_config.return_value = MagicMock(
                scheduler=MagicMock(transient_block_patterns=()),
            )
            rc = command_telemetry(args, storage, console)
        return rc, console

    def test_json_beads_include_cost_usd_field(self):
        bead = self._make_bead_with_history("B-aabb1122", cost_usd=0.25)
        args = _make_args(output_json=True)
        rc, console = self._run_command(args, [bead])
        self.assertEqual(rc, 0)
        dumped = console.dump_json.call_args[0][0]
        bead_entries = dumped["beads"]
        self.assertEqual(len(bead_entries), 1)
        self.assertIn("cost_usd", bead_entries[0])
        self.assertAlmostEqual(bead_entries[0]["cost_usd"], 0.25, places=4)

    def test_json_beads_cost_usd_none_when_no_metadata(self):
        bead = self._make_bead_with_history("B-aabb1122")
        args = _make_args(output_json=True)
        rc, console = self._run_command(args, [bead])
        self.assertEqual(rc, 0)
        dumped = console.dump_json.call_args[0][0]
        self.assertIsNone(dumped["beads"][0]["cost_usd"])

    def test_json_aggregates_include_cost_fields(self):
        bead = self._make_bead_with_history("B-aabb1122", cost_usd=0.10)
        args = _make_args(output_json=True)
        rc, console = self._run_command(args, [bead])
        self.assertEqual(rc, 0)
        dumped = console.dump_json.call_args[0][0]
        agg = dumped["aggregates"]
        self.assertIn("total_cost_usd", agg)
        self.assertIn("avg_cost_usd_per_bead", agg)
        self.assertAlmostEqual(agg["total_cost_usd"], 0.10, places=4)
        self.assertAlmostEqual(agg["avg_cost_usd_per_bead"], 0.10, places=4)


if __name__ == "__main__":
    unittest.main()
