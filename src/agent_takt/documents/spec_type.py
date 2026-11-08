"""Spec document type: the single ``## Requirements`` parser and delta validation.

Unified rules:

* Only the first ``## Requirements`` heading starts the section; a repeated one
  is treated as ordinary section content.
* The section ends at the next ``## `` heading outside a fenced code block.
* Headings inside fenced code blocks are ignored.
* Heading lines are ``rstrip()``ped before matching.
* A ``#### Scenario:`` with an empty name is a validation error; the
  scenario parser skips such scenarios (see :func:`find_empty_scenarios`).

Syntax checks (:func:`validate_delta_syntax`) and consistency checks against
``specs/current/`` (:func:`validate_delta_consistency`) are separate so callers
can skip consistency for specs that are already done.

The module also holds the ``SPEC_TYPE`` configuration, the ``specs/current/``
merge performed on transition to ``done`` and the spec validation hook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import SPEC_STYLE
from .types import Document, DocumentType, ValidationError

REQUIREMENTS_HEADING = "## Requirements"
SCENARIO_PREFIX = "#### Scenario:"
DEFAULT_CURRENT_DIR = "specs/current"

_REQ_HEADING_RE = re.compile(r"^### (ADDED|MODIFIED|REMOVED):\s+(.+?)\s+/\s+(.+)$")
_CAPABILITY_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHALL_MUST_RE = re.compile(r"\bSHALL\b|\bMUST\b")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CURRENT_REQ_RE = re.compile(r"^## Requirement:\s+(.+)$", re.MULTILINE)


@dataclass
class RequirementEntry:
    """One ``### OP: capability / requirement`` entry of a Requirements section."""

    heading: str
    body_lines: list[str] = field(default_factory=list)
    op: str | None = None
    capability: str = ""
    requirement: str = ""

    @property
    def well_formed(self) -> bool:
        return self.op is not None


def fenced_flags(lines: list[str]) -> list[bool]:
    """Per-line flags: True for lines inside a fenced code block (fences included)."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE_RE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                flags.append(True)
            else:
                flags.append(False)
        else:
            flags.append(True)
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and not m.group(2).strip()
            ):
                fence = None
    return flags


def extract_requirements_section(body: str) -> str | None:
    """Return the text of the ``## Requirements`` section, or None if absent."""
    lines = body.splitlines()
    fenced = fenced_flags(lines)
    start: int | None = None
    for i, line in enumerate(lines):
        if not fenced[i] and line.rstrip().strip() == REQUIREMENTS_HEADING:
            start = i + 1
            break
    if start is None:
        return None
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if not fenced[i] and stripped.startswith("## ") and stripped != REQUIREMENTS_HEADING:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


def parse_requirement_entries(section: str) -> list[RequirementEntry]:
    """Split a Requirements section into ``###`` entries (fence-aware)."""
    lines = section.splitlines()
    fenced = fenced_flags(lines)
    entries: list[RequirementEntry] = []
    current: RequirementEntry | None = None
    for line, in_fence in zip(lines, fenced):
        if not in_fence and line.startswith("###") and not line.startswith("####"):
            heading = line.rstrip()
            current = RequirementEntry(heading=heading)
            m = _REQ_HEADING_RE.match(heading)
            if m:
                current.op = m.group(1)
                current.capability = m.group(2).strip()
                current.requirement = m.group(3).strip()
            entries.append(current)
        elif current is not None:
            current.body_lines.append(line)
    return entries


def _split_scenarios(body_lines: list[str]) -> list[tuple[str, list[str]]]:
    """Return (scenario heading line, following lines) pairs, ignoring fenced headings."""
    scenarios: list[tuple[str, list[str]]] = []
    fenced = fenced_flags(body_lines)
    cur: str | None = None
    cur_lines: list[str] = []
    for line, in_fence in zip(body_lines, fenced):
        if not in_fence and line.startswith(SCENARIO_PREFIX):
            if cur is not None:
                scenarios.append((cur, cur_lines))
            cur = line.rstrip()
            cur_lines = []
        elif cur is not None:
            cur_lines.append(line)
    if cur is not None:
        scenarios.append((cur, cur_lines))
    return scenarios


