"""Operation-specific behavior behind the shared F3 operational adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ha_mcp_engineering.f3.contracts import F3_ADAPTER_CONTRACT_MODEL

from ..governance.operational import (
    BackupAdministrationGateway,
    OperationalGatewayError,
)
from ..governance.operational_lifecycle import (
    LifecycleGatewayError,
    OperationalLifecycleGateway,
)
from ..governance.helper_state import (
    HelperStateGateway,
    HelperStateGatewayError,
)
from .operational_models import (
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    EVIDENCE_DEADLINE_CLASSES,
    PROVIDER_CONTRACT_MODELS,
    PROVIDER_IDENTITIES,
    PROVIDER_OPERATIONS,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
    TARGET_CLASSES,
    VERIFICATION_MODELS,
    OperationalCapabilityDescriptor,
    OperationalEvidenceProjection,
    PreparedOperationalOperation,
    canonical_json,
    stable_hash,
)
from .operational_observability import OperationalMetrics


@dataclass(frozen=True)
class StrategyDispatch:
    response_received: bool
    response_evidence_hash: str | None
    provider_operation_id: str | None = None
    provider_backup_id: str | None = None
    confirmed_failure: bool = False
    diagnostic_code: str = "provider_response_received"


@dataclass(frozen=True)
class StrategyObservation:
    status: str
    mismatch_fields: tuple[str, ...]
    evidence_hash: str
    diagnostic_codes: tuple[str, ...]
    provider_reachable: bool | None
    target_reachable: bool | None


COMMON_PROVIDER_FIELDS = (
    "provider",
    "server_name",
    "server_version",
    "protocol_version",
    "compatibility_entry_id",
    "normalized_catalog_fingerprint",
    "aggregate_fingerprint_model",
    "runtime_contract_fingerprint_model",
    "argument_constraints",
)
BACKUP_PROVIDER_FIELDS = COMMON_PROVIDER_FIELDS + (
    "tool_contract_fingerprint",
)
LIFECYCLE_PROVIDER_FIELDS = COMMON_PROVIDER_FIELDS + (
    "lifecycle_addon_response_contract_model",
    "lifecycle_addon_response_envelope_variant",
    "tool_contract_fingerprints",
)
HELPER_STATE_PROVIDER_FIELDS = (
    "provider",
    "provider_contract_model",
    "provider_operation",
    "transport",
    "readback_transport",
    "argument_constraints",
    "fallback",
    "fallback_occurred",
)


def _provider_matches(
    planned: dict[str, Any], fresh: dict[str, Any], fields: tuple[str, ...]
) -> bool:
    return all(planned.get(field) == fresh.get(field) for field in fields)


def _bounded_result(result: Any) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(result, dict):
        return "failed", ("invalid_verification_result",), stable_hash(
            {"status": "invalid"}
        )
    status = result.get("status")
    if status not in {"verified", "pending", "failed"}:
        status = "failed"
    mismatch = tuple(
        str(value)[:96]
        for value in (result.get("mismatch_fields") or ())[:16]
        if isinstance(value, str)
    )
    projection = {
        "status": status,
        "mismatch_fields": list(mismatch),
        "evidence": result.get("evidence") if isinstance(result.get("evidence"), dict) else {},
    }
    try:
        evidence_hash = stable_hash(projection)
    except (TypeError, ValueError, OverflowError):
        return "failed", ("invalid_verification_evidence",), stable_hash(
            {"status": "invalid_evidence"}
        )
    return status, mismatch, evidence_hash


def _capability(
    operation: str,
    *,
    argument_surface: tuple[str, ...],
    limitations: tuple[str, ...],
) -> OperationalCapabilityDescriptor:
    value = OperationalCapabilityDescriptor(
        adapter_id="operational_administration",
        contract_model=F3_ADAPTER_CONTRACT_MODEL,
        operation_family="operational_administration",
        supported_operations=(operation,),
        rollback_supported=False,
        readback_recovery_supported=True,
        exact_provider_contract_required=True,
        capability_id=CAPABILITY_IDENTITIES[operation],
        target_class=TARGET_CLASSES[operation],
        provider=PROVIDER_IDENTITIES[operation],
        provider_contract_model=PROVIDER_CONTRACT_MODELS[operation],
        provider_operation=PROVIDER_OPERATIONS[operation],
        argument_surface=argument_surface,
        verification_contract_model=VERIFICATION_MODELS[operation],
        recovery_supported=True,
        evidence_deadline_class=EVIDENCE_DEADLINE_CLASSES[operation],
        manual_review_hold_model="f3-operational-target-hold-v1",
        limitations=limitations,
    )
    value.validate()
    return value


class OperationalStrategy:
    operation: str
    capability: OperationalCapabilityDescriptor

    def __init__(
        self,
        *,
        metrics: OperationalMetrics,
    ) -> None:
        self.metrics = metrics

    async def preflight_evidence(
        self, prepared: PreparedOperationalOperation
    ) -> tuple[bool, str, tuple[str, ...], dict[str, Any] | None]:
        raise NotImplementedError

    async def dispatch(
        self,
        prepared: PreparedOperationalOperation,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
    ) -> StrategyDispatch:
        raise NotImplementedError

    async def observe(
        self,
        prepared: PreparedOperationalOperation,
        *,
        provider_response_received: bool,
        recovering: bool,
        evidence: OperationalEvidenceProjection,
    ) -> StrategyObservation:
        raise NotImplementedError


class FullBackupOperationStrategy(OperationalStrategy):
    operation = CREATE_FULL_BACKUP
    capability = _capability(
        CREATE_FULL_BACKUP,
        argument_surface=("scope=snapshot", "action=create", "name=exact_planned"),
        limitations=(
            "recorder_database_excluded",
            "archive_integrity_not_independently_validated",
            "restore_delete_download_retention_unavailable",
        ),
    )

    def __init__(
        self,
        gateway: BackupAdministrationGateway,
        *,
        metrics: OperationalMetrics,
    ) -> None:
        super().__init__(metrics=metrics)
        self.gateway = gateway

    async def preflight_evidence(self, prepared):
        self.metrics.increment(self.operation, "inventory_reads")
        try:
            fresh = await self.gateway.planning_evidence()
        except OperationalGatewayError as exc:
            return False, exc.category, ("provider_admission",), None
        provider = fresh.get("provider")
        baseline = fresh.get("baseline")
        if not isinstance(provider, dict) or not _provider_matches(
            prepared.provider_evidence, provider, BACKUP_PROVIDER_FIELDS
        ):
            return False, "exact_contract_mismatch", ("provider_contract",), fresh
        if not isinstance(baseline, dict):
            return False, "invalid_provider_response", ("backup_inventory",), fresh
        planned = prepared.baseline
        if (
            baseline.get("inventory_readable") is not True
            or not isinstance(baseline.get("backup_ids"), list)
            or baseline.get("backup_ids") != planned.get("backup_ids")
            or baseline.get("operation_state") != "idle"
        ):
            return False, "stale_baseline", ("backup_inventory",), fresh
        return True, "eligible", (), fresh

    async def dispatch(self, prepared, *, before_dispatch):
        try:
            result = await self.gateway.create_full_backup(
                prepared.requested_name,
                before_dispatch=before_dispatch,
            )
        except OperationalGatewayError as exc:
            if not exc.dispatched:
                raise
            confirmed = exc.category in {
                "permission_failure",
                "backup_rejected",
                "backup_failed",
            }
            return StrategyDispatch(
                response_received=confirmed,
                response_evidence_hash=stable_hash(
                    {"category": exc.category, "confirmed": confirmed}
                ),
                confirmed_failure=confirmed,
                diagnostic_code=(
                    "provider_rejection_confirmed"
                    if confirmed
                    else "provider_response_lost"
                ),
            )
        response_hash = stable_hash(
            {
                "backup_id": result.backup_id,
                "operation_id": result.operation_id,
                "name": result.name,
                "date": result.date,
                "size_bytes": result.size_bytes,
            }
        )
        return StrategyDispatch(
            response_received=True,
            response_evidence_hash=response_hash,
            provider_operation_id=result.operation_id,
            provider_backup_id=result.backup_id,
        )

    async def observe(
        self,
        prepared,
        *,
        provider_response_received,
        recovering,
        evidence,
    ):
        del provider_response_received, recovering
        started_at = evidence.intent_committed_at
        if not isinstance(started_at, str):
            return StrategyObservation(
                status="manual_review",
                mismatch_fields=("dispatch_timestamp",),
                evidence_hash=stable_hash({"reason": "dispatch_timestamp_missing"}),
                diagnostic_codes=("dispatch_timestamp_missing",),
                provider_reachable=None,
                target_reachable=None,
            )
        self.metrics.increment(self.operation, "inventory_reads")
        try:
            result = await self.gateway.verify_full_backup(
                requested_name=prepared.requested_name,
                baseline_ids=list(prepared.baseline.get("backup_ids") or ()),
                apply_started_at=started_at,
                backup_id=(
                    evidence.provider_backup_id
                    if isinstance(evidence.provider_backup_id, str)
                    else None
                ),
                operation_id=(
                    evidence.provider_operation_id
                    if isinstance(evidence.provider_operation_id, str)
                    else None
                ),
            )
        except OperationalGatewayError as exc:
            return StrategyObservation(
                status="pending",
                mismatch_fields=("backup_inventory",),
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=("backup_observation_unavailable",),
                provider_reachable=False,
                target_reachable=None,
            )
        status, mismatch, evidence_hash = _bounded_result(result)
        if status == "verified":
            self.metrics.increment(self.operation, "new_backup_detections")
        elif status == "pending":
            self.metrics.increment(self.operation, "ambiguous_backup_outcomes")
        return StrategyObservation(
            status=status,
            mismatch_fields=mismatch,
            evidence_hash=evidence_hash,
            diagnostic_codes=(f"backup_{status}",),
            provider_reachable=True,
            target_reachable=True,
        )


class ControlledReloadOperationStrategy(OperationalStrategy):
    operation = CONTROLLED_RELOAD
    capability = _capability(
        CONTROLLED_RELOAD,
        argument_surface=("target=exact_allowlisted_domain",),
        limitations=("direct_reload_effect_signal_unavailable",),
    )

    def __init__(self, gateway: OperationalLifecycleGateway, *, metrics):
        super().__init__(metrics=metrics)
        self.gateway = gateway

    async def preflight_evidence(self, prepared):
        self.metrics.increment(self.operation, "configuration_checks")
        self.metrics.increment(self.operation, "service_checks")
        self.metrics.increment(self.operation, "domain_inventory_reads")
        try:
            fresh = await self.gateway.planning_evidence(
                self.operation, prepared.target.target_id
            )
        except LifecycleGatewayError as exc:
            return False, exc.category, ("provider_or_home_assistant",), None
        provider = fresh.get("provider")
        baseline = fresh.get("baseline")
        if not isinstance(provider, dict) or not _provider_matches(
            prepared.provider_evidence, provider, LIFECYCLE_PROVIDER_FIELDS
        ):
            return False, "exact_contract_mismatch", ("provider_contract",), fresh
        if not isinstance(baseline, dict):
            return False, "invalid_provider_response", ("reload_baseline",), fresh
        if baseline.get("configuration_validation", {}).get("status") != "valid":
            return False, "configuration_invalid", ("configuration_validation",), fresh
        if baseline.get("service_available") is not True:
            return False, "service_unavailable", ("reload_service",), fresh
        domain = baseline.get("domain_evidence")
        if not isinstance(domain, dict) or domain.get("state_inventory_readable") is not True:
            return False, "invalid_provider_response", ("domain_inventory",), fresh
        planned = prepared.baseline
        if (
            baseline.get("service") != planned.get("service")
            or domain != planned.get("domain_evidence")
        ):
            return False, "stale_baseline", ("reload_domain",), fresh
        return True, "eligible", (), fresh

    async def dispatch(self, prepared, *, before_dispatch):
        try:
            result = await self.gateway.dispatch_reload(
                prepared.target.target_id, before_dispatch=before_dispatch
            )
        except LifecycleGatewayError as exc:
            if not exc.dispatched:
                raise
            confirmed = exc.category in {"operation_rejected", "operation_failed"}
            return StrategyDispatch(
                response_received=confirmed,
                response_evidence_hash=stable_hash({"category": exc.category}),
                confirmed_failure=confirmed,
                diagnostic_code=(
                    "provider_rejection_confirmed"
                    if confirmed
                    else "provider_response_lost"
                ),
            )
        return StrategyDispatch(
            response_received=result.provider_response_received,
            response_evidence_hash=stable_hash(result.response),
        )

    async def observe(
        self,
        prepared,
        *,
        provider_response_received,
        recovering,
        evidence,
    ):
        del recovering, evidence
        self.metrics.increment(self.operation, "configuration_checks")
        self.metrics.increment(self.operation, "service_checks")
        self.metrics.increment(self.operation, "domain_inventory_reads")
        try:
            result = await self.gateway.verify_reload(prepared.target.target_id)
        except LifecycleGatewayError as exc:
            return StrategyObservation(
                status="pending",
                mismatch_fields=("reload_readback",),
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=("reload_observation_unavailable",),
                provider_reachable=False,
                target_reachable=None,
            )
        status, mismatch, evidence_hash = _bounded_result(result)
        result_evidence = result.get("evidence") if isinstance(result, dict) else None
        independent_effect = bool(
            isinstance(result_evidence, dict)
            and result_evidence.get("reload_effect_observed") is True
        )
        if status == "verified" and not (
            provider_response_received or independent_effect
        ):
            status = "pending"
            mismatch = ("reload_effect_evidence",)
            evidence_hash = stable_hash(
                {
                    "status": "pending",
                    "provider_response_received": False,
                    "reload_effect_observed": False,
                }
            )
        return StrategyObservation(
            status=status,
            mismatch_fields=mismatch,
            evidence_hash=evidence_hash,
            diagnostic_codes=(f"reload_{status}",),
            provider_reachable=True,
            target_reachable=True,
        )


class AddonRestartOperationStrategy(OperationalStrategy):
    operation = RESTART_ADDON
    capability = _capability(
        RESTART_ADDON,
        argument_surface=("slug=exact_planned", "action=restart"),
        limitations=("restart_signal_depends_on_target_class",),
    )

    def __init__(self, gateway: OperationalLifecycleGateway, *, metrics):
        super().__init__(metrics=metrics)
        self.gateway = gateway

    async def preflight_evidence(self, prepared):
        try:
            fresh = await self.gateway.planning_evidence(
                self.operation, prepared.target.target_id
            )
        except LifecycleGatewayError as exc:
            return False, exc.category, ("addon_identity",), None
        provider = fresh.get("provider")
        baseline = fresh.get("baseline")
        if not isinstance(provider, dict) or not _provider_matches(
            prepared.provider_evidence, provider, LIFECYCLE_PROVIDER_FIELDS
        ):
            return False, "exact_contract_mismatch", ("provider_contract",), fresh
        if not isinstance(baseline, dict):
            return False, "invalid_provider_response", ("addon_baseline",), fresh
        addon = baseline.get("addon")
        target = baseline.get("target_identity")
        planned = prepared.baseline
        if not isinstance(addon, dict) or not isinstance(target, dict):
            return False, "invalid_provider_response", ("addon_identity",), fresh
        if addon.get("state") not in {"started", "running"}:
            return False, "target_state_invalid", ("addon_running_state",), fresh
        if any(
            addon.get(field) != planned.get("addon", {}).get(field)
            for field in ("slug", "name", "version")
        ) or any(
            target.get(field) != planned.get("target_identity", {}).get(field)
            for field in (
                "requested_slug",
                "resolved_slug",
                "resolved_name",
                "resolved_version",
                "resolved_repository",
                "identity_source",
                "authoritative_self_match",
                "authoritative_upstream_match",
                "target_class",
            )
        ):
            return False, "stale_baseline", ("addon_identity",), fresh
        if planned.get("upstream_addon_identity") != baseline.get("upstream_addon_identity"):
            return False, "stale_baseline", ("upstream_addon_identity",), fresh
        self.metrics.increment(self.operation, "identity_bindings")
        model = provider.get("lifecycle_addon_response_contract_model")
        self.metrics.increment(
            self.operation,
            (
                "structured_response_models"
                if model == "ha-mcp-lifecycle-addon-structured-content-v1"
                else "legacy_response_models"
            ),
        )
        return True, "eligible", (), fresh

    async def dispatch(self, prepared, *, before_dispatch):
        try:
            result = await self.gateway.dispatch_addon_restart(
                prepared.target.target_id, before_dispatch=before_dispatch
            )
        except LifecycleGatewayError as exc:
            if not exc.dispatched:
                raise
            confirmed = exc.category in {"operation_rejected", "operation_failed"}
            return StrategyDispatch(
                response_received=confirmed,
                response_evidence_hash=stable_hash({"category": exc.category}),
                confirmed_failure=confirmed,
                diagnostic_code=(
                    "provider_rejection_confirmed"
                    if confirmed
                    else "provider_response_lost"
                ),
            )
        return StrategyDispatch(
            response_received=result.provider_response_received,
            response_evidence_hash=stable_hash(result.response),
        )

    async def observe(
        self,
        prepared,
        *,
        provider_response_received,
        recovering,
        evidence,
    ):
        del recovering, evidence
        try:
            result = await self.gateway.verify_addon_restart(
                prepared.target.target_id,
                baseline=prepared.baseline,
                provider_response_received=provider_response_received,
                provider_evidence=prepared.provider_evidence,
            )
        except LifecycleGatewayError as exc:
            return StrategyObservation(
                status="pending",
                mismatch_fields=("addon_readback",),
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=("addon_observation_unavailable",),
                provider_reachable=False,
                target_reachable=None,
            )
        status, mismatch, evidence_hash = _bounded_result(result)
        evidence = result.get("evidence") if isinstance(result, dict) else {}
        if isinstance(evidence, dict):
            if evidence.get("restart_proof") in {"process_identity", "provider_acknowledgement"}:
                self.metrics.increment(self.operation, "reconnect_observations")
            if evidence.get("restart_proof") == "upstream_readmission":
                self.metrics.increment(self.operation, "readmission_observations")
        return StrategyObservation(
            status=status,
            mismatch_fields=mismatch,
            evidence_hash=evidence_hash,
            diagnostic_codes=(f"addon_restart_{status}",),
            provider_reachable=True,
            target_reachable=True,
        )


class HomeAssistantRestartOperationStrategy(OperationalStrategy):
    operation = RESTART_HOME_ASSISTANT
    capability = _capability(
        RESTART_HOME_ASSISTANT,
        argument_surface=("confirm=true",),
        limitations=("outage_evidence_is_time_bounded",),
    )

    def __init__(self, gateway: OperationalLifecycleGateway, *, metrics):
        super().__init__(metrics=metrics)
        self.gateway = gateway

    async def preflight_evidence(self, prepared):
        self.metrics.increment(self.operation, "cheap_eligibility_checks")
        try:
            fresh = await self.gateway.planning_evidence(
                self.operation, prepared.target.target_id
            )
        except LifecycleGatewayError as exc:
            self.metrics.increment(self.operation, "expensive_probes_avoided")
            return False, exc.category, ("home_assistant_identity",), None
        provider = fresh.get("provider")
        baseline = fresh.get("baseline")
        if not isinstance(provider, dict) or not _provider_matches(
            prepared.provider_evidence, provider, LIFECYCLE_PROVIDER_FIELDS
        ):
            return False, "exact_contract_mismatch", ("provider_contract",), fresh
        if not isinstance(baseline, dict):
            return False, "invalid_provider_response", ("restart_baseline",), fresh
        if baseline.get("configuration_validation", {}).get("status") != "valid":
            return False, "configuration_invalid", ("configuration_validation",), fresh
        runtime = baseline.get("runtime")
        planned_runtime = prepared.baseline.get("runtime")
        identity = baseline.get("home_assistant")
        planned_identity = prepared.baseline.get("home_assistant")
        if not isinstance(runtime, dict) or not isinstance(identity, dict):
            return False, "invalid_provider_response", ("runtime_identity",), fresh
        if any(
            identity.get(field) != planned_identity.get(field)
            for field in ("location_name", "version")
        ) or any(
            runtime.get(field) != planned_runtime.get(field)
            for field in (
                "server_version",
                "build_sha",
                "registered_tool_count",
                "engineering_tool_count",
                "delegated_tool_count",
                "upstream_version",
                "upstream_protocol",
                "upstream_catalog_fingerprint",
            )
        ):
            return False, "stale_baseline", ("runtime_identity",), fresh
        if (
            runtime.get("governance_storage_status") != "healthy"
            or runtime.get("audit_storage_status") != "healthy"
            or runtime.get("audit_write_failures") != 0
            or runtime.get("upstream_admission_status") != "admitted_exact"
            or runtime.get("fallback_count") not in {0, None}
        ):
            return False, "storage_unhealthy", ("runtime_storage",), fresh
        return True, "eligible", (), fresh

    async def dispatch(self, prepared, *, before_dispatch):
        try:
            result = await self.gateway.dispatch_home_assistant_restart(
                before_dispatch=before_dispatch
            )
        except LifecycleGatewayError as exc:
            if not exc.dispatched:
                raise
            confirmed = exc.category in {"operation_rejected", "operation_failed"}
            return StrategyDispatch(
                response_received=confirmed,
                response_evidence_hash=stable_hash({"category": exc.category}),
                confirmed_failure=confirmed,
                diagnostic_code=(
                    "provider_rejection_confirmed"
                    if confirmed
                    else "provider_response_lost"
                ),
            )
        return StrategyDispatch(
            response_received=result.provider_response_received,
            response_evidence_hash=stable_hash(result.response),
        )

    async def observe(
        self,
        prepared,
        *,
        provider_response_received,
        recovering,
        evidence,
    ):
        del recovering
        outage = evidence.outage_observed
        self.metrics.increment(self.operation, "expensive_probes")
        try:
            result = await self.gateway.verify_home_assistant_restart(
                baseline=prepared.baseline,
                restart_dispatch_confirmed=provider_response_received,
                authoritative_outage_observed=outage,
                outage_observation_window_open=not outage,
                outage_observation_deadline=evidence.evidence_deadline,
            )
        except LifecycleGatewayError as exc:
            return StrategyObservation(
                status="pending",
                mismatch_fields=("home_assistant_recovery",),
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=("home_assistant_observation_unavailable",),
                provider_reachable=False,
                target_reachable=False,
            )
        status, mismatch, evidence_hash = _bounded_result(result)
        if status == "verified" and not (
            evidence.outage_observed and evidence.reconnect_observed
        ):
            status = "pending"
            mismatch = ("persisted_outage_reconnect_evidence",)
            evidence_hash = stable_hash(
                {
                    "status": "pending",
                    "outage_observed": evidence.outage_observed,
                    "reconnect_observed": evidence.reconnect_observed,
                }
            )
        return StrategyObservation(
            status=status,
            mismatch_fields=mismatch,
            evidence_hash=evidence_hash,
            diagnostic_codes=(f"home_assistant_restart_{status}",),
            provider_reachable=(status != "pending" or outage),
            target_reachable=(status == "verified"),
        )


class InputBooleanStateOperationStrategy(OperationalStrategy):
    operation = SET_INPUT_BOOLEAN_STATE
    capability = _capability(
        SET_INPUT_BOOLEAN_STATE,
        argument_surface=(
            "domain=input_boolean",
            "service=turn_on|turn_off",
            "target=exact_entity_id",
        ),
        limitations=(
            "input_boolean_only",
            "toggle_unavailable",
            "separate_reverse_plan_required",
        ),
    )

    def __init__(
        self,
        gateway: HelperStateGateway,
        *,
        dependency_risk_reader: Callable[..., Awaitable[
            dict[str, Any]
        ]],
        metrics: OperationalMetrics,
    ) -> None:
        super().__init__(metrics=metrics)
        self.gateway = gateway
        self.dependency_risk_reader = dependency_risk_reader

    async def preflight_evidence(self, prepared):
        self.metrics.increment(self.operation, "state_reads")
        try:
            fresh = await self.gateway.planning_evidence(
                prepared.target.target_id
            )
        except HelperStateGatewayError as exc:
            return False, exc.category, ("entity_state",), None
        provider = fresh.get("provider")
        state_baseline = fresh.get("baseline")
        if not isinstance(provider, dict) or not _provider_matches(
            prepared.provider_evidence,
            provider,
            HELPER_STATE_PROVIDER_FIELDS,
        ):
            return False, "exact_contract_mismatch", ("provider_contract",), fresh
        if not isinstance(state_baseline, dict):
            return False, "invalid_provider_response", ("entity_state",), fresh
        try:
            dependency = await self.dependency_risk_reader(
                prepared.target.target_id, refresh=True
            )
        except Exception:
            dependency = None
        binding = (
            dependency.get("binding")
            if isinstance(dependency, dict)
            else None
        )
        if not isinstance(binding, dict):
            return (
                False,
                "dependency_evidence_unavailable",
                ("dependency_risk",),
                fresh,
            )
        combined = {
            "provider": provider,
            "baseline": {
                **state_baseline,
                "dependency_risk": binding,
            },
        }
        if state_baseline.get("state") == prepared.requested_name:
            return False, "already_desired", (), combined
        planned = prepared.baseline
        planned_dependency = planned.get("dependency_risk")
        if binding.get("evidence_complete") is not True:
            return (
                False,
                "dependency_evidence_incomplete",
                ("dependency_risk_completeness",),
                combined,
            )
        if (
            not isinstance(planned_dependency, dict)
            or binding.get("evidence_fingerprint")
            != planned_dependency.get("evidence_fingerprint")
        ):
            return (
                False,
                "dependency_risk_drift",
                ("dependency_risk_fingerprint",),
                combined,
            )
        planned_state = {
            key: planned.get(key)
            for key in ("entity_id", "state", "last_changed")
        }
        if state_baseline != planned_state:
            return False, "stale_baseline", ("entity_state",), fresh
        return True, "eligible", (), combined

    async def dispatch(self, prepared, *, before_dispatch):
        try:
            result = await self.gateway.set_state(
                prepared.target.target_id,
                prepared.requested_name,
                before_dispatch=before_dispatch,
            )
        except HelperStateGatewayError as exc:
            if not exc.dispatched:
                raise
            confirmed = exc.category == "provider_rejected"
            return StrategyDispatch(
                response_received=confirmed,
                response_evidence_hash=stable_hash(
                    {"category": exc.category, "confirmed": confirmed}
                ),
                confirmed_failure=confirmed,
                diagnostic_code=(
                    "provider_rejection_confirmed"
                    if confirmed
                    else "provider_response_lost"
                ),
            )
        return StrategyDispatch(
            response_received=result.provider_response_received,
            response_evidence_hash=stable_hash(
                {
                    "provider_response_received": (
                        result.provider_response_received
                    )
                }
            ),
        )

    async def observe(
        self,
        prepared,
        *,
        provider_response_received,
        recovering,
        evidence,
    ):
        del provider_response_received, recovering, evidence
        self.metrics.increment(self.operation, "state_reads")
        try:
            state = await self.gateway.read_state(prepared.target.target_id)
        except HelperStateGatewayError as exc:
            status = "failed" if exc.category == "entity_not_found" else "pending"
            return StrategyObservation(
                status=status,
                mismatch_fields=("entity_state",),
                evidence_hash=stable_hash({"category": exc.category}),
                diagnostic_codes=(f"helper_state_{status}",),
                provider_reachable=(False if status == "pending" else True),
                target_reachable=(False if status == "failed" else None),
            )
        verified = state.get("state") == prepared.requested_name
        status = "verified" if verified else "failed"
        return StrategyObservation(
            status=status,
            mismatch_fields=() if verified else ("entity_state",),
            evidence_hash=stable_hash(state),
            diagnostic_codes=(f"helper_state_{status}",),
            provider_reachable=True,
            target_reachable=True,
        )


def default_strategies(
    *,
    backup_gateway: BackupAdministrationGateway,
    lifecycle_gateway: OperationalLifecycleGateway,
    helper_state_gateway: HelperStateGateway,
    helper_dependency_risk_reader: Callable[..., Awaitable[
        dict[str, Any]
    ]],
    metrics: OperationalMetrics,
) -> dict[str, OperationalStrategy]:
    strategies: tuple[OperationalStrategy, ...] = (
        FullBackupOperationStrategy(
            backup_gateway, metrics=metrics
        ),
        ControlledReloadOperationStrategy(
            lifecycle_gateway, metrics=metrics
        ),
        AddonRestartOperationStrategy(
            lifecycle_gateway, metrics=metrics
        ),
        HomeAssistantRestartOperationStrategy(
            lifecycle_gateway, metrics=metrics
        ),
        InputBooleanStateOperationStrategy(
            helper_state_gateway,
            dependency_risk_reader=helper_dependency_risk_reader,
            metrics=metrics,
        ),
    )
    return {strategy.operation: strategy for strategy in strategies}
