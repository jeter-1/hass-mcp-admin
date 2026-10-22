"""Entity-to-definition matching for the HAMCP-132 report.

Order, strongest first:

1. ``exact_config_entry``: the registry row names a config entry of one of
   the twelve integrations. The definition is UI-defined and Core has no
   read-only source of its options, so it is
   ``definition_not_readable_without_flow`` (owner decision (i)).
2. ``exact_unique_id``: the registry row's (domain, platform, unique_id)
   equals the unique_id Core derives from a YAML definition. A unique_id
   YAML 1.1 could read differently (``unique_id_parser_dependent``, M2) is
   never exact: one candidate is ``heuristic`` with ``medium`` confidence
   and the reason ``unique_id_parser_dependent``.
3. ``heuristic``: no registry row, and Core's naming rules applied to a
   definition's static name, default name or key give this entity ID. The
   confidence is stated: ``high`` for one candidate whose platform
   ``entity/source`` confirms with every YAML source complete; ``medium``
   when the platform is unconfirmed or a source is incomplete; ``low`` when
   the ID matched only after removing a collision suffix (``_2``, ``_3``).
   More than one candidate is ``ambiguous`` and none is chosen.

The reverse direction (definition to entity) applies the same rules to the
registry rows its unique_id names (M1): a unique_id two read definitions
share is ambiguous in both directions.

A legacy platform entry (``unsupported_legacy_platform``) creates no entity
in this Core and is never matched; it is only a diagnosis signal.
"""

from __future__ import annotations

import re
from typing import Any

from . import schemas as s
from .naming import core_slugify

_SUFFIX = re.compile(r"(.+)_([2-9]|[1-9][0-9]{1,3})\Z")
MAX_CANDIDATES = 5


