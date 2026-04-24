from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt_fleet.executor import fan_out


# ── Basic behaviour ───────────────────────────────────────────────────────────


def test_fan_out_empty_returns_empty() -> None:
    results = fan_out([], lambda x: x, max_parallel=4)
    assert results == []


def test_fan_out_single_item() -> None:
    results = fan_out([42], lambda x: x * 2, max_parallel=1)
    assert len(results) == 1
    item, value, exc = results[0]
    assert item == 42
    assert value == 84
    assert exc is None


def test_fan_out_returns_all_items_in_order() -> None:
    items = list(range(10))
    results = fan_out(items, lambda x: x * x, max_parallel=4)
    assert len(results) == 10
    for idx, (item, value, exc) in enumerate(results):
        assert item == idx
        assert value == idx * idx
        assert exc is None


def test_fan_out_captures_exception_per_item() -> None:
    def boom(x: int) -> int:
        if x == 3:
            raise ValueError("bad item")
        return x

    items = [1, 2, 3, 4]
    results = fan_out(items, boom, max_parallel=4)
    assert len(results) == 4

    ok_items = [(item, val, exc) for item, val, exc in results if exc is None]
    bad_items = [(item, val, exc) for item, val, exc in results if exc is not None]

    assert len(ok_items) == 3
    assert len(bad_items) == 1
    bad_item, bad_val, bad_exc = bad_items[0]
    assert bad_item == 3
    assert bad_val is None
    assert isinstance(bad_exc, ValueError)
    assert "bad item" in str(bad_exc)


def test_fan_out_all_fail() -> None:
    def always_raise(x: int) -> int:
        raise RuntimeError(f"fail {x}")

    results = fan_out([1, 2, 3], always_raise, max_parallel=2)
    assert all(exc is not None for _, _, exc in results)
    assert all(val is None for _, val, _ in results)


def test_fan_out_preserves_item_order_with_varying_latency() -> None:
    """Items that finish last must still appear at their original index."""
    delays = [0.05, 0.01, 0.03, 0.02]

    def slow(delay: float) -> float:
        time.sleep(delay)
        return delay

    results = fan_out(delays, slow, max_parallel=4)
    assert [item for item, _, _ in results] == delays


# ── Concurrency bounds ────────────────────────────────────────────────────────


def test_fan_out_respects_max_parallel() -> None:
    """Peak concurrent executions must not exceed max_parallel."""
    max_parallel = 2
    concurrent_count = 0
    peak = 0
    lock = threading.Lock()

    def measure(_: int) -> None:
        nonlocal concurrent_count, peak
        with lock:
            concurrent_count += 1
            if concurrent_count > peak:
                peak = concurrent_count
        time.sleep(0.02)
        with lock:
            concurrent_count -= 1

    fan_out(list(range(6)), measure, max_parallel=max_parallel)
    assert peak <= max_parallel


def test_fan_out_max_parallel_one_runs_sequentially() -> None:
    order: list[int] = []

    def record(x: int) -> None:
        order.append(x)

    fan_out([1, 2, 3], record, max_parallel=1)
    assert order == [1, 2, 3]


# ── Exception type preservation ───────────────────────────────────────────────


def test_fan_out_preserves_exception_type() -> None:
    class CustomError(Exception):
        pass

    def raise_custom(x: int) -> None:
        raise CustomError("oops")

    results = fan_out([1], raise_custom, max_parallel=1)
    _, _, exc = results[0]
    assert isinstance(exc, CustomError)


# ── KeyboardInterrupt propagation ─────────────────────────────────────────────


def test_fan_out_propagates_keyboard_interrupt() -> None:
    """fan_out must re-raise KeyboardInterrupt after cancelling pending futures."""
    barrier = threading.Barrier(2, timeout=5)

    def blocking(_: int) -> None:
        barrier.wait()
        time.sleep(10)

    with pytest.raises(KeyboardInterrupt):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch

        original_as_completed = __import__(
            "concurrent.futures", fromlist=["as_completed"]
        ).as_completed

        call_count = 0

        def patched_as_completed(fs, **kwargs):
            nonlocal call_count
            for future in original_as_completed(fs, **kwargs):
                call_count += 1
                if call_count == 1:
                    raise KeyboardInterrupt
                yield future

        with patch("agent_takt_fleet.executor.concurrent.futures.as_completed", patched_as_completed):
            fan_out([1, 2], blocking, max_parallel=2)