def _scenario_name(heading: str) -> str:
    return heading[len(SCENARIO_PREFIX):].strip()


def parse_requirement_scenarios(spec_text: str) -> list[tuple[str, str, str]]:
    """Return (capability, requirement, scenario) for ADDED/MODIFIED scenarios.

    Scenarios with an empty name are skipped (they are validation errors).
    """
    section = extract_requirements_section(spec_text)
    if section is None:
        return []
    results: list[tuple[str, str, str]] = []
    for entry in parse_requirement_entries(section):
        if not entry.well_formed or entry.op == "REMOVED":
            continue
        for heading, _ in _split_scenarios(entry.body_lines):
            name = _scenario_name(heading)
            if name:
                results.append((entry.capability, entry.requirement, name))
    return results


def find_empty_scenarios(spec_text: str) -> list[tuple[str, str]]:
    """Return (capability, requirement) for each ADDED/MODIFIED empty-name scenario."""
    section = extract_requirements_section(spec_text)
    if section is None:
        return []
    found: list[tuple[str, str]] = []
    for entry in parse_requirement_entries(section):
        if not entry.well_formed or entry.op == "REMOVED":
            continue
        for heading, _ in _split_scenarios(entry.body_lines):
            if not _scenario_name(heading):
                found.append((entry.capability, entry.requirement))
    return found


def validate_delta_syntax(path: str, section: str) -> list[ValidationError]:
    """Syntax checks for the deltas of a Requirements section (no filesystem access)."""
    errors: list[ValidationError] = []

    def err(heading: str, message: str) -> None:
        errors.append(ValidationError(path=path, message=f"{heading}: {message}"))

    seen: set[tuple[str, str]] = set()
    for entry in parse_requirement_entries(section):
        heading = entry.heading
        if not entry.well_formed:
            err(
                heading,
                "malformed heading (expected "
                "'### ADDED|MODIFIED|REMOVED: <capability> / <requirement name>')",
            )
            continue
        if not _CAPABILITY_SLUG_RE.match(entry.capability):
            err(
                heading,
                f"invalid capability slug '{entry.capability}' "
                "(must match ^[a-z0-9]+(-[a-z0-9]+)*$)",
            )
        key = (entry.capability.lower(), entry.requirement.lower())
        if key in seen:
            err(heading, f"duplicate capability/requirement pair "
                         f"'{entry.capability} / {entry.requirement}'")
        else:
            seen.add(key)

        if entry.op in ("ADDED", "MODIFIED"):
            if not _SHALL_MUST_RE.search("\n".join(entry.body_lines)):
                err(heading, "no SHALL or MUST sentence found")
            scenarios = _split_scenarios(entry.body_lines)
            if not scenarios:
                err(heading, "no '#### Scenario:' sub-heading found")
            for sc_heading, sc_lines in scenarios:
                if not _scenario_name(sc_heading):
                    err(sc_heading, "scenario has an empty name")
                stripped = [bl.strip() for bl in sc_lines]
                if not any(s.startswith("- **WHEN**") for s in stripped):
                    err(sc_heading, "scenario missing '- **WHEN**' line")
                if not any(s.startswith("- **THEN**") for s in stripped):
                    err(sc_heading, "scenario missing '- **THEN**' line")
        else:  # REMOVED
            if not any(bl.strip().startswith("Reason:") for bl in entry.body_lines):
                err(heading, "REMOVED entry missing 'Reason:' line")
            if any(bl.startswith(SCENARIO_PREFIX) for bl in entry.body_lines):
                err(heading, "REMOVED entry must not contain scenarios")
    return errors


