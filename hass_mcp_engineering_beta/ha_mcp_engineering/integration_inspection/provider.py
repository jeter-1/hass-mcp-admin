"""Sequential native evidence collection and configuration-only joins."""

import asyncio
from datetime import datetime, timedelta, timezone
import time
import uuid

from ..request_context import current_telemetry
from ..observability import METRICS
from . import contracts as c
from .models import InspectionError, Source
from .projection import Projection, Projector


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AlarmoProvider:
    def __init__(self, client, core_runtime, *, known_secrets=()):
        self.client = client
        self.core_runtime = core_runtime
        self.known_secrets = known_secrets

    def authority(self):
        status = self.core_runtime.route_status(c.CORE_REQUIREMENTS)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None
                or telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise InspectionError("authority_unavailable")
        return status["generation"], observation.version

    async def collect(self, target):
        authority = self.authority()
        projector = Projector(known_secrets=self.known_secrets)
        if projector.identifier(target, entity=True) is None:
            raise InspectionError("redacted_by_policy")
        started, started_at = time.monotonic(), utc_now()
        projections, sources = {}, []
        consumed = 0
        commands = 0

        async def read(kind, *, entity_ids=None, final=False):
            nonlocal consumed, commands
            if self.authority() != authority:
                raise InspectionError("identity_drift")
            source_id = kind.value + ("_final" if final else "")
            request_id = uuid.uuid4().hex
            before = time.monotonic()
            p = None
            reason = None
            telemetry = current_telemetry()
            attempts_before = telemetry.ha_request_count if telemetry else 0
            try:
                remaining = c.COLLECTION_SECONDS - (before - started)
                if remaining <= 0:
                    raise InspectionError("timeout")
                if consumed >= c.COLLECTION_BYTES:
                    raise InspectionError("response_bytes")
                if commands >= c.MAX_COMMANDS:
                    raise InspectionError("record_limit")
                commands += 1
                raw, size = await self.client.read_alarmo_inspection(
                    kind, core_version=authority[1], remaining_seconds=remaining,
                    remaining_bytes=c.COLLECTION_BYTES - consumed, entity_ids=entity_ids,
                )
                consumed += size
                if self.authority() != authority:
                    raise InspectionError("identity_drift")
                p = projector.project(kind, raw, source_id)
                del raw
                if time.monotonic() - started >= c.COLLECTION_SECONDS:
                    raise InspectionError("timeout")
            except InspectionError as exc:
                if exc.reason in ("authority_unavailable", "identity_drift", "access_denied"):
                    raise
                # A failed frame may have consumed the whole remaining frame
                # allowance. Charge conservatively; never retry that source.
                consumed = min(c.COLLECTION_BYTES, consumed + c.FRAME_BYTES + 2 * c.AUTH_BYTES)
                reason = exc.reason
                p = Projection(kind, source_id, None, available=False, omitted=None)
                p.gap(reason)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Exception text/provider values never enter evidence.
                reason = "source_unavailable"
                p = Projection(kind, source_id, None, available=False, omitted=None)
                p.gap(reason)
            finally:
                METRICS.record_provider_result(
                    "direct_ha_api", "failed" if p is None or not p.available else "partial" if p.gap_count else "complete",
                    dispatched=bool(telemetry and telemetry.ha_request_count > attempts_before),
                )
            source = Source(
                source_id=source_id, kind=kind.value, request_id=request_id, captured_at=utc_now(),
                duration_ms=round((time.monotonic() - before) * 1000, 3),
                status="unavailable" if not p.available else "partial" if p.gap_count else "complete",
                records_observed=p.observed, records_retained=p.retained, omitted_count=p.omitted,
                count_precision="unknown" if p.observed is None else "lower_bound" if p.gap_count else "exact",
                truncated=p.truncated, reason=reason or (p.gaps[0].reason if p.gaps else None),
                projection_sha256=c.digest(p.value) if p.available else None,
            )
            sources.append(source.model_dump())
            projections[source_id] = p
            return p

        manifest = await read(c.ReadKind.MANIFEST)
        if not manifest.available or manifest.value.get("domain") != "alarmo":
            raise InspectionError("integration_identity_unverified")
        if manifest.value.get("version") != c.ALARMO_VERSION:
            raise InspectionError("integration_version_unsupported")
        entries = await read(c.ReadKind.CONFIG_ENTRIES)
        if not entries.available or entries.gap_count:
            raise InspectionError("integration_identity_unverified")
        if len(entries.value) != 1:
            raise InspectionError("integration_not_installed" if not entries.value else "ambiguous_target")
        entry = entries.value[0]
        if entry["state"] != "loaded":
            raise InspectionError("integration_not_loaded")
        entities = await read(c.ReadKind.ALARM_ENTITIES)
        if not entities.available or entities.gap_count:
            raise InspectionError("integration_identity_unverified")
        matches = [item for item in entities.value if item["entity_id"] == target]
        if len(matches) != 1:
            raise InspectionError("target_not_found" if not matches else "ambiguous_target")
        area = matches[0]["area_id"]
        scope = {"kind": "master"} if type(area) is int and area == 0 else {"kind": "area", "id": area}
        areas = await read(c.ReadKind.AREAS)
        sensors = await read(c.ReadKind.SENSORS)
        general = await read(c.ReadKind.GENERAL)
        groups = await read(c.ReadKind.SENSOR_GROUPS)
        area_ids = sorted((areas.value or {}).keys()) if scope["kind"] == "master" else [area]
        chosen, unresolved = [], []
        for entity_id, row in (sensors.value or {}).items():
            if row.get("area") not in (areas.value or {}):
                unresolved.append(entity_id)
            elif scope["kind"] == "master" or row["area"] == area:
                chosen.append(entity_id)
        chosen, unresolved = sorted(chosen), sorted(unresolved)
        registry_ids = tuple(sorted({target, *chosen, *unresolved}))
        registry = await read(c.ReadKind.ENTITY_REGISTRY, entity_ids=registry_ids)
        final_manifest = await read(c.ReadKind.MANIFEST, final=True)
        if final_manifest.available and final_manifest.value != manifest.value:
            raise InspectionError("identity_drift")
        if self.authority() != authority:
            raise InspectionError("identity_drift")

        records, extra_gaps = build_records(projections, area_ids, chosen, unresolved)
        scope_complete = all(area_id in (areas.value or {}) for area_id in area_ids)
        if not scope_complete:
            extra_gaps.append({"reason": "scope_incomplete", "source_id": "areas", "pointer": None})
        gaps = [gap.model_dump() for p in projections.values() for gap in p.gaps] + extra_gaps
        all_gap_count = sum(p.gap_count for p in projections.values()) + len(extra_gaps)
        bracket = "match" if final_manifest.available and not final_manifest.gap_count else "unavailable"
        if bracket != "match":
            gaps.append({"reason": "freshness_unverified", "source_id": "manifest_final", "pointer": None})
            all_gap_count += 1
        membership_complete = sensors.available and not sensors.gap_count and areas.available and not areas.gap_count and not unresolved and scope_complete
        retained = len(chosen) + len(unresolved)
        membership = {
            "outcome": "unavailable" if not sensors.available else "partial" if not membership_complete else "present" if retained else "empty",
            "configured_members_observed": sensors.observed,
            "configured_members_retained": retained,
            "total_in_scope": retained if membership_complete else None,
            "count_precision": "exact" if membership_complete else "lower_bound" if retained else "unknown",
        }
        if not records or not any(p.available for p in (areas, sensors, general, groups)):
            raise InspectionError("source_unavailable")
        finished_at = utc_now()
        return {
            "model_version": "integration-inspection-v1", "integration": "alarmo",
            "target": {"alarm_entity_id": target, "config_entry_id": entry["entry_id"], "scope": scope, "area_ids": area_ids},
            "compatibility": {"adapter_id": c.ADAPTER_ID, "adapter_contract_sha256": c.ADAPTER_CONTRACT_SHA256,
                "reviewed_alarmo_source_commit": c.ALARMO_COMMIT, "observed_manifest_version": c.ALARMO_VERSION,
                "artifact_provenance": "not_observed_by_this_tool", "source_binding": "reviewed_release_metadata_match",
                "core_generation": authority[0], "core_profile_ids": ["core_basic_websocket_read_v1", "core_integration_inspection_metadata_read_v1"],
                "core_version_observed": authority[1], "upstream_used": False},
            "assessment": "partial" if all_gap_count else "complete",
            "complete_for": "selected_allowlisted_configuration_fields_only", "membership": membership,
            "records": records, "sources": sources,
            "evidence_entries": [e.model_dump() for p in projections.values() for e in p.entries.values()],
            "privacy": {"policy": "alarmo_allowlist_v1", "never_collected_categories": list(c.NEVER_COLLECTED), "removed_categories": list(c.EXCLUDED)},
            "freshness": {"capture_started_at": started_at, "capture_finished_at": finished_at, "served_at": finished_at,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=c.SNAPSHOT_TTL)).isoformat().replace("+00:00", "Z"),
                "atomic_snapshot": False, "status": "captured" if bracket == "match" else "unverified",
                "manifest_bracket": bracket, "configuration_unchanged_during_capture": "not_established"},
            "pagination": {"requested_limit": 0, "effective_limit": 0, "offset": 0, "returned": 0,
                "total_retained_records": len(records), "omitted_records": None if not membership_complete else 0,
                "has_more": False, "next_cursor": None, "snapshot_fingerprint": ""},
            "gaps": gaps[:c.MAX_GAPS],
            "truncation": {"occurred": any(p.truncated for p in projections.values()) or all_gap_count > c.MAX_GAPS,
                "reasons": sorted({g["reason"] for g in gaps if g["reason"] in {"response_bytes", "structural_budget", "record_limit"}}),
                "omitted_count": None if any(p.omitted is None for p in projections.values()) else sum(p.omitted for p in projections.values()) + max(0, all_gap_count - c.MAX_GAPS)},
            "behavior_verification": "not_performed",
            "routing": {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback_occurred": False},
        }


