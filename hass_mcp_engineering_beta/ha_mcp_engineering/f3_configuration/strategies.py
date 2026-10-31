"""Explicit resource strategies for F3 configuration operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from ha_mcp_engineering.f3.contracts import F3_ADAPTER_CONTRACT_MODEL

from ..errors import AuthorizationError
from ..governance.entity_metadata import (
    ENTITY_METADATA_CAPABILITY_IDENTITY,
    ENTITY_METADATA_PROVIDER,
    ENTITY_METADATA_PROVIDER_CONTRACT,
    ENTITY_METADATA_PROVIDER_OPERATION,
    ENTITY_METADATA_RESOURCE_TYPE,
    ENTITY_METADATA_UNAUTHORIZED_REASON,
    MANAGED_FIELDS as ENTITY_METADATA_MANAGED_FIELDS,
    normalize_entry as normalize_entity_metadata_entry,
    update_command as entity_metadata_update_command,
    update_command_argument_names as entity_metadata_argument_names,
    update_fields as entity_metadata_update_fields,
)
from ..governance import entity_availability_exposure as availability_exposure
from ..governance import label_registry
from ..governance import dashboard_metadata
from ..governance import scene_configuration
from ..governance.normalize import stable_hash
from ..governance.resources import (
    configuration_write_config,
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
    (
        ENTITY_METADATA_RESOURCE_TYPE,
        "update",
    ): ENTITY_METADATA_CAPABILITY_IDENTITY,
    **{
        (resource_type, "update"): identity
        for resource_type, identity in (
            availability_exposure.CAPABILITY_IDENTITIES.items()
        )
    },
    **{
        (label_registry.LABEL_RESOURCE_TYPE, action): identity
        for action, identity in label_registry.CAPABILITY_IDENTITIES.items()
    },
    (
        dashboard_metadata.RESOURCE_TYPE,
        dashboard_metadata.ACTION,
    ): dashboard_metadata.CAPABILITY_IDENTITY,
    (
        scene_configuration.RESOURCE_TYPE,
        scene_configuration.ACTION,
    ): scene_configuration.CAPABILITY_IDENTITY,
}


class ConfigurationStrategy(ABC):
    """One closed resource contract; never an arbitrary provider adapter."""

    resource_type: str
    provider = "home_assistant_configuration_gateway"
    provider_contract = CONFIGURATION_PROVIDER_CONTRACT
    validation_contract = "existing_configuration_validation_v1"

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
            provider=self.provider,
            provider_contract=self.provider_contract,
            argument_names=self.allowed_argument_names,
            validation_contract=self.validation_contract,
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
            if (
                self.resource_type in {"automation", "script"}
                and "category" in proposed_config
            ):
                errors.append(
                    "category is read-only registry metadata and cannot be "
                    "set by a configuration create operation"
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
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        """Describe the exact provider call.

        ``current_config`` is used only by strategies whose payload is a
        delta from the planned current state; full-replacement strategies
        ignore it, so their descriptors and hashes are unchanged.
        """

        del current_config
        payload = self.provider_payload(target_id, proposed_config)
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=self.argument_names(proposed_config),
            arguments_hash=stable_hash(payload),
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the exact bounded configuration sent to the provider."""

        del current_config
        return dict(proposed_config)

    # Optional preflight and outcome hooks.  The defaults preserve the
    # shipped behaviour of every full-replacement configuration strategy.

    #: Whether Home Assistant's configuration check gates preflight and
    #: exact verification.  YAML-backed configuration children keep it.
    requires_configuration_check = True

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        del observed, proposed_config
        return False

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        del gateway, target_id, current_config, proposed_config
        return ()

    def dispatch_scope(
        self, operation: Any, provider_config: dict[str, Any]
    ) -> AbstractContextManager[Any]:
        """Authority installed around the one send, after the durable intent."""

        del operation, provider_config
        return nullcontext()

    async def observation_gap(
        self, gateway: Any, operation: Any, observed: dict[str, Any] | None
    ) -> tuple[str, ...]:
        """Codes that make an exact readback incomplete evidence; ``()``: none."""

        del gateway, operation, observed
        return ()

    def read_error_code(self, exc: BaseException) -> str:
        del exc
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        del exc
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        del exc
        return ()

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
            "body": self.dispatch_config(proposed_config),
        }

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del current_config
        return configuration_write_config(
            self.resource_type, proposed_config
        )

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


