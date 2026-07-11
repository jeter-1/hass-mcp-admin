"""Secret-safe beta audit logging."""

from datetime import datetime, timezone
import json
import os

AUDIT_MAX_BYTES = 5 * 1024 * 1024


def redact(value: str, secret: str) -> str:
    if not secret:
        return value
    return value.replace(secret, "<access_secret>")


class AuditLogger:
    def __init__(self, path: str, access_secret: str):
        self.path = path
        self.access_secret = access_secret

    def write(self, entry: dict) -> None:
        safe = json.loads(redact(json.dumps(entry, default=str), self.access_secret))
        safe = {"ts": datetime.now(timezone.utc).isoformat(), **safe}
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > AUDIT_MAX_BYTES:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, default=str) + "\n")
        except OSError as exc:
            print(f"audit write failed: {exc}", flush=True)
