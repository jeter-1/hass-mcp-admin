"""Unavailability and capability diagnosis (HAMCP-132 slice 2, item D).

Every classification names the read-only signals it rests on and never
infers more than they support; ``undetermined`` is the honest answer when
they do not decide. State values are never copied: only whether a state is
present, ``unavailable`` or ``unknown``.

The ``ha_mcp_tools`` capability block uses only Core's own reads
(``manifest/get``, ``config_entries/get``, ``/api/config`` components). The
component's commands are not called (owner decision, 2026-10-21), so a
loaded component whose helper listing still fails stays ``undetermined``.
"""

from __future__ import annotations

import re
from typing import Any

from . import contracts as c
from . import credentials
from . import schemas as s

ENTRY_STATES = frozenset({
    "loaded", "setup_error", "migration_error", "setup_retry", "not_loaded", "failed_unload",
    "setup_in_progress", "unload_in_progress",
})
TRANSIENT_ENTRY_STATES = frozenset({"setup_retry", "setup_in_progress", "unload_in_progress"})
DISABLED_BY = frozenset({"user", "integration", "config_entry", "device", "hass"})
# A malformed source value (not text) kept for its item only (M4).
INVALID = "invalid_response"
TRANSLATION_KEY = re.compile(r"[a-z0-9_]{1,64}\Z")
ENTRY_ID = re.compile(r"[0-9A-Za-z_-]{1,64}\Z")
VERSION = re.compile(r"([0-9]{1,4})\.([0-9]{1,4})(?:\.([0-9]{1,4}))?\Z")
# The first component release with the helper listing (design section 3 D).
HELPERS_LIST_VERSION = (1, 1, 0)
MAX_SOURCE_SIGNALS = 5


def entry_rows(value: Any, domain: str, known_secrets: tuple[str, ...] = ()) -> list[dict] | None:
    """Projected ``config_entries/get`` rows: identity and state only.

    The free-form ``reason`` text is withheld; only whether one is present
    and a pattern-checked translation key are kept. The title is Core free
    text: the complete original title is shown only when the allowlist
    clears it (``credentials.display_text``), never truncated first.
    """

    if type(value) is not list:
        return None
    rows = []
    for item in value:
        if type(item) is not dict or item.get("domain") != domain:
            continue
        entry_id = item.get("entry_id")
        if type(entry_id) is not str or not ENTRY_ID.fullmatch(entry_id):
            continue
        state = item.get("state")
        disabled_by = item.get("disabled_by")
        key = item.get("error_reason_translation_key")
        title = item.get("title")
        rows.append({
            "entry_id": entry_id,
            "domain": domain,
            "title": credentials.display_text(title, c.MAX_TITLE, known_secrets)
            if type(title) is str else None,
            # Values that are not text are malformed, never a set lookup (M4).
            "state": INVALID if type(state) is not str else state if state in ENTRY_STATES else "unrecognized",
            "disabled_by": None if disabled_by is None else INVALID if type(disabled_by) is not str
            else disabled_by if disabled_by in DISABLED_BY else "unrecognized",
            "reason_present": item.get("reason") not in (None, ""),
            "error_reason_translation_key": key if type(key) is str and TRANSLATION_KEY.fullmatch(key)
            else None,
        })
    return rows


def _entry_reasons(entry: dict) -> tuple[str, list[str]]:
    if INVALID in (entry["state"], entry["disabled_by"]):
        return "undetermined", [f"config_entry_{INVALID}"]
    if entry["disabled_by"] is not None:
        return "disabled", [f"config_entry_disabled:{entry['disabled_by']}"]
    reasons = [f"config_entry_state:{entry['state']}"]
    if entry["state"] in TRANSIENT_ENTRY_STATES:
        return "transient", reasons
    if entry["state"] == "loaded":
        return "", reasons
    return "deployment_dependency", reasons


