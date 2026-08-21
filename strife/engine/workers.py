from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TypeVar

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None


def start_workers(*, max_workers: int) -> ThreadPoolExecutor:
    """Create the shared CPU/render pool. Safe to call more than once."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="strife-cpu",
        )
    return _executor


def get_executor() -> ThreadPoolExecutor:
    if _executor is None:
        return start_workers(max_workers=8)
    return _executor


async def run_cpu(fn: Callable[..., T], /, *args, **kwargs) -> T:
    """Run *fn* on the dedicated CPU pool (bots, board renders)."""
    loop = asyncio.get_running_loop()
    executor = get_executor()
    if kwargs:
        return await loop.run_in_executor(executor, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(executor, fn, *args)


def shutdown_workers() -> None:
    global _executor
    if _executor is None:
        return
    _executor.shutdown(wait=False, cancel_futures=True)
    _executor = None
