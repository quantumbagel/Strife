from __future__ import annotations

import secrets
import time
from typing import Protocol


class PayloadCache(Protocol):
    def put(self, value: bytes, *, ttl: float = 3600) -> str: ...
    def get(self, token: str) -> bytes | None: ...


class InMemoryPayloadCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, float]] = {}

    def put(self, value: bytes, *, ttl: float = 3600) -> str:
        self._sweep()
        token = secrets.token_urlsafe(6)[:8]
        self._store[token] = (value, time.monotonic() + ttl)
        return token

    def get(self, token: str) -> bytes | None:
        self._sweep()
        entry = self._store.get(token)
        if entry is None:
            return None
        value, expires = entry
        if expires < time.monotonic():
            self._store.pop(token, None)
            return None
        return value

    def _sweep(self) -> None:
        now = time.monotonic()
        expired = [token for token, (_, exp) in self._store.items() if exp < now]
        for token in expired:
            self._store.pop(token, None)
