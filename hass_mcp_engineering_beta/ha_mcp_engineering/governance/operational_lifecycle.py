"""Shared readback boundary for governed reload and restart operations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Awaitable, Callable

from ..clients.rest import HomeAssistantRestClient
from ..clients.websocket import HomeAssistantWebSocketClient
from ..errors import (
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..providers.operational_lifecycle import (
    OperationalDispatchResult,
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ..providers.supervisor_self import (
    SelfAddonIdentityError,
    SupervisorSelfAddonIdentity,
)
from ..sanitization import sanitize_untrusted_data
from .config_validation import normalize_configuration_validation


# This is the repository source slug, not an installed Supervisor identity.
# Remote and local repositories prepend an installation-specific repository ID.
ENGINEERING_ADDON_SLUG = "hass_mcp_engineering_beta"
UPSTREAM_PROVIDER_CONTRACT_FIELDS = (
    "provider",
    "server_name",
    "server_version",
    "protocol_version",
    "compatibility_entry_id",
    "catalog_fingerprint",
    "tool_contract_fingerprints",
    "argument_constraints",
)
UPSTREAM_PROVIDER_IDENTITY_FIELDS = (
    "provider",
    "server_name",
    "server_version",
    "protocol_version",
    "compatibility_entry_id",
    "catalog_fingerprint",
)
RELOAD_SERVICES = {
    "automation": ("automation", "reload"),
    "script": ("script", "reload"),
    "input_boolean": ("input_boolean", "reload"),
    "input_number": ("input_number", "reload"),
}
MAX_STATES = 20_000
RESTART_DISRUPTION_PROBE_ATTEMPTS = 15
RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS = 1.0
_SAFE_IDENTITY = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")


class LifecycleGatewayError(RuntimeError):
    """Bounded operation failure carrying authoritative dispatch evidence."""

    def __init__(self, category: str, *, dispatched: bool = False) -> None:
        super().__init__("The governed operational lifecycle request failed.")
        self.category = category
        self.dispatched = dispatched


class OperationalLifecycleGateway:
    """Combine exact upstream actions with independent Home Assistant reads."""

    def __init__(
        self,
        provider: ReviewedOperationalLifecycleProvider,
        rest: HomeAssistantRestClient,
        websocket: HomeAssistantWebSocketClient,
        *,
        configuration_validator: Callable[[], Awaitable[Any]],
        runtime_snapshot: Callable[[], dict[str, Any]],
        process_instance_id: str,
        self_addon_identity_resolver: (
            Callable[[], Awaitable[SupervisorSelfAddonIdentity]] | None
        ) = None,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        self.provider = provider
        self.rest = rest
        self.websocket = websocket
        self.configuration_validator = configuration_validator
        self.runtime_snapshot = runtime_snapshot
        self.process_instance_id = process_instance_id
        self.self_addon_identity_resolver = (
            self_addon_identity_resolver
        )
        self.sensitive_values = sensitive_values

    async def planning_evidence(
        self, operation: str, target: str
    ) -> dict[str, Any]:
        try:
            provider = await self.provider.probe(operation)
            provider_evidence = provider.as_dict()
            if operation == "controlled_reload":
                validation = await self.configuration_validation()
                services = await self.read_services()
                domain, service = RELOAD_SERVICES[target]
                if not _service_available(services, domain, service):
                    raise LifecycleGatewayError("service_unavailable")
                baseline = {
                    "configuration_validation": validation,
                    "service_available": True,
                    "service": f"{domain}.{service}",
                    "domain_evidence": await self.read_domain(target),
                }
            elif operation == "restart_addon":
                if self.self_addon_identity_resolver is None:
                    raise LifecycleGatewayError(
                        "self_addon_identity_unavailable"
                    )
                try:
                    self_identity = (
                        await self.self_addon_identity_resolver()
                    )
                except SelfAddonIdentityError:
                    raise LifecycleGatewayError(
                        "self_addon_identity_unavailable"
                    ) from None
                addon = await self.provider.get_addon(target)
                upstream_identity = addon.get(
                    "upstream_addon_identity"
                )
                target_class = _addon_target_class(
                    target,
                    addon,
                    self_identity,
                    upstream_identity,
                )
                upstream_binding = (
                    _upstream_addon_binding(
                        upstream_identity,
                        provider_contract=provider_evidence,
                        require_provider_contract=True,
                    )
                    if target_class != "engineering_addon"
                    else None
                )
                if (
                    upstream_binding is not None
                    and not _provider_identity_matches(
                        upstream_binding["admission_evidence"],
                        provider_evidence,
                    )
                ):
                    raise LifecycleGatewayError(
                        "upstream_addon_identity_unavailable"
                    )
                baseline = {
                    "addon": _addon_identity(addon),
                    "target_class": target_class,
                    "target_identity": {
                        "requested_slug": target,
                        "resolved_slug": addon.get("slug"),
                        "resolved_name": addon.get("name"),
                        "resolved_version": addon.get("version"),
                        "resolved_repository": addon.get("repository"),
                        "identity_source": (
                            self_identity.as_dict().get(
                                "identity_source"
                            )
                            if target_class == "engineering_addon"
                            else upstream_binding.get(
                                "identity_source"
                            )
                        ),
                        "authoritative_self_match": (
                            target_class == "engineering_addon"
                        ),
                        "authoritative_upstream_match": (
                            target_class == "upstream_ha_mcp_addon"
                        ),
                        "target_class": target_class,
                    },
                    "process_instance_id": self.process_instance_id,
                }
                if upstream_binding is not None:
                    baseline["upstream_addon_identity"] = (
                        upstream_binding
                    )
                if target_class in {
                    "engineering_addon",
                    "upstream_ha_mcp_addon",
                }:
                    baseline["runtime"] = self.runtime_snapshot()
            elif operation == "restart_home_assistant":
                validation = await self.configuration_validation()
                baseline = {
                    "configuration_validation": validation,
                    "home_assistant": await self.read_home_assistant_identity(),
                    "runtime": self.runtime_snapshot(),
                    "process_instance_id": self.process_instance_id,
                }
            else:
                raise LifecycleGatewayError("invalid_request")
        except OperationalLifecycleProviderError as exc:
            raise LifecycleGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None
        return {"provider": provider_evidence, "baseline": baseline}

    async def configuration_validation(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            raw = await self.configuration_validator()
        except AuthorizationError:
            return {
                "status": "unavailable",
                "checked_at": checked_at,
                "failure_category": "permission_failure",
            }
        except (HomeAssistantUnavailableError, HomeAssistantTimeoutError):
            return {
                "status": "unavailable",
                "checked_at": checked_at,
                "failure_category": "home_assistant_unavailable",
            }
        except HomeAssistantApiError:
            return {
                "status": "failed",
                "checked_at": checked_at,
                "failure_category": "configuration_check_failed",
            }
        except Exception:
            return {
                "status": "failed",
                "checked_at": checked_at,
                "failure_category": "configuration_check_failed",
            }
        status, evidence = normalize_configuration_validation(
            raw, known_secrets=self.sensitive_values
        )
        reason = evidence.get("reason")
        if status == "valid":
            normalized = "valid"
        elif reason in {
            "configuration_invalid",
            "configuration_errors_present",
        }:
            normalized = "invalid"
        else:
            normalized = "failed"
        safe = sanitize_untrusted_data(
            evidence,
            known_secrets=self.sensitive_values,
            max_string=500,
        )
        return {
            "status": normalized,
            "checked_at": checked_at,
            "evidence": safe.value if isinstance(safe.value, dict) else {},
        }

    async def dispatch_reload(
        self,
        target: str,
        *,
        before_dispatch: Callable[[], None | Awaitable[None]],
    ) -> OperationalDispatchResult:
        try:
            return await self.provider.reload(
                target, before_dispatch=before_dispatch
            )
        except OperationalLifecycleProviderError as exc:
            raise LifecycleGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None

    async def dispatch_addon_restart(
        self,
        slug: str,
        *,
        before_dispatch: Callable[[], None | Awaitable[None]],
    ) -> OperationalDispatchResult:
        try:
            return await self.provider.restart_addon(
                slug, before_dispatch=before_dispatch
            )
        except OperationalLifecycleProviderError as exc:
            raise LifecycleGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None

    async def dispatch_home_assistant_restart(
        self,
        *,
        before_dispatch: Callable[[], None | Awaitable[None]],
    ) -> OperationalDispatchResult:
        try:
            return await self.provider.restart_home_assistant(
                before_dispatch=before_dispatch
            )
        except OperationalLifecycleProviderError as exc:
            raise LifecycleGatewayError(
                exc.category, dispatched=exc.dispatched
            ) from None

    async def verify_reload(self, target: str) -> dict[str, Any]:
        validation = await self.configuration_validation()
        if validation.get("status") != "valid":
            return {
                "status": (
                    "pending"
                    if validation.get("status") == "unavailable"
                    else "failed"
                ),
                "mismatch_fields": ["configuration_validation"],
                "evidence": {
                    "configuration_validation": validation,
                    "redispatch_performed": False,
                },
            }
        services = await self.read_services()
        domain, service = RELOAD_SERVICES[target]
        if not _service_available(services, domain, service):
            return {
                "status": "failed",
                "mismatch_fields": ["reload_service"],
                "evidence": {
                    "configuration_validation": validation,
                    "redispatch_performed": False,
                },
            }
        domain_evidence = await self.read_domain(target)
        return {
            "status": "verified",
            "mismatch_fields": [],
            "evidence": {
                "configuration_validation": validation,
                "service": f"{domain}.{service}",
                "service_available": True,
                "domain_evidence": domain_evidence,
                "home_assistant_connected": True,
                "redispatch_performed": False,
            },
        }

    async def verify_addon_restart(
        self,
        slug: str,
        *,
        baseline: dict[str, Any],
        provider_response_received: bool,
        provider_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            addon = await self.provider.get_addon(slug)
        except OperationalLifecycleProviderError as exc:
            if exc.category in {
                "provider_timeout",
                "provider_unavailable",
                "addon_not_found",
                "resource_not_found",
            }:
                return {
                    "status": "pending",
                    "mismatch_fields": ["addon_unavailable"],
                    "evidence": {
                        "failure_category": exc.category,
                        "redispatch_performed": False,
                    },
                }
            if exc.category in {
                "catalog_mismatch",
                "reviewed_contract_mismatch",
                "server_identity_mismatch",
                "upstream_version_mismatch",
                "unsupported_protocol_version",
                "required_tool_missing",
            }:
                return _upstream_binding_verification_result(
                    "failed",
                    identity_status="provider_contract_mismatch",
                    failure_category=exc.category,
                )
            raise LifecycleGatewayError(
                exc.category, dispatched=True
            ) from None
        current = _addon_identity(addon)
        expected = baseline.get("addon")
        if not isinstance(expected, dict) or any(
            current.get(field) != expected.get(field)
            for field in ("slug", "name", "version")
        ):
            return {
                "status": "failed",
                "mismatch_fields": ["addon_identity"],
                "evidence": {
                    "addon": current,
                    "redispatch_performed": False,
                },
            }
        if current.get("state") not in {"started", "running"}:
            return {
                "status": "pending",
                "mismatch_fields": ["addon_running_state"],
                "evidence": {
                    "addon": current,
                    "redispatch_performed": False,
                },
            }
        target_class = baseline.get("target_class")
        process_restarted = (
            target_class == "engineering_addon"
            and baseline.get("process_instance_id")
            != self.process_instance_id
        )
        restart_proof: str | None = None
        observed_runtime = self.runtime_snapshot()
        runtime = (
            observed_runtime
            if isinstance(observed_runtime, dict)
            else {}
        )
        if target_class == "engineering_addon":
            planned_runtime = baseline.get("runtime")
            runtime_ready = _engineering_runtime_ready(
                planned_runtime, runtime
            )
            if not runtime_ready:
                return {
                    "status": "pending",
                    "mismatch_fields": ["engineering_runtime"],
                    "evidence": {
                        "addon": current,
                        "target_class": target_class,
                        "process_instance_changed": process_restarted,
                        "runtime_identity_available": _runtime_identity_available(
                            runtime
                        ),
                        "governance_storage_healthy": (
                            runtime.get("governance_storage_status")
                            == "healthy"
                        ),
                        "audit_continuity_available": (
                            runtime.get("audit_storage_status")
                            == "healthy"
                            and runtime.get("audit_write_failures") == 0
                        ),
                        "redispatch_performed": False,
                    },
                }
            if process_restarted:
                restart_proof = "process_identity"
        if target_class == "upstream_ha_mcp_addon":
            planned_identity = baseline.get(
                "upstream_addon_identity"
            )
            try:
                planned_binding = _upstream_addon_binding(
                    planned_identity,
                    require_provider_contract=True,
                )
            except LifecycleGatewayError:
                return _upstream_binding_verification_result(
                    "failed",
                    identity_status="baseline_incomplete",
                )
            fresh_identity = addon.get("upstream_addon_identity")
            if not isinstance(fresh_identity, dict):
                return _upstream_binding_verification_result(
                    "pending",
                    identity_status="fresh_evidence_unavailable",
                )
            fresh_status = fresh_identity.get("status")
            if fresh_status != "bound":
                conclusive = _upstream_binding_failure_is_conclusive(
                    planned_binding,
                    fresh_identity,
                )
                return _upstream_binding_verification_result(
                    "failed" if conclusive else "pending",
                    identity_status=(
                        fresh_status
                        if isinstance(fresh_status, str)
                        else "fresh_evidence_unavailable"
                    ),
                )
            try:
                admitted = await self.provider.probe("restart_addon")
            except OperationalLifecycleProviderError as exc:
                if exc.category in {
                    "catalog_mismatch",
                    "reviewed_contract_mismatch",
                    "server_identity_mismatch",
                    "upstream_version_mismatch",
                    "unsupported_protocol_version",
                    "required_tool_missing",
                }:
                    return _upstream_binding_verification_result(
                        "failed",
                        identity_status="provider_contract_mismatch",
                        failure_category=exc.category,
                    )
                return {
                    "status": "pending",
                    "mismatch_fields": ["upstream_readmission"],
                    "evidence": {
                        "failure_category": exc.category,
                        "redispatch_performed": False,
                    },
                }
            admitted_evidence = admitted.as_dict()
            try:
                fresh_binding = _upstream_addon_binding(
                    fresh_identity,
                    provider_contract=admitted_evidence,
                    require_provider_contract=True,
                )
            except LifecycleGatewayError:
                return _upstream_binding_verification_result(
                    "failed",
                    identity_status="fresh_evidence_conflicting",
                )
            if fresh_binding != planned_binding:
                return _upstream_binding_verification_result(
                    "failed",
                    identity_status="binding_mismatch",
                )
            if _upstream_readmission_matches(
                provider_evidence,
                admitted_evidence,
                runtime,
            ):
                restart_proof = "upstream_readmission"
        elif (
            target_class == "other_addon"
            and provider_response_received
        ):
            restart_proof = "provider_acknowledgement"
        if restart_proof is None:
            return {
                "status": "pending",
                "mismatch_fields": ["restart_evidence"],
                "evidence": {
                    "addon": current,
                    "target_class": target_class,
                    "provider_response_received": (
                        provider_response_received
                    ),
                    "process_instance_changed": process_restarted,
                    "redispatch_performed": False,
                },
            }
        return {
            "status": "verified",
            "mismatch_fields": [],
            "evidence": {
                "addon": current,
                "target_class": target_class,
                "restart_proof": restart_proof,
                "provider_response_received": provider_response_received,
                "process_instance_changed": process_restarted,
                "version_unchanged": current.get("version")
                == expected.get("version"),
                "running": True,
                "redispatch_performed": False,
            },
        }

    async def verify_home_assistant_restart(
        self,
        *,
        baseline: dict[str, Any],
        restart_dispatch_confirmed: bool,
        expected_disruption_observed: bool,
    ) -> dict[str, Any]:
        if not expected_disruption_observed:
            try:
                disruption_observed = (
                    await self._observe_home_assistant_disruption()
                )
            except LifecycleGatewayError as exc:
                return {
                    "status": "pending",
                    "mismatch_fields": ["home_assistant_recovery"],
                    "evidence": {
                        "failure_category": exc.category,
                        "redispatch_performed": False,
                    },
                }
            if disruption_observed:
                return {
                    "status": "pending",
                    "mismatch_fields": ["home_assistant_recovery"],
                    "evidence": {
                        "expected_disruption_observed": True,
                        "restart_dispatch_confirmed": (
                            restart_dispatch_confirmed
                        ),
                        "redispatch_performed": False,
                    },
                }
        try:
            identity = await self.read_home_assistant_identity()
            provider = await self.provider.probe("restart_home_assistant")
        except (LifecycleGatewayError, OperationalLifecycleProviderError) as exc:
            return {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "failure_category": getattr(
                        exc, "category", "provider_unavailable"
                    ),
                    "redispatch_performed": False,
                },
            }
        validation = await self.configuration_validation()
        runtime = self.runtime_snapshot()
        expected_identity = baseline.get("home_assistant")
        mismatch: list[str] = []
        if not isinstance(expected_identity, dict) or any(
            identity.get(field) != expected_identity.get(field)
            for field in ("location_name", "version")
        ):
            mismatch.append("home_assistant_identity")
        if validation.get("status") != "valid":
            mismatch.append("configuration_validation")
        if not expected_disruption_observed:
            mismatch.append("restart_evidence")
        expected_runtime = baseline.get("runtime")
        if not isinstance(expected_runtime, dict) or any(
            runtime.get(field) != expected_runtime.get(field)
            for field in (
                "server_version",
                "build_sha",
                "registered_tool_count",
                "engineering_tool_count",
                "delegated_tool_count",
            )
        ):
            mismatch.append("engineering_runtime")
        if provider.server_version != (
            baseline.get("runtime", {}).get("upstream_version")
        ):
            mismatch.append("upstream_identity")
        runtime_checks = {
            "governance_storage_status": runtime.get(
                "governance_storage_status"
            ),
            "audit_storage_status": runtime.get("audit_storage_status"),
            "dependency_index_state": runtime.get("dependency_index_state"),
            "dependency_prewarm_state": runtime.get(
                "dependency_prewarm_state"
            ),
            "governance_plan_count": runtime.get(
                "governance_plan_count"
            ),
            "audit_write_failures": runtime.get(
                "audit_write_failures"
            ),
            "upstream_admission_status": runtime.get(
                "upstream_admission_status"
            ),
            "upstream_catalog_fingerprint": runtime.get(
                "upstream_catalog_fingerprint"
            ),
            "fallback_count": runtime.get("fallback_count"),
        }
        if runtime_checks["governance_storage_status"] != "healthy":
            mismatch.append("governance_storage")
        if runtime_checks["audit_storage_status"] != "healthy":
            mismatch.append("audit_storage")
        planned_runtime = baseline.get("runtime", {})
        if (
            not isinstance(runtime_checks["governance_plan_count"], int)
            or runtime_checks["governance_plan_count"]
            < int(planned_runtime.get("governance_plan_count") or 0)
        ):
            mismatch.append("governance_persistence")
        if runtime_checks["audit_write_failures"] != 0:
            mismatch.append("audit_continuity")
        if runtime_checks["upstream_admission_status"] != "admitted_exact":
            mismatch.append("upstream_admission")
        if runtime_checks["upstream_catalog_fingerprint"] != (
            planned_runtime.get("upstream_catalog_fingerprint")
        ):
            mismatch.append("upstream_catalog")
        if (
            runtime_checks["dependency_index_state"]
            not in {
                "valid",
                "stale_refreshing",
                "stale_available",
                "refresh_failed_stale_available",
            }
            or runtime_checks["dependency_prewarm_state"] != "complete"
        ):
            mismatch.append("dependency_index_recovery")
        if runtime_checks["fallback_count"] not in {0, None}:
            mismatch.append("fallback")
        return {
            "status": "verified" if not mismatch else "pending",
            "mismatch_fields": mismatch,
            "evidence": {
                "home_assistant": identity,
                "configuration_validation": validation,
                "engineering_runtime": runtime,
                "upstream": provider.as_dict(),
                "restart_dispatch_confirmed": (
                    restart_dispatch_confirmed
                ),
                "expected_disruption_observed": expected_disruption_observed,
                "runtime_checks": runtime_checks,
                "redispatch_performed": False,
            },
        }

    async def _observe_home_assistant_disruption(self) -> bool:
        """Boundedly require a real disconnect before restart verification."""

        for attempt in range(RESTART_DISRUPTION_PROBE_ATTEMPTS):
            try:
                await self.read_home_assistant_identity()
            except LifecycleGatewayError as exc:
                if exc.category in {
                    "provider_timeout",
                    "provider_unavailable",
                }:
                    return True
                raise
            if attempt + 1 < RESTART_DISRUPTION_PROBE_ATTEMPTS:
                await asyncio.sleep(
                    RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS
                )
        return False

    async def read_services(self) -> dict[str, Any]:
        try:
            raw = await self.websocket.command({"type": "get_services"})
        except AuthorizationError:
            raise LifecycleGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise LifecycleGatewayError("provider_timeout") from None
        except HomeAssistantUnavailableError:
            raise LifecycleGatewayError("provider_unavailable") from None
        except HomeAssistantApiError:
            raise LifecycleGatewayError("provider_error") from None
        if not isinstance(raw, dict) or len(raw) > 2_000:
            raise LifecycleGatewayError("invalid_response")
        return raw

    async def read_domain(self, target: str) -> dict[str, Any]:
        try:
            raw = await self.websocket.command({"type": "get_states"})
        except AuthorizationError:
            raise LifecycleGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise LifecycleGatewayError("provider_timeout") from None
        except HomeAssistantUnavailableError:
            raise LifecycleGatewayError("provider_unavailable") from None
        except HomeAssistantApiError:
            raise LifecycleGatewayError("provider_error") from None
        if not isinstance(raw, list) or len(raw) > MAX_STATES:
            raise LifecycleGatewayError("invalid_response")
        prefix = f"{target}."
        matching = [
            item
            for item in raw
            if isinstance(item, dict)
            and isinstance(item.get("entity_id"), str)
            and item["entity_id"].startswith(prefix)
        ]
        return {
            "domain": target,
            "state_inventory_readable": True,
            "matching_entity_count": len(matching),
        }

    async def read_home_assistant_identity(self) -> dict[str, Any]:
        try:
            raw = await self.rest.request("GET", "/config")
        except AuthorizationError:
            raise LifecycleGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise LifecycleGatewayError("provider_timeout") from None
        except HomeAssistantUnavailableError:
            raise LifecycleGatewayError("provider_unavailable") from None
        except HomeAssistantApiError:
            raise LifecycleGatewayError("provider_error") from None
        if not isinstance(raw, dict):
            raise LifecycleGatewayError("invalid_response")
        location_name = _safe_identity(raw.get("location_name"))
        version = _safe_identity(raw.get("version"))
        if location_name is None or version is None:
            raise LifecycleGatewayError("invalid_response")
        return {
            "location_name": location_name,
            "version": version,
            "connected": True,
        }

    def health_snapshot(self) -> dict[str, Any]:
        return self.provider.health_snapshot()


def _runtime_identity_available(runtime: Any) -> bool:
    if not isinstance(runtime, dict):
        return False
    return (
        isinstance(runtime.get("server_version"), str)
        and bool(runtime.get("server_version"))
        and isinstance(runtime.get("build_sha"), str)
        and bool(runtime.get("build_sha"))
        and all(
            isinstance(runtime.get(field), int)
            for field in (
                "registered_tool_count",
                "engineering_tool_count",
                "delegated_tool_count",
            )
        )
    )


def _engineering_runtime_ready(
    planned: Any, current: Any
) -> bool:
    if not isinstance(planned, dict) or not _runtime_identity_available(
        current
    ):
        return False
    identity_fields = (
        "server_version",
        "build_sha",
        "registered_tool_count",
        "engineering_tool_count",
        "delegated_tool_count",
    )
    if not all(
        current.get(field) == planned.get(field)
        for field in identity_fields
    ):
        return False
    planned_count = planned.get("governance_plan_count")
    current_count = current.get("governance_plan_count")
    return (
        isinstance(planned_count, int)
        and isinstance(current_count, int)
        and current_count >= planned_count
        and current.get("governance_storage_status") == "healthy"
        and current.get("audit_storage_status") == "healthy"
        and current.get("audit_write_failures") == 0
        and current.get("fallback_count") == 0
    )


def _upstream_readmission_matches(
    planned_provider: dict[str, Any],
    admitted_provider: dict[str, Any],
    runtime: Any,
) -> bool:
    if not isinstance(runtime, dict):
        return False
    return (
        _provider_contract_matches(
            admitted_provider, planned_provider
        )
        and runtime.get("upstream_version")
        == planned_provider.get("server_version")
        and runtime.get("upstream_protocol")
        == planned_provider.get("protocol_version")
        and runtime.get("upstream_catalog_fingerprint")
        == planned_provider.get("catalog_fingerprint")
        and runtime.get("upstream_admission_status")
        == "admitted_exact"
        and runtime.get("fallback_count") == 0
    )


def _service_available(
    services: dict[str, Any], domain: str, service: str
) -> bool:
    domain_value = services.get(domain)
    if not isinstance(domain_value, dict):
        return False
    service_value = domain_value.get(service)
    return isinstance(service_value, dict)


def _safe_identity(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and _SAFE_IDENTITY.fullmatch(value)
        else None
    )


def _addon_identity(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": value.get("slug"),
        "name": value.get("name"),
        "version": value.get("version"),
        "state": value.get("state"),
    }


def _addon_target_class(
    slug: str,
    addon: dict[str, Any],
    self_identity: SupervisorSelfAddonIdentity,
    upstream_identity: Any,
) -> str:
    if (
        not isinstance(self_identity, SupervisorSelfAddonIdentity)
        or addon.get("slug") != slug
    ):
        raise LifecycleGatewayError("self_addon_identity_unavailable")
    if slug == self_identity.slug:
        if (
            addon.get("name") != self_identity.name
            or addon.get("version") != self_identity.version
            or (
                self_identity.repository is not None
                and addon.get("repository") is not None
                and addon.get("repository") != self_identity.repository
            )
        ):
            raise LifecycleGatewayError(
                "self_addon_identity_unavailable"
            )
        return "engineering_addon"
    if (
        addon.get("name") == self_identity.name
        and addon.get("version") == self_identity.version
        and (
            self_identity.repository is None
            or addon.get("repository") is None
            or addon.get("repository") == self_identity.repository
        )
    ):
        raise LifecycleGatewayError("self_addon_identity_unavailable")
    upstream = _upstream_addon_binding(upstream_identity)
    if slug == upstream["slug"]:
        if (
            addon.get("name") != upstream["name"]
            or addon.get("version") != upstream["installed_version"]
            or (
                upstream.get("repository") is not None
                and addon.get("repository") is not None
                and addon.get("repository") != upstream["repository"]
            )
        ):
            raise LifecycleGatewayError(
                "upstream_addon_identity_unavailable"
            )
        return "upstream_ha_mcp_addon"
    return "other_addon"


def _provider_contract_matches(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and all(
            left.get(field) == right.get(field)
            for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
        )
    )


def _provider_identity_matches(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and all(
            left.get(field) == right.get(field)
            for field in UPSTREAM_PROVIDER_IDENTITY_FIELDS
        )
    )


def _normalized_upstream_addon_binding(
    value: Any,
    *,
    provider_contract: dict[str, Any] | None = None,
    require_provider_contract: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("status") != "bound":
        raise LifecycleGatewayError(
            "upstream_addon_identity_unavailable"
        )
    slug = value.get("slug")
    name = _safe_identity(value.get("name"))
    installed_version = _safe_identity(
        value.get("installed_version")
    )
    repository = value.get("repository")
    endpoint_host = value.get("endpoint_host")
    identity_source = value.get("identity_source")
    inventory_arguments = value.get("inventory_arguments")
    admission_evidence = value.get("admission_evidence")
    observed_provider_contract = (
        provider_contract
        if provider_contract is not None
        else value.get("provider_contract")
    )
    if (
        not isinstance(slug, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", slug)
        or name is None
        or installed_version is None
        or (
            repository is not None
            and _safe_identity(repository) is None
        )
        or not isinstance(endpoint_host, str)
        or endpoint_host != slug.replace("_", "-")
        or identity_source
        != "configured_endpoint_supervisor_dns_and_reviewed_admission"
        or inventory_arguments
        != {"source": "installed", "include_stats": False}
        or not isinstance(admission_evidence, dict)
        or any(
            field not in admission_evidence
            for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
        )
        or (
            require_provider_contract
            and (
                not isinstance(observed_provider_contract, dict)
                or any(
                    field not in observed_provider_contract
                    for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
                )
            )
        )
    ):
        raise LifecycleGatewayError(
            "upstream_addon_identity_unavailable"
        )
    normalized = {
        "status": "bound",
        "slug": slug,
        "name": name,
        "installed_version": installed_version,
        "repository": repository,
        "endpoint_host": endpoint_host,
        "identity_source": identity_source,
        "inventory_arguments": dict(inventory_arguments),
        "admission_evidence": {
            key: admission_evidence.get(key)
            for key in (
                *UPSTREAM_PROVIDER_CONTRACT_FIELDS,
                "reviewed_source_commit",
                "reviewed_image_index_digest",
                "runtime_artifact_observed",
                "fallback",
                "fallback_occurred",
            )
        },
    }
    if isinstance(observed_provider_contract, dict):
        normalized["provider_contract"] = dict(
            observed_provider_contract
        )
    return normalized


def _upstream_addon_binding(
    value: Any,
    *,
    provider_contract: dict[str, Any] | None = None,
    require_provider_contract: bool = False,
) -> dict[str, Any]:
    return _normalized_upstream_addon_binding(
        value,
        provider_contract=provider_contract,
        require_provider_contract=require_provider_contract,
    )


def _upstream_binding_failure_is_conclusive(
    planned: dict[str, Any],
    fresh: dict[str, Any],
) -> bool:
    status = fresh.get("status")
    if status in {"ambiguous", "conflicting"}:
        return True
    endpoint_host = fresh.get("endpoint_host")
    return (
        isinstance(endpoint_host, str)
        and endpoint_host != planned.get("endpoint_host")
    )


def _upstream_binding_verification_result(
    status: str,
    *,
    identity_status: str,
    failure_category: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "identity_status": identity_status,
        "redispatch_performed": False,
    }
    if failure_category is not None:
        evidence["failure_category"] = failure_category
    return {
        "status": status,
        "mismatch_fields": ["upstream_addon_identity"],
        "evidence": evidence,
    }
