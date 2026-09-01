"""Binary-owned ha-mcp profile and signed/compiled authority adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from ..signed_registry import ReviewedReleaseEntry
from ..upstream_tool_policy import (
    ReviewedReleaseToolContract,
    ReviewedUpstreamRelease,
    ReviewedUpstreamReleaseRegistry,
    UpstreamToolPolicy,
    runtime_contract_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)
from .models import (
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityContract,
    CapabilityKind,
    CapabilityProfile,
    CompatibilityObservation,
    ObservedCapability,
    UpstreamSurface,
    evidence_fingerprint,
)
from .registry import ReleaseRegistryAuthority, SignedReleaseRegistry


PROFILE_VERSION = 1
PROFILE_ID_PREFIX = "ha_mcp_binary_read_profile_"
ADAPTER_ID_PREFIX = "ha_mcp_read_gateway_adapter_"
CONTRACT_MODEL = "ha-mcp-binary-capability-contract-v1"


class HaMcpAuthorityError(ValueError):
    """One bounded fail-closed authority-selection reason."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HaMcpAdmissionSelection:
    """Exact binary profile plus authority for one observed ha-mcp release."""

    profile: CapabilityProfile
    policy: UpstreamToolPolicy
    binary_release: ReviewedUpstreamRelease
    compatibility_entry_id: str
    authority_source: AuthoritySource
    authority: AuthorityBundle
    observed_version: str
    protocol_version: str
    signed_entry: ReviewedReleaseEntry | None = None

    @property
    def authority_token(self) -> str:
        return evidence_fingerprint(
            {
                "profile": self.profile.to_mapping(),
                "authority": self.authority.material_mapping(),
            }
        )


