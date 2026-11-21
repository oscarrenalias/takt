"""Synthetic fleet builder for the web UI tests and ``--demo`` mode.

``build_fixture_fleet`` writes a set of fake takt projects plus a fleet
registry file under a caller-supplied directory.  It never touches the real
registry or any real project, and takes all timestamps from an injectable
clock so output is deterministic for a fixed clock.

Project variants (assigned by project index, cycling every five projects):

* 0 ``full``      current bead shape, finished run plus an alive active run
* 1 ``older``     beads missing optional fields
* 2 ``newer``     beads (and handoff summaries) with unknown extra fields
* 3 ``corrupt``   one unparseable bead file next to valid ones
* 4 ``no-version`` no ``.takt/version.json``

The ``full`` project also gets three fake Claude sessions (growing, waiting,
ended) whose transcripts live in ``FixtureFleet.claude_config_dir``.

One extra registry entry always points at a path that does not exist.
Use ``count >= 5`` to get every variant.  This module imports nothing from
``agent_takt``: the file shapes follow docs/events.md and the bead JSON
contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

Clock = Callable[[], datetime]

VARIANTS = ("full", "older", "newer", "corrupt", "no-version")
MISSING_PROJECT_NAME = "ghost"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class FixtureProject:
    name: str
    path: Path
    tags: tuple[str, ...]
    variant: str
    feature_root_id: str | None = None
    active_run_id: str | None = None
    bead_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixtureFleet:
    root: Path
    registry_path: Path
    projects: tuple[FixtureProject, ...]
    now: datetime
    missing: tuple[FixtureProject, ...] = field(default=())
    claude_config_dir: Path | None = None
    sessions: tuple["FixtureSession", ...] = field(default=())


@dataclass(frozen=True)
class FixtureSession:
    """A fake Claude Code session: ``kind`` is growing, waiting or ended."""

    kind: str
    project: str
    bead_id: str
    session_id: str
    transcript_path: Path | None  # None for the waiting session


def _hex8(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _bead(
    bead_id: str,
    title: str,
    agent: str,
    status: str,
    now: datetime,
    *,
    root: str | None,
    parent: str | None = None,
    deps: list[str] | None = None,
    age_min: int = 60,
    block_reason: str = "",
    bead_type: str = "task",
) -> dict[str, Any]:
    created = (now - timedelta(minutes=age_min)).isoformat()
    history = [
        {"timestamp": created, "event": "created", "agent_type": "scheduler",
         "summary": "Bead created", "details": {}},
    ]
    if status in ("in_progress", "done", "blocked", "handed_off"):
        history.append({
            "timestamp": (now - timedelta(minutes=age_min - 5)).isoformat(),
            "event": "started", "agent_type": agent,
            "summary": "Worker started", "details": {},
        })
    if status == "done":
        history.append({
            "timestamp": (now - timedelta(minutes=age_min - 15)).isoformat(),
            "event": "completed", "agent_type": agent,
            "summary": f"{title} completed", "details": {},
        })
    handoff = {
        "completed": f"{title} done" if status == "done" else "",
        "remaining": "", "risks": "",
        "verdict": "approved" if status == "done" else "",
        "findings_count": 0, "requires_followup": False,
        "changed_files": [], "updated_docs": [], "next_action": "",
        "next_agent": "", "block_reason": block_reason,
        "expected_files": [], "expected_globs": [], "touched_files": [],
        "conflict_risks": "", "design_decisions": "",
        "test_coverage_notes": "", "known_limitations": "",
        "assumptions": [], "criteria_status": [],
    }
    return {
        "bead_id": bead_id,
        "title": title,
        "agent_type": agent,
        "description": f"Synthetic bead: {title}",
        "status": status,
        "bead_type": bead_type,
        "parent_id": parent,
        "dependencies": deps or [],
        "acceptance_criteria": [f"{title} works"],
        "linked_docs": [],
        "feature_root_id": root,
        "execution_branch_name": f"feature/{root.lower()}" if root else "",
        "execution_worktree_path": "",
        "expected_files": [],
        "expected_globs": [],
        "touched_files": [],
        "changed_files": [],
        "updated_docs": [],
        "handoff_summary": handoff,
        "block_reason": block_reason,
        "conflict_risks": "",
        "branch_name": "",
        "worktree_path": "",
        "lease": None,
        "retries": 0,
        "execution_history": history,
        "metadata": {},
        "labels": [],
        "recovery_for": None,
        "priority": None,
    }


_OPTIONAL = (
    "bead_type", "parent_id", "linked_docs", "execution_branch_name",
    "execution_worktree_path", "expected_files", "expected_globs",
    "touched_files", "changed_files", "updated_docs", "conflict_risks",
    "branch_name", "worktree_path", "lease", "retries", "metadata", "labels",
    "recovery_for", "priority", "handoff_summary", "block_reason",
)


def _to_older(bead: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in bead.items() if k not in _OPTIONAL}


def _to_newer(bead: dict[str, Any]) -> dict[str, Any]:
    out = dict(bead)
    out["risk_score"] = 0.42
    out["reviewers"] = [{"name": "alice", "state": "pending"}]
    out["handoff_summary"] = {**bead["handoff_summary"], "confidence": "high"}
    return out


def _event(ts: datetime, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": ts.isoformat(), "event_type": event_type, "payload": payload}


def _run_events(
    run_id: str,
    start: datetime,
    *,
    pid: int,
    hostname: str,
    feature_root: str,
    finished: bool,
) -> list[dict[str, Any]]:
    def at(minutes: float) -> datetime:
        return start + timedelta(minutes=minutes)

    rid = {"run_id": run_id}
    events = [
        _event(start, "scheduler_run_started", {
            **rid, "pid": pid, "hostname": hostname, "takt_version": "0.9.0",
            "runner": "claude", "max_workers": 2, "max_cycles": 50,
            "feature_root_id": None,
        }),
        _event(at(0.1), "scheduler_cycle_started", {
            **rid, "cycle_index": 1, "max_workers": 2,
            "feature_root_id": None, "pid": pid,
        }),
        _event(at(4), "bead_completed", {
            **rid, "bead_id": f"{feature_root}-dev-a", "agent_type": "developer",
            "summary": "Implemented module", "created_bead_ids": [],
            "feature_root_id": feature_root, "verdict": "approved",
            "duration_seconds": 214.5, "cost_usd": 0.31, "attempt": 1,
        }),
        _event(at(5), "bead_blocked", {
            **rid, "bead_id": f"{feature_root}-dev-b", "agent_type": "developer",
            "summary": "Blocked on missing API", "feature_root_id": feature_root,
            "duration_seconds": 60.0, "cost_usd": 0.12, "attempt": 1,
        }),
        _event(at(5.5), "bead_deferred", {
            **rid, "bead_id": f"{feature_root}-test", "agent_type": "tester",
            "reason": "dependencies not done", "feature_root_id": feature_root,
        }),
    ]
    events.append(_event(at(6), "scheduler_cycle_completed", {
        **rid, "cycle_index": 1, "started_count": 2, "completed_count": 1,
        "blocked_count": 1, "deferred_count": 1, "pid": pid,
    }))
    if finished:
        events.append(_event(at(6.2), "scheduler_run_finished", {
            **rid, "pid": pid, "exit_reason": "quiescence", "error": None,
            "cycles": 1, "started_count": 2, "completed_count": 1,
            "blocked_count": 1, "correctives_created_count": 0,
            "deferred_count": 1,
            "final_state": {"done": 1, "blocked": 1, "open": 2, "ready": 1},
            "duration_seconds": 372.0, "cost_usd": 0.43,
        }))
    else:
        events.append(_event(at(6.3), "scheduler_cycle_started", {
            **rid, "cycle_index": 2, "max_workers": 2,
            "feature_root_id": None, "pid": pid,
        }))
    return events


VOLUME_FEATURES = 12
VOLUME_TITLES = (
    "pagination", "search filters", "export", "notifications", "audit trail",
    "rate limiting", "caching", "webhooks", "dark mode", "bulk edit",
    "onboarding", "billing",
)


def _volume_beads(name: str, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Twelve feature trees (root, dev, test, docs, review) for paging volume.

    Returns ``(beads, events)``.  Events are spread over two extra finished
    runs, include ``cost_usd``, and feature 3 has a blocked review that spawns
    a corrective bead.  Trees 10 and 11 are still in flight.
    """
    beads: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    hostname = socket.gethostname()
    runs = [f"SR-{_hex8(name + '-vol-a')}", f"SR-{_hex8(name + '-vol-b')}"]
    run_start = [now - timedelta(hours=9), now - timedelta(hours=5)]
    for r, start in zip(runs, run_start):
        events.append(_event(start, "scheduler_run_started", {
            "run_id": r, "pid": 5000, "hostname": hostname,
            "takt_version": "0.9.0", "runner": "claude", "max_workers": 3,
            "max_cycles": 50, "feature_root_id": None}))
    cycle_counts = [0, 0]
    for n in range(VOLUME_FEATURES):
        title = VOLUME_TITLES[n % len(VOLUME_TITLES)]
        root = f"B-{_hex8(f'{name}-vol-{n}')}"
        ri = 0 if n < 6 else 1
        rid = {"run_id": runs[ri]}
        base = run_start[ri] + timedelta(minutes=(n % 6) * 40)
        inflight = n >= 10
        statuses = {
            "root": "in_progress" if inflight else "done",
            "dev": "done", "test": "in_progress" if inflight else "done",
            "docs": "ready" if inflight else "done",
            "review": "open" if inflight else "done",
        }
        ids = {k: (root if k == "root" else f"{root}-{k}") for k in statuses}
        beads.append(_bead(root, f"Feature: {title}", "planner", statuses["root"],
                           now, root=root, age_min=700 - n * 20))
        agents = {"dev": "developer", "test": "tester", "docs": "documentation",
                  "review": "review"}
        deps = {"dev": [], "test": [ids["dev"]], "docs": [ids["dev"]],
                "review": [ids["dev"], ids["test"], ids["docs"]]}
        for k in ("dev", "test", "docs", "review"):
            beads.append(_bead(ids[k], f"{title.title()} {k}", agents[k],
                               statuses[k], now, root=root, parent=root,
                               deps=deps[k], age_min=690 - n * 20 - 10))
        # events: a cycle per feature
        ci = cycle_counts[ri] = cycle_counts[ri] + 1
        events.append(_event(base, "scheduler_cycle_started", {
            **rid, "cycle_index": ci, "max_workers": 3, "feature_root_id": None,
            "pid": 5000}))
        done_n = 0
        blocked_n = 0
        for j, k in enumerate(("dev", "test", "docs", "review")):
            if inflight and k != "dev":
                continue
            t0 = base + timedelta(minutes=j * 6 + 1)
            events.append(_event(t0, "bead_started", {
                **rid, "bead_id": ids[k], "agent_type": agents[k],
                "title": f"{title.title()} {k}", "feature_root_id": root}))
            block = n == 3 and k == "review"
            cost = round(0.08 + 0.05 * ((n + j) % 7), 2)
            payload = {**rid, "bead_id": ids[k], "agent_type": agents[k],
                       "feature_root_id": root,
                       "duration_seconds": float(60 + 25 * ((n + j) % 6)),
                       "cost_usd": cost, "attempt": 1}
            if block:
                blocked_n += 1
                events.append(_event(t0 + timedelta(minutes=4), "bead_blocked", {
                    **payload, "summary": "Approved with unmet acceptance criteria"}))
                corr = f"{ids[k]}-corrective"
                cb = _bead(corr, "Fix unmet criterion", "developer", "done", now,
                           root=root, parent=root, deps=[ids[k]],
                           age_min=300 - n * 5)
                cb["metadata"] = {"auto_corrective_for": ids[k]}
                cb["labels"] = ["corrective"]
                beads.append(cb)
                tc = t0 + timedelta(minutes=5)
                events.append(_event(tc, "bead_started", {
                    **rid, "bead_id": corr, "agent_type": "developer",
                    "title": "Fix unmet criterion", "feature_root_id": root}))
                events.append(_event(tc + timedelta(minutes=3), "bead_completed", {
                    **rid, "bead_id": corr, "agent_type": "developer",
                    "summary": "Resolved unmet criterion",
                    "created_bead_ids": [], "feature_root_id": root,
                    "verdict": "approved", "duration_seconds": 140.0,
                    "cost_usd": 0.19, "attempt": 1}))
                done_n += 1
            else:
                done_n += 1
                events.append(_event(t0 + timedelta(minutes=4), "bead_completed", {
                    **payload, "summary": f"{title.title()} {k} finished",
                    "created_bead_ids": [], "verdict": "approved"}))
        events.append(_event(base + timedelta(minutes=30), "scheduler_cycle_completed", {
            **rid, "cycle_index": ci, "started_count": done_n,
            "completed_count": done_n - blocked_n, "blocked_count": blocked_n,
            "deferred_count": 0, "pid": 5000}))
    for ri, r in enumerate(runs):
        events.append(_event(run_start[ri] + timedelta(minutes=250), "scheduler_run_finished", {
            "run_id": r, "pid": 5000, "exit_reason": "quiescence", "error": None,
            "cycles": cycle_counts[ri], "started_count": 20,
            "completed_count": 19, "blocked_count": 1,
            "correctives_created_count": 1 if ri == 0 else 0,
            "deferred_count": 0,
            "final_state": {"done": 20, "blocked": 0, "open": 0},
            "duration_seconds": 15000.0, "cost_usd": 3.4}))
    return beads, events


