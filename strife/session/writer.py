from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from strife.engine.players import Move
from strife.logging import get_logger

log = get_logger("session.writer")


class MoveWriter:
    """Ordered background writer: one task drains a queue into ``moves``."""

    def __init__(
        self,
        append: Callable[[int, list[Move]], Awaitable[None]],
        match_id: int,
    ) -> None:
        self._append = append
        self._match_id = match_id
        self._queue: asyncio.Queue[Move] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def submit(self, move: Move) -> None:
        self._queue.put_nowait(move)

    async def flush(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        await self.flush()
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            try:
                while True:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                try:
                    await self._append(self._match_id, batch)
                except asyncio.CancelledError:
                    try:
                        await self._append(self._match_id, batch)
                    except Exception:
                        log.exception(
                            "Failed to persist %s move(s) for match %s on stop",
                            len(batch),
                            self._match_id,
                        )
                    raise
                except Exception:
                    log.exception(
                        "Failed to persist %s move(s) for match %s",
                        len(batch),
                        self._match_id,
                    )
            finally:
                for _ in batch:
                    self._queue.task_done()