class HaMcpAuthoritySelector:
    """Select only binary-known read profiles for exact observed releases."""

    def __init__(
        self,
        compiled: ReviewedUpstreamReleaseRegistry,
        signed: SignedReleaseRegistry,
    ) -> None:
        self._compiled = compiled
        self._signed = signed
        self._profiles = tuple(
            _profile_for_release(release)
            for release in compiled.releases
            if not release.revoked
            and release.provider_disposition("read_gateway") != "held"
        )
        self._by_policy_binding: dict[
            tuple[str, str], tuple[ReviewedUpstreamRelease, CapabilityProfile]
        ] = {}
        for release, profile in zip(
            (
                item
                for item in compiled.releases
                if not item.revoked
                and item.provider_disposition("read_gateway") != "held"
            ),
            self._profiles,
            strict=True,
        ):
            key = (release.policy_resource, release.policy_sha256)
            if key in self._by_policy_binding:
                raise HaMcpAuthorityError(
                    "binary_profile_binding_duplicate"
                )
            self._by_policy_binding[key] = (release, profile)

    @property
    def signed_registry(self) -> SignedReleaseRegistry:
        return self._signed

    def select(
        self,
        *,
        server_name: str,
        version: str,
        protocol_version: str,
        evaluated_at: datetime | None = None,
    ) -> HaMcpAdmissionSelection:
        now = evaluated_at or self._signed.evaluated_at()
        if now.tzinfo is None or now.utcoffset() is None:
            raise HaMcpAuthorityError("authority_clock_invalid")
        evaluated_epoch = int(now.astimezone(timezone.utc).timestamp())
        registry = self._signed.authority()
        compiled = self._compiled.by_version.get(version)
        if (
            compiled is not None
            and compiled.provider_disposition("read_gateway") == "held"
        ):
            compiled = None
        signed_entry = registry.entry_for(server_name, version)
        selected = (
            (compiled, _profile_for_release(compiled))
            if compiled is not None
            else self._select_signed_profile(signed_entry)
        )
        if selected is None:
            raise HaMcpAuthorityError("positive_authority_missing")
        binary_release, profile = selected
        if (
            compiled is None
            and signed_entry is not None
            and protocol_version
            not in signed_entry.allowed_protocol_versions
        ):
            raise HaMcpAuthorityError(
                "identity_or_protocol_disagreement"
            )
        if (
            server_name != profile.expected_identity
            or protocol_version not in profile.supported_protocols
        ):
            raise HaMcpAuthorityError(
                "identity_or_protocol_disagreement"
            )

        if registry.revoked(server_name, version):
            authority = AuthorityBundle(
                evaluated_at_epoch=evaluated_epoch,
                decisions=(
                    _authority_decision(
                        profile=profile,
                        source=AuthoritySource.SIGNED_REGISTRY,
                        status=AuthorityStatus.DENY_ONLY,
                        identity=server_name,
                        version=version,
                        protocol=protocol_version,
                        capability_ids=(),
                        reason_code="retained_signed_revocation",
                        registry=registry,
                    ),
                ),
            )
            source = AuthoritySource.SIGNED_REGISTRY
            compatibility_entry_id = (
                signed_entry.entry_id
                if signed_entry is not None
                else binary_release.entry_id
            )
        elif compiled is not None:
            authority = AuthorityBundle(
                evaluated_at_epoch=evaluated_epoch,
                decisions=(
                    _authority_decision(
                        profile=profile,
                        source=AuthoritySource.COMPILED_EXACT,
                        status=AuthorityStatus.POSITIVE,
                        identity=server_name,
                        version=version,
                        protocol=protocol_version,
                        capability_ids=tuple(
                            item.capability_id
                            for item in profile.capabilities
                        ),
                        reason_code="compiled_exact_release",
                    ),
                ),
            )
            source = AuthoritySource.COMPILED_EXACT
            compatibility_entry_id = compiled.entry_id
        elif signed_entry is not None:
            capability_ids = _signed_matching_capabilities(
                signed_entry,
                binary_release,
                profile,
            )
            authority = AuthorityBundle(
                evaluated_at_epoch=evaluated_epoch,
                decisions=(
                    _authority_decision(
                        profile=profile,
                        source=AuthoritySource.SIGNED_REGISTRY,
                        status=AuthorityStatus.POSITIVE,
                        identity=server_name,
                        version=version,
                        protocol=protocol_version,
                        capability_ids=capability_ids,
                        reason_code="signed_compatible_release",
                        registry=registry,
                        expires_at_epoch=int(
                            datetime.strptime(
                                signed_entry_to_expiry(
                                    signed_entry,
                                    registry,
                                ),
                                "%Y-%m-%dT%H:%M:%SZ",
                            )
                            .replace(tzinfo=timezone.utc)
                            .timestamp()
                        ),
                    ),
                ),
            )
            source = AuthoritySource.SIGNED_REGISTRY
            compatibility_entry_id = signed_entry.entry_id
        else:
            raise HaMcpAuthorityError("positive_authority_missing")

        return HaMcpAdmissionSelection(
            profile=profile,
            policy=binary_release.policy,
            binary_release=binary_release,
            compatibility_entry_id=compatibility_entry_id,
            authority_source=source,
            authority=authority,
            observed_version=version,
            protocol_version=protocol_version,
            signed_entry=(
                signed_entry
                if source is AuthoritySource.SIGNED_REGISTRY
                else None
            ),
        )

    def _select_signed_profile(
        self,
        entry: ReviewedReleaseEntry | None,
    ) -> tuple[ReviewedUpstreamRelease, CapabilityProfile] | None:
        if entry is None:
            return None
        return self._by_policy_binding.get(
            (entry.policy_resource, entry.policy_sha256)
        )


def signed_entry_to_expiry(
    entry: ReviewedReleaseEntry,
    registry: ReleaseRegistryAuthority,
) -> str:
    del entry
    if registry.envelope is None:
        raise HaMcpAuthorityError("signed_positive_authority_expired")
    return registry.envelope.expires_at


