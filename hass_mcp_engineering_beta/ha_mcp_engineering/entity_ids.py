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


def validate_entity_id(entity_id: Any) -> str:
    if (
        not isinstance(entity_id, str)
        or len(entity_id) > MAX_ENTITY_ID_LENGTH
        or not ENTITY_ID_PATTERN.fullmatch(entity_id)
    ):
        raise ValueError("entity_id is invalid")
    return entity_id


__all__ = ["ENTITY_ID_PATTERN", "MAX_ENTITY_ID_LENGTH", "validate_entity_id"]
