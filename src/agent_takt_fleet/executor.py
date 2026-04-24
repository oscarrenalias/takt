from __future__ import annotations

import concurrent.futures
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def fan_out(
    items: Sequence[T],
    fn: Callable[[T], R],
    max_parallel: int,
) -> list[tuple[T, R | None, BaseException | None]]:
    """Run fn(item) for each item with bounded concurrency.

    Returns a list of (item, result, exception) in the same order as items.
    If fn raises, result is None and exception holds the error — the fleet
    operation continues for remaining items.
    Ctrl-C cancels pending futures and re-raises KeyboardInterrupt.
    """
    if not items:
        return []

    n = len(items)
    results: list[tuple[T, R | None, BaseException | None]] = [None] * n  # type: ignore[list-item]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        future_to_idx: dict[concurrent.futures.Future[R], int] = {
            pool.submit(fn, item): i for i, item in enumerate(items)
        }
        try:
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                item = items[idx]
                exc = future.exception()
                if exc is not None:
                    results[idx] = (item, None, exc)
                else:
                    results[idx] = (item, future.result(), None)
        except KeyboardInterrupt:
            for f in future_to_idx:
                f.cancel()
            raise

    return results
