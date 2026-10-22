"""Pure projection of YAML definitions into the HAMCP-132 report.

Positive allowlist: only the keys named in ``schemas`` are copied from a
definition; every other key is counted, never copied. Withheld in every
position, before the shared sanitizer runs (``service.py``):

- ``db_url`` (``contracts.WITHHELD_KEYS``), whatever its value. A SQL
  definition's own ``db_url`` field is a fixed policy redaction (owner
  decision, 2026-10-22): it is reported in place as
  ``{"redacted_by_policy": "database_url"}`` and not listed in ``withheld``,
  so it alone leaves the report complete. A ``db_url`` anywhere else (nested,
  or in another integration's definition) is listed in ``withheld``;
- a ``!secret`` or ``!env_var`` reference (its name included);
- any string or nested key carrying an invisible, format or control
  character, in its original text or after a bounded percent-decode
  (``credentials.unverifiable_text``, R2-H1): the whole value becomes
  ``[withheld:unverifiable_text]``, checked before any credential screening,
  so it is never screened and shown;
- any string carrying a YAML reader redaction or truncation marker;
- any string the exit point's credential predicate matches
  (``credentials.credential_text``: a URI with userinfo, a database
  connection URI by scheme, a user and password before a host, a
  connection-string password)
  in any position: the reader already redacts a URI with userinfo, and this
  check keeps a database URL inside an allowlisted text field (a SQL
  ``query``, a template) withheld without depending on the reader;
- any nested key the YAML reader's credential or connection predicates match.
  Those predicates are not applied to the allowlisted keys themselves: they
  match substrings ("uri" in "during"), and the allowlisted keys are Core's
  own schema keys.

An ``!include`` value is reported as not followed and is never read.

Nothing is derived from a withheld input (owner decision, Josh, 2026-10-22).
When any input of a derived value (a name, key, tariff, ``source``,
``type`` or ``entity_namespace`` for the entity ID; a ``unique_id`` for a
generated unique_id) is withheld here or would be withheld by the exit point
(``credentials.withheld_at_exit``, with the known secrets), the expected
entity has no derived entity ID (``derived_entity_id_withheld``), or no
unique_id and no entity ID (``unique_id_withheld``); a dependency value is
not read for entity IDs (the diagnosis reports ``dependencies_withheld``); a
mapping key is replaced by its position in the definition ID; a legacy name
gives no candidate match. Each case is a path in ``withheld``, so the report
is partial.

Identity text is read with slice 1's parser-dependence rule
(``recorder_settings.projection.yaml11_readings``) before any matching or
name derivation (M2): a reader string Home Assistant's YAML 1.1 loader could
read as another type (an unquoted ``on``, ``yes`` or ``1:30``) gives
a ``unique_id`` marked ``unique_id_parser_dependent`` with each reading Core
could register (``unique_id_readings``), which is never an exact match, and
a name or key that gives no derived entity ID (``name_parser_dependent``).
"""

from __future__ import annotations

from itertools import product
from typing import Any

from ..entity_ids import STRICT_ENTITY_ID
from ..entity_reconciliation.contracts import DOMAIN
from ..providers.upstream_yaml_reader import canonical, is_connection_key, is_credential_key
from ..recorder_settings.projection import _is_include, _is_reference, _truncated, yaml11_readings
from . import contracts as c
from . import schemas as s
from .credentials import UNVERIFIABLE_MARKER, credential_text, unverifiable_text, withheld_at_exit
from .naming import core_slugify

# The YAML paths each integration's definitions can appear under.
INTEGRATION_PATHS = {
    "template": ("template", "sensor", "binary_sensor"),
    "sql": ("sql", "sensor"),
    "group": ("group", "sensor", "binary_sensor"),
    "utility_meter": ("utility_meter", "sensor"),
    "filter": ("sensor",), "derivative": ("sensor",), "statistics": ("sensor",),
    "integration": ("sensor",), "min_max": ("sensor",), "history_stats": ("sensor",),
    "threshold": ("binary_sensor",), "trend": ("binary_sensor",),
}
DEFINITION_PATHS = ("template", "sql", "group", "utility_meter", "sensor", "binary_sensor")
MAX_DEPENDENCIES = 20


