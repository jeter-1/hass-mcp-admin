"""Bounded native capture; inventory scope and registry-lineage assurance are explicit."""

import asyncio
from collections import Counter
from datetime import datetime, timezone
import time
import uuid

from ..request_context import current_telemetry
from ..sanitization import sanitize_untrusted_data
from . import capture_contracts as c
from .models import BASELINE_SCHEMA, FINGERPRINT_MODEL
from .validation import canonical_configuration_digest, BaselineValidationError, _new_baseline


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Projector:
    def __init__(self, known_secrets=()):
        self.known_secrets = known_secrets

    def identifier(self, value, *, entity=False):
        pattern = c.ENTITY if entity else c.ID
        if type(value) is not str or not pattern.fullmatch(value) or value in {".", ".."}:
            return None
        sanitized = sanitize_untrusted_data(value, known_secrets=self.known_secrets, max_string=256)
        return value if sanitized.value == value and not sanitized.failed_closed else None

    def project(self, kind, value):
        c.bounded(value)
        if kind == "principal":
            if type(value) is not dict or type(value.get("is_admin")) is not bool:
                raise c.CaptureError("malformed_response")
            if value["is_admin"] is not True:
                raise c.CaptureError("access_denied")
            return True
        if type(value) is not list or len(value) > c.MAX_INVENTORY:
            raise c.CaptureError("structural_limit")
        if kind == "entries":
            if len(value) != 1 or type(value[0]) is not dict:
                raise c.CaptureError("identity_unverified")
            row = value[0]
            entry_id = self.identifier(row.get("entry_id"))
            if not entry_id or row.get("domain") != "hassio" or row.get("state") != "loaded":
                raise c.CaptureError("identity_unverified")
            return entry_id
        if kind == "devices":
            matches = []
            for row in value:
                if type(row) is not dict:
                    raise c.CaptureError("malformed_response")
                identifiers = row.get("identifiers")
                if type(identifiers) is list and ["hassio", "core"] in identifiers:
                    device_id = self.identifier(row.get("id"))
                    entry_id = self.identifier(row.get("config_entry_id"))
                    if not device_id or not entry_id or row.get("parent_device_id") is not None:
                        raise c.CaptureError("identity_unverified")
                    matches.append((device_id, entry_id))
            if len(matches) != 1:
                raise c.CaptureError("identity_unverified")
            return matches[0]
        result, seen, invalid = {}, set(), 0
        for row in value:
            if type(row) is not dict or type(row.get("entity_id")) is not str:
                raise c.CaptureError("malformed_response")
            raw_entity = row["entity_id"]
            if not raw_entity.startswith("automation."):
                continue
            entity = self.identifier(raw_entity, entity=True)
            if not entity:
                invalid += 1
                continue
            if entity in seen:
                raise c.CaptureError("malformed_response")
            seen.add(entity)
            if kind == "states":
                attributes = row.get("attributes")
                config_id = self.identifier(attributes.get("id")) if type(attributes) is dict else None
                state = row.get("state")
                result[entity] = {"id": config_id, "state": state if state in ("on", "off", "unknown", "unavailable") else None}
            elif kind == "registry":
                result[entity] = {"id": self.identifier(row.get("unique_id")),
                                  "platform_valid": row.get("platform") == "automation",
                                  "disabled": row.get("disabled_by") is not None}
            else:
                raise c.CaptureError("invalid_arguments")
        return {"records": result, "invalid_count": invalid}

    def hash_configuration(self, config_id, raw):
        if type(raw) is not dict or raw.get("id") != config_id:
            raise c.CaptureError("configuration_identity_mismatch")
        try:
            # Full returned configuration is hashed ephemerally; no projection,
            # interpolation, template evaluation or raw-body retention.
            return canonical_configuration_digest(raw)
        except BaselineValidationError:
            raise c.CaptureError("malformed_response") from None