def load_current_requirements(
    capability: str, current_dir: str | Path = DEFAULT_CURRENT_DIR
) -> set[str] | None:
    """Lowercase requirement names in ``<current_dir>/<capability>.md``, or None if missing."""
    cap_path = Path(current_dir) / f"{capability}.md"
    if not cap_path.is_file():
        return None
    try:
        content = cap_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {m.group(1).strip().lower() for m in _CURRENT_REQ_RE.finditer(content)}


def validate_delta_consistency(
    path: str, section: str, current_dir: str | Path = DEFAULT_CURRENT_DIR
) -> list[ValidationError]:
    """Check deltas against ``specs/current/``: ADDED must be new; MODIFIED/REMOVED must exist.

    Malformed entries are skipped (syntax checks report them).
    """
    errors: list[ValidationError] = []
    cache: dict[str, set[str] | None] = {}
    for entry in parse_requirement_entries(section):
        if not entry.well_formed:
            continue
        cap = entry.capability
        if cap not in cache:
            cache[cap] = load_current_requirements(cap, current_dir)
        current = cache[cap]
        cap_file = str(Path(current_dir) / f"{cap}.md")
        req = entry.requirement.lower()

        def err(message: str) -> None:
            errors.append(ValidationError(path=path, message=f"{entry.heading}: {message}"))

        if entry.op == "ADDED":
            if current is not None and req in current:
                err(f"requirement already exists in {cap_file}")
        elif current is None:
            err(f"capability file not found: {cap_file}")
        elif req not in current:
            err(f"requirement not found in {cap_file}")
    return errors


# ---------------------------------------------------------------------------
# specs/current/ merge (transition to done)
# ---------------------------------------------------------------------------

_CURRENT_REQ_LINE_RE = re.compile(r"^## Requirement:\s+(.+)$")
SPEC_ID_RE = re.compile(r"^spec-[0-9a-f]{8}$")
TEMPLATE_FILE = "spec-template.md"


def _shift_headings(lines: list[str]) -> list[str]:
    """Shift ``####`` headings to ``###`` for the capability file format."""
    result: list[str] = []
    for line, in_fence in zip(lines, fenced_flags(lines)):
        if in_fence:
            result.append(line)
        elif line.startswith("#### "):
            result.append("### " + line[5:])
        elif line.startswith("####"):
            result.append("### " + line[4:])
        else:
            result.append(line)
    return result


def _build_requirement_block(req_name: str, spec_id: str, body_lines: list[str]) -> list[str]:
    shifted = _shift_headings(body_lines)
    while shifted and not shifted[-1].strip():
        shifted.pop()
    return [f"## Requirement: {req_name}", f"<!-- source: {spec_id} -->"] + shifted


def _requirement_range(lines: list[str], req_name: str) -> tuple[int, int] | None:
    wanted = req_name.lower()
    start: int | None = None
    for i, line in enumerate(lines):
        m = _CURRENT_REQ_LINE_RE.match(line)
        if m and m.group(1).strip().lower() == wanted:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## Requirement:"):
            end = i
            break
    return start, end


def _split_around(lines: list[str], start: int, end: int) -> tuple[list[str], list[str]]:
    before = lines[:start]
    while before and not before[-1].strip():
        before.pop()
    after = lines[end:]
    while after and not after[0].strip():
        after.pop(0)
    return before, after


def _apply_added(content: str, capability: str, req_name: str, spec_id: str,
                 body_lines: list[str]) -> str:
    block_str = "\n".join(_build_requirement_block(req_name, spec_id, body_lines))
    if not content.strip():
        return f"# {capability}\n\n{block_str}\n"
    return content.rstrip() + "\n\n" + block_str + "\n"


