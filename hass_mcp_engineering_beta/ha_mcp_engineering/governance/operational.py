"""Narrow backup-administration gateway and resumable verification model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Awaitable, Callable

from ..clients.websocket import HomeAssistantWebSocketClient
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..providers.operational_backup import (
    BackupDispatchResult,
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)


BACKUP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,95}$")
MAX_BACKUP_INVENTORY = 200


class OperationalGatewayError(RuntimeError):
    """Bounded operation failure used by governance policy."""

    def __init__(self, category: str, *, dispatched: bool = False) -> None:
        super().__init__("The governed operational request failed.")
        self.category = category
        self.dispatched = dispatched


class BackupAdministrationGateway:
    """Compose one exact write provider with independent HA readback."""

    def __init__(
        self,
        provider: ReviewedOperationalBackupProvider,
        websocket: HomeAssistantWebSocketClient,
    ) -> None:
        self.provider = provider
        self.websocket = websocket

    async def planning_evidence(self) -> dict[str, Any]:
        try:
            provider = await self.provider.probe()
            inventory = await self.read_inventory()
        except OperationalBackupProviderError as exc:
            raise OperationalGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None
        baseline = {
            "inventory_readable": True,
            "inventory_count": inventory["inventory_count"],
            "backup_ids": list(inventory["backup_ids"]),
            "operation_state": inventory["operation_state"],
            "last_action_event": inventory["last_action_event"],
        }
        return {
            "provider": provider.as_dict(),
            "baseline": baseline,
        }

    async def create_full_backup(
        self,
        name: str,
        *,
        before_dispatch: Callable[[], None | Awaitable[None]],
    ) -> BackupDispatchResult:
        try:
            return await self.provider.create_full_backup(
                name, before_dispatch=before_dispatch
            )
        except OperationalBackupProviderError as exc:
            raise OperationalGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None

    async def read_inventory(self) -> dict[str, Any]:
        try:
            raw = await self.websocket.command({"type": "backup/info"})
        except AuthorizationError:
            raise OperationalGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise OperationalGatewayError("verification_timeout") from None
        except HomeAssistantUnavailableError:
            raise OperationalGatewayError("provider_unavailable") from None
        except HomeAssistantApiError:
            raise OperationalGatewayError("verification_failed") from None
        if not isinstance(raw, dict):
            raise OperationalGatewayError("verification_failed")
        backups = raw.get("backups")
        if not isinstance(backups, list) or len(backups) > MAX_BACKUP_INVENTORY:
            raise OperationalGatewayError("verification_failed")
        normalized: list[dict[str, Any]] = []
        for item in backups:
            safe = _normalize_backup(item)
            if safe is None:
                raise OperationalGatewayError("verification_failed")
            normalized.append(safe)
        normalized.sort(key=lambda item: item["backup_id"])
        state = raw.get("state")
        event = raw.get("last_action_event")
        if state is not None and not isinstance(state, str):
            raise OperationalGatewayError("verification_failed")
        safe_event = _normalize_event(event)
        if event is not None and safe_event is None:
            raise OperationalGatewayError("verification_failed")
        return {
            "inventory_readable": True,
            "inventory_count": len(normalized),
            "backup_ids": [item["backup_id"] for item in normalized],
            "backups": normalized,
            "operation_state": state,
            "last_action_event": safe_event,
        }

    async def verify_full_backup(
        self,
        *,
        requested_name: str,
        baseline_ids: list[str],
        apply_started_at: str,
        backup_id: str | None,
        operation_id: str | None,
    ) -> dict[str, Any]:
        inventory = await self.read_inventory()
        backups = inventory["backups"]
        baseline = set(baseline_ids)
        candidates = [
            item
            for item in backups
            if item["backup_id"] not in baseline
            and item["name"] == requested_name
            and _date_in_apply_window(item.get("date"), apply_started_at)
        ]
        if backup_id:
            candidates = [
                item for item in candidates if item["backup_id"] == backup_id
            ]
        if len(candidates) != 1:
            event = inventory.get("last_action_event") or {}
            completed = (
                inventory.get("operation_state") == "idle"
                and event.get("state") == "completed"
            )
            return {
                "status": (
                    "failed"
                    if completed or candidates
                    else "pending"
                ),
                "operation_completed": False,
                "inventory_readable": True,
                "mismatch_fields": [
                    "backup_missing" if not candidates else "backup_ambiguous"
                ],
                "evidence": {
                    "provider_operation_id": operation_id,
                    "provider_backup_id": backup_id,
                    "matching_backup_count": len(candidates),
                },
            }
        backup = candidates[0]
        event = inventory.get("last_action_event") or {}
        event_state = event.get("state")
        operation_state = inventory.get("operation_state")
        if event_state == "failed":
            return {
                "status": "failed",
                "operation_completed": False,
                "inventory_readable": True,
                "mismatch_fields": ["operation_failed"],
                "evidence": {
                    "backup_id": backup["backup_id"],
                    "provider_operation_id": operation_id,
                    "operation_state": operation_state,
                    "last_action_state": event_state,
                },
            }
        if operation_state != "idle" or event_state != "completed":
            return {
                "status": "pending",
                "operation_completed": False,
                "inventory_readable": True,
                "mismatch_fields": ["operation_not_completed"],
                "evidence": {
                    "backup_id": backup["backup_id"],
                    "provider_operation_id": operation_id,
                    "operation_state": operation_state,
                    "last_action_state": event_state,
                },
            }
        event_backup_id = event.get("backup_id")
        if event_backup_id and event_backup_id != backup["backup_id"]:
            return {
                "status": "failed",
                "operation_completed": False,
                "inventory_readable": True,
                "mismatch_fields": ["last_action_backup_id"],
                "evidence": {
                    "backup_id": backup["backup_id"],
                    "provider_operation_id": operation_id,
                    "operation_state": operation_state,
                    "last_action_state": event_state,
                },
            }
        size = backup.get("size_bytes")
        if size is not None and size <= 0:
            return {
                "status": "failed",
                "operation_completed": True,
                "inventory_readable": True,
                "mismatch_fields": ["backup_size"],
                "evidence": {
                    "backup_id": backup["backup_id"],
                    "provider_operation_id": operation_id,
                    "size_bytes": size,
                },
            }
        return {
            "status": "verified",
            "operation_completed": True,
            "inventory_readable": True,
            "mismatch_fields": [],
            "evidence": {
                "backup_id": backup["backup_id"],
                "provider_operation_id": operation_id,
                "name": backup["name"],
                "date": backup.get("date"),
                "size_bytes": size,
                "operation_state": operation_state,
                "last_action_state": event_state,
                "new_relative_to_baseline": True,
                "archive_integrity_validated": False,
            },
        }

    def health_snapshot(self) -> dict[str, Any]:
        return self.provider.health_snapshot()


def normalize_backup_name(value: Any, *, generated_at: datetime) -> str:
    if value is None or value == "":
        return f"Engineering_Backup_{generated_at.astimezone(timezone.utc):%Y-%m-%d_%H-%M-%S}"
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("backup name must be a trimmed string")
    if not BACKUP_NAME.fullmatch(value):
        raise ValueError(
            "backup name must be 1-96 safe letters, numbers, spaces, dots, underscores, or hyphens"
        )
    if ".." in value or any(ord(character) < 32 for character in value):
        raise ValueError("backup name contains a prohibited sequence")
    return value


def _normalize_backup(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    backup_id = value.get("backup_id")
    name = value.get("name")
    if (
        not isinstance(backup_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", backup_id)
        or not isinstance(name, str)
        or not 1 <= len(name) <= 160
    ):
        return None
    result: dict[str, Any] = {"backup_id": backup_id, "name": name}
    date = value.get("date")
    if date is not None:
        if not isinstance(date, str) or len(date) > 64:
            return None
        result["date"] = date
    agents = value.get("agents")
    if isinstance(agents, dict):
        sizes = [
            item.get("size")
            for item in agents.values()
            if isinstance(item, dict)
            and isinstance(item.get("size"), int)
            and not isinstance(item.get("size"), bool)
        ]
        if sizes:
            result["size_bytes"] = max(sizes)
    return result


def _normalize_event(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    state = value.get("state")
    if state is not None:
        if state not in {"completed", "failed", "in_progress"}:
            return None
        result["state"] = state
    backup_id = value.get("backup_id")
    if backup_id is not None:
        if not isinstance(backup_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,160}", backup_id
        ):
            return None
        result["backup_id"] = backup_id
    return result


def _date_in_apply_window(value: Any, started_at: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (
        started - timedelta(seconds=10)
        <= created
        <= started + timedelta(hours=36)
    )
