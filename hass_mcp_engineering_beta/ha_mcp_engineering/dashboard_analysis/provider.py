"""Pure native-inventory projection; dashboard rule collection is not wired yet."""

from ..sanitization import sanitize_untrusted_data
from . import contracts as c
from .models import Inventory


def safe_entity(value, known_secrets=()):
    if type(value) is not str or len(value) > 256 or not c.ENTITY.fullmatch(value):
        return None
    cleaned = sanitize_untrusted_data(value, known_secrets=known_secrets, max_string=256)
    return value if cleaned.value == value and not cleaned.failed_closed else None


def project_inventory(kind, value, *, known_secrets=()):
    """Retain valid positives while incomplete identity evidence forbids absence."""
    if kind not in {"states", "registry"}:
        raise c.AnalysisError("invalid_arguments")
    if type(value) is not list:
        return Inventory(kind, (), False, 0, 0, 0, 0, "malformed_response")
    records, seen = {}, set()
    invalid = duplicate = 0
    examined = min(len(value), c.INVENTORY_ENTRIES)
    for row in value[:examined]:
        entity = safe_entity(row.get("entity_id"), known_secrets) if type(row) is dict else None
        if not entity:
            invalid += 1
            continue
        if entity in seen:
            duplicate += 1
            records.pop(entity, None)
            continue
        seen.add(entity)
        if kind == "states":
            state = row.get("state")
            if type(state) is not str:
                invalid += 1
                continue
            category = ("unavailable" if state == "unavailable" else
                        "state_unknown" if state == "unknown" else "present")
        else:
            if "disabled_by" not in row or (row["disabled_by"] is not None
                                             and type(row["disabled_by"]) is not str):
                invalid += 1
                continue
            category = "registry_only" if row["disabled_by"] is None else "registry_disabled"
        records[entity] = category
    omitted = len(value) - len(records)
    complete = not invalid and not duplicate and examined == len(value)
    return Inventory(kind, tuple(sorted(records.items())), complete, examined, omitted,
                     invalid, duplicate)


def failed_inventory(kind, reason):
    if kind not in {"states", "registry"}:
        raise c.AnalysisError("invalid_arguments")
    failure = c.AnalysisError(reason)
    if failure.reason in {"access_denied", "authority_unavailable", "authority_drift",
                          "identity_mismatch", "hash_mismatch", "source_rejected"}:
        raise failure
    return Inventory(kind, (), False, 0, 0, 0, 0, failure.reason)


def availability(entity, states, registry):
    if states.kind != "states" or registry.kind != "registry":
        raise c.AnalysisError("identity_mismatch")
    state_map, registry_map = dict(states.records), dict(registry.records)
    if entity in state_map:
        return state_map[entity]
    if entity in registry_map:
        return registry_map[entity]
    if states.complete and registry.complete:
        return "absent_from_observed_inventories"
    return "unassessed"
