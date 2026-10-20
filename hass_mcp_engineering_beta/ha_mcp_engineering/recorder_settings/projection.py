"""Pure projection of the HAMCP-132 recorder sources into the report.

Positive allowlist: only the recorder keys named in ``contracts`` are read
from the YAML reader's value. ``db_url`` contributes only whether it is
written; its value, any ``secret_ref`` name and every unrecognized key are
never copied (design section 6). The final report also passes through the
shared sanitizer (``service.py``).
"""

from __future__ import annotations

from typing import Any

from . import contracts as c
from . import entity_filter as f


UNKNOWN_REASONS = (
    # Most specific first: a key is unknown for the first reason that holds.
    "yaml_unavailable", "include_not_followed", "yaml_truncated",
    "packages_unreadable", "packages_present",
)


def _is_include(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().lower().startswith("!include")


def _is_reference(value: Any) -> bool:
    return type(value) is dict and bool({"secret_ref", "env_var_ref"} & set(value))


def _is_marker(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("[REDACTED:", "[TRUNCATED:"))


def gateway_envelope(result: Any, yaml_path: str) -> tuple[dict | None, str | None]:
    """(envelope, failure_reason) for one fixed YAML reader result."""

    if type(result) is not dict or result.get("success") is not True:
        code = result.get("error_code") if type(result) is dict else None
        return None, code if code in c.YAML_FAILURE_CODES else "yaml_read_failed"
    metadata = result.get("metadata")
    data = result.get("data")
    if (type(metadata) is not dict or metadata.get("provider") != "upstream_read_gateway"
            or metadata.get("fallback_occurred") is not False
            or type(data) is not dict or data.get("yaml_path") != yaml_path
            or data.get("file") != "configuration.yaml"
            or type(data.get("found")) is not bool
            or data.get("completeness") not in {"complete", "partial"}
            or type(data.get("limitations")) is not list):
        return None, "yaml_read_response_identity_invalid"
    return data, None


def _truncated(envelope: dict) -> bool:
    return any(
        isinstance(item, str) and item.startswith(("value_truncated:", "value_depth_limited:",
                                                   "include_not_followed_truncated",
                                                   "strings_truncated:"))
        for item in envelope["limitations"]
    )


def _include_paths(envelope: dict) -> set[str]:
    prefix = "include_not_followed:"
    return {
        item[len(prefix):] for item in envelope["limitations"]
        if isinstance(item, str) and item.startswith(prefix)
    }


def packages_status(packages: dict | None, packages_failure: str | None,
                    core: dict | None, core_failure: str | None) -> str:
    """``absent`` | ``present`` | ``unreadable`` from the packages reads.

    The pinned reader returns not-found for ``homeassistant.packages`` both
    when there is no packages key and when an intermediate node is not a
    mapping (``homeassistant: !include core.yaml``), so a not-found packages
    path is resolved only from the ``homeassistant`` node's type and keys.
    """

    if packages is None:
        return "unreadable" if packages_failure else "unknown"
    if packages["found"]:
        value = packages["value"]
        if type(value) is dict and not value and packages["completeness"] == "complete":
            return "absent"
        return "unreadable" if _is_include(value) else "present"
    if core is None:
        return "unreadable"
    if not core["found"] or core["value"] is None:
        return "absent"
    value = core["value"]
    if type(value) is dict and "packages" not in value and not _truncated(core):
        return "absent"
    return "unreadable"


class Projector:
    def __init__(self):
        self.omissions: list[str] = []

    def omit(self, item: str) -> None:
        if item not in self.omissions:
            self.omissions.append(item)

    # ------------------------------------------------------------------
    # A1/A2: settings

    @staticmethod
    def _opaque(recorder: dict) -> bool:
        """An include that can supply any recorder key hides unwritten keys.

        That is an include at ``recorder`` itself or under a key the schema
        does not name (for example a ``<<`` merge key). An include under a
        named key affects only that key.
        """

        value = recorder["value"] if recorder["found"] else None
        if _is_include(value) or "recorder" in _include_paths(recorder):
            return True
        return type(value) is dict and any(
            _is_include(item) or f"recorder.{key}" in _include_paths(recorder)
            for key, item in value.items() if key not in c.RECOGNIZED_KEYS
        )

    def _default_reason(self, recorder: dict | None, recorder_failure: str | None,
                        packages: str) -> str | None:
        """None when Core's default applies to an unwritten key, else why not."""

        if recorder is None or recorder_failure:
            return "yaml_unavailable"
        if self._opaque(recorder):
            return "include_not_followed"
        if recorder["found"] and _truncated(recorder):
            return "yaml_truncated"
        if packages == "absent":
            return None
        return "packages_present" if packages == "present" else "packages_unreadable"

    def _scalar(self, validator: str, value: Any) -> dict:
        if _is_include(value):
            return {"value": None, "origin": "unknown", "reason": "include_not_followed"}
        if _is_reference(value) or _is_marker(value):
            return {"value": None, "origin": "configured", "value_withheld": True,
                    "reason": "secret_reference" if _is_reference(value) else "redacted"}
        try:
            if validator == "boolean":
                coerced: Any = f.boolean(value)
            else:
                coerced = f.coerce_int(value, 1 if validator == "int_min_1" else 0)
        except f.FilterValueInvalid:
            entry: dict[str, Any] = {"value": None, "origin": "configured", "invalid": True,
                                     "reason": "fails_core_schema"}
            if isinstance(value, (bool, int, float, str)):
                entry["as_written"] = value
            return entry
        entry = {"value": coerced, "origin": "configured"}
        if type(value) is not type(coerced) or value != coerced:
            entry["as_written"] = value
        return entry

    def settings(self, recorder: dict | None, recorder_failure: str | None,
                 packages: str, db_in_default_location: bool | None) -> dict:
        mapping = recorder["value"] if recorder and recorder["found"] else None
        if recorder and recorder["found"] and mapping is None:
            mapping = {}  # ``recorder:`` with no value is ``{}`` (config.py:282-283)
        written = mapping if type(mapping) is dict else {}
        if recorder and recorder["found"] and mapping is not None and type(mapping) is not dict \
                and not _is_include(mapping):
            self.omit("recorder_value_not_a_mapping")
        result: dict[str, Any] = {}
        for key, (validator, default) in c.SCALAR_SETTINGS.items():
            if key in written:
                result[key] = self._scalar(validator, written[key])
                continue
            reason = self._default_reason(recorder, recorder_failure, packages)
            if type(mapping) is not dict and mapping is not None and not _is_include(mapping):
                reason = "recorder_value_not_a_mapping"
            if reason is None:
                result[key] = {"value": default, "origin": "core_default", "pin": f.CORE_PIN}
            else:
                result[key] = {"value": None, "origin": "unknown", "reason": reason}
        for key, validator in c.DEPRECATED_SETTINGS.items():
            if key in written:
                result[key] = {**self._scalar(validator, written[key]), "deprecated": True}
        db: dict[str, Any] = {"value_withheld": True, "default_location": db_in_default_location}
        if c.DB_URL_KEY in written:
            db["origin"] = "configured"
        else:
            reason = self._default_reason(recorder, recorder_failure, packages)
            if type(mapping) is not dict and mapping is not None and not _is_include(mapping):
                reason = "recorder_value_not_a_mapping"
            db.update({"origin": "core_default_location"} if reason is None
                      else {"origin": "unknown", "reason": reason})
        result[c.DB_URL_KEY] = db
        unrecognized = sum(1 for key in written if key not in c.RECOGNIZED_KEYS)
        if unrecognized:
            # Names and values are never reported: an unknown key may carry a
            # credential, and Core's recorder schema refuses unknown keys.
            self.omit("unrecognized_recorder_keys_not_reported")
            result["unrecognized_key_count"] = unrecognized
        for key, entry in result.items():
            if type(entry) is dict and entry.get("origin") == "unknown":
                self.omit(f"setting_unknown:{key}:{entry['reason']}")
        return result

    # ------------------------------------------------------------------
    # A4: filter

    def _filter_part(self, value: Any, *, exclude: bool) -> tuple[dict | None, str | None]:
        if value is None:
            return None, "fails_core_schema"  # ``include:`` with no mapping
        if _is_include(value):
            return None, "include_not_followed"
        if type(value) is not dict:
            return None, "fails_core_schema"
        if any(_is_include(item) for item in value.values()):
            return None, "include_not_followed"
        allowed = set(c.FILTER_LIST_KEYS) | ({c.EVENT_TYPES_KEY} if exclude else set())
        if not set(value) <= allowed:
            return None, "fails_core_schema"
        part: dict[str, list[str]] = {}
        for key in (*c.FILTER_LIST_KEYS, *((c.EVENT_TYPES_KEY,) if exclude else ())):
            raw = value.get(key, [])
            items = raw if isinstance(raw, list) else [raw]
            if any(_is_include(item) for item in items) or _is_include(raw):
                return None, "include_not_followed"
            if any(_is_reference(item) or _is_marker(item) for item in items) or _is_reference(raw):
                return None, "value_withheld"
            try:
                part[key] = f.entity_ids(raw) if key == "entities" and key in value else (
                    [] if key not in value else f.string_list(raw))
            except f.FilterValueInvalid:
                return None, "fails_core_schema"
            if len(part[key]) > c.MAX_FILTER_ITEMS or any(
                    len(item) > c.MAX_FILTER_TEXT for item in part[key]):
                return None, "filter_too_large"
        return part, None

    def filter(self, recorder: dict | None, recorder_failure: str | None,
               packages: str) -> tuple[dict, f.RecorderFilter | None]:
        mapping = recorder["value"] if recorder and recorder["found"] else None
        if recorder and recorder["found"] and mapping is None:
            mapping = {}
        if recorder is None or recorder_failure:
            report = {"origin": "unknown", "computable": False, "reason": "yaml_unavailable"}
            self.omit("filter_unknown:yaml_unavailable")
            return report, None
        if _is_include(mapping) or self._opaque(recorder):
            self.omit("filter_unknown:include_not_followed")
            return {"origin": "unknown", "computable": False, "reason": "include_not_followed"}, None
        if mapping is not None and type(mapping) is not dict:
            self.omit("filter_unknown:recorder_value_not_a_mapping")
            return {"origin": "unknown", "computable": False,
                    "reason": "recorder_value_not_a_mapping"}, None
        written = mapping or {}
        parts: dict[str, dict] = {}
        for key in c.FILTER_KEYS:
            if key not in written:
                parts[key] = {name: [] for name in c.FILTER_LIST_KEYS}
                if key == "exclude":
                    parts[key][c.EVENT_TYPES_KEY] = []
                continue
            part, reason = self._filter_part(written[key], exclude=key == "exclude")
            if part is None:
                self.omit(f"filter_unknown:{reason}")
                return {"origin": "unknown", "computable": False, "reason": reason}, None
            parts[key] = part
        if recorder["found"] and _truncated(recorder):
            self.omit("filter_unknown:yaml_truncated")
            return {"origin": "unknown", "computable": False, "reason": "yaml_truncated"}, None
        recorder_filter = f.RecorderFilter(parts["include"], parts["exclude"])
        report: dict[str, Any] = {
            "origin": "configured" if any(key in written for key in c.FILTER_KEYS) else "core_default",
            "include": parts["include"],
            "exclude": parts["exclude"],
            "filter_case": recorder_filter.case,
            "exclude_domains_ignored": (recorder_filter.case == f.INCLUDE_DOMAIN_OR_GLOB
                                        and bool(parts["exclude"]["domains"])),
            "state_changed_event_type_ignored": "state_changed" in parts["exclude"][c.EVENT_TYPES_KEY],
            "computable": True,
        }
        if report["origin"] == "core_default":
            report["pin"] = f.CORE_PIN
        if packages != "absent":
            # Packages merge recorder lists by concatenation (config.py
            # _recursive_merge); the file alone is not the loaded filter.
            reason = "packages_present" if packages == "present" else "packages_unreadable"
            report.update(origin="unknown", computable=False, reason=reason,
                          configuration_yaml_portion_only=True)
            self.omit(f"filter_unknown:{reason}")
            return report, None
        return report, recorder_filter

    # ------------------------------------------------------------------
    # A3: runtime

    def runtime(self, outcome: tuple[bool, Any] | None, failure: str | None) -> dict:
        if failure:
            return {"status": "unavailable", "reason": failure}
        ok, value = outcome if outcome is not None else (False, "")
        if not ok:
            # Core registers recorder/info only when the recorder is set up;
            # ``unknown_command`` never means "not recording".
            return {"status": "unavailable",
                    "reason": "recorder_not_set_up" if value == "unknown_command" else "command_failed"}
        if type(value) is not dict:
            return {"status": "unavailable", "reason": "invalid_response"}
        report: dict[str, Any] = {"status": "available"}
        for key, types in c.RUNTIME_FIELDS.items():
            field = value.get(key)
            if key in value and type(field) in types:
                report[key] = field
            else:
                report[key] = None
                self.omit(f"runtime_field_unavailable:{key}")
        # The recorder.disable/enable flag has no Core read surface.
        report["disable_service_state"] = "not_observable"
        return report

    # ------------------------------------------------------------------
    # A4: per entity

    def entities(self, entity_ids: tuple[str, ...], outcomes: list | None,
                 failure: str | None, recorder_filter: f.RecorderFilter | None) -> list[dict]:
        rows = []
        for index, entity_id in enumerate(entity_ids):
            core: bool | None = None
            reason = failure
            if failure is None and outcomes is not None:
                ok, value = outcomes[index]
                if ok and type(value) is dict and set(value) == {"recording_disabled_by"} \
                        and value["recording_disabled_by"] in (None, "user"):
                    core = value["recording_disabled_by"] is None
                elif ok:
                    reason = "invalid_response"
                else:
                    reason = "recorder_not_set_up" if value == "unknown_command" else "command_failed"
            computed = recorder_filter.recorded(entity_id) if recorder_filter else None
            row: dict[str, Any] = {"entity_id": entity_id, "core_recorded": core,
                                   "computed_recorded": computed}
            if core is not None and computed is not None:
                row["agreement"] = "agree" if core == computed else "filter_drift"
            else:
                row["agreement"] = "unknown"
            if reason:
                row["core_reason"] = reason
            if computed is None:
                row["computed_reason"] = "filter_not_computable"
            if row["agreement"] == "filter_drift":
                # Never resolved: the file may have changed since the last
                # start, packages or YAML 1.1/1.2 parsing may differ.
                self.omit("filter_drift:" + entity_id)
            rows.append(row)
        return rows
