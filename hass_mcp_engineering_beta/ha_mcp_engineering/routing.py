"""Secret-path authentication, routing, rate limiting, and request auditing."""

import json
import time

from .audit import AuditLogger
from .configuration import Settings


class TokenBucket:
    def __init__(self, per_minute: float, burst: float):
        self.rate = per_minute / 60.0
        self.cap = burst
        self.tokens = burst
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class AuthenticatedMcpGateway:
    def __init__(self, app, settings: Settings, audit: AuditLogger):
        self.app = app
        self.settings = settings
        self.audit = audit
        self.prefix = f"/{settings.access_secret}"
        self.clients: dict[str, TokenBucket] = {}
        self.auth_failures: dict[str, TokenBucket] = {}
        self.global_bucket = TokenBucket(
            settings.rate_limit_per_minute * 2, settings.rate_limit_burst * 2
        )

    @staticmethod
    async def _respond(send, status: int, body: bytes) -> None:
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _client_ip(scope) -> str:
        for name, value in scope.get("headers", []):
            if name == b"cf-connecting-ip":
                return value.decode("latin-1")
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _bucket(store: dict, key: str, rate: float, burst: float) -> TokenBucket:
        if key not in store:
            if len(store) > 1000:
                store.clear()
            store[key] = TokenBucket(rate, burst)
        return store[key]

    def _audit_path(self, path: str) -> str:
        return self.audit_path(path)[:64]

    def audit_path(self, path: str) -> str:
        secret = self.settings.access_secret
        return path.replace(secret, "<access_secret>") if secret else path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/health":
            return await self._respond(send, 200, b"ok")

        client_ip = self._client_ip(scope)
        if not path.startswith(self.prefix + "/") and path != self.prefix:
            bucket = self._bucket(self.auth_failures, client_ip, 0.5, 5)
            if not bucket.allow():
                self.audit.write({
                    "event": "auth_failure_throttled",
                    "client_ip": client_ip,
                    "path": self._audit_path(path),
                })
                return await self._respond(send, 429, b"too many requests")
            self.audit.write({
                "event": "auth_failure",
                "client_ip": client_ip,
                "path": self._audit_path(path),
            })
            return await self._respond(send, 404, b"not found")

        client_bucket = self._bucket(
            self.clients,
            client_ip,
            self.settings.rate_limit_per_minute,
            self.settings.rate_limit_burst,
        )
        if not client_bucket.allow() or not self.global_bucket.allow():
            self.audit.write({"event": "rate_limited", "client_ip": client_ip})
            return await self._respond(send, 429, b"rate limited")

        new_receive = receive
        tool_entry = None
        if scope.get("method") == "POST":
            chunks = []
            more = True
            total = 0
            while more:
                message = await receive()
                chunks.append(message)
                total += len(message.get("body", b""))
                more = message.get("more_body", False)
                if total > 2_000_000:
                    break
            body = b"".join(message.get("body", b"") for message in chunks)
            try:
                rpc = json.loads(body)
                if isinstance(rpc, dict) and rpc.get("method") == "tools/call":
                    params = rpc.get("params", {})
                    tool_entry = {
                        "event": "tool_call",
                        "tool": params.get("name"),
                        "client_ip": client_ip,
                    }
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            queue = list(chunks)

            async def replay():
                return queue.pop(0) if queue else await receive()

            new_receive = replay

        forwarded = dict(scope)
        forwarded["path"] = path[len(self.prefix):] or "/"
        if forwarded["path"] == "/mcp":
            forwarded["path"] = "/mcp/"
        if forwarded.get("raw_path"):
            raw_prefix = self.prefix.encode()
            raw_path = forwarded["raw_path"]
            if raw_path.startswith(raw_prefix):
                forwarded["raw_path"] = raw_path[len(raw_prefix):] or b"/"
                if forwarded["raw_path"] == b"/mcp":
                    forwarded["raw_path"] = b"/mcp/"

        status = {}

        async def audited_send(message):
            if message["type"] == "http.response.start":
                status["value"] = message["status"]
            await send(message)

        try:
            await self.app(forwarded, new_receive, audited_send)
        finally:
            if tool_entry:
                tool_entry["http_status"] = status.get("value")
                self.audit.write(tool_entry)
