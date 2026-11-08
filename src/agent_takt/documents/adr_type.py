"""ADR document type: configuration and hooks.

Ports the behaviour of ``agent_takt.adr`` onto the document core:

* ``accepted_at`` is set when an ADR moves to ``approved``; ``superseded_at``
  when it moves to ``superseded``.
* Supersession links (``supersedes`` on the new ADR, ``superseded_by`` on the
  old one) are frontmatter fields supplied by the caller through
  ``DocumentStore.apply`` so both ADRs change atomically; the hooks guarantee
  the on-disk layout (``Adr.to_dict()`` key order, ``yaml.dump`` output) is the
  one ``adr.py`` produces.
* The required-body-section check blocks approval (``before_transition``) and
  is reported by ``validate_document`` for approved ADRs.

This module deliberately does not import ``agent_takt.adr`` so that module can
later become a thin wrapper over the core without a circular import.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .frontmatter import ADR_STYLE, read_frontmatter, split_frontmatter
from .types import Document, DocumentType, ValidationError

ADR_STATUSES = ("draft", "approved", "superseded", "rejected")

REQUIRED_BODY_SECTIONS = ["Summary", "Context", "Considered Options", "Decision", "Consequences"]
REQUIRED_FRONTMATTER_FIELDS = ["id", "title", "status", "created_at", "authors"]
ADR_ID_RE = re.compile(r"^ADR-[0-9a-f]{8}$")
TEMPLATE_PATH = ("templates", "adr", "template.md")

# Key order of ``Adr.to_dict()``.
ADR_KEY_ORDER = [
    "id", "title", "status", "created_at", "authors", "description", "accepted_at",
    "superseded_at", "superseded_by", "supersedes", "tags", "related_specs",
    "related_beads", "review_after",
]

# Unmodified template placeholder lines — not real ADR content.
_PLACEHOLDER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^> In the context of(?=(?:.*?<[^>]+>){3})"),
    re.compile(r"^\* \([^)]+\)\s*$"),
    re.compile(r"^### Option [A-Z] — \(name\)\s*$"),
    re.compile(r"^One sentence in the structured form above\."),
    re.compile(r"^\*Optional but strongly recommended\.\*"),
    re.compile(r"^\* Good: [\.…]+\s*$"),
    re.compile(r"^\* Bad: [\.…]+\s*$"),
    re.compile(r"^What is the issue, situation"),
    re.compile(r"^The chosen option, stated directly"),
    re.compile(r"^The options that were on the table"),
    re.compile(r"^If the decision has a binding implication"),
    re.compile(r"^This is the section that"),
    re.compile(r"^Each option gets its own subsection"),
    re.compile(r"^\* \(what becomes easier"),
    re.compile(r"^\* \(what we accept as the cost"),
    re.compile(r"^Specific forces pushing this decision"),
    re.compile(r"^Future agents will read this section"),
    re.compile(r"^Each driver is a one-line bullet\."),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_author(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "config", "user.name"], cwd=root, capture_output=True, text=True, timeout=5,
        )
        name = result.stdout.strip()
        if name:
            return [name]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Body checks (ported from adr.py)
# ---------------------------------------------------------------------------


def _parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = m.group(1)
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _has_real_content(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(p.match(stripped) for p in _PLACEHOLDER_PATTERNS):
            continue
        return True
    return False


def _has_real_option_subsection(content: str) -> bool:
    for line in content.splitlines():
        if re.match(r"^### ", line) and not re.match(r"^### Option [A-Z] — \(name\)\s*$", line):
            return True
    return False


def check_body_sections(body: str) -> list[str]:
    """Return error strings for missing/placeholder-only required body sections."""
    errors: list[str] = []
    sections = _parse_sections(body)
    for section in REQUIRED_BODY_SECTIONS:
        if section not in sections:
            errors.append(f"missing required section: ## {section}")
            continue
        content = sections[section]
        if section == "Considered Options":
            if not _has_real_option_subsection(content):
                errors.append(
                    "## Considered Options has no real option subsections (all are placeholders)"
                )
        elif not _has_real_content(content):
            errors.append(f"## {section} contains only placeholder or empty content")
    return errors


# ---------------------------------------------------------------------------
# Frontmatter normalisation (Adr.from_dict(...).to_dict())
# ---------------------------------------------------------------------------


def normalize_frontmatter(fm: dict[str, Any]) -> dict[str, Any]:
    """Return *fm* as ``Adr.from_dict(fm).to_dict()`` would: known keys, fixed order."""
    raw_created = fm.get("created_at", "") or ""
    created = raw_created.isoformat() if hasattr(raw_created, "isoformat") else str(raw_created)
    out: dict[str, Any] = {
        "id": fm["id"],
        "title": fm["title"],
        "status": fm["status"],
        "created_at": created,
        "authors": list(fm.get("authors") or []),
        "description": fm.get("description"),
        "accepted_at": fm.get("accepted_at"),
        "superseded_at": fm.get("superseded_at"),
        "superseded_by": fm.get("superseded_by"),
        "supersedes": list(fm.get("supersedes") or []),
        "tags": list(fm.get("tags") or []),
        "related_specs": list(fm.get("related_specs") or []),
        "related_beads": list(fm.get("related_beads") or []),
        "review_after": fm.get("review_after"),
    }
    return out


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def adr_on_create(store, doc: Document) -> None:
    """Stamp ``created_at``/``authors`` and use the project ADR template body if present."""
    doc.frontmatter["created_at"] = _utc_now()
    doc.frontmatter["authors"] = _git_author(store.root)
    tpl = store.root.joinpath(*TEMPLATE_PATH)
    if tpl.is_file():
        text = tpl.read_text(encoding="utf-8")
        _, body = split_frontmatter(text)
        title = str(doc.frontmatter.get("title") or "")
        doc.body = body.replace("{{title}}", title)


def _yaml_list_placeholder(items: list[str]) -> str:
    """Render a list for inline ``{{placeholder}}`` substitution in the template."""
    if not items:
        return ""
    return yaml.dump(items, default_flow_style=True, allow_unicode=True).strip()


def adr_render_new(store, doc: Document) -> str | None:
    """Render a new ADR by filling the project template's ``{{...}}`` placeholders.

    Keeps the template's key order and keys (title/description JSON-quoted) and
    returns None when no template exists so the core layout is used.
    """
    tpl = store.root.joinpath(*TEMPLATE_PATH)
    if not tpl.is_file():
        return None
    content = tpl.read_text(encoding="utf-8")
    fm = doc.frontmatter
    title = str(fm.get("title") or "")
    description = fm.get("description")
    fm_substitutions = {
        "{{title}}": json.dumps(title, ensure_ascii=False),
        "{{description}}": json.dumps(description, ensure_ascii=False) if description else "",
    }
    substitutions = {
        "{{id}}": str(doc.id),
        "{{title}}": title,
        "{{created_at}}": str(fm.get("created_at") or ""),
        "{{authors}}": _yaml_list_placeholder(list(fm.get("authors") or [])),
        "{{tags}}": _yaml_list_placeholder(list(fm.get("tags") or [])),
        "{{related_specs}}": _yaml_list_placeholder(list(fm.get("related_specs") or [])),
        "{{related_beads}}": _yaml_list_placeholder(list(fm.get("related_beads") or [])),
        "{{supersedes}}": _yaml_list_placeholder(list(fm.get("supersedes") or [])),
    }
    if content.startswith("---"):
        fm_end = content.find("\n---", 3)
        if fm_end != -1:
            fm_part, rest = content[:fm_end], content[fm_end:]
            for placeholder, value in fm_substitutions.items():
                fm_part = fm_part.replace(placeholder, value)
            content = fm_part + rest
    for placeholder, value in substitutions.items():
        content = content.replace(placeholder, value)
    return content


def adr_before_transition(store, doc: Document, new_status: str) -> None:
    """Block approval of an ADR whose required body sections are missing/placeholder."""
    if new_status != "approved":
        return
    errors = check_body_sections(doc.body)
    if errors:
        raise ValueError(
            f"ADR {doc.id} cannot be approved; body section errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def adr_after_transition(store, doc: Document, old_status: str) -> None:
    """Set ``accepted_at``/``superseded_at`` and re-dump frontmatter in ``adr.py`` layout."""
    path = doc.path
    text = path.read_text(encoding="utf-8")
    fm, body = read_frontmatter(text, path)
    fm = dict(fm or {})
    now = _utc_now()
    if doc.status == "approved":
        fm["accepted_at"] = now
    elif doc.status == "superseded":
        fm["superseded_at"] = now
    normalized = normalize_frontmatter(fm)
    path.write_text(ADR_STYLE.render(normalized, body.lstrip("\n")), encoding="utf-8")
    doc.frontmatter.clear()
    doc.frontmatter.update(normalized)


def adr_validate_document(store, doc: Document) -> list[ValidationError]:
    """Validation for one ADR; messages match ``AdrStore.validate_all``.

    ``ValidationError.path`` carries the ADR id (or the file path when the id is
    missing), as the legacy ``ValidationError.adr_id`` did.
    """
    fm = doc.frontmatter
    adr_id = fm.get("id") or ""
    label = str(adr_id) if adr_id else str(doc.path)
    out: list[ValidationError] = []

    def add(lbl: str, message: str) -> None:
        out.append(ValidationError(path=lbl, message=message))

    for fname in REQUIRED_FRONTMATTER_FIELDS:
        val = fm.get(fname)
        if val is None or (isinstance(val, str) and not val.strip()):
            add(label, f"missing required frontmatter field: {fname!r}")
    if adr_id and not ADR_ID_RE.match(str(adr_id)):
        add(str(adr_id), f"invalid ADR ID format: {adr_id!r}")
    status = fm.get("status") or ""
    if status not in ADR_STATUSES:
        add(label, f"invalid status value: {status!r}")

    if "id" not in fm or "title" not in fm or "status" not in fm:
        add(label, "failed to parse frontmatter as Adr")
        return out
    if status == "approved":
        for msg in check_body_sections(doc.body):
            add(str(fm["id"]), msg)

    all_ids = {d.id for d in store.load_all() if not d.legacy}
    superseded_by = fm.get("superseded_by")
    if superseded_by and superseded_by not in all_ids:
        add(str(fm["id"]), f"dangling superseded_by reference: {superseded_by!r}")
    for ref in list(fm.get("supersedes") or []):
        if ref not in all_ids:
            add(str(fm["id"]), f"dangling supersedes reference: {ref!r}")
    return out


ADR_BODY_TEMPLATE = """\
# {title}

