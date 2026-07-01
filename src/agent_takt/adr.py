from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ADR_DRAFT = "draft"
ADR_APPROVED = "approved"
ADR_SUPERSEDED = "superseded"
ADR_REJECTED = "rejected"

ADR_STATUSES = {ADR_DRAFT, ADR_APPROVED, ADR_SUPERSEDED, ADR_REJECTED}

_STATUS_FOLDERS: dict[str, str] = {
    ADR_DRAFT: "drafts",
    ADR_APPROVED: "approved",
    ADR_SUPERSEDED: "superseded",
    ADR_REJECTED: "rejected",
}

_REQUIRED_BODY_SECTIONS = ["Summary", "Context", "Considered Options", "Decision", "Consequences"]
_REQUIRED_FRONTMATTER_FIELDS = ["id", "title", "status", "created_at", "authors"]
_ADR_ID_RE = re.compile(r"^ADR-[0-9a-f]{8}$")

# Lines that are unmodified template placeholders — not real ADR content.
# The Alexandrian Summary stub has 5 angle-bracket tokens; real prose rarely has 3+.
# Requiring ≥3 prevents false-positives from incidental tokens like `<type>` in path references.
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


@dataclass
class Adr:
    id: str
    title: str
    status: str
    created_at: str
    authors: list[str]
    description: str | None = None
    accepted_at: str | None = None
    superseded_at: str | None = None
    superseded_by: str | None = None
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related_specs: list[str] = field(default_factory=list)
    related_beads: list[str] = field(default_factory=list)
    review_after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "authors": self.authors,
            "description": self.description,
            "accepted_at": self.accepted_at,
            "superseded_at": self.superseded_at,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "tags": self.tags,
            "related_specs": self.related_specs,
            "related_beads": self.related_beads,
            "review_after": self.review_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Adr":
        # PyYAML parses ISO timestamps as datetime objects; coerce to str explicitly
        raw_created_at = data.get("created_at", "") or ""
        if hasattr(raw_created_at, "isoformat"):
            created_at_str = raw_created_at.isoformat()
        else:
            created_at_str = str(raw_created_at)
        return cls(
            id=data["id"],
            title=data["title"],
            status=data["status"],
            created_at=created_at_str,
            authors=list(data.get("authors") or []),
            description=data.get("description"),
            accepted_at=data.get("accepted_at"),
            superseded_at=data.get("superseded_at"),
            superseded_by=data.get("superseded_by"),
            supersedes=list(data.get("supersedes") or []),
            tags=list(data.get("tags") or []),
            related_specs=list(data.get("related_specs") or []),
            related_beads=list(data.get("related_beads") or []),
            review_after=data.get("review_after"),
        )


@dataclass
class ValidationError:
    adr_id: str
    message: str


def _allocate_id() -> str:
    raw = uuid.uuid4().hex
    return f"ADR-{raw[:8]}"


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


def _git_author(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        name = result.stdout.strip()
        if name:
            return [name]
    except Exception:
        pass
    return []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter_dict, body).

    Returns ({}, full_text) if there is no leading YAML front matter block.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    raw = yaml.safe_load(fm_text) or {}
    return raw, body


def _render_frontmatter(data: dict[str, Any]) -> str:
    """Serialise frontmatter dict back to the YAML block (without delimiters)."""
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _yaml_list_placeholder(items: list[str]) -> str:
    """Render a list for inline {{placeholder}} substitution in the template."""
    if not items:
        return ""
    return yaml.dump(items, default_flow_style=True, allow_unicode=True).strip()


def _parse_sections(body: str) -> dict[str, str]:
    """Parse a markdown body into sections keyed by ## heading text."""
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
        else:
            if current is not None:
                lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _has_real_content(text: str) -> bool:
    """Return True if text has at least one non-placeholder, non-whitespace line."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(p.match(stripped) for p in _PLACEHOLDER_PATTERNS):
            continue
        return True
    return False


def _has_real_option_subsection(content: str) -> bool:
    """Return True if Considered Options content has at least one non-placeholder ### subsection."""
    for line in content.splitlines():
        if re.match(r"^### ", line) and not re.match(r"^### Option [A-Z] — \(name\)\s*$", line):
            return True
    return False


