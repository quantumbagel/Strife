from __future__ import annotations

import base64
import hmac
import hashlib
from dataclasses import dataclass

import msgpack

from strife.routing.cache import PayloadCache

_MSGPACK_OPTS = {
    "strict_map_key": True,
    "max_bin_len": 256,
    "max_str_len": 256,
    "max_array_len": 64,
    "max_map_len": 64,
}


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
    def __init__(
        self,
        cache: PayloadCache,
        *,
        signing_key: bytes,
        limit: int = 100,
    ) -> None:
        self._cache = cache
        self._limit = limit
        self._signing_key = signing_key

    _MAC_BYTES = 16

    def _sign(self, body: bytes) -> str:
        digest = hmac.new(self._signing_key, body, hashlib.sha256).digest()[: self._MAC_BYTES]
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _verify(self, body: bytes, signature: str) -> None:
        pad = "=" * (-len(signature) % 4)
        expected = base64.urlsafe_b64decode(signature + pad)
        actual = hmac.new(self._signing_key, body, hashlib.sha256).digest()[: self._MAC_BYTES]
        if len(expected) != self._MAC_BYTES or not hmac.compare_digest(expected, actual):
            raise CustomIdError("invalid signature")

    def invalidate_resource(self, resource_id: int) -> None:
        invalidate = getattr(self._cache, "invalidate", None)
        if callable(invalidate):
            invalidate(resource_id)

    def _unpack(self, raw: bytes) -> dict:
        return msgpack.unpackb(raw, **_MSGPACK_OPTS)

    def encode(self, prefix: str, resource_id: int, source: str, payload: dict | None) -> str:
        body = {"s": source, "p": payload or {}}
        packed = msgpack.packb(body)
        signature = self._sign(packed)
        blob = base64.urlsafe_b64encode(packed).rstrip(b"=").decode()
        cid = f"{prefix}{resource_id}/{blob}.{signature}"
        if len(cid) <= self._limit:
            return cid
        token = self._cache.put(packed, resource_id=resource_id)
        return f"{prefix}{resource_id}/~{token}.{signature}"

    def decode(self, custom_id: str) -> Route:
        if ":" not in custom_id:
            raise CustomIdError("missing prefix")
        prefix, rest = custom_id.split(":", 1)
        prefix = f"{prefix}:"
        rid_str, _, body = rest.partition("/")
        if not rid_str or not body:
            raise CustomIdError("malformed custom_id")
        if "." not in body:
            raise CustomIdError("missing signature")
        payload_part, signature = body.rsplit(".", 1)
        if payload_part.startswith("~"):
            raw = self._cache.get(payload_part[1:])
            if raw is None:
                raise PayloadExpired()
            self._verify(raw, signature)
            data = self._unpack(raw)
        else:
            pad = "=" * (-len(payload_part) % 4)
            raw = base64.urlsafe_b64decode(payload_part + pad)
            self._verify(raw, signature)
            data = self._unpack(raw)
        return Route(prefix, int(rid_str), data["s"], data.get("p", {}))
