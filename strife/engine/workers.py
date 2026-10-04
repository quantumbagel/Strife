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
    """Run *fn* on the shared :class:`ThreadPoolExecutor` (bots, board renders).

    Pure Python still holds the GIL on those threads, so this mainly avoids
    blocking the asyncio event loop—not parallel CPU across cores. It helps
    when *fn* releases the GIL (native libraries, image rendering, I/O).
    """
    loop = asyncio.get_running_loop()
    executor = get_executor()
    if kwargs:
        return await loop.run_in_executor(executor, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(executor, fn, *args)


def shutdown_workers(*, wait: bool = False) -> None:
    """Stop the shared pool. ``wait=True`` lets in-flight work finish first."""
    global _executor
    if _executor is None:
        return
    executor, _executor = _executor, None
    executor.shutdown(wait=wait, cancel_futures=True)
