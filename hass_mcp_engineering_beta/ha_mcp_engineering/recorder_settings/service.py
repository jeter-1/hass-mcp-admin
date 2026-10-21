"""Sequential fixed recorder reads and the runtime holder (HAMCP-132)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Callable

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
from .entity_filter import CORE_PIN
from .projection import Projector, gateway_envelope, packages_status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RecorderSettingsService:
    def __init__(self, client, core_runtime, *, yaml_reader_factory: Callable[[], Any] | None = None,
                 known_secrets: tuple[str, ...] = (), clock: Callable[[], datetime] = utc_now):
        self.client = client
        self.core_runtime = core_runtime
        self.yaml_reader_factory = yaml_reader_factory
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.clock = clock

    def authority(self):
        status = self.core_runtime.route_status(c.CORE_REQUIREMENTS)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None
                or telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise c.RecorderSettingsError("authority_unavailable")
        return status["generation"], observation.version

    def _source(self, sources: list[dict], source: str, status: str, reason: str | None) -> None:
        sources.append({"source": source, "status": status, "reason": reason,
                        "observed_at": iso(self.clock())})

    async def _yaml(self, reader, path: str, authority) -> tuple[dict | None, str | None]:
        if self.authority() != authority:
            raise c.RecorderSettingsError("authority_unavailable")
        if reader is None:
            return None, "yaml_reader_not_admitted"
        try:
            result = await reader.read(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = None
        if self.authority() != authority:
            raise c.RecorderSettingsError("authority_unavailable")
        return gateway_envelope(result, path)

    async def _batch(self, payloads: list[dict], authority) -> tuple[list | None, str | None]:
        if self.authority() != authority:
            raise c.RecorderSettingsError("authority_unavailable")
        telemetry = current_telemetry()
        attempts_before = telemetry.ha_request_count if telemetry else 0
        outcomes, reason = None, None
        try:
            outcomes = await self.client.command_batch(payloads)
            if type(outcomes) is not list or len(outcomes) != len(payloads):
                outcomes, reason = None, "invalid_response"
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
        if self.authority() != authority:
            raise c.RecorderSettingsError("authority_unavailable")
        return outcomes, reason

    async def report(self, entity_ids: tuple[str, ...] | list[str] = ()) -> dict:
        # A tuple is normalized; any other non-list is refused, never split.
        entity_ids = c.validate_arguments(
            {"entity_ids": list(entity_ids) if type(entity_ids) is tuple else entity_ids})
        if current_caller_id() == "anonymous":
            raise c.RecorderSettingsError("authority_unavailable")
        authority = self.authority()
        telemetry = current_telemetry()
        prior_error = telemetry.error_code if telemetry else None
        started = self.clock()
        sources: list[dict] = []
        projector = Projector()

        reader = self.yaml_reader_factory() if self.yaml_reader_factory else None
        yaml_reads = 0
        recorder, recorder_failure = await self._yaml(reader, c.YAML_RECORDER_PATH, authority)
        yaml_reads += reader is not None
        self._source(sources, "configuration_yaml:recorder",
                     "unavailable" if recorder is None else
                     ("complete" if recorder["completeness"] == "complete" else "partial"),
                     recorder_failure or (None if recorder is None or recorder["completeness"] == "complete"
                                          else "include_or_truncation"))

        packages, packages_failure = await self._yaml(reader, c.YAML_PACKAGES_PATH, authority)
        yaml_reads += reader is not None
        core, core_failure = None, None
        if packages is not None and not packages["found"]:
            # Only the node type and whether a ``packages`` key exists are
            # used; nothing else from ``homeassistant`` is kept or returned.
            core, core_failure = await self._yaml(reader, c.YAML_CORE_PATH, authority)
            yaml_reads += 1
        package_state = packages_status(packages, packages_failure, core, core_failure)
        del core
        self._source(sources, "configuration_yaml:homeassistant.packages",
                     "complete" if package_state in {"absent", "present"} else "unavailable",
                     packages_failure or core_failure or (
                         "packages_unreadable" if package_state == "unreadable" else None))

        info, info_failure = await self._batch([dict(c.INFO_COMMAND)], authority)
        info_outcome = info[0] if info else None
        runtime = projector.runtime(info_outcome, info_failure)
        self._source(sources, "recorder/info",
                     "complete" if runtime["status"] == "available" else (
                         "refused" if info_failure == "permission_failure" else "unavailable"),
                     runtime.get("reason"))

        options, options_failure = None, None
        if entity_ids:
            # A separate batch: losing admin authority on this command must
            # not discard the runtime status above.
            options, options_failure = await self._batch(
                [{"type": c.ENTITY_OPTIONS_TYPE, "entity_id": entity_id} for entity_id in entity_ids],
                authority,
            )

        settings = projector.settings(recorder, recorder_failure, package_state,
                                      runtime.get("db_in_default_location"))
        filters, recorder_filter = projector.filter(recorder, recorder_failure, package_state)
        entities = projector.entities(entity_ids, options, options_failure, recorder_filter)
        del recorder
        if entity_ids:
            answered = sum(1 for row in entities if row["core_recorded"] is not None)
            self._source(sources, "recorder/entity_options/get",
                         "refused" if options_failure == "permission_failure" else
                         "unavailable" if answered == 0 else
                         "complete" if answered == len(entity_ids) else "partial",
                         options_failure or (None if answered == len(entity_ids) else
                                             entities[[row["core_recorded"] for row in entities].index(None)]
                                             .get("core_reason")))
        for source in sources:
            if source["status"] != "complete":
                projector.omit(f"source_{source['status']}:{source['source']}")

        result: dict[str, Any] = {
            "schema": c.SCHEMA,
            "observed_at": iso(started),
            "core_pin": CORE_PIN,
            "settings": settings,
            "filters": filters,
            "runtime": runtime,
            "entities": entities,
            "packages": package_state,
            "coverage": {
                "configuration_source": "configuration.yaml on disk, not the loaded configuration",
                "includes_followed": False,
                "packages_read": False,
                "yaml_parse": "ha-mcp YAML 1.2; Home Assistant loads YAML 1.1",
                "entities_requested": len(entity_ids),
                "entities_answered_by_core": sum(1 for row in entities if row["core_recorded"] is not None),
                "yaml_reader": reader.provenance if reader is not None else None,
            },
            "sources": sources,
        }
        sanitized = sanitize_untrusted_data(result, known_secrets=self.known_secrets,
                                            max_string=c.MAX_FILTER_TEXT)
        if sanitized.failed_closed:
            raise c.RecorderSettingsError("sources_unavailable")
        if sanitized.redacted_field_count or sanitized.truncated_field_count:
            projector.omit("redacted_by_policy")
        result = sanitized.value
        if len(json.dumps(result, separators=(",", ":"), default=str)) > c.RESPONSE_BUDGET:
            for part in ("include", "exclude"):
                lists = result["filters"].get(part)
                if type(lists) is dict:
                    result["filters"][part] = {key: {"count": len(value), "listed": False}
                                               for key, value in lists.items()}
            projector.omit("filter_lists_not_listed_response_bytes")
        result["omissions"] = list(projector.omissions)
        result["complete"] = not projector.omissions and all(
            source["status"] == "complete" for source in sources)
        result["completeness"] = "complete" if result["complete"] else "partial"
        if all(source["status"] in {"unavailable", "refused"} for source in sources):
            raise c.RecorderSettingsError("sources_unavailable")
        if telemetry:
            telemetry.error_code = prior_error
            telemetry.result_status = "success" if result["complete"] else "partial"
            telemetry.completeness = result["completeness"]
            telemetry.audit_context.update(
                provider="engineering", upstream_calls=yaml_reads, fallback="none",
                source_outcomes=[{"source": s["source"], "status": s["status"],
                                  "reason": s["reason"]} for s in sources],
                entity_count=len(entity_ids),
            )
        result["upstream_calls"] = yaml_reads
        return result


class RecorderSettingsRuntime:
    """Explicit no-I/O application composition."""

    def __init__(self):
        self.service: RecorderSettingsService | None = None

    def configure(self, client, core_runtime, settings, *, yaml_reader_factory=None):
        self.service = RecorderSettingsService(
            client, core_runtime, yaml_reader_factory=yaml_reader_factory,
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> RecorderSettingsService:
        if self.service is None:
            raise c.RecorderSettingsError("authority_unavailable")
        return self.service


RECORDER_SETTINGS = RecorderSettingsRuntime()
