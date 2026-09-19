"""Persisted exact Core identity for registry-selected ordinary operations."""
import re

from .profiles import CORE_TYPED_OPERATION_PROFILES


def operation_profile(family):
    return next(p for p in CORE_TYPED_OPERATION_PROFILES
                if p.capability_id == "core.typed_" + family + "_operation")


def checked_binding(value, family):
    profile = operation_profile(family)
    if (not isinstance(value, dict)
            or set(value) != {"version", "contract_fingerprint"}
            or not isinstance(value["version"], str)
            or re.fullmatch(r"(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})",
                            value["version"]) is None
            or value["contract_fingerprint"] != profile.contract_fingerprint):
        raise ValueError("typed_core_binding_invalid")
    return dict(value)