def entity(entity_id: str, *, state: str | None, states_read: bool, registry_row: dict | None,
           registry_read: bool, match: dict, entries: dict[str, dict], definition: dict | None,
           components: set[str] | None, platform: str | None, legacy: list[str],
           states: dict[str, str] | None, yaml_gaps: list[str]) -> dict:
    """The classification and reasons for one requested entity.

    ``yaml_gaps`` names each YAML definition source that was not read whole
    (a failed or partial path, unread packages, an include not followed).
    With any gap a definition can exist unread, so absence is never claimed
    (M3); the claim made with no gap still states that labelled keys cannot
    be addressed (``contracts.LABELLED_KEY_DISCLOSURE``).
    """

    reasons: list[str] = []
    if not states_read:
        return {"classification": "undetermined", "reasons": ["source_failed:states"]}
    if registry_row is not None and registry_row.get("identity") == INVALID:
        return {"classification": "undetermined", "reasons": [f"registry_identity_{INVALID}"]}
    if registry_row is not None and registry_row.get("disabled_by") == INVALID:
        return {"classification": "undetermined", "reasons": [f"registry_disabled_by_{INVALID}"]}
    if state == INVALID:
        return {"classification": "undetermined", "reasons": [f"state_{INVALID}"]}
    if registry_row is not None and registry_row.get("disabled_by") is not None:
        by = registry_row["disabled_by"]
        return {"classification": "disabled",
                "reasons": [f"registry_disabled:{by if by in DISABLED_BY else 'unrecognized'}"]}
    if state == "available":
        return {"classification": "available", "reasons": []}

    entry_id = match.get("config_entry_id")
    if entry_id is not None:
        entry = entries.get(entry_id)
        if entry is None:
            reasons.append("source_failed:config_entries/get")
        else:
            classification, entry_reasons = _entry_reasons(entry)
            reasons.extend(entry_reasons)
            if classification:
                return {"classification": classification, "reasons": reasons}

    if state is None:
        reasons.append("no_state")
        if legacy:
            return {"classification": "deployment_dependency",
                    "reasons": reasons + ["legacy_platform_unsupported"]}
        if registry_row is None and registry_read and definition is None and entry_id is None:
            if yaml_gaps:
                return {"classification": "undetermined",
                        "reasons": reasons + yaml_gaps[:MAX_SOURCE_SIGNALS]}
            return {"classification": "not_found",
                    "reasons": reasons + ["definition_absent", "labelled_keys_not_addressable"]}
        integration = definition["integration"] if definition else (
            registry_row["platform"] if registry_row else None)
        if integration and components is not None and entry_id is None:
            domain = entity_id.split(".", 1)[0]
            if integration not in components:
                return {"classification": "deployment_dependency",
                        "reasons": reasons + [f"integration_not_loaded:{integration}"]}
            # Core adds "<platform>.<domain>" after a platform's setup
            # succeeds (helpers/entity_platform.py:480, :571).
            if f"{integration}.{domain}" not in components:
                return {"classification": "deployment_dependency",
                        "reasons": reasons + [f"platform_not_loaded:{integration}.{domain}"]}
        if components is None:
            reasons.append("source_failed:config")
        if not registry_read:
            reasons.append("source_failed:config/entity_registry/list")
        return {"classification": "undetermined", "reasons": reasons}

    reasons.append(f"state_{state}")
    if definition is not None and definition.get("dependencies_withheld"):
        # Owner decision (Josh, 2026-10-22): no entity ID is read out of a
        # withheld dependency value, so the dependencies were not checked.
        return {"classification": "undetermined", "reasons": reasons + [c.DEPENDENCIES_WITHHELD]}
    if definition is not None and states is not None:
        if any(states.get(item) == INVALID for item in definition["dependencies"]):
            return {"classification": "undetermined", "reasons": reasons + [f"source_entity_{INVALID}"]}
        missing = [item for item in definition["dependencies"] if item not in states]
        unavailable = [item for item in definition["dependencies"]
                       if states.get(item) in {"unavailable", "unknown"}]
        if missing or unavailable:
            reasons.extend(f"source_entity_missing:{item}" for item in missing[:MAX_SOURCE_SIGNALS])
            reasons.extend(f"source_entity_unavailable:{item}"
                           for item in unavailable[:MAX_SOURCE_SIGNALS])
            return {"classification": "dependency_unavailable", "reasons": reasons}
    # Without the one matched definition its dependencies were not checked, so
    # an entity of a supported platform is not called entity-level.
    if definition is None and entry_id is None and match.get("method") != "not_applicable":
        return {"classification": "undetermined", "reasons": reasons + ["definition_not_matched"]}
    if entry_id is not None:
        # A UI definition is not readable without a flow, so its dependencies
        # were not checked (M3).
        return {"classification": "undetermined",
                "reasons": reasons + ["definition_not_readable_without_flow", "dependencies_not_checked"]}
    if definition is not None and definition["integration"] not in s.DEPENDENCY_KEYS:
        # A template or SQL definition's sources are not extracted.
        return {"classification": "undetermined", "reasons": reasons + ["dependencies_not_checked"]}
    return {"classification": "entity_level", "reasons": reasons}


