"""Filesystem persistence for agent trace records.

Layout: ``<root>/.takt/traces/<bead_id>/<seq>.meta.json`` (plus optional
``<seq>.raw.jsonl`` and ``<seq>.events.jsonl``). Planner records, which have no
bead, live under ``_planner/``. All writes are atomic (temp file + replace).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable

from .models import TraceRecordMeta

PLANNER_DIR = "_planner"

_SEQ_META_RE = re.compile(r"^(\d+)\.meta\.json$")
# Process-wide lock: serialises seq allocation across all TraceStore instances.
# Cross-process safety comes from the exclusive os.link() publish below.
_ALLOC_LOCK = threading.Lock()


class TraceStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.traces_dir = self.root / ".takt" / "traces"

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _key(bead_id: str | None) -> str:
        if bead_id is None:
            return PLANNER_DIR
        if not bead_id or bead_id in (".", "..") or "/" in bead_id or "\\" in bead_id or "\x00" in bead_id:
            raise ValueError(f"invalid bead id for trace storage: {bead_id!r}")
        return bead_id

    def bead_dir(self, bead_id: str | None) -> Path:
        return self.traces_dir / self._key(bead_id)

    def meta_path(self, bead_id: str | None, seq: int) -> Path:
        return self.bead_dir(bead_id) / f"{seq}.meta.json"

    def raw_path(self, bead_id: str | None, seq: int) -> Path:
        return self.bead_dir(bead_id) / f"{seq}.raw.jsonl"

    def events_path(self, bead_id: str | None, seq: int) -> Path:
        return self.bead_dir(bead_id) / f"{seq}.events.jsonl"

    # ----------------------------------------------------------- atomic write
    @staticmethod
    def _tmp_in(directory: Path) -> tuple[int, str]:
        return tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")

    def _atomic_write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = self._tmp_in(path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _meta_bytes(meta: TraceRecordMeta) -> bytes:
        return (json.dumps(meta.to_dict(), indent=2) + "\n").encode("utf-8")

    # ---------------------------------------------------------------- records
    def _existing_seqs(self, directory: Path) -> list[int]:
        if not directory.is_dir():
            return []
        seqs: list[int] = []
        for entry in directory.iterdir():
            match = _SEQ_META_RE.match(entry.name)
            if match:
                seqs.append(int(match.group(1)))
        return seqs

    def new_record(self, bead_id: str | None, **meta_fields: Any) -> TraceRecordMeta:
        """Allocate the next seq for the bead (or planner) and write its meta file."""
        names = {f.name for f in fields(TraceRecordMeta)}
        unknown = set(meta_fields) - names
        if unknown:
            raise TypeError(f"unknown trace meta field(s): {', '.join(sorted(unknown))}")
        meta_fields.pop("seq", None)
        meta_fields.pop("bead_id", None)
        directory = self.bead_dir(bead_id)
        directory.mkdir(parents=True, exist_ok=True)
        with _ALLOC_LOCK:
            seq = max(self._existing_seqs(directory), default=0) + 1
            while True:
                meta = TraceRecordMeta(bead_id=bead_id, seq=seq, **meta_fields)
                fd, tmp = self._tmp_in(directory)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(self._meta_bytes(meta))
                    try:
                        # Exclusive, atomic publish: fails if another process won the seq.
                        os.link(tmp, self.meta_path(bead_id, seq))
                    except FileExistsError:
                        seq += 1
                        continue
                    return meta
                finally:
                    Path(tmp).unlink(missing_ok=True)

    def update_meta(self, bead_id: str | None, seq: int, **changes: Any) -> TraceRecordMeta:
        meta = self.load_meta(bead_id, seq)
        names = {f.name for f in fields(TraceRecordMeta)} - {"bead_id", "seq"}
        unknown = set(changes) - names
        if unknown:
            raise TypeError(f"cannot update trace meta field(s): {', '.join(sorted(unknown))}")
        for name, value in changes.items():
            setattr(meta, name, value)
        self._atomic_write_bytes(self.meta_path(bead_id, seq), self._meta_bytes(meta))
        return meta

    def load_meta(self, bead_id: str | None, seq: int) -> TraceRecordMeta:
        data = json.loads(self.meta_path(bead_id, seq).read_text(encoding="utf-8"))
        return TraceRecordMeta.from_dict(data)

    def list_records(self, bead_id: str | None) -> list[TraceRecordMeta]:
        directory = self.bead_dir(bead_id)
        records: list[TraceRecordMeta] = []
        for seq in sorted(self._existing_seqs(directory)):
            try:
                records.append(self.load_meta(bead_id, seq))
            except (OSError, ValueError):
                continue
        return records

    def list_planner_records(self) -> list[TraceRecordMeta]:
        return self.list_records(None)

    def find_record(self, bead_id: str | None, session_id: str) -> TraceRecordMeta | None:
        for record in self.list_records(bead_id):
            if record.session_id == session_id:
                return record
        return None

    # ------------------------------------------------------------- payloads
    def write_raw(self, bead_id: str | None, seq: int, src_path: Path | str) -> Path:
        """Copy the source transcript byte-for-byte to ``<seq>.raw.jsonl``."""
        dest = self.raw_path(bead_id, seq)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = self._tmp_in(dest.parent)
        try:
            with os.fdopen(fd, "wb") as out, open(src_path, "rb") as src:
                shutil.copyfileobj(src, out)
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return dest

    def write_events(self, bead_id: str | None, seq: int, events: Iterable[dict[str, Any]]) -> Path:
        dest = self.events_path(bead_id, seq)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = self._tmp_in(dest.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                for event in events:
                    out.write(json.dumps(event) + "\n")
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return dest

    def load_events(self, bead_id: str | None, seq: int) -> list[dict[str, Any]]:
        path = self.events_path(bead_id, seq)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
        return events

    def delete_bead_traces(self, bead_id: str) -> None:
        """Remove a bead's trace directory. Never touches ``_planner``."""
        if bead_id is None or self._key(bead_id) == PLANNER_DIR:
            return
        shutil.rmtree(self.bead_dir(bead_id), ignore_errors=True)
