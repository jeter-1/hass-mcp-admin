"""Serializable failure model reserved for v2 response envelopes."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ErrorModel:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
