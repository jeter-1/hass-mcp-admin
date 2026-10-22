"""Sequential fixed definition reads and the runtime holder (HAMCP-132)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Awaitable, Callable, NamedTuple

from ..clients.paths import rest_path
from ..entity_ids import STRICT_ENTITY_ID
from ..entity_reconciliation.contracts import DOMAIN
from ..entity_reconciliation.service import _platforms
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..observability import METRICS
from ..recorder_settings.projection import gateway_envelope, packages_status
from ..request_context import current_caller_id, current_telemetry
from ..sanitization import sanitize_untrusted_data
from . import contracts as c
from . import credentials, diagnosis
from .matching import Matcher
from .naming import SLUGIFY_PIN
from .projection import Definitions, paths_for
from .schemas import CORE_PIN

MAX_ENTRY_ENTITIES = 20
_INTERNAL_KEYS = ("dependencies",)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class _Checked(NamedTuple):
    """A projection whose malformed items were kept as ``invalid_response`` (M4)."""

    value: Any
    invalid: int


def _states(value: Any) -> _Checked | None:
    """entity ID -> ``available`` | ``unavailable`` | ``unknown``; no value is kept.

    A state that is not text is ``invalid_response`` for that entity only.
    """

    if type(value) is not list:
        return None
    states = {}
    invalid = 0
    for row in value:
        entity_id = row.get("entity_id") if type(row) is dict else None
        if type(entity_id) is str and STRICT_ENTITY_ID.fullmatch(entity_id):
            state = row.get("state")
            if type(state) is not str:
                states[entity_id] = diagnosis.INVALID
                invalid += 1
            else:
                states[entity_id] = state if state in {"unavailable", "unknown"} else "available"
    return _Checked(states, invalid)


# Registry identity fields: ``None`` is a legitimate absence; any other value
# that does not conform is malformed (M4).
_IDENTITY_FIELDS = (
    ("platform", lambda value: type(value) is str and DOMAIN.fullmatch(value) is not None),
    ("unique_id", lambda value: type(value) is str),
    ("config_entry_id", lambda value: type(value) is str and diagnosis.ENTRY_ID.fullmatch(value) is not None),
)


def _registry(value: Any) -> _Checked | None:
    """Registry rows by entity ID; a malformed item is kept as ``invalid_response``.

    A row whose platform, unique_id or config_entry_id is present but
    malformed has ``identity`` ``invalid_response``: its identity fields are
    ``None`` and it never takes part in matching, so a malformed
    config_entry_id is not mistaken for an absent one (M4).
    """

    if type(value) is not list:
        return None
    rows = {}
    invalid = 0
    for row in value:
        entity_id = row.get("entity_id") if type(row) is dict else None
        if type(entity_id) is not str or not STRICT_ENTITY_ID.fullmatch(entity_id):
            continue
        identity = {field: row.get(field) for field, _valid in _IDENTITY_FIELDS}
        malformed = any(identity[field] is not None and not valid(identity[field])
                        for field, valid in _IDENTITY_FIELDS)
        if malformed:
            identity = dict.fromkeys(identity)
            invalid += 1
        disabled_by = row.get("disabled_by")
        if disabled_by is not None and type(disabled_by) is not str:
            # Malformed: kept as ``invalid_response``, never a set lookup.
            disabled_by = diagnosis.INVALID
            invalid += 1
        rows[entity_id] = {
            **identity,
            "identity": diagnosis.INVALID if malformed else "valid",
            # A closed value: Core's reason, or ``unrecognized`` when set to anything else.
            "disabled_by": disabled_by if disabled_by in diagnosis.DISABLED_BY
            or disabled_by == diagnosis.INVALID else (None if disabled_by is None else "unrecognized"),
        }
    return _Checked(rows, invalid)


def _entries(value: Any, domain: str, known_secrets: tuple[str, ...] = ()) -> _Checked | None:
    rows = diagnosis.entry_rows(value, domain, known_secrets)
    if rows is None:
        return None
    return _Checked(rows, sum(diagnosis.INVALID in (row["state"], row["disabled_by"]) for row in rows))


def _components(value: Any) -> set[str] | None:
    if type(value) is not dict or type(value.get("components")) is not list:
        return None
    return {item for item in value["components"] if type(item) is str and len(item) <= 129}


class ConfigurationDefinitionsService:
    def __init__(self, rest_client, websocket_client, core_runtime, *,
                 yaml_reader_factory: Callable[[], Any] | None = None,
                 known_secrets: tuple[str, ...] = (), clock: Callable[[], datetime] = utc_now):
        self.rest_client = rest_client
        self.client = websocket_client
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
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        return status["generation"], observation.version

    def _source(self, sources: list[dict], source: str, status: str, reason: str | None) -> None:
        sources.append({"source": source, "status": status, "reason": reason,
                        "observed_at": iso(self.clock())})

    async def _yaml(self, reader, path: str, authority) -> tuple[dict | None, str | None]:
        if self.authority() != authority:
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        if reader is None:
            return None, "yaml_reader_not_admitted"
        try:
            result = await reader.read(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = None
        if self.authority() != authority:
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        return gateway_envelope(result, path)

    async def _read(self, read: Callable[[], Awaitable[Any]], authority) -> tuple[Any, str | None]:
        if self.authority() != authority:
            raise c.ConfigurationDefinitionsError("authority_unavailable")
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
        except HomeAssistantApiError as exc:
            reason = "not_found" if exc.details.get("status") == 404 else "command_failed"
        except Exception:
            # Exception text and provider values never enter the report.
            reason = "source_unavailable"
        finally:
            METRICS.record_provider_result(
                "direct_ha_api", "failed" if reason else "complete",
                dispatched=bool(telemetry and telemetry.ha_request_count > attempts_before),
            )
        if self.authority() != authority:
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        return raw, reason

    async def _projected(self, sources: list[dict], source: str, read, project, authority,
                         *, absent_ok: bool = False):
        raw, reason = await self._read(read, authority)
        value = None
        if reason is None:
            try:
                value = project(raw)
            except (TypeError, ValueError, KeyError, AttributeError):
                # M4: malformed source data is this source's invalid_response,
                # never an abort of the whole report.
                value = None
        invalid = 0
        if isinstance(value, _Checked):
            value, invalid = value
        if reason is None and value is None:
            reason = "invalid_response"
        del raw
        status = "complete" if reason is None or (absent_ok and reason == "not_found") else (
            "refused" if reason == "permission_failure" else "unavailable")
        if reason is None and invalid:
            # The malformed items are kept as invalid_response; the rest is used.
            status, reason = "partial", "invalid_response"
        self._source(sources, source, status, reason)
        return value, reason

    async def report(self, entity_ids: tuple[str, ...] | list[str] = (),
                     integrations: tuple[str, ...] | list[str] = ()) -> dict:
        # A tuple is normalized; any other non-list is refused, never split.
        entity_ids, integrations = c.validate_arguments({
            "entity_ids": list(entity_ids) if type(entity_ids) is tuple else entity_ids,
            "integrations": list(integrations) if type(integrations) is tuple else integrations,
        })
        if current_caller_id() == "anonymous":
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        authority = self.authority()
        telemetry = current_telemetry()
        prior_error = telemetry.error_code if telemetry else None
        started = self.clock()
        sources: list[dict] = []
        omissions: list[str] = []

        def omit(item: str) -> None:
            if item not in omissions:
                omissions.append(item)

        # 1. YAML definitions, then packages, through the admitted reader.
        reader = self.yaml_reader_factory() if self.yaml_reader_factory else None
        yaml_reads = 0
        envelopes: dict[str, dict | None] = {}
        yaml_complete = True
        # Each YAML definition source not read whole (M3): absence is never
        # claimed while one can hold an unread definition.
        yaml_gaps: list[str] = []
        for path in paths_for(entity_ids, integrations):
            envelope, failure = await self._yaml(reader, path, authority)
            yaml_reads += reader is not None
            envelopes[path] = envelope
            complete = envelope is not None and envelope["completeness"] == "complete"
            yaml_complete &= complete
            self._source(sources, f"configuration_yaml:{path}",
                         "unavailable" if envelope is None else "complete" if complete else "partial",
                         failure or (None if complete else "include_or_truncation"))
            if not complete:
                yaml_gaps.append(f"source_{'failed' if envelope is None else 'partial'}:"
                                 f"configuration_yaml:{path}")
        packages, packages_failure = await self._yaml(reader, "homeassistant.packages", authority)
        yaml_reads += reader is not None
        core, core_failure, homeassistant_read = None, None, False
        if packages is not None and not packages["found"]:
            # Only the node type and whether a ``packages`` key exists are
            # used; nothing else from ``homeassistant`` is kept or returned.
            core, core_failure = await self._yaml(reader, "homeassistant", authority)
            yaml_reads += 1
            homeassistant_read = True
        package_state = packages_status(packages, packages_failure, core, core_failure)
        del core, packages
        self._source(sources, "configuration_yaml:homeassistant.packages",
                     "complete" if package_state in {"absent", "present"} else "unavailable",
                     packages_failure or core_failure or (
                         "packages_unreadable" if package_state == "unreadable" else None))
        if package_state != "absent":
            yaml_complete = False
            omit("definitions_in_packages_not_read" if package_state == "present"
                 else "packages_not_determined")
            yaml_gaps.append("definitions_in_packages_not_read" if package_state == "present"
                             else "packages_not_determined")

        collector = Definitions(envelopes)
        definitions = collector.collect()
        del envelopes
        for path in collector.include_paths:
            omit(f"include_not_followed:{path}")
            yaml_complete = False
            yaml_gaps.append(f"include_not_followed:{path}")
        if any(d["include_not_followed"] for d in definitions):
            yaml_complete = False
            omit("definition_values_include_not_followed")
            yaml_gaps.append("definition_values_include_not_followed")
        if any(d["status"] == "blueprint_not_projected" for d in definitions):
            # A blueprint block can define entities this reader never sees
            # (Core template/config.py:343-349 substitutes the blueprint;
            # the blueprint file is not followed), so absence is never claimed.
            yaml_complete = False
            omit("blueprint_definitions_not_projected")
            yaml_gaps.append("blueprint_definitions_not_projected")

        # 2. Core inventories.
        states, _ = await self._projected(
            sources, "states", lambda: self.rest_client.request("GET", rest_path("/states")), _states,
            authority)
        states_read = states is not None
        registry, _ = await self._projected(
            sources, "config/entity_registry/list", lambda: self.client.command(dict(c.REGISTRY_COMMAND)),
            _registry, authority)
        platforms, _ = await self._projected(
            sources, "entity/source", lambda: self.client.command(dict(c.SOURCE_COMMAND)), _platforms,
            authority)
        components, _ = await self._projected(
            sources, "config", lambda: self.rest_client.request("GET", rest_path("/config")), _components,
            authority)

        # 3. Config entries: the requested integrations, the platforms of the
        # requested entities' entry-backed registry rows, and the component.
        domains = list(integrations)
        for entity_id in entity_ids:
            row = (registry or {}).get(entity_id)
            if row and row["config_entry_id"] and row["platform"] in c.INTEGRATIONS \
                    and row["platform"] not in domains:
                domains.append(row["platform"])
        entries: dict[str, list[dict] | None] = {}
        entry_failures: dict[str, str | None] = {}
        for domain in [*sorted(domains), c.COMPONENT_DOMAIN]:
            entries[domain], entry_failures[domain] = await self._projected(
                sources, f"config_entries/get:{domain}",
                lambda domain=domain: self.client.command(c.entries_payload(domain)),
                lambda value, domain=domain: _entries(value, domain, self.known_secrets), authority)
        manifest, manifest_failure = await self._projected(
            sources, f"manifest/get:{c.COMPONENT_DOMAIN}",
            lambda: self.client.command(dict(c.MANIFEST_COMMAND)),
            lambda value: value if type(value) is dict else None, authority, absent_ok=True)

        # 4. Matching and diagnosis.
        ui_domains = {entry["entry_id"]: domain for domain, rows in entries.items()
                      if domain != c.COMPONENT_DOMAIN for entry in rows or []}
        entry_index = {entry["entry_id"]: entry for domain, rows in entries.items()
                       if domain != c.COMPONENT_DOMAIN for entry in rows or []}
        matcher = Matcher(definitions, registry=registry, platforms=platforms, states=states,
                          yaml_complete=yaml_complete, ui_domains=ui_domains)
        by_id = {d["definition_id"]: d for d in definitions}
        entities, wanted = [], []
        for entity_id in entity_ids:
            match = matcher.entity(entity_id)
            legacy = matcher.legacy_candidates(entity_id)
            definition = by_id[match["definition_ids"][0]] \
                if match["method"] in {"exact_unique_id", "heuristic"} else None
            row = registry.get(entity_id) if registry is not None else None
            state = states.get(entity_id) if states is not None else None
            entities.append({
                "entity_id": entity_id,
                "state": None if states is None else state or "absent",
                "registry": None if registry is None else {
                    "present": row is not None,
                    "platform": row["platform"] if row else None,
                    "config_entry_id": row["config_entry_id"] if row else None,
                    "disabled_by": row["disabled_by"] if row else None,
                    "has_unique_id": bool(row and row["unique_id"]),
                    "identity": row["identity"] if row else None,
                },
                "platform": platforms.get(entity_id) if platforms is not None else None,
                "platform_status": "unknown" if platforms is None else (
                    "entity_source" if platforms.get(entity_id) else "absent"),
                "definition": match,
                "legacy_candidates": legacy,
                "diagnosis": diagnosis.entity(
                    entity_id, state=state, states_read=states is not None, registry_row=row,
                    registry_read=registry is not None, match=match, entries=entry_index,
                    definition=definition, components=components,
                    platform=platforms.get(entity_id) if platforms is not None else None,
                    legacy=legacy, states=states, yaml_gaps=yaml_gaps),
            })
            wanted.extend(match["definition_ids"] + legacy)

        listed = [d for d in definitions
                  if d["integration"] in integrations or d["definition_id"] in wanted
                  or (integrations and d["integration"] == "unknown")]
        if len(listed) > c.MAX_DEFINITIONS:
            listed = listed[:c.MAX_DEFINITIONS]
            omit("definitions_not_listed_limit")
        rendered = []
        for definition in listed:
            row = {key: value for key, value in definition.items() if key not in _INTERNAL_KEYS}
            row["expected_entities"] = [
                {**expected, "match": result}
                for expected, result in zip(definition["expected_entities"], matcher.definition(definition))
            ]
            rendered.append(row)

        ui_entries = []
        for domain in integrations:
            for entry in entries.get(domain) or []:
                ui_entries.append({**entry, "readability": "definition_not_readable_without_flow"})
        for entity in entities:
            entry_id = entity["definition"].get("config_entry_id")
            if entry_id in entry_index and not any(item["entry_id"] == entry_id for item in ui_entries):
                ui_entries.append({**entry_index[entry_id], "readability": "definition_not_readable_without_flow"})
        if len(ui_entries) > c.MAX_UI_ENTRIES:
            ui_entries = ui_entries[:c.MAX_UI_ENTRIES]
            omit("ui_entries_not_listed_limit")
        if registry is not None:
            for entry in ui_entries:
                owned = sorted(entity_id for entity_id, row in registry.items()
                               if row["config_entry_id"] == entry["entry_id"])
                entry["entity_ids"] = owned[:MAX_ENTRY_ENTITIES]
                entry["entity_count"] = len(owned)

        capability = diagnosis.capability(
            manifest, manifest_failure, entries.get(c.COMPONENT_DOMAIN),
            entry_failures.get(c.COMPONENT_DOMAIN), components)
        del manifest, registry, platforms, states

        for source in sources:
            if source["status"] != "complete":
                omit(f"source_{source['status']}:{source['source']}")
        if collector.unprojected:
            omit("definitions_not_projected")
        if all(source["status"] in {"unavailable", "refused"} for source in sources):
            raise c.ConfigurationDefinitionsError("sources_unavailable")
        if not states_read and entity_ids:
            omit("entity_states_unknown")

        report: dict[str, Any] = {
            "schema": c.SCHEMA,
            "observed_at": iso(started),
            "core_pin": CORE_PIN,
            "scope": {"entity_ids": list(entity_ids), "integrations": list(integrations)},
            "entities": entities,
            "definitions": rendered,
            "ui_entries": ui_entries,
            "capability": capability,
            "packages": package_state,
            "coverage": {
                "configuration_source": c.PARSE_DISCLOSURE,
                "includes": c.INCLUDE_DISCLOSURE,
                "labelled_keys": c.LABELLED_KEY_DISCLOSURE,
                "ui_definitions": "definition_not_readable_without_flow: Core returns no entry data or options "
                                  "without opening a flow",
                "heuristic_naming": SLUGIFY_PIN,
                "parser_dependent_identity": c.PARSER_DEPENDENCE_DISCLOSURE,
                "yaml_numeric_spelling": c.YAML_NUMERIC_SPELLING_LIMITATION,
                "state_values_copied": False,
                # ``homeassistant`` is read only to tell an absent packages key
                # from an unreadable node; nothing from it is returned.
                "yaml_paths_read": [s["source"].split(":", 1)[1] for s in sources
                                    if s["source"].startswith("configuration_yaml:")]
                                   + (["homeassistant"] if homeassistant_read else []),
                "definitions_found": len(definitions),
                "definitions_listed": len(rendered),
                "definitions_not_projected": dict(sorted(collector.unprojected.items())),
                "yaml_reader": reader.provenance if reader is not None else None,
            },
            "sources": sources,
            "upstream_calls": yaml_reads,
        }
        result = self._finish(report, omissions, omit)
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
        return result

    def _finish(self, report: dict, omissions: list[str], omit: Callable[[str], None]) -> dict:
        """The single exit point: the complete envelope, sanitized once.

        Every field the caller can see, the omissions included, is assembled
        first. Core free text was already shown only through the allowlist
        (``credentials.display_text``). Credential text is withheld from every
        key and string of the whole envelope (``credentials.withhold``:
        titles, entry metadata, reasons, omissions and definitions alike),
        then the strict sanitizer
        runs over it with the known secrets. Nothing is added afterwards; a
        change (a withholding or redaction omission, definitions or entries
        dropped for the byte budget) rebuilds the envelope and screens and
        sanitizes it again.
        """

        while True:
            envelope = {**report, "omissions": list(omissions)}
            envelope["complete"] = not omissions and all(
                source["status"] == "complete" for source in report["sources"])
            envelope["completeness"] = "complete" if envelope["complete"] else "partial"
            envelope, withheld = credentials.withhold(envelope, self.known_secrets)
            sanitized = sanitize_untrusted_data(envelope, known_secrets=self.known_secrets,
                                                max_string=c.MAX_TEXT)
            if sanitized.failed_closed:
                raise c.ConfigurationDefinitionsError("sources_unavailable")
            changed = False
            if withheld and credentials.OMISSION not in omissions:
                # H1: withheld credential text makes the report partial.
                omit(credentials.OMISSION)
                changed = True
            if credentials.contains(envelope, credentials.UNVERIFIABLE_MARKER) \
                    and credentials.UNVERIFIABLE_OMISSION not in omissions:
                # Free text the allowlist did not clear makes the report partial.
                omit(credentials.UNVERIFIABLE_OMISSION)
                changed = True
            if (sanitized.redacted_field_count or sanitized.truncated_field_count) \
                    and "redacted_by_policy" not in omissions:
                # HAMCP-191: a redaction anywhere makes the report partial.
                omit("redacted_by_policy")
                changed = True
            size = len(json.dumps(sanitized.value, separators=(",", ":"), default=str))
            if size > c.RESPONSE_BUDGET:
                # Drop trailing rows by their sanitized sizes, then rebuild and
                # sanitize again: one pass per change, not one per row, so a
                # large report does not hold the event loop.
                for key, omission in (("definitions", "definitions_not_listed_response_bytes"),
                                      ("ui_entries", "ui_entries_not_listed_response_bytes")):
                    if report[key]:
                        kept = report[key][:-_overflow(report[key], sanitized.value.get(key),
                                                       size - c.RESPONSE_BUDGET)]
                        report = {**report, key: kept}
                        if key == "definitions":
                            report["coverage"] = {**report["coverage"], "definitions_listed": len(kept)}
                        omit(omission)
                        break
                else:
                    raise c.ConfigurationDefinitionsError("sources_unavailable")
                changed = True
            if not changed:
                return sanitized.value


def _overflow(rows: list, sanitized_rows: Any, excess: int) -> int:
    """How many trailing rows cover ``excess`` compact JSON characters (at least one)."""

    if type(sanitized_rows) is not list or len(sanitized_rows) != len(rows):
        return 1
    dropped = freed = 0
    for row in reversed(sanitized_rows):
        if freed >= excess:
            break
        freed += len(json.dumps(row, separators=(",", ":"), default=str)) + 1
        dropped += 1
    return max(dropped, 1)


class ConfigurationDefinitionsRuntime:
    """Explicit no-I/O application composition."""

    def __init__(self):
        self.service: ConfigurationDefinitionsService | None = None

    def configure(self, rest_client, websocket_client, core_runtime, settings, *, yaml_reader_factory=None):
        self.service = ConfigurationDefinitionsService(
            rest_client, websocket_client, core_runtime, yaml_reader_factory=yaml_reader_factory,
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> ConfigurationDefinitionsService:
        if self.service is None:
            raise c.ConfigurationDefinitionsError("authority_unavailable")
        return self.service


CONFIGURATION_DEFINITIONS = ConfigurationDefinitionsRuntime()