class BaselineCaptureProvider:
    def __init__(self, client, core_runtime, *, known_secrets=()):
        self.client, self.core_runtime = client, core_runtime
        self.projector = Projector(known_secrets)

    def authority(self):
        status = self.core_runtime.route_status(c.REQUIREMENTS)
        observation = self.core_runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None or telemetry is None
                or telemetry.core_dispatch_authorizer is None or not telemetry.authorize_core_dispatch()):
            raise c.CaptureError("authority_unavailable")
        return status["generation"], observation.version

    def authority_metadata(self, authority):
        health = self.core_runtime.health_projection()
        counters = health.get("counters", {})
        registry = health.get("release_registry") or {}
        def integer(value):
            return value if type(value) is int and value >= 0 else None
        return {"status": "admitted", "home_assistant_core": authority[1], "generation": authority[0],
                "registry_sequence": integer(registry.get("sequence")),
                "compatible_count": integer(health.get("compatible_count")), "fallback_count": 0,
                "verification_failure_count": integer(counters.get("verification_failures")),
                "retirement_count": integer(counters.get("retirements")), "limitations": []}

    async def collect(self):
        authority = self.authority()
        started, started_at = time.monotonic(), now()
        sources, outcomes = [], {}

        def authorize():
            if self.authority() != authority:
                raise c.CaptureError("authority_drift")

        async with self.client.capture(authority[1], authorize) as peer:
            async def fence(label):
                projected = {}
                for kind in c.FENCE_READS:
                    authorize()
                    before = time.monotonic()
                    raw = await peer.read(kind)
                    projected[kind] = await c.worker(self.projector.project, kind, raw)
                    del raw
                    sources.append({"kind": kind, "fence": label, "observed_at": now(),
                                    "provider": "direct_ha_api", "duration_ms": round((time.monotonic()-before)*1000, 3)})
                if projected["devices"][1] != projected["entries"]:
                    raise c.CaptureError("identity_unverified")
                projected["identity"] = c.digest([c.IDENTITY_METHOD, *projected["devices"]])
                return projected

            first = await fence("before")
            observed_at = now()
            states = first["states"]["records"]
            registry = first["registry"]["records"]
            config_counts = Counter(row["id"] for row in states.values() if row["id"])
            records, unmapped = [], []
            # Frozen identity/mapping order; no raw provider object crosses this point.
            for entity, state in sorted(states.items()):
                config_id = state["id"]
                mapped = registry.get(entity)
                if (not config_id or config_counts[config_id] != 1 or not mapped or not mapped["platform_valid"]
                        or mapped["id"] != config_id or mapped["disabled"]):
                    unmapped.append(entity)
                    continue
                if len(records) == c.MAX_RECORDS:
                    continue
                records.append({"configuration_id": config_id, "entity_id": entity, "mapping_status": "verified",
                    "configuration": {"status": "omitted", "digest": None, "fingerprint_model": None,
                        "collected_at": None, "collection_time_status": "unavailable", "provider": "direct_ha_api",
                        "coverage": {"completeness": "unknown", "fallback_occurred": False, "warning_count": 1,
                                     "redacted": False, "truncated": False, "omitted": True}},
                    "enabled_state": {"state": state["state"], "collected_at": observed_at,
                                      "collection_time_status": "observed"}})
            position = 0

            async def collect_records():
                nonlocal position
                while position < len(records):
                    index, position = position, position + 1
                    row = records[index]
                    config_id, config = row["configuration_id"], row["configuration"]
                    if time.monotonic() - started >= c.COLLECTION_SECONDS - 10:
                        outcomes[config_id] = "timeout"
                        continue
                    try:
                        authorize()
                        raw = await peer.configuration(config_id)
                        value = await c.worker(self.projector.hash_configuration, config_id, raw)
                        del raw
                        authorize()
                        config.update(status="readable", digest=value, fingerprint_model=FINGERPRINT_MODEL,
                                      collected_at=now(), collection_time_status="observed")
                        config["coverage"].update(completeness="complete", warning_count=0, omitted=False)
                    except c.CaptureError as exc:
                        if exc.reason in {"authority_unavailable", "authority_drift", "access_denied"}:
                            raise
                        outcomes[config_id] = exc.reason
                        config.update(status="unreadable", collected_at=now(), collection_time_status="observed")
                        config["coverage"].update(completeness="failed", omitted=False)

            # Structured ownership: a failed/cancelled child cancels and drains siblings.
            tasks = [asyncio.create_task(collect_records()) for _ in range(c.CONCURRENCY)]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                last = await fence("after")
                authorize()
            except c.CaptureError as exc:
                if exc.reason in {"authority_unavailable", "authority_drift", "access_denied", "identity_unverified"}:
                    raise
                last = None
            if last and first["identity"] != last["identity"]:
                raise c.CaptureError("identity_drift")
            authorize()
            authority_metadata = self.authority_metadata(authority)
            requests, transport_bytes = peer.requests, peer.consumed

        omitted = len(states) + first["states"]["invalid_count"] - len(records)
        capped = len(states) + first["states"]["invalid_count"] >= c.MAX_RECORDS
        # Registry-only observations are supplemental, not absence authority.
        extras = sorted(set(registry) - set(states))
        identity_established = last is not None
        inventory_drift = ("unknown" if last is None else
                           "none_observed_at_capture_fences" if first["states"] == last["states"] else "detected")
        # Mapping changes also invalidate shared-record certainty, without turning
        # every unrelated registry mutation into an inventory change.
        mapping_drift = bool(last and any(registry.get(e) != last["registry"]["records"].get(e) for e in states))
        if mapping_drift:
            inventory_drift = "detected"
            for record in records:
                record["mapping_status"] = "contradictory"
        baseline = {
            "schema": BASELINE_SCHEMA, "baseline_id": "aab-" + uuid.uuid4().hex, "source_artifact": None,
            "capture": {"started_at": started_at, "ended_at": now(), "non_atomic": True},
            "installation": {"status": "established" if identity_established else "unestablished",
                "installation_id": first["identity"] if identity_established else None, "method": c.IDENTITY_METHOD,
                "limitations": ["registry_lineage_not_core_uuid", "restored_clones_may_share_identity", "recreated_anchor_breaks_continuity"]},
            "inventory": {"scope": c.SCOPE, "discovery_method": "admin_rest_states_with_registry_mapping",
                "completeness": "partial" if omitted or capped else "complete",
                "declared_count": len(states) + first["states"]["invalid_count"], "limit": c.MAX_RECORDS,
                "limit_reached": capped, "omitted_count": omitted,
                "limitations": ["loaded_scope_only_not_stored_yaml", "registry_only_discovery_partial"]},
            "fingerprint_contract": c.fingerprint_contract(), "records": records,
            "consistency": {"inventory_drift": inventory_drift,
                "authority_drift": "none_observed_at_capture_fences" if identity_established else "unknown",
                "limitations": ["non_atomic_changes_and_reversions_may_be_unobserved"]},
            "authority": authority_metadata,
            "limitations": ["blueprint_external_bodies_not_fingerprinted", "membership_removal_is_not_disk_deletion"],
            "structural_assertions": {"source_internal_material_digest_verified": None,
                "configuration_hashes_recomputed": True, "record_count_verified": True,
                "fingerprint_model_assignment": "native_full_configuration_data"},
        }
        await c.worker(_new_baseline, baseline)
        return {"baseline": baseline, "diagnostics": {
            "sources": sources, "provider": "direct_ha_api", "fallback_occurred": False,
            "requests": requests, "transport_bytes": transport_bytes,
            "configuration_incomplete_count": len(outcomes),
            "unmapped_entities": unmapped[:16], "unmapped_count": len(unmapped),
            "registry_only_entities": extras[:16], "registry_only_count": len(extras),
            "registry_only_count_precision": "lower_bound", "record_outcomes": outcomes,
            "final_fence": "complete" if last else "unavailable"}}
