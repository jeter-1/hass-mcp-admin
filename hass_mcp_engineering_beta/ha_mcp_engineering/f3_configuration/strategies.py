"""Explicit resource strategies for F3 configuration operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ha_mcp_engineering.f3.contracts import F3_ADAPTER_CONTRACT_MODEL

from ..governance.normalize import stable_hash
from ..governance.resources import (
    normalize_resource_config,
    resource_fingerprint,
    validate_resource,
    validate_resource_create_identity,
)
from .models import (
    CONFIGURATION_LOCK_SET_MODEL,
    CONFIGURATION_PROVIDER_CONTRACT,
    CONFIGURATION_VERIFICATION_CONTRACT_MODEL,
    ConfigurationCapabilityDescriptor,
    ConfigurationProviderDescriptor,
)


SUPPORTED_ACTIONS = frozenset({"create", "update"})

CAPABILITY_IDENTITIES = {
    ("automation", "create"): "create_automation_configuration",
    ("automation", "update"): "update_automation_configuration",
    ("script", "create"): "create_script_configuration",
    ("script", "update"): "update_script_configuration",
    ("input_boolean", "create"): "create_input_boolean_configuration",
    ("input_boolean", "update"): "update_input_boolean_configuration",
    ("input_number", "create"): "create_input_number_configuration",
    ("input_number", "update"): "update_input_number_configuration",
}


class ConfigurationStrategy(ABC):
    """One closed resource contract; never an arbitrary provider adapter."""

    resource_type: str

    def __init__(self, action: str) -> None:
        if action not in SUPPORTED_ACTIONS:
            raise ValueError("configuration action must be create or update")
        if (self.resource_type, action) not in CAPABILITY_IDENTITIES:
            raise ValueError("configuration capability is not reviewed")
        self.action = action

    @property
    def capability_identity(self) -> str:
        return CAPABILITY_IDENTITIES[(self.resource_type, self.action)]

    @property
    def capabilities(self) -> ConfigurationCapabilityDescriptor:
        return ConfigurationCapabilityDescriptor(
            adapter_id="configuration_operation",
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            operation_family="configuration_change",
            supported_operations=(self.capability_identity,),
            rollback_supported=False,
            readback_recovery_supported=True,
            exact_provider_contract_required=True,
            capability_identity=self.capability_identity,
            resource_type=self.resource_type,
            action=self.action,
            provider="home_assistant_configuration_gateway",
            provider_contract=CONFIGURATION_PROVIDER_CONTRACT,
            argument_names=self.allowed_argument_names,
            validation_contract="existing_configuration_validation_v1",
            verification_contract=(
                CONFIGURATION_VERIFICATION_CONTRACT_MODEL
            ),
            lock_set_version=CONFIGURATION_LOCK_SET_MODEL,
        )

    def canonical_target(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> str:
        """Preserve the exact currently accepted identifier form."""

        valid, errors, _ = validate_resource(
            self.resource_type, target_id, proposed_config
        )
        identity_errors = [
            error
            for error in errors
            if "resource_id" in error or "automation_id" in error
        ]
        if not valid and identity_errors:
            raise ValueError("; ".join(identity_errors))
        if not isinstance(target_id, str) or target_id != target_id.strip():
            raise ValueError("configuration target identity is not canonical")
        return target_id

    def validate(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        valid, errors, warnings = validate_resource(
            self.resource_type, target_id, proposed_config
        )
        if self.action == "create":
            errors.extend(
                validate_resource_create_identity(
                    self.resource_type, target_id, proposed_config
                )
            )
        unique_errors = tuple(dict.fromkeys(errors))
        return (
            valid and not unique_errors,
            unique_errors,
            tuple(dict.fromkeys(warnings)),
        )

    def normalize(
        self, config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return normalize_resource_config(self.resource_type, config)

    def fingerprint(self, config: dict[str, Any] | None) -> str:
        return resource_fingerprint(self.resource_type, config)

    def rollback_available(self, plan_contract_version: int) -> bool:
        """Forward F3-C1 capabilities never imply executable rollback."""

        del plan_contract_version
        return False

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
    ) -> ConfigurationProviderDescriptor:
        payload = self.provider_payload(target_id, proposed_config)
        return ConfigurationProviderDescriptor(
            provider="home_assistant_configuration_gateway",
            contract_model=CONFIGURATION_PROVIDER_CONTRACT,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=self.argument_names(proposed_config),
            arguments_hash=stable_hash(payload),
        )

    @property
    @abstractmethod
    def transport(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def allowed_argument_names(self) -> tuple[str, ...]:
        """Return the complete reviewed argument vocabulary for this action."""

        raise NotImplementedError

    @abstractmethod
    def provider_operation(self, target_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError


class _RestConfigurationStrategy(ConfigurationStrategy):
    @property
    def transport(self) -> str:
        return "home_assistant_rest"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("body", "method", "path")

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return f"{self.resource_type}_configuration_write"

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": f"/config/{self.resource_type}/config/{target_id}",
            "body": proposed_config,
        }

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        return ("body", "method", "path")


class AutomationConfigurationStrategy(_RestConfigurationStrategy):
    resource_type = "automation"


class ScriptConfigurationStrategy(_RestConfigurationStrategy):
    resource_type = "script"


class _HelperConfigurationStrategy(ConfigurationStrategy):
    id_field: str
    configuration_fields: tuple[str, ...]

    @property
    def transport(self) -> str:
        return "home_assistant_websocket"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        values = {"type", *self.configuration_fields}
        if self.action == "update":
            values.add(self.id_field)
        return tuple(sorted(values, key=lambda item: item.encode("utf-8")))

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return f"{self.resource_type}_{self.action}"

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        command: dict[str, Any] = {
            "type": f"{self.resource_type}/{self.action}",
        }
        if self.action == "update":
            command[self.id_field] = target_id.split(".", 1)[1]
        command.update(proposed_config)
        return command

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        values = {"type", *proposed_config.keys()}
        if self.action == "update":
            values.add(self.id_field)
        return tuple(sorted(values, key=lambda item: item.encode("utf-8")))


class InputBooleanConfigurationStrategy(_HelperConfigurationStrategy):
    resource_type = "input_boolean"
    id_field = "input_boolean_id"
    configuration_fields = ("icon", "initial", "name")


class InputNumberConfigurationStrategy(_HelperConfigurationStrategy):
    resource_type = "input_number"
    id_field = "input_number_id"
    configuration_fields = (
        "icon",
        "initial",
        "max",
        "min",
        "mode",
        "name",
        "step",
        "unit_of_measurement",
    )


_STRATEGIES = {
    "automation": AutomationConfigurationStrategy,
    "script": ScriptConfigurationStrategy,
    "input_boolean": InputBooleanConfigurationStrategy,
    "input_number": InputNumberConfigurationStrategy,
}


def strategy_for(
    resource_type: str, action: str
) -> ConfigurationStrategy:
    """Return one reviewed strategy or fail closed."""

    strategy_type = _STRATEGIES.get(resource_type)
    if strategy_type is None:
        raise ValueError("configuration resource type is not reviewed")
    return strategy_type(action)
