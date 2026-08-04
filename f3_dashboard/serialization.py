"""Private durable and bounded public projections for F3-B artifacts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from .json_codec import engineering_sha256
from .models import DashboardUpdateProposal
from .semantic_diff import semantic_diff_projection


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: to_plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [to_plain(item) for item in value]
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    return value


def proposal_hash(proposal: DashboardUpdateProposal) -> str:
    payload = to_plain(proposal)
    payload.pop("proposal_sha256", None)
    return engineering_sha256(payload)


def private_proposal_projection(proposal: DashboardUpdateProposal) -> dict[str, Any]:
    return to_plain(proposal)


def public_proposal_projection(proposal: DashboardUpdateProposal) -> dict[str, Any]:
    """Exclude exact configurations, generated Python, and raw card values."""

    return {
        "model": proposal.model,
        "plan_id": proposal.plan_id,
        "title": proposal.title,
        "description": proposal.description,
        "created_at": proposal.created_at,
        "expires_at": proposal.expires_at,
        "requested_by": proposal.requested_by,
        "target": {"type": proposal.target_type, "identifier": proposal.target_id},
        "evidence": {
            "raw_model": proposal.raw_evidence.model,
            "upstream_config_hash": proposal.raw_evidence.upstream_config_hash,
            "preread_sha256": proposal.raw_evidence.engineering_config_sha256,
            "raw_size_bytes": proposal.raw_evidence.serialized_size_bytes,
            "upstream_version": proposal.raw_evidence.upstream_version,
            "protocol_version": proposal.raw_evidence.protocol_version,
            "compatibility_entry": proposal.raw_evidence.compatibility_entry,
            "completeness": proposal.raw_evidence.completeness,
        },
        "patch": {
            "model": proposal.compilation.model,
            "operation_count": len(proposal.compilation.operations),
            "canonical_patch_sha256": proposal.compilation.canonical_patch_sha256,
            "resulting_sha256": proposal.compilation.resulting_sha256,
            "resulting_upstream_config_hash": proposal.compilation.resulting_upstream_config_hash,
            "resulting_size_bytes": proposal.compilation.resulting_size_bytes,
            "semantic_leaf_change_count": proposal.compilation.semantic_leaf_change_count,
            "generated_transform_model": proposal.compilation.generated_transform_model,
            "generated_transform_sha256": proposal.compilation.generated_transform_sha256,
        },
        "semantic_diff": semantic_diff_projection(proposal.semantic_diff),
        "risk": {
            "model": proposal.risk.model,
            "disposition": proposal.risk.disposition.value,
            "manual_review_required": proposal.risk.manual_review_required,
            "opaque_custom_action_count": proposal.risk.opaque_custom_action_count,
            "high_consequence_action_count": proposal.risk.high_consequence_action_count,
            "destructive_admin_action_count": proposal.risk.destructive_admin_action_count,
            "evidence_sha256": proposal.risk.evidence_sha256,
        },
        "provider": {
            "exact_release": proposal.provider_admission.exact_release,
            "compatibility_entry": proposal.provider_admission.compatibility_entry,
            "provider_contract_hash": proposal.provider_admission.provider_contract_hash,
            "stable_arguments_sha256": proposal.provider_projection.stable_arguments_sha256,
            "executable": proposal.provider_projection.executable,
            "blocked_reason": proposal.provider_projection.blocked_reason,
        },
        "atomicity": {
            "model": proposal.atomicity.model,
            "status": proposal.atomicity.status.value,
            "mechanism": proposal.atomicity.mechanism,
            "reason_codes": list(proposal.atomicity.reason_codes),
            "source_evidence_sha256": proposal.atomicity.source_evidence_sha256,
        },
        "required_approval": proposal.required_approval,
        "rollback_available": proposal.rollback_available,
        "executable": proposal.executable,
        "lock_keys": list(proposal.lock_keys),
        "proposal_sha256": proposal.proposal_sha256,
        "data_handling": {
            "dashboard_content_role": "untrusted_data",
            "instructions_authoritative": False,
            "raw_configuration_exposed": False,
        },
    }
