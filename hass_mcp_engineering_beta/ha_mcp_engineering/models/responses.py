"""Response-model boundary for future structured envelopes."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any

MAX_CHARS = 60_000


@dataclass
class EngineeringResponse:
    ok: bool
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def dump_json(data: Any, limit: int = MAX_CHARS) -> str:
    output = json.dumps(data, indent=2, default=str)
    if len(output) > limit:
        return output[:limit] + f"\n... [truncated at {limit} chars — narrow the query]"
    return output
