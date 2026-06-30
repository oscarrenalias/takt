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
        return cls(
            id=data["id"],
            title=data["title"],
            status=data["status"],
            created_at=data["created_at"],
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

        Raises ValueError on zero or multiple matches.
        """
        prefix_lower = prefix.lower()
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
