from __future__ import annotations

import base64
from dataclasses import dataclass

import msgpack

from strife.routing.cache import PayloadCache


class PayloadExpired(Exception):
    pass


class CustomIdError(Exception):
    pass


@dataclass(frozen=True)
class Route:
    prefix: str
    resource_id: int
    source: str
    payload: dict


class CustomIdEncoder:
    def __init__(self, cache: PayloadCache, *, limit: int = 100) -> None:
        self._cache = cache
        self._limit = limit

    def encode(self, prefix: str, resource_id: int, source: str, payload: dict | None) -> str:
        body = {"s": source, "p": payload or {}}
        blob = base64.urlsafe_b64encode(msgpack.packb(body)).rstrip(b"=").decode()
        cid = f"{prefix}{resource_id}/{blob}"
        if len(cid) <= self._limit:
            return cid
        token = self._cache.put(msgpack.packb(body))
        return f"{prefix}{resource_id}/~{token}"

    def decode(self, custom_id: str) -> Route:
        if ":" not in custom_id:
            raise CustomIdError("missing prefix")
        prefix, rest = custom_id.split(":", 1)
        prefix = f"{prefix}:"
        rid_str, _, body = rest.partition("/")
        if not rid_str or not body:
            raise CustomIdError("malformed custom_id")
        if body.startswith("~"):
            raw = self._cache.get(body[1:])
            if raw is None:
                raise PayloadExpired()
            data = msgpack.unpackb(raw)
        else:
            pad = "=" * (-len(body) % 4)
            data = msgpack.unpackb(base64.urlsafe_b64decode(body + pad))
        return Route(prefix, int(rid_str), data["s"], data.get("p", {}))
