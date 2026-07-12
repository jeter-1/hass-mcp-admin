"""Explicit direct-Home-Assistant provider boundary for native exceptions."""

from __future__ import annotations

import inspect
import time
from typing import Any, Awaitable, Callable

from .base import EngineeringEvidenceProvider
from .models import (
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)


Handler = Callable[[EvidenceRequest], ProviderResult | Awaitable[ProviderResult]]


class DirectHaApiProvider(EngineeringEvidenceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset(
        {
            ProviderCapability.AUTOMATION_CONFIG,
            ProviderCapability.AUTOMATION_TRACE,
            ProviderCapability.BLUEPRINT_SOURCE,
            ProviderCapability.CONFIG_VALIDATION,
            ProviderCapability.GOVERNED_APPLY,
            ProviderCapability.EXACT_VERIFICATION,
            ProviderCapability.GOVERNED_ROLLBACK,
            ProviderCapability.CURRENT_ENTITY_STATE,
            ProviderCapability.TEMPLATE_RENDER,
            ProviderCapability.HISTORY_READ,
            ProviderCapability.LOGBOOK_READ,
            ProviderCapability.ERROR_LOG_READ,
            ProviderCapability.AUTOMATION_LIST,
            ProviderCapability.DEVICE_REGISTRY_READ,
            ProviderCapability.ENTITY_REGISTRY_READ,
            ProviderCapability.BLUEPRINT_LIST,
            ProviderCapability.LEGACY_AUTOMATION_WRITE,
        }
    )

    def __init__(self, handlers: dict[ProviderCapability, Handler] | None = None):
        self.handlers = dict(handlers or {})

    @property
    def available(self) -> bool:
        return bool(self.handlers)

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        handler = self.handlers.get(request.capability)
        if handler is None:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.UNAVAILABLE,
                failure=ProviderError(
                    ProviderFailureCategory.UNSUPPORTED,
                    "No direct Home Assistant handler is registered for this capability.",
                ),
                coverage=ProviderCoverage(1, 0, (self.provider_id,)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        try:
            value: Any = handler(request)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, ProviderResult):
                raise TypeError("provider handler returned an invalid response")
            value.provider_id = self.provider_id
            value.timing_ms = (time.perf_counter() - started) * 1000
            return value
        except TimeoutError:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.TIMEOUT, "The provider timed out.", True),
                coverage=ProviderCoverage(1, 0, (self.provider_id,)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.UPSTREAM_ERROR, "The provider failed."),
                coverage=ProviderCoverage(1, 0, (self.provider_id,)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
