"""Signed, process-bound continuation cursors (the incident-correlation format).

``base64url(canonical JSON) + "." + base64url(HMAC-SHA256)`` with a random
per-process key: a cursor from another process, a restart or an edited
payload is refused before any request. The payload is not encrypted, so it
holds only positions, timestamps and keyed digests, never caller filter
text or log content. Keyed (not plain) digests keep a cursor from serving
as an offline oracle for guessing log lines.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Callable

from ..errors import ErrorCode, GovernanceError


CURSOR_TTL_SECONDS = 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64(data) != text:
        raise ValueError("cursor encoding is not canonical")
    return data


class CursorCodec:
    def __init__(self, feature: str, *, key: bytes | None = None,
                 clock: Callable[[], float] = time.time):
        self.feature = feature
        self._key = key or secrets.token_bytes(32)
        self._clock = clock

    def digest(self, label: str, data: bytes) -> str:
        """A keyed 128-bit digest; equal input gives equal output in-process."""

        return hmac.new(self._key, label.encode("ascii") + b"\0" + data, hashlib.sha256).hexdigest()[:32]

    def fingerprint(self, value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return self.digest("query", canonical.encode("ascii"))

    def encode(self, payload: dict[str, Any]) -> str:
        body = dict(payload, f=self.feature, x=int(self._clock()) + CURSOR_TTL_SECONDS)
        data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return _b64(data) + "." + _b64(hmac.new(self._key, data, hashlib.sha256).digest())

    def decode(self, cursor: str) -> dict[str, Any]:
        try:
            payload_part, signature_part = cursor.split(".", 1)
            data = _unb64(payload_part)
            signature = _unb64(signature_part)
            if not hmac.compare_digest(signature, hmac.new(self._key, data, hashlib.sha256).digest()):
                raise ValueError("cursor signature mismatch")
            value = json.loads(data.decode("ascii"))
            if type(value) is not dict or value.get("f") != self.feature or type(value.get("x")) is not int:
                raise ValueError("cursor fields are invalid")
        except Exception as exc:
            raise GovernanceError(ErrorCode.INVALID_CURSOR, details={
                "feature": self.feature, "reason": "integrity_or_format_invalid",
            }) from exc
        if value["x"] < self._clock():
            raise GovernanceError(ErrorCode.STALE_CURSOR, details={
                "feature": self.feature, "reason": "cursor_expired",
            })
        return value