def paths_for(entity_ids: tuple[str, ...], integrations: tuple[str, ...]) -> tuple[str, ...]:
    """The definition paths a request needs, in a fixed order."""

    if entity_ids:
        return DEFINITION_PATHS
    wanted = {path for name in integrations for path in INTEGRATION_PATHS[name]}
    return tuple(path for path in DEFINITION_PATHS if path in wanted)


def _reader_marker(value: Any) -> bool:
    # A connection URI is rendered as ``<scheme>://[REDACTED:connection_uri]``,
    # so the marker is matched anywhere in the string.
    return isinstance(value, str) and ("[REDACTED:" in value or "[TRUNCATED:" in value)


def _userinfo_text(value: Any) -> bool:
    # The exit point's predicate (H1), so a YAML value is withheld here
    # structurally by the same rules; refused text is never copied.
    return type(value) is str and credential_text(value)


def _credential_shaped(key: str) -> bool:
    try:
        canonical_key, _rounds = canonical(key)
    except Exception:
        # A key the reader's canonicalization refuses is never copied.
        return True
    return is_credential_key(canonical_key) or is_connection_key(canonical_key)


def _text(value: Any) -> str | None:
    """Core's ``cv.string`` reading of a scalar, or ``None``."""

    if type(value) is str:
        return value
    if type(value) is int:
        return str(value)
    return None


MAX_UID_READINGS = 4


def _dependent(*values: Any) -> bool:
    """Whether YAML 1.1 can read any of these reader strings differently."""

    return any(type(value) is str and yaml11_readings(value) is not None for value in values)


def _readings(value: Any) -> list[str]:
    """Core's ``cv.string`` texts of a reader scalar: the reader's own text,
    then each different YAML 1.1 reading (``True`` for an unquoted ``on``)."""

    text = _text(value)
    if text is None:
        return []
    texts = [text]
    alternative = yaml11_readings(value) if type(value) is str else None
    for reading in alternative[1] if alternative else ():
        if str(reading) not in texts:
            texts.append(str(reading))
    return texts


def _combine(join: Any, *readings: list[str]) -> list[str]:
    combined: list[str] = []
    for parts in product(*readings):
        text = join(*parts)
        if text not in combined:
            combined.append(text)
    return combined[:MAX_UID_READINGS]


def _static(text: str | None) -> bool:
    return text is not None and not any(marker in text for marker in s.TEMPLATE_MARKERS)


def _texts(value: Any):
    """Every text of ``value`` Core could read: strings, keys, integers."""

    if type(value) is str:
        yield value
    elif type(value) is int:
        yield str(value)
    elif type(value) is dict:
        for key, child in value.items():
            yield from _texts(key)
            yield from _texts(child)
    elif type(value) is list:
        for child in value:
            yield from _texts(child)


def _object_id(text: str | None) -> tuple[str | None, str | None]:
    """(object ID, limit) for a name Core lowercases and slugifies."""

    if text is None:
        return None, "name_not_text"
    if not _static(text):
        return None, "name_is_template"
    slug = core_slugify(text.lower())
    if slug is None:
        return None, "name_outside_slugify_port"
    if slug == "":
        return None, "name_empty"
    return slug, None