def _apply_modified(content: str, req_name: str, spec_id: str, body_lines: list[str]) -> str:
    lines = content.splitlines()
    rng = _requirement_range(lines, req_name)
    if rng is None:
        return content
    before, after = _split_around(lines, *rng)
    new_block = _build_requirement_block(req_name, spec_id, body_lines)
    result = before + [""] + new_block if before else list(new_block)
    if after:
        result = result + [""] + after
    return "\n".join(result) + "\n"


def _apply_removed(content: str, req_name: str) -> str:
    lines = content.splitlines()
    rng = _requirement_range(lines, req_name)
    if rng is None:
        return content
    before, after = _split_around(lines, *rng)
    if before and after:
        result = before + [""] + after
    elif before:
        result = before
    elif after:
        result = list(after)
    else:
        return ""
    return "\n".join(result) + "\n"


def _has_requirements(content: str) -> bool:
    return bool(re.search(r"^## Requirement:", content, re.MULTILINE))


def merge_requirements(
    path: str, spec_id: str, body: str, current_dir: str | Path = DEFAULT_CURRENT_DIR
) -> list[tuple[str, str, str]]:
    """Validate a spec's deltas and merge them into ``<current_dir>/<capability>.md``.

    Raises ``ValueError`` (listing every problem) before touching any file when
    the deltas are invalid. A capability file left without requirements is
    deleted. Returns ``(op, capability, requirement)`` per applied delta, or
    ``[]`` when the spec has no Requirements section. Writes here bypass
    ``on_document_changed`` (capability files are not registered documents).
    """
    section = extract_requirements_section(body)
    if section is None:
        return []
    errors = validate_delta_syntax(path, section) + validate_delta_consistency(
        path, section, current_dir
    )
    if errors:
        raise ValueError(
            "invalid requirement deltas:\n" + "\n".join(f"  {e.path}: {e.message}" for e in errors)
        )
    entries = parse_requirement_entries(section)
    if not entries:
        return []

    cur = Path(current_dir)
    contents: dict[str, str] = {}
    for entry in entries:
        if entry.capability not in contents:
            cap_path = cur / f"{entry.capability}.md"
            try:
                contents[entry.capability] = (
                    cap_path.read_text(encoding="utf-8") if cap_path.is_file() else ""
                )
            except OSError:
                contents[entry.capability] = ""
    for entry in entries:
        cap = entry.capability
        if entry.op == "ADDED":
            contents[cap] = _apply_added(contents[cap], cap, entry.requirement, spec_id,
                                         entry.body_lines)
        elif entry.op == "MODIFIED":
            contents[cap] = _apply_modified(contents[cap], entry.requirement, spec_id,
                                            entry.body_lines)
        else:
            contents[cap] = _apply_removed(contents[cap], entry.requirement)

    cur.mkdir(parents=True, exist_ok=True)
    for cap, content in contents.items():
        cap_path = cur / f"{cap}.md"
        if not _has_requirements(content):
            cap_path.unlink(missing_ok=True)
        else:
            cap_path.write_text(content, encoding="utf-8")
    return [(e.op or "", e.capability, e.requirement) for e in entries]


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _current_dir(store) -> Path:
    return store.root / store.doc_type.root_dir / "current"


def spec_on_create(store, doc: Document) -> None:
    """Use ``specs/spec-template.md`` (``{name}`` substituted) when it exists."""
    tpl = store.root / store.doc_type.root_dir / TEMPLATE_FILE
    if tpl.is_file():
        name = str(doc.frontmatter.get("name") or "")
        doc.body = tpl.read_text(encoding="utf-8").replace("{name}", name)


def spec_before_transition(store, doc: Document, new_status: str) -> list[tuple[str, str, str]]:
    """Merge deltas into ``specs/current/`` on transition to ``done``.

    Skipped when the spec is already done. Raises ``ValueError`` (no files
    changed) on invalid deltas. The applied deltas are returned and also kept
    on ``store.last_merged`` for callers that print ``merged: ...`` lines.
    """
    store.last_merged = []
    if new_status != "done" or doc.status == "done":
        return []
    applied = merge_requirements(
        str(doc.path), str(doc.frontmatter.get("id") or doc.id), doc.body, _current_dir(store)
    )
    store.last_merged = applied
    return applied


