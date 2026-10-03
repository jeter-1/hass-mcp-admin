"""Closed read/serialization contract for on-demand loaded-automation capture."""

import asyncio
import hashlib
import json
import math
import re

from ..errors import ErrorCode, GovernanceError
from .models import FINGERPRINT_MODEL, FINGERPRINT_SERIALIZATION
from .validation import NUMBER_SERIALIZATION, LEGACY_EXCLUDED_FIELDS

SCOPE = "loaded_automation_entities-v1"
IDENTITY_METHOD = "supervised_core_registry_lineage-v1"
CAPABILITY = "core.automation_baseline_metadata_read"
REQUIREMENTS = (CAPABILITY, "core.basic_rest_read", "core.basic_websocket_read",
                "core.non_device_registry_read", "core.direct_device_registry_read",
                "core.automation_configuration_read")
COMMANDS = {
    "principal": {"type": "auth/current_user"},
    "entries": {"type": "config_entries/get", "domain": "hassio"},
    "devices": {"type": "config/device_registry/list"},
    "registry": {"type": "config/entity_registry/list"},
}
FENCE_READS = ("principal", "entries", "devices", "states", "registry")
MAX_RECORDS = 1000
MAX_INVENTORY = 20_000
FRAME_BYTES = 4 * 1024 * 1024
AUTH_BYTES = 65_536
CONFIG_BYTES = 262_144
TOTAL_BYTES = 32 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 64
STRING_BYTES = 16_384
COLLECTION_SECONDS = 45
READ_SECONDS = 5
CONCURRENCY = 4
SNAPSHOT_BYTES = 2 * 1024 * 1024
SNAPSHOT_TTL = 600
MAX_SNAPSHOTS = 2
PAGE_BYTES = 48_000
ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
ENTITY = re.compile(r"automation\.[a-z0-9_]{1,220}\Z")
CURSOR = re.compile(r"[0-9a-f]{32}:[0-9]{1,4}:[0-9a-f]{64}\Z")
REASONS = frozenset({"invalid_arguments", "invalid_cursor", "snapshot_expired", "capacity_busy",
    "authority_unavailable", "authority_drift", "identity_unverified", "identity_drift", "access_denied",
    "source_unavailable", "malformed_response", "response_limit", "structural_limit", "timeout",
    "output_limit", "configuration_unavailable", "configuration_identity_mismatch", "redacted_identity"})


class CaptureError(GovernanceError):
    def __init__(self, reason):
        if reason not in REASONS:
            reason = "source_unavailable"
        code = (ErrorCode.INVALID_REQUEST if reason == "invalid_arguments" else
                ErrorCode.INVALID_CURSOR if reason in {"invalid_cursor", "snapshot_expired"} else
                ErrorCode.PROVIDER_UNAVAILABLE if reason in {"authority_unavailable", "authority_drift"} else
                ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason})
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return self.reason in {"capacity_busy", "timeout", "source_unavailable"}


def validate_arguments(arguments):
    if (type(arguments) is not dict or set(arguments) - {"limit", "cursor"}
            or type(arguments.get("limit", 25)) is not int or not 1 <= arguments.get("limit", 25) <= 100
            or type(arguments.get("cursor", "")) is not str
            or (arguments.get("cursor") and not CURSOR.fullmatch(arguments["cursor"]))):
        raise CaptureError("invalid_arguments")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def bounded(value):
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise CaptureError("structural_limit")
        if type(item) is dict:
            if len(item) + nodes + len(stack) > MAX_NODES:
                raise CaptureError("structural_limit")
            for key, child in item.items():
                if type(key) is not str or len(key.encode("utf-8")) > STRING_BYTES:
                    raise CaptureError("structural_limit")
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) + nodes + len(stack) > MAX_NODES:
                raise CaptureError("structural_limit")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item.encode("utf-8")) > STRING_BYTES:
                raise CaptureError("structural_limit")
        elif type(item) is float:
            if not math.isfinite(item):
                raise CaptureError("malformed_response")
        elif item is not None and type(item) not in (bool, int):
            raise CaptureError("malformed_response")
    return value


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CaptureError("malformed_response")
            result[key] = value
        return result
    def invalid(_):
        raise CaptureError("malformed_response")
    try:
        return bounded(json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise CaptureError("malformed_response") from None


async def worker(function, *args):
    """Drain owned bounded pure work on cancellation; no detached provider work."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


def fingerprint_contract():
    return {"model": FINGERPRINT_MODEL, "algorithm": "sha256", "serialization": FINGERPRINT_SERIALIZATION,
            "input_scope": "configuration_data_object_only", "object_key_order": "lexicographic",
            "array_order": "preserved", "unicode_escaping": "ensure_ascii=true",
            "number_serialization": NUMBER_SERIALIZATION, "non_finite_numbers": "rejected",
            "excluded_fields": list(LEGACY_EXCLUDED_FIELDS), "raw_configuration_persisted": False}


CORE_METADATA_CONTRACT = {
    "commands": list(COMMANDS.values()), "rest": ["GET /states", "GET /config/automation/config/{verified_configuration_id}"],
    "principal_projection": ["is_admin"], "entries_projection": ["entry_id", "domain", "state"],
    "device_projection": ["id", "config_entry_id", "identifiers", "parent_device_id"],
    "scope": SCOPE, "identity": IDENTITY_METHOD, "inventory_is_not_stored_yaml_inventory": True,
    "registry_omission_is_not_absence": True, "rest_states_serialization_failure_refuses_inventory": True, "single_authenticated_ws": True,
    "origin_and_credential": "existing_configured_origin_same_frozen_credential",
    "maximum_records": MAX_RECORDS, "configuration_fingerprint": FINGERPRINT_MODEL,
    "forbidden": ["writes", "fallback", "arbitrary_commands", "raw_config_retention", "secret_retention"],
}
