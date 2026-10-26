"""UI-neutral feature audit report builders (no dependency on ``agent_takt.cli``)."""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any

from .bead_metrics import bead_cost_usd, bead_wall_clock_seconds
from .models import BEAD_DONE, Bead
from .storage import RepositoryStorage

AUDIT_SCHEMA_VERSION = 1


def _takt_version() -> str | None:
    try:
        return _pkg_version("agent-takt")
    except PackageNotFoundError:
        return None


def _feature_tree(storage: RepositoryStorage, feature_root_id: str) -> list[Bead]:
    beads = [
        bead
        for bead in storage.list_beads()
        if bead.bead_id == feature_root_id or storage.feature_root_id_for(bead) == feature_root_id
    ]
    return sorted(beads, key=storage._bead_sort_key)


def _section_feature(root: Bead, feature_root_id: str) -> dict[str, Any]:
    return {
        "bead_id": feature_root_id,
        "title": root.title,
        "linked_docs": list(root.linked_docs),
        "branch": f"feature/{feature_root_id.lower()}",
        "worktree_path": f".takt/worktrees/{feature_root_id}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "takt_version": _takt_version(),
    }


def _section_status(beads: list[Bead]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for bead in beads:
        counts[bead.status] = counts.get(bead.status, 0) + 1
    return {
        "counts": counts,
        "not_done": [
            {"bead_id": bead.bead_id, "title": bead.title}
            for bead in beads
            if bead.status != BEAD_DONE
        ],
    }


def _section_beads(beads: list[Bead]) -> list[dict[str, Any]]:
    corrective: dict[str, int] = {}
    recovery: dict[str, int] = {}
    for bead in beads:
        target = bead.metadata.get("auto_corrective_for")
        if target:
            corrective[target] = corrective.get(target, 0) + 1
        if bead.recovery_for:
            recovery[bead.recovery_for] = recovery.get(bead.recovery_for, 0) + 1
    return [
        {
            "bead_id": bead.bead_id,
            "agent_type": bead.agent_type,
            "bead_type": bead.bead_type,
            "status": bead.status,
            "verdict": bead.handoff_summary.verdict or None,
            "findings_count": bead.handoff_summary.findings_count,
            "corrective_children": corrective.get(bead.bead_id, 0),
            "recovery_children": recovery.get(bead.bead_id, 0),
            "wall_clock_seconds": bead_wall_clock_seconds(bead),
            "cost_usd": bead_cost_usd(bead),
        }
        for bead in beads
    ]


def _section_changed_files(beads: list[Bead]) -> list[str]:
    files: set[str] = set()
    for bead in beads:
        files.update(bead.changed_files)
    return sorted(files)


def _section_cost(beads: list[Bead]) -> dict[str, Any]:
    costs = [c for c in (bead_cost_usd(b) for b in beads) if c is not None]
    walls = [w for w in (bead_wall_clock_seconds(b) for b in beads) if w is not None]
    with_telemetry = sum(1 for b in beads if b.metadata and b.metadata.get("telemetry") is not None)
    return {
        "total_cost_usd": sum(costs) if costs else None,
        "total_wall_clock_seconds": sum(walls) if walls else None,
        "beads_with_telemetry": with_telemetry,
    }


def _section_merges(root: Bead) -> list[dict[str, Any]]:
    records = root.metadata.get("merge_records") if root.metadata else None
    return [dict(r) for r in records] if isinstance(records, list) else []


def _is_gate_bead(bead: Bead) -> bool:
    return bead.agent_type in ("tester", "review")


def _section_gates(beads: list[Bead]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for bead in beads:
        if not _is_gate_bead(bead):
            continue
        verdict = bead.handoff_summary.verdict or None
        # A needs_changes result blocks the bead, recorded as a "blocked" history event.
        blocked = sum(1 for r in bead.execution_history if r.event == "blocked")
        if verdict == "needs_changes" and blocked:
            blocked -= 1  # the final result is itself needs_changes, not a prior one
        gates.append(
            {
                "bead_id": bead.bead_id,
                "agent_type": bead.agent_type,
                "final_verdict": verdict,
                "prior_needs_changes": blocked,
                "verdict_overridden": [
                    {
                        "timestamp": r.timestamp,
                        "summary": r.summary,
                        "details": dict(r.details or {}),
                    }
                    for r in bead.execution_history
                    if r.event == "verdict_overridden"
                ],
            }
        )
    return gates


def _section_assumptions(beads: list[Bead]) -> dict[str, Any]:
    structural: list[dict[str, Any]] = []
    local: list[dict[str, Any]] = []
    for bead in beads:
        for entry in bead.handoff_summary.assumptions or []:
            if not isinstance(entry, dict):
                continue
            tagged = {**entry, "bead_id": bead.bead_id}
            (local if entry.get("scope") == "local" else structural).append(tagged)
    return {
        "items": structural + local,
        "counts": {"structural": len(structural), "local": len(local)},
    }


def _section_criteria(beads: list[Bead]) -> dict[str, Any] | None:
    totals = {"met": 0, "unmet": 0, "descoped": 0}
    gaps: list[dict[str, Any]] = []
    seen = False
    for bead in beads:
        entries = [e for e in (bead.handoff_summary.criteria_status or []) if isinstance(e, dict)]
        if not (bead.acceptance_criteria and entries):
            continue
        seen = True
        for entry in entries:
            status = entry.get("status")
            if status in totals:
                totals[status] += 1
            if status in ("unmet", "descoped"):
                gaps.append(
                    {
                        "bead_id": bead.bead_id,
                        "index": entry.get("index"),
                        "status": status,
                        "criterion": entry.get("criterion"),
                        "evidence": entry.get("evidence"),
                    }
                )
    if not seen:
        return None
    return {**totals, "gaps": gaps}


def build_feature_audit(storage: RepositoryStorage, feature_root_id: str) -> dict[str, Any]:
    """Build the audit report for a feature tree. Read-only: writes no beads or events."""
    root = storage.load_bead(feature_root_id)
    beads = _feature_tree(storage, feature_root_id)
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "feature": _section_feature(root, feature_root_id),
        "status": _section_status(beads),
        "beads": _section_beads(beads),
        "gates": _section_gates(beads),
        "assumptions": _section_assumptions(beads),
        "criteria": _section_criteria(beads),
        "changed_files": _section_changed_files(beads),
        "cost": _section_cost(beads),
        "merges": _section_merges(root),
    }


_NOT_RECORDED = "not recorded"
_WARN_GATE_STATUSES = ("skipped", "not_run", "not_configured")


def _fmt(value: Any) -> str:
    return _NOT_RECORDED if value is None or value == "" else str(value)


def _fmt_cost(value: Any) -> str:
    return _NOT_RECORDED if value is None else f"${value:.4f}"


def _fmt_seconds(value: Any) -> str:
    return _NOT_RECORDED if value is None else f"{value:.1f}s"


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows)
    return lines