class Definitions:
    """Collects projected definitions from the reader envelopes."""

    def __init__(self, envelopes: dict[str, dict | None], known_secrets: tuple[str, ...] = ()):
        self.envelopes = envelopes
        self.known_secrets = known_secrets
        self.definitions: list[dict] = []
        self.unprojected: dict[str, int] = {}
        self.include_paths: list[str] = []

    # ------------------------------------------------------------------
    # Values

    def _value(self, value: Any, path: str, record: dict) -> Any:
        if _is_include(value):
            record["include_not_followed"].append(path)
            return {"include_not_followed": True}
        if _is_reference(value):
            kind = "env_var_ref" if "env_var_ref" in value else "secret_ref"
            record["withheld"].append(path)
            return {"withheld": kind}
        if type(value) is str and unverifiable_text(value):
            record["withheld"].append(path)
            return UNVERIFIABLE_MARKER
        if _reader_marker(value):
            record["withheld"].append(path)
            return {"withheld": "reader_redaction"}
        if _userinfo_text(value):
            record["withheld"].append(path)
            return {"withheld": "credential_uri"}
        if type(value) is dict:
            projected = {}
            for key, child in value.items():
                if type(key) is str and unverifiable_text(key):
                    record["withheld"].append(f"{path}.<key>")
                    continue
                child_path = f"{path}.{key}"
                if type(key) is not str or key in c.WITHHELD_KEYS or _credential_shaped(key):
                    record["withheld"].append(child_path if type(key) is str else f"{path}.<key>")
                    continue
                projected[key] = self._value(child, child_path, record)
            return projected
        if type(value) is list:
            return [self._value(child, f"{path}[{index}]", record) for index, child in enumerate(value)]
        if value is None or type(value) in {str, int, float, bool}:
            return value
        record["withheld"].append(path)
        return {"withheld": "unsupported_value"}

    def _input_withheld(self, *values: Any) -> bool:
        """Whether any input of a derived value is withheld.

        Owner decision (Josh, 2026-10-22): an input this projection withholds
        (``_value``: a reference, an include, invisible text, a reader marker,
        credential text), or one whose text the exit point would withhold
        (``credentials.withheld_at_exit``: the known secrets and the strict
        sanitizer included), yields no derived value. A slug or a generated
        ID could carry the text past the exit point's checks.
        """

        for value in values:
            if value is None:
                continue
            scratch: dict[str, list] = {"withheld": [], "include_not_followed": []}
            self._value(value, "input", scratch)
            if scratch["withheld"] or scratch["include_not_followed"]:
                return True
            if any(withheld_at_exit(text, self.known_secrets) for text in _texts(value)):
                return True
        return False

    def _key_path(self, prefix: str, key: Any, position: int, record_withheld: list[str]) -> str:
        """The definition path of a keyed entry: positional when the key is withheld."""

        if self._input_withheld(key):
            path = f"{prefix}[{position}]"
            record_withheld.append(f"{path}.<key>")
            return path
        return f"{prefix}.{key}"

    def _fields(self, entry: dict, allowed: frozenset[str], path: str, record: dict) -> dict:
        fields = {}
        for key, value in entry.items():
            if type(key) is str and key in c.WITHHELD_KEYS:
                if key == "db_url" and record.get("integration") == c.POLICY_REDACTED_INTEGRATION:
                    fields[key] = c.db_url_policy_redaction()
                else:
                    record["withheld"].append(f"{path}.{key}")
            elif type(key) is str and key in allowed:
                fields[key] = self._value(value, f"{path}.{key}", record)
            else:
                record["unrecognized_key_count"] += 1
        return fields

    # ------------------------------------------------------------------
    # Records

    def _record(self, definition_id: str, integration: str, yaml_path: str, form: str,
                entity_domain: str) -> dict:
        envelope = self.envelopes.get(yaml_path)
        return {
            "definition_id": definition_id,
            "integration": integration,
            "yaml_path": yaml_path,
            "form": form,
            "entity_domain": entity_domain,
            "status": "unsupported_legacy_platform" if form == "legacy_platform" else "read",
            "fields": {},
            "withheld": [],
            "include_not_followed": [],
            "unrecognized_key_count": 0,
            "reader_partial": bool(envelope and _truncated(envelope)),
            "expected_entities": [],
            "dependencies": [],
            "dependencies_withheld": False,
            "legacy_object_id": None,
        }

    def _add(self, record: dict) -> None:
        if record["include_not_followed"] and record["status"] == "read":
            record["status"] = "include_not_followed"
        record["dependencies"] = record["dependencies"][:MAX_DEPENDENCIES]
        self.definitions.append(record)

    def _whole_path_include(self, yaml_path: str, value: Any) -> bool:
        if _is_include(value):
            self.include_paths.append(yaml_path)
            return True
        return False

    def _expected(self, record: dict, entity_domain: str, unique_id: str | None,
                  object_id: str | None, derivation: str | None, limit: str | None, *,
                  uid_readings: list[str] | None = None, name_dependent: bool = False,
                  name_inputs: tuple = (), uid_inputs: tuple = ()) -> None:
        position = f"{record['definition_id']}.expected_entities[{len(record['expected_entities'])}]"
        if self._input_withheld(*uid_inputs):
            # Owner decision (2026-10-22): no unique_id, readings or entity ID
            # from a withheld unique_id input, and no name match in its place.
            unique_id, uid_readings = None, None
            object_id, limit = None, c.UNIQUE_ID_WITHHELD
            record["withheld"].append(f"{position}.unique_id")
        elif self._input_withheld(*name_inputs):
            # Owner decision (2026-10-22): no entity ID, and no limit that
            # describes the withheld name, from a withheld name input.
            object_id, limit = None, c.DERIVED_ENTITY_ID_WITHHELD
            record["withheld"].append(f"{position}.derived_entity_id")
        if name_dependent and object_id is not None:
            # M2: a name or key YAML 1.1 could read as another type.
            object_id, limit = None, "name_parser_dependent"
        derived = None
        if object_id is not None:
            candidate = f"{entity_domain}.{object_id}"
            if STRICT_ENTITY_ID.fullmatch(candidate):
                derived = candidate
            else:
                limit, derivation = "derived_id_not_strict", None
        record["expected_entities"].append({
            "entity_domain": entity_domain,
            "unique_id": unique_id,
            "derived_entity_id": derived,
            "derivation": derivation if derived else None,
            "derivation_limit": None if derived else (limit or "not_derivable"),
        })
        if unique_id is not None and uid_readings and len(uid_readings) > 1:
            record["expected_entities"][-1].update(unique_id_parser_dependent=True,
                                                   unique_id_readings=uid_readings[:MAX_UID_READINGS])

    def _dependencies(self, record: dict, entry: dict, integration: str) -> None:
        for key in s.DEPENDENCY_KEYS.get(integration, ()):
            value = entry.get(key)
            if self._input_withheld(value):
                # Owner decision (2026-10-22): no entity ID is read out of a
                # withheld value; the diagnosis says so instead of checking.
                record["dependencies_withheld"] = True
                record["withheld"].append(f"{record['definition_id']}.dependencies")
                continue
            items = value.split(",") if type(value) is str else value if type(value) is list else []
            for item in items:
                item = item.strip().lower() if type(item) is str else None
                if item and STRICT_ENTITY_ID.fullmatch(item) and item not in record["dependencies"]:
                    record["dependencies"].append(item)

    # ------------------------------------------------------------------
    # sql

    def sql(self) -> None:
        envelope = self.envelopes.get("sql")
        if not envelope or not envelope["found"] or self._whole_path_include("sql", envelope["value"]):
            return
        value = envelope["value"]
        entries = value if type(value) is list else [value]
        for index, entry in enumerate(entries):
            record = self._record(f"sql[{index}]", "sql", "sql", "integration_key", "sensor")
            if not self._sql_entry(record, entry, f"sql[{index}]"):
                continue
            self._add(record)

    def _sql_entry(self, record: dict, entry: Any, path: str) -> bool:
        if _is_include(entry):
            record["include_not_followed"].append(path)
            self._add(record)
            return False
        if type(entry) is not dict:
            self._count("sql:not_a_mapping")
            return False
        record["fields"] = self._fields(entry, s.SQL_KEYS, path, record)
        # The name is rendered once with no variables (Core
        # ``trigger_template_entity.py:350-353``); only a static name is
        # slugified here.
        name, uid = entry.get("name"), entry.get("unique_id")
        object_id, limit = _object_id(_text(name))
        self._expected(record, "sensor", _text(uid), object_id, "name", limit,
                       uid_readings=_readings(uid), name_dependent=_dependent(name),
                       name_inputs=(name,), uid_inputs=(uid,))
        return True

    # ------------------------------------------------------------------
    # template

    def template(self) -> None:
        envelope = self.envelopes.get("template")
        if not envelope or not envelope["found"] or self._whole_path_include("template", envelope["value"]):
            return
        value = envelope["value"]
        blocks = value if type(value) is list else [value]
        for index, block in enumerate(blocks):
            path = f"template[{index}]"
            if _is_include(block):
                record = self._record(path, "template", "template", "integration_key", "unknown")
                record["include_not_followed"].append(path)
                self._add(record)
                continue
            if type(block) is not dict:
                self._count("template:not_a_mapping")
                continue
            if "use_blueprint" in block:
                record = self._record(path, "template", "template", "blueprint_block", "unknown")
                record["status"] = "blueprint_not_projected"
                self._add(record)
                continue
            block_meta: dict[str, Any] = {"withheld": [], "include_not_followed": [],
                                          "unrecognized_key_count": 0}
            block_fields = self._fields(
                {key: item for key, item in block.items()
                 if not (type(key) is str and key in s.TEMPLATE_PLATFORMS)},
                s.TEMPLATE_BLOCK_KEYS, path, block_meta)
            block_uid = _text(block.get("unique_id"))
            block_readings = _readings(block.get("unique_id"))
            trigger_based = any(key in block for key in ("triggers", "trigger"))
            for platform in s.TEMPLATE_PLATFORMS:
                entities = block.get(platform)
                if entities is None:
                    continue
                entities = entities if type(entities) is list else [entities]
                for position, entity in enumerate(entities):
                    entity_path = f"{path}.{platform}[{position}]"
                    record = self._record(entity_path, "template", "template", "integration_key", platform)
                    record["block"] = {"fields": block_fields, "trigger_based": trigger_based}
                    record["withheld"].extend(block_meta["withheld"])
                    record["include_not_followed"].extend(block_meta["include_not_followed"])
                    record["unrecognized_key_count"] += block_meta["unrecognized_key_count"]
                    if _is_include(entity):
                        record["include_not_followed"].append(entity_path)
                        self._add(record)
                        continue
                    if type(entity) is not dict:
                        self._count("template:entity_not_a_mapping")
                        continue
                    allowed = s.TEMPLATE_ENTITY_KEYS.get(platform)
                    if allowed is None:
                        # Platforms outside sensor and binary_sensor: only the
                        # common keys are projected; the rest are counted.
                        allowed = s.TEMPLATE_COMMON_KEYS
                        record["status_detail"] = "platform_keys_not_projected"
                    record["fields"] = self._fields(entity, allowed, entity_path, record)
                    self._template_expected(record, platform, entity, block_uid, block_readings,
                                            trigger_based, block.get("unique_id"))
                    self._add(record)

    def _template_expected(self, record: dict, platform: str, entity: dict, block_uid: str | None,
                           block_readings: list[str], trigger_based: bool, block_uid_input: Any) -> None:
        entity_uid = _text(entity.get("unique_id"))
        unique_id = f"{block_uid}-{entity_uid}" if block_uid and entity_uid else entity_uid
        readings = _readings(entity.get("unique_id"))
        uid_inputs = (entity.get("unique_id"),)
        if block_uid and entity_uid:
            readings = _combine("{}-{}".format, block_readings, readings)
            uid_inputs = (block_uid_input, entity.get("unique_id"))
        default_entity_id = entity.get("default_entity_id")
        if type(default_entity_id) is str and default_entity_id.startswith(platform + "."):
            object_id = default_entity_id.split(".", 1)[1]
            self._expected(record, platform, unique_id, object_id, "default_entity_id", None,
                           uid_readings=readings, name_inputs=(default_entity_id,), uid_inputs=uid_inputs)
            return
        name = entity.get("name")
        if name is None:
            default = s.TEMPLATE_DEFAULT_NAMES.get(platform)
            object_id, limit = _object_id(default) if default else (None, "default_name_not_pinned")
            self._expected(record, platform, unique_id, object_id, "default_name", limit, uid_readings=readings,
                           uid_inputs=uid_inputs)
            return
        object_id, limit = _object_id(_text(name))
        if object_id is None and limit == "name_is_template" and trigger_based:
            limit = "trigger_based_template_name"
        self._expected(record, platform, unique_id, object_id, "name", limit, uid_readings=readings,
                       name_dependent=_dependent(name), name_inputs=(name,), uid_inputs=uid_inputs)

    # ------------------------------------------------------------------
    # group (old style) and utility_meter: keyed mappings

    def group(self) -> None:
        envelope = self.envelopes.get("group")
        if not envelope or not envelope["found"] or self._whole_path_include("group", envelope["value"]):
            return
        value = envelope["value"]
        if type(value) is not dict:
            self._count("group:not_a_mapping")
            return
        for position, (key, entry) in enumerate(value.items()):
            key_withheld: list[str] = []
            path = self._key_path("group", key, position, key_withheld)
            record = self._record(path, "group", "group", "integration_key", "group")
            record["withheld"].extend(key_withheld)
            if _is_include(entry):
                record["include_not_followed"].append(path)
                self._add(record)
                continue
            if type(entry) is list:
                # Core preprocesses a bare list to ``{entities: [...]}``.
                entry = {"entities": entry}
            if type(entry) is not dict:
                self._count("group:entry_not_a_mapping")
                continue
            record["fields"] = self._fields(entry, s.GROUP_KEYS, path, record)
            self._dependencies(record, entry, "group")
            # entity ID from the key, never a unique_id (group/__init__.py).
            object_id, limit = _object_id(_text(key))
            self._expected(record, "group", None, object_id, "key", limit, name_dependent=_dependent(key),
                           name_inputs=(key,))
            self._add(record)

    def utility_meter(self) -> None:
        envelope = self.envelopes.get("utility_meter")
        if (not envelope or not envelope["found"]
                or self._whole_path_include("utility_meter", envelope["value"])):
            return
        value = envelope["value"]
        if type(value) is not dict:
            self._count("utility_meter:not_a_mapping")
            return
        for position, (key, entry) in enumerate(value.items()):
            key_withheld: list[str] = []
            path = self._key_path("utility_meter", key, position, key_withheld)
            record = self._record(path, "utility_meter", "utility_meter", "integration_key", "sensor")
            record["withheld"].extend(key_withheld)
            if _is_include(entry):
                record["include_not_followed"].append(path)
                self._add(record)
                continue
            if type(entry) is not dict:
                self._count("utility_meter:entry_not_a_mapping")
                continue
            record["fields"] = self._fields(entry, s.UTILITY_METER_KEYS, path, record)
            self._dependencies(record, entry, "utility_meter")
            self._meter_expected(record, key, entry)
            self._add(record)

    def _meter_expected(self, record: dict, raw_key: Any, entry: dict) -> None:
        key = _text(raw_key)
        raw_uid = entry.get("unique_id")
        uid = _text(raw_uid)
        uid_readings = _readings(raw_uid)
        tariffs = entry.get("tariffs") or []
        if type(tariffs) is not list or not all(type(item) is str for item in tariffs):
            self._expected(record, "sensor", None, None, None, "tariffs_not_projected")
            return
        if not tariffs:
            name = entry.get("name", raw_key)
            object_id, limit = _object_id(_text(name))
            self._expected(record, "sensor", f"{uid}_single_tariff" if uid else None,
                           object_id, "name" if "name" in entry else "key", limit,
                           uid_readings=[f"{item}_single_tariff" for item in uid_readings],
                           name_dependent=_dependent(name), name_inputs=(name,), uid_inputs=(raw_uid,))
            return
        object_id, limit = _object_id(key)
        self._expected(record, "select", uid, object_id, "key", limit, uid_readings=uid_readings,
                       name_dependent=_dependent(raw_key), name_inputs=(raw_key,), uid_inputs=(raw_uid,))
        for tariff in tariffs:
            object_id, limit = _object_id(f"{key} {tariff}" if key is not None else None)
            self._expected(record, "sensor", f"{uid}_{tariff}" if uid else None, object_id,
                           "key_and_tariff", limit,
                           uid_readings=_combine("{}_{}".format, uid_readings, _readings(tariff)),
                           name_dependent=_dependent(raw_key, tariff), name_inputs=(raw_key, tariff),
                           uid_inputs=(raw_uid, tariff) if uid else (raw_uid,))

    # ------------------------------------------------------------------
    # sensor: and binary_sensor: platform lists

    def platforms(self, entity_domain: str) -> None:
        envelope = self.envelopes.get(entity_domain)
        if (not envelope or not envelope["found"]
                or self._whole_path_include(entity_domain, envelope["value"])):
            return
        value = envelope["value"]
        entries = value if type(value) is list else [value]
        supported = s.SENSOR_PLATFORM_KEYS if entity_domain == "sensor" else s.BINARY_SENSOR_PLATFORM_KEYS
        for index, entry in enumerate(entries):
            path = f"{entity_domain}[{index}]"
            if _is_include(entry):
                record = self._record(path, "unknown", entity_domain, "platform_entry", entity_domain)
                record["include_not_followed"].append(path)
                self._add(record)
                continue
            if type(entry) is not dict:
                self._count(f"{entity_domain}:not_a_mapping")
                continue
            platform = entry.get("platform")
            if type(platform) is not str:
                self._count(f"{entity_domain}:platform_not_text")
                continue
            if platform in supported:
                self._platform_entry(entity_domain, path, platform, entry, supported[platform])
            elif platform in s.LEGACY_PLATFORM_KEYS:
                record = self._record(path, platform, entity_domain, "legacy_platform", entity_domain)
                record["fields"] = self._fields(
                    entry, s.LEGACY_PLATFORM_KEYS[platform] | s.PLATFORM_COMMON_KEYS, path, record)
                record["legacy_object_id"] = self._legacy_object_id(entry.get("name"))
                self._add(record)
            else:
                # Outside the twelve integrations: counted by platform name
                # only when the name is a plain domain, never copied.
                name = platform if DOMAIN.fullmatch(platform) else "<other>"
                self._count(f"{entity_domain}:platform:{name}")

    def _platform_entry(self, entity_domain: str, path: str, platform: str, entry: dict,
                        allowed: frozenset[str]) -> None:
        if platform == "trend":
            self._trend(path, entry)
            return
        record = self._record(path, platform, entity_domain, "platform_entry", entity_domain)
        record["fields"] = self._fields(entry, allowed | s.PLATFORM_COMMON_KEYS, path, record)
        self._dependencies(record, entry, platform)
        raw_uid = None if platform == "threshold" else entry.get("unique_id")
        uid, uid_readings = _text(raw_uid), _readings(raw_uid)
        name, derivation, name_inputs = self._platform_name(entity_domain, platform, entry)
        name_dependent = _dependent(entry.get("name"), entry.get("entity_namespace"), entry.get("source"),
                                    entry.get("type"))
        if name is None and uid is not None:
            # A nameless entity with a unique_id: ``<platform>_<unique_id>``
            # (entity_registry.py:1476, :637-638).
            name, derivation = f"{platform}_{uid}", "unique_id_fallback"
            name_inputs = name_inputs + (raw_uid,)
            name_dependent = name_dependent or len(uid_readings) > 1
        elif name is None:
            # A nameless entity without a unique_id (entity_platform.py:1149-1166).
            name, derivation = s.DEVICE_DEFAULT_NAME, "device_default_name"
        namespace = entry.get("entity_namespace")
        if namespace is not None:
            name = f"{namespace} {name}" if type(namespace) is str else None
            name_inputs = name_inputs + (namespace,)
        object_id, limit = _object_id(name)
        self._expected(record, entity_domain, uid, object_id, derivation, limit, uid_readings=uid_readings,
                       name_dependent=name_dependent, name_inputs=name_inputs, uid_inputs=(raw_uid,))
        self._add(record)

    @staticmethod
    def _platform_name(entity_domain: str, platform: str,
                       entry: dict) -> tuple[str | None, str | None, tuple]:
        """(name, derivation, the raw inputs the name was built from)."""

        name = entry.get("name")
        if name is not None:
            text = _text(name)
            if platform == "group" and entity_domain == "sensor" and text == s.SENSOR_DEFAULT_NAMES["group"]:
                # group/sensor.py:367-369 replaces the default name.
                kind = entry.get("type")
                return ((f"Sensor group {kind}", "default_name", (name, kind)) if type(kind) is str
                        else (None, None, (name, kind)))
            return text, "name", (name,)
        if platform in {"derivative", "integration"}:
            source = entry.get("source")
            suffix = "derivative" if platform == "derivative" else "integral"
            return ((f"{source} {suffix}", "default_name", (source,)) if type(source) is str
                    else (None, None, (source,)))
        if platform == "min_max":
            kind = entry.get("type", "max")
            return ((f"{kind} sensor".capitalize(), "default_name", (kind,)) if type(kind) is str
                    else (None, None, (kind,)))
        if platform == "group" and entity_domain == "sensor":
            kind = entry.get("type")
            return ((f"Sensor group {kind}", "default_name", (kind,)) if type(kind) is str
                    else (None, None, (kind,)))
        defaults = s.SENSOR_DEFAULT_NAMES if entity_domain == "sensor" else s.BINARY_SENSOR_DEFAULT_NAMES
        if platform in defaults:
            return defaults[platform], "default_name", ()
        return None, None, ()

    def _legacy_object_id(self, name: Any) -> str | None:
        """The object ID a legacy entry's static name would give, for
        ``legacy_candidates``; none from a withheld name (owner decision,
        2026-10-22), so a match can never reveal it."""

        if type(name) is not str or self._input_withheld(name):
            return None
        object_id, _limit = _object_id(name)
        return object_id

    def _trend(self, path: str, entry: dict) -> None:
        sensors = entry.get("sensors")
        if type(sensors) is not dict:
            record = self._record(path, "trend", "binary_sensor", "platform_entry", "binary_sensor")
            record["fields"] = self._fields(entry, s.PLATFORM_COMMON_KEYS, path, record)
            if _is_include(sensors):
                record["include_not_followed"].append(f"{path}.sensors")
            self._add(record)
            return
        for position, (key, sensor) in enumerate(sensors.items()):
            key_withheld: list[str] = []
            sensor_path = self._key_path(f"{path}.sensors", key, position, key_withheld)
            record = self._record(sensor_path, "trend", "binary_sensor", "platform_entry", "binary_sensor")
            record["withheld"].extend(key_withheld)
            if _is_include(sensor):
                record["include_not_followed"].append(sensor_path)
                self._add(record)
                continue
            if type(sensor) is not dict:
                self._count("binary_sensor:trend_sensor_not_a_mapping")
                continue
            record["fields"] = self._fields(sensor, s.TREND_SENSOR_KEYS, sensor_path, record)
            self._dependencies(record, sensor, "trend")
            # The entity ID is forced from the slug key (trend/binary_sensor.py:127-129).
            object_id, limit = _object_id(_text(key))
            self._expected(record, "binary_sensor", _text(sensor.get("unique_id")), object_id, "key", limit,
                           uid_readings=_readings(sensor.get("unique_id")), name_dependent=_dependent(key),
                           name_inputs=(key,), uid_inputs=(sensor.get("unique_id"),))
            self._add(record)

    # ------------------------------------------------------------------

    def _count(self, item: str) -> None:
        self.unprojected[item] = self.unprojected.get(item, 0) + 1

    def collect(self) -> list[dict]:
        self.template()
        self.sql()
        self.group()
        self.utility_meter()
        self.platforms("sensor")
        self.platforms("binary_sensor")
        return self.definitions
