from __future__ import annotations

import secrets
import time
from typing import Protocol


class PayloadCache(Protocol):
    def put(
        self, value: bytes, *, ttl: float = 604800, resource_id: int | None = None
    ) -> str: ...
    def get(self, token: str) -> bytes | None: ...
    def invalidate(self, resource_id: int) -> None: ...


class InMemoryPayloadCache:
    _SWEEP_INTERVAL = 30.0
    _DEFAULT_TTL = 604800.0  # 7 days; teardown should invalidate sooner
    _MAX_ENTRIES = 4096

    def __init__(self, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._store: dict[str, tuple[bytes, float, int | None]] = {}
        self._last_sweep = 0.0
        self._max_entries = max_entries

    def put(
        self, value: bytes, *, ttl: float | None = None, resource_id: int | None = None
    ) -> str:
        self._sweep_if_due()
        self._evict_if_full()
        token = secrets.token_urlsafe(16)[:16]
        lifetime = self._DEFAULT_TTL if ttl is None else ttl
        self._store[token] = (value, time.monotonic() + lifetime, resource_id)
        return token

    def get(self, token: str) -> bytes | None:
        self._sweep_if_due()
        entry = self._store.get(token)
        if entry is None:
            return None
        value, expires, _resource_id = entry
        if expires < time.monotonic():
            self._store.pop(token, None)
            return None
        return value

    def invalidate(self, resource_id: int) -> None:
        stale = [
            token
            for token, (_value, _exp, rid) in self._store.items()
            if rid == resource_id
        ]
        for token in stale:
            self._store.pop(token, None)

    def _evict_if_full(self) -> None:
        overflow = len(self._store) - self._max_entries + 1
        if overflow <= 0:
            return
        oldest = sorted(self._store.items(), key=lambda item: item[1][1])[:overflow]
        for token, _ in oldest:
            self._store.pop(token, None)

    def _sweep_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < self._SWEEP_INTERVAL:
            return
        self._last_sweep = now
        expired = [token for token, (_, exp, _) in self._store.items() if exp < now]
        for token in expired:
            self._store.pop(token, None)
