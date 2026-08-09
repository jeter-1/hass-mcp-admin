"""Exact-release dashboard setter admission and planning binding."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Final

from .atomicity import require_executable_atomicity
from .constants import PROTOCOL_VERSION, SHA256, UPSTREAM_CONFIG_HASH
from .errors import AtomicityGateError, ProviderAdmissionError
from .json_codec import engineering_sha256
from .models import (
    AtomicityDecision,
    PatchCompilation,
    ProviderAdmission,
    ProviderPlanningProjection,
    ProviderResponseEvidence,
    ProviderRuntimeEvidence,
)


TOOL_NAME = "ha_config_set_dashboard"
COMMON_INPUT_SCHEMA_FINGERPRINT = (
    "a7d11d72710f1c39937bfc864291f6d0936b2d4feb68dc4ff049eda3b91a3ac1"
)
COMMON_ANNOTATION_FINGERPRINT = (
    "257ce08c1f5c5920ef67ff72325abbb942721664158b1012cc94127e50acfde5"
)
COMMON_DESCRIPTION_FINGERPRINT = (
    "97fdcfaf0c05e07e11113ebccbc7c1c964ab906658c642f5c34e853595d9870b"
)
COMMON_OUTPUT_CONTRACT_FINGERPRINT = (
    "f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109"
)

EXACT_CONTRACTS: Final = {
    "7.14.2": ProviderRuntimeEvidence(
        upstream_version="7.14.2",
        protocol_version=PROTOCOL_VERSION,
        compatibility_entry="ha-mcp-v7.14.2-7917b2d3",
        source_commit="904c14ebbe76de700f7c3535f5cc71c017dca12e",
        tool_name=TOOL_NAME,
        input_schema_fingerprint=COMMON_INPUT_SCHEMA_FINGERPRINT,
        annotation_fingerprint=COMMON_ANNOTATION_FINGERPRINT,
        description_fingerprint=COMMON_DESCRIPTION_FINGERPRINT,
        output_contract_fingerprint=COMMON_OUTPUT_CONTRACT_FINGERPRINT,
        runtime_contract_fingerprint="a97e6bc4a001b124e142d83df01118aad7e1c9ebd42845ca88bbcaf1b0c189b8",
        policy_classification="persistent_write",
    ),
    "8.0.0": ProviderRuntimeEvidence(
        upstream_version="8.0.0",
        protocol_version=PROTOCOL_VERSION,
        compatibility_entry="ha-mcp-v8.0.0-d65630f6",
        source_commit="9dd3ac620e3149cd34ec3c990b6ee81e778191f2",
        tool_name=TOOL_NAME,
        input_schema_fingerprint=COMMON_INPUT_SCHEMA_FINGERPRINT,
        annotation_fingerprint=COMMON_ANNOTATION_FINGERPRINT,
        description_fingerprint=COMMON_DESCRIPTION_FINGERPRINT,
        output_contract_fingerprint=COMMON_OUTPUT_CONTRACT_FINGERPRINT,
        runtime_contract_fingerprint="42c5f8769ab712b5299b71a5bd56c489214a7f04b528fb8a1cfb3feb869617b5",
        policy_classification="persistent_write",
    ),
    "8.1.1": ProviderRuntimeEvidence(
        upstream_version="8.1.1",
        protocol_version=PROTOCOL_VERSION,
        compatibility_entry="ha-mcp-v8.1.1-e1d76a6e",
        source_commit="ae84694b50bfbd8d507042381fdee5e529bf73c5",
        tool_name=TOOL_NAME,
        input_schema_fingerprint=COMMON_INPUT_SCHEMA_FINGERPRINT,
        annotation_fingerprint=COMMON_ANNOTATION_FINGERPRINT,
        description_fingerprint=COMMON_DESCRIPTION_FINGERPRINT,
        output_contract_fingerprint=COMMON_OUTPUT_CONTRACT_FINGERPRINT,
        runtime_contract_fingerprint="42c5f8769ab712b5299b71a5bd56c489214a7f04b528fb8a1cfb3feb869617b5",
        policy_classification="persistent_write",
    ),
}

POTENTIAL_EPHEMERAL_ARGUMENT_NAMES = ("BestPracticeKey",)
PROHIBITED_ARGUMENT_NAMES = (
    "python_transform",
    "title",
    "icon",
    "require_admin",
    "show_in_sidebar",
    "view_path",
    "resources",
    "preferences",
)


def admit_provider_contract(evidence: ProviderRuntimeEvidence) -> ProviderAdmission:
    expected = EXACT_CONTRACTS.get(evidence.upstream_version)
    if expected is None:
        raise ProviderAdmissionError("Unknown ha-mcp release is not admitted")
    mismatches = tuple(
        field
        for field, expected_value in asdict(expected).items()
        if getattr(evidence, field) != expected_value
    )
    if mismatches:
        raise ProviderAdmissionError(
            "Exact dashboard setter contract mismatch: " + ",".join(mismatches)
        )
    projection = asdict(expected)
    return ProviderAdmission(
        admitted_for_planning=True,
        executable=True,
        exact_release=expected.upstream_version,
        compatibility_entry=expected.compatibility_entry,
        provider_contract_hash=engineering_sha256(projection),
        diagnostic_codes=(
            "exact_release_contract_reviewed",
            "operator_accepted_non_atomic_contract",
        ),
    )


def build_provider_projection(
    *,
    admission: ProviderAdmission,
    compilation: PatchCompilation,
    url_path: str,
    current_config_hash: str,
    atomicity: AtomicityDecision,
) -> ProviderPlanningProjection:
    if not admission.admitted_for_planning:
        raise ProviderAdmissionError("Provider contract was not admitted")
    if not UPSTREAM_CONFIG_HASH.fullmatch(current_config_hash):
        raise ProviderAdmissionError("Current config_hash is malformed")
    binding: dict[str, Any] = {
        "tool_name": TOOL_NAME,
        "target_url_path": url_path,
        "current_config_hash": current_config_hash,
        "resulting_configuration_sha256": compilation.resulting_sha256,
        "resulting_upstream_config_hash": compilation.resulting_upstream_config_hash,
        "resulting_size_bytes": compilation.resulting_size_bytes,
    }
    if not SHA256.fullmatch(compilation.resulting_sha256):
        raise ProviderAdmissionError("Resulting configuration hash is malformed")
    require_executable_atomicity(atomicity)
    executable = admission.executable
    blocked_reasons: list[str] = []
    return ProviderPlanningProjection(
        tool_name=TOOL_NAME,
        target_url_path=url_path,
        current_config_hash=current_config_hash,
        resulting_configuration_sha256=compilation.resulting_sha256,
        resulting_upstream_config_hash=compilation.resulting_upstream_config_hash,
        resulting_size_bytes=compilation.resulting_size_bytes,
        binding_sha256=engineering_sha256(binding),
        potential_ephemeral_argument_names=POTENTIAL_EPHEMERAL_ARGUMENT_NAMES,
        prohibited_argument_names=PROHIBITED_ARGUMENT_NAMES,
        executable=executable,
        blocked_reason=";".join(blocked_reasons),
    )


def require_executable_projection(projection: ProviderPlanningProjection) -> None:
    """Require the exact reviewed provider and explicit concurrency policy."""

    if not projection.executable:
        raise AtomicityGateError("Dashboard provider projection is not executable")


def project_provider_response(
    payload: Any,
    *,
    expected_url_path: str,
) -> ProviderResponseEvidence:
    """Retain only the reviewed config-mode update response fields."""

    if not isinstance(payload, dict):
        raise ProviderAdmissionError("Unsupported dashboard setter response model")
    required = {
        "success",
        "action",
        "url_path",
        "dashboard_created",
        "config_updated",
        "metadata_updated",
    }
    if not required.issubset(payload):
        raise ProviderAdmissionError("Dashboard setter response is incomplete")
    if payload.get("success") is not True or payload.get("action") != "update":
        raise ProviderAdmissionError("Dashboard setter did not claim config-mode update success")
    if payload.get("url_path") != expected_url_path:
        raise ProviderAdmissionError("Dashboard setter response target mismatch")
    if (
        payload.get("dashboard_created") is not False
        or payload.get("config_updated") is not True
        or payload.get("metadata_updated") is not False
    ):
        raise ProviderAdmissionError("Dashboard setter response is not one exact existing-config update")
    projection = {
        "success": True,
        "action": "update",
        "url_path": expected_url_path,
        "dashboard_created": False,
        "config_updated": True,
        "metadata_updated": False,
    }
    return ProviderResponseEvidence(
        response_received=True,
        success_claimed=True,
        write_committed_claimed=True,
        post_write_verified_claimed=False,
        upstream_config_hash=None,
        response_evidence_sha256=engineering_sha256(projection),
        diagnostic_codes=(
            "provider_response_is_not_verification",
            "exact_existing_config_update_claimed",
            "readback_required",
        ),
    )


def validate_fingerprint(value: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ProviderAdmissionError("Provider fingerprint is malformed")