## Summary

> In the context of <use case>, facing <concern>, we decided for <option>, to achieve <quality>, accepting <downside>.

One sentence in the structured form above. This is the executive line — operators and agents read it to grasp the decision without parsing the whole document.

## Context

What is the issue, situation, or pressure motivating this decision? Be concrete — name the constraints, the stakeholders, the prior art, and any failed prior attempts. Future agents will read this section to understand whether the decision still applies to their situation.

## Decision Drivers

*Optional but strongly recommended.* Specific forces pushing this decision — constraints, quality attributes, or non-negotiable requirements. Each driver is a one-line bullet.

* (driver 1)
* (driver 2)

## Considered Options

The options that were on the table. Each option gets its own subsection with bullet lists of arguments for and against. This is the section that prevents future agents from re-proposing rejected alternatives — keep the "Bad" bullets specific enough to actually argue against, not just hand-waved.

### Option A — (name)

* Good: …
* Good: …
* Bad: …

### Option B — (name)

* Good: …
* Bad: …
* Bad: …

## Decision

The chosen option, stated directly and unambiguously. If the decision has a binding implication ("X is forbidden", "Y is the default", "all Z must be Q"), state it as such. This is the section that future agents will treat as a constraint — keep it crisp.

## Consequences

### Positive

* (what becomes easier, safer, or otherwise improved)
* …

### Negative

* (what we accept as the cost of this decision)
* …
"""

ADR_TYPE = DocumentType(
    name="adr",
    id_prefix="ADR-",
    root_dir="adr",
    filename_pattern="adr-{hex}-{slug}.md",
    statuses={
        "draft": "drafts",
        "approved": "approved",
        "superseded": "superseded",
        "rejected": "rejected",
    },
    allow_custom_statuses=False,
    transitions=frozenset({
        ("draft", "approved"),
        ("draft", "rejected"),
        ("approved", "superseded"),
    }),
    resolution="id_prefix",
    frontmatter_style=ADR_STYLE,
    default_frontmatter={
        "id": None,
        "title": None,
        "status": "draft",
        "created_at": None,
        "authors": [],
        "description": None,
        "accepted_at": None,
        "superseded_at": None,
        "superseded_by": None,
        "supersedes": [],
        "tags": [],
        "related_specs": [],
        "related_beads": [],
        "review_after": None,
    },
    template=ADR_BODY_TEMPLATE,
    auto_commit=True,
    on_create=adr_on_create,
    render_new=adr_render_new,
    before_transition=adr_before_transition,
    after_transition=adr_after_transition,
    validate_document=adr_validate_document,
)
