"""Device-configuration receipts, undo, safety gate and authority (HAMCP-297).

Ordinary runtime control like ``control_helper``: no plan and no approval panel
(B8). Shared ownership, locks, recovery and hold release are the shipped fan
machinery. Additive records (§5.13): ``verified-observations/`` and
``undo-claims/`` under this namespace; no existing record format changes.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import tempfile

from ..f3.models import parse_timestamp, validate_sha256
from ..fan.audit import FanExecutionRepository, request_summary as _request_summary
from ..fan.authority import FanCoreAuthority
from ..fan.contracts import CORE_REQUIREMENTS
from ..fan.service import MAX_RECORDS, FanService
from ..helper_value.safety import CONFIRMATION, CONFIRMATION_SAFETY_CRITICAL
from ..request_context import current_telemetry
from .adapter import (EFFECTS, PROFILE, DeviceConfigurationAdapter, checked_binding,
                      prepare_record)
from .contracts import (FAMILY, PROVIDER, UNIT_CONVERTERS, DeviceConfigurationRefusal,
                        DeviceConfigurationRequest, Refusal, digest, disclosures, identity,
                        service_call, target_kind, verified)
from .gateway import DeviceConfigurationGateway

NAMESPACE = "ordinary-device-configuration-v1"
VERIFIED_MODEL = "device-configuration-verified-observation-v1"
VERIFIED_FIELDS = frozenset({"model", "task_id", "prepared_hash", "identity",
                             "post_fingerprint", "restore", "recorded_at"})
CLAIM_MODEL = "device-configuration-undo-claim-v1"
CLAIM_FIELDS = frozenset({"model", "original_task_id", "original_prepared_hash",
                          "undo_task_id", "undo_prepared_hash", "claimed_at"})
MAX_SIDECAR = 4096
_GATE = contextvars.ContextVar("device_configuration_gate", default=None)
SAFETY_GATE_VALUES = frozenset({
    "not_evaluated_value_already_current", "not_safety_critical",
    "confirmation_required", "acknowledged",
})
CONFIRMATION_REASONS = frozenset({CONFIRMATION_SAFETY_CRITICAL})
OUTCOMES = frozenset({"confirmation_required"})
_REASON = re.compile(r"device_configuration_[a-z_]{1,64}")
# Pre-dispatch terminal outcomes; their first device_configuration_ code is the reason.
_PRE_DISPATCH = frozenset({"failed_pre_dispatch", "preflight_rejected",
                           "provider_unavailable_pre_dispatch", "cancelled_pre_dispatch",
                           "lock_conflict"})


class _ConfirmationRequired(Exception):
    def __init__(self, payload):
        super().__init__("device_configuration_safety_confirmation_required")
        self.payload = payload


class DeviceConfigurationCoreAuthority(FanCoreAuthority):
    """Signed-only: no legacy Core version or unsigned contract exists (§5.3)."""
    family = FAMILY
    legacy_contracts = ()
    trigger = "device_configuration_authority"
    refusal = DeviceConfigurationRefusal

    async def acquire(self, prepared=None, preflight=None):
        await self.runtime.reconcile_once(self.trigger)
        requirements = CORE_REQUIREMENTS + (PROFILE.capability_id,)
        target = getattr(prepared, "target", None)
        binding = getattr(prepared, "core_binding", None)
        if binding is None:
            if not self.runtime.route_status(requirements)["available"]:
                return None
            return self.runtime.acquire(requirements, expected_core_version=None, target=target)
        try:
            binding = checked_binding(binding)
        except ValueError:
            return None
        return self.runtime.acquire(requirements, expected_core_version=binding["version"],
                                    target=target)

    async def read(self, callback, prepared=None):
        authority = await self.acquire(prepared)
        if authority is None:
            raise self.refusal("device_configuration_core_authority_unavailable")
        commits = self.consume(authority)
        if commits is None:
            self.release(authority)
            raise self.refusal("device_configuration_core_authority_unavailable")

        def check():
            if not self.revalidate(authority, commits):
                raise self.refusal("device_configuration_core_authority_retired")
        check.core_binding = (
            {"version": authority.core_version, "contract_fingerprint": PROFILE.contract_fingerprint}
            if PROFILE.capability_id in getattr(authority, "capability_ids", ()) else None
        )
        try:
            check()
            value = await callback(check)
            check()
            return value
        finally:
            self.finish(commits)


class DeviceConfigurationExecutionRepository(FanExecutionRepository):
    kind = FAMILY
    provider_name = PROVIDER


def request_summary(telemetry):
    safe = _request_summary(telemetry, kind=FAMILY, provider=PROVIDER)
    context = telemetry.audit_context
    outcome = context.get("device_configuration_outcome")
    if isinstance(outcome, str) and outcome in OUTCOMES:
        safe["device_configuration_outcome"] = outcome
    gate = context.get("device_configuration_safety_gate")
    if isinstance(gate, str) and gate in SAFETY_GATE_VALUES:
        safe["safety_gate"] = gate
        safe["external_approval"] = False
        if gate == "acknowledged":
            safe["safety_confirmation"] = CONFIRMATION
    reason = context.get("device_configuration_confirmation_reason")
    if isinstance(reason, str) and reason in CONFIRMATION_REASONS:
        safe["safety_confirmation_reason"] = reason
    status = context.get("device_configuration_status")
    if isinstance(status, str) and status in STATUSES:
        safe["status"] = status
    return safe


STATUSES = frozenset({"succeeded_verified", "no_change", "changed_unverified",
                      "verification_failed", "confirmation_required", "refused",
                      "provider_unavailable", "in_progress"})


def status_of(receipt):
    """Contract status from durable facts only (the ``control_helper`` vocabulary)."""
    outcome = receipt.get("terminal_outcome")
    dispatched = receipt.get("dispatch_intent_recorded") is True
    if receipt.get("terminal") is not True:
        return "changed_unverified" if dispatched else "in_progress"
    if outcome == "succeeded_verified":
        return "succeeded_verified" if dispatched else "no_change"
    if not dispatched:
        return "provider_unavailable" if outcome == "provider_unavailable_pre_dispatch" else "refused"
    if outcome == "dispatch_failed_confirmed" or receipt.get("provider_dispatch_occurred") is False:
        return "provider_unavailable"
    if receipt.get("provider_response_received") is True:
        return "verification_failed"
    return "changed_unverified"


def _service_projection(entity_id, domain, value):
    payload = service_call(entity_id, domain, value)
    return {"service": payload["domain"] + "." + payload["service"],
            "data": payload.get("service_data", {})}


def _baseline_projection(observed, restore):
    attributes = observed["attributes"]
    value = {"value": restore}
    if observed["entity_id"].startswith("select."):
        value["options"] = list(attributes["options"])
    elif observed["entity_id"].startswith("number."):
        device_class = observed["registry"]["original_device_class"]
        value.update({key: attributes[key] for key in (
            "min", "max", "step", "mode", "unit_of_measurement")})
        value["original_device_class"] = device_class
        # §5.5.1 rule 2: the reason no Core unit conversion can apply.
        value["identity_conversion"] = (
            "device_class_has_no_unit_converter" if device_class not in UNIT_CONVERTERS
            else "no_registry_unit_option")
    return value


def _atomic_write(path, value, prefix):
    """The shipped receipt write sequence (mkstemp, fsync, replace, dir fsync)."""
    path.parent.mkdir(exist_ok=True)
    payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    fd, temp = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class DeviceConfigurationService(FanService):
    namespace = NAMESPACE
    request_type = DeviceConfigurationRequest
    adapter_type = DeviceConfigurationAdapter
    repository_type = DeviceConfigurationExecutionRepository
    contracts = ()  # signed-only; no unsigned declarations exist to recover
    prepare_record = staticmethod(prepare_record)
    provider_name = PROVIDER
    binding_attribute = "ordinary_device_configuration_binding"
    outcome_key = "device_configuration_outcome"
    refusal = DeviceConfigurationRefusal

    def __init__(self, root, provider, core, *, safety_trigger=None, **kwargs):
        """``safety_trigger`` is a unit-test hook (§5.6 S1-S8); production passes none.

        The first-build trigger is empty (B14): every result is
        ``not_safety_critical``. ``DeviceConfigurationRuntime.configure`` never
        sets the hook.
        """
        super().__init__(root, provider, core, **kwargs)
        self.safety_trigger = safety_trigger
        inner = self.adapter.inner
        inner.undo_source = self._undo_source
        inner.record_verified = self._record_verified

    @staticmethod
    def receipt_parameters(request):
        return {"domain": request.domain, "value": request.value,
                "undo_task_id": request.undo_task_id,
                "acknowledge_safety_critical": request.acknowledge_safety_critical,
                "consequences": list(EFFECTS)}

    # --- Additive sidecars (§5.11.1, §5.11.2) ------------------------------

    def _verified_path(self, task_id):
        return self.root / "verified-observations" / self._path(task_id).name

    def _claim_path(self, task_id):
        return self.root / "undo-claims" / self._path(task_id).name

    @staticmethod
    def _read_bounded(path):
        if not path.exists():
            return None
        if path.stat().st_size > MAX_SIDECAR:
            raise ValueError
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError
        return value

    def _load_verified(self, task_id):
        """The verified-observation sidecar, or ``None`` (missing or invalid: fail closed)."""
        try:
            value = self._read_bounded(self._verified_path(task_id))
            if value is None:
                return None
            if (set(value) != VERIFIED_FIELDS or value["model"] != VERIFIED_MODEL
                    or value["task_id"] != task_id or not isinstance(value["identity"], dict)
                    or not isinstance(value["restore"], str)):
                return None
            validate_sha256(value["prepared_hash"], field_name="prepared_hash")
            validate_sha256(value["post_fingerprint"], field_name="post_fingerprint")
            parse_timestamp(value["recorded_at"], field_name="recorded_at")
        except (OSError, ValueError, TypeError, KeyError):
            return None
        return value

    def _record_verified(self, prepared, fingerprint):
        """Write once, before the terminal commit; never raises into the executor."""
        task_id = prepared.request.task_id
        try:
            path = self._verified_path(task_id)
            if path.exists():
                return
            value = {"model": VERIFIED_MODEL, "task_id": task_id,
                     "prepared_hash": prepared.prepared_operation_hash,
                     "identity": identity(prepared.baseline["observation"]),
                     "post_fingerprint": validate_sha256(fingerprint, field_name="post_fingerprint"),
                     "restore": prepared.baseline["restore"],
                     "recorded_at": self.now().isoformat()}
            if len(json.dumps(value, sort_keys=True, allow_nan=False).encode()) > MAX_SIDECAR:
                return  # unrecordable: undo stays unavailable (fail closed)
            _atomic_write(path, value, ".verified-")
        except Exception:
            return  # undo refuses device_configuration_undo_unavailable

    def _load_claim(self, task_id):
        value = self._read_bounded(self._claim_path(task_id))
        if value is None:
            return None
        if (set(value) != CLAIM_FIELDS or value["model"] != CLAIM_MODEL
                or value["original_task_id"] != task_id):
            raise ValueError
        for key in ("original_prepared_hash", "undo_prepared_hash"):
            validate_sha256(value[key], field_name=key)
        self._path(value["undo_task_id"])
        return value

    def _undo_source(self, request):
        """§5.11.2 eligibility from local durable records; no Home Assistant I/O."""
        original_id = request.undo_task_id
        try:
            original = self.load(original_id)
        except Exception:
            original = None
        if original is None or original.request.entity_id != request.entity_id:
            raise Refusal("device_configuration_undo_unavailable")
        if original.request.is_undo:
            raise Refusal("device_configuration_undo_not_eligible")
        record = self.executions.get(original_id)
        if (record is None or not record.terminal
                or record.normalized_outcome != "succeeded_verified"
                or record.dispatch_intent is None):
            raise Refusal("device_configuration_undo_unavailable")
        sidecar = self._load_verified(original_id)
        if sidecar is None or sidecar["prepared_hash"] != original.prepared_operation_hash:
            raise Refusal("device_configuration_undo_unavailable")
        return {"task_id": original_id, "prepared_hash": original.prepared_operation_hash,
                "identity": sidecar["identity"], "post_fingerprint": sidecar["post_fingerprint"],
                "restore": sidecar["restore"]}

    def _zero_dispatch_proven(self, task_id):
        """Read-only under ``.declarations.lock``; the execution lock never waits on it."""
        try:
            if self.load(task_id) is None:
                return True  # claim without receipt: that undo never started
        except Exception:
            return False
        record = self.executions.get(task_id)
        if record is None or not record.terminal or record.normalized_outcome == "succeeded_verified":
            return False
        return (record.dispatch_intent is None and record.dispatch_count == 0) or (
            record.normalized_outcome == "dispatch_failed_confirmed")

    def _claim_undo(self, prepared):
        original = prepared.baseline["undo_of"]
        try:
            claim = self._load_claim(original["task_id"])
        except (OSError, ValueError, TypeError, KeyError, DeviceConfigurationRefusal):
            raise Refusal("device_configuration_undo_already_claimed") from None
        task_id = prepared.request.task_id
        # The same undo task without a receipt is a crash retry (no receipt
        # means it never started); another undo task keeps the claim unless
        # zero dispatch is proven.
        if (claim is not None and claim["undo_task_id"] != task_id
                and not self._zero_dispatch_proven(claim["undo_task_id"])):
            raise Refusal("device_configuration_undo_already_claimed")
        _atomic_write(self._claim_path(original["task_id"]), {
            "model": CLAIM_MODEL, "original_task_id": original["task_id"],
            "original_prepared_hash": original["prepared_hash"], "undo_task_id": task_id,
            "undo_prepared_hash": prepared.prepared_operation_hash,
            "claimed_at": self.now().isoformat()}, ".claim-")

    def save(self, prepared):
        """FanService.save's checks and bytes, plus the undo claim, in ONE transaction.

        Never ``super().save()`` and never a nested ``transaction()``: flock is
        per open file description, so a second open would deadlock (§5.11.2).
        """
        request = prepared.request
        with self.transaction():
            prior = self.load(request.task_id)
            if prior is not None:
                if prior.request != request:
                    raise self.refusal("fan_operation_id_rebound")
                return prior
            if not request.is_fresh(self.now()):
                raise self.refusal("fan_operation_id_expired")
            if len(list(self.root.glob("*.json"))) >= MAX_RECORDS:
                raise self.refusal("fan_receipt_capacity_exhausted")
            if request.is_undo:
                self._claim_undo(prepared)
            value = {"model": self.namespace, "request": request.model_dump(),
                     "baseline": prepared.baseline, "prepared_hash": prepared.prepared_operation_hash}
            if prepared.core_binding is not None:
                value.update(model=self.namespace + "-core-bound-v2",
                             provider_contract=prepared.provider_contract,
                             core_binding=prepared.core_binding)
            payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
            fd, temp = tempfile.mkstemp(prefix=".fan-", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                os.replace(temp, self._path(request.task_id))
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return prepared

    # --- Receipt -----------------------------------------------------------

    @staticmethod
    def _reason(record):
        if record is None or not record.terminal or record.normalized_outcome not in _PRE_DISPATCH:
            return None
        for event in reversed(record.events):
            for code in event["diagnostic_codes"]:
                if isinstance(code, str) and _REASON.fullmatch(code):
                    return code
        return None

    def _undo_status(self, prepared, status):
        request = prepared.request
        if request.is_undo:
            return {"available": False, "reason": "device_configuration_undo_not_eligible"}
        if status != "succeeded_verified":
            return {"available": False, "reason": "no_verified_change" if status == "no_change"
                    else "device_configuration_undo_unavailable"}
        sidecar = self._load_verified(request.task_id)
        if sidecar is None or sidecar["prepared_hash"] != prepared.prepared_operation_hash:
            return {"available": False, "reason": "device_configuration_undo_unavailable"}
        try:
            claim = self._load_claim(request.task_id)
        except Exception:
            claim = {}
        if claim is not None:
            return {"available": False, "reason": "device_configuration_undo_already_claimed"}
        return {"available": True, "undo_task_id": request.task_id,
                "restores": sidecar["restore"],
                "rule": "only while the post-change observation is still exact; a later change or an "
                        "integration reload ends eligibility, and a Home Assistant restart is expected "
                        "to as well; after that, make a compensating change"}

    def receipt(self, task_id):
        value = super().receipt(task_id)
        if value is None:
            return None
        prepared = self.load(task_id)
        observed = prepared.baseline["observation"]
        record = self.executions.get(task_id)
        status = status_of(value)
        value["status"] = status
        value["target"] = {
            "entity_id": prepared.request.entity_id, "domain": prepared.request.domain,
            "identity": identity(observed), "entity_category": "config",
            "target_kind": target_kind(observed),
            "device_name": prepared.baseline["display"]["device_name"],
            "entity_name": prepared.baseline["display"]["entity_name"],
        }
        value["baseline"] = _baseline_projection(observed, prepared.baseline["restore"])
        target_value = prepared.baseline["target_value"]
        value["requested_value"] = target_value
        value.update(_service_projection(prepared.request.entity_id, prepared.request.domain,
                                         target_value))
        value["disclosures"] = disclosures(observed)
        value["undo"] = self._undo_status(prepared, status)
        if prepared.request.is_undo:
            value["undo_of"] = prepared.baseline["undo_of"]["task_id"]
            value["undone"] = status == "succeeded_verified"
        reason = self._reason(record)
        if reason is not None:
            value["reason"] = reason
        value["external_approval"] = False
        return value

    # --- Safety gate (§5.6) -------------------------------------------------

    def _confirmation_payload(self, prepared, reason):
        request = prepared.request
        return {
            "status": "confirmation_required", "reason": reason,
            "entity_id": request.entity_id, "action": request.action,
            "value": prepared.baseline["target_value"], "operation_id": request.operation_id,
            "dispatch": "no_dispatch", "task_created": False,
            "disclosures": disclosures(prepared.baseline["observation"]),
            "next_step": (
                "Tell the user why this configuration change is safety-critical and ask whether "
                "to proceed. Only if they confirm, call control_device_configuration again with "
                "the same arguments and acknowledge_safety_critical=true (a new operation_id if "
                "this one has expired). This is a chat confirmation, not external approval."),
            "authorization": "authenticated_connector", "provider": PROVIDER,
            "fallback": "none", "external_approval": False,
        }

    async def before_declaration(self, prepared, check):
        notes = _GATE.get()
        notes = notes if notes is not None else {}
        if self.adapter.inner.desired(prepared.request, prepared.baseline):
            # No dispatch can follow: preflight requires this same baseline.
            notes["gate"] = "not_evaluated_value_already_current"
            return
        reason = self.safety_trigger(prepared) if self.safety_trigger is not None else None
        check()
        if reason is not None and reason not in CONFIRMATION_REASONS:
            raise Refusal("device_configuration_safety_gate_invalid")
        notes["confirmation_reason"] = reason
        if reason is not None and not prepared.request.acknowledge_safety_critical:
            raise _ConfirmationRequired(self._confirmation_payload(prepared, reason))
        notes["gate"] = "acknowledged" if reason is not None else "not_safety_critical"

    async def control(self, request):
        telemetry = current_telemetry()
        notes = {}
        token = _GATE.set(notes)
        try:
            result = await super().control(request)
        except _ConfirmationRequired as gate:
            if telemetry:
                telemetry.audit_context.pop("task_id", None)
                telemetry.audit_context[self.outcome_key] = "confirmation_required"
                telemetry.audit_context["device_configuration_safety_gate"] = "confirmation_required"
                telemetry.audit_context["device_configuration_confirmation_reason"] = gate.payload["reason"]
                telemetry.audit_context["device_configuration_status"] = "confirmation_required"
            return gate.payload
        finally:
            _GATE.reset(token)
        result = dict(result)
        gate = notes.get("gate")
        if gate is None:
            result["safety_gate"] = "not_evaluated_reconciliation_of_existing_task"
        else:
            result["safety_gate"] = gate
            if gate == "acknowledged":
                result["safety_confirmation"] = CONFIRMATION
                result["safety_confirmation_reason"] = notes["confirmation_reason"]
        if telemetry:
            if gate is not None:
                telemetry.audit_context["device_configuration_safety_gate"] = gate
                if gate == "acknowledged":
                    telemetry.audit_context["device_configuration_confirmation_reason"] = (
                        notes["confirmation_reason"])
            if result.get("status") in STATUSES:
                telemetry.audit_context["device_configuration_status"] = result["status"]
        return result

    # --- Hold release review (shipped HAMCP-192 path) ----------------------

    async def _fresh_readback(self, prepared):
        """One composite read through the adapter's own observation; never dispatches."""
        inner = self.adapter.inner
        request = prepared.request
        try:
            observed, _ = await inner.core.read(
                lambda check: inner._read(request.entity_id, check), prepared)
            target = prepared.baseline["target_value"]
            matches = verified(request.domain, target, observed,
                               prepared.baseline["observation"]) is True
            readback_hash = validate_sha256(digest(observed), field_name="readback_state_fingerprint")
        except Exception:
            raise self.refusal("fan_hold_release_readback_unavailable") from None
        return {"requested": target, "state": observed["state"]}, matches, readback_hash

    @staticmethod
    def _state_comparison(request, state):
        return [("state", state["requested"], state["state"])]


class DeviceConfigurationRuntime:
    service = None

    def require(self):
        if self.service is None:
            raise Refusal("device_configuration_runtime_unavailable")
        return self.service

    def configure(self, settings, core_runtime, rest_client, websocket_client, *, audit=None):
        # Production: no safety_trigger (B14, the empty first-build trigger).
        self.service = DeviceConfigurationService(
            settings.governance_path, DeviceConfigurationGateway(rest_client, websocket_client),
            DeviceConfigurationCoreAuthority(core_runtime), audit=audit)


DEVICE_CONFIGURATION_OPERATIONS = DeviceConfigurationRuntime()
