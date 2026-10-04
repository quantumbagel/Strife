from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from strife.engine.players import Move
from strife.logging import get_logger
from strife.persistence.repositories import MatchNotLive, MoveConflict

log = get_logger("session.writer")

_RETRY_INITIAL_SECONDS = 0.5
_RETRY_MAX_SECONDS = 30.0
_CANCEL_APPEND_SECONDS = 5.0


class MoveWriter:
    """Ordered background writer: one task drains a queue into ``moves``."""

    def __init__(
        self,
        append: Callable[[int, list[Move]], Awaitable[None]],
        match_id: int,
        *,
        on_fatal: Callable[[BaseException], Awaitable[None]] | None = None,
    ) -> None:
        self._append = append
        self._match_id = match_id
        self._on_fatal = on_fatal
        self._queue: asyncio.Queue[Move] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._failed = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def submit(self, move: Move) -> None:
        if self._failed:
            return
        self._queue.put_nowait(move)

    async def flush(self) -> None:
        await self._queue.join()

    async def stop(self, timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        if not self._failed:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            except asyncio.TimeoutError:
                log.warning(
                    "Timed out draining move writer for match %s",
                    self._match_id,
                )
        if self._task is None or self._task.done():
            self._task = None
            return
        leftover = max(0.01, deadline - loop.time())
        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=leftover)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        self._task = None

    async def _fail(self, exc: BaseException) -> None:
        if self._failed:
            return
        self._failed = True
        log.error(
            "Move writer permanently failed for match %s: %s",
            self._match_id,
            exc,
        )
        if self._on_fatal is None:
            return
        try:
            await self._on_fatal(exc)
        except Exception:
            log.exception("on_fatal failed for match %s", self._match_id)

    async def _persist(self, batch: list[Move]) -> bool:
        delay = _RETRY_INITIAL_SECONDS
        while True:
            try:
                await self._append(self._match_id, batch)
                return True
            except asyncio.CancelledError:
                raise
            except (MatchNotLive, MoveConflict) as exc:
                await self._fail(exc)
                return False
            except Exception:
                log.exception(
                    "Failed to persist %s move(s) for match %s; retrying",
                    len(batch),
                    self._match_id,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RETRY_MAX_SECONDS)

    async def _run(self) -> None:
        while not self._failed:
            first = await self._queue.get()
            batch = [first]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            cancelled = False
            persisted = False
            try:
                persisted = await self._persist(batch)
            except asyncio.CancelledError:
                cancelled = True
                try:
                    await asyncio.wait_for(
                        self._append(self._match_id, batch),
                        timeout=_CANCEL_APPEND_SECONDS,
                    )
                    persisted = True
                except asyncio.CancelledError:
                    pass
                except (MatchNotLive, MoveConflict) as exc:
                    await self._fail(exc)
                except Exception:
                    log.exception(
                        "Failed to persist %s move(s) for match %s on stop",
                        len(batch),
                        self._match_id,
                    )
            if persisted:
                for _ in batch:
                    self._queue.task_done()
            if cancelled:
                raise asyncio.CancelledError
            if not persisted:
                return
