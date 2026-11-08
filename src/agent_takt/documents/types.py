"""Core types for the document core: ``DocumentType``, ``Document``, ``ValidationError``."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .frontmatter import SPEC_STYLE, FrontmatterStyle

__all__ = [
    "CUSTOM_STATUS_RE",
    "Document",
    "DocumentType",
    "ValidationError",
    "noop_on_document_changed",
]

CUSTOM_STATUS_RE = re.compile(r"^[a-zA-Z0-9-]+$")

_RESOLUTIONS = ("id_prefix", "id_prefix_or_filename")


def noop_on_document_changed(doc_type: "DocumentType", doc_id: str, path: Path) -> None:
    """Default ``on_document_changed`` callback: does nothing."""
    return None


@dataclass(frozen=True)
class ValidationError:
    """A validation finding for a document file."""

    path: str
    message: str
    severity: str = "error"


@dataclass
class Document:
    """A loaded document."""

    id: str
    type: "DocumentType"
    status: str
    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    legacy: bool = False


@dataclass(frozen=True)
class DocumentType:
    """Per-type configuration of the generic document store."""

    name: str
    id_prefix: str
    root_dir: str
    filename_pattern: str = "{slug}.md"
    filename_builder: Callable[..., str] | None = None
    statuses: Mapping[str, str] = field(default_factory=dict)
    allow_custom_statuses: bool = False
    reserved_statuses: frozenset[str] = frozenset()
    # None = any transition allowed; otherwise the allowed (from, to) pairs.
    transitions: frozenset[tuple[str, str]] | None = None
    resolution: str = "id_prefix"
    frontmatter_style: FrontmatterStyle = SPEC_STYLE
    default_frontmatter: Mapping[str, Any] = field(default_factory=dict)
    template: str = ""
    auto_commit: bool = False
    on_create: Callable[..., Any] | None = None
    # Optional ``render_new(store, doc) -> str | None``: full file text for a newly
    # created document; returning None falls back to the standard rendering.
    render_new: Callable[..., str | None] | None = None
    before_transition: Callable[..., Any] | None = None
    after_transition: Callable[..., Any] | None = None
    validate_document: Callable[..., list[ValidationError]] | None = None
    on_document_changed: Callable[["DocumentType", str, Path], None] = noop_on_document_changed

    def __post_init__(self) -> None:
        if self.resolution not in _RESOLUTIONS:
            raise ValueError(
                f"invalid resolution {self.resolution!r}; expected one of {', '.join(_RESOLUTIONS)}"
            )

    def folder_for_status(self, status: str) -> str:
        """Return the folder name for *status*; raise ``ValueError`` if not allowed."""
        if status in self.reserved_statuses:
            raise ValueError(f"status {status!r} is reserved for {self.name} documents")
        if status in self.statuses:
            return self.statuses[status]
        if not self.allow_custom_statuses:
            allowed = ", ".join(self.statuses)
            raise ValueError(f"invalid status {status!r} for {self.name}; allowed: {allowed}")
        if not CUSTOM_STATUS_RE.match(status or ""):
            raise ValueError(
                f"invalid status {status!r}: custom statuses must match ^[a-zA-Z0-9-]+$"
            )
        return status

    def status_for_folder(self, folder: str) -> str | None:
        """Return the status for *folder*, or ``None`` if it is not a status folder."""
        for status, mapped in self.statuses.items():
            if mapped == folder:
                return status
        if (
            self.allow_custom_statuses
            and folder not in self.reserved_statuses
            and CUSTOM_STATUS_RE.match(folder or "")
        ):
            return folder
        return None

    def is_transition_allowed(self, from_status: str, to_status: str) -> bool:
        """True when moving from *from_status* to *to_status* is permitted."""
        if self.transitions is None:
            return True
        return (from_status, to_status) in self.transitions
