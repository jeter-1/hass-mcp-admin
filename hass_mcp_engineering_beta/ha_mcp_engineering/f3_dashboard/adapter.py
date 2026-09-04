"""F3 adapter for one governed existing-dashboard update."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from ..errors import DashboardProviderError
from ..f3.contracts import (
    F3_ADAPTER_CONTRACT_MODEL,
    HA_MCP_PROVIDER_LOCK_KEY,
    AdapterCapabilityDescriptor,
    DispatchResult,
    LockMode,
    LockRequest,
    LockScope,
    NormalizedOperationOutcome,
    ObservationResult,
    OperationTarget,
    PreflightResult,
    PreparedOperation,
    RecoveryContext,
    VerificationResult,
)
from ..governance.models import ApprovalState, ChangeOperation, PlanStatus
from ..governance.normalize import stable_hash
from .artifact_store import (
    DashboardArtifactStore,
    artifact_resulting_configuration,
)
from .errors import ArtifactStorageError, RawEvidenceError
from .identity import operational_identity_from_mapping
from .json_codec import canonical_json_bytes, engineering_sha256
from .raw_evidence import build_raw_dashboard_evidence


ADAPTER_ID = "dashboard_update"
CAPABILITY_ID = "update_existing_dashboard"
VERIFICATION_MODEL = "f3-dashboard-exact-reread-v1"
PROVIDER_CONTRACT_MODEL = "ha-mcp-dashboard-full-result-update-v1"
OPERATOR_POLICY = "bounded_dashboard_update_non_atomic_v1"
EVIDENCE_DEADLINE_SECONDS = 120
_PROVIDER_FAILURE_DIAGNOSTICS = {
    "structured_provider_rejection": "structured_provider_rejection_received",
    "transport_silence_or_response_loss": "transport_silence_or_response_loss",
    "provider_5xx_ambiguous": "provider_5xx_ambiguous",
    "protocol_or_transport_failure": "other_protocol_or_transport_failure",
}


def _provider_failure_projection(
    error: DashboardProviderError,
) -> tuple[bool, tuple[str, ...], str]:
    """Project bounded provider evidence into durable F3 diagnostics."""

    details = error.details
    response_received = details.get("provider_response_received") is True
    failure_kind = details.get("provider_failure_kind")
    diagnostic = _PROVIDER_FAILURE_DIAGNOSTICS.get(
        failure_kind, "unclassified_provider_failure"
    )
    codes = [diagnostic, "readback_only_recovery", "fallback_none"]
    upstream_code: str | None = None
    candidate_code = details.get("upstream_error_code")
    if (
        isinstance(candidate_code, str)
        and 1 <= len(candidate_code) <= 64
        and candidate_code[0].isalpha()
        and all(
            character.isupper() or character.isdigit() or character == "_"
            for character in candidate_code
        )
    ):
        upstream_code = candidate_code
        codes.append(f"upstream_error_{upstream_code.lower()}")
    action: str | None = None
    candidate_action = details.get("upstream_action")
    if (
        isinstance(candidate_action, str)
        and 1 <= len(candidate_action) <= 32
        and candidate_action[0].isalpha()
        and all(
            character.islower() or character.isdigit() or character == "_"
            for character in candidate_action
        )
    ):
        action = candidate_action
        codes.append(f"upstream_action_{action}")
    evidence_hash = stable_hash(
        {
            "provider_failure_kind": failure_kind,
            "provider_response_received": response_received,
            "upstream_error_code": upstream_code,
            "upstream_action": action,
            "http_response_received": (
                details.get("http_response_received") is True
            ),
            "http_status_class": details.get("http_status_class"),
        }
    )
    return response_received, tuple(codes), evidence_hash


@dataclass(frozen=True)
class DashboardPreparationRequest:
    plan: Any
    expected_plan_hash: str
    approval_bundle_hash: str
    public_task_id: str
    child_execution_id: str
    authoritative_provider_slug: str
    provider_identity_evidence_hash: str


@dataclass(frozen=True)
class PreparedDashboardOperation(PreparedOperation):
    capability_id: str
    capability_identity: str
    operation_id: str
    plan_id: str
    plan_hash: str
    plan_contract_version: int
    public_task_id: str
    child_execution_id: str
    plan_expires_at: str
    authoritative_provider_slug: str
    provider_identity_evidence_hash: str
    provider_authority_evidence_hash: str
    compatibility_entry: str
    upstream_version: str
    protocol_version: str
    current_upstream_config_hash: str
    current_engineering_sha256: str
    resulting_upstream_config_hash: str
    resulting_engineering_sha256: str
    resulting_configuration_json: str
    provider_arguments_hash: str
    proposal_sha256: str
    artifact_payload_sha256: str
    non_atomic: bool
    operator_policy: str
    evidence_deadline_seconds: int
    selective_hold_keys: tuple[str, ...]


class DashboardUpdateAdapter:
    """Closed adapter using one setter invocation and exact readback."""

    capabilities = AdapterCapabilityDescriptor(
        adapter_id=ADAPTER_ID,
        contract_model=F3_ADAPTER_CONTRACT_MODEL,
        operation_family="dashboard_update",
        supported_operations=(ChangeOperation.UPDATE_DASHBOARD.value,),
        rollback_supported=False,
        readback_recovery_supported=True,
        exact_provider_contract_required=True,
    )

    def __init__(
        self,
        gateway: Any,
        artifacts: DashboardArtifactStore,
        *,
        now=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.gateway = gateway
        self.artifacts = artifacts
        self.now = now
        self._ephemeral_bps_keys: dict[str, str] = {}

    async def prepare(
        self, request: DashboardPreparationRequest
    ) -> PreparedDashboardOperation:
        plan = request.plan
        operational = plan.operational
        if (
            plan.contract_version != 3
            or plan.operation is not ChangeOperation.UPDATE_DASHBOARD
            or plan.plan_family != "dashboard_update"
            or operational is None
            or operational.family != "dashboard_update"
            or operational.operation != ChangeOperation.UPDATE_DASHBOARD.value
            or plan.target_type != "dashboard"
            or operational.requested_name != plan.target_id
            or plan.status is not PlanStatus.APPROVED
            or plan.approval.state is not ApprovalState.APPROVED
            or plan.approval.bound_plan_hash != request.expected_plan_hash
            or plan.policy_decision is None
            or plan.approval.policy_decision_hash
            != plan.policy_decision.policy_decision_hash
        ):
            raise ValueError("dashboard_plan_contract_mismatch")
        baseline = operational.baseline
        if (
            baseline.get("operator_policy") != OPERATOR_POLICY
            or baseline.get("non_atomic") is not True
            or baseline.get("storage_mode_confirmed") is not True
        ):
            raise ValueError("dashboard_operator_policy_mismatch")
        identity_value = baseline.get("dashboard_operational_identity")
        if not isinstance(identity_value, dict):
            raise ValueError("dashboard_provider_identity_missing")
        identity = operational_identity_from_mapping(identity_value)
        if (
            identity.target_url_path != plan.target_id
            or identity.authority.provider_slug
            != request.authoritative_provider_slug
            or identity.evidence_hash
            != request.provider_identity_evidence_hash
            or identity.evidence_hash
            != baseline.get("dashboard_provider_identity_hash")
            or identity.baseline_engineering_sha256
            != baseline.get("current_engineering_sha256")
            or identity.baseline_upstream_config_hash
            != baseline.get("current_upstream_config_hash")
        ):
            raise ValueError("dashboard_provider_identity_mismatch")
        artifact = self.artifacts.get(plan.plan_id)
        if artifact is None:
            raise ArtifactStorageError("Dashboard artifact is missing")
        if (
            artifact.proposal_sha256 != baseline.get("proposal_sha256")
            or artifact.payload_sha256
            != baseline.get("artifact_payload_sha256")
            or artifact.schema != baseline.get("artifact_schema")
        ):
            raise ArtifactStorageError("Dashboard artifact plan binding drifted")
        payload = artifact.payload
        raw = payload.get("raw_evidence")
        compilation = payload.get("compilation")
        if not isinstance(raw, dict) or not isinstance(compilation, dict):
            raise ArtifactStorageError("Dashboard artifact authority is malformed")
        artifact_identity = raw.get("operational_identity")
        if (
            not isinstance(artifact_identity, dict)
            or operational_identity_from_mapping(artifact_identity) != identity
        ):
            raise ArtifactStorageError(
                "Dashboard artifact provider identity drifted"
            )
        resulting = artifact_resulting_configuration(artifact)
        resulting_json = canonical_json_bytes(resulting).decode("utf-8")
        provider_arguments_hash = stable_hash(
            {
                "tool": "ha_config_set_dashboard",
                "url_path": plan.target_id,
                "config_sha256": engineering_sha256(resulting),
                "config_hash": raw.get("upstream_config_hash"),
                "MandatoryBPS": False,
                "return_screenshot": False,
                "ephemeral_best_practice_key": "required_not_persisted",
            }
        )
        values = {
            "contract_model": F3_ADAPTER_CONTRACT_MODEL,
            "adapter_id": ADAPTER_ID,
            "operation": ChangeOperation.UPDATE_DASHBOARD.value,
            "target": OperationTarget("dashboard", plan.target_id),
            "current_state_fingerprint": str(raw.get("engineering_config_sha256")),
            "normalized_proposed_hash": str(compilation.get("resulting_sha256")),
            "risk_level": plan.risk.level.value,
            "policy_decision_hash": plan.policy_decision.policy_decision_hash,
            "approval_bundle_hash": request.approval_bundle_hash,
            "expected_effects": (
                "existing_storage_dashboard_updated",
                "approved_patch_result_observed",
                "undeclared_fields_preserved",
                "non_atomic_result_disclosed",
            ),
            "verification_contract_model": VERIFICATION_MODEL,
            "verification_contract_hash": stable_hash(
                operational.verification_contract
            ),
            "rollback_available": False,
            "capability_id": CAPABILITY_ID,
            "capability_identity": CAPABILITY_ID,
            "operation_id": ChangeOperation.UPDATE_DASHBOARD.value,
            "plan_id": plan.plan_id,
            "plan_hash": request.expected_plan_hash,
            "plan_contract_version": plan.contract_version,
            "public_task_id": request.public_task_id,
            "child_execution_id": request.child_execution_id,
            "plan_expires_at": plan.expires_at,
            "authoritative_provider_slug": request.authoritative_provider_slug,
            "provider_identity_evidence_hash": request.provider_identity_evidence_hash,
            "provider_authority_evidence_hash": identity.authority.evidence_hash,
            "compatibility_entry": str(raw.get("compatibility_entry")),
            "upstream_version": str(raw.get("upstream_version")),
            "protocol_version": str(raw.get("protocol_version")),
            "current_upstream_config_hash": str(raw.get("upstream_config_hash")),
            "current_engineering_sha256": str(raw.get("engineering_config_sha256")),
            "resulting_upstream_config_hash": str(
                compilation.get("resulting_upstream_config_hash")
            ),
            "resulting_engineering_sha256": str(compilation.get("resulting_sha256")),
            "resulting_configuration_json": resulting_json,
            "provider_arguments_hash": provider_arguments_hash,
            "proposal_sha256": artifact.proposal_sha256,
            "artifact_payload_sha256": artifact.payload_sha256,
            "non_atomic": True,
            "operator_policy": OPERATOR_POLICY,
            "evidence_deadline_seconds": EVIDENCE_DEADLINE_SECONDS,
            "selective_hold_keys": (f"dashboard:{plan.target_id}",),
        }
        prepared_hash = stable_hash(
            {
                **values,
                "target": {
                    "target_type": values["target"].target_type,
                    "target_id": values["target"].target_id,
                },
            }
        )
        return PreparedDashboardOperation(
            prepared_operation_hash=prepared_hash, **values
        )

    def lock_requests(
        self, operation: PreparedDashboardOperation
    ) -> tuple[LockRequest, ...]:
        return tuple(
            sorted(
                (
                    LockRequest(
                        f"dashboard:{operation.target.target_id}",
                        (LockScope.RESOURCE,),
                        LockMode.EXCLUSIVE,
                        ("dashboard_target_mutation",),
                    ),
                    LockRequest(
                        "home_assistant:core",
                        (LockScope.RESOURCE,),
                        LockMode.SHARED,
                        ("home_assistant_availability_dependency",),
                    ),
                    LockRequest(
                        HA_MCP_PROVIDER_LOCK_KEY,
                        (LockScope.PROVIDER,),
                        LockMode.SHARED,
                        ("upstream_provider_dependency",),
                    ),
                ),
                key=lambda item: item.key,
            )
        )

    async def preflight(
        self,
        operation: PreparedDashboardOperation,
        *,
        acquired_locks: tuple[LockRequest, ...],
    ) -> PreflightResult:
        from ..f3.locks import normalize_lock_requests

        if normalize_lock_requests(acquired_locks) != normalize_lock_requests(
            self.lock_requests(operation)
        ):
            return self._preflight_rejection(
                operation, "complete_dashboard_lock_set_missing"
            )
        try:
            expires = datetime.fromisoformat(operation.plan_expires_at)
        except (TypeError, ValueError):
            return self._preflight_rejection(operation, "plan_expiration_invalid")
        if expires.tzinfo is None or expires <= self.now().astimezone(timezone.utc):
            return self._preflight_rejection(operation, "plan_expired")
        if self.gateway is None:
            return self._preflight_rejection(
                operation, "dashboard_provider_unavailable_or_unreviewed"
            )
        try:
            current = build_raw_dashboard_evidence(
                await self.gateway.preread(url_path=operation.target.target_id),
                requested_url_path=operation.target.target_id,
            )
            if (
                current.upstream_version != operation.upstream_version
                or current.protocol_version != operation.protocol_version
                or current.compatibility_entry != operation.compatibility_entry
                or current.operational_identity.evidence_hash
                != operation.provider_identity_evidence_hash
                or current.upstream_config_hash
                != operation.current_upstream_config_hash
                or current.engineering_config_sha256
                != operation.current_engineering_sha256
            ):
                return self._preflight_rejection(
                    operation,
                    "stale_or_provider_contract_mismatch",
                    observed=current.engineering_config_sha256,
                )
            key = await self.gateway.best_practice_key(
                expected_provider_authority_evidence_hash=(
                    operation.provider_authority_evidence_hash
                )
            )
        except (RawEvidenceError, DashboardProviderError, ValueError):
            return self._preflight_rejection(
                operation, "dashboard_provider_unavailable_or_unreviewed"
            )
        self._ephemeral_bps_keys[operation.prepared_operation_hash] = key
        evidence = stable_hash(
            {
                "target": operation.target.target_id,
                "current_engineering_sha256": current.engineering_config_sha256,
                "compatibility_entry": current.compatibility_entry,
                "provider_arguments_hash": operation.provider_arguments_hash,
                "operator_policy": operation.operator_policy,
                "non_atomic": True,
            }
        )
        return PreflightResult(
            eligible=True,
            outcome=None,
            confirmed_target=operation.target,
            observed_state_fingerprint=current.engineering_config_sha256,
            provider_contract=PROVIDER_CONTRACT_MODEL,
            provider_operation="ha_config_set_dashboard",
            provider_arguments_hash=operation.provider_arguments_hash,
            evidence_hash=evidence,
            diagnostic_codes=(
                "exact_storage_target_revalidated",
                "exact_provider_catalog_admitted",
                "operator_accepted_non_atomic",
                "fallback_none",
            ),
        )

    @staticmethod
    def _preflight_rejection(
        operation: PreparedDashboardOperation,
        code: str,
        *,
        observed: str | None = None,
    ) -> PreflightResult:
        return PreflightResult(
            eligible=False,
            outcome=NormalizedOperationOutcome.PREFLIGHT_REJECTED,
            confirmed_target=operation.target,
            observed_state_fingerprint=observed,
            provider_contract=None,
            provider_operation=None,
            provider_arguments_hash=None,
            evidence_hash=stable_hash({"category": code}),
            diagnostic_codes=(code, "fallback_none"),
        )

    async def dispatch(
        self,
        operation: PreparedDashboardOperation,
        preflight: PreflightResult,
        *,
        before_dispatch,
    ) -> DispatchResult:
        if not preflight.eligible:
            return DispatchResult(
                outcome=NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                dispatch_intent_recorded=False,
                mutating_invocation_count=0,
                may_have_dispatched=False,
                provider_response_received=False,
                diagnostic_codes=("preflight_not_eligible",),
            )
        key = self._ephemeral_bps_keys.pop(
            operation.prepared_operation_hash, None
        )
        if key is None:
            return DispatchResult(
                outcome=NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                dispatch_intent_recorded=False,
                mutating_invocation_count=0,
                may_have_dispatched=False,
                provider_response_received=False,
                diagnostic_codes=("ephemeral_bps_receipt_missing",),
            )
        await before_dispatch()
        try:
            response = await self.gateway.write(
                url_path=operation.target.target_id,
                configuration=json.loads(operation.resulting_configuration_json),
                config_hash=operation.current_upstream_config_hash,
                best_practice_key=key,
                expected_provider_authority_evidence_hash=(
                    operation.provider_authority_evidence_hash
                ),
            )
        except DashboardProviderError as exc:
            response_received, diagnostics, evidence_hash = (
                _provider_failure_projection(exc)
            )
            return DispatchResult(
                outcome=(
                    NormalizedOperationOutcome.OBSERVING
                    if "structured_provider_rejection_received" in diagnostics
                    else NormalizedOperationOutcome.DISPATCH_INDETERMINATE
                ),
                dispatch_intent_recorded=True,
                mutating_invocation_count=1,
                may_have_dispatched=True,
                provider_response_received=response_received,
                response_evidence_hash=evidence_hash,
                diagnostic_codes=diagnostics,
            )
        except Exception:
            return DispatchResult(
                outcome=NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
                dispatch_intent_recorded=True,
                mutating_invocation_count=1,
                may_have_dispatched=True,
                provider_response_received=False,
                diagnostic_codes=(
                    "unclassified_provider_failure",
                    "readback_only_recovery",
                    "fallback_none",
                ),
            )
        return DispatchResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            dispatch_intent_recorded=True,
            mutating_invocation_count=1,
            may_have_dispatched=True,
            provider_response_received=True,
            response_evidence_hash=stable_hash(response),
            diagnostic_codes=(
                "provider_update_claimed",
                "provider_claim_is_not_verification",
                "non_atomic",
                "fallback_none",
            ),
        )

    async def observe(
        self,
        operation: PreparedDashboardOperation,
        dispatch: DispatchResult | None,
    ) -> ObservationResult:
        dispatch_diagnostics = (
            dispatch.diagnostic_codes if dispatch is not None else ()
        )
        return await self._observe_with_dispatch_diagnostics(
            operation, dispatch_diagnostics
        )

    async def _observe_with_dispatch_diagnostics(
        self,
        operation: PreparedDashboardOperation,
        dispatch_diagnostics: tuple[str, ...],
    ) -> ObservationResult:
        try:
            observed = build_raw_dashboard_evidence(
                await self.gateway.preread(url_path=operation.target.target_id),
                requested_url_path=operation.target.target_id,
            )
        except Exception:
            return ObservationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=1,
                observation_complete=False,
                provider_reachable=None,
                target_reachable=None,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                diagnostic_codes=("dashboard_readback_unavailable", "fallback_none"),
            )
        if (
            observed.operational_identity.authority.evidence_hash
            != operation.provider_authority_evidence_hash
        ):
            return ObservationResult(
                outcome=NormalizedOperationOutcome.VERIFICATION_MISMATCH,
                attempt_count=1,
                observation_complete=True,
                provider_reachable=True,
                target_reachable=True,
                readback_state_fingerprint=observed.engineering_config_sha256,
                intended_result_observed=False,
                mismatch_fields=("dashboard_provider_identity",),
                evidence_hash=stable_hash(
                    {"category": "dashboard_provider_identity_changed"}
                ),
                diagnostic_codes=(
                    "dashboard_provider_identity_changed",
                    "fallback_none",
                ),
            )
        exact = (
            observed.engineering_config_sha256
            == operation.resulting_engineering_sha256
            and observed.upstream_config_hash
            == operation.resulting_upstream_config_hash
            and canonical_json_bytes(observed.configuration).decode("utf-8")
            == operation.resulting_configuration_json
        )
        unchanged = (
            observed.engineering_config_sha256
            == operation.current_engineering_sha256
            and observed.upstream_config_hash
            == operation.current_upstream_config_hash
        )
        structured_rejection_unchanged = (
            "structured_provider_rejection_received" in dispatch_diagnostics
            and unchanged
            and not exact
        )
        if structured_rejection_unchanged:
            outcome = NormalizedOperationOutcome.OBSERVING
            mismatch_fields: tuple[str, ...] = ()
            diagnostic_codes = (
                "provider_rejection_confirmed_no_change",
                "authoritative_state_unchanged",
                "non_atomic",
                "fallback_none",
            )
        else:
            outcome = (
                NormalizedOperationOutcome.OBSERVING
                if exact
                else NormalizedOperationOutcome.VERIFICATION_MISMATCH
            )
            mismatch_fields = () if exact else ("dashboard_configuration",)
            diagnostic_codes = (
                "exact_full_configuration_match"
                if exact
                else "exact_readback_mismatch",
                "non_atomic",
                "fallback_none",
            )
        return ObservationResult(
            outcome=outcome,
            attempt_count=1,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=True,
            readback_state_fingerprint=observed.engineering_config_sha256,
            intended_result_observed=exact,
            mismatch_fields=mismatch_fields,
            evidence_hash=stable_hash(
                {
                    "target": operation.target.target_id,
                    "observed_sha256": observed.engineering_config_sha256,
                    "observed_upstream_hash": observed.upstream_config_hash,
                    "exact": exact,
                    "unchanged": unchanged,
                    "structured_rejection_unchanged": (
                        structured_rejection_unchanged
                    ),
                }
            ),
            diagnostic_codes=diagnostic_codes,
        )

    async def verify(
        self,
        operation: PreparedDashboardOperation,
        observation: ObservationResult,
    ) -> VerificationResult:
        if not observation.observation_complete:
            return VerificationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=observation.attempt_count,
                verified=None,
                resulting_state_fingerprint=None,
                evidence_hash=observation.evidence_hash,
            )
        if observation.intended_result_observed is True:
            return VerificationResult(
                outcome=NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                attempt_count=observation.attempt_count,
                verified=True,
                resulting_state_fingerprint=observation.readback_state_fingerprint,
                evidence_hash=observation.evidence_hash,
            )
        if (
            "provider_rejection_confirmed_no_change"
            in observation.diagnostic_codes
        ):
            return VerificationResult(
                outcome=NormalizedOperationOutcome.FAILED_POST_DISPATCH,
                attempt_count=observation.attempt_count,
                verified=False,
                resulting_state_fingerprint=(
                    observation.readback_state_fingerprint
                ),
                evidence_hash=observation.evidence_hash,
                manual_review_reason_code=(
                    "provider_rejection_confirmed_no_change"
                ),
            )
        return VerificationResult(
            outcome=NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            attempt_count=observation.attempt_count,
            verified=False,
            resulting_state_fingerprint=observation.readback_state_fingerprint,
            mismatch_fields=observation.mismatch_fields,
            evidence_hash=observation.evidence_hash,
            manual_review_reason_code="dashboard_verification_mismatch",
        )

    async def recover(
        self,
        operation: PreparedDashboardOperation,
        *,
        context: RecoveryContext,
    ) -> ObservationResult:
        dispatch_diagnostics = tuple(
            value
            for value in getattr(context, "dispatch_diagnostic_codes", ())
            if isinstance(value, str)
        )
        return await self._observe_with_dispatch_diagnostics(
            operation, dispatch_diagnostics
        )

    async def prepare_rollback(
        self,
        operation: PreparedDashboardOperation,
        *,
        expected_current_fingerprint: str,
    ) -> PreparedDashboardOperation | None:
        del operation, expected_current_fingerprint
        return None


__all__ = [
    "CAPABILITY_ID",
    "DashboardPreparationRequest",
    "DashboardUpdateAdapter",
    "PreparedDashboardOperation",
]
