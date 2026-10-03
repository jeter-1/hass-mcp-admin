"""Closed configured-behavior rules for frontend 20260826.7.

Source/line/test provenance is in docs/evidence/dashboard-analysis-source-review.json.
This is a configuration inspection, never a browser or service-eligibility test.
Only fixed structural keys are visited. Raw values never leave the worker.
"""

from . import contracts as c
from .provider import safe_entity

FRONTEND_COMMIT = "380e9b5a81ada29a1d187b4123c54fb3d6fbcc89"
CORE_VERSION = "2026.9.4"
TOGGLE_DOMAINS = frozenset({"fan", "input_boolean", "light", "switch", "group",
                            "automation", "humidifier", "valve"})
HELPERS = {"input_boolean": None, "input_number": "input_number.set_value",
           "input_text": "input_text.set_value", "input_select": "input_select.select_option",
           "input_datetime": "input_datetime.set_datetime", "input_button": "input_button.press"}
HELPER_TYPES = {"input_boolean": "toggle-entity", "input_number": "input-number-entity",
                "input_text": "input-text-entity", "input_select": "input-select-entity",
                "input_datetime": "input-datetime-entity", "input_button": "input-button-entity"}
ACTIONS = frozenset({"none", "more-info", "toggle", "perform-action", "call-service",
                     "navigate", "url", "assist", "fire-dom-event"})
SLOTS = ("tap_action", "hold_action", "double_tap_action")


def truthy(value):
    """JSON subset of JS truthiness (notably, empty objects/arrays are true)."""
    return value is not None and value is not False and value != "" and value != 0


def confirmation(value):
    if value is None or value is False:
        return "absent"
    return "present" if value is True or type(value) is dict else "unresolved"