def observation_for_catalog(
    catalog: Any,
    selection: HaMcpAdmissionSelection,
) -> CompatibilityObservation:
    """Translate one complete catalog into binary-classified observations."""

    by_name = selection.policy.by_name
    contracts = selection.binary_release.tool_contracts_by_name
    observed: list[ObservedCapability] = []
    for raw in tuple(catalog.tools):
        if not isinstance(raw, dict):
            observed.append(
                ObservedCapability(
                    capability_id="invalid_descriptor",
                    kind=CapabilityKind.MIXED,
                    contract_fingerprint=evidence_fingerprint(
                        {"invalid_descriptor": True}
                    ),
                )
            )
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name or len(name) > 128:
            safe_id = "invalid_descriptor"
            contract = evidence_fingerprint(
                {"invalid_descriptor": True}
            )
            kind = CapabilityKind.MIXED
        elif name in by_name and name in contracts:
            safe_id = name
            entry = by_name[name]
            kind = _capability_kind(entry.classification)
            contract = _observed_contract_fingerprint(
                tool=raw,
                policy_entry=entry,
                release_contract=contracts[name],
                runtime_model=(
                    selection.binary_release
                    .runtime_contract_fingerprint_model
                ),
            )
        else:
            safe_id = name
            kind = CapabilityKind.MIXED
            try:
                contract = evidence_fingerprint(
                    {
                        "model": "ha-mcp-uncompiled-descriptor-v1",
                        "descriptor_fingerprint": schema_fingerprint(raw),
                    }
                )
            except Exception:
                contract = evidence_fingerprint(
                    {"invalid_uncompiled_descriptor": True}
                )
        observed.append(
            ObservedCapability(
                capability_id=safe_id,
                kind=kind,
                contract_fingerprint=contract,
            )
        )
    session_id = getattr(catalog, "session_id", "") or evidence_fingerprint(
        {
            "server_name": catalog.server_name,
            "server_version": catalog.server_version,
            "protocol_version": catalog.protocol_version,
            "session_scope": "configured-ha-mcp-gateway",
        }
    )
    return CompatibilityObservation(
        surface=UpstreamSurface.HA_MCP,
        identity=catalog.server_name,
        version=catalog.server_version,
        protocol_version=catalog.protocol_version,
        session_id=session_id,
        capabilities=tuple(observed),
        connected=True,
        authenticated=True,
        catalog_complete=bool(
            getattr(catalog, "catalog_complete", True)
        ),
        evidence_reason="observation_complete",
    )


def _profile_for_release(
    release: ReviewedUpstreamRelease,
) -> CapabilityProfile:
    policy = release.policy
    contracts = release.tool_contracts_by_name
    capabilities = tuple(
        CapabilityContract(
            capability_id=entry.upstream_name,
            kind=CapabilityKind.ORDINARY_READ,
            contract_fingerprint=_expected_contract_fingerprint(
                tool_name=entry.upstream_name,
                argument_restrictions=entry.argument_restrictions,
                release_contract=contracts[entry.upstream_name],
            ),
        )
        for entry in policy.tools
        if entry.classification == "automatic_read"
    )
    digest_id = release.policy_sha256.removeprefix("sha256:")[:16]
    return CapabilityProfile(
        profile_id=f"{PROFILE_ID_PREFIX}{digest_id}",
        profile_version=PROFILE_VERSION,
        surface=UpstreamSurface.HA_MCP,
        adapter_id=f"{ADAPTER_ID_PREFIX}{digest_id}",
        expected_identity=release.server_name,
        supported_protocols=release.allowed_protocol_versions,
        capabilities=capabilities,
    )


def _expected_contract_fingerprint(
    *,
    tool_name: str,
    argument_restrictions: Sequence[str],
    release_contract: ReviewedReleaseToolContract,
) -> str:
    return evidence_fingerprint(
        {
            "model": CONTRACT_MODEL,
            "tool_name": tool_name,
            "classification": "automatic_read",
            "argument_restrictions": list(argument_restrictions),
            "input_schema_fingerprint": (
                release_contract.input_schema_fingerprint
            ),
            "description_fingerprint": (
                release_contract.description_fingerprint
            ),
            "annotation_fingerprint": (
                release_contract.annotation_fingerprint
            ),
            "output_contract_fingerprint": (
                release_contract.output_contract_fingerprint
            ),
            "runtime_contract_fingerprint": (
                release_contract.runtime_contract_fingerprint
            ),
        }
    )


