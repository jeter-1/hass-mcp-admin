"""Transport-independent evidence provider protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import EvidenceRequest, ProviderCapability, ProviderResult


class EngineeringEvidenceProvider(ABC):
    provider_id: str
    capabilities: frozenset[ProviderCapability]

    @property
    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        raise NotImplementedError