class Scanner:
    def __init__(self, fingerprint, states, registry, *, core_version, known_secrets=()):
        if states.kind != "states" or registry.kind != "registry":
            raise c.AnalysisError("identity_mismatch")
        self.fingerprint = fingerprint
        self.states, self.registry = dict(states.records), dict(registry.records)
        self.inventories_complete = states.complete and registry.complete
        self.defaults = core_version == CORE_VERSION
        self.secrets = known_secrets
        self.items, self.unique, self.ids, self.entity_pointers = [], set(), set(), set()
        self.nodes = self.occurrences = self.bytes = 0
        self.truncated = self.partial = self.stopped = False

    def emit(self, kind, pointer, rule_id, **values):
        if self.stopped:
            return
        item = dict(kind=kind, pointer=pointer, rule_id=rule_id,
                    source_projection_exact=kind == "entity_reference", **values)
        item["id"] = self.item_id(item)
        if item["id"] in self.ids:
            return
        encoded = c.canonical(item)
        # Reserve one gap and the fixed header. Cost each record once; no
        # quadratic serialization or unpageable retained record.
        if (len(self.items) >= c.ITEMS - 1 or len(encoded) > 8000
                or self.bytes + len(encoded) > c.SNAPSHOT_BYTES - 16_384):
            self.truncated = self.partial = self.stopped = True
            return
        self.items.append(item)
        self.ids.add(item["id"])
        self.bytes += len(encoded)

    def item_id(self, item):
        # Location identity is stable across inventory availability changes.
        # Slots distinguish multiple controls inferred at the same real node;
        # gap reasons distinguish separate unassessed behaviors there.
        return c.digest([c.MODEL, self.fingerprint, item["kind"], item["pointer"],
                         item["rule_id"], item.get("slot"), item.get("reason")])

    def gap(self, pointer, reason="unsupported_component", *, conditional=False):
        self.partial = True
        self.emit("coverage_gap", pointer, "coverage", reason=reason, conditional=conditional)

    def visit(self, pointer, depth):
        if self.stopped:
            return False
        if self.nodes >= c.SCAN_NODES:
            self.truncated = self.partial = self.stopped = True
            return False
        self.nodes += 1
        if depth > c.SCAN_DEPTH or len(pointer) > 256:
            self.truncated = self.partial = True
            # A rejected child path may exceed the allowed length; use the
            # nearest real ancestor, never a truncated/fabricated pointer.
            while len(pointer) > 256:
                pointer = pointer.rsplit("/", 1)[0]
            self.gap(pointer, "structural_limit")
            return False
        return True

    def entity(self, value, pointer, depth, *, rule="literal_entity", conditional=False):
        if not self.visit(pointer, depth):
            return None
        entity = safe_entity(value, self.secrets)
        if entity is None:
            self.gap(pointer, "malformed_selector", conditional=conditional)
            return None
        if entity not in self.unique and len(self.unique) >= c.UNIQUE_REFERENCES:
            self.truncated = self.partial = self.stopped = True
            return None
        self.unique.add(entity)
        self.entity_pointers.add(pointer)
        self.occurrences += 1
        status = self.states.get(entity, self.registry.get(entity,
            "absent_from_observed_inventories" if self.inventories_complete else "unassessed"))
        self.emit("entity_reference", pointer, rule, entity_id=entity,
                  availability=status, conditional=conditional)
        return entity

    def array(self, value, pointer, depth, callback, conditional=False):
        if not self.visit(pointer, depth):
            return
        if type(value) is not list:
            self.gap(pointer, "malformed_selector", conditional=conditional)
            return
        for i, child in enumerate(value):
            if self.stopped:
                break
            callback(child, pointer + "/" + str(i), depth + 1, conditional)

    def conditions(self, obj, pointer, depth, conditional):
        if not self.visit(pointer, depth):
            return
        if type(obj) is not dict:
            self.gap(pointer, "malformed_selector", conditional=True)
            return
        kind = obj.get("condition", "state")
        if type(kind) is not str:
            self.gap(pointer, "malformed_selector", conditional=True)
        elif kind in {"and", "or", "not"}:
            if "conditions" in obj:
                self.array(obj["conditions"], pointer + "/conditions", depth + 1, self.conditions, True)
        elif kind in {"state", "numeric_state"}:
            if "entity" in obj:
                self.entity(obj["entity"], pointer + "/entity", depth + 1,
                            rule="condition_entity", conditional=True)
            else:
                self.gap(pointer, "dynamic_value", conditional=True)
            # The selected first slice does not expand comparison values into
            # additional entity dependencies or evaluate browser context.
            for key in ("state", "state_not", "above", "below"):
                value = obj.get(key)
                if type(value) in (list, dict) or (type(value) is str and
                    (len(value) > c.SCALAR_CHARS or safe_entity(value, self.secrets)
                     or "{{" in value or "[[" in value)):
                    self.gap(pointer, "dynamic_value", conditional=True)
                    break
        else:
            self.gap(pointer, conditional=True)

    def targets(self, value, pointer, depth, conditional):
        targets = []
        if type(value) is list:
            def member(v, p, d, cond):
                entity = self.entity(v, p, d, rule="literal_target", conditional=cond)
                if entity:
                    targets.append(entity)
            self.array(value, pointer, depth, member, conditional)
        else:
            entity = self.entity(value, pointer, depth, rule="literal_target", conditional=conditional)
            if entity:
                targets.append(entity)
        return targets

    def action(self, value, pointer, depth, parent, conditional, slot,
               *, inferred=False, rule="explicit_action", target_base=None):
        if not self.visit(pointer, depth):
            return
        if inferred and not self.defaults:
            self.gap(pointer, "source_gate_pending", conditional=conditional)
            return
        if type(value) is not dict or type(value.get("action")) is not str or value["action"] not in ACTIONS:
            self.gap(pointer, "dynamic_value", conditional=conditional)
            return
        interaction = value["action"]
        targets, service, expansion = [], None, "not_applicable"
        base = target_base if target_base is not None else pointer
        if interaction in {"perform-action", "call-service"}:
            raw_service = value.get("perform_action")
            if not truthy(raw_service):
                raw_service = value.get("service")
            service = safe_entity(raw_service, self.secrets)
            if not service:
                self.gap(pointer, "dynamic_value", conditional=conditional)
                return
            expansion = "unknown"
            data_key = "data" if value.get("data") is not None else "service_data"
            data, target = value.get(data_key), value.get("target")
            unknown = False
            for key, obj in ((data_key, data), ("target", target)):
                if obj is None:
                    continue
                if not self.visit(base, depth + 1):
                    break
                if type(obj) is not dict:
                    unknown = True
                    self.gap(pointer, "malformed_selector", conditional=conditional)
                    continue
                if "entity_id" in obj:
                    found = self.targets(obj["entity_id"], base + "/" + key + "/entity_id", depth + 2, conditional)
                    targets.extend(found)
                    expected = len(obj["entity_id"]) if type(obj["entity_id"]) is list else 1
                    if not found or len(found) != expected:
                        unknown = True
                if any(k in obj for k in ("area_id", "device_id", "label_id", "floor_id")):
                    unknown = True
            # The selected frontend forwards data and target independently.
            # Their downstream merge is outside this reviewed rule profile.
            if (type(data) is dict and type(target) is dict
                    and "entity_id" in data and "entity_id" in target):
                unknown = True
            targets = list(dict.fromkeys(targets))
            if targets and not unknown:
                expansion = "literal"
        elif interaction in {"toggle", "more-info"}:
            target = parent
            if interaction == "more-info" and truthy(value.get("entity")):
                target = self.entity(value["entity"], pointer + "/entity", depth + 1,
                                     rule="literal_target", conditional=conditional)
            targets = [target] if target else []
            expansion = "literal" if target else "unknown"
        elif interaction in {"assist", "fire-dom-event"}:
            expansion = "unknown"
        provenance = "inferred_builtin_default" if inferred else "explicit_configuration"
        values = dict(provenance=provenance, interaction=interaction,
                      confirmation=confirmation(value.get("confirmation")),
                      target_references=targets, target_expansion=expansion,
                      conditional=conditional, slot=slot)
        if service:
            values["service"] = service
        self.emit("control", pointer, rule, **values)
        if expansion == "unknown":
            self.gap(pointer, "dynamic_value", conditional=conditional)

    def slots(self, obj, pointer, depth, entity, conditional, defaults, rule, prefix=""):
        for slot in SLOTS:
            key = prefix + slot
            if self.stopped:
                break
            if key in obj:
                self.action(obj[key], pointer + "/" + key, depth + 1, entity, conditional, key)
            elif slot in defaults:
                self.action({"action": defaults[slot]}, pointer, depth, entity, conditional,
                            key, inferred=True, rule=rule)

    def row(self, obj, pointer, depth, conditional):
        if not self.visit(pointer, depth):
            return
        if type(obj) is str:
            entity = self.entity(obj, pointer, depth, conditional=conditional)
            config, kind = {}, None
        elif type(obj) is dict:
            config, kind = obj, obj.get("type")
            if type(kind) is str and kind == "call-service":
                self.service_row(obj, pointer, depth, conditional)
                return
            supported_types = {None, "", "attribute", "simple-entity", *HELPER_TYPES.values()}
            if (kind is not None and type(kind) is not str) or kind not in supported_types:
                self.gap(pointer, conditional=conditional)
                return
            if "entity" not in obj:
                self.gap(pointer, "malformed_selector", conditional=conditional)
                return
            entity = self.entity(obj["entity"], pointer + "/entity", depth + 1, conditional=conditional)
        else:
            self.gap(pointer, "malformed_selector", conditional=conditional)
            return
        if not entity:
            return
        domain = entity.split(".", 1)[0]
        is_helper = domain in HELPERS and (not kind or kind == HELPER_TYPES[domain])
        if is_helper:
            if self.defaults:
                values = dict(entity_id=entity, interaction="inline_helper", slot="inline",
                              provenance="inferred_builtin_default", target_references=[entity],
                              target_expansion="literal", conditional=True,
                              confirmation=confirmation(config.get("confirmation")) if domain == "input_button" else "absent")
                if HELPERS[domain]:
                    values["service"] = HELPERS[domain]
                self.emit("control", pointer, "entities_helper", **values)
            else:
                self.gap(pointer, "source_gate_pending", conditional=conditional)
        elif kind not in {"attribute", "simple-entity"}:
            self.gap(pointer, conditional=conditional)
        self.slots(config, pointer, depth, entity, conditional,
                   {"tap_action": "more-info"}, "row_default")

    def service_row(self, obj, pointer, depth, conditional):
        # Source has no perform-action row alias. call-service requires name
        # and action/service even if an explicit tap_action overrides its tap.
        if not truthy(obj.get("name")) or not (truthy(obj.get("action")) or truthy(obj.get("service"))):
            self.gap(pointer, "malformed_selector", conditional=conditional)
            return
        entity = self.entity(obj["entity"], pointer + "/entity", depth + 1, conditional=conditional) if "entity" in obj else None
        if "tap_action" in obj:
            self.action(obj["tap_action"], pointer + "/tap_action", depth + 1, entity, conditional, "tap_action")
        else:
            if not self.defaults:
                self.gap(pointer, "source_gate_pending", conditional=conditional)
                return
            selected = "data" if truthy(obj.get("data")) else "service_data"
            value = {"action": "perform-action", "perform_action": obj.get("action") if truthy(obj.get("action")) else obj.get("service"), selected: obj.get(selected)}
            self.action(value, pointer, depth, entity, conditional, "tap_action", rule="entities_service", target_base=pointer)
        for slot in SLOTS[1:]:
            if slot in obj:
                self.action(obj[slot], pointer + "/" + slot, depth + 1, entity, conditional, slot)
            elif slot == "hold_action":
                self.action({"action": "more-info"}, pointer, depth, entity, conditional, slot, inferred=True, rule="row_default")

    def header(self, obj, pointer, depth, conditional):
        rows = obj.get("entities")
        if type(rows) is not list:
            return
        members, selectors, invalid = [], [], False
        for index, row in enumerate(rows):
            if not self.visit(pointer, depth):
                break
            value = row if type(row) is str else row.get("entity") if type(row) is dict else None
            if value is None:
                continue
            entity = safe_entity(value, self.secrets)
            if not entity:
                invalid = True
            elif entity.split(".", 1)[0] in TOGGLE_DOMAINS:
                members.append(entity)
                entity_pointer = pointer + "/entities/" + str(index) + ("/entity" if type(row) is dict else "")
                # Header membership is a separate reviewed use of the outer
                # row selector, even when the row renderer itself is opaque.
                selectors.append((value, entity_pointer))
        visible = truthy(obj["show_header_toggle"]) if "show_header_toggle" in obj else "title" in obj and len(members) >= 2
        if invalid:
            self.gap(pointer, "malformed_selector", conditional=True)
        if visible:
            if not self.defaults:
                self.gap(pointer, "source_gate_pending", conditional=True)
            else:
                for value, entity_pointer in selectors:
                    if self.stopped:
                        break
                    if entity_pointer not in self.entity_pointers:
                        self.entity(value, entity_pointer, depth + 2, rule="entities_header", conditional=True)
                members = list(dict.fromkeys(members))
                if len(members) > c.UNIQUE_REFERENCES:
                    self.truncated = self.partial = True
                    members = members[:c.UNIQUE_REFERENCES]
                self.emit("control", pointer, "entities_header", interaction="header_toggle", slot="header",
                          provenance="inferred_builtin_default", confirmation="absent", conditional=True,
                          target_references=members, target_expansion="unknown")

    def node(self, obj, pointer, depth, conditional=False, *, kind="card"):
        if not self.visit(pointer, depth):
            return
        if type(obj) is not dict:
            self.gap(pointer, "malformed_selector", conditional=conditional)
            return
        if "strategy" in obj:
            self.gap(pointer, conditional=conditional)
            return
        if kind == "card" and (type(obj.get("type")) is not str or obj["type"] not in {
                "grid", "vertical-stack", "horizontal-stack", "conditional", "entities", "button", "tile"}):
            self.gap(pointer, conditional=conditional)
            return
        if "visibility" in obj:
            conditional = True
            self.array(obj["visibility"], pointer + "/visibility", depth + 1, self.conditions, True)
        if "visible" in obj or obj.get("disabled") is True:
            conditional = True
        for key in ("badges", "features", "header", "footer", "sidebar"):
            if key in obj:
                self.gap(pointer, conditional=conditional)
        card_type = obj.get("type")
        if kind in {"dashboard", "view", "section"}:
            allowed = {None, "masonry", "panel", "sidebar", "sections"} if kind == "view" else {None, "grid"}
            if kind != "dashboard" and (type(card_type) not in (str, type(None)) or card_type not in allowed):
                self.gap(pointer, conditional=conditional)
                return
            children = {"dashboard": (("views", "view"),),
                        "view": (("sections", "section"), ("cards", "card")),
                        "section": (("cards", "card"),)}
            for key, child_kind in children[kind]:
                if key in obj:
                    self.array(obj[key], pointer + "/" + key, depth + 1,
                        lambda v, p, d, cond, k=child_kind: self.node(v, p, d, cond, kind=k), conditional)
            return
        if type(card_type) is not str:
            self.gap(pointer, "malformed_selector", conditional=conditional)
        elif card_type in {"grid", "vertical-stack", "horizontal-stack"}:
            if "cards" in obj:
                self.array(obj["cards"], pointer + "/cards", depth + 1, self.node, conditional)
            else:
                self.gap(pointer, "malformed_selector", conditional=conditional)
        elif card_type == "conditional":
            if "conditions" in obj:
                self.array(obj["conditions"], pointer + "/conditions", depth + 1, self.conditions, True)
            else:
                self.gap(pointer, "malformed_selector", conditional=True)
            if "card" in obj:
                self.node(obj["card"], pointer + "/card", depth + 1, True)
            else:
                self.gap(pointer, "malformed_selector", conditional=True)
        elif card_type == "entities":
            if "entities" in obj:
                self.array(obj["entities"], pointer + "/entities", depth + 1, self.row, conditional)
            else:
                self.gap(pointer, "malformed_selector", conditional=conditional)
            if not self.stopped:
                self.header(obj, pointer, depth, conditional)
        elif card_type in {"button", "tile"}:
            entity = self.entity(obj["entity"], pointer + "/entity", depth + 1, conditional=conditional) if "entity" in obj else None
            if card_type == "tile" and entity is None:
                self.gap(pointer, "malformed_selector", conditional=conditional)
            domain = entity.split(".", 1)[0] if entity else None
            defaults = {"tap_action": "toggle" if card_type == "button" and domain in TOGGLE_DOMAINS else "more-info"}
            if card_type == "button":
                defaults.update(hold_action="more-info", double_tap_action="none")
            self.slots(obj, pointer, depth, entity, conditional, defaults, card_type + "_default")
            if card_type == "tile":
                default = "toggle" if domain in TOGGLE_DOMAINS or domain in {"button", "input_button", "scene"} else "none"
                icon = obj.get("icon_tap_action", {"action": default})
                if type(icon) is dict and icon.get("action") == "none":
                    # Do not certify an icon hold-only config as reachable
                    # while the source disables the icon's interaction target.
                    if any(k in obj for k in ("icon_hold_action", "icon_double_tap_action")):
                        self.gap(pointer, conditional=conditional)
                    else:
                        self.slots(obj, pointer, depth, entity, conditional,
                                   {"tap_action": default}, "tile_icon_default", "icon_")
                else:
                    self.slots(obj, pointer, depth, entity, conditional, {"tap_action": default}, "tile_icon_default", "icon_")
        else:
            self.gap(pointer, conditional=conditional)

    def finish(self):
        if self.truncated:
            item = dict(kind="coverage_gap", pointer="", rule_id="coverage", reason="structural_limit",
                        conditional=False, source_projection_exact=False)
            item["id"] = self.item_id(item)
            if item["id"] not in self.ids:
                self.items.append(item)
        partial = self.partial or self.truncated
        return {"items": self.items, "projection_hash": c.digest(self.items),
                "coverage": {"references": "partial" if partial else "complete",
                    "availability": "partial" if partial or not self.inventories_complete else "complete",
                    "controls": "partial" if partial else "complete"},
                "counts": {"examined": self.nodes, "retained": len(self.items),
                           "reference_occurrences": self.occurrences, "unique_references": len(self.unique),
                           "precision": "lower_bound" if self.truncated else "exact",
                           "processing_truncated": self.truncated}}


def scan(configuration, fingerprint, states, registry, *, core_version, known_secrets=()):
    scanner = Scanner(fingerprint, states, registry, core_version=core_version, known_secrets=known_secrets)
    scanner.node(configuration, "", 0, kind="dashboard")
    return scanner.finish()
