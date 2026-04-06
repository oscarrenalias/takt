from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
BLUE = "\033[38;5;39m"
GREEN = "\033[38;5;42m"
YELLOW = "\033[38;5;220m"
RED = "\033[38;5;196m"
MAGENTA = "\033[38;5;171m"
CYAN = "\033[38;5;81m"


class Spinner(AbstractContextManager["Spinner"]):
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, console: "ConsoleReporter", label: str) -> None:
        self.console = console
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Spinner":
        if self.console.is_tty:
            self._thread = threading.Thread(target=self._render, daemon=True)
            self._thread.start()
        else:
            self.console.info(self.label)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.fail(str(exc))
        return None

    def _render(self) -> None:
        index = 0
        while not self._stop.is_set():
            frame = self.FRAMES[index % len(self.FRAMES)]
            text = f"\r{CYAN}{frame}{RESET} {self.label}"
            with self.console._lock:
                sys.stdout.write(text)
                sys.stdout.flush()
            time.sleep(0.1)
            index += 1

    def _finish(self, icon: str, color: str, message: str) -> None:
        if self.console.is_tty:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)
            with self.console._lock:
                sys.stdout.write("\r\033[2K")
                sys.stdout.flush()
        self.console.emit(f"{color}{icon}{RESET} {message}")

    def success(self, message: str | None = None) -> None:
        self._finish("✓", GREEN, message or self.label)

    def fail(self, message: str | None = None) -> None:
        self._finish("✗", RED, message or self.label)

    def warn(self, message: str | None = None) -> None:
        self._finish("!", YELLOW, message or self.label)


@dataclass
class ConsoleReporter:
    stream: Any = sys.stdout

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def is_tty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def _c(self, code: str) -> str:
        """Return *code* only when writing to a TTY; empty string otherwise."""
        return code if self.is_tty else ""

    def emit(self, message: str = "") -> None:
        with self._lock:
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def section(self, title: str) -> None:
        self.emit(f"{self._c(BOLD)}{self._c(CYAN)}{title}{self._c(RESET)}")

    def info(self, message: str) -> None:
        self.emit(f"{self._c(BLUE)}•{self._c(RESET)} {message}")

    def success(self, message: str) -> None:
        self.emit(f"{self._c(GREEN)}✓{self._c(RESET)} {message}")

    def warn(self, message: str) -> None:
        self.emit(f"{self._c(YELLOW)}!{self._c(RESET)} {message}")

    def error(self, message: str) -> None:
        self.emit(f"{self._c(RED)}✗{self._c(RESET)} {message}")

    def detail(self, message: str) -> None:
        self.emit(f"{self._c(DIM)}  {message}{self._c(RESET)}")

    def spin(self, label: str) -> Spinner:
        return Spinner(self, label)

    def dump_json(self, payload: Any) -> None:
        self.emit(json.dumps(payload, indent=2))


class SpinnerPool:
    """Manages N concurrent spinner lines using ANSI cursor positioning.

    Each slot corresponds to a fixed terminal line. Active spinners update
    their slot in-place; finished spinners print a final status and free
    the slot. Non-TTY environments fall back to sequential line output.
    """

    FRAMES = Spinner.FRAMES

    def __init__(self, console: ConsoleReporter, max_workers: int) -> None:
        self.console = console
        self.max_workers = max_workers
        self._lock = threading.Lock()
        # slot -> (key, label)
        self._slots: dict[int, tuple[str, str]] = {}
        # key -> slot index
        self._key_to_slot: dict[str, int] = {}
        # Animation state per slot
        self._frame_indices: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._region_started = False

    def start(self) -> None:
        if not self.console.is_tty:
            return
        with self._lock:
            # Reserve N blank lines for the spinner region
            for _ in range(self.max_workers):
                self.console.stream.write("\n")
            self.console.stream.flush()
            self._region_started = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.console.is_tty and self._region_started:
            with self._lock:
                self._move_below_region()

    def add(self, key: str, label: str) -> None:
        with self._lock:
            if key in self._key_to_slot:
                return
            slot = self._find_free_slot()
            if slot is None:
                slot = 0  # overwrite first slot as fallback
            self._slots[slot] = (key, label)
            self._key_to_slot[key] = slot
            self._frame_indices[slot] = 0
        if not self.console.is_tty:
            self.console.info(label)

    def finish(self, key: str, icon: str, color: str, message: str) -> None:
        with self._lock:
            slot = self._key_to_slot.pop(key, None)
            if slot is not None:
                del self._slots[slot]
                self._frame_indices.pop(slot, None)
                if self.console.is_tty and self._region_started:
                    self._write_slot(slot, f"{color}{icon}{RESET} {message}")
        if not self.console.is_tty:
            self.console.emit(f"{color}{icon}{RESET} {message}")

    def _find_free_slot(self) -> int | None:
        for i in range(self.max_workers):
            if i not in self._slots:
                return i
        return None

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                for slot, (key, label) in self._slots.items():
                    idx = self._frame_indices.get(slot, 0)
                    frame = self.FRAMES[idx % len(self.FRAMES)]
                    self._write_slot(slot, f"{CYAN}{frame}{RESET} {label}")
                    self._frame_indices[slot] = idx + 1
                self.console.stream.flush()
            time.sleep(0.1)

    def _write_slot(self, slot: int, text: str) -> None:
        # Move cursor up from bottom of region to the target slot line,
        # clear the line, write text, then move back to bottom.
        lines_up = self.max_workers - slot
        self.console.stream.write(
            f"\033[{lines_up}A"  # move up
            f"\r\033[2K"         # clear line
            f"{text}"
            f"\033[{lines_up}B"  # move back down
            f"\r"                # return to start of line
        )

    def _move_below_region(self) -> None:
        self.console.stream.write("\r")
        self.console.stream.flush()
