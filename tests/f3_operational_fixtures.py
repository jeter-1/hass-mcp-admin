"""Deterministic offline fixtures for F3-C2 operational conformance."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.models import (
    ExecutionIdentity,
    ExecutorTiming,
    LockTiming,
)
from ha_mcp_engineering.f3.executor import SharedOperationExecutor
from ha_mcp_engineering.f3.locks import DurableLockStore
from ha_mcp_engineering.f3.operational_adapter import (
    OperationalAdministrationAdapter,
)
from ha_mcp_engineering.f3.operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    OperationalAuthoritySnapshot,
    OperationalPreparationRequest,
    stable_hash,
)
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
from ha_mcp_engineering.governance.models import (
    ApprovalActionKind,
    ApprovalActionRecord,
    ApprovalPolicyClass,
    ApprovalState,
    ChangeApproval,
    ChangeOperation,
    ChangePlan,
    ChangePolicyDecision,
    ChangeRiskAssessment,
    ChangeRollback,
    ChangeTarget,
    OperationalPlanDetails,
    PhysicalConsequence,
    PlanStatus,
    RecoveryVerification,
    RiskDelta,
    RiskLevel,
)
from ha_mcp_engineering.governance.operational import OperationalGatewayError
from ha_mcp_engineering.governance.operational_lifecycle import LifecycleGatewayError


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
PROVIDER_SLUG = "local_ha_mcp"
TASK_ID = "task-operational-1"
PLAN_ID = "plan-operational-1"
PLAN_HASH = "a" * 64
POLICY_HASH = "b" * 64
SUBJECT_HASH = "c" * 64
PROVIDER_IDENTITY_HASH = "d" * 64


def provider_evidence(operation: str, *, version: str = "8.0.0") -> dict[str, Any]:
    entry = (
        "ha-mcp-v8.0.0-d65630f6"
        if version == "8.0.0"
        else "ha-mcp-v7.14.2-7917b2d3"
    )
    base = {
        "provider": (
            "upstream_operational_backup"
            if operation == CREATE_FULL_BACKUP
            else "upstream_operational_lifecycle"
        ),
        "server_name": "ha-mcp",
        "server_version": version,
        "protocol_version": "2025-03-26",
        "compatibility_entry_id": entry,
        "catalog_fingerprint": "e" * 64,
        "normalized_catalog_fingerprint": "f" * 64,
        "aggregate_fingerprint_model": "ha-mcp-reviewed-normalized-catalog-v1",
        "runtime_contract_fingerprint_model": "ha-mcp-runtime-contract-v2",
        "runtime_artifact_observed": False,
        "fallback": "none",
        "fallback_occurred": False,
    }
    if operation == CREATE_FULL_BACKUP:
        base.update(
            {
                "tool_contract_fingerprint": "1" * 64,
                "argument_constraints": {
                    "scope": "snapshot",
                    "action": "create",
                    "name": "bounded_engineering_value",
                    "restore_allowed": False,
                    "delete_allowed": False,
                    "arbitrary_arguments_allowed": False,
                },
            }
        )
    else:
        base.update(
            {
                "lifecycle_addon_response_contract_model": (
                    "ha-mcp-lifecycle-addon-structured-content-v1"
                    if version == "8.0.0"
                    else "ha-mcp-lifecycle-addon-text-json-v1"
                ),
                "lifecycle_addon_response_envelope_variant": (
                    "mcp-direct-structured-content-v1"
                    if version == "8.0.0"
                    else "mcp-text-content-v1"
                ),
                "tool_contract_fingerprints": {
                    "ha_reload_core": "2" * 64,
                    "ha_manage_addon": "3" * 64,
                    "ha_get_addon": "4" * 64,
                    "ha_restart": "5" * 64,
                },
                "argument_constraints": (
                    {
                        "target_allowlist": [
                            "automation",
                            "input_boolean",
                            "input_number",
                            "script",
                        ],
                        "entry_id_allowed": False,
                        "reload_all_allowed": False,
                        "arbitrary_arguments_allowed": False,
                    }
                    if operation == CONTROLLED_RELOAD
                    else {
                        "action": "restart",
                        "slug": "exact_planned_value",
                        "other_actions_allowed": False,
                        "configuration_mutation_allowed": False,
                        "proxy_allowed": False,
                        "arbitrary_arguments_allowed": False,
                    }
                    if operation == RESTART_ADDON
                    else {
                        "confirm": True,
                        "variants_allowed": False,
                        "arbitrary_arguments_allowed": False,
                    }
                ),
            }
        )
    return base


def runtime_baseline(version: str = "8.0.0") -> dict[str, Any]:
    return {
        "server_version": "2.2.0-beta.19",
        "build_sha": "9f51830907799d4a409bf230c11fe8fbe8c61ead",
        "registered_tool_count": 72 if version == "8.0.0" else 74,
        "engineering_tool_count": 48,
        "delegated_tool_count": 24 if version == "8.0.0" else 26,
        "governance_storage_status": "healthy",
        "governance_plan_count": 3,
        "audit_storage_status": "healthy",
        "audit_write_failures": 0,
        "dependency_index_state": "valid",
        "dependency_prewarm_state": "complete",
        "upstream_version": version,
        "upstream_protocol": "2025-03-26",
        "upstream_catalog_fingerprint": "e" * 64,
        "upstream_admission_status": "admitted_exact",
        "fallback_count": 0,
    }


def baseline_for(
    operation: str,
    *,
    target_id: str,
    version: str = "8.0.0",
    target_class: str = "other_addon",
) -> dict[str, Any]:
    if operation == CREATE_FULL_BACKUP:
        return {
            "inventory_readable": True,
            "inventory_count": 1,
            "backup_ids": ["backup-before"],
            "operation_state": "idle",
            "last_action_event": {"state": "completed", "backup_id": "backup-before"},
        }
    if operation == CONTROLLED_RELOAD:
        return {
            "configuration_validation": {
                "status": "valid",
                "checked_at": NOW.isoformat(),
                "evidence": {},
            },
            "service_available": True,
            "service": f"{target_id}.reload",
            "domain_evidence": {
                "domain": target_id,
                "state_inventory_readable": True,
                "matching_entity_count": 2,
            },
        }
    if operation == RESTART_ADDON:
        upstream = {
            "status": "bound",
            "slug": PROVIDER_SLUG,
            "name": "Synthetic ha-mcp",
            "installed_version": version,
            "repository": "local",
            "endpoint_host": PROVIDER_SLUG.replace("_", "-"),
            "identity_source": "configured_endpoint_supervisor_dns_and_reviewed_admission",
            "inventory_arguments": {"source": "installed", "include_stats": False},
            "admission_evidence": provider_evidence(operation, version=version),
            "provider_contract": provider_evidence(operation, version=version),
        }
        return {
            "addon": {
                "slug": target_id,
                "name": "Synthetic add-on",
                "version": "1.2.3",
                "state": "started",
            },
            "target_class": target_class,
            "target_identity": {
                "requested_slug": target_id,
                "resolved_slug": target_id,
                "resolved_name": "Synthetic add-on",
                "resolved_version": "1.2.3",
                "resolved_repository": target_id.partition("_")[0],
                "identity_source": "synthetic_exact_inventory",
                "authoritative_self_match": target_class == "engineering_addon",
                "authoritative_upstream_match": target_class == "upstream_ha_mcp_addon",
                "target_class": target_class,
            },
            "upstream_addon_identity": upstream,
            "process_instance_id": "process-before",
            "runtime": runtime_baseline(version),
        }
    return {
        "configuration_validation": {
            "status": "valid",
            "checked_at": NOW.isoformat(),
            "evidence": {},
        },
        "home_assistant": {
            "location_name": "Synthetic Home",
            "version": "2026.7.4",
            "connected": True,
        },
        "runtime": runtime_baseline(version),
        "process_instance_id": "process-before",
    }


def _policy(operation: str) -> ChangePolicyDecision:
    elevated = operation in {RESTART_ADDON, RESTART_HOME_ASSISTANT}
    return ChangePolicyDecision(
        policy_version="f2-v1",
        policy_class=(
            ApprovalPolicyClass.ELEVATED_ADMIN
            if elevated
            else ApprovalPolicyClass.STANDARD_ADMIN
        ),
        risk_delta=RiskDelta.HIGH if elevated else RiskDelta.MODERATE,
        physical_consequence=PhysicalConsequence.INDIRECT,
        reason_codes=(
            "addon_restart_elevated_policy"
            if operation == RESTART_ADDON
            else "home_assistant_restart_elevated_policy"
            if operation == RESTART_HOME_ASSISTANT
            else "full_backup_standard_policy"
            if operation == CREATE_FULL_BACKUP
            else "controlled_reload_standard_policy",
        ),
        required_acknowledgements=(
            (
                ApprovalActionKind.PLAN_APPROVAL,
                ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
            )
            if elevated
            else (ApprovalActionKind.PLAN_APPROVAL,)
        ),
        policy_subject_hash=SUBJECT_HASH,
        policy_decision_hash=POLICY_HASH,
    )


def make_plan(
    operation: str,
    *,
    target_id: str | None = None,
    version: str = "8.0.0",
    target_class: str = "other_addon",
) -> ChangePlan:
    target_id = target_id or {
        CREATE_FULL_BACKUP: "local_full_backup",
        CONTROLLED_RELOAD: "automation",
        RESTART_ADDON: "local_example",
        RESTART_HOME_ASSISTANT: "core",
    }[operation]
    target_type = {
        CREATE_FULL_BACKUP: "backup",
        CONTROLLED_RELOAD: "reload_domain",
        RESTART_ADDON: "addon",
        RESTART_HOME_ASSISTANT: "home_assistant",
    }[operation]
    baseline = baseline_for(
        operation,
        target_id=target_id,
        version=version,
        target_class=target_class,
    )
    provider = provider_evidence(operation, version=version)
    elevated = operation in {RESTART_ADDON, RESTART_HOME_ASSISTANT}
    elevated_record = (
        ApprovalActionRecord(
            kind=ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
            state=ApprovalState.APPROVED,
            bound_plan_hash=PLAN_HASH,
            policy_decision_hash=POLICY_HASH,
            policy_class="elevated_admin",
            risk_delta="high",
            physical_consequence="indirect",
            approver_principal="synthetic-admin",
        )
        if elevated
        else None
    )
    requested = "Synthetic Backup" if operation == CREATE_FULL_BACKUP else target_id
    verification_required = {
        CREATE_FULL_BACKUP: ["new_backup_identifier", "exact_name"],
        CONTROLLED_RELOAD: ["post_reload_configuration_valid"],
        RESTART_ADDON: ["restart_evidence", "running_state"],
        RESTART_HOME_ASSISTANT: ["outage", "reconnect", "runtime_restored"],
    }[operation]
    effects = {
        CREATE_FULL_BACKUP: [
            "Create one new local Home Assistant backup archive.",
            "Exclude the recorder database under the reviewed upstream contract.",
        ],
        CONTROLLED_RELOAD: [f"Reload the exact {target_id} configuration domain."],
        RESTART_ADDON: [f"Restart the exact installed add-on {target_id}."],
        RESTART_HOME_ASSISTANT: ["Restart Home Assistant Core once."],
    }[operation]
    limitations = [
        "Rollback is unavailable.",
        "A lost provider response can require readback-only reconciliation or manual review.",
    ]
    if operation == CREATE_FULL_BACKUP:
        limitations.extend(
            [
                "Recorder database content is excluded by the reviewed provider.",
                "Archive-content integrity is not independently validated.",
            ]
        )
    operational = OperationalPlanDetails(
        schema_version=1,
        family="operational_administration",
        operation=operation,
        requested_name=requested,
        provider=provider["provider"],
        provider_capability_evidence=provider,
        expected_effects=effects,
        preconditions=["Exact provider admission and external approval remain valid."],
        verification_contract={
            "version": 1,
            "operation": operation,
            "required": verification_required,
            "no_blind_redispatch": True,
        },
        baseline=baseline,
        dispatch={"attempt_count": 0, "dispatched": False},
        verification=RecoveryVerification(),
        limitations=limitations,
        rollback_available=False,
    )
    policy = _policy(operation)
    approval = ChangeApproval(
        state=ApprovalState.APPROVED,
        channel="external_admin",
        approver_principal="synthetic-admin",
        principal_separation_enforced=True,
        approved_at=NOW.isoformat(),
        bound_plan_hash=PLAN_HASH,
        approval_expires_at=(NOW + timedelta(hours=1)).isoformat(),
        policy_decision_hash=POLICY_HASH,
        policy_class=policy.policy_class.value,
        bundle_state="complete",
        same_principal_confirmed=True,
        elevated_risk_acknowledgement=elevated_record,
    )
    return ChangePlan(
        plan_id=PLAN_ID,
        plan_version=1,
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
        status=PlanStatus.APPROVED,
        title=f"Synthetic {operation}",
        description="Synthetic deterministic operational plan.",
        requested_by="synthetic-requester",
        target=ChangeTarget(target_type, target_id),
        operation=ChangeOperation(operation),
        proposed_config={},
        current_config=None,
        normalized_proposed_config={},
        normalized_current_config=None,
        current_state_fingerprint=stable_hash(baseline),
        proposed_config_hash=stable_hash(
            {"operation": operation, "target_type": target_type, "target_id": target_id}
        ),
        risk=ChangeRiskAssessment(
            level=RiskLevel.HIGH if elevated else RiskLevel.MEDIUM,
            reasons=["Synthetic exact baseline risk."],
            warnings=["A dispatched operation is never blindly repeated."],
        ),
        policy_decision=policy,
        normalization_version=1,
        warnings=["A dispatched operation is never blindly repeated."],
        validation_results={"valid": True, "planning_write_performed": False},
        dry_run_results={"provider_dispatch_occurred": False},
        approval=approval,
        rollback=ChangeRollback(available=False, status="unavailable"),
        contract_version=3,
        plan_family="operational_administration",
        operational=operational,
        execution_outcome="not_applied",
    )


class SyntheticDurableLedger:
    """File-backed test fixture; production integration remains owned by F3-D."""

    def __init__(self, root: Path) -> None:
        self.path = root / "synthetic-operational-ledger.json"
        self.merge_failures = 0

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise ValueError("synthetic ledger corrupt")
        return value

    def load(self, task_id: str) -> dict[str, Any]:
        value = self._read().get(task_id, {})
        if not isinstance(value, dict):
            raise ValueError("synthetic task ledger corrupt")
        return deepcopy(value)

    def merge(self, task_id: str, values: dict[str, Any]) -> None:
        if self.merge_failures:
            self.merge_failures -= 1
            raise OSError("synthetic ledger persistence failure")
        state = self._read()
        record = state.setdefault(task_id, {})
        record.update(deepcopy(values))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class FakeBackupGateway:
    def __init__(self, evidence: dict[str, Any], baseline: dict[str, Any]) -> None:
        self.evidence = deepcopy(evidence)
        self.baseline = deepcopy(baseline)
        self.behavior = "success"
        self.provider_dispatches = 0
        self.simulated_effects = 0
        self.inventory_reads = 0
        self.new_backup = False

    async def planning_evidence(self):
        self.inventory_reads += 1
        if self.behavior == "provider_unavailable":
            raise OperationalGatewayError("provider_unavailable")
        return {"provider": deepcopy(self.evidence), "baseline": deepcopy(self.baseline)}

    async def create_full_backup(self, name, *, before_dispatch):
        await before_dispatch()
        self.provider_dispatches += 1
        if self.provider_dispatches > 1:
            raise AssertionError("backup dispatched more than once")
        if self.behavior == "confirmed_rejection":
            raise OperationalGatewayError("backup_rejected", dispatched=True)
        if self.behavior == "response_lost_before_effect":
            raise OperationalGatewayError("indeterminate_dispatch", dispatched=True)
        self.new_backup = True
        self.simulated_effects += 1
        if self.behavior == "response_lost_after_effect":
            raise OperationalGatewayError("indeterminate_dispatch", dispatched=True)
        return SimpleNamespace(
            backup_id="backup-after",
            operation_id="operation-backup-1",
            name=name,
            date=NOW.isoformat(),
            size_bytes=4096,
        )

    async def verify_full_backup(self, **kwargs):
        self.inventory_reads += 1
        if self.behavior == "inventory_unreadable":
            raise OperationalGatewayError("verification_failed", dispatched=True)
        if self.behavior == "metadata_mismatch":
            return {
                "status": "failed",
                "mismatch_fields": ["backup_size"],
                "evidence": {"matching_backup_count": 1},
            }
        if not self.new_backup:
            return {
                "status": "pending",
                "mismatch_fields": ["backup_missing"],
                "evidence": {"matching_backup_count": 0},
            }
        return {
            "status": "verified",
            "mismatch_fields": [],
            "evidence": {
                "backup_id": "backup-after",
                "new_relative_to_baseline": True,
                "archive_integrity_validated": False,
            },
        }


class FakeLifecycleGateway:
    def __init__(self, operation: str, evidence: dict[str, Any], baseline: dict[str, Any]) -> None:
        self.operation = operation
        self.evidence = deepcopy(evidence)
        self.baseline = deepcopy(baseline)
        self.behavior = "success"
        self.provider_dispatches = 0
        self.simulated_effects = 0
        self.observations = 0
        self.outage_observed = False

    async def planning_evidence(self, operation: str, target: str):
        if self.behavior == "provider_unavailable":
            raise LifecycleGatewayError("provider_unavailable")
        return {"provider": deepcopy(self.evidence), "baseline": deepcopy(self.baseline)}

    async def _dispatch(self, *, before_dispatch):
        await before_dispatch()
        self.provider_dispatches += 1
        if self.provider_dispatches > 1:
            raise AssertionError("lifecycle operation dispatched more than once")
        if self.behavior == "confirmed_rejection":
            raise LifecycleGatewayError("operation_rejected", dispatched=True)
        if self.behavior == "response_lost_before_effect":
            raise LifecycleGatewayError("indeterminate_dispatch", dispatched=True)
        self.simulated_effects += 1
        if self.behavior == "response_lost_after_effect":
            raise LifecycleGatewayError("indeterminate_dispatch", dispatched=True)
        return SimpleNamespace(
            provider_response_received=True,
            response={"success": True},
        )

    async def dispatch_reload(self, target, *, before_dispatch):
        return await self._dispatch(before_dispatch=before_dispatch)

    async def dispatch_addon_restart(self, slug, *, before_dispatch):
        return await self._dispatch(before_dispatch=before_dispatch)

    async def dispatch_home_assistant_restart(self, *, before_dispatch):
        return await self._dispatch(before_dispatch=before_dispatch)

    async def verify_reload(self, target):
        self.observations += 1
        if self.behavior == "post_config_invalid":
            return {
                "status": "failed",
                "mismatch_fields": ["configuration_validation"],
                "evidence": {"redispatch_performed": False},
            }
        return {
            "status": "verified",
            "mismatch_fields": [],
            "evidence": {
                "configuration_validation": {"status": "valid"},
                "service_available": True,
                "redispatch_performed": False,
            },
        }

    async def verify_addon_restart(self, slug, *, baseline, provider_response_received, provider_evidence):
        self.observations += 1
        if self.behavior == "identity_drift":
            return {
                "status": "failed",
                "mismatch_fields": ["addon_identity"],
                "evidence": {"redispatch_performed": False},
            }
        target_class = baseline.get("target_class")
        proof = (
            "upstream_readmission"
            if target_class == "upstream_ha_mcp_addon" and self.simulated_effects
            else "process_identity"
            if target_class == "engineering_addon" and self.simulated_effects
            else "provider_acknowledgement"
            if provider_response_received and self.simulated_effects
            else None
        )
        return {
            "status": "verified" if proof else "pending",
            "mismatch_fields": [] if proof else ["restart_evidence"],
            "evidence": {
                "restart_proof": proof,
                "running": True,
                "redispatch_performed": False,
            },
        }

    async def verify_home_assistant_restart(
        self,
        *,
        baseline,
        restart_dispatch_confirmed,
        authoritative_outage_observed,
        outage_observation_window_open,
        outage_observation_deadline,
    ):
        self.observations += 1
        if self.behavior == "identity_drift":
            return {
                "status": "pending",
                "mismatch_fields": ["home_assistant_identity"],
                "evidence": {"redispatch_performed": False},
            }
        if not authoritative_outage_observed:
            self.outage_observed = True
            return {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "outage_observed_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "expected_disruption_observed": True,
                    "redispatch_performed": False,
                },
            }
        verified = restart_dispatch_confirmed and self.simulated_effects == 1
        return {
            "status": "verified" if verified else "pending",
            "mismatch_fields": [] if verified else ["restart_evidence"],
            "evidence": {
                "outage_observed": True,
                "home_assistant_reconnected": True,
                "reconnected_at": (NOW + timedelta(seconds=5)).isoformat(),
                "runtime_checks": runtime_baseline(),
                "redispatch_performed": False,
            },
        }


@dataclass
class FixtureContext:
    plan: ChangePlan
    backup: FakeBackupGateway
    lifecycle: FakeLifecycleGateway
    ledger: SyntheticDurableLedger
    adapter: OperationalAdministrationAdapter
    authority: OperationalAuthoritySnapshot


def make_context(
    root: Path,
    operation: str,
    *,
    target_id: str | None = None,
    version: str = "8.0.0",
    target_class: str = "other_addon",
) -> FixtureContext:
    plan = make_plan(
        operation,
        target_id=target_id,
        version=version,
        target_class=target_class,
    )
    backup = FakeBackupGateway(
        provider_evidence(CREATE_FULL_BACKUP, version=version),
        baseline_for(CREATE_FULL_BACKUP, target_id="local_full_backup", version=version),
    )
    lifecycle = FakeLifecycleGateway(
        operation,
        provider_evidence(operation if operation != CREATE_FULL_BACKUP else CONTROLLED_RELOAD, version=version),
        deepcopy(plan.operational.baseline),
    )
    ledger = SyntheticDurableLedger(root)
    authority = OperationalAuthoritySnapshot(
        plan_id=plan.plan_id,
        plan_hash=PLAN_HASH,
        task_id=TASK_ID,
        active_task_id=TASK_ID,
        operation=operation,
        target_type=plan.target_type,
        target_id=plan.target_id,
        policy_decision_hash=POLICY_HASH,
        approval_consumed=True,
        elevated_acknowledgement_consumed=(
            operation in {RESTART_ADDON, RESTART_HOME_ASSISTANT}
        ),
        governance_storage_status="healthy",
        audit_storage_status="healthy",
        execution_task_storage_status="healthy",
    )
    adapter = OperationalAdministrationAdapter(
        backup_gateway=backup,
        lifecycle_gateway=lifecycle,
        recovery_ledger=ledger,
        authority_reader=lambda _prepared: authority,
        now=lambda: NOW,
    )
    return FixtureContext(plan, backup, lifecycle, ledger, adapter, authority)


async def prepare_context(context: FixtureContext):
    return await context.adapter.prepare(
        OperationalPreparationRequest(
            plan=context.plan,
            expected_plan_hash=PLAN_HASH,
            task_id=TASK_ID,
            authoritative_provider_slug=PROVIDER_SLUG,
            provider_identity_evidence_hash=PROVIDER_IDENTITY_HASH,
        )
    )


def make_executor(
    root: Path,
    *,
    now=lambda: NOW,
    execution_fault_hook=None,
    executor_fault_hook=None,
    lock_fault_hook=None,
) -> SharedOperationExecutor:
    return SharedOperationExecutor(
        lock_store=DurableLockStore(root, fault_hook=lock_fault_hook),
        execution_repository=DurableExecutionRepository(
            root, fault_hook=execution_fault_hook
        ),
        lock_timing=LockTiming(
            lease_seconds=60,
            renewal_interval_seconds=10,
            wait_timeout_seconds=0,
        ),
        executor_timing=ExecutorTiming(
            post_dispatch_evidence_seconds=3600,
            claim_lease_seconds=60,
            max_observation_attempts=3,
            max_verification_attempts=3,
        ),
        now=now,
        fault_hook=executor_fault_hook,
    )


def execution_identity(*, owner_id: str = "owner-1") -> ExecutionIdentity:
    return ExecutionIdentity(
        task_id=TASK_ID,
        plan_id=PLAN_ID,
        attempt_id="attempt-1",
        request_id="request-1",
        owner_id=owner_id,
    )