def render_feature_audit_markdown(audit: dict) -> str:
    """Render the audit dict as markdown, with the nine sections in JSON order."""
    feature = audit.get("feature") or {}
    status = audit.get("status") or {}
    out: list[str] = [f"# Feature audit: {feature.get('bead_id')} \"{feature.get('title')}\"", ""]

    out += ["## Feature", ""]
    docs = feature.get("linked_docs") or []
    out += [
        f"- Bead: {_fmt(feature.get('bead_id'))}",
        f"- Title: {_fmt(feature.get('title'))}",
        f"- Linked docs: {', '.join(docs) if docs else _NOT_RECORDED}",
        f"- Branch: {_fmt(feature.get('branch'))}",
        f"- Worktree: {_fmt(feature.get('worktree_path'))}",
        f"- Generated at: {_fmt(feature.get('generated_at'))}",
        f"- takt version: {_fmt(feature.get('takt_version'))}",
        "",
    ]

    out += ["## Status", ""]
    counts = status.get("counts") or {}
    out += [f"- {name}: {n}" for name, n in sorted(counts.items())] or ["- No beads."]
    not_done = status.get("not_done") or []
    out += ["", "Not done:" if not_done else "All beads are done."]
    out += [f"- {b['bead_id']}: {b['title']}" for b in not_done]
    out.append("")

    out += ["## Beads", ""]
    beads = audit.get("beads") or []
    if beads:
        out += _md_table(
            ["Bead", "Agent", "Type", "Status", "Verdict", "Findings", "Corrective", "Recovery", "Wall clock", "Cost"],
            [
                [
                    b.get("bead_id"), b.get("agent_type"), b.get("bead_type"), b.get("status"),
                    _fmt(b.get("verdict")), b.get("findings_count"), b.get("corrective_children"),
                    b.get("recovery_children"), _fmt_seconds(b.get("wall_clock_seconds")),
                    _fmt_cost(b.get("cost_usd")),
                ]
                for b in beads
            ],
        )
    else:
        out.append("No beads.")
    out.append("")

    out += ["## Gates", ""]
    gates = audit.get("gates") or []
    if gates:
        for g in gates:
            out.append(
                f"- {g.get('bead_id')} ({g.get('agent_type')}): final verdict {_fmt(g.get('final_verdict'))}, "
                f"{g.get('prior_needs_changes', 0)} prior needs_changes"
            )
            for ov in g.get("verdict_overridden") or []:
                out.append(f"  - verdict overridden at {ov.get('timestamp')}: {ov.get('summary')}")
    else:
        out.append("No gate beads.")
    out.append("")

    out += ["## Assumptions", ""]
    assumptions = audit.get("assumptions") or {}
    items = assumptions.get("items") or []
    if items:
        ac = assumptions.get("counts") or {}
        out += [f"Structural: {ac.get('structural', 0)}, local: {ac.get('local', 0)}", ""]
        for a in items:
            out.append(f"- [{a.get('scope')}] {a.get('bead_id')}: {a.get('statement')} (rationale: {_fmt(a.get('rationale'))})")
    else:
        out.append("None recorded.")
    out.append("")

    out += ["## Criteria", ""]
    criteria = audit.get("criteria")
    if criteria is None:
        out.append("Not recorded.")
    else:
        out.append(f"Met: {criteria.get('met', 0)}, unmet: {criteria.get('unmet', 0)}, descoped: {criteria.get('descoped', 0)}")
        gaps = criteria.get("gaps") or []
        if gaps:
            out.append("")
            for gap in gaps:
                out.append(
                    f"- {gap.get('bead_id')} #{gap.get('index')} [{gap.get('status')}] "
                    f"{_fmt(gap.get('criterion'))} — {_fmt(gap.get('evidence'))}"
                )
    out.append("")

    out += ["## Changed files", ""]
    files = audit.get("changed_files") or []
    out += [f"- {f}" for f in files] or ["No changed files."]
    out.append("")

    out += ["## Cost", ""]
    cost = audit.get("cost") or {}
    out += [
        f"- Total cost: {_fmt_cost(cost.get('total_cost_usd'))}",
        f"- Total wall clock: {_fmt_seconds(cost.get('total_wall_clock_seconds'))}",
        f"- Beads with telemetry: {cost.get('beads_with_telemetry', 0)}",
        "",
    ]

    out += ["## Merges", ""]
    merges = audit.get("merges") or []
    if merges:
        for m in merges:
            gate = m.get("test_gate") or {}
            out += [
                f"- {_fmt(m.get('merged_at'))} by {_fmt(m.get('approver'))} ({_fmt(m.get('approver_source'))})",
                f"  - branch: {_fmt(m.get('branch'))}, feature head: {_fmt(m.get('feature_head_sha'))}",
                f"  - main before: {_fmt(m.get('main_sha_before'))}, after: {_fmt(m.get('main_sha_after'))}",
                f"  - preflight: {_fmt(m.get('preflight'))}, test gate: {_fmt(gate.get('status'))}",
                f"  - audit: {_fmt(m.get('audit_path'))} (sha256 {_fmt(m.get('audit_sha256'))})",
            ]
    else:
        out.append("No merge records.")
    out.append("")
    return "\n".join(out)