class EntityRegistryMetadataConfigurationStrategy(ConfigurationStrategy):
    """Closed ``config/entity_registry/update`` for one exact entity.

    The plan records the full final entry; only the touched managed fields
    are sent, derived from the plan's own recorded current state.
    """

    resource_type = ENTITY_METADATA_RESOURCE_TYPE
    provider = ENTITY_METADATA_PROVIDER
    provider_contract = ENTITY_METADATA_PROVIDER_CONTRACT
    validation_contract = "entity_registry_metadata_validation_v1"
    # V8 (owner direction 2026-10-12): a registry update reads no YAML, so
    # an unrelated configuration error must not block or fail it.
    requires_configuration_check = False

    def __init__(self, action: str) -> None:
        if action != "update":
            raise ValueError("entity registry metadata is update-only")
        super().__init__(action)

    @property
    def transport(self) -> str:
        return "home_assistant_websocket"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {"type", "entity_id", *ENTITY_METADATA_MANAGED_FIELDS},
                key=lambda item: item.encode("utf-8"),
            )
        )

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return ENTITY_METADATA_PROVIDER_OPERATION

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        command = entity_metadata_update_command(
            target_id, current_config, proposed_config
        )
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=entity_metadata_argument_names(command),
            arguments_hash=stable_hash(command),
        )

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "entity metadata payloads require the planned current state"
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError(
            "entity metadata argument names require the planned current state"
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return entity_metadata_update_fields(current_config, proposed_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        if observed is None:
            return False
        try:
            return normalize_entity_metadata_entry(
                observed
            ) == normalize_entity_metadata_entry(proposed_config)
        except (TypeError, ValueError):
            return False

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        check = getattr(gateway, "entity_metadata_preflight_evidence", None)
        if check is None or current_config is None:
            return ("dependency_evidence_unavailable",)
        try:
            return tuple(
                await check(target_id, current_config, proposed_config)
            )
        except AuthorizationError:
            return ("permission_failure",)
        except Exception:
            return ("reference_definitions_unavailable",)

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == ENTITY_METADATA_UNAUTHORIZED_REASON
        ):
            return ("provider_permission_denied",)
        return ()


class _AvailabilityExposureStrategy(ConfigurationStrategy):
    """One closed 069B write for one exact entity (HAMCP-069B).

    The plan records the full final entry plus its bindings; the single
    command sent is rebuilt from the plan's own recorded current state.
    """

    requires_configuration_check = False

    def __init__(self, action: str) -> None:
        if action != "update":
            raise ValueError("entity availability and exposure are update-only")
        super().__init__(action)

    @property
    def provider(self) -> str:  # type: ignore[override]
        return availability_exposure.PROVIDERS[self.resource_type]

    @property
    def transport(self) -> str:
        return "home_assistant_websocket"

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return availability_exposure.PROVIDER_OPERATIONS[self.resource_type]

    def _command(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return availability_exposure.update_command(
            self.resource_type, target_id, current_config, proposed_config
        )

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        command = self._command(target_id, proposed_config, current_config)
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=tuple(
                sorted(command, key=lambda item: item.encode("utf-8"))
            ),
            arguments_hash=stable_hash(command),
        )

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "availability/exposure payloads require the planned current state"
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError(
            "availability/exposure argument names require the planned current state"
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = proposed_config.get("entity_id")
        return self._command(target_id, proposed_config, current_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        # V9: exactly the planned final entry is present, so nothing is sent.
        if observed is None:
            return False
        try:
            return availability_exposure.normalize_entry(
                observed
            ) == availability_exposure.normalize_entry(
                availability_exposure.without_bindings(proposed_config)
            )
        except (TypeError, ValueError):
            return False

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        check = getattr(
            gateway, "availability_exposure_preflight_evidence", None
        )
        if check is None or current_config is None:
            return ("dependency_evidence_unavailable",)
        try:
            return tuple(
                await check(target_id, current_config, proposed_config)
            )
        except AuthorizationError:
            return ("permission_failure",)
        except Exception:
            return ("dependency_evidence_unavailable",)

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == availability_exposure.UNAUTHORIZED_REASON
        ):
            return ("provider_permission_denied",)
        return ()


class EntityRegistryAvailabilityConfigurationStrategy(
    _AvailabilityExposureStrategy
):
    """Closed ``config/entity_registry/update`` of ``disabled_by`` only."""

    resource_type = availability_exposure.AVAILABILITY_RESOURCE_TYPE
    provider_contract = availability_exposure.AVAILABILITY_PROVIDER_CONTRACT
    validation_contract = "entity_registry_availability_validation_v1"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("disabled_by", "entity_id", "type")


class EntityAssistantExposureConfigurationStrategy(
    _AvailabilityExposureStrategy
):
    """Closed ``homeassistant/expose_entity`` for one assistant and entity."""

    resource_type = availability_exposure.EXPOSURE_RESOURCE_TYPE
    provider_contract = availability_exposure.EXPOSURE_PROVIDER_CONTRACT
    validation_contract = "entity_assistant_exposure_validation_v1"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("assistants", "entity_ids", "should_expose", "type")


class LabelRegistryEntryConfigurationStrategy(ConfigurationStrategy):
    """One closed label create or update for one exact label (HAMCP-165).

    The plan records the full final entry plus its bindings; the single
    command sent is rebuilt from the plan's own recorded current state.
    ``config/label_registry/delete`` is never constructed.
    """

    resource_type = label_registry.LABEL_RESOURCE_TYPE
    provider = label_registry.LABEL_PROVIDER
    provider_contract = label_registry.LABEL_PROVIDER_CONTRACT
    validation_contract = "label_registry_entry_validation_v1"
    requires_configuration_check = False

    @property
    def transport(self) -> str:
        return "home_assistant_websocket"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        names = {"type", *label_registry.LABEL_FIELDS}
        if self.action == "update":
            names.add("label_id")
        return tuple(sorted(names))

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return label_registry.PROVIDER_OPERATIONS[self.action]

    def _command(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return label_registry.write_command(
            self.action, target_id, current_config, proposed_config
        )

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        command = self._command(target_id, proposed_config, current_config)
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=tuple(
                sorted(command, key=lambda item: item.encode("utf-8"))
            ),
            arguments_hash=stable_hash(command),
        )

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "label registry payloads require the planned current state"
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError(
            "label registry argument names require the planned current state"
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = proposed_config.get("label_id")
        return self._command(target_id, proposed_config, current_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        # Exactly the planned final entry is present, so nothing is sent.
        if observed is None:
            return False
        try:
            return label_registry.normalize_entry(
                observed
            ) == label_registry.normalize_entry(
                label_registry.without_bindings(proposed_config)
            )
        except (TypeError, ValueError):
            return False

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        check = getattr(gateway, "label_preflight_evidence", None)
        if check is None or (current_config is None) != (self.action == "create"):
            return ("dependency_evidence_unavailable",)
        try:
            return tuple(
                await check(target_id, current_config, proposed_config)
            )
        except AuthorizationError:
            return ("permission_failure",)
        except Exception:
            return ("dependency_evidence_unavailable",)

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == label_registry.UNAUTHORIZED_REASON
        ):
            return ("provider_permission_denied",)
        return ()


class DashboardMetadataConfigurationStrategy(ConfigurationStrategy):
    """One closed dashboard metadata update for one exact dashboard (HAMCP-106).

    The plan records the full final state (metadata row plus the content
    fingerprint); the single command sent is rebuilt from the plan's own
    recorded current state and carries ``dashboard_id`` plus only the changed
    fields.  Create, delete, content and resource commands are never built.
    """

    resource_type = dashboard_metadata.RESOURCE_TYPE
    provider = dashboard_metadata.PROVIDER
    provider_contract = dashboard_metadata.PROVIDER_CONTRACT
    validation_contract = "dashboard_metadata_validation_v1"
    requires_configuration_check = False

    @property
    def transport(self) -> str:
        return "home_assistant_websocket"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return tuple(sorted({"type", "dashboard_id", *dashboard_metadata.FIELDS}))

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return dashboard_metadata.PROVIDER_OPERATIONS[self.action]

    def _command(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self.action != dashboard_metadata.ACTION:
            raise ValueError("dashboard metadata is update-only")
        return dashboard_metadata.write_command(
            target_id, current_config, proposed_config
        )

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        command = self._command(target_id, proposed_config, current_config)
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=tuple(
                sorted(command, key=lambda item: item.encode("utf-8"))
            ),
            arguments_hash=stable_hash(command),
        )

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "dashboard metadata payloads require the planned current state"
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError(
            "dashboard metadata argument names require the planned current state"
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = proposed_config.get("url_path")
        return self._command(target_id, proposed_config, current_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        # Exactly the planned final state, content included, is present.
        if observed is None:
            return False
        try:
            return dashboard_metadata.normalize_state(
                observed
            ) == dashboard_metadata.normalize_state(proposed_config)
        except (TypeError, ValueError):
            return False

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        check = getattr(gateway, "dashboard_metadata_preflight_evidence", None)
        if check is None or current_config is None:
            return ("dependency_evidence_unavailable",)
        try:
            return tuple(
                await check(target_id, current_config, proposed_config)
            )
        except AuthorizationError:
            return ("permission_failure",)
        except Exception:
            return ("dependency_evidence_unavailable",)

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == dashboard_metadata.UNAUTHORIZED_REASON
        ):
            return ("provider_permission_denied",)
        return ()


class SceneConfigurationConfigurationStrategy(ConfigurationStrategy):
    """One closed scene configuration update for one exact scene (HAMCP-071).

    The plan records the full final state (the exact stored configuration and
    the loaded scene identity); the single POST is rebuilt from the plan's own
    recorded current state and carries the full proposed configuration
    without ``id``.  Activation, create, delete and reload are never built.
    """

    resource_type = scene_configuration.RESOURCE_TYPE
    provider = scene_configuration.PROVIDER
    provider_contract = scene_configuration.PROVIDER_CONTRACT
    validation_contract = "scene_configuration_validation_v1"
    requires_configuration_check = False

    @property
    def transport(self) -> str:
        return "home_assistant_rest"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("body", "config_id", "method")

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return scene_configuration.PROVIDER_OPERATIONS[self.action]

    def _command(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self.action != scene_configuration.ACTION:
            raise ValueError("scene configuration is update-only")
        return scene_configuration.write_command(
            target_id, current_config, proposed_config
        )

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        command = self._command(target_id, proposed_config, current_config)
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=tuple(
                sorted(command, key=lambda item: item.encode("utf-8"))
            ),
            arguments_hash=stable_hash(command),
        )

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "scene configuration payloads require the planned current state"
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        raise NotImplementedError(
            "scene configuration argument names require the planned current state"
        )

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_id = proposed_config.get("config_id")
        return self._command(target_id, proposed_config, current_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        # Exactly the planned final state, loaded scene included, is present.
        if observed is None:
            return False
        try:
            # Type-exact: 1, 1.0 and True are distinct stored values.
            return scene_configuration.exactly_equal(
                scene_configuration.normalize_state(observed),
                scene_configuration.normalize_state(proposed_config),
            )
        except (TypeError, ValueError):
            return False

    def dispatch_scope(
        self, operation: Any, provider_config: dict[str, Any]
    ) -> AbstractContextManager[Any]:
        """HAMCP-071 R1-H2: the single-use permit for exactly this POST."""

        current = operation.current_config() or {}
        return scene_configuration.write_permit(
            scene_configuration.ScenePermit(
                plan_id=operation.plan_id,
                task_id=operation.task_id,
                operation_id=operation.operation_id,
                config_id=operation.target.target_id,
                entity_id=str(current.get("entity_id")),
                arguments_hash=operation.provider_descriptor.arguments_hash,
                intent=scene_configuration.DISPATCH_INTENT,
            )
        )

    async def observation_gap(
        self, gateway: Any, operation: Any, observed: dict[str, Any] | None
    ) -> tuple[str, ...]:
        """HAMCP-071 R1-H1: a stored match without the save reload is unverified.

        The stored configuration can equal the plan while the loaded scene
        still holds the previous member snapshots, which no read exposes.
        Only a reload observed after this operation's own POST proves the
        loaded scene; until then the readback is incomplete evidence, never
        verified and never a mismatch.
        """

        proposed = operation.proposed_config()
        if observed is None or any(
            observed.get(key) != proposed.get(key)
            for key in ("config_id", "entity_id", "registry_entry_id")
        ):
            return ()
        if not scene_configuration.exactly_equal(
            observed.get("config"), proposed.get("config")
        ):
            return ()
        check = getattr(gateway, "scene_configuration_reload_observed", None)
        if (
            check is not None
            and observed.get("loaded_matches_config") is True
            and check(operation.target.target_id, operation.operation_id) is True
        ):
            return ()
        return (
            scene_configuration.STORED_CONFIGURATION_VERIFIED,
            scene_configuration.RELOAD_UNVERIFIED,
        )

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        check = getattr(gateway, "scene_configuration_preflight_evidence", None)
        if check is None or current_config is None:
            return ("dependency_evidence_unavailable",)
        try:
            return tuple(
                await check(target_id, current_config, proposed_config)
            )
        except AuthorizationError:
            return ("permission_failure",)
        except Exception:
            return ("dependency_evidence_unavailable",)

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == scene_configuration.UNAUTHORIZED_REASON
        ):
            return ("provider_permission_denied",)
        return ()


_STRATEGIES = {
    "automation": AutomationConfigurationStrategy,
    "script": ScriptConfigurationStrategy,
    "input_boolean": InputBooleanConfigurationStrategy,
    "input_number": InputNumberConfigurationStrategy,
    ENTITY_METADATA_RESOURCE_TYPE: (
        EntityRegistryMetadataConfigurationStrategy
    ),
    availability_exposure.AVAILABILITY_RESOURCE_TYPE: (
        EntityRegistryAvailabilityConfigurationStrategy
    ),
    availability_exposure.EXPOSURE_RESOURCE_TYPE: (
        EntityAssistantExposureConfigurationStrategy
    ),
    label_registry.LABEL_RESOURCE_TYPE: LabelRegistryEntryConfigurationStrategy,
    dashboard_metadata.RESOURCE_TYPE: DashboardMetadataConfigurationStrategy,
    scene_configuration.RESOURCE_TYPE: SceneConfigurationConfigurationStrategy,
}


def strategy_for(
    resource_type: str, action: str
) -> ConfigurationStrategy:
    """Return one reviewed strategy or fail closed."""

    strategy_type = _STRATEGIES.get(resource_type)
    if strategy_type is None:
        raise ValueError("configuration resource type is not reviewed")
    return strategy_type(action)
