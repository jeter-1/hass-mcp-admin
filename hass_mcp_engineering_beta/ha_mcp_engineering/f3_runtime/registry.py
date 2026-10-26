"""Closed, code-owned F3 adapter registry for the Beta 20 execution surface."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

from ..f3.contracts import F3_ADAPTER_CONTRACT_MODEL


@dataclass(frozen=True)
class AdapterRegistration:
    capability_id: str
    adapter_id: str
    operation_family: str
    plan_operation: str
    target_type: str
    action: str
    provider_model: str
    resource_lock_model: str
    provider_dependency_lock_model: str
    provider_admission_model: str
    verification_model: str
    recovery_model: str
    rollback_declaration: str
    runtime_route: str
    historical_compatibility: str
    required_releases: tuple[str, ...] = ("7.14.2", "8.0.0")
    required_protocol: str = "2025-03-26"
    contract_model: str = F3_ADAPTER_CONTRACT_MODEL


CONFIGURATION_REGISTRATIONS = (
    ("create_automation_configuration", "automation", "create"),
    ("update_automation_configuration", "automation", "update"),
    ("create_script_configuration", "script", "create"),
    ("update_script_configuration", "script", "update"),
    ("create_input_boolean_configuration", "input_boolean", "create"),
    ("update_input_boolean_configuration", "input_boolean", "update"),
    ("create_input_number_configuration", "input_number", "create"),
    ("update_input_number_configuration", "input_number", "update"),
)

# HAMCP-069A-1: the direct, code-owned entity registry metadata update.  It is
# a configuration child but is not admitted through the reviewed ha-mcp release
# registry; its Core admission is the dormant entity-metadata profile.
ENTITY_METADATA_REGISTRATION = (
    "update_entity_registry_metadata_configuration",
    "entity_registry_metadata",
    "update",
)

# HAMCP-069B: two further code-owned direct registry writes, each admitted
# only by its own dormant Core profile.
AVAILABILITY_EXPOSURE_REGISTRATIONS = (
    (
        "update_entity_registry_availability_configuration",
        "entity_registry_availability",
        "update",
        "direct-ha-exact-entity-registry-availability-v1",
    ),
    (
        "update_entity_assistant_exposure_configuration",
        "entity_assistant_exposure",
        "update",
        "direct-ha-exact-assistant-exposure-v1",
    ),
)

# HAMCP-165: the code-owned label registry create and update, admitted only
# by the dormant ``core.label_registry_operation`` profile.  Rollback is not
# available: undoing a create would need deletion (HAMCP-155).
LABEL_REGISTRATIONS = (
    (
        "create_label_registry_entry_configuration",
        "label_registry_entry",
        "create",
        "direct-ha-exact-label-registry-v1",
    ),
    (
        "update_label_registry_entry_configuration",
        "label_registry_entry",
        "update",
        "direct-ha-exact-label-registry-v1",
    ),
)

# HAMCP-155: governed deletion of one UI-managed automation or script.  The
# sole DELETE is code-owned and admitted only by its own dormant Core profile.
CONFIGURATION_DELETE_REGISTRATIONS = (
    ("delete_automation_configuration", "automation", "delete"),
    ("delete_script_configuration", "script", "delete"),
)
CONFIGURATION_DELETE_PROVIDER_MODEL = (
    "compiled-core-configuration-delete-operation-v1"
)

# HAMCP-232: the exact options-flow update of one UI-managed template,
# filter, or statistics helper config entry.  Admitted only by the dormant
# ``core.config_entry_helper_options_operation`` profile.
HELPER_OPTIONS_REGISTRATION = (
    "update_config_entry_helper_options_configuration",
    "config_entry_helper_options",
    "update",
)
HELPER_OPTIONS_PROVIDER_MODEL = (
    "compiled-core-config-entry-helper-options-operation-v1"
)

OPERATIONAL_REGISTRATIONS = (
    ("create_full_home_assistant_backup", "create_full_backup", "backup"),
    (
        "reload_home_assistant_configuration_domain",
        "controlled_reload",
        "reload_domain",
    ),
    (
        "restart_installed_home_assistant_addon",
        "restart_addon",
        "addon",
    ),
    ("restart_home_assistant_core", "restart_home_assistant", "home_assistant"),
)

HELPER_STATE_REGISTRATION = (
    "set_exact_input_boolean_state",
    "set_input_boolean_state",
    "input_boolean",
)

# HAMCP-145: the exact input_select option, admitted only by the dormant
# ``core.typed_input_select_option_operation`` profile.
INPUT_SELECT_REGISTRATION = (
    "set_exact_input_select_option",
    "set_input_select_option",
    "input_select",
)

DASHBOARD_REGISTRATION = (
    "update_existing_dashboard",
    "update_dashboard",
    "dashboard",
)


class AdapterRegistryError(RuntimeError):
    pass


class ClosedAdapterRegistry:
    """Exact registry; callers cannot select imports or arbitrary capabilities."""

    def __init__(self, entries: Iterable[AdapterRegistration], adapters: dict[str, Any]):
        self._entries = tuple(entries)
        self._adapters = dict(adapters)
        self.validate()

    @classmethod
    def build(
        cls,
        *,
        configuration_adapters: dict[str, Any],
        operational_adapter: Any,
        dashboard_adapter: Any,
    ):
        entries = [
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model="direct_home_assistant_configuration_gateway",
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="exact_reviewed_release_registry",
                verification_model="f3-configuration-verification-v1",
                recovery_model="exact_configuration_readback",
                rollback_declaration="separate_governed_update_plan",
                runtime_route="apply_change_plan",
                historical_compatibility=(
                    "deterministic_immutable_projection_or_new_plan"
                ),
            )
            for capability, target, action in CONFIGURATION_REGISTRATIONS
        ]
        capability, target, action = ENTITY_METADATA_REGISTRATION
        entries.append(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model="direct-ha-exact-entity-registry-metadata-v1",
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="exact_registry_readback",
                recovery_model="exact_configuration_readback",
                rollback_declaration="governed_reverse_update_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
        )
        entries.extend(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model=provider_model,
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="exact_registry_readback",
                recovery_model="exact_configuration_readback",
                rollback_declaration="governed_reverse_update_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
            for capability, target, action, provider_model in (
                AVAILABILITY_EXPOSURE_REGISTRATIONS
            )
        )
        entries.extend(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model=provider_model,
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="exact_registry_readback",
                recovery_model="exact_configuration_readback",
                rollback_declaration="unavailable",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
            for capability, target, action, provider_model in LABEL_REGISTRATIONS
        )
        entries.extend(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model=CONFIGURATION_DELETE_PROVIDER_MODEL,
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="exact_absence_readback",
                recovery_model="exact_absence_readback_no_redispatch",
                rollback_declaration="separate_governed_recreate_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
            for capability, target, action in CONFIGURATION_DELETE_REGISTRATIONS
        )
        capability, target, action = HELPER_OPTIONS_REGISTRATION
        entries.append(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="configuration_operation",
                operation_family="configuration",
                plan_operation="configuration_plan",
                target_type=target,
                action=action,
                provider_model=HELPER_OPTIONS_PROVIDER_MODEL,
                resource_lock_model="f3-configuration-lock-set-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="exact_options_flow_readback",
                recovery_model="exact_options_flow_readback_no_redispatch",
                rollback_declaration="separate_governed_reverse_update_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
        )
        entries.extend(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="operational_administration",
                operation_family="operational_administration",
                plan_operation=operation,
                target_type=target,
                action=operation,
                provider_model="f3-operational-provider-contract-v1",
                resource_lock_model="f3-operational-complete-lock-graph-v1",
                provider_dependency_lock_model=(
                    "authoritative_ha_mcp_addon_shared_dependency"
                ),
                provider_admission_model="exact_reviewed_release_registry",
                verification_model="operation_specific_exact_readback",
                recovery_model="operation_specific_observation_only",
                rollback_declaration="unavailable",
                runtime_route="apply_change_plan",
                historical_compatibility="legacy_authority_readback_only",
            )
            for capability, operation, target in OPERATIONAL_REGISTRATIONS
        )
        adapters = dict(configuration_adapters)
        for capability, _operation, _target in OPERATIONAL_REGISTRATIONS:
            adapters[capability] = operational_adapter
        capability, operation, target = HELPER_STATE_REGISTRATION
        entries.append(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="operational_administration",
                operation_family="operational_administration",
                plan_operation=operation,
                target_type=target,
                action=operation,
                provider_model="direct-ha-exact-input-boolean-v1",
                resource_lock_model="f3-operational-complete-lock-graph-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="f3-input-boolean-exact-state-readback-v1",
                recovery_model="exact_state_readback_only",
                rollback_declaration="separate_governed_reverse_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
        )
        adapters[capability] = operational_adapter
        capability, operation, target = INPUT_SELECT_REGISTRATION
        entries.append(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="operational_administration",
                operation_family="operational_administration",
                plan_operation=operation,
                target_type=target,
                action=operation,
                provider_model="direct-ha-exact-input-select-option-v1",
                resource_lock_model="f3-operational-complete-lock-graph-v1",
                provider_dependency_lock_model=(
                    "home_assistant_core_shared_dependency"
                ),
                provider_admission_model="code_owned_closed_contract",
                verification_model="f3-input-select-exact-option-readback-v1",
                recovery_model="exact_state_readback_only",
                rollback_declaration="separate_governed_reverse_plan",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=(),
                required_protocol="direct-home-assistant-api-v1",
            )
        )
        adapters[capability] = operational_adapter
        capability, operation, target = DASHBOARD_REGISTRATION
        entries.append(
            AdapterRegistration(
                capability_id=capability,
                adapter_id="dashboard_update",
                operation_family="dashboard_update",
                plan_operation=operation,
                target_type=target,
                action="update",
                provider_model="ha-mcp-dashboard-full-result-update-v1",
                resource_lock_model="f3-dashboard-lock-set-v1",
                provider_dependency_lock_model=(
                    "authoritative_ha_mcp_addon_shared_dependency"
                ),
                provider_admission_model="exact_reviewed_release_registry",
                verification_model="f3-dashboard-exact-reread-v1",
                recovery_model="exact_dashboard_readback_only",
                rollback_declaration="unavailable",
                runtime_route="apply_change_plan",
                historical_compatibility="new_plan_required",
                required_releases=("8.1.1", "8.2.0"),
            )
        )
        adapters[capability] = dashboard_adapter
        return cls(entries, adapters)

    def validate(self) -> None:
        expected = {
            item[0]
            for item in (
                *CONFIGURATION_REGISTRATIONS,
                *OPERATIONAL_REGISTRATIONS,
                HELPER_STATE_REGISTRATION,
                INPUT_SELECT_REGISTRATION,
                ENTITY_METADATA_REGISTRATION,
                *AVAILABILITY_EXPOSURE_REGISTRATIONS,
                *LABEL_REGISTRATIONS,
                *CONFIGURATION_DELETE_REGISTRATIONS,
                HELPER_OPTIONS_REGISTRATION,
                DASHBOARD_REGISTRATION,
            )
        }
        identities = [item.capability_id for item in self._entries]
        if len(identities) != len(set(identities)) or set(identities) != expected:
            raise AdapterRegistryError("F3 capability registry is incomplete or duplicated")
        if set(self._adapters) != expected:
            raise AdapterRegistryError("F3 route and adapter registry disagree")
        for entry in self._entries:
            if entry.contract_model != F3_ADAPTER_CONTRACT_MODEL:
                raise AdapterRegistryError("F3 adapter model is unsupported")
            direct_helper = entry.capability_id in {
                HELPER_STATE_REGISTRATION[0],
                INPUT_SELECT_REGISTRATION[0],
                ENTITY_METADATA_REGISTRATION[0],
                *(item[0] for item in AVAILABILITY_EXPOSURE_REGISTRATIONS),
                *(item[0] for item in LABEL_REGISTRATIONS),
                *(item[0] for item in CONFIGURATION_DELETE_REGISTRATIONS),
                HELPER_OPTIONS_REGISTRATION[0],
            }
            if (
                (
                    direct_helper
                    and (
                        entry.required_releases != ()
                        or entry.required_protocol
                        != "direct-home-assistant-api-v1"
                        or entry.provider_admission_model
                        != "code_owned_closed_contract"
                    )
                )
                or (
                    not direct_helper
                    and (
                        entry.required_releases
                        not in {
                            ("7.14.2", "8.0.0"),
                            ("8.1.1", "8.2.0"),
                        }
                        or entry.required_protocol != "2025-03-26"
                        or entry.provider_admission_model
                        != "exact_reviewed_release_registry"
                    )
                )
                or entry.runtime_route != "apply_change_plan"
                or (
                    entry.action == "delete"
                    and (
                        entry.capability_id
                        not in {
                            item[0]
                            for item in CONFIGURATION_DELETE_REGISTRATIONS
                        }
                        or entry.target_type not in {"automation", "script"}
                        or entry.provider_model
                        != CONFIGURATION_DELETE_PROVIDER_MODEL
                    )
                )
                or (
                    entry.target_type == "config_entry_helper_options"
                    and (
                        entry.capability_id != HELPER_OPTIONS_REGISTRATION[0]
                        or entry.action != "update"
                        or entry.provider_model != HELPER_OPTIONS_PROVIDER_MODEL
                    )
                )
                or not entry.resource_lock_model
                or not entry.provider_dependency_lock_model
                or entry.target_type not in {
                    "automation", "script", "input_boolean", "input_number",
                    "input_select", "backup", "reload_domain", "addon", "home_assistant",
                    "dashboard", "entity_registry_metadata",
                    "entity_registry_availability", "entity_assistant_exposure",
                    "label_registry_entry", "config_entry_helper_options",
                }
            ):
                raise AdapterRegistryError("F3 route admission binding is invalid")
            adapter = self._adapters[entry.capability_id]
            capabilities = getattr(adapter, "capabilities", None)
            supported = tuple(getattr(capabilities, "supported_operations", ()))
            expected_operation = (
                entry.plan_operation
                if entry.operation_family
                in {"operational_administration", "dashboard_update"}
                else entry.capability_id
            )
            if (
                getattr(capabilities, "contract_model", None)
                != F3_ADAPTER_CONTRACT_MODEL
                or expected_operation not in supported
                or getattr(capabilities, "adapter_id", None) != entry.adapter_id
            ):
                raise AdapterRegistryError("F3 adapter capability binding is invalid")

    def adapter(self, capability_id: str) -> Any:
        try:
            return self._adapters[capability_id]
        except KeyError:
            raise AdapterRegistryError("unknown F3 capability") from None

    def entry(self, capability_id: str) -> AdapterRegistration:
        for entry in self._entries:
            if entry.capability_id == capability_id:
                return entry
        raise AdapterRegistryError("unknown F3 capability")

    @property
    def entries(self) -> tuple[AdapterRegistration, ...]:
        return self._entries

    def health(self) -> dict[str, Any]:
        projection = [
            {
                name: getattr(entry, name)
                for name in entry.__dataclass_fields__
            }
            for entry in self._entries
        ]
        return {
            "status": "ready",
            "contract_model": F3_ADAPTER_CONTRACT_MODEL,
            "registered_adapter_count": len({id(value) for value in self._adapters.values()}),
            "activated_capability_count": len(self._entries),
            "registry_sha256": hashlib.sha256(
                json.dumps(
                    projection, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            "dashboard_capability_count": sum(
                entry.operation_family == "dashboard_update"
                for entry in self._entries
            ),
            "fallback_count": 0,
        }
