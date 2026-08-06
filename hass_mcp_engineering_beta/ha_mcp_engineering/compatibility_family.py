"""Deterministic compatibility-family evidence and admission decisions.

Families are review-time policy, never runtime wildcard authority.  A family
decision may authorize generation of one exact release entry only after the
candidate's immutable identity and two exact runtime captures are bound.  The
running server still selects a release solely by an exact compiled entry.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


FAMILY_POLICY_SCHEMA_VERSION = 1
FAMILY_DECISION_SCHEMA_VERSION = 1
FAMILY_POLICY_PATH = Path(__file__).with_name(
    "upstream_compatibility_families.json"
)

DRIFT_CATEGORIES = frozenset(
    {
        "immutable_identity_only",
        "packaging_or_dependency_only",
        "documentation_only",
        "descriptor_wording_unchanged_semantics",
        "deployment_dynamic_metadata",
        "input_schema",
        "output_schema",
        "annotations",
        "tool_addition_removal_or_rename",
        "classification_change",
        "consumed_response_envelope",
        "lifecycle_provider",
        "dashboard_provider",
        "security_or_transport_behavior",
        "unknown_drift",
    }
)

AUTOMATIC_NON_SEMANTIC_CATEGORIES = frozenset(
    {
        "immutable_identity_only",
        "packaging_or_dependency_only",
        "documentation_only",
        "descriptor_wording_unchanged_semantics",
        "deployment_dynamic_metadata",
    }
)

MATERIAL_DRIFT_CATEGORIES = DRIFT_CATEGORIES - (
    AUTOMATIC_NON_SEMANTIC_CATEGORIES
)

PROVIDER_SURFACES = frozenset(
    {"read_gateway", "dashboard", "backup", "lifecycle"}
)
PROVIDER_DISPOSITIONS = frozenset({"admitted", "partial", "held"})
RELEASE_OUTCOMES = frozenset(
    {"admitted_automatic", "admitted_with_selective_holds", "rejected"}
)
NON_CATALOG_DRIFT_CATEGORIES = frozenset(
    {
        "immutable_identity_only",
        "packaging_or_dependency_only",
        "documentation_only",
        "classification_change",
        "consumed_response_envelope",
        "lifecycle_provider",
        "dashboard_provider",
        "security_or_transport_behavior",
        "unknown_drift",
    }
)
GLOBAL_REJECTION_CATEGORIES = frozenset(
    {
        "classification_change",
        "consumed_response_envelope",
        "security_or_transport_behavior",
        "unknown_drift",
    }
)

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]{0,3})\."
    r"(0|[1-9][0-9]{0,3})\."
    r"(0|[1-9][0-9]{0,3})$"
)
_FAMILY_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{2,79}$")
_RESOURCE = re.compile(
    r"^docs/evidence/upstream-read-compatibility/"
    r"ha-mcp-[0-9]+\.[0-9]+\.[0-9]+-family-decision\.json$"
)


class CompatibilityFamilyError(ValueError):
    """A family policy, observation, or decision failed closed."""


def _reject_duplicate_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise CompatibilityFamilyError("duplicate_json_member")
        value[name] = item
    return value


def _reject_nonfinite(_value: str) -> None:
    raise CompatibilityFamilyError("nonfinite_json_value")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def load_strict_json(path: Path, *, error: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompatibilityFamilyError(error) from exc


def sha256_resource(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CompatibilityFamilyError("family_resource_unreadable") from exc


def semantic_version(value: Any, *, error: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise CompatibilityFamilyError(error)
    matched = _SEMVER.fullmatch(value)
    if matched is None:
        raise CompatibilityFamilyError(error)
    return tuple(int(part) for part in matched.groups())


def _exact_keys(value: Any, expected: set[str], *, error: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CompatibilityFamilyError(error)
    return value


def _bounded_text(value: Any, *, error: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= maximum:
        raise CompatibilityFamilyError(error)
    return value


@dataclass(frozen=True)
class BaselineIdentity:
    version: str
    entry_id: str
    source_commit: str
    image_index_digest: str
    capture_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class DescriptorNormalizationRule:
    rule_id: str
    tool_name: str
    field: str
    normalizer: str


@dataclass(frozen=True)
class CompatibilityFamily:
    family_id: str
    server_name: str
    baseline: BaselineIdentity
    eligible_major: int
    eligible_minor: int
    minimum_patch: int
    allowed_protocol_versions: tuple[str, ...]
    allowed_non_semantic_drift: frozenset[str]
    material_drift: frozenset[str]
    descriptor_normalization_rules: tuple[DescriptorNormalizationRule, ...]
    required_evidence: tuple[str, ...]
    provider_surface_tools: tuple[tuple[str, tuple[str, ...]], ...]
    unknown_drift_disposition: str

    def candidate_is_eligible(self, version: str) -> bool:
        major, minor, patch = semantic_version(
            version, error="family_candidate_version_invalid"
        )
        return (
            major == self.eligible_major
            and minor == self.eligible_minor
            and patch >= self.minimum_patch
            and version != self.baseline.version
        )

    @property
    def provider_tools(self) -> dict[str, tuple[str, ...]]:
        return dict(self.provider_surface_tools)

    def normalization_rule(
        self, tool_name: str, field: str
    ) -> DescriptorNormalizationRule | None:
        matches = [
            rule
            for rule in self.descriptor_normalization_rules
            if rule.tool_name == tool_name and rule.field == field
        ]
        if len(matches) > 1:
            raise CompatibilityFamilyError(
                "family_descriptor_normalization_ambiguous"
            )
        return matches[0] if matches else None


@dataclass(frozen=True)
class CompatibilityFamilyRegistry:
    schema_version: int
    families: tuple[CompatibilityFamily, ...]

    @property
    def by_id(self) -> dict[str, CompatibilityFamily]:
        return {family.family_id: family for family in self.families}


@dataclass(frozen=True)
class FamilyAdmissionBinding:
    family_id: str
    baseline_entry_id: str
    policy_resource: str
    policy_sha256: str
    decision_resource: str
    decision_sha256: str
    outcome: str


@dataclass(frozen=True)
class FamilyComparison:
    outcome: str
    observed_drift_categories: tuple[str, ...]
    material_drift_categories: tuple[str, ...]
    unknown_drift: tuple[str, ...]
    unchanged_tools: tuple[str, ...]
    normalized_descriptor_tools: tuple[str, ...]
    held_automatic_reads: tuple[str, ...]
    nondelegated_changed_tools: tuple[str, ...]
    provider_dispositions: tuple[tuple[str, str], ...]

    @property
    def admitted(self) -> bool:
        return self.outcome != "rejected"


def load_compatibility_families(
    path: Path = FAMILY_POLICY_PATH,
) -> CompatibilityFamilyRegistry:
    raw = load_strict_json(path, error="family_policy_unreadable")
    document = _exact_keys(
        raw,
        {"schema_version", "families"},
        error="family_policy_fields_invalid",
    )
    if document["schema_version"] != FAMILY_POLICY_SCHEMA_VERSION:
        raise CompatibilityFamilyError("family_policy_schema_invalid")
    raw_families = document["families"]
    if not isinstance(raw_families, list) or not raw_families:
        raise CompatibilityFamilyError("family_policy_entries_invalid")
    families = tuple(_load_family(item) for item in raw_families)
    ids = [family.family_id for family in families]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise CompatibilityFamilyError("family_policy_order_invalid")
    baselines = [family.baseline.entry_id for family in families]
    if len(baselines) != len(set(baselines)):
        raise CompatibilityFamilyError("family_policy_baseline_duplicate")
    return CompatibilityFamilyRegistry(
        schema_version=document["schema_version"], families=families
    )


def _load_family(value: Any) -> CompatibilityFamily:
    raw = _exact_keys(
        value,
        {
            "family_id",
            "server_name",
            "baseline",
            "eligible_version",
            "allowed_protocol_versions",
            "allowed_non_semantic_drift",
            "material_drift_categories",
            "descriptor_normalization_rules",
            "required_evidence",
            "provider_surface_tools",
            "unknown_drift_disposition",
        },
        error="family_policy_entry_fields_invalid",
    )
    family_id = raw["family_id"]
    if not isinstance(family_id, str) or not _FAMILY_ID.fullmatch(family_id):
        raise CompatibilityFamilyError("family_policy_id_invalid")
    if raw["server_name"] != "ha-mcp":
        raise CompatibilityFamilyError("family_policy_server_invalid")
    baseline_raw = _exact_keys(
        raw["baseline"],
        {
            "version",
            "entry_id",
            "source_commit",
            "image_index_digest",
            "capture_sha256",
            "policy_sha256",
        },
        error="family_policy_baseline_fields_invalid",
    )
    semantic_version(
        baseline_raw["version"], error="family_policy_baseline_version_invalid"
    )
    if (
        not isinstance(baseline_raw["entry_id"], str)
        or not baseline_raw["entry_id"].startswith(
            f"ha-mcp-v{baseline_raw['version']}-"
        )
        or not isinstance(baseline_raw["source_commit"], str)
        or not _COMMIT.fullmatch(baseline_raw["source_commit"])
        or any(
            not isinstance(baseline_raw[name], str)
            or not _SHA256.fullmatch(baseline_raw[name])
            for name in (
                "image_index_digest",
                "capture_sha256",
                "policy_sha256",
            )
        )
    ):
        raise CompatibilityFamilyError("family_policy_baseline_invalid")
    eligible = _exact_keys(
        raw["eligible_version"],
        {"major", "minor", "minimum_patch", "prerelease_allowed"},
        error="family_policy_eligible_version_fields_invalid",
    )
    if (
        any(type(eligible[name]) is not int or eligible[name] < 0 for name in ("major", "minor", "minimum_patch"))
        or eligible["prerelease_allowed"] is not False
    ):
        raise CompatibilityFamilyError("family_policy_eligible_version_invalid")
    baseline_parts = semantic_version(
        baseline_raw["version"], error="family_policy_baseline_version_invalid"
    )
    if tuple(baseline_parts[:2]) != (eligible["major"], eligible["minor"]):
        raise CompatibilityFamilyError("family_policy_family_mismatch")
    protocols = raw["allowed_protocol_versions"]
    if (
        not isinstance(protocols, list)
        or protocols != sorted(set(protocols))
        or protocols != ["2025-03-26"]
    ):
        raise CompatibilityFamilyError("family_policy_protocols_invalid")
    allowed = _category_set(
        raw["allowed_non_semantic_drift"],
        error="family_policy_allowed_drift_invalid",
    )
    material = _category_set(
        raw["material_drift_categories"],
        error="family_policy_material_drift_invalid",
    )
    if (
        not allowed <= AUTOMATIC_NON_SEMANTIC_CATEGORIES
        or not MATERIAL_DRIFT_CATEGORIES <= material
        or allowed & material
    ):
        raise CompatibilityFamilyError("family_policy_drift_partition_invalid")
    rules_raw = raw["descriptor_normalization_rules"]
    if not isinstance(rules_raw, list):
        raise CompatibilityFamilyError(
            "family_descriptor_normalization_rules_invalid"
        )
    rules = tuple(_load_normalization_rule(item) for item in rules_raw)
    rule_keys = [(rule.tool_name, rule.field) for rule in rules]
    if rule_keys != sorted(rule_keys) or len(rule_keys) != len(set(rule_keys)):
        raise CompatibilityFamilyError(
            "family_descriptor_normalization_rules_invalid"
        )
    required = raw["required_evidence"]
    if (
        not isinstance(required, list)
        or not required
        or required != sorted(set(required))
        or any(not isinstance(item, str) or not 1 <= len(item) <= 96 for item in required)
    ):
        raise CompatibilityFamilyError("family_required_evidence_invalid")
    surfaces_raw = raw["provider_surface_tools"]
    if not isinstance(surfaces_raw, dict) or set(surfaces_raw) != PROVIDER_SURFACES:
        raise CompatibilityFamilyError("family_provider_surfaces_invalid")
    surfaces: list[tuple[str, tuple[str, ...]]] = []
    for surface, names in sorted(surfaces_raw.items()):
        if (
            not isinstance(names, list)
            or names != sorted(set(names))
            or any(not isinstance(name, str) or not name for name in names)
        ):
            raise CompatibilityFamilyError("family_provider_surfaces_invalid")
        surfaces.append((surface, tuple(names)))
    if raw["unknown_drift_disposition"] != "reject_candidate":
        raise CompatibilityFamilyError("family_unknown_disposition_invalid")
    return CompatibilityFamily(
        family_id=family_id,
        server_name=raw["server_name"],
        baseline=BaselineIdentity(**baseline_raw),
        eligible_major=eligible["major"],
        eligible_minor=eligible["minor"],
        minimum_patch=eligible["minimum_patch"],
        allowed_protocol_versions=tuple(protocols),
        allowed_non_semantic_drift=frozenset(allowed),
        material_drift=frozenset(material),
        descriptor_normalization_rules=rules,
        required_evidence=tuple(required),
        provider_surface_tools=tuple(surfaces),
        unknown_drift_disposition=raw["unknown_drift_disposition"],
    )


def _category_set(value: Any, *, error: str) -> set[str]:
    if (
        not isinstance(value, list)
        or value != sorted(set(value))
        or any(not isinstance(item, str) or item not in DRIFT_CATEGORIES for item in value)
    ):
        raise CompatibilityFamilyError(error)
    return set(value)


def _load_normalization_rule(value: Any) -> DescriptorNormalizationRule:
    raw = _exact_keys(
        value,
        {"rule_id", "tool_name", "field", "normalizer"},
        error="family_descriptor_normalization_rule_fields_invalid",
    )
    rule_id = _bounded_text(
        raw["rule_id"], error="family_descriptor_normalization_rule_invalid", maximum=80
    )
    tool_name = _bounded_text(
        raw["tool_name"], error="family_descriptor_normalization_rule_invalid", maximum=128
    )
    if raw["field"] != "description" or raw["normalizer"] != "ascii-whitespace-v1":
        raise CompatibilityFamilyError(
            "family_descriptor_normalization_rule_invalid"
        )
    return DescriptorNormalizationRule(
        rule_id=rule_id,
        tool_name=tool_name,
        field=raw["field"],
        normalizer=raw["normalizer"],
    )


def normalize_descriptor_wording(value: Any, *, normalizer: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 8_192:
        raise CompatibilityFamilyError("family_descriptor_wording_invalid")
    if normalizer != "ascii-whitespace-v1":
        raise CompatibilityFamilyError("family_descriptor_normalizer_invalid")
    return re.sub(r"[\t\n\r ]+", " ", value).strip()


def _without_dynamic_policy(value: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(canonical_json(value))
    meta = normalized.get("_meta")
    ha_mcp = meta.get("ha_mcp") if isinstance(meta, dict) else None
    policy = ha_mcp.get("policy") if isinstance(ha_mcp, dict) else None
    if isinstance(policy, dict):
        for name in ("deployment", "enabled", "live", "rules"):
            if name in policy:
                policy[name] = "<deployment-dynamic>"
    return normalized


def compare_family_candidate(
    family: CompatibilityFamily,
    *,
    candidate_version: str,
    baseline_tools: list[dict[str, Any]],
    candidate_tools: list[dict[str, Any]],
    baseline_classifications: dict[str, str],
    observed_non_catalog_categories: set[str] | frozenset[str],
) -> FamilyComparison:
    """Compare one exact candidate without deriving trust from its version."""

    if not family.candidate_is_eligible(candidate_version):
        raise CompatibilityFamilyError("family_candidate_not_eligible")
    observed_non_catalog = set(observed_non_catalog_categories)
    if not observed_non_catalog <= NON_CATALOG_DRIFT_CATEGORIES:
        raise CompatibilityFamilyError("family_candidate_drift_unknown")
    baseline = _tools_by_name(baseline_tools)
    candidate = _tools_by_name(candidate_tools)
    observed_categories = set(observed_non_catalog)
    material: set[str] = set()
    unknown: set[str] = set()
    unchanged: list[str] = []
    normalized: list[str] = []
    held_reads: list[str] = []
    nondelegated_changed: list[str] = []
    changed_components: dict[str, set[str]] = {}

    if set(baseline) != set(candidate):
        observed_categories.add("tool_addition_removal_or_rename")
        material.add("tool_addition_removal_or_rename")
        unknown.update(sorted(set(baseline) ^ set(candidate)))
    else:
        for name in sorted(baseline):
            before = baseline[name]
            after = candidate[name]
            if before == after:
                unchanged.append(name)
                continue
            components: set[str] = set()
            if before.get("inputSchema") != after.get("inputSchema"):
                components.add("input_schema")
            if (
                ("outputSchema" in before) != ("outputSchema" in after)
                or before.get("outputSchema") != after.get("outputSchema")
            ):
                components.add("output_schema")
            if (
                ("annotations" in before) != ("annotations" in after)
                or before.get("annotations") != after.get("annotations")
            ):
                components.add("annotations")
            description_changed = before.get("description") != after.get("description")
            if description_changed:
                rule = family.normalization_rule(name, "description")
                if rule is not None and normalize_descriptor_wording(
                    before.get("description"), normalizer=rule.normalizer
                ) == normalize_descriptor_wording(
                    after.get("description"), normalizer=rule.normalizer
                ):
                    components.add("descriptor_wording_unchanged_semantics")
                    normalized.append(name)
                else:
                    components.add("unknown_drift")
                    unknown.add(f"{name}:description")
            before_other = {
                key: item
                for key, item in before.items()
                if key not in {"description", "inputSchema", "outputSchema", "annotations"}
            }
            after_other = {
                key: item
                for key, item in after.items()
                if key not in {"description", "inputSchema", "outputSchema", "annotations"}
            }
            if before_other != after_other:
                if _without_dynamic_policy(before_other) == _without_dynamic_policy(after_other):
                    components.add("deployment_dynamic_metadata")
                else:
                    components.add("unknown_drift")
                    unknown.add(f"{name}:runtime_descriptor")
            changed_components[name] = components
            observed_categories.update(components)
            semantic_components = components - family.allowed_non_semantic_drift
            classification = baseline_classifications.get(name)
            if classification is None:
                material.add("classification_change")
                unknown.add(f"{name}:classification")
            elif semantic_components:
                material.update(semantic_components)
                if classification == "automatic_read":
                    held_reads.append(name)
                else:
                    nondelegated_changed.append(name)

    disallowed_non_catalog = observed_non_catalog - family.allowed_non_semantic_drift
    material.update(disallowed_non_catalog)
    global_rejection = (
        "unknown_drift" in observed_categories
        or bool(observed_non_catalog & GLOBAL_REJECTION_CATEGORIES)
    )
    if global_rejection or "tool_addition_removal_or_rename" in material:
        outcome = "rejected"
    elif held_reads or material:
        outcome = "admitted_with_selective_holds"
    else:
        outcome = "admitted_automatic"

    provider_dispositions: dict[str, str] = {
        surface: "admitted" for surface in PROVIDER_SURFACES
    }
    if outcome == "rejected":
        provider_dispositions = {
            surface: "held" for surface in PROVIDER_SURFACES
        }
    else:
        provider_dispositions["read_gateway"] = (
            "partial" if held_reads else "admitted"
        )
        if "lifecycle_provider" in observed_non_catalog:
            provider_dispositions["lifecycle"] = "held"
        if "dashboard_provider" in observed_non_catalog:
            provider_dispositions["dashboard"] = "held"
        for surface, tool_names in family.provider_tools.items():
            if surface == "read_gateway":
                continue
            if any(
                name in changed_components
                and bool(changed_components[name] - family.allowed_non_semantic_drift)
                for name in tool_names
            ):
                provider_dispositions[surface] = "held"
    return FamilyComparison(
        outcome=outcome,
        observed_drift_categories=tuple(sorted(observed_categories)),
        material_drift_categories=tuple(sorted(material)),
        unknown_drift=tuple(sorted(set(unknown))),
        unchanged_tools=tuple(sorted(unchanged)),
        normalized_descriptor_tools=tuple(sorted(normalized)),
        held_automatic_reads=tuple(sorted(held_reads)),
        nondelegated_changed_tools=tuple(sorted(nondelegated_changed)),
        provider_dispositions=tuple(sorted(provider_dispositions.items())),
    )


def _tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(tools, list) or not tools:
        raise CompatibilityFamilyError("family_catalog_invalid")
    values: dict[str, dict[str, Any]] = {}
    for item in tools:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or name in values:
            raise CompatibilityFamilyError("family_catalog_invalid")
        values[name] = item
    return values


def load_family_admission_binding(value: Any) -> FamilyAdmissionBinding:
    raw = _exact_keys(
        value,
        {
            "family_id",
            "baseline_entry_id",
            "policy_resource",
            "policy_sha256",
            "decision_resource",
            "decision_sha256",
            "outcome",
        },
        error="family_admission_binding_fields_invalid",
    )
    if (
        not isinstance(raw["family_id"], str)
        or not _FAMILY_ID.fullmatch(raw["family_id"])
        or not isinstance(raw["baseline_entry_id"], str)
        or not raw["baseline_entry_id"].startswith("ha-mcp-v")
        or raw["policy_resource"] != "upstream_compatibility_families.json"
        or not isinstance(raw["policy_sha256"], str)
        or not _SHA256.fullmatch(raw["policy_sha256"])
        or not isinstance(raw["decision_resource"], str)
        or not _RESOURCE.fullmatch(raw["decision_resource"])
        or not isinstance(raw["decision_sha256"], str)
        or not _SHA256.fullmatch(raw["decision_sha256"])
        or raw["outcome"] not in RELEASE_OUTCOMES - {"rejected"}
    ):
        raise CompatibilityFamilyError("family_admission_binding_invalid")
    return FamilyAdmissionBinding(**raw)


def validate_family_admission_binding(
    binding: FamilyAdmissionBinding,
    *,
    release: dict[str, Any],
    releases_by_entry_id: dict[str, dict[str, Any]],
    registry_path: Path,
) -> dict[str, Any]:
    """Bind one exact compiled release to its reviewed family decision."""

    policy_path = registry_path.parent / binding.policy_resource
    if sha256_resource(policy_path) != binding.policy_sha256:
        raise CompatibilityFamilyError("family_admission_policy_digest_mismatch")
    families = load_compatibility_families(policy_path)
    family = families.by_id.get(binding.family_id)
    if family is None:
        raise CompatibilityFamilyError("family_admission_family_missing")
    baseline_release = releases_by_entry_id.get(binding.baseline_entry_id)
    if baseline_release is None:
        raise CompatibilityFamilyError("family_admission_baseline_missing")
    if (
        binding.baseline_entry_id != family.baseline.entry_id
        or baseline_release.get("version") != family.baseline.version
        or baseline_release.get("source_commit") != family.baseline.source_commit
        or baseline_release.get("image_index_digest")
        != family.baseline.image_index_digest
        or baseline_release.get("capture_sha256") != family.baseline.capture_sha256
        or baseline_release.get("policy_sha256") != family.baseline.policy_sha256
    ):
        raise CompatibilityFamilyError("family_admission_baseline_mismatch")
    version = release.get("version")
    if not isinstance(version, str) or not family.candidate_is_eligible(version):
        raise CompatibilityFamilyError("family_admission_candidate_ineligible")
    decision_path = registry_path.parents[2] / binding.decision_resource
    if sha256_resource(decision_path) != binding.decision_sha256:
        raise CompatibilityFamilyError("family_admission_decision_digest_mismatch")
    decision = load_strict_json(
        decision_path, error="family_admission_decision_unreadable"
    )
    _validate_family_decision(
        decision,
        binding=binding,
        release=release,
        family=family,
    )
    return decision


def _validate_family_decision(
    value: Any,
    *,
    binding: FamilyAdmissionBinding,
    release: dict[str, Any],
    family: CompatibilityFamily,
) -> None:
    raw = _exact_keys(
        value,
        {
            "schema_version",
            "decision_id",
            "family_id",
            "baseline",
            "candidate",
            "capture_determinism",
            "observed_drift_categories",
            "material_drift_categories",
            "unknown_drift",
            "surface_disposition",
            "required_validation",
            "outcome",
            "reason",
        },
        error="family_admission_decision_fields_invalid",
    )
    if raw["schema_version"] != FAMILY_DECISION_SCHEMA_VERSION:
        raise CompatibilityFamilyError("family_admission_decision_schema_invalid")
    _bounded_text(raw["decision_id"], error="family_admission_decision_id_invalid", maximum=128)
    if raw["family_id"] != binding.family_id:
        raise CompatibilityFamilyError("family_admission_decision_family_mismatch")
    baseline = _exact_keys(
        raw["baseline"],
        {"version", "entry_id", "source_commit", "image_index_digest", "capture_sha256", "policy_sha256"},
        error="family_admission_decision_baseline_fields_invalid",
    )
    if baseline != {
        "version": family.baseline.version,
        "entry_id": family.baseline.entry_id,
        "source_commit": family.baseline.source_commit,
        "image_index_digest": family.baseline.image_index_digest,
        "capture_sha256": family.baseline.capture_sha256,
        "policy_sha256": family.baseline.policy_sha256,
    }:
        raise CompatibilityFamilyError("family_admission_decision_baseline_mismatch")
    candidate = _exact_keys(
        raw["candidate"],
        {
            "version",
            "entry_id",
            "source_commit",
            "source_tag_object",
            "source_tree",
            "source_archive_sha256",
            "image_index_digest",
            "architecture_image_digests",
            "addon_artifact_digests",
            "image_revision",
            "capture_sha256",
            "policy_sha256",
            "artifact_evidence_sha256",
            "protocol_version",
            "server_name",
            "package_version",
            "initialize_version",
            "supervisor_addon_version",
        },
        error="family_admission_decision_candidate_fields_invalid",
    )
    expected_candidate = {
        "version": release.get("version"),
        "entry_id": release.get("entry_id"),
        "source_commit": release.get("source_commit"),
        "source_tag_object": release.get("source_tag_object"),
        "source_tree": release.get("source_tree"),
        "source_archive_sha256": release.get("source_archive_sha256"),
        "image_index_digest": release.get("image_index_digest"),
        "architecture_image_digests": release.get("architecture_image_digests"),
        "addon_artifact_digests": release.get("addon_artifact_digests"),
        "image_revision": release.get("image_revision"),
        "capture_sha256": release.get("capture_sha256"),
        "policy_sha256": release.get("policy_sha256"),
        "artifact_evidence_sha256": release.get("artifact_evidence_sha256"),
        "protocol_version": "2025-03-26",
        "server_name": "ha-mcp",
        "package_version": release.get("version"),
        "initialize_version": release.get("version"),
        "supervisor_addon_version": release.get("version"),
    }
    if candidate != expected_candidate:
        raise CompatibilityFamilyError("family_admission_decision_candidate_mismatch")
    determinism = _exact_keys(
        raw["capture_determinism"],
        {"capture_count", "capture_sha256_values", "byte_identical"},
        error="family_admission_capture_determinism_fields_invalid",
    )
    if (
        determinism["capture_count"] != 2
        or determinism["byte_identical"] is not True
        or determinism["capture_sha256_values"]
        != [release.get("capture_sha256"), release.get("capture_sha256")]
    ):
        raise CompatibilityFamilyError("family_admission_capture_nondeterministic")
    observed = _category_set(
        raw["observed_drift_categories"],
        error="family_admission_observed_drift_invalid",
    )
    material = _category_set(
        raw["material_drift_categories"],
        error="family_admission_material_drift_invalid",
    )
    if not observed or not material <= observed:
        raise CompatibilityFamilyError("family_admission_drift_inconsistent")
    if raw["unknown_drift"] != []:
        raise CompatibilityFamilyError("family_admission_unknown_drift")
    surface = raw["surface_disposition"]
    if (
        not isinstance(surface, dict)
        or set(surface) != PROVIDER_SURFACES
        or any(item not in PROVIDER_DISPOSITIONS for item in surface.values())
        or surface != release.get("provider_dispositions")
    ):
        raise CompatibilityFamilyError("family_admission_surface_disposition_invalid")
    validations = raw["required_validation"]
    if (
        not isinstance(validations, list)
        or validations != list(family.required_evidence)
    ):
        raise CompatibilityFamilyError("family_admission_validation_incomplete")
    if raw["outcome"] != binding.outcome or raw["outcome"] == "rejected":
        raise CompatibilityFamilyError("family_admission_outcome_mismatch")
    _bounded_text(raw["reason"], error="family_admission_reason_invalid", maximum=1_000)
    if raw["outcome"] == "admitted_automatic" and (
        material or any(item != "admitted" for item in surface.values())
    ):
        raise CompatibilityFamilyError("family_admission_fast_path_invalid")