def _observed_contract_fingerprint(
    *,
    tool: dict[str, Any],
    policy_entry: Any,
    release_contract: ReviewedReleaseToolContract,
    runtime_model: str,
) -> str:
    try:
        description = runtime_description_fingerprint(
            tool.get("description")
        )
        components = {
            "input_schema_fingerprint": schema_fingerprint(
                tool.get("inputSchema")
            ),
            "description_fingerprint": (
                description
                or schema_fingerprint({"invalid_description": True})
            ),
            "annotation_fingerprint": schema_fingerprint(
                {
                    "present": "annotations" in tool,
                    "value": tool.get("annotations"),
                }
            ),
            "output_contract_fingerprint": schema_fingerprint(
                {
                    "present": "outputSchema" in tool,
                    "value": tool.get("outputSchema"),
                }
            ),
            "runtime_contract_fingerprint": runtime_contract_fingerprint(
                tool,
                model=runtime_model,
            ),
        }
    except Exception:
        components = {
            name: schema_fingerprint({"invalid": name})
            for name in (
                "input_schema_fingerprint",
                "description_fingerprint",
                "annotation_fingerprint",
                "output_contract_fingerprint",
                "runtime_contract_fingerprint",
            )
        }
    del release_contract
    return evidence_fingerprint(
        {
            "model": CONTRACT_MODEL,
            "tool_name": tool.get("name"),
            "classification": policy_entry.classification,
            "argument_restrictions": list(
                policy_entry.argument_restrictions
            ),
            **components,
        }
    )


def _signed_matching_capabilities(
    entry: ReviewedReleaseEntry,
    release: ReviewedUpstreamRelease,
    profile: CapabilityProfile,
) -> tuple[str, ...]:
    signed = {item.tool_name: item for item in entry.tool_contracts}
    compiled = release.tool_contracts_by_name
    policy = release.policy.by_name
    matched: list[str] = []
    for capability in profile.capabilities:
        name = capability.capability_id
        remote = signed.get(name)
        known = compiled.get(name)
        policy_entry = policy.get(name)
        if remote is None or known is None or policy_entry is None:
            continue
        if (
            remote.policy_classification == "automatic_read"
            and remote.reviewed_automatic_read
            and remote.quarantine_reason is None
            and remote.input_schema_fingerprint
            == known.input_schema_fingerprint
            and remote.description_fingerprint
            == known.description_fingerprint
            and remote.annotation_fingerprint
            == known.annotation_fingerprint
            and remote.output_contract_fingerprint
            == known.output_contract_fingerprint
            and remote.runtime_contract_fingerprint
            == known.runtime_contract_fingerprint
            and remote.argument_restrictions
            == policy_entry.argument_restrictions
        ):
            matched.append(name)
    return tuple(sorted(matched))


def _authority_decision(
    *,
    profile: CapabilityProfile,
    source: AuthoritySource,
    status: AuthorityStatus,
    identity: str,
    version: str,
    protocol: str,
    capability_ids: tuple[str, ...],
    reason_code: str,
    registry: ReleaseRegistryAuthority | None = None,
    expires_at_epoch: int | None = None,
) -> AuthorityDecision:
    return AuthorityDecision(
        source=source,
        status=status,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        adapter_id=profile.adapter_id,
        subject_identity=identity,
        subject_version=version,
        protocol_version=protocol,
        capability_ids=capability_ids,
        reason_code=reason_code,
        registry_sequence=(
            registry.sequence
            if source is AuthoritySource.SIGNED_REGISTRY and registry
            else None
        ),
        registry_digest=(
            registry.content_digest
            if source is AuthoritySource.SIGNED_REGISTRY and registry
            else None
        ),
        expires_at_epoch=(
            expires_at_epoch
            if source is AuthoritySource.SIGNED_REGISTRY
            else None
        ),
    )


def _capability_kind(classification: str) -> CapabilityKind:
    return {
        "automatic_read": CapabilityKind.ORDINARY_READ,
        "persistent_write": CapabilityKind.PERSISTENT_WRITE,
        "physical_or_high_risk_action": CapabilityKind.ACTION,
        "mixed_or_requires_wrapper": CapabilityKind.MIXED,
    }.get(classification, CapabilityKind.MIXED)