def build_records(projections, area_ids, chosen, unresolved):
    areas, sensors, groups, general, registry = (projections[k] for k in ("areas", "sensors", "sensor_groups", "general", "entity_registry"))
    records, gaps = [], []

    def fact(source, pointer):
        return source.fact(pointer).model_dump()

    def gap(reason, source=None, pointer=None):
        gaps.append({"reason": reason, "source_id": source, "pointer": pointer})

    records.append({"kind": "general", **{key: fact(general, "/" + key) for key in c.GENERAL_FIELDS},
                    "master_enabled": fact(general, "/master/enabled")})
    mode_facts = {}
    for area in area_ids:
        for mode in sorted(c.MODES):
            prefix = "/" + area + "/modes/" + mode
            enabled = fact(areas, prefix + "/enabled")
            mode_facts[(area, mode)] = enabled
            records.append({"kind": "mode", "area": {"kind": "area", "id": area}, "mode": mode,
                "enabled": enabled, **{key + "_seconds": fact(areas, prefix + "/" + key) for key in ("entry_time", "exit_time", "trigger_time")}})
    members = set(chosen + unresolved)
    group_matches = {entity: [] for entity in members}
    relevant_groups = []
    for key, row in sorted((groups.value or {}).items()):
        listed = row.get("entities")
        if type(listed) is not list:
            gap("group_conflict", "sensor_groups", "/" + key)
            continue
        for entity in members.intersection(listed):
            group_matches[entity].append(key)
        if not members.intersection(listed):
            continue
        outside = None if not sensors.available or sensors.gap_count else any(
            (sensors.value.get(entity) or {}).get("area") not in area_ids for entity in listed)
        relevant_groups.append({"kind": "sensor_group", "group_id": key,
            "configured_members": fact(groups, "/" + key + "/entities"),
            "timeout_seconds": fact(groups, "/" + key + "/timeout"),
            "event_count": fact(groups, "/" + key + "/event_count"),
            "includes_members_outside_target_area": outside})
    sensor_records, unresolved_records = [], []
    for entity in chosen + unresolved:
        row, prefix = sensors.value[entity], "/" + entity
        flags = {key: fact(sensors, prefix + "/" + key) for key in c.SENSOR_FLAGS}
        area_fact = fact(sensors, prefix + "/area")
        area = area_fact["value"]
        if area is not None:
            area_fact["value"] = {"kind": "area", "id": area}
        modes = fact(sensors, prefix + "/modes")
        eligibility = []
        for mode in c.MODES:
            enabled = mode_facts.get((area, mode), fact(areas, "/" + area + "/modes/" + mode + "/enabled") if area else {"value": None, "evidence": []})
            participating = (mode in modes["value"] if modes["value"] is not None else None)
            always = flags["always_on"]["value"]
            selected = True if always is True or participating is True else False if always is False and participating is False else None
            inputs = (flags["enabled"]["value"], enabled["value"], selected)
            result = "excluded" if False in inputs else "eligible" if all(x is True for x in inputs) else "indeterminate"
            refs = [*area_fact["evidence"], *flags["enabled"]["evidence"], *enabled["evidence"], *modes["evidence"], *flags["always_on"]["evidence"]]
            eligibility.append({"mode": mode, "result": result, "status": "inferred", "derivation_rule": "configured_mode_eligibility_v1", "evidence": refs})
        reported = fact(sensors, prefix + "/group")
        matches = group_matches[entity]
        if not groups.available or groups.gap_count:
            reported = {**reported, "status": "unavailable", "value": None, "reason": "source_unavailable"}
        elif len(matches) > 1 or (matches[0] if matches else None) != reported["value"]:
            gap("group_conflict", "sensors", prefix + "/group")
        reg = {"status": "unavailable", "current_entity_id": None, "registry_entry_id": None, "registry_disabled": None, "evidence": []}
        if registry.available and entity in (registry.value or {}):
            observed = registry.value[entity]
            if observed is None:
                reg["status"] = "not_in_registry"
                reg["evidence"] = [{"source_id": "entity_registry", "pointer": prefix}]
            elif observed.get("entity_id") == entity and observed.get("id") and "disabled_by" in observed:
                disabled = registry.fact(prefix + "/disabled_by")
                if disabled.status == "observed":
                    reg = {"status": "resolved", "current_entity_id": entity, "registry_entry_id": observed["id"],
                           "registry_disabled": observed["disabled_by"] is not None,
                           "evidence": [{"source_id": "entity_registry", "pointer": prefix + "/" + key} for key in ("entity_id", "id", "disabled_by")]}
        if reg["status"] == "unavailable":
            gap("registry_incomplete", "entity_registry", prefix)
        record = {"kind": "sensor", "configured_entity_id": entity, "alarmo_area": area_fact, **flags,
            "type": fact(sensors, prefix + "/type"), "configured_modes": modes,
            "auto_bypass_modes": fact(sensors, prefix + "/auto_bypass_modes"),
            "reported_group_id": reported, "registry": reg, "configured_mode_eligibility": eligibility}
        if entity in unresolved:
            unresolved_records.append(record)
            gap("dangling_area", "sensors", prefix + "/area")
        else:
            sensor_records.append(record)
    # Unresolved scope is a separate deterministic tail.
    return [*records, *sensor_records, *relevant_groups, *unresolved_records], gaps
