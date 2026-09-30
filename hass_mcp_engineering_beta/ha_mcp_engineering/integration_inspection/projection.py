"""Positive field selection before retention, hashing, evidence or pagination."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..sanitization import sanitize_untrusted_data
from . import contracts as c
from .models import Evidence, Fact, Gap, Ref


@dataclass
class Budget:
    nodes: int = 0
    edges: int = 0
    exhausted: bool = False

    def charge(self, depth=0) -> bool:
        if self.nodes >= c.MAX_NODES:
            self.exhausted = True
            return False
        self.nodes += 1
        if depth > c.MAX_DEPTH:
            self.exhausted = True
            return False
        return True


@dataclass
class Projection:
    kind: c.ReadKind
    source_id: str
    value: Any
    entries: dict[str, Evidence] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)
    gap_count: int = 0
    observed: int | None = None
    retained: int = 0
    omitted: int | None = 0
    truncated: bool = False
    available: bool = True

    def gap(self, reason, pointer=None):
        self.gap_count += 1
        gap = Gap(reason=reason, source_id=self.source_id, pointer=pointer)
        if len(self.gaps) < c.MAX_GAPS and gap not in self.gaps:
            self.gaps.append(gap)
        if reason in {"structural_budget", "record_limit", "response_bytes"}:
            self.truncated = True

    def fact(self, pointer):
        item = self.entries.get(pointer)
        if item is None:
            return Fact[Any](status="unavailable", value=None, evidence=[], reason="source_unavailable" if not self.available else "invalid_field")
        reason = {"redacted": "redacted_by_policy", "unsupported": "invalid_field", "unavailable": "source_unavailable"}.get(item.status)
        return Fact[Any](status=item.status, value=item.value, evidence=[Ref(source_id=self.source_id, pointer=pointer)], reason=reason)


class Projector:
    def __init__(self, *, known_secrets=(), budget=None):
        self.known_secrets = known_secrets
        self.budget = budget or Budget()

    def identifier(self, value, *, entity=False):
        valid = c.valid_entity(value) if entity else type(value) is str and c.OPAQUE_PATTERN.fullmatch(value) is not None
        if not valid:
            return None
        sanitized = sanitize_untrusted_data(value, known_secrets=self.known_secrets, max_string=128)
        return value if sanitized.value == value and not sanitized.failed_closed else None

    def project(self, kind: c.ReadKind, raw, source_id: str) -> Projection:
        steps = self._project_steps(kind, raw, source_id)
        while True:
            try:
                next(steps)
            except StopIteration as completed:
                return completed.value

    async def project_async(self, kind: c.ReadKind, raw, source_id: str) -> Projection:
        # The same projection and structural accounting, with bounded batches.
        # Cancellation unwinds this caller; no raw data or unfinished worker
        # escapes into a background task or outlives the collection deadline.
        steps = self._project_steps(kind, raw, source_id)
        while True:
            try:
                next(steps)
            except StopIteration as completed:
                return completed.value
            await asyncio.sleep(0)

    def _project_steps(self, kind: c.ReadKind, raw, source_id: str):
        p = Projection(kind, source_id, None)
        if not self.budget.charge():
            p.gap("structural_budget")
            p.available = False
            return p

        def scalar(parent, key, pointer, expected, *, nullable=False, depth=2):
            if type(parent) is not dict or key not in parent:
                p.gap("invalid_field", pointer)
                return False, None
            if not self.budget.charge(depth):
                p.gap("structural_budget")
                return False, None
            value = parent[key]
            valid = (value is None and nullable)
            if value is not None:
                if expected == "bool":
                    valid = type(value) is bool
                elif expected == "integer":
                    valid = type(value) is int and 0 <= value <= 2147483647
                elif expected == "id":
                    valid = type(value) is str and c.OPAQUE_PATTERN.fullmatch(value) is not None
                elif expected == "entity":
                    valid = c.valid_entity(value)
                elif isinstance(expected, tuple):
                    valid = type(value) is str and value in expected
            status = "observed" if valid else "unsupported"
            if valid and type(value) is str and sanitize_untrusted_data(
                value, known_secrets=self.known_secrets, max_string=128
            ).value != value:
                status = "redacted"
                valid = False
            safe = value if valid else None
            p.entries[pointer] = Evidence(source_id=source_id, pointer=pointer, status=status, value=safe)
            if not valid:
                p.gap("redacted_by_policy" if status == "redacted" else "invalid_field", pointer)
            return True, safe

        def fields(row, specification, prefix):
            result = {}
            for key, expected in specification:
                ok, value = scalar(row, key, prefix + "/" + key, expected)
                if ok:
                    result[key] = value
            return result

        def identifier_list(row, key, prefix, *, modes=False):
            pointer = prefix + "/" + key
            if type(row) is not dict or key not in row:
                p.gap("invalid_field", pointer)
                return None
            if not self.budget.charge(3):
                p.gap("structural_budget")
                return None
            values = row[key]
            result = []
            valid = type(values) is list
            if valid:
                for index, value in enumerate(values):
                    if index % 16 == 0:
                        yield
                    if not self.budget.charge(4):
                        p.gap("structural_budget")
                        valid = False
                        break
                    if modes and len(result) >= len(c.MODES):
                        valid = False
                        break
                    if not modes:
                        if self.budget.edges >= c.MAX_EDGES:
                            p.gap("record_limit")
                            valid = False
                            break
                        self.budget.edges += 1
                    item_ok = type(value) is str and value in c.MODES if modes else self.identifier(value, entity=True) is not None
                    if not item_ok or value in result:
                        valid = False
                        break
                    result.append(value)
            # Do not certify a shortened selector as its complete source value.
            safe = result if valid else None
            p.entries[pointer] = Evidence(source_id=source_id, pointer=pointer, status="observed" if valid else "unsupported", value=safe)
            if not valid:
                p.gap("unknown_mode" if modes else "invalid_field", pointer)
            return safe

        if kind in (c.ReadKind.MANIFEST, c.ReadKind.GENERAL):
            if type(raw) is not dict:
                p.gap("malformed_response")
                p.available = False
                return p
            p.observed = p.retained = 1
            if kind is c.ReadKind.MANIFEST:
                p.value = fields(raw, (("domain", ("alarmo",)), ("version", "id")), "")
            else:
                p.value = fields(raw, tuple((key, "bool") for key in c.GENERAL_FIELDS), "")
                master = raw.get("master")
                ok, value = scalar(master, "enabled", "/master/enabled", "bool", depth=3)
                if ok:
                    p.value["master"] = {"enabled": value}
            return p

        if kind in (c.ReadKind.CONFIG_ENTRIES, c.ReadKind.ALARM_ENTITIES):
            if type(raw) is not list:
                p.gap("malformed_response")
                p.available = False
                return p
            p.value, p.observed = [], len(raw)
            bound = 2 if kind is c.ReadKind.CONFIG_ENTRIES else c.MAX_AREAS + 1
            for index, row in enumerate(raw):
                if index % 8 == 0:
                    yield
                if not self.budget.charge(1):
                    p.gap("structural_budget")
                    break
                if index >= bound:
                    p.gap("record_limit")
                    break
                if type(row) is not dict:
                    p.value.append(None)
                    p.gap("malformed_response")
                    continue
                prefix = "/" + str(index)
                if kind is c.ReadKind.CONFIG_ENTRIES:
                    safe = fields(row, (("entry_id", "id"), ("domain", ("alarmo",)), ("state", ("loaded", "not_loaded", "setup_error", "setup_retry", "migration_error", "failed_unload", "setup_in_progress", "unload_in_progress"))), prefix)
                else:
                    safe = fields(row, (("entity_id", "entity"),), prefix)
                    # Exact int zero is master. False and string zero are not.
                    master = type(row.get("area_id")) is int and row["area_id"] == 0
                    ok, area = scalar(row, "area_id", prefix + "/area_id", "integer" if master else "id")
                    if ok:
                        safe["area_id"] = area
                p.value.append(safe)
                p.retained += 1
            p.omitted = p.observed - p.retained
            return p

        if type(raw) is not dict:
            p.gap("malformed_response")
            p.available = False
            return p
        p.value, p.observed = {}, len(raw)
        bound = {c.ReadKind.AREAS: c.MAX_AREAS, c.ReadKind.SENSORS: c.MAX_SENSORS,
                 c.ReadKind.SENSOR_GROUPS: c.MAX_GROUPS, c.ReadKind.ENTITY_REGISTRY: c.MAX_REGISTRY}[kind]
        for index, (key, row) in enumerate(raw.items()):
            if index % 8 == 0:
                yield
            if not self.budget.charge(1):
                p.gap("structural_budget")
                break
            if index >= bound:
                p.gap("record_limit")
                break
            entity = kind in (c.ReadKind.SENSORS, c.ReadKind.ENTITY_REGISTRY)
            if self.identifier(key, entity=entity) is None:
                p.gap("redacted_by_policy" if type(key) is str and len(key) <= 128 else "invalid_field")
                continue
            prefix = "/" + key  # validated grammar excludes JSON Pointer separators
            if kind is c.ReadKind.ENTITY_REGISTRY and row is None:
                p.value[key] = None
                p.entries[prefix] = Evidence(source_id=source_id, pointer=prefix, status="observed", value=None)
                p.retained += 1
                continue
            if type(row) is not dict:
                p.gap("malformed_response", prefix)
                continue
            if kind is c.ReadKind.AREAS:
                safe = fields(row, (("area_id", "id"),), prefix)
                modes = row.get("modes")
                safe["modes"] = {}
                if type(modes) is not dict:
                    p.gap("invalid_field", prefix + "/modes")
                else:
                    if len(modes) != len(c.MODES):
                        p.gap("unknown_mode", prefix + "/modes")
                    for mode in c.MODES:
                        pointer = prefix + "/modes/" + mode
                        mode_row = modes.get(mode)
                        data = fields(mode_row, (("enabled", "bool"),), pointer)
                        for duration in ("entry_time", "exit_time", "trigger_time"):
                            ok, value = scalar(mode_row, duration, pointer + "/" + duration, "integer", nullable=True, depth=4)
                            if ok:
                                data[duration] = value
                        safe["modes"][mode] = data
            elif kind is c.ReadKind.SENSORS:
                safe = fields(row, (("entity_id", "entity"), ("area", "id"), ("type", c.SENSOR_TYPES),
                                    *((key, "bool") for key in c.SENSOR_FLAGS)), prefix)
                for field_name in ("modes", "auto_bypass_modes"):
                    value = yield from identifier_list(row, field_name, prefix, modes=True)
                    if field_name in row:
                        safe[field_name] = value
                ok, group = scalar(row, "group", prefix + "/group", "id", nullable=True)
                if ok:
                    safe["group"] = group
            elif kind is c.ReadKind.SENSOR_GROUPS:
                safe = fields(row, (("group_id", "id"), ("timeout", "integer"), ("event_count", "integer")), prefix)
                value = yield from identifier_list(row, "entities", prefix)
                if "entities" in row:
                    safe["entities"] = value
            else:
                safe = fields(row, (("entity_id", "entity"), ("id", "id")), prefix)
                ok, disabled = scalar(row, "disabled_by", prefix + "/disabled_by", ("user", "integration", "config_entry", "device"), nullable=True)
                if ok:
                    safe["disabled_by"] = disabled
            identity_key = {c.ReadKind.AREAS: "area_id", c.ReadKind.SENSORS: "entity_id",
                            c.ReadKind.SENSOR_GROUPS: "group_id", c.ReadKind.ENTITY_REGISTRY: "entity_id"}[kind]
            if safe.get(identity_key) != key:
                p.gap("invalid_field", prefix)
                # Conflicting/removed identity must not be reconstructed from the map key.
                for pointer in tuple(p.entries):
                    if pointer.startswith(prefix + "/"):
                        del p.entries[pointer]
                continue
            p.value[key] = safe
            p.retained += 1
        p.omitted = p.observed - p.retained
        return p
