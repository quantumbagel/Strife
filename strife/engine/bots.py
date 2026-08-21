from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from strife.engine.workers import run_cpu

T = TypeVar("T")


async def run_in_thread(fn: Callable[..., T], *args, **kwargs) -> T:
    return await run_cpu(fn, *args, **kwargs)