def _criteria(statuses: list[tuple[str, str, str]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (acceptance_criteria, criteria_status) for (text, status, evidence)."""
    texts = [t for t, _, _ in statuses]
    status = [
        {"index": i, "criterion": t, "status": st, "evidence": ev}
        for i, (t, st, ev) in enumerate(statuses)
    ]
    return texts, status


def _governance_beads(name: str, now: datetime) -> list[dict[str, Any]]:
    """A second feature tree exercising the governance data in the bead contract.

    Contains developer/tester/docs/review beads, a corrective child
    (``metadata.auto_corrective_for``), a recovery bead (``recovery_for``), a
    discovered-issue bead, structural and local assumptions, met/unmet/descoped
    criteria, ``telemetry_history``, ``merge_records`` on the root and one bead
    with unknown top-level and nested fields.  No bead is ``blocked`` so the
    status counts of the P1 fixture beads are unaffected.
    """
    g = f"B-{_hex8(name + '-gov')}"
    merged_at = (now - timedelta(minutes=30)).isoformat()

    root = _bead(g, "Feature: governance", "planner", "done", now,
                 root=g, age_min=300)
    root["metadata"] = {"merge_records": [{
        "merged_at": merged_at,
        "approver": "Demo Approver <demo@example.com>",
        "approver_source": "git",
        "branch": f"feature/{g.lower()}",
        "feature_head_sha": _hex8(g + "head") * 5,
        "main_sha_before": _hex8(g + "before") * 5,
        "main_sha_after": _hex8(g + "after") * 5,
        "preflight": {"status": "clean"},
        "test_gate": {"status": "passed", "command": "pytest -q"},
        "audit_path": f".takt/audits/{g}/20260101T000000Z.json",
        "audit_sha256": None,
    }]}

    dev = _bead(f"{g}-dev", "Implement governance", "developer", "done", now,
                root=g, parent=g, age_min=290)
    crit, status = _criteria([
        ("Module implemented", "met", "src/mod.py"),
        ("Edge cases handled", "unmet", "not reported by agent"),
        ("Docs updated", "descoped", "owned by docs bead"),
    ])
    dev["acceptance_criteria"] = crit
    dev["handoff_summary"].update({
        "criteria_status": status,
        "assumptions": [
            {"statement": "Config key is optional", "rationale": "spec silent",
             "scope": "structural"},
            {"statement": "Sort order is by id", "rationale": "not specified",
             "scope": "local"},
        ],
    })
    dev["metadata"] = {"telemetry_history": [
        {"attempt": 1, "duration_seconds": 120.5, "cost_usd": 0.21,
         "input_tokens": 12000, "output_tokens": 3400},
        {"attempt": 2, "duration_seconds": 98.0, "cost_usd": 0.17,
         "input_tokens": 11000, "output_tokens": 2900},
    ]}
    dev["labels"] = ["governance"]

    tester = _bead(f"{g}-test", "Test governance", "tester", "done", now,
                   root=g, parent=g, deps=[f"{g}-dev"], age_min=280)
    tester["acceptance_criteria"], tester["handoff_summary"]["criteria_status"] = _criteria([
        ("Scenario: gov / display / shows criteria — covered", "met",
         "tests/test_gov.py::test_shows_criteria"),
    ])
    tester["metadata"] = {"telemetry_history": [
        {"attempt": 1, "duration_seconds": 60.0, "cost_usd": 0.09}]}

    docs = _bead(f"{g}-docs", "Document governance", "documentation", "done", now,
                 root=g, parent=g, deps=[f"{g}-dev"], age_min=280)
    review = _bead(f"{g}-review", "Review governance", "review", "done", now,
                   root=g, parent=g,
                   deps=[f"{g}-dev", f"{g}-test", f"{g}-docs"], age_min=270)
    review["acceptance_criteria"], review["handoff_summary"]["criteria_status"] = _criteria([
        ("Reviewed all structural assumptions", "met", "see risks"),
    ])

    corrective = _bead(f"{g}-review-corrective", "Fix unmet criterion",
                       "developer", "done", now, root=g, parent=g,
                       deps=[f"{g}-review"], age_min=260)
    corrective["metadata"] = {
        "auto_corrective_for": f"{g}-review",
        "unmet_dependency_criteria": [
            {"bead_id": f"{g}-dev", "index": 1, "criterion": "Edge cases handled"}],
    }
    corrective["acceptance_criteria"], corrective["handoff_summary"]["criteria_status"] = _criteria([
        (f"Resolve unmet criterion #1 from {g}-dev: Edge cases handled", "met",
         "src/mod.py handles empty input"),
    ])
    corrective["labels"] = ["corrective"]

    recovered = _bead(f"{g}-dev2", "Recovered developer work", "developer",
                      "done", now, root=g, parent=g, age_min=250)
    recovery = _bead(f"{g}-dev2-recovery", "Recover Recovered developer work",
                     "recovery", "done", now, root=g, parent=f"{g}-dev2",
                     age_min=240, bead_type="recovery")
    recovery["recovery_for"] = f"{g}-dev2"

    discovered = _bead(f"B-{_hex8(name + '-disc')}", "Discovered: flaky helper",
                       "developer", "open", now, root=None, age_min=230)
    discovered["feature_root_id"] = discovered["bead_id"]
    discovered["execution_branch_name"] = f"feature/{discovered['bead_id'].lower()}"
    discovered["labels"] = ["discovered"]
    discovered["metadata"] = {"discovered_issue_of": f"{g}-dev",
                              "discovered_in_feature": g}

    extra = _bead(f"{g}-extra", "Bead with unknown fields", "developer", "done",
                  now, root=g, parent=g, age_min=220)
    extra["future_field"] = {"nested": {"deep": [1, 2, {"k": "v"}]}, "flag": True}
    extra["handoff_summary"]["_extra"] = {"confidence": "high", "tags": ["a"]}
    extra["handoff_summary"]["future_nested"] = {"x": {"y": 1}}
    extra["metadata"] = {"future_meta": {"a": [1, 2]}, "transient_retry_count": 1}
    # Unresolved unmet criterion in a merged tree (no corrective resolves it).
    extra["acceptance_criteria"], extra["handoff_summary"]["criteria_status"] = _criteria([
        ("Retry path covered", "unmet", "not reported by agent"),
    ])

    return [root, dev, tester, docs, review, corrective, recovered, recovery,
            discovered, extra]


COST_HISTORY_DAYS = 45


def _cost_history_events(name: str, index: int, root: str, now: datetime) -> list[dict[str, Any]]:
    """Terminal bead events with ``cost_usd`` spread over ``COST_HISTORY_DAYS`` days.

    Roughly one event per day; every fifth has a null cost.  Events carry no
    ``run_id`` so they do not create phantom scheduler runs.
    """
    events: list[dict[str, Any]] = []
    agents = ("developer", "tester", "documentation", "review")
    for d in range(1, COST_HISTORY_DAYS + 1):
        for k in range(1 + (d + index) % 2):
            ts = now - timedelta(days=d, hours=k * 3 + (index % 5))
            kind = ("bead_failed" if (d + k + index) % 11 == 0
                    else "bead_blocked" if (d + k + index) % 4 == 0
                    else "bead_completed")
            agent = agents[(d + k + index) % len(agents)]
            payload: dict[str, Any] = {
                "bead_id": f"{root}-hist-{d}-{k}", "agent_type": agent,
                "feature_root_id": root,
                "duration_seconds": float(30 + 15 * ((d + k) % 8)),
                "cost_usd": (None if (d * 2 + k + index) % 5 == 0
                             else round(0.05 + 0.04 * ((d + k + index) % 9), 2)),
                "attempt": 1,
            }
            if kind == "bead_failed":
                payload["error"] = "Agent timed out"
            elif kind == "bead_blocked":
                payload["summary"] = "Blocked on demo condition"
            else:
                payload.update(summary=f"{name} history work", created_bead_ids=[],
                               verdict="approved")
            events.append(_event(ts, kind, payload))
    return events


LARGE_PROMPT_BYTES = 300 * 1024  # > 256 KiB
AUDIT_TIMESTAMPS = ("2026-01-01T00-00-00.000000Z", "2026-01-02T00-00-00.000000Z")


def _telemetry_attempt(
    bead_id: str, agent: str, attempt: int, now: datetime, *,
    prompt_text: str, outcome: str = "completed", age_min: int = 280,
) -> dict[str, Any]:
    start = now - timedelta(minutes=age_min - attempt)
    return {
        "telemetry_version": 1,
        "bead_id": bead_id,
        "agent_type": agent,
        "attempt": attempt,
        "started_at": start.isoformat(),
        "finished_at": (start + timedelta(minutes=2)).isoformat(),
        "outcome": outcome,
        "prompt_text": prompt_text,
        "response_text": '{"outcome": "completed", "summary": "done"}',
        "parsed_result": {"outcome": "completed", "summary": "done"},
        "metrics": {"cost_usd": 0.2, "duration_seconds": 120.0,
                    "input_tokens": 12000, "output_tokens": 3400,
                    "model": "demo-model"},
        "error": None,
        "trace_refs": [attempt] if attempt <= 2 else [],
    }


def _trace_events(now: datetime) -> list[dict[str, Any]]:
    """One of each event kind, including a failed tool call."""
    def ts(sec: int) -> str:
        return (now - timedelta(minutes=280) + timedelta(seconds=sec)).isoformat()

    return [
        {"kind": "session", "session_id": "demo-session-1", "cwd": "/demo/work",
         "model": "demo-model", "started_at": ts(0)},
        {"kind": "assistant_text", "ts": ts(1),
         "text_excerpt": "Reading the module first.", "text_chars": 25},
        {"kind": "tool_call", "ts": ts(2), "call_id": "toolu_1", "tool": "Read",
         "input": {"file_path": "src/mod.py"}, "result_ts": ts(3),
         "duration_ms": 120, "is_error": False,
         "result_excerpt": "def main(): ...", "result_chars": 15},
        {"kind": "tool_call", "ts": ts(4), "call_id": "toolu_2", "tool": "Bash",
         "input": {"command": "false"}, "result_ts": ts(5),
         "duration_ms": 900, "is_error": True,
         "result_excerpt": "exit status 1", "result_chars": 13},
        {"kind": "usage", "ts": ts(6), "message_id": "msg_1",
         "input_tokens": 1200, "output_tokens": 340,
         "cache_read_tokens": 800, "cache_creation_tokens": 100},
    ]


def _write_governance_evidence(takt: Path, g: str, now: datetime) -> None:
    """Telemetry attempts, trace records and audit snapshots for the
    governance feature tree (see ``_governance_beads``)."""
    dev, tester = f"{g}-dev", f"{g}-test"

    # Telemetry: attempt 1 carries a prompt above 256 KiB.
    big = ("Implement the governance module.\n"
           + "context line for the large prompt fixture\n"
           * (LARGE_PROMPT_BYTES // 40 + 1))
    tel = takt / "telemetry"
    _write_json(tel / dev / "1.json", _telemetry_attempt(
        dev, "developer", 1, now, prompt_text=big))
    _write_json(tel / dev / "2.json", _telemetry_attempt(
        dev, "developer", 2, now, prompt_text="Short retry prompt."))
    _write_json(tel / tester / "1.json", _telemetry_attempt(
        tester, "tester", 1, now, prompt_text="Test the governance module.",
        age_min=270))

    # Traces: seq 1 captured with events; seq 2 missing, meta only.
    traces = takt / "traces" / dev
    base = {
        "trace_version": 1, "bead_id": dev, "agent_type": "developer",
        "feature_root_id": g, "spec_id": None, "backend": "claude",
        "role": "main", "outcome": "completed", "malformed_lines": 0,
    }
    events = _trace_events(now)
    raw = "".join(json.dumps({"type": "demo", "i": i}) + "\n"
                  for i in range(len(events)))
    _write_json(traces / "1.meta.json", {
        **base, "seq": 1, "session_id": "demo-session-1", "status": "captured",
        "started_at": events[0]["started_at"], "finished_at": events[-1]["ts"],
        "source_path": "/demo/claude/projects/demo-session-1.jsonl",
        "raw_bytes": len(raw.encode("utf-8")), "tool_calls": 2,
        "telemetry_attempt": 1, "error": None,
    })
    (traces / "1.raw.jsonl").write_text(raw, encoding="utf-8")
    (traces / "1.events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    _write_json(traces / "2.meta.json", {
        **base, "seq": 2, "session_id": None, "status": "missing",
        "outcome": None, "started_at": None, "finished_at": None,
        "source_path": None, "raw_bytes": None, "tool_calls": None,
        "telemetry_attempt": 2, "error": "transcript not found",
    })

    # Audits: two snapshots; the later one has nested unknown keys.
    def audit(generated: datetime, extra: bool) -> dict[str, Any]:
        report: dict[str, Any] = {
            "audit_schema_version": 1,
            "feature": {
                "bead_id": g, "title": "Feature: governance",
                "linked_docs": [], "branch": f"feature/{g.lower()}",
                "worktree_path": f".takt/worktrees/{g}",
                "generated_at": generated.isoformat(), "takt_version": "0.9.0",
            },
            "status": {"counts": {"done": 9, "open": 1}, "not_done": []},
            "beads": [{
                "bead_id": dev, "agent_type": "developer", "bead_type": "task",
                "status": "done", "verdict": "approved", "findings_count": 0,
                "corrective_children": [f"{g}-review-corrective"],
                "recovery_children": [], "wall_clock_seconds": 218.5,
                "cost_usd": 0.38, "trace": {"traces": 2, "tool_calls": 2},
            }],
            "gates": [{"bead_id": f"{g}-review", "agent_type": "review",
                       "verdict": "approved", "history": ["approved"]}],
            "assumptions": {"items": [{"statement": "Config key is optional",
                                       "rationale": "spec silent",
                                       "scope": "structural"}],
                            "counts": {"structural": 1, "local": 1}},
            "criteria": {"met": 3, "unmet": 1, "descoped": 1, "gaps": []},
            "changed_files": ["src/mod.py"],
            "cost": {"total_cost_usd": 0.47, "total_wall_clock_seconds": 400.0,
                     "beads_with_telemetry": 2},
            "trace": {"traces": 2, "tool_calls": 2, "tool_errors": 1,
                      "documents": 0},
            "merges": [],
        }
        if extra:
            report["future_section"] = {
                "level1": {"level2": [{"k": "v", "deep": {"n": 1}}], "flag": True}}
            report["cost"]["forecast"] = {"model": {"p50": 0.5, "p95": 0.9}}
            report["feature"]["labels"] = {"team": {"name": "demo"}}
        return report

    audits = takt / "audits" / g
    for i, stamp in enumerate(AUDIT_TIMESTAMPS):
        generated = now - timedelta(days=2 - i)
        _write_json(audits / f"{stamp}.json", audit(generated, extra=i == 1))
        (audits / f"{stamp}.md").write_text(
            f"# Feature audit: Feature: governance\n\nGenerated "
            f"{generated.isoformat()}\n", encoding="utf-8")


SESSION_KINDS = ("growing", "waiting", "ended")


def _session_id(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"takt-fleet-demo/{seed}"))


def _transcript_line(
    session_id: str, ts: datetime, kind: str, **kw: Any
) -> dict[str, Any]:
    """One Claude Code transcript line: assistant text, tool call or tool result."""
    base = {"sessionId": session_id, "timestamp": ts.isoformat()}
    if kind == "text":
        return {**base, "type": "assistant", "message": {
            "role": "assistant", "model": "demo-model",
            "content": [{"type": "text", "text": kw["text"]}]}}
    if kind == "tool_call":
        return {**base, "type": "assistant", "message": {
            "role": "assistant", "model": "demo-model",
            "content": [{"type": "tool_use", "id": kw["call_id"],
                         "name": kw["tool"], "input": kw["input"]}]}}
    return {**base, "type": "user", "message": {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": kw["call_id"],
                     "content": kw["text"], "is_error": False}]}}


def append_transcript_line(
    config_dir: Path, project_slug: str, session_id: str, ts: datetime,
    kind: str, **kw: Any,
) -> Path:
    """Append one transcript line under ``<config_dir>/projects/<slug>/``."""
    path = Path(config_dir) / "projects" / project_slug / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _transcript_line(session_id, ts, kind, **kw)
    with path.open("ab") as fh:
        fh.write((json.dumps(line) + "\n").encode("utf-8"))
    return path


def _write_sessions(
    path: Path, name: str, root: str, config_dir: Path, now: datetime
) -> tuple[list[dict[str, Any]], list[FixtureSession]]:
    """Session events, transcripts and a captured trace for one project.

    Returns ``(events, sessions)``.  Everything lives under ``path`` (the
    fixture project) and ``config_dir`` (the fake Claude config dir).
    """
    slug = "-demo-" + name
    hostname_payload = {"agent_type": "developer", "feature_root_id": root,
                        "backend": "claude", "role": "main"}
    events: list[dict[str, Any]] = []
    sessions: list[FixtureSession] = []

    def start(kind: str, bead_id: str, agent: str, minutes_ago: float) -> tuple[str, datetime]:
        sid = _session_id(f"{name}-{kind}")
        t0 = now - timedelta(minutes=minutes_ago)
        events.append(_event(t0, "agent_session_started", {
            **hostname_payload, "agent_type": agent, "bead_id": bead_id,
            "session_id": sid, "started_at": t0.isoformat()}))
        return sid, t0

    # Growing: transcript exists and the demo driver keeps appending to it.
    bead = f"{root}-docs"
    sid, t0 = start("growing", bead, "documentation", 4)
    tp = append_transcript_line(config_dir, slug, sid, t0 + timedelta(seconds=5),
                                "text", text="Reading the docs before editing.")
    sessions.append(FixtureSession("growing", name, bead, sid, tp))

    # Waiting: started, no transcript file yet.
    bead = f"{root}-test"
    sid, _ = start("waiting", bead, "tester", 1)
    sessions.append(FixtureSession("waiting", name, bead, sid, None))

    # Ended: transcript plus trace_captured and a captured trace record.
    bead = f"{root}-dev-a"
    sid, t0 = start("ended", bead, "developer", 30)
    tp = append_transcript_line(config_dir, slug, sid, t0 + timedelta(seconds=5),
                                "text", text="Implementing the module.")
    append_transcript_line(config_dir, slug, sid, t0 + timedelta(seconds=8),
                           "tool_call", call_id="toolu_end", tool="Read",
                           input={"file_path": "src/mod.py"})
    append_transcript_line(config_dir, slug, sid, t0 + timedelta(seconds=9),
                           "tool_result", call_id="toolu_end", text="def main(): ...")
    t_end = t0 + timedelta(minutes=10)
    events.append(_event(t_end, "trace_captured", {
        "bead_id": bead, "agent_type": "developer", "feature_root_id": root,
        "seq": 1, "session_id": sid, "status": "captured", "role": "main"}))
    traces = path / ".takt" / "traces" / bead
    raw = tp.read_text(encoding="utf-8")
    _write_json(traces / "1.meta.json", {
        "trace_version": 1, "bead_id": bead, "agent_type": "developer",
        "feature_root_id": root, "spec_id": None, "backend": "claude",
        "role": "main", "outcome": "completed", "malformed_lines": 0,
        "seq": 1, "session_id": sid, "status": "captured",
        "started_at": t0.isoformat(), "finished_at": t_end.isoformat(),
        "source_path": str(tp), "raw_bytes": len(raw.encode("utf-8")),
        "tool_calls": 1, "telemetry_attempt": 1, "error": None})
    (traces / "1.raw.jsonl").write_text(raw, encoding="utf-8")
    (traces / "1.events.jsonl").write_text(json.dumps({
        "kind": "session", "session_id": sid, "cwd": "/demo/work",
        "model": "demo-model", "started_at": t0.isoformat()}) + "\n",
        encoding="utf-8")
    sessions.append(FixtureSession("ended", name, bead, sid, tp))
    return events, sessions


def _build_project(
    path: Path, name: str, variant: str, index: int, now: datetime,
    config_dir: Path | None = None,
    session_sink: list[FixtureSession] | None = None,
) -> FixtureProject:
    takt = path / ".takt"
    beads_dir = takt / "beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    (takt / "logs").mkdir(parents=True, exist_ok=True)

    (takt / "config.yaml").write_text(
        yaml.safe_dump({"common": {"default_runner": "claude",
                                   "test_command": "pytest -q",
                                   "commit_bead_state": False}},
                       sort_keys=False),
        encoding="utf-8",
    )

    root = f"B-{_hex8(name)}"
    standalone = f"B-{_hex8(name + '-solo')}"
    beads = [
        _bead(root, "Feature: demo", "planner", "in_progress", now,
              root=root, age_min=120),
        _bead(f"{root}-dev-a", "Implement module", "developer", "done", now,
              root=root, parent=root, age_min=110),
        _bead(f"{root}-dev-b", "Wire API", "developer", "blocked", now,
              root=root, parent=root, age_min=100,
              block_reason="Blocked on missing API"),
        _bead(f"{root}-test", "Test feature", "tester", "open", now,
              root=root, parent=root, deps=[f"{root}-dev-a", f"{root}-dev-b"],
              age_min=90),
        _bead(f"{root}-docs", "Document feature", "documentation", "ready", now,
              root=root, parent=root, deps=[f"{root}-dev-a"], age_min=90),
        _bead(f"{root}-review", "Review feature", "review", "open", now,
              root=root, parent=root,
              deps=[f"{root}-test", f"{root}-docs"], age_min=90),
        _bead(standalone, "Standalone fix", "developer", "handed_off", now,
              root=standalone, age_min=200),
    ]
    beads += _governance_beads(name, now)
    # Unmerged tree (the main feature root): unmet criterion + structural assumption.
    dev_a = beads[1]
    dev_a["acceptance_criteria"], dev_a["handoff_summary"]["criteria_status"] = _criteria([
        ("Implement module works", "met", "src/module.py"),
        ("Error paths handled", "unmet", "not reported by agent"),
    ])
    dev_a["handoff_summary"]["assumptions"] = [
        {"statement": "API returns JSON", "rationale": "spec silent",
         "scope": "structural"}]
    volume_events: list[dict[str, Any]] = []
    if variant == "full":
        vb, volume_events = _volume_beads(name, now)
        beads += vb
    for bead in beads:
        if variant == "older":
            bead = _to_older(bead)
        elif variant == "newer":
            bead = _to_newer(bead)
        _write_json(beads_dir / f"{bead['bead_id']}.json", bead)
    _write_governance_evidence(takt, f"B-{_hex8(name + '-gov')}", now)
    if variant == "corrupt":
        (beads_dir / f"B-{_hex8(name + '-bad')}.json").write_text(
            '{"bead_id": "B-broken", "title": ', encoding="utf-8"
        )

    hostname = socket.gethostname()
    old_run = f"SR-{_hex8(name + '-run1')}"
    events = _run_events(old_run, now - timedelta(hours=2), pid=4000 + index,
                         hostname=hostname, feature_root=root, finished=True)
    active_run: str | None = None
    if variant == "full":
        active_run = f"SR-{_hex8(name + '-run2')}"
        start = now - timedelta(minutes=10)
        pid = os.getpid()
        events += _run_events(active_run, start, pid=pid, hostname=hostname,
                              feature_root=root, finished=False)
        _write_json(takt / "runs" / "active" / f"{active_run}.json", {
            "run_id": active_run, "pid": pid, "hostname": hostname,
            "started_at": start.isoformat(), "runner": "claude",
            "max_workers": 2, "feature_root_id": None, "cycle_index": 2,
            "updated_at": (now - timedelta(minutes=1)).isoformat(),
        })
    events += volume_events
    events += _cost_history_events(name, index, root, now)
    if variant == "full" and config_dir is not None:
        session_events, fixture_sessions = _write_sessions(
            path, name, root, config_dir, now)
        events += session_events
        if session_sink is not None:
            session_sink.extend(fixture_sessions)
    events.sort(key=lambda e: e["timestamp"])
    (takt / "logs" / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )

    if variant != "no-version":
        version = "0.3.0" if variant == "older" else (
            "99.0.0" if variant == "newer" else "0.9.0")
        _write_json(takt / "version.json", {
            "takt_version": version,
            "last_upgraded_at": (now - timedelta(days=3)).isoformat(),
        })

    return FixtureProject(
        name=name, path=path, tags=("demo", variant), variant=variant,
        feature_root_id=root, active_run_id=active_run,
        bead_ids=tuple(b["bead_id"] for b in beads),
    )


def build_fixture_fleet(
    root: Path, *, count: int = 5, clock: Clock = _utc_now
) -> FixtureFleet:
    """Write ``count`` synthetic projects and a registry file under ``root``.

    Projects live in ``<root>/projects/<name>``; the registry is written to
    ``<root>/fleet.yaml`` in the format ``agent_takt_fleet.registry`` reads
    (one extra entry points at a nonexistent path).
    """
    root = Path(root)
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    projects_dir = root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Fake Claude config dir inside the fixture root; never the real ~/.claude.
    claude_config_dir = root / "claude-config"
    (claude_config_dir / "projects").mkdir(parents=True, exist_ok=True)
    sessions: list[FixtureSession] = []

    projects: list[FixtureProject] = []
    for i in range(count):
        variant = VARIANTS[i % len(VARIANTS)]
        name = f"demo-{i + 1}-{variant}"
        projects.append(_build_project(
            projects_dir / name, name, variant, i, now,
            claude_config_dir, sessions))

    missing = FixtureProject(
        name=MISSING_PROJECT_NAME, path=projects_dir / "does-not-exist",
        tags=("demo", "missing"), variant="missing",
    )
    entries = [
        {"name": p.name, "path": str(p.path), "tags": list(p.tags)}
        for p in (*projects, missing)
    ]
    registry_path = root / "fleet.yaml"
    registry_path.write_text(
        yaml.safe_dump({"version": 1, "projects": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return FixtureFleet(root=root, registry_path=registry_path,
                        projects=tuple(projects), now=now, missing=(missing,),
                        claude_config_dir=claude_config_dir,
                        sessions=tuple(sessions))


# ---------------------------------------------------------------------------
# Mutation helpers (used by tests and demo mode to simulate live changes)
# ---------------------------------------------------------------------------

def _events_path(project_path: Path) -> Path:
    return Path(project_path) / ".takt" / "logs" / "events.jsonl"


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def append_event(
    project_path: Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Append one JSON event line to the project's events.jsonl; return it."""
    event = _event(_aware(clock()), event_type, dict(payload or {}))
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write((json.dumps(event) + "\n").encode("utf-8"))
    return event


def append_raw(project_path: Path, text: str | bytes) -> None:
    """Append raw text/bytes (no newline added) to events.jsonl.

    Use for partial-line or malformed-line scenarios.
    """
    data = text.encode("utf-8") if isinstance(text, str) else text
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as fh:
        fh.write(data)


def truncate_events(project_path: Path) -> None:
    """Truncate events.jsonl to zero length in place (simulates rotation)."""
    path = _events_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb"):
        pass


def set_bead_status(
    project_path: Path,
    bead_id: str,
    status: str,
    *,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Change a bead's status, rewriting its file atomically.

    Unknown fields are preserved.  A ``status_changed`` entry stamped with
    ``clock`` is appended to ``execution_history`` when that list exists.
    Returns the updated bead dict.
    """
    path = Path(project_path) / ".takt" / "beads" / f"{bead_id}.json"
    bead = json.loads(path.read_text(encoding="utf-8"))
    bead["status"] = status
    history = bead.get("execution_history")
    if isinstance(history, list):
        history.append({
            "timestamp": _aware(clock()).isoformat(),
            "event": "status_changed",
            "agent_type": "scheduler",
            "summary": f"Status set to {status}",
            "details": {},
        })
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(bead, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return bead


def rewrite_bead_content(
    project_path: Path,
    bead_id: str,
    note: str,
    *,
    clock: Clock = _utc_now,
) -> dict[str, Any]:
    """Change a bead's handoff summary and history without touching status.

    Rewrites ``handoff_summary.remaining`` (when a handoff dict exists) and
    appends a ``note_added`` entry to ``execution_history`` (when that list
    exists).  The file is replaced atomically via a temp file and
    ``os.replace``; unknown fields are preserved.  Returns the updated bead.
    """
    path = Path(project_path) / ".takt" / "beads" / f"{bead_id}.json"
    bead = json.loads(path.read_text(encoding="utf-8"))
    handoff = bead.get("handoff_summary")
    if isinstance(handoff, dict):
        handoff["remaining"] = note
    history = bead.get("execution_history")
    if isinstance(history, list):
        history.append({
            "timestamp": _aware(clock()).isoformat(),
            "event": "note_added",
            "agent_type": "scheduler",
            "summary": note,
            "details": {},
        })
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(bead, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return bead


def add_registry_project(
    registry_path: Path,
    project_path: Path,
    name: str,
    tags: list[str] | tuple[str, ...] = (),
) -> None:
    """Add a project entry to a fleet registry file (created if missing)."""
    registry_path = Path(registry_path)
    data: dict[str, Any] = {"version": 1, "projects": []}
    if registry_path.exists():
        loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    projects = data.setdefault("projects", [])
    projects.append(
        {"name": name, "path": str(project_path), "tags": list(tags)}
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_name(registry_path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, registry_path)


def remove_project_dir(project_path: Path) -> None:
    """Delete a project directory tree (simulates a removed project)."""
    import shutil

    shutil.rmtree(project_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Demo activity driver (``takt-fleet serve --demo``)
# ---------------------------------------------------------------------------

class DemoActivityDriver:
    """Background thread that keeps appending synthetic scheduler activity.

    Each round, on the first ``full`` demo project, it starts a bead, completes
    it, starts another and blocks it, then finishes the run (and opens a new
    one on the next round).  Only the mutation helpers above are used, so the
    driver writes inside the temporary demo directory exclusively.
    """

    def __init__(self, fleet: FixtureFleet, *, interval: float = 3.0) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._target = next((p for p in fleet.projects if p.variant == "full"), None)
        self._round = 0
        self._config_dir = fleet.claude_config_dir
        self._growing = next(
            (s for s in fleet.sessions if s.kind == "growing"), None)
        self._transcript_thread: threading.Thread | None = None
        self._transcript_count = 0

    def start(self) -> None:
        if self._target is None or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="demo-activity", daemon=True
        )
        self._thread.start()
        if self._growing is not None and self._config_dir is not None:
            self._transcript_thread = threading.Thread(
                target=self._run_transcript, name="demo-transcript", daemon=True
            )
            self._transcript_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._transcript_thread is not None:
            self._transcript_thread.join(timeout=5)
            self._transcript_thread = None

    def append_transcript_step(self) -> None:
        """Append the next transcript line to the growing session.

        Cycles assistant text -> tool call -> tool result.  Writes only under
        the fixture's fake Claude config dir.
        """
        g = self._growing
        assert g is not None and self._config_dir is not None
        n = self._transcript_count
        self._transcript_count += 1
        call_id = f"toolu_live_{n // 3}"
        step = n % 3
        slug = g.transcript_path.parent.name if g.transcript_path else "-demo"
        now = _utc_now()
        if step == 0:
            append_transcript_line(self._config_dir, slug, g.session_id, now,
                                   "text", text=f"Working on step {n // 3 + 1}.")
        elif step == 1:
            append_transcript_line(self._config_dir, slug, g.session_id, now,
                                   "tool_call", call_id=call_id, tool="Bash",
                                   input={"command": f"echo step {n // 3 + 1}"})
        else:
            append_transcript_line(self._config_dir, slug, g.session_id, now,
                                   "tool_result", call_id=call_id,
                                   text=f"step {n // 3 + 1}")

    def _run_transcript(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.append_transcript_step()
            except Exception:  # never crash the demo thread
                pass

    def _steps(self) -> list[Callable[[], None]]:
        p = self._target
        assert p is not None
        root = p.feature_root_id
        self._round += 1
        # Round 1 continues the fixture's running run so live events carry its
        # run_id; later rounds open fresh runs.
        continuing = self._round == 1 and p.active_run_id is not None
        run_id = (p.active_run_id if continuing
                  else f"SR-{_hex8(p.name + f'-demo-{self._round}')}")
        cycle = 3 if continuing else 1
        pid = os.getpid()
        docs, test = f"{root}-docs", f"{root}-test"
        rid = {"run_id": run_id}

        def cycle_started() -> None:
            append_event(p.path, "scheduler_cycle_started", {
                **rid, "cycle_index": cycle, "max_workers": 2,
                "feature_root_id": None, "pid": pid})

        def cycle_completed() -> None:
            append_event(p.path, "scheduler_cycle_completed", {
                **rid, "cycle_index": cycle, "started_count": 2,
                "completed_count": 1, "blocked_count": 1,
                "deferred_count": 0, "pid": pid})

        def run_started() -> None:
            if continuing:
                return
            append_event(p.path, "scheduler_run_started", {
                **rid, "pid": pid, "hostname": socket.gethostname(),
                "takt_version": "0.9.0", "runner": "claude", "max_workers": 2,
                "max_cycles": 50, "feature_root_id": None})

        def started(bead_id: str, agent: str) -> Callable[[], None]:
            def step() -> None:
                set_bead_status(p.path, bead_id, "in_progress")
                append_event(p.path, "bead_started", {
                    **rid, "bead_id": bead_id, "agent_type": agent,
                    "title": f"Demo {agent} work", "feature_root_id": root})
            return step

        def completed() -> None:
            set_bead_status(p.path, docs, "done")
            append_event(p.path, "bead_completed", {
                **rid, "bead_id": docs, "agent_type": "documentation",
                "summary": "Demo work finished", "created_bead_ids": [],
                "feature_root_id": root, "verdict": "approved",
                "duration_seconds": 12.0, "cost_usd": 0.05, "attempt": 1})

        def blocked() -> None:
            set_bead_status(p.path, test, "blocked")
            append_event(p.path, "bead_blocked", {
                **rid, "bead_id": test, "agent_type": "tester",
                "summary": "Demo block: tests failing", "feature_root_id": root,
                "duration_seconds": 8.0, "cost_usd": 0.04, "attempt": 1})

        def annotate(bead_id: str) -> Callable[[], None]:
            def step() -> None:
                rewrite_bead_content(
                    p.path, bead_id,
                    f"Demo note, round {self._round}: still progressing")
            return step

        def finished() -> None:
            cycle_completed()
            append_event(p.path, "scheduler_run_finished", {
                **rid, "pid": pid, "exit_reason": "quiescence", "error": None,
                "cycles": 1, "started_count": 2, "completed_count": 1,
                "blocked_count": 1, "correctives_created_count": 0,
                "deferred_count": 0,
                "final_state": {"done": 2, "blocked": 2},
                "duration_seconds": 20.0, "cost_usd": 0.09})
            set_bead_status(p.path, docs, "ready")
            set_bead_status(p.path, test, "open")

        return [run_started, cycle_started, started(docs, "documentation"),
                annotate(docs), completed,
                started(test, "tester"), annotate(test), blocked,
                annotate(f"{root}-dev-a"), finished]

    def _run(self) -> None:
        while not self._stop.is_set():
            for step in self._steps():
                if self._stop.wait(self._interval):
                    return
                try:
                    step()
                except Exception:  # demo data may vanish; never crash the thread
                    pass
