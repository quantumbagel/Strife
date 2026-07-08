from __future__ import annotations

import secrets
import time
from typing import Protocol


class PayloadCache(Protocol):
    def put(self, value: bytes, *, ttl: float = 3600) -> str: ...
    def get(self, token: str) -> bytes | None: ...


class InMemoryPayloadCache:
    _SWEEP_INTERVAL = 30.0

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, float]] = {}
        self._last_sweep = 0.0

    def put(self, value: bytes, *, ttl: float = 3600) -> str:
        self._sweep_if_due()
        token = secrets.token_urlsafe(16)[:16]
        self._store[token] = (value, time.monotonic() + ttl)
        return token

    def get(self, token: str) -> bytes | None:
        self._sweep_if_due()
        entry = self._store.get(token)
        if entry is None:
            return None
        value, expires = entry
        if expires < time.monotonic():
            self._store.pop(token, None)
            return None
        return value

    def _sweep_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < self._SWEEP_INTERVAL:
            return
        self._last_sweep = now
        expired = [token for token, (_, exp) in self._store.items() if exp < now]
        for token in expired:
            self._store.pop(token, None)