def spec_validate_document(store, doc: Document) -> list[ValidationError]:
    """Validation for one spec (errors unless noted as warnings)."""
    path = str(doc.path)
    out: list[ValidationError] = []

    def add(message: str, severity: str = "error") -> None:
        out.append(ValidationError(path=path, message=message, severity=severity))

    if doc.legacy:
        add("legacy spec without valid frontmatter (run 'takt spec migrate')", "warning")
        return out
    fm = doc.frontmatter
    raw_id = fm.get("id")
    if raw_id in (None, ""):
        add("missing 'id' in frontmatter")
    else:
        sid = str(raw_id)
        if sum(1 for d in store.load_all() if not d.legacy and d.id.lower() == sid.lower()) > 1:
            add(f"duplicate id '{sid}'")
        if not SPEC_ID_RE.match(sid):
            add(f"id '{sid}' does not match ^spec-[0-9a-f]{{8}}$", "warning")
    if not fm.get("name"):
        add("missing 'name' in frontmatter", "warning")
    fm_status = str(fm.get("status") or "")
    if fm_status != doc.status:
        add(f"status '{fm_status}' does not match folder status '{doc.status}'")

    section = extract_requirements_section(doc.body)
    if section is not None:
        out.extend(validate_delta_syntax(path, section))
        if doc.status != "done":
            out.extend(validate_delta_consistency(path, section, _current_dir(store)))
    return out


SPEC_BODY_TEMPLATE = """\
# {name}

## Objective

## Problems to Fix

## Changes

## Requirements

<!--
  Optional section. Each entry uses the format:
  ### <OP>: <capability> / <requirement name>
  where <OP> is ADDED, MODIFIED, or REMOVED, and <capability> is a
  kebab-case slug (e.g. bead-lifecycle, tui-tree-panel).

  ADDED / MODIFIED entries must contain:
  - a normative sentence using SHALL or MUST (uppercase)
  - at least one Scenario sub-heading:
      #### Scenario: <name>
      - **WHEN** <trigger>
      - **THEN** <observable outcome>
      - **AND** <additional outcome>   (optional)

  REMOVED entries must contain a "Reason:" line and no scenarios.

  Example:
  ### ADDED: bead-lifecycle / Recovery bead on missing structured output
  The scheduler SHALL create a recovery bead when output has no JSON.
  #### Scenario: Developer bead returns prose only
  - **WHEN** a developer bead's runner output contains no parseable JSON
  - **THEN** a bead `<id>-recovery` with `agent_type=recovery` is created

  ### REMOVED: bead-lifecycle / Manual retry required after parse failure
  Reason: superseded by automatic recovery beads.
-->

## Files to Modify

| File | Change |
|---|---|

## Acceptance Criteria

## Pending Decisions
"""

SPEC_TYPE = DocumentType(
    name="spec",
    id_prefix="spec-",
    root_dir="specs",
    filename_pattern="{slug}.md",
    statuses={"draft": "drafts", "planned": "planned", "done": "done"},
    allow_custom_statuses=True,
    reserved_statuses=frozenset({"current"}),
    transitions=None,
    resolution="id_prefix_or_filename",
    frontmatter_style=SPEC_STYLE,
    default_frontmatter={
        "name": None,
        "id": None,
        "description": None,
        "dependencies": None,
        "priority": None,
        "complexity": None,
        "status": "draft",
        "tags": [],
        "scope": {"in": None, "out": None},
        "feature_root_id": None,
    },
    template=SPEC_BODY_TEMPLATE,
    auto_commit=False,
    on_create=spec_on_create,
    before_transition=spec_before_transition,
    validate_document=spec_validate_document,
)