class Matcher:
    def __init__(self, definitions: list[dict], *, registry: dict[str, dict] | None,
                 platforms: dict[str, str | None] | None, states: dict[str, str] | None,
                 yaml_complete: bool, ui_domains: dict[str, str]):
        self.definitions = definitions
        self.registry = registry
        self.platforms = platforms
        self.states = states
        self.yaml_complete = yaml_complete
        # config entry ID -> domain, for entries of the twelve integrations.
        self.ui_domains = ui_domains
        self.by_uid: dict[tuple[str, str, str], list[str]] = {}
        self.by_dependent_uid: dict[tuple[str, str, str], list[str]] = {}
        self.by_derived: dict[str, list[str]] = {}
        self.legacy: dict[str, list[str]] = {}
        for definition in definitions:
            if definition["form"] == "legacy_platform":
                continue
            platform = s.REGISTRY_PLATFORM.get(definition["integration"])
            for expected in definition["expected_entities"]:
                if expected["unique_id"] is not None and platform:
                    if expected.get("unique_id_parser_dependent"):
                        for reading in expected["unique_id_readings"]:
                            key = (expected["entity_domain"], platform, reading)
                            self.by_dependent_uid.setdefault(key, []).append(definition["definition_id"])
                        continue
                    key = (expected["entity_domain"], platform, expected["unique_id"])
                    self.by_uid.setdefault(key, []).append(definition["definition_id"])
                elif expected["derived_entity_id"]:
                    self.by_derived.setdefault(expected["derived_entity_id"], []).append(
                        definition["definition_id"])
        self.integration = {d["definition_id"]: d["integration"] for d in definitions}

    def legacy_candidates(self, entity_id: str) -> list[str]:
        """Legacy definitions whose static name would give this entity ID."""

        domain, object_id = entity_id.split(".", 1)
        found = []
        for definition in self.definitions:
            if definition["form"] != "legacy_platform" or definition["entity_domain"] != domain:
                continue
            name = definition["fields"].get("name")
            if type(name) is str and not any(m in name for m in s.TEMPLATE_MARKERS) \
                    and core_slugify(name.lower()) == object_id:
                found.append(definition["definition_id"])
        return found[:MAX_CANDIDATES]

    def _heuristic(self, entity_id: str, candidates: list[str], suffix: bool) -> dict:
        platform = self.platforms.get(entity_id) if self.platforms is not None else None
        if platform is not None:
            candidates = [item for item in candidates
                          if s.REGISTRY_PLATFORM.get(self.integration[item]) == platform]
        if not candidates:
            return {}
        if len(candidates) > 1:
            return {"method": "ambiguous", "confidence": None,
                    "definition_ids": candidates[:MAX_CANDIDATES],
                    "reason": "several_definitions_derive_this_entity_id"}
        if suffix:
            confidence = "low"
        elif platform is not None and self.yaml_complete and self.registry is not None:
            confidence = "high"
        else:
            confidence = "medium"
        return {"method": "heuristic", "confidence": confidence, "definition_ids": candidates,
                "reason": None if confidence == "high" else (
                    "collision_suffix_removed" if suffix else
                    "platform_unconfirmed" if platform is None else "sources_incomplete")}

    def entity(self, entity_id: str) -> dict:
        """The match for one entity ID."""

        row = self.registry.get(entity_id) if self.registry is not None else None
        if row is not None:
            entry = row.get("config_entry_id")
            if entry is not None and entry in self.ui_domains:
                return {"method": "exact_config_entry", "confidence": "exact", "definition_ids": [],
                        "config_entry_id": entry, "readability": "definition_not_readable_without_flow",
                        "reason": None}
            if row["platform"] not in s.REGISTRY_PLATFORM:
                return {"method": "not_applicable", "confidence": None, "definition_ids": [],
                        "reason": "platform_outside_supported_integrations"}
            if entry is not None:
                # A config entry of a supported platform whose entry was not
                # read (config_entries/get failed or was not listed).
                return {"method": "unmatched", "confidence": None, "definition_ids": [],
                        "config_entry_id": entry, "reason": "config_entry_not_observed"}
            key = (entity_id.split(".", 1)[0], row["platform"], row["unique_id"])
            found = self.by_uid.get(key, []) if type(row["unique_id"]) is str else []
            dependent = self.by_dependent_uid.get(key, []) if type(row["unique_id"]) is str else []
            if dependent:
                found = found + [item for item in dependent if item not in found]
                if len(found) == 1:
                    return {"method": "heuristic", "confidence": "medium", "definition_ids": found,
                            "reason": "unique_id_parser_dependent"}
            elif len(found) == 1:
                return {"method": "exact_unique_id", "confidence": "exact", "definition_ids": found,
                        "reason": None}
            if found:
                return {"method": "ambiguous", "confidence": None, "definition_ids": found[:MAX_CANDIDATES],
                        "reason": "several_definitions_share_this_unique_id"}
            # A uid-less YAML definition never creates a registry row, so no
            # name heuristic applies to a registered entity.
            return {"method": "unmatched", "confidence": None, "definition_ids": [],
                    "reason": "no_read_definition_has_this_unique_id"}
        # Without the registry an entity could be registered under a
        # unique_id; the name heuristic is then medium at most.
        match = self._heuristic(entity_id, self.by_derived.get(entity_id, []), False)
        if match:
            return match
        suffixed = _SUFFIX.fullmatch(entity_id)
        if suffixed:
            match = self._heuristic(entity_id, self.by_derived.get(suffixed.group(1), []), True)
            if match:
                return match
        return {"method": "unmatched", "confidence": None, "definition_ids": [],
                "reason": "no_definition_derives_this_entity_id"}

    def definition(self, definition: dict) -> list[dict]:
        """Matches for each entity a definition is expected to create."""

        if definition["form"] == "legacy_platform":
            return [{"entity_id": None, "method": "not_applicable",
                     "reason": "unsupported_legacy_platform"}]
        platform = s.REGISTRY_PLATFORM.get(definition["integration"])
        results = []
        for expected in definition["expected_entities"]:
            result: dict[str, Any] = {"entity_id": None, "method": "unmatched", "reason": None}
            if expected["unique_id"] is not None and platform and self.registry is not None:
                readings = expected.get("unique_id_readings") or [expected["unique_id"]]
                hits = [entity_id for entity_id, row in self.registry.items()
                        if entity_id.split(".", 1)[0] == expected["entity_domain"]
                        and row["platform"] == platform and row["unique_id"] in readings
                        and row.get("config_entry_id") is None]
                if len(hits) > 1:
                    result.update(method="ambiguous", confidence=None,
                                  reason="several_registry_entries_share_this_unique_id")
                elif hits:
                    # The forward rules decide (M1): a unique_id two read
                    # definitions share is ambiguous in both directions.
                    match = self.entity(hits[0])
                    if definition["definition_id"] in match.get("definition_ids", []):
                        result.update(entity_id=hits[0], method=match["method"],
                                      confidence=match["confidence"])
                        if match.get("reason"):
                            result["reason"] = match["reason"]
                    else:
                        result["reason"] = match.get("reason") or "no_registry_entry_with_this_unique_id"
                else:
                    result["reason"] = "no_registry_entry_with_this_unique_id"
            elif expected["derived_entity_id"]:
                derived = expected["derived_entity_id"]
                if self.states is not None and derived in self.states:
                    match = self.entity(derived)
                    if definition["definition_id"] in match.get("definition_ids", []):
                        result.update(entity_id=derived, method=match["method"],
                                      confidence=match["confidence"])
                        if match.get("reason"):
                            result["reason"] = match["reason"]
                    else:
                        result["reason"] = match.get("reason") or "derived_entity_id_owned_elsewhere"
                else:
                    result["reason"] = ("states_unavailable" if self.states is None
                                        else "derived_entity_id_not_in_states")
            else:
                result["reason"] = expected["derivation_limit"]
            results.append(result)
        return results
