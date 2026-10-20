"""Sequential fixed reads of Core backup state and the runtime holder."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
import time
from typing import Any, Callable

from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_caller_id, current_telemetry
from . import contracts as c
from . import report
from .projection import Projector, project_backups, project_config


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encryption_key(raw: Any, read_complete: bool) -> tuple[bool, str | None]:
    """(key_known, key): the stored key, only so the sanitizer can withhold it.

    The key is known only when the configuration was read and its field is
    present and either null or a string. A failed read, a malformed
    configuration or a missing or invalid field leaves it unknown, and the
    projector then withholds all upstream text (projection.py).
    """

    config = raw.get("config") if read_complete and type(raw) is dict else None
    create = config.get("create_backup") if type(config) is dict else None
    if type(create) is not dict or "password" not in create:
        return False, None
    password = create["password"]
    if password is None:
        return True, None
    if type(password) is str:
        return True, password or None
    return False, None


class BackupInventoryService:
    def __init__(self, client, core_runtime, *, known_secrets: tuple[str, ...] = (),
                 clock: Callable[[], datetime] = utc_now):
        self.client = client
        self.core_runtime = core_runtime
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.clock = clock

    def authority(self):
        status = self.core_runtime.route_status(c.CORE_REQUIREMENTS)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None
                or telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise c.BackupInventoryError("authority_unavailable")
        return status["generation"], observation.version

    async def _read(self, command, source_id: str, authority, sources: list[dict]):
        if self.authority() != authority:
            raise c.BackupInventoryError("authority_unavailable")
        telemetry = current_telemetry()
        attempts_before = telemetry.ha_request_count if telemetry else 0
        before = time.monotonic()
        raw, reason = None, None
        try:
            raw = await self.client.command(dict(command))
        except asyncio.CancelledError:
            raise
        except AuthorizationError:
            reason = "permission_failure"
        except HomeAssistantTimeoutError:
            reason = "timeout"
        except HomeAssistantUnavailableError:
            reason = "home_assistant_unavailable"
        except HomeAssistantApiError:
            reason = "command_failed"
        except Exception:
            # Exception text and provider values never enter the report.
            reason = "source_unavailable"
        finally:
            METRICS.record_provider_result(
                "direct_ha_api", "failed" if reason else "complete",
                dispatched=bool(telemetry and telemetry.ha_request_count > attempts_before),
            )
        sources.append({
            "source_id": source_id,
            "command": command["type"],
            "status": "unavailable" if reason else "complete",
            "reason": reason,
            "duration_ms": round((time.monotonic() - before) * 1000, 3),
        })
        if reason == "permission_failure":
            # Lost admin authority is not partial evidence; it fails closed.
            raise c.BackupInventoryError("permission_failure")
        if self.authority() != authority:
            raise c.BackupInventoryError("authority_unavailable")
        return raw

    async def inventory(self, limit: int) -> dict:
        limit = c.validate_arguments({"limit": limit})
        if current_caller_id() == "anonymous":
            raise c.BackupInventoryError("authority_unavailable")
        authority = self.authority()
        telemetry = current_telemetry()
        prior_error = telemetry.error_code if telemetry else None
        sources: list[dict] = []

        raw_config = await self._read(c.CONFIG_COMMAND, "backup_config", authority, sources)
        key_known, key = _encryption_key(raw_config, sources[-1]["status"] == "complete")
        projector = Projector(known_secrets=self.known_secrets + ((key,) if key else ()),
                              key_known=key_known)
        del key
        config = project_config(projector, raw_config) if sources[-1]["status"] == "complete" else None
        del raw_config

        raw_info = await self._read(c.INFO_COMMAND, "backup_info", authority, sources)
        now = self.clock()
        info = project_backups(projector, raw_info, now) if sources[-1]["status"] == "complete" else None
        del raw_info

        if config is None and info is None:
            raise c.BackupInventoryError("sources_unavailable")
        for source in sources:
            if source["status"] != "complete":
                projector.omit("source_unavailable:" + source["source_id"])
        result = report.build(projector, now=now, limit=limit, config=config, info=info, sources=sources)
        result["observed_at"] = report.iso(now)
        trimmed = 0
        while result["backups"] and len(json.dumps(
                result, separators=(",", ":"), default=str)) > c.RESPONSE_BUDGET:
            result["backups"].pop()
            trimmed += 1
        if trimmed:
            result["completeness"] = "partial"
            result["omissions"].append({"reason": "backups_not_listed_response_bytes", "count": trimmed})
            result["summary"]["listed_count"] = len(result["backups"])
            result["summary"]["more_backups_not_listed"] += trimmed
        if telemetry:
            # A source failure inside a partial report is evidence in
            # ``sources``; it does not turn the response into a failure.
            telemetry.error_code = prior_error
            telemetry.result_status = "partial" if result["completeness"] != "complete" else "success"
            telemetry.completeness = result["completeness"]
            telemetry.audit_context.update(
                provider="engineering", upstream_calls=0, fallback="none",
                source_outcomes=[{"source_id": s["source_id"], "status": s["status"],
                                  "reason": s["reason"]} for s in sources],
                backup_count=result["summary"]["backup_count"],
            )
        return result


class BackupInventoryRuntime:
    """Explicit no-I/O application composition."""

    def __init__(self):
        self.service: BackupInventoryService | None = None

    def configure(self, client, core_runtime, settings):
        self.service = BackupInventoryService(
            client, core_runtime,
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> BackupInventoryService:
        if self.service is None:
            raise c.BackupInventoryError("authority_unavailable")
        return self.service


BACKUP_INVENTORY = BackupInventoryRuntime()
