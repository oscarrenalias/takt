"""Incremental events.jsonl tailing for the fleet web UI.

Pure and synchronous (no Starlette dependency). One ``EventLogTail`` per
project file; the caller invokes ``poll()`` periodically and receives only the
JSON objects appended since the previous call.
"""
from __future__ import annotations

import json
import os
from collections import deque
from hashlib import blake2b
from pathlib import Path

from ..tailer import EVENTS_JSONL

__all__ = ["EventLogTail", "EVENTS_JSONL"]

_SENT_MEMORY = 2048
_READ_CHUNK = 1 << 20


class EventLogTail:
    """Tail a JSONL file, returning parsed dict records appended after start.

    - Starts at the current end of file (or ``start_offset`` when given). If the
      file does not exist yet, everything written once it appears is new.
    - A partial last line is buffered until its newline arrives.
    - Malformed (or non-object) JSON lines are skipped.
    - A shrunk or replaced file (size < offset, or inode change) is re-read from
      the start; lines already returned are not returned again.
    - A missing file yields no lines and never raises.
    """

    def __init__(self, path: Path | str, start_offset: int | None = None) -> None:
        self.path = Path(path)
        self._buffer = b""
        self._inode: int | None = None
        self._recent: deque[bytes] = deque(maxlen=_SENT_MEMORY)
        self._recent_set: set[bytes] = set()
        try:
            st = os.stat(self.path)
        except OSError:
            self._offset = 0 if start_offset is None else start_offset
            return
        self._inode = st.st_ino
        self._offset = st.st_size if start_offset is None else min(start_offset, st.st_size)

    @property
    def offset(self) -> int:
        return self._offset

    def _remember(self, raw: bytes) -> None:
        digest = blake2b(raw, digest_size=16).digest()
        if len(self._recent) == self._recent.maxlen:
            self._recent_set.discard(self._recent[0])
        self._recent.append(digest)
        self._recent_set.add(digest)

    def _seen(self, raw: bytes) -> bool:
        return blake2b(raw, digest_size=16).digest() in self._recent_set

    def poll(self) -> list[dict]:
        """Return new complete, valid JSON-object lines since the last poll."""
        try:
            st = os.stat(self.path)
        except OSError:
            return []

        rotated = False
        if self._inode is not None and st.st_ino != self._inode:
            rotated = True
        elif st.st_size < self._offset:
            rotated = True
        if rotated:
            self._offset = 0
            self._buffer = b""
        elif self._inode is None:
            # File appeared after we started: all of it is new.
            self._offset = 0
        self._inode = st.st_ino

        if st.st_size <= self._offset:
            return []

        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read(_READ_CHUNK)
        except OSError:
            return []
        if not data:
            return []
        self._offset += len(data)

        data = self._buffer + data
        *complete, tail = data.split(b"\n")
        self._buffer = tail

        out: list[dict] = []
        for raw in complete:
            raw = raw.strip()
            if not raw:
                continue
            if rotated and self._seen(raw):
                continue
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            self._remember(raw)
            out.append(obj)
        return out
