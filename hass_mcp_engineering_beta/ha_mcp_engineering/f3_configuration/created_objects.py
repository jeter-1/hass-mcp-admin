"""F3 adapters for scene create and created-object deletion (HAMCP-329/298).

Design: ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.3 (scene create) and §3.4.3/§3.5 (created-object delete).

- ``SceneCreateAdapter`` executes one ``create_scene_configuration_configuration``
  child: absence is rechecked at preflight and immediately before durable
  intent, the one POST is sent under the single-use create permit, and the
  stored configuration plus the scene registry entry are polled (6 x 0.5 s).
  A stored configuration whose registry entry never appears is
  ``scene_config_stored_registry_unverified``: manual review, never success.
- ``CreatedObjectDeleteAdapter`` executes one ``delete_created_*`` child.  It
  reuses the HAMCP-155 delete adapter's preflight, drift recheck and absence
  polling through a closed view of the created-object gateway methods, and
  additionally requires the plan-bound created-object ownership marker: a
  child without one is refused before any read.

Neither adapter changes the generic create/update adapter or the HAMCP-155
automation/script delete contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome

from ..errors import AuthorizationError
from ..governance import created_object_delete as created_contract
from ..governance import scene_configuration as scene_contract
from ..governance.normalize import stable_hash
from .adapter import ConfigurationOperationAdapter
from .delete import (
    CORE_ADMISSION_UNAVAILABLE,
    CREATED_OBJECT_BINDING_UNAVAILABLE,
    ConfigurationDeleteAdapter,
    ConfigurationDeleteStrategy,
)
from .models import (
    ConfigurationObservationResult,
    ConfigurationProviderDescriptor,
    ConfigurationVerificationResult,
    PreparedConfigurationOperation,
    bounded_mismatches,
)
from .observability import (
    ConfigurationAdapterMetrics,
    ConfigurationEventSink,
    NullConfigurationEventSink,
)
from .strategies import SceneConfigurationConfigurationStrategy


CREATED_OBJECT_MARKER_REQUIRED = "created_object_marker_required"
SCENE_CREATE_NOT_APPLIED = "scene_create_not_applied"
SCENE_CREATE_STORED_DIFFERS = "scene_create_stored_configuration_differs"
SCENE_CREATE_REGISTRY_AMBIGUOUS = "scene_create_registry_entry_ambiguous"

# Design §3.2.1 and the §3.3 readback table: registering the same
# ``(scene, homeassistant, <config id>)`` again takes back the deleted
# entry's ``disabled_by``, ``hidden_by``, area and options.  Those are
# reported on a verified create as closed codes and are never a failure.  A
# value outside the reviewed registry vocabularies is reported as
# unrecognized; an area is reported by presence only, never by its id.
SCENE_CREATE_OBSERVED_PREFIX = "scene_create_observed_"
OBSERVED_DISABLED_BY = frozenset(
    {"config_entry", "device", "hass", "integration", "user"}
)
OBSERVED_HIDDEN_BY = frozenset({"integration", "user"})
OBSERVED_ASSISTANTS = ("cloud.alexa", "cloud.google_assistant", "conversation")


def observed_registry_metadata(entry: dict[str, Any]) -> tuple[str, ...]:
    """The closed report of what one scene registry entry carries.

    Read only from the entry the create readback already holds: nothing is
    requested for it and the entry is not changed.
    """

    codes: list[str] = []
    for field, known in (
        ("disabled_by", OBSERVED_DISABLED_BY),
        ("hidden_by", OBSERVED_HIDDEN_BY),
    ):
        value = entry.get(field)
        if value is not None:
            name = value if type(value) is str and value in known else "unrecognized"
            codes.append(f"{SCENE_CREATE_OBSERVED_PREFIX}{field}_{name}")
    if entry.get("area_id") is not None:
        codes.append(f"{SCENE_CREATE_OBSERVED_PREFIX}area_assigned")
    options = entry.get("options")
    for assistant in OBSERVED_ASSISTANTS:
        settings = options.get(assistant) if type(options) is dict else None
        stored = settings.get("should_expose") if type(settings) is dict else None
        if type(stored) is bool:
            state = "exposed" if stored else "unexposed"
            codes.append(
                f"{SCENE_CREATE_OBSERVED_PREFIX}{state}_{assistant.replace('.', '_')}"
            )
    return tuple(codes)


# ---------------------------------------------------------------------------
# HAMCP-329: scene create
# ---------------------------------------------------------------------------


class SceneCreateStrategy(SceneConfigurationConfigurationStrategy):
    """The closed create POST of one absent scene config id.

    The plan binds the exact proposed stored configuration
    (``{"id", "name", "icon"?, "entities"}``) and the absence fingerprint;
    the POST body is that configuration without ``id``.
    """

    # The create is admitted only by its own compiled profile's adapter.
    provider_contract = created_contract.PROVIDER_CONTRACTS[
        scene_contract.RESOURCE_TYPE
    ]
    validation_contract = "scene_create_validation_v1"

    def __init__(self, action: str = scene_contract.CREATE_ACTION) -> None:
        if action != scene_contract.CREATE_ACTION:
            raise ValueError("the scene create strategy supports create only")
        self.action = scene_contract.CREATE_ACTION

    @property
    def capability_identity(self) -> str:
        return scene_contract.CREATE_CAPABILITY_IDENTITY

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return scene_contract.CREATE_PROVIDER_OPERATION

    def canonical_target(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> str:
        del proposed_config
        if not scene_contract.config_id_valid(target_id):
            raise ValueError("scene create target must be a config id")
        return target_id

    def validate(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        errors = tuple(
            dict.fromkeys(
                scene_contract.create_proposed_errors(target_id, proposed_config)
            )
        )
        return not errors, errors, ()

    def _command(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if current_config is not None:
            raise ValueError("a scene create has no current configuration")
        return scene_contract.create_command(target_id, proposed_config)

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._command(proposed_config.get("id"), proposed_config, current_config)

    def already_reached(
        self,
        observed: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> bool:
        # An existing scene is never adopted as Engineering's create.
        del observed, proposed_config
        return False

    def dispatch_scope(
        self, operation: Any, provider_config: dict[str, Any]
    ) -> AbstractContextManager[Any]:
        """The single-use create permit for exactly this POST."""

        return scene_contract.write_permit(
            scene_contract.ScenePermit(
                plan_id=operation.plan_id,
                task_id=operation.task_id,
                operation_id=operation.operation_id,
                config_id=operation.target.target_id,
                entity_id="",
                arguments_hash=operation.provider_descriptor.arguments_hash,
                intent=scene_contract.DISPATCH_INTENT,
                action=scene_contract.CREATE_ACTION,
            )
        )

    async def preflight_evidence(
        self,
        gateway: Any,
        target_id: str,
        current_config: dict[str, Any] | None,
        proposed_config: dict[str, Any],
    ) -> tuple[str, ...]:
        # Absence is the create's whole current-state evidence; it is checked
        # by the adapter's authoritative state read.
        del gateway, target_id, current_config, proposed_config
        return ()


class SceneCreateAdapter(ConfigurationOperationAdapter):
    """One ``OperationAdapter`` for a governed scene create."""

    PREDISPATCH_READS_HOME_ASSISTANT = True

    def __init__(
        self,
        gateway: Any,
        *,
        admission: Callable[[], bool],
        metrics: ConfigurationAdapterMetrics | None = None,
        event_sink: ConfigurationEventSink | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        poll_attempts: int = scene_contract.CREATE_POLL_ATTEMPTS,
        poll_interval: float = scene_contract.CREATE_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(admission):
            raise TypeError("a scene create adapter requires an admission check")
        self.strategy = SceneCreateStrategy()
        self.gateway = gateway
        self.metrics = metrics or ConfigurationAdapterMetrics()
        self.event_sink = event_sink or NullConfigurationEventSink()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._admission = admission
        self._sleep = sleep or asyncio.sleep
        self._poll_attempts = max(1, int(poll_attempts))
        self._poll_interval = max(0.0, float(poll_interval))

    def _admitted(self) -> bool:
        try:
            return self._admission() is True
        except Exception:
            return False

    async def preflight(self, operation, *, acquired_locks):
        required_locks, rejection = self._preflight_gates(operation, acquired_locks)
        if rejection is not None:
            self._increment("preflight_attempts")
            return rejection
        if not self._admitted():
            self._increment("preflight_attempts")
            return self._preflight_rejected(
                operation,
                (CORE_ADMISSION_UNAVAILABLE,),
                outcome=NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH,
            )
        del required_locks
        return await super().preflight(operation, acquired_locks=acquired_locks)

    async def _authoritative_state(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[dict[str, Any] | None, str]:
        """Absence by config GET and by scene registry identity (§3.3)."""

        try:
            reason = await self.gateway.scene_create_target_absent_reason(
                operation.target.target_id
            )
        except Exception as exc:
            return None, self.strategy.read_error_code(exc)
        if reason is not None:
            return None, reason
        if self.strategy.fingerprint(None) != operation.current_state_fingerprint:
            return None, "stale_target_state"
        return None, "state_matches_plan"

    async def _predispatch_refusal(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[str, ...]:
        if not self._admitted():
            return (CORE_ADMISSION_UNAVAILABLE,)
        _state, code = await self._authoritative_state(operation)
        return () if code == "state_matches_plan" else (code,)

    async def _send(
        self,
        operation: PreparedConfigurationOperation,
        provider_config: dict[str, Any],
    ) -> Any:
        return await self.gateway.scene_create_write(
            operation.target.target_id, provider_config
        )

    async def _observe(
        self,
        operation: PreparedConfigurationOperation,
        *,
        attempt_count: int,
        error_result_sent: bool = False,
    ) -> ConfigurationObservationResult:
        del error_result_sent
        self._increment("readbacks")
        if not self._admitted():
            return self._incomplete(operation, attempt_count, CORE_ADMISSION_UNAVAILABLE)
        proposed = operation.proposed_config()
        config: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        for poll in range(self._poll_attempts):
            try:
                config, entries = await self.gateway.scene_create_observation(
                    operation.target.target_id
                )
            except Exception as exc:
                return self._incomplete(
                    operation, attempt_count, self.strategy.observe_error_code(exc)
                )
            if config is None:
                break  # a 404 after the POST is not applied, never retried
            if not scene_contract.exactly_equal(config, proposed):
                break
            if entries:
                break
            if poll + 1 < self._poll_attempts:
                await self._sleep(self._poll_interval)
        mismatches: list[str] = []
        codes: tuple[str, ...] = ("exact_readback_completed",)
        exact = config is not None and scene_contract.exactly_equal(config, proposed)
        # Exactly one entry, on the scene platform.  What that entry carries
        # (a restored ``disabled_by``, ``hidden_by``, area or exposure) does
        # not decide the outcome; it is reported below.
        entry_ok = len(entries) == 1 and entries[0].get("platform") == "homeassistant"
        verified = exact and entry_ok
        observed = observed_registry_metadata(entries[0]) if verified else ()
        if config is None:
            mismatches.append("configuration_absent")
            codes += (SCENE_CREATE_NOT_APPLIED,)
        elif not exact:
            mismatches.append("stored_configuration")
            codes += (SCENE_CREATE_STORED_DIFFERS,)
        elif not entries:
            mismatches.append("registry_entry_absent")
            codes += (scene_contract.CREATE_REGISTRY_UNVERIFIED,)
        elif not entry_ok:
            mismatches.append("registry_entry")
            codes += (SCENE_CREATE_REGISTRY_AMBIGUOUS,)
        codes += observed
        try:
            resulting = None if config is None else self.strategy.fingerprint(config)
        except (TypeError, ValueError):
            resulting = stable_hash(config)
        evidence = {
            "capability_identity": operation.capability_identity,
            "target_id": operation.target.target_id,
            "config_present": config is not None,
            "config_exact": exact,
            "registry_entries": len(entries),
            "registry_metadata": list(observed),
            "mismatch_fields": list(bounded_mismatches(mismatches)),
        }
        result = ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=config is not None,
            readback_state_fingerprint=resulting,
            intended_result_observed=verified,
            mismatch_fields=bounded_mismatches(mismatches),
            evidence_hash=stable_hash(evidence),
            diagnostic_codes=codes,
            identity_match=config is not None and config.get("id") == operation.target.target_id,
            resource_exists=config is not None,
            semantic_match=verified,
            normalization_valid=True,
            configuration_check_status="not_applicable",
            normalized_observed_fingerprint=resulting,
        )
        self._emit("observation", operation.target.target_id, "observing", codes)
        return result

    def _incomplete(
        self,
        operation: PreparedConfigurationOperation,
        attempt_count: int,
        code: str,
    ) -> ConfigurationObservationResult:
        self._emit("observation", operation.target.target_id, "observing", (code,))
        return ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=False,
            provider_reachable=None if code == CORE_ADMISSION_UNAVAILABLE else False,
            target_reachable=None,
            readback_state_fingerprint=None,
            intended_result_observed=None,
            mismatch_fields=(),
            evidence_hash=None,
            diagnostic_codes=(code,),
            identity_match=None,
            resource_exists=None,
            semantic_match=None,
            normalization_valid=None,
            configuration_check_status="unavailable",
            normalized_observed_fingerprint=None,
        )

    async def verify(
        self,
        operation: PreparedConfigurationOperation,
        observation: ConfigurationObservationResult,
    ) -> ConfigurationVerificationResult:
        self._require_operation(operation)
        if not observation.observation_complete:
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.OBSERVING,
                verified=None,
                codes=("readback_incomplete",),
            )
        if (
            observation.intended_result_observed is True
            and observation.semantic_match is True
            and not observation.mismatch_fields
            # Cursor L1: the resulting fingerprint is the proposed hash.
            and observation.readback_state_fingerprint
            == operation.normalized_proposed_hash
        ):
            self._increment("verification_successes")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                verified=True,
                codes=("exact_readback_verified",),
            )
        if scene_contract.CREATE_REGISTRY_UNVERIFIED in observation.diagnostic_codes:
            # Item 15: stored but the registry entry was not observed within
            # the bound: not success and never rollback-eligible.
            self._increment("manual_review_transitions")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                verified=False,
                codes=(scene_contract.CREATE_REGISTRY_UNVERIFIED,),
            )
        self._increment("verification_mismatches")
        return self._verification_result(
            operation,
            observation,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            verified=False,
            codes=("exact_readback_mismatch",),
        )


# ---------------------------------------------------------------------------
# HAMCP-298: created-object delete
# ---------------------------------------------------------------------------


class CreatedObjectDeleteStrategy(ConfigurationDeleteStrategy):
    """The closed delete of one Engineering-created scene or storage helper."""

    provider = created_contract.PROVIDER
    validation_contract = "created_object_delete_intent_validation_v1"

    def __init__(self, resource_type: str) -> None:
        if resource_type not in created_contract.RESOURCE_TYPES:
            raise ValueError("created object delete resource type is not reviewed")
        self.resource_type = resource_type
        self.action = created_contract.ACTION
        self.provider_contract = created_contract.PROVIDER_CONTRACTS[resource_type]

    @property
    def capability_identity(self) -> str:
        return created_contract.CAPABILITY_IDENTITIES[self.resource_type]

    @property
    def transport(self) -> str:
        if self.resource_type in created_contract.HELPER_TYPES:
            return "home_assistant_websocket"
        return "home_assistant_rest"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        if self.resource_type in created_contract.HELPER_TYPES:
            return tuple(sorted(("type", f"{self.resource_type}_id")))
        return ("method", "path")

    def canonical_target(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> str:
        del proposed_config
        errors = created_contract._target_errors(self.resource_type, target_id)
        if errors:
            raise ValueError("; ".join(errors))
        if not isinstance(target_id, str) or target_id != target_id.strip():
            raise ValueError("configuration target identity is not canonical")
        return target_id

    def validate(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        errors = tuple(
            dict.fromkeys(
                created_contract.intent_errors(
                    self.resource_type, target_id, proposed_config
                )
            )
        )
        return not errors, errors, ()

    def normalize(self, config: dict[str, Any] | None) -> dict[str, Any] | None:
        return created_contract.normalize_intent(config)

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return f"{self.resource_type}_created_object_delete"

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        del proposed_config
        return created_contract.provider_payload(self.resource_type, target_id)

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        del current_config
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=self.allowed_argument_names,
            arguments_hash=stable_hash(
                self.provider_payload(target_id, proposed_config)
            ),
        )

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == created_contract.DELETE_REJECTED_REASON
        ):
            return (created_contract.DELETE_REJECTED_REASON,)
        return ()


class _CreatedDeleteGatewayView:
    """The HAMCP-155 adapter's gateway names, bound to the created gateway.

    Only these five calls exist; every one is routed to the created-object
    gateway methods for this adapter's one resource type.
    """

    def __init__(self, gateway: Any, resource_type: str) -> None:
        self._gateway = gateway
        self._resource_type = resource_type

    @property
    def provider_admitted(self) -> bool:
        return getattr(self._gateway, "provider_admitted", False) is True

    def _require(self, resource_type: str) -> None:
        if resource_type != self._resource_type:
            raise ValueError("created delete view is bound to one resource type")

    async def delete_read_config(self, resource_type: str, target_id: str):
        self._require(resource_type)
        return await self._gateway.created_delete_read_config(resource_type, target_id)

    async def delete_registry_entry(self, resource_type: str, target_id: str):
        self._require(resource_type)
        return await self._gateway.created_delete_registry_entry(
            resource_type, target_id
        )

    async def delete_state_exists(self, entity_id: str) -> bool:
        return await self._gateway.created_delete_state_exists(
            self._resource_type, entity_id
        )

    async def delete_consumer_fingerprint(self, entity_id: str) -> str | None:
        return await self._gateway.created_delete_consumer_fingerprint(entity_id)

    async def delete_configuration(self, resource_type: str, target_id: str):
        self._require(resource_type)
        return await self._gateway.created_delete(resource_type, target_id)


class CreatedObjectDeleteAdapter(ConfigurationDeleteAdapter):
    """One ``OperationAdapter`` for a created scene or helper delete child."""

    def __init__(
        self,
        resource_type: str,
        gateway: Any,
        *,
        admission: Callable[[], bool],
        created_object_binding: Callable[
            [PreparedConfigurationOperation], dict[str, Any] | None
        ],
        metrics: ConfigurationAdapterMetrics | None = None,
        event_sink: ConfigurationEventSink | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        poll_attempts: int = created_contract.ABSENCE_POLL_ATTEMPTS,
        poll_interval: float = created_contract.ABSENCE_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(created_object_binding):
            raise TypeError("a created-object delete requires its ownership binding")
        super().__init__(
            "automation",
            _CreatedDeleteGatewayView(gateway, resource_type),
            admission=admission,
            metrics=metrics,
            event_sink=event_sink,
            now=now,
            sleep=sleep,
            poll_attempts=poll_attempts,
            poll_interval=poll_interval,
            created_object_binding=created_object_binding,
        )
        self.strategy = CreatedObjectDeleteStrategy(resource_type)

    @staticmethod
    def _intent(operation: PreparedConfigurationOperation) -> dict[str, Any]:
        return created_contract.intent(operation.proposed_config())

    async def preflight(self, operation, *, acquired_locks):
        # A created delete exists only as a created-object rollback child: a
        # child with no plan-bound ownership marker is refused before reads.
        try:
            binding = self._created_object_binding(operation)
        except Exception:
            return self._preflight_rejected(
                operation, (CREATED_OBJECT_BINDING_UNAVAILABLE,)
            )
        if binding is None:
            return self._preflight_rejected(
                operation, (CREATED_OBJECT_MARKER_REQUIRED,)
            )
        return await super().preflight(operation, acquired_locks=acquired_locks)

    async def _live_state_code(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            current = await self.gateway.delete_read_config(
                operation.resource_type, operation.target.target_id
            )
        except AuthorizationError as exc:
            return None, self.strategy.read_error_code(exc)
        except Exception as exc:
            return None, self.strategy.read_error_code(exc)
        if current is None:
            return None, "delete_target_missing"
        if not _created_identity_matches(
            operation.resource_type, operation.target.target_id, current
        ):
            return current, "resource_identity_mismatch"
        if self.strategy.fingerprint(current) != operation.current_state_fingerprint:
            return current, "stale_target_state"
        return current, "state_matches_plan"


def _created_identity_matches(
    resource_type: str, target_id: str, config: dict[str, Any]
) -> bool:
    """The stored body's own id is the storage object id."""

    from ..governance import created_object_identity as identity_model

    try:
        storage_id = identity_model.storage_object_id_for(resource_type, target_id)
    except identity_model.IdentityMismatch:
        return False
    return isinstance(config, dict) and config.get("id") == storage_id


__all__ = [
    "CREATED_OBJECT_MARKER_REQUIRED",
    "CreatedObjectDeleteAdapter",
    "CreatedObjectDeleteStrategy",
    "SCENE_CREATE_NOT_APPLIED",
    "SCENE_CREATE_STORED_DIFFERS",
    "SceneCreateAdapter",
    "SceneCreateStrategy",
]