def _version(value: Any) -> tuple[str | None, tuple[int, ...] | None]:
    if type(value) is not str:
        return None, None
    parsed = VERSION.fullmatch(value)
    if parsed is None:
        return None, None
    return value, tuple(int(part or 0) for part in parsed.groups())


def capability(manifest: Any, manifest_failure: str | None, entries: list[dict] | None,
               entries_failure: str | None, components: set[str] | None) -> dict:
    """The ``ha_mcp_tools`` component capability block from Core's reads."""

    block: dict[str, Any] = {
        "component": c.COMPONENT_DOMAIN,
        "component_commands": "not_used_owner_decision",
        "signals": [],
        "installed": None,
        "version": None,
        "loaded": None if components is None else c.COMPONENT_DOMAIN in components,
        "entries": entries[:c.MAX_UI_ENTRIES] if entries is not None else None,
    }
    signals = block["signals"]
    if manifest_failure == "not_found":
        block["installed"] = False
        signals.append("manifest_not_found")
    elif manifest_failure:
        signals.append(f"manifest:{manifest_failure}")
    elif type(manifest) is dict:
        block["installed"] = True
        version, parsed = _version(manifest.get("version"))
        block["version"] = version
        if version is None:
            signals.append("manifest_version_unparsed")
    else:
        signals.append("manifest:invalid_response")
    if entries_failure:
        signals.append(f"config_entries:{entries_failure}")
    if components is None:
        signals.append("config:unavailable")

    def done(classification: str, reason: str) -> dict:
        block.update(classification=classification, reason=reason)
        return block

    if "permission_failure" in (manifest_failure, entries_failure):
        return done("permission", "permission_failure")
    if block["installed"] is False:
        return done("deployment_dependency", "not_installed")
    if entries is not None and block["installed"]:
        if not entries:
            return done("deployment_dependency", "not_configured")
        classifications = [_entry_reasons(entry) for entry in entries]
        for classification, reasons in classifications:
            signals.extend(reasons)
        if any(item[0] == "undetermined" for item in classifications) and not any(
                item[0] == "" for item in classifications):
            return done("undetermined", f"config_entry_{INVALID}")
        if any(item[0] == "disabled" for item in classifications) and not any(
                item[0] == "" for item in classifications):
            return done("deployment_dependency", "config_entry_disabled")
        if not any(item[0] == "" for item in classifications):
            if any(item[0] == "transient" for item in classifications):
                return done("transient", "config_entry_not_yet_loaded")
            return done("deployment_dependency", "config_entry_not_loaded")
        version = _version(block["version"])[1]
        if version is not None and version < HELPERS_LIST_VERSION:
            return done("upstream_feature_gap", "component_version_before_helper_listing")
        return done("undetermined", "component_loaded_cause_not_observable_without_component_commands")
    if manifest_failure in {"timeout", "home_assistant_unavailable"} \
            or entries_failure in {"timeout", "home_assistant_unavailable"}:
        return done("transient", "source_unavailable")
    return done("undetermined", "signals_incomplete")
