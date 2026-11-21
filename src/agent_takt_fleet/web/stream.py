"""Incremental events.jsonl tailing for the fleet web UI.

Pure and synchronous (no Starlette dependency). One ``EventLogTail`` per
project file; the caller invokes ``poll()`` periodically and receives only the
JSON objects appended since the previous call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from hashlib import blake2b
from pathlib import Path
from typing import Any, Callable, Iterable

from ..tailer import EVENTS_JSONL

__all__ = ["EventLogTail", "EVENTS_JSONL", "FleetStreamHub", "Subscription", "format_sse"]

logger = logging.getLogger(__name__)

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


# --- change-detection hub ---------------------------------------------------

_QUEUE_MAX = 1000


def format_sse(event: str, data: Any) -> str:
    """Encode one SSE frame with a JSON ``data`` payload."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class Subscription:
    """One SSE client: an outgoing queue plus its own per-project event tails."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.tails: dict[str, EventLogTail] = {}

    def put(self, event: str, data: Any) -> None:
        try:
            self.queue.put_nowait((event, data))
        except asyncio.QueueFull:
            logger.warning("SSE subscriber queue full; dropping %s", event)


class FleetStreamHub:
    """Polls the registry and project files and fans changes out to subscribers.

    Emits ``fleet_event`` (new events.jsonl lines, per-client tails starting at
    connect time), ``project_updated`` (full card, only when it changed) and
    ``fleet_changed`` (full fleet body, when the registry content changes).
    """

    def __init__(
        self,
        projects_provider: Callable[[], Iterable[Any]],
        card_builder: Callable[[Any], dict[str, Any]],
        fleet_builder: Callable[[list[Any]], dict[str, Any]],
        event_builder: Callable[[str, dict[str, Any]], dict[str, Any]],
        poll_interval: float = 1.0,
    ) -> None:
        self._provider = projects_provider
        self._card_builder = card_builder
        self._fleet_builder = fleet_builder
        self._event_builder = event_builder
        self.poll_interval = poll_interval
        self._subscribers: set[Subscription] = set()
        self._cards: dict[str, str] = {}
        self._registry_sig: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # lifecycle
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            self._prime(self._projects())
        except Exception:
            logger.warning("stream hub priming failed", exc_info=True)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._subscribers.clear()

    # subscriptions
    def subscribe(self) -> Subscription:
        sub = Subscription()
        self._sync_tails(sub, self._projects())
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)
        sub.tails.clear()

    # polling
    def _projects(self) -> list[Any]:
        return list(self._provider())

    @staticmethod
    def _registry_signature(projects: list[Any]) -> str:
        return json.dumps(
            [[p.name, str(p.path), sorted(getattr(p, "tags", []) or [])] for p in projects]
        )

    def _card_json(self, project: Any) -> str:
        return json.dumps(self._card_builder(project), sort_keys=True, default=str)

    def _prime(self, projects: list[Any]) -> None:
        self._registry_sig = self._registry_signature(projects)
        self._cards = {}
        for proj in projects:
            try:
                self._cards[proj.name] = self._card_json(proj)
            except Exception:
                logger.warning("card build failed for %s", proj.name, exc_info=True)

    @staticmethod
    def _sync_tails(sub: Subscription, projects: list[Any]) -> None:
        wanted = {p.name: Path(p.path) / ".takt" / "logs" / "events.jsonl" for p in projects}
        for name in list(sub.tails):
            if name not in wanted or sub.tails[name].path != wanted[name]:
                del sub.tails[name]
        for name, path in wanted.items():
            if name not in sub.tails:
                sub.tails[name] = EventLogTail(path)

    def poll_once(self) -> None:
        """Run one detection pass and publish to subscribers."""
        projects = self._projects()
        if self._registry_sig is None:
            self._prime(projects)
        signature = self._registry_signature(projects)
        if signature != self._registry_sig:
            self._registry_sig = signature
            body = self._fleet_builder(projects)
            for sub in list(self._subscribers):
                self._sync_tails(sub, projects)
                sub.put("fleet_changed", body)

        names = set()
        for proj in projects:
            names.add(proj.name)
            try:
                card_json = self._card_json(proj)
            except Exception:
                logger.warning("card build failed for %s", proj.name, exc_info=True)
                continue
            previous = self._cards.get(proj.name)
            self._cards[proj.name] = card_json
            if previous is not None and previous != card_json:
                card = json.loads(card_json)
                for sub in list(self._subscribers):
                    sub.put("project_updated", card)
        for gone in set(self._cards) - names:
            del self._cards[gone]

        for sub in list(self._subscribers):
            for name, tail in list(sub.tails.items()):
                try:
                    records = tail.poll()
                except Exception:
                    continue
                for record in records:
                    sub.put("fleet_event", self._event_builder(name, record))

    async def _run(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.warning("stream poll failed", exc_info=True)
            await asyncio.sleep(self.poll_interval)
