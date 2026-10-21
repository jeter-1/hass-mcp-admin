"""The single entity_id syntax rule for governed entity metadata (HAMCP-069A-1).

Design §1.1: one string matching ``^[a-z0-9_]+\\.[a-z0-9_]+$`` of at most 255
characters.  Core admission (``ha_core_readmission.routes``) and the planner
(``governance.entity_metadata``) both call ``validate_entity_id``; neither
keeps its own copy of the rule.  This module imports nothing from the package
so that the admission boundary can use it without loading governance.
"""

from __future__ import annotations

import re
from typing import Any


ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
MAX_ENTITY_ID_LENGTH = 255

# HAMCP-132: Core's ``VALID_ENTITY_ID`` (homeassistant/core.py at 2026.10.1)
# for read commands that Core validates with ``cv.strict_entity_id``: slug
# domain and object ID, no leading, trailing or doubled underscore. ASCII
# digits only, no trailing newline and at most 255 characters, so it admits
# a strict subset of what Core accepts. It does not replace the governed
# metadata rule above.
_STRICT_SLUG = r"(?!_)[0-9a-z_]+(?<!_)"
STRICT_ENTITY_ID = re.compile(
    r"(?=.{1,255}\Z)(?!.+__)" + _STRICT_SLUG + r"\." + _STRICT_SLUG + r"\Z", re.ASCII
)


def validate_entity_id(entity_id: Any) -> str:
    if (
        not isinstance(entity_id, str)
        or len(entity_id) > MAX_ENTITY_ID_LENGTH
        or not ENTITY_ID_PATTERN.fullmatch(entity_id)
    ):
        raise ValueError("entity_id is invalid")
    return entity_id


__all__ = [
    "ENTITY_ID_PATTERN", "MAX_ENTITY_ID_LENGTH", "STRICT_ENTITY_ID", "validate_entity_id",
]