def build_merge_summary_lines(
    audit: dict, *, preflight: str, test_gate: dict, approver: str
) -> list[tuple[str, bool]]:
    """Condensed pre-merge summary as ``(line, highlight)`` pairs; UI-neutral."""
    feature = audit.get("feature") or {}
    lines: list[tuple[str, bool]] = [
        (f"Merge summary for {feature.get('bead_id')} \"{feature.get('title')}\"", False)
    ]

    counts = (audit.get("status") or {}).get("counts")
    if counts is None:
        lines.append(("  beads: not recorded", False))
    else:
        done = counts.get(BEAD_DONE, 0)
        other = sum(n for s, n in counts.items() if s != BEAD_DONE)
        lines.append((f"  beads: {done} done, {other} other", other > 0))

    gates = audit.get("gates")
    if gates is None:
        lines.append(("  gates: not recorded", False))
    elif not gates:
        lines.append(("  gates: none", False))
    else:
        parts = [f"{g.get('agent_type')} {_fmt(g.get('final_verdict'))}" for g in gates]
        prior = sum(g.get("prior_needs_changes", 0) or 0 for g in gates)
        suffix = f" ({prior} prior needs_changes)" if prior else ""
        lines.append((f"  gates: {', '.join(parts)}{suffix}", False))

    ac = (audit.get("assumptions") or {}).get("counts")
    if ac is None:
        lines.append(("  assumptions: not recorded", False))
    else:
        structural = ac.get("structural", 0)
        lines.append((f"  assumptions: {structural} structural, {ac.get('local', 0)} local", structural > 0))

    criteria = audit.get("criteria")
    if criteria is None:
        lines.append(("  criteria: not recorded", False))
    else:
        unmet = criteria.get("unmet", 0)
        lines.append(
            (f"  criteria: {criteria.get('met', 0)} met, {unmet} unmet, {criteria.get('descoped', 0)} descoped", unmet > 0)
        )

    gate_status = (test_gate or {}).get("status")
    if gate_status is None:
        gate_text = _NOT_RECORDED
    else:
        gate_text = str(gate_status)
        duration = test_gate.get("duration_seconds")
        if duration is not None:
            gate_text += f" ({duration:.1f}s)"
    warn = gate_status in _WARN_GATE_STATUSES or preflight in _WARN_GATE_STATUSES
    lines.append((f"  test gate: {gate_text}    preflight: {_fmt(preflight)}", warn))

    lines.append((f"  approver: {_fmt(approver)}", False))
    return lines