def _check_body_sections(body: str) -> list[str]:
    """Return list of error strings for body section violations."""
    errors: list[str] = []
    sections = _parse_sections(body)

    for section in _REQUIRED_BODY_SECTIONS:
        if section not in sections:
            errors.append(f"missing required section: ## {section}")
            continue
        content = sections[section]
        if section == "Considered Options":
            if not _has_real_option_subsection(content):
                errors.append("## Considered Options has no real option subsections (all are placeholders)")
        else:
            if not _has_real_content(content):
                errors.append(f"## {section} contains only placeholder or empty content")

    return errors


class AdrStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.adr_dir = self.root / "adr"

    def _folder(self, status: str) -> Path:
        return self.adr_dir / _STATUS_FOLDERS[status]

    def _all_md_files(self) -> list[Path]:
        files: list[Path] = []
        for folder in _STATUS_FOLDERS.values():
            d = self.adr_dir / folder
            if d.is_dir():
                files.extend(d.glob("*.md"))
        return files

    def _load_file(self, path: Path) -> Adr:
        text = path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        return Adr.from_dict(fm)

    def _load_file_with_body(self, path: Path) -> tuple[Adr, str]:
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        return Adr.from_dict(fm), body

    def _save_file(self, path: Path, adr: Adr, body: str) -> None:
        fm_text = _render_frontmatter(adr.to_dict())
        path.write_text(f"---\n{fm_text}---\n\n{body}", encoding="utf-8")

    def load_all(self) -> list[Adr]:
        adrs = []
        for p in self._all_md_files():
            try:
                adrs.append(self._load_file(p))
            except (KeyError, TypeError, yaml.YAMLError):
                pass
        return adrs

    def find_by_id(self, adr_id: str) -> Adr:
        for p in self._all_md_files():
            try:
                adr = self._load_file(p)
                if adr.id == adr_id:
                    return adr
            except (KeyError, TypeError, yaml.YAMLError):
                pass
        raise KeyError(f"ADR not found: {adr_id}")

    def find_file_by_id(self, adr_id: str) -> Path:
        """Return the filesystem path for an ADR by full ID."""
        for p in self._all_md_files():
            try:
                adr = self._load_file(p)
                if adr.id == adr_id:
                    return p
            except (KeyError, TypeError, yaml.YAMLError):
                pass
        raise KeyError(f"ADR file not found: {adr_id}")

    def resolve_prefix(self, prefix: str) -> str:
        """Resolve a partial ADR ID prefix to a full ID.

        Accepts the full ID, "ADR-<hex>", or a bare hex prefix like "a3f1".
        Raises ValueError on zero or multiple matches.
        """
        prefix_lower = prefix.lower()
        # Normalise: bare hex prefix → "adr-<prefix>" so "a3f1" matches "ADR-a3f19c2b"
        if not prefix_lower.startswith("adr-"):
            prefix_lower = f"adr-{prefix_lower}"
        matches = [adr.id for adr in self.load_all() if adr.id.lower().startswith(prefix_lower)]
        if not matches:
            raise ValueError(f"No ADR matches prefix: {prefix!r}")
        if len(matches) > 1:
            joined = ", ".join(sorted(matches))
            raise ValueError(f"Ambiguous ADR prefix {prefix!r}: matches {joined}")
        return matches[0]

    def list_adrs(
        self,
        statuses: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[Adr]:
        """Return ADRs filtered by status (OR) and tags (AND)."""
        results = self.load_all()
        if statuses:
            status_set = set(statuses)
            results = [a for a in results if a.status in status_set]
        if tags:
            tag_set = set(tags)
            results = [a for a in results if tag_set.issubset(set(a.tags))]
        return results

    def _template_path(self) -> Path:
        return self.root / "templates" / "adr" / "template.md"

    def new_adr(
        self,
        title: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
        related_specs: list[str] | None = None,
        related_beads: list[str] | None = None,
        supersedes: list[str] | None = None,
    ) -> Adr:
        """Create a new draft ADR from the template and write it to adr/drafts/.

        Raises FileNotFoundError if the ADR template does not exist.
        """
        template_path = self._template_path()
        if not template_path.exists():
            raise FileNotFoundError(
                f"ADR template not found at {template_path}. "
                "Run 'takt init' or 'takt upgrade' to install it."
            )
        template_text = template_path.read_text(encoding="utf-8")

        adr_id = _allocate_id()
        created_at = _utc_now()
        authors = _git_author(self.root)
        tags = list(tags or [])
        related_specs = list(related_specs or [])
        related_beads = list(related_beads or [])
        supersedes = list(supersedes or [])

        substitutions: dict[str, str] = {
            "{{id}}": adr_id,
            "{{title}}": title,
            "{{created_at}}": created_at,
            "{{authors}}": _yaml_list_placeholder(authors),
            "{{description}}": description or "",
            "{{tags}}": _yaml_list_placeholder(tags),
            "{{related_specs}}": _yaml_list_placeholder(related_specs),
            "{{related_beads}}": _yaml_list_placeholder(related_beads),
            "{{supersedes}}": _yaml_list_placeholder(supersedes),
        }

        content = template_text
        for placeholder, value in substitutions.items():
            content = content.replace(placeholder, value)

        drafts_dir = self._folder(ADR_DRAFT)
        drafts_dir.mkdir(parents=True, exist_ok=True)

        hex_part = adr_id[len("ADR-"):]
        slug = _slugify(title)
        filename = f"adr-{hex_part}-{slug}.md"
        file_path = drafts_dir / filename
        file_path.write_text(content, encoding="utf-8")

        adr = Adr(
            id=adr_id,
            title=title,
            status=ADR_DRAFT,
            created_at=created_at,
            authors=authors,
            description=description,
            supersedes=supersedes,
            tags=tags,
            related_specs=related_specs,
            related_beads=related_beads,
        )
        return adr

    def approve(self, adr_id: str, supersedes: list[str] | None = None) -> Adr:
        """Transition a draft ADR to approved.

        If supersedes is provided, each listed ADR must currently be approved and is
        atomically transitioned to superseded. Any validation failure aborts the entire
        operation — no files are modified.
        """
        supersedes_ids = list(supersedes or [])

        # Validate main ADR
        adr_file = self.find_file_by_id(adr_id)
        adr, body = self._load_file_with_body(adr_file)
        if adr.status != ADR_DRAFT:
            raise ValueError(
                f"ADR {adr_id} cannot be approved: status is {adr.status!r} (must be 'draft')"
            )
        body_errors = _check_body_sections(body)
        if body_errors:
            raise ValueError(
                f"ADR {adr_id} cannot be approved; body section errors:\n"
                + "\n".join(f"  - {e}" for e in body_errors)
            )

        # Validate supersedes targets before touching anything
        targets: list[tuple[Path, Adr, str]] = []
        for target_id in supersedes_ids:
            target_file = self.find_file_by_id(target_id)
            target_adr, target_body = self._load_file_with_body(target_file)
            if target_adr.status != ADR_APPROVED:
                raise ValueError(
                    f"Cannot supersede {target_id}: status is {target_adr.status!r} (must be 'approved')"
                )
            targets.append((target_file, target_adr, target_body))

        # All validation passed — build write plan
        now = _utc_now()
        approved_dir = self._folder(ADR_APPROVED)
        approved_dir.mkdir(parents=True, exist_ok=True)
        superseded_dir = self._folder(ADR_SUPERSEDED)
        if targets:
            superseded_dir.mkdir(parents=True, exist_ok=True)

        all_supersedes = list(adr.supersedes)
        for sid in supersedes_ids:
            if sid not in all_supersedes:
                all_supersedes.append(sid)
        adr.status = ADR_APPROVED
        adr.accepted_at = now
        adr.supersedes = all_supersedes

        writes: list[tuple[Path, Adr, str]] = [(approved_dir / adr_file.name, adr, body)]
        deletes: list[Path] = [adr_file]

        for target_file, target_adr, target_body in targets:
            target_adr.status = ADR_SUPERSEDED
            target_adr.superseded_at = now
            target_adr.superseded_by = adr_id
            writes.append((superseded_dir / target_file.name, target_adr, target_body))
            deletes.append(target_file)

        # Write new files first; only unlink originals after all writes succeed
        written: list[Path] = []
        try:
            for new_path, adr_obj, body_text in writes:
                self._save_file(new_path, adr_obj, body_text)
                written.append(new_path)
            for old_path in deletes:
                old_path.unlink()
        except Exception:
            for p in written:
                if p.exists():
                    p.unlink()
            raise

        return adr

    def reject(self, adr_id: str) -> Adr:
        """Transition a draft ADR to rejected."""
        adr_file = self.find_file_by_id(adr_id)
        adr, body = self._load_file_with_body(adr_file)
        if adr.status != ADR_DRAFT:
            raise ValueError(
                f"ADR {adr_id} cannot be rejected: status is {adr.status!r} (must be 'draft')"
            )

        rejected_dir = self._folder(ADR_REJECTED)
        rejected_dir.mkdir(parents=True, exist_ok=True)
        new_file = rejected_dir / adr_file.name
        adr.status = ADR_REJECTED

        written: list[Path] = []
        try:
            self._save_file(new_file, adr, body)
            written.append(new_file)
            adr_file.unlink()
        except Exception:
            for p in written:
                if p.exists():
                    p.unlink()
            raise

        return adr

    def supersede(self, old_id: str, new_id: str) -> Adr:
        """Standalone transition: move an approved ADR to superseded.

        Both old_id must be approved; new_id must exist and be approved.
        """
        old_file = self.find_file_by_id(old_id)
        old_adr, old_body = self._load_file_with_body(old_file)
        if old_adr.status != ADR_APPROVED:
            raise ValueError(
                f"ADR {old_id} cannot be superseded: status is {old_adr.status!r} (must be 'approved')"
            )

        new_file_path = self.find_file_by_id(new_id)
        new_adr, _ = self._load_file_with_body(new_file_path)
        if new_adr.status != ADR_APPROVED:
            raise ValueError(
                f"Replacement ADR {new_id} is not approved: status is {new_adr.status!r}"
            )

        now = _utc_now()
        old_adr.status = ADR_SUPERSEDED
        old_adr.superseded_at = now
        old_adr.superseded_by = new_id

        superseded_dir = self._folder(ADR_SUPERSEDED)
        superseded_dir.mkdir(parents=True, exist_ok=True)
        dest = superseded_dir / old_file.name

        written: list[Path] = []
        try:
            self._save_file(dest, old_adr, old_body)
            written.append(dest)
            old_file.unlink()
        except Exception:
            for p in written:
                if p.exists():
                    p.unlink()
            raise

        return old_adr

    def validate_all(self) -> list[ValidationError]:
        """Walk all ADRs and return a list of ValidationError instances.

        Checks: missing required frontmatter fields, invalid ID format, invalid status,
        body-section violations on approved ADRs, and dangling superseded_by/supersedes
        references.
        """
        errors: list[ValidationError] = []
        all_ids: set[str] = set()
        adr_records: list[tuple[Adr, str]] = []

        for path in self._all_md_files():
            try:
                raw_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(ValidationError(str(path), f"cannot read file: {exc}"))
                continue

            fm, body = _parse_frontmatter(raw_text)
            adr_id: str = fm.get("id") or ""
            label = adr_id or str(path)

            # Required frontmatter fields
            for fname in _REQUIRED_FRONTMATTER_FIELDS:
                val = fm.get(fname)
                if val is None or (isinstance(val, str) and not val.strip()):
                    errors.append(ValidationError(label, f"missing required frontmatter field: {fname!r}"))

            # ID format
            if adr_id and not _ADR_ID_RE.match(adr_id):
                errors.append(ValidationError(adr_id, f"invalid ADR ID format: {adr_id!r}"))

            # Status validity
            status: str = fm.get("status") or ""
            if status not in ADR_STATUSES:
                errors.append(ValidationError(label, f"invalid status value: {status!r}"))

            try:
                adr = Adr.from_dict(fm)
            except (KeyError, TypeError):
                errors.append(ValidationError(label, "failed to parse frontmatter as Adr"))
                continue

            all_ids.add(adr.id)
            adr_records.append((adr, body))

            # Body section checks on approved ADRs
            if adr.status == ADR_APPROVED:
                for be in _check_body_sections(body):
                    errors.append(ValidationError(adr.id, be))

        # Referential integrity (second pass once all IDs are known)
        for adr, _ in adr_records:
            if adr.superseded_by and adr.superseded_by not in all_ids:
                errors.append(
                    ValidationError(adr.id, f"dangling superseded_by reference: {adr.superseded_by!r}")
                )
            for ref in adr.supersedes:
                if ref not in all_ids:
                    errors.append(
                        ValidationError(adr.id, f"dangling supersedes reference: {ref!r}")
                    )

        return errors
