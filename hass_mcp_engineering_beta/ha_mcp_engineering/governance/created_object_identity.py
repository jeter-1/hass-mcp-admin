"""HAMCP-298 creation-time identity of an Engineering-created object.

Rollback of a governed create deletes the created object, so it needs proof,
observed by the forward task under its own locks right after the create
verified, of which Home Assistant object the create produced
(``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md`` §3.2).
Rollback-time observation is never substituted for missing capture.

The identity is kept in a sidecar store keyed by the child execution id and
hash-bound to the child's verified evidence; the closed
``ExecutionRecord.evidence`` schema is unchanged, so a record written by this
build loads in an older runtime and an older record simply has no identity
(``created_object_identity_not_captured``).

This build captures automation and script creates through the reviewed
HAMCP-155 delete reads (``core.configuration_delete_operation``); helper and
scene captures belong to their own dormant profiles and are not captured
yet, so those creates stay ineligible.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Awaitable, Callable

from .normalize import stable_hash
from .resources import resource_fingerprint

MODEL = "hamcp298-created-identity-v1"
STORE_MODEL = "hamcp298-created-identity-store-v1"
CAPTURED = "captured"
NOT_OBSERVED = "registry_entry_not_observed_within_bound"
IDENTITY_MISMATCH = "identity_mismatch"
CAPABILITY_UNADMITTED = "capability_unadmitted"
READ_FAILED = "read_failed"
OUTCOMES = frozenset(
    {CAPTURED, NOT_OBSERVED, IDENTITY_MISMATCH, CAPABILITY_UNADMITTED, READ_FAILED}
)
# The HAMCP-155 absence bound: 6 attempts at 0.5 s.
POLL_ATTEMPTS = 6
POLL_INTERVAL_SECONDS = 0.5
CAPTURED_TYPES = ("automation", "script")
REGISTRY = {
    "automation": ("automation", "automation"),
    "script": ("script", "script"),
}
IDENTITY_FIELDS = frozenset(
    {
        "model",
        "resource_type",
        "resource_target",
        "storage_object_id",
        "registry_domain",
        "registry_platform",
        "registry_unique_id",
        "registry_entry_id",
        "entity_id",
        "registry_created_at",
        "registry_modified_at",
        "delete_argument",
        "created_fingerprint",
        "capture_fingerprint",
    }
)
STORE_FIELDS = frozenset(
    {
        "model",
        "child_id",
        "plan_id",
        "operation_id",
        "resulting_state_fingerprint",
        "outcome",
        "identity",
        "record_hash",
    }
)
MAX_TIMESTAMP_LENGTH = 64


class IdentityMismatch(ValueError):
    """A registry entry or body does not correspond to the created object."""


def _timestamp(value: Any) -> str:
    # Core returns float epoch seconds; keep the exact JSON value as text so
    # equality is exact and never rounded.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise IdentityMismatch("registry_timestamp_malformed")
    text = json.dumps(value) if not isinstance(value, str) else value
    if not 1 <= len(text) <= MAX_TIMESTAMP_LENGTH:
        raise IdentityMismatch("registry_timestamp_malformed")
    return text


def delete_argument(resource_type: str, target_id: str) -> dict[str, str]:
    if resource_type not in CAPTURED_TYPES:
        raise IdentityMismatch("resource_type_not_captured")
    return {"method": "DELETE", "path": f"/api/config/{resource_type}/config/{target_id}"}


def identity_from(
    resource_type: str,
    target_id: str,
    entry: dict[str, Any],
    created_fingerprint: str,
) -> dict[str, Any]:
    """Build the closed identity, enforcing the §3.2.2 correspondence checks."""

    if resource_type not in CAPTURED_TYPES:
        raise IdentityMismatch("resource_type_not_captured")
    domain, platform = REGISTRY[resource_type]
    entity_id = entry.get("entity_id")
    entry_id = entry.get("id")
    if entry.get("platform") != platform or entry.get("unique_id") != target_id:
        raise IdentityMismatch("registry_entry_not_corresponding")
    if not isinstance(entity_id, str) or not entity_id.startswith(f"{domain}."):
        raise IdentityMismatch("registry_entity_id_domain")
    if not isinstance(entry_id, str) or not entry_id:
        raise IdentityMismatch("registry_entry_id_missing")
    identity = {
        "model": MODEL,
        "resource_type": resource_type,
        "resource_target": target_id,
        "storage_object_id": target_id,
        "registry_domain": domain,
        "registry_platform": platform,
        "registry_unique_id": target_id,
        "registry_entry_id": entry_id,
        "entity_id": entity_id,
        "registry_created_at": _timestamp(entry.get("created_at")),
        "registry_modified_at": _timestamp(entry.get("modified_at")),
        "delete_argument": delete_argument(resource_type, target_id),
        "created_fingerprint": created_fingerprint,
    }
    identity["capture_fingerprint"] = capture_fingerprint(identity)
    return identity


def capture_fingerprint(identity: dict[str, Any]) -> str:
    return stable_hash(
        {key: identity[key] for key in sorted(IDENTITY_FIELDS - {"capture_fingerprint"})}
    )


def validate_identity(identity: Any) -> dict[str, Any]:
    """Return a stored identity only if it is closed, self-consistent and bound."""

    if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS:
        raise IdentityMismatch("identity_shape")
    if identity["model"] != MODEL:
        raise IdentityMismatch("identity_model")
    if identity["capture_fingerprint"] != capture_fingerprint(identity):
        raise IdentityMismatch("identity_fingerprint")
    resource_type = identity["resource_type"]
    target = identity["resource_target"]
    rebuilt = identity_from(
        resource_type,
        target,
        {
            "platform": identity["registry_platform"],
            "unique_id": identity["registry_unique_id"],
            "entity_id": identity["entity_id"],
            "id": identity["registry_entry_id"],
            "created_at": identity["registry_created_at"],
            "modified_at": identity["registry_modified_at"],
        },
        identity["created_fingerprint"],
    )
    if rebuilt != identity or identity["storage_object_id"] != target:
        raise IdentityMismatch("identity_correspondence")
    return identity


async def capture(
    gateway: Any,
    resource_type: str,
    target_id: str,
    created_fingerprint: str,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> tuple[str, dict[str, Any] | None]:
    """Observe the created object's identity within the bounded poll.

    Never raises for an observation problem: the outcome names it and the
    create's own verified outcome is unchanged.
    """

    if resource_type not in CAPTURED_TYPES:
        return IDENTITY_MISMATCH, None
    entry = None
    try:
        for attempt in range(POLL_ATTEMPTS):
            entry = await gateway.registry_entry(resource_type, target_id)
            if entry is not None:
                break
            if attempt + 1 < POLL_ATTEMPTS:
                await sleep(POLL_INTERVAL_SECONDS)
        if entry is None:
            return NOT_OBSERVED, None
        body = await gateway.read_config(resource_type, target_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        return READ_FAILED, None
    if body is None or resource_fingerprint(resource_type, body) != created_fingerprint:
        return IDENTITY_MISMATCH, None
    try:
        return CAPTURED, identity_from(resource_type, target_id, entry, created_fingerprint)
    except IdentityMismatch:
        return IDENTITY_MISMATCH, None


def observed_ownership(
    identity: dict[str, Any],
    entry: dict[str, Any] | None,
    body_fingerprint: str | None,
) -> dict[str, Any]:
    """Classify the current object against the captured identity (§3.4.2).

    ``status`` is ``owned`` (all equal), ``modified_since_create`` (option E:
    only ``modified_at`` changed), or a refusal reason.  Never "proven".
    """

    if entry is None or body_fingerprint is None:
        return {"status": "created_object_already_absent"}
    if body_fingerprint != identity["created_fingerprint"]:
        return {"status": "created_object_changed"}
    try:
        observed = identity_from(
            identity["resource_type"], identity["resource_target"], entry,
            identity["created_fingerprint"],
        )
    except IdentityMismatch:
        return {"status": "created_object_identity_changed"}
    if (
        observed["registry_entry_id"] != identity["registry_entry_id"]
        or observed["registry_created_at"] != identity["registry_created_at"]
    ):
        return {"status": "created_object_identity_changed"}
    result = {
        "status": "owned",
        "observed_modified_at": observed["registry_modified_at"],
        "observed_entity_id": observed["entity_id"],
    }
    if observed["registry_modified_at"] != identity["registry_modified_at"]:
        result["status"] = "modified_since_create"
    return result


def record_hash(value: dict[str, Any]) -> str:
    return stable_hash({key: value[key] for key in sorted(STORE_FIELDS - {"record_hash"})})


class CreatedIdentityStore:
    """Sidecar of creation identities; one atomic file per child execution."""

    def __init__(self, storage_root: str | os.PathLike[str]) -> None:
        self.root = Path(storage_root) / "created_identity"
        self._lock = threading.Lock()

    def _path(self, child_id: str) -> Path:
        name = hashlib.sha256(child_id.encode("utf-8")).hexdigest()
        return self.root / f"{name}.json"

    def put(
        self,
        *,
        child_id: str,
        plan_id: str,
        operation_id: str,
        resulting_state_fingerprint: str,
        outcome: str,
        identity: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if outcome not in OUTCOMES or (outcome == CAPTURED) != (identity is not None):
            raise ValueError("created identity outcome and identity disagree")
        if identity is not None:
            validate_identity(identity)
        value = {
            "model": STORE_MODEL,
            "child_id": child_id,
            "plan_id": plan_id,
            "operation_id": operation_id,
            "resulting_state_fingerprint": resulting_state_fingerprint,
            "outcome": outcome,
            "identity": identity,
        }
        value["record_hash"] = record_hash(value)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(child_id)
            if path.exists():
                # First capture wins; a capture is never replaced.
                existing = self.get(child_id)
                if existing is not None:
                    return existing
            temporary = path.with_name(
                f".identity.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
            )
            try:
                with open(temporary, "x", encoding="utf-8") as handle:
                    handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return value

    def get(self, child_id: str) -> dict[str, Any] | None:
        """Return the stored value only if it is closed and hash-consistent."""

        try:
            value = json.loads(self._path(child_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (
            not isinstance(value, dict)
            or set(value) != STORE_FIELDS
            or value["model"] != STORE_MODEL
            or value["child_id"] != child_id
            or value["outcome"] not in OUTCOMES
            or value["record_hash"] != record_hash(value)
        ):
            return None
        if value["outcome"] == CAPTURED:
            try:
                validate_identity(value["identity"])
            except IdentityMismatch:
                return None
        elif value["identity"] is not None:
            return None
        return value

    def captured_for(
        self,
        *,
        child_id: str,
        plan_id: str,
        operation_id: str,
        resulting_state_fingerprint: Any,
    ) -> dict[str, Any] | None:
        """The identity bound to exactly this verified child, else ``None``."""

        value = self.get(child_id)
        if (
            value is None
            or value["outcome"] != CAPTURED
            or value["plan_id"] != plan_id
            or value["operation_id"] != operation_id
            or value["resulting_state_fingerprint"] != resulting_state_fingerprint
            or value["identity"]["created_fingerprint"] != resulting_state_fingerprint
        ):
            return None
        return value["identity"]
