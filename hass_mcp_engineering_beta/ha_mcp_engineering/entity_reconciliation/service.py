"""Sequential fixed state and registry reads and the runtime holder (HAMCP-132)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Awaitable, Callable

from ..clients.paths import rest_path
from ..entity_ids import STRICT_ENTITY_ID
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..request_context import current_caller_id, current_telemetry
from ..sanitization import sanitize_untrusted_data
from . import contracts as c


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _entity_ids(rows: Any) -> tuple[set[str], int] | None:
    """Entity IDs from a list of objects; malformed rows are only counted."""

    if type(rows) is not list:
        return None
    found, malformed = set(), 0
    for row in rows:
        entity_id = row.get("entity_id") if type(row) is dict else None
        if type(entity_id) is str and STRICT_ENTITY_ID.fullmatch(entity_id):
            found.add(entity_id)
        else:
            malformed += 1
    return found, malformed


def _excess_rows(envelope: dict) -> int:
    """How many trailing entity rows put a sanitized envelope over budget."""

    rows = envelope["entities"]
    size = len(json.dumps(envelope, separators=(",", ":"), default=str))
    excess = 0
    while excess < len(rows) and size > c.RESPONSE_BUDGET:
        # A row and its separating comma leave the serialized envelope.
        size -= len(json.dumps(rows[len(rows) - 1 - excess], separators=(",", ":"), default=str)) + 1
        excess += 1
    return excess


def _platforms(value: Any) -> dict[str, str | None] | None:
    """``entity/source`` rows; an invalid platform value is kept as ``None``."""

    if type(value) is not dict:
        return None
    platforms: dict[str, str | None] = {}
    for entity_id, row in value.items():
        if type(entity_id) is not str or STRICT_ENTITY_ID.fullmatch(entity_id) is None:
            continue
        platform = row.get("domain") if type(row) is dict else None
        platforms[entity_id] = platform if type(platform) is str and c.DOMAIN.fullmatch(platform) else None
    return platforms


def _config_entry(outcome: Any) -> tuple[str | None, str]:
    """(config_entry_id, status) from one ``search/related`` outcome."""

    if type(outcome) is not tuple or len(outcome) != 2:
        return None, "invalid_response"
    ok, value = outcome
    if not ok:
        return None, "command_failed"
    if type(value) is not dict:
        return None, "invalid_response"
    entries = value.get("config_entry", [])
    if type(entries) is not list or not all(type(item) is str and 0 < len(item) <= 64 for item in entries):
        return None, "invalid_response"
    if not entries:
        return None, "none_related"
    if len(entries) > 1:
        # ``search/related`` also returns entries related through other
        # items; more than one is never narrowed to a guess.
        return None, "ambiguous"
    return entries[0], "resolved"


class EntityReconciliationService:
    def __init__(self, rest_client, websocket_client, core_runtime, *,
                 known_secrets: tuple[str, ...] = (), clock: Callable[[], datetime] = utc_now):
        self.rest_client = rest_client
        self.client = websocket_client
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
            raise c.EntityReconciliationError("authority_unavailable")
        return status["generation"], observation.version

    async def _read(self, source: str, read: Callable[[], Awaitable[Any]], authority,
                    sources: list[dict]) -> Any:
        if self.authority() != authority:
            raise c.EntityReconciliationError("authority_unavailable")
        telemetry = current_telemetry()
        attempts_before = telemetry.ha_request_count if telemetry else 0
        raw, reason = None, None
        try:
            raw = await read()
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
        sources.append({"source": source, "status": "unavailable" if reason else "complete",
                        "reason": reason, "observed_at": iso(self.clock())})
        if self.authority() != authority:
            raise c.EntityReconciliationError("authority_unavailable")
        return raw

    def _invalid(self, sources: list[dict]) -> None:
        sources[-1].update(status="unavailable", reason="invalid_response")

    async def report(self, domains: tuple[str, ...] | list[str] | None = None,
                     limit: int = c.DEFAULT_LIMIT) -> dict:
        # A tuple is normalized; any other non-list (a string included) is
        # passed through unchanged so validation refuses it, never splits it.
        domains, limit = c.validate_arguments(
            {"limit": limit} | ({} if domains is None else {
                "domains": list(domains) if type(domains) is tuple else domains}))
        if current_caller_id() == "anonymous":
            raise c.EntityReconciliationError("authority_unavailable")
        authority = self.authority()
        telemetry = current_telemetry()
        prior_error = telemetry.error_code if telemetry else None
        started = self.clock()
        sources: list[dict] = []
        omissions: list[str] = []

        def omit(item: str) -> None:
            if item not in omissions:
                omissions.append(item)

        raw = await self._read("states", lambda: self.rest_client.request("GET", rest_path("/states")),
                               authority, sources)
        states = _entity_ids(raw) if sources[-1]["status"] == "complete" else None
        if sources[-1]["status"] == "complete" and states is None:
            self._invalid(sources)
        raw = await self._read("config/entity_registry/list",
                               lambda: self.client.command(dict(c.REGISTRY_COMMAND)), authority, sources)
        registry = _entity_ids(raw) if sources[-1]["status"] == "complete" else None
        if sources[-1]["status"] == "complete" and registry is None:
            self._invalid(sources)
        del raw
        if states is None or registry is None:
            # Without both inventories no entity can be called unregistered.
            raise c.EntityReconciliationError("sources_unavailable")
        state_ids, malformed_states = states
        registry_ids, malformed_registry = registry
        if malformed_states:
            omit("malformed_state_rows_not_reconciled")
        if malformed_registry:
            # A malformed registry row could hide a registered entity.
            omit("malformed_registry_rows_not_reconciled")

        unregistered = sorted(state_ids - registry_ids)
        in_scope = [entity_id for entity_id in unregistered
                    if domains is None or entity_id.split(".", 1)[0] in domains]
        listed = in_scope[:limit]
        truncated = len(in_scope) > len(listed)
        if truncated:
            omit("entities_not_listed_limit")

        platforms: dict[str, str | None] | None = None
        if listed:
            raw = await self._read("entity/source", lambda: self.client.command(dict(c.SOURCE_COMMAND)),
                                   authority, sources)
            platforms = _platforms(raw) if sources[-1]["status"] == "complete" else None
            if sources[-1]["status"] == "complete" and platforms is None:
                self._invalid(sources)
            del raw
            if platforms is None:
                omit("platform_unknown:entity_source_unavailable")

        searched = [entity_id for entity_id in listed if platforms and platforms.get(entity_id)]
        related: dict[str, tuple[str | None, str]] = {}
        if searched:
            raw = await self._read(
                c.SEARCH_TYPE,
                lambda: self.client.command_batch([c.search_payload(entity_id) for entity_id in searched]),
                authority, sources)
            if sources[-1]["status"] == "complete" and (type(raw) is not list or len(raw) != len(searched)):
                self._invalid(sources)
            if sources[-1]["status"] == "complete":
                related = {entity_id: _config_entry(outcome) for entity_id, outcome in zip(searched, raw)}
                failed = sum(1 for _, status in related.values()
                             if status in {"command_failed", "invalid_response"})
                if failed:
                    sources[-1].update(status="partial" if failed < len(searched) else "unavailable",
                                       reason="per_entity_failures")
            else:
                omit("config_entry_unknown:search_related_unavailable")
            del raw

        entities = []
        for entity_id in listed:
            row: dict[str, Any] = {
                "entity_id": entity_id,
                "state_present": True,
                "registry_present": False,
                "definition_location": "not_determinable",
            }
            if platforms is None:
                row.update(platform=None, platform_status="unknown")
            elif entity_id not in platforms:
                # No ``entity/source`` row: the state was written directly,
                # not added by an integration platform.
                row.update(platform=None, platform_status="directly_written_state")
            elif platforms[entity_id] is None:
                row.update(platform=None, platform_status="invalid_response")
                omit("platform_unknown:invalid_response")
            else:
                row.update(platform=platforms[entity_id], platform_status="entity_source")
            if entity_id in related:
                row["config_entry_id"], row["config_entry_status"] = related[entity_id]
                if row["config_entry_status"] in {"command_failed", "invalid_response"} \
                        and sum(item.startswith("config_entry_unknown:") for item in omissions) \
                        < c.MAX_OMITTED_ENTITY_IDS:
                    # Capped; the row itself still carries the status.
                    omit("config_entry_unknown:" + entity_id)
            elif row["platform_status"] == "directly_written_state":
                row.update(config_entry_id=None, config_entry_status="not_applicable")
            else:
                row.update(config_entry_id=None, config_entry_status="unknown")
            entities.append(row)

        result: dict[str, Any] = {
            "schema": c.SCHEMA,
            "observed_at": iso(started),
            "scope": {"domains": list(domains) if domains is not None else None, "limit": limit},
            "counts": {
                "states": len(state_ids),
                "registry_entries": len(registry_ids),
                "unregistered": len(unregistered),
                "unregistered_in_scope": len(in_scope),
                "listed": len(listed),
                "directly_written_states": sum(1 for row in entities
                                               if row["platform_status"] == "directly_written_state"),
            },
            "truncated": truncated,
            "entities": entities,
            "coverage": {
                "definition_location": "Core records no file or package for an entity without a registry entry",
                "candidate_definitions": "not offered in this release",
            },
            "sources": sources,
        }
        for source in sources:
            if source["status"] != "complete":
                omit(f"source_{source['status']}:{source['source']}")
        result = self._finish(result, omissions, omit)
        if telemetry:
            telemetry.error_code = prior_error
            telemetry.result_status = "success" if result["complete"] else "partial"
            telemetry.completeness = result["completeness"]
            telemetry.audit_context.update(
                provider="engineering", upstream_calls=0, fallback="none",
                source_outcomes=[{"source": s["source"], "status": s["status"],
                                  "reason": s["reason"]} for s in sources],
                entity_count=len(result["entities"]),
            )
        return result

    def _finish(self, result: dict, omissions: list[str], omit: Callable[[str], None]) -> dict:
        """The single exit point: the complete envelope, sanitized once.

        Every field the caller can see, the omissions with their entity IDs
        included, is assembled first and the strict sanitizer runs over the
        whole envelope with the known secrets. Nothing is added afterwards; a
        change (a redaction omission, entities dropped for the byte budget)
        rebuilds the envelope and sanitizes it again.
        """

        while True:
            envelope = {**result, "omissions": list(omissions)}
            envelope["complete"] = not omissions
            envelope["completeness"] = "complete" if envelope["complete"] else "partial"
            sanitized = sanitize_untrusted_data(envelope, known_secrets=self.known_secrets, max_string=255)
            if sanitized.failed_closed:
                raise c.EntityReconciliationError("sources_unavailable")
            changed = False
            if (sanitized.redacted_field_count or sanitized.truncated_field_count) \
                    and "redacted_by_policy" not in omissions:
                # HAMCP-191: a redaction anywhere makes the report partial.
                omit("redacted_by_policy")
                changed = True
            excess = _excess_rows(sanitized.value)
            if excess:
                kept = len(result["entities"]) - excess
                result = {**result, "entities": result["entities"][:kept], "truncated": True,
                          "counts": {**result["counts"], "listed": kept}}
                omit("entities_not_listed_response_bytes")
                changed = True
            if not changed:
                return sanitized.value


class EntityReconciliationRuntime:
    """Explicit no-I/O application composition."""

    def __init__(self):
        self.service: EntityReconciliationService | None = None

    def configure(self, rest_client, websocket_client, core_runtime, settings):
        self.service = EntityReconciliationService(
            rest_client, websocket_client, core_runtime,
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> EntityReconciliationService:
        if self.service is None:
            raise c.EntityReconciliationError("authority_unavailable")
        return self.service


ENTITY_RECONCILIATION = EntityReconciliationRuntime()
