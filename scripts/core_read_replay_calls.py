"""Raw delegated-read calls against one ha-mcp peer on loopback (HAMCP-371).

Runs inside the lane's holder container.  For every case the public
Engineering call is turned into upstream wire arguments by the adapter code
of this checkout (the same functions, in the same order, as the dispatch path
in ``providers/upstream_read_gateway.py``), sent as one ``tools/call`` over
streamable HTTP and stored raw: request, status, headers, body and decoded
messages.  Nothing here interprets, filters or repairs a result.

One case is not a read: ``f-invalid-json`` sends text that is not JSON to the
upstream service tool, naming a service that exists in no Core, because the
``VALIDATION_INVALID_JSON`` result has no other source the lane can reach.
Upstream refuses it at its parse step, before any dispatch.

The ``review_probes`` group is the review script's four error probes word for
word (N31, 2026-11-15).  They reference no seed value, so ``--seed`` may be
left out when only that group is selected.

The capture runs before a 9.2 adapter exists, so the wire arguments for both
upstream versions are built from one reviewed policy version (``--policy``)
and recorded with every result.  The replay tests then assert that the 9.2
adapter builds the same arguments.

The peer's output is untrusted data: it is written to the output directory
and never evaluated.  ``$seed.<name>`` strings in a case are replaced by the
values the seed step recorded; a case never carries a token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "hass_mcp_engineering_beta"))
import mcp_raw_observation as raw  # noqa: E402

CALLS_FORMAT_VERSION = 1
SEED = "$seed."
SKILL = {"skill": "home-assistant-best-practices", "file": "SKILL.md"}
UNKNOWN_ID = "hamcp371_absent_object"
# The forced case: a service that exists in no Core, and text no JSON parser
# accepts.  Upstream parses ``data`` before it dispatches anything.
ABSENT_DOMAIN, ABSENT_SERVICE = "hamcp371_absent_domain", "hamcp371_absent_service"
INVALID_JSON = "{hamcp371 not json"

# kind "public": ``tool`` is the Engineering name and the adapter builds the
# wire arguments.  kind "upstream": ``tool`` and ``arguments`` go to the peer
# exactly as written (harness-chosen probes the adapter would refuse).
# kind "yaml": the closed YAML reader shape.  ``new_session`` and ``idle``
# mark the transport observations.
CASES: tuple[dict[str, Any], ...] = (
    # The 27 delegated reads, one primary call each.
    {"id": "r01-automation", "group": "reads", "tool": "ha_config_get_automation",
     "arguments": {"identifier": "$seed.automation_id"}},
    {"id": "r02-calendar-events", "group": "reads", "tool": "ha_config_get_calendar_events",
     "arguments": {"entity_id": "$seed.calendar_entity", "start": "$seed.window_start",
                   "end": "$seed.window_end", "max_results": 5}},
    {"id": "r03-category", "group": "reads", "tool": "ha_config_get_category",
     "arguments": {"scope": "automation"}},
    {"id": "r04-label", "group": "reads", "tool": "ha_config_get_label", "arguments": {}},
    {"id": "r05-scene", "group": "reads", "tool": "ha_config_get_scene",
     "arguments": {"scene_id": "$seed.scene_id"}},
    {"id": "r06-script", "group": "reads", "tool": "ha_config_get_script",
     "arguments": {"script_id": "$seed.script_id"}},
    {"id": "r07-dashboard-resources", "group": "reads",
     "tool": "ha_config_list_dashboard_resources", "arguments": {"limit": 5}},
    {"id": "r08-groups", "group": "reads", "tool": "ha_config_list_groups",
     "arguments": {"limit": 5}},
    {"id": "r09-helpers", "group": "reads", "tool": "ha_config_list_helpers",
     "arguments": {"helper_type": "input_boolean", "limit": 5}},
    {"id": "r10-template", "group": "reads", "tool": "ha_eval_template",
     "arguments": {"template": "$seed.template"}},
    {"id": "r11-automation-traces", "group": "reads", "tool": "ha_get_automation_traces",
     "arguments": {"automation_id": "$seed.automation_entity", "limit": 5}},
    {"id": "r12-blueprints", "group": "reads", "tool": "ha_get_blueprint",
     "arguments": {"domain": "automation"}},
    {"id": "r13-devices", "group": "reads", "tool": "ha_get_device", "arguments": {"limit": 5}},
    {"id": "r14-entity", "group": "reads", "tool": "ha_get_entity",
     "arguments": {"entity_id": "$seed.helper_entity"}},
    {"id": "r15-entity-exposure", "group": "reads", "tool": "ha_get_entity_exposure",
     "arguments": {"entity_id": "$seed.helper_entity"}},
    {"id": "r16-history", "group": "reads", "tool": "ha_get_history",
     "arguments": {"entity_ids": "$seed.helper_entity", "start_time": "$seed.window_start",
                   "end_time": "$seed.window_end", "limit": 5}},
    {"id": "r17-overview", "group": "reads", "tool": "ha_get_overview",
     "arguments": {"detail_level": "minimal", "domains": ["input_boolean"], "limit": 5,
                   "include_notifications": False}},
    {"id": "r18-skill-guide", "group": "reads", "tool": "ha_get_skill_guide",
     "arguments": dict(SKILL)},
    {"id": "r19-state", "group": "reads", "tool": "ha_get_state",
     "arguments": {"entity_id": "$seed.helper_entity"}},
    {"id": "r20-system-health", "group": "reads", "tool": "ha_get_system_health",
     "arguments": {}},
    {"id": "r21-todo", "group": "reads", "tool": "ha_get_todo", "arguments": {}},
    {"id": "r22-zones", "group": "reads", "tool": "ha_get_zone", "arguments": {}},
    {"id": "r23-floors-areas", "group": "reads", "tool": "ha_list_floors_areas",
     "arguments": {}},
    {"id": "r24-services", "group": "reads", "tool": "ha_list_services",
     "arguments": {"limit": 5}},
    {"id": "r25-search", "group": "reads", "tool": "ha_search",
     "arguments": {"domain_filter": "input_boolean", "limit": 5}},
    {"id": "r26-integrations", "group": "reads", "tool": "ha_get_integration",
     "arguments": {"limit": 5}},
    {"id": "r27-apps", "group": "reads", "tool": "ha_get_app", "arguments": {}},
    # Second shapes of the same reads.
    {"id": "v16-history-relative", "group": "variants", "tool": "ha_get_history",
     "arguments": {"entity_ids": "$seed.helper_entity", "start_time": "24h", "limit": 5}},
    {"id": "v16-history-offset-free", "group": "variants", "tool": "ha_get_history",
     "arguments": {"entity_ids": "$seed.helper_entity",
                   "start_time": "$seed.window_start_offset_free",
                   "end_time": "$seed.window_end_offset_free", "limit": 5}},
    {"id": "v16-history-statistics", "group": "variants", "tool": "ha_get_history",
     "arguments": {"entity_ids": "$seed.statistics_entity", "source": "statistics",
                   "start_time": "$seed.window_start", "end_time": "$seed.window_end",
                   "limit": 5}},
    {"id": "v17-overview-standard", "group": "variants", "tool": "ha_get_overview",
     "arguments": {"detail_level": "standard", "limit": 5, "include_notifications": False}},
    {"id": "v21-todo-list", "group": "variants", "tool": "ha_get_todo",
     "arguments": {"entity_id": "$seed.todo_entity"}},
    {"id": "v22-zone-single", "group": "variants", "tool": "ha_get_zone",
     "arguments": {"zone_id": "$seed.zone_id"}},
    {"id": "v24-services-domain-summary", "group": "variants", "tool": "ha_list_services",
     "arguments": {"domain": "input_boolean", "detail_level": "summary", "limit": 5}},
    {"id": "v24-services-domain-full", "group": "variants", "tool": "ha_list_services",
     "arguments": {"domain": "input_boolean", "detail_level": "full", "limit": 5}},
    {"id": "v24-services-response", "group": "variants", "tool": "ha_list_services",
     "arguments": {"domain": "calendar", "detail_level": "full", "limit": 5}},
    {"id": "v25-search-text", "group": "variants", "tool": "ha_search",
     "arguments": {"query": "$seed.search_text", "limit": 5}},
    # P3: invalid arguments, sent to the peer as written.
    {"id": "p3-wrong-type", "group": "errors", "kind": "upstream", "tool": "ha_search",
     "arguments": {"query": "hamcp371", "limit": {"synthetic": True}}},
    {"id": "p3-unknown-argument", "group": "errors", "kind": "upstream", "tool": "ha_get_state",
     "arguments": {"entity_id": "$seed.helper_entity", "hamcp371_unknown_argument": True}},
    # P4: reads of objects the seed never created.
    {"id": "p4-absent-automation", "group": "errors", "tool": "ha_config_get_automation",
     "arguments": {"identifier": UNKNOWN_ID}},
    {"id": "p4-absent-state", "group": "errors", "tool": "ha_get_state",
     "arguments": {"entity_id": "sensor." + UNKNOWN_ID}},
    {"id": "p4-absent-entity", "group": "errors", "tool": "ha_get_entity",
     "arguments": {"entity_id": "sensor." + UNKNOWN_ID}},
    # N31 (2026-11-15): the review script's four error probes, word for word
    # (``review_upstream_read_release.ERROR_PROBES``), sent as written.  Only
    # the lane's ``probes`` command selects them, on a Core that holds the
    # onboarding and the two tokens and no seeded object, so the automation
    # list is empty.  They need no seed value.  The id is ``e-`` and the probe
    # name with hyphens.
    {"id": "e-invalid-search", "group": "review_probes", "kind": "upstream",
     "tool": "ha_search", "arguments": {"search_types": []}},
    {"id": "e-missing-state", "group": "review_probes", "kind": "upstream",
     "tool": "ha_get_state",
     "arguments": {"entity_id": "sensor.compatibility_review_missing_state"}},
    {"id": "e-missing-automation", "group": "review_probes", "kind": "upstream",
     "tool": "ha_config_get_automation",
     "arguments": {"identifier": "compatibility_review_missing_automation"}},
    {"id": "e-missing-registry-entity", "group": "review_probes", "kind": "upstream",
     "tool": "ha_get_entity",
     "arguments": {"entity_id": "sensor.compatibility_review_missing_registry_entity"}},
    # P5 and P9: transport observations.
    {"id": "t-large-services", "group": "transport", "kind": "upstream",
     "tool": "ha_list_services", "arguments": {"detail_level": "full"}},
    {"id": "t-after-idle", "group": "transport", "tool": "ha_get_entity", "idle": True,
     "arguments": {"entity_id": "$seed.helper_entity"}},
    {"id": "t-new-session", "group": "transport", "tool": "ha_get_state", "new_session": True,
     "arguments": {"entity_id": "$seed.helper_entity"}},
    # P9 (e): socket-borne reads after the disposable Core was restarted.
    {"id": "a-entity-after-core-restart", "group": "after_restart", "tool": "ha_get_entity",
     "arguments": {"entity_id": "$seed.helper_entity"}},
    {"id": "a-floors-areas-after-core-restart", "group": "after_restart",
     "tool": "ha_list_floors_areas", "arguments": {}},
    # The one call here that is not a read.  ``VALIDATION_INVALID_JSON`` comes
    # from upstream's JSON parse of a string argument, which no delegated read
    # has; the service tool has it, ahead of any dispatch.  Selected only by
    # the lane's control order.
    {"id": "f-invalid-json", "group": "forced", "kind": "upstream", "tool": "ha_call_service",
     "arguments": {"domain": ABSENT_DOMAIN, "service": ABSENT_SERVICE, "data": INVALID_JSON}},
    # The two reads whose results differed between the versions with no known
    # cause, and the whole service listing in the order upstream pages it.
    {"id": "o-overview-standard", "group": "order", "tool": "ha_get_overview",
     "arguments": {"detail_level": "standard", "limit": 5, "include_notifications": False}},
    {"id": "o-services-first-five", "group": "order", "tool": "ha_list_services",
     "arguments": {"limit": 5}},
    {"id": "o-services-default-page", "group": "order", "kind": "upstream",
     "tool": "ha_list_services", "arguments": {"detail_level": "full"}},
    {"id": "o-services-page-1", "group": "order", "kind": "upstream",
     "tool": "ha_list_services", "arguments": {"limit": 200, "offset": 0}},
    {"id": "o-services-page-2", "group": "order", "kind": "upstream",
     "tool": "ha_list_services", "arguments": {"limit": 200, "offset": 200}},
    # The YAML read: profile B only, selected by the lane.
    {"id": "y-group", "group": "yaml", "kind": "yaml", "tool": "ha_config_get_yaml",
     "arguments": {"yaml_path": "group"}},
    {"id": "y-absent-key", "group": "yaml", "kind": "yaml", "tool": "ha_config_get_yaml",
     "arguments": {"yaml_path": UNKNOWN_ID}},
)
GROUPS = tuple(dict.fromkeys(case["group"] for case in CASES))
READ_COUNT = 27


class CallsError(Exception):
    """A fixed token; never upstream text."""


def resolve(value: Any, seed: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith(SEED):
        name = value[len(SEED):]
        if not isinstance(seed.get(name), (str, int, float)) or isinstance(seed.get(name), bool):
            raise CallsError("seed_value_missing")
        return seed[name]
    if isinstance(value, dict):
        return {key: resolve(item, seed) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, seed) for item in value]
    return value


HISTORY_REFUSAL = "history_time_offset_required"


def policy_entries(version: str) -> dict[str, Any]:
    """Reviewed policy entries of one admitted version, by Engineering name."""
    from ha_mcp_engineering import upstream_tool_policy

    registry = upstream_tool_policy.load_reviewed_upstream_release_registry()
    release = registry.by_version.get(version)
    if release is None:
        raise CallsError("policy_version_not_reviewed")
    return {entry.exposed_name: entry for entry in release.policy.tools}


def wire_call(entry: Any, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Upstream tool name and arguments for one public call.

    Mirrors the dispatch path (``upstream_read_gateway.py``, "dispatch_arguments"):
    the 8.6 read adapter, then the 9.x module the gateway's ``_reads`` selects
    (9.2 for an entry naming a 9.2.0 adapter token, else 9.1), then the
    blueprint and platform adapters. HAMCP-437 I2a-D: 9.2.0 is the only
    reviewed row, so the 9.2 selection is what dispatch now uses.
    """
    from ha_mcp_engineering.providers import upstream_blueprint, upstream_platform_reads
    from ha_mcp_engineering.providers import upstream_reads_8_6, upstream_reads_9_1
    from ha_mcp_engineering.providers import upstream_reads_9_2

    if entry.classification == "unsupported":
        raise CallsError("tool_not_dispatched")
    if upstream_reads_9_2.names_adapter(entry) is None:
        reads = upstream_reads_9_1
    elif upstream_reads_9_1.names_adapter(entry) is not None:
        raise CallsError("read_adapter_release_ambiguous")
    else:
        reads = upstream_reads_9_2
    built = reads.read_arguments(entry, upstream_reads_8_6.read_arguments(entry, arguments))
    if upstream_blueprint.is_blueprint_adapter(entry):
        built = upstream_blueprint.read_arguments(arguments)
    if upstream_platform_reads.is_platform_adapter(entry):
        built = upstream_platform_reads.read_arguments(entry, arguments, stats_precheck=False)
    return entry.upstream_name, dict(built)


def yaml_wire_call(version: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The closed YAML reader shape of one reviewed version."""
    from ha_mcp_engineering.providers import upstream_yaml_reader as reader

    bindings = [binding for binding in reader.ADAPTER_TOKENS.values()
                if binding.version == version and binding.tool_name == reader.TOOL_NAME]
    if len(bindings) != 1:
        raise CallsError("yaml_binding_not_reviewed")
    entry = reader.synthesized_policy_entry(bindings[0])
    return reader.TOOL_NAME, reader.upstream_arguments(
        reader.validate_arguments(arguments), entry=entry)


def plan(policy_version: str, groups: list[str], seed: dict[str, Any]) -> list[dict[str, Any]]:
    """Every selected case with its resolved public and wire arguments."""
    from ha_mcp_engineering.providers.upstream_history_9_2 import HistoryTimeOffsetRequired

    entries = policy_entries(policy_version)
    planned = []
    for case in CASES:
        if case["group"] not in groups:
            continue
        arguments = resolve(case["arguments"], seed)
        kind = case.get("kind", "public")
        built_by = ("harness, sent as written" if kind == "upstream"
                    else "adapter code, policy " + policy_version)
        if kind == "public":
            entry = entries.get(case["tool"])
            if entry is None:
                raise CallsError("tool_not_in_reviewed_policy")
            try:
                upstream_tool, wire = wire_call(entry, arguments)
            except HistoryTimeOffsetRequired:
                # HAMCP-437 I2a-D: Engineering refuses this time before dispatch
                # on 9.2.0 (HAMCP-371 S9). The raw capture still records what
                # upstream does with it, using the unchecked 9.1 row that 9.2.0
                # runs after its time check (upstream_history_9_2.py
                # dispatch_arguments), as the committed s6 receipts were built.
                from ha_mcp_engineering.providers import upstream_history_9_1
                upstream_tool = entry.upstream_name
                wire = upstream_history_9_1.dispatch_arguments(arguments)
                built_by += "; refused before dispatch by Engineering " + HISTORY_REFUSAL
        elif kind == "yaml":
            upstream_tool, wire = yaml_wire_call(policy_version, arguments)
        else:
            upstream_tool, wire = case["tool"], arguments
        planned.append({"id": case["id"], "group": case["group"], "kind": kind,
                        "public_tool": case["tool"], "public_arguments": arguments,
                        "upstream_tool": upstream_tool, "wire_arguments": wire,
                        "arguments_built_by": built_by,
                        "new_session": bool(case.get("new_session")),
                        "idle": bool(case.get("idle"))})
    return planned


class Session:
    """One streamable-HTTP session, opened with the reviewed client handshake."""

    def __init__(self, endpoint: str, exchange: Any = raw.exchange) -> None:
        self.endpoint, self.exchange, self.next_id = endpoint, exchange, 100
        self.initialize = exchange(endpoint, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": raw.REQUESTED_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": dict(raw.CLIENT_INFO)}})
        result = (self.initialize.get("response") or {}).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise CallsError("initialize_failed")
        self.protocol = result["protocolVersion"]
        self.session_id = next((value for name, value in self.initialize["response_headers"]
                                if name.lower() == "mcp-session-id"), None)
        self.initialized = exchange(
            endpoint, {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=self.session_id, protocol_version=self.protocol)
        if self.initialized["status"] not in {200, 202, 204}:
            raise CallsError("initialized_notification_refused")

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        return self.exchange(
            self.endpoint,
            {"jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
             "params": {"name": tool, "arguments": arguments}},
            session_id=self.session_id, protocol_version=self.protocol)

    def close(self) -> dict[str, Any] | None:
        if self.session_id is None:
            return None
        return self.exchange(self.endpoint, None, method="DELETE",
                             session_id=self.session_id, protocol_version=self.protocol)


def summary(record: dict[str, Any]) -> dict[str, Any]:
    """Transport facts of one stored exchange; no payload content."""
    record = record or {}
    response = record.get("response") if isinstance(record.get("response"), dict) else {}
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    error = response.get("error") if isinstance(response.get("error"), dict) else None
    return {"http_status": record.get("status"), "content_type": record.get("content_type"),
            "body_bytes": record.get("body_bytes"),
            "response_header_names": sorted(
                name.lower() for name, _ in record.get("response_headers") or []),
            "is_error": result.get("isError") if result else None,
            "has_structured_content": "structuredContent" in result,
            "json_rpc_error_code": error.get("code") if error else None}


def run(endpoint: str, planned: list[dict[str, Any]], out_dir: Path, *, idle_seconds: float,
        exchange: Any = raw.exchange, sleep: Any = time.sleep) -> dict[str, Any]:
    """Send every planned call and store each exchange raw; returns the index."""
    out_dir.mkdir(parents=True, exist_ok=False)
    session = Session(endpoint, exchange)
    sessions = [session]
    (out_dir / "_initialize.json").write_bytes(raw.serialize(session.initialize))
    index = []
    for case in planned:
        active, record, failure = session, None, None
        started = time.monotonic()
        try:
            if case["new_session"]:
                active = Session(endpoint, exchange)
                sessions.append(active)
            if case["idle"]:
                sleep(idle_seconds)
                started = time.monotonic()
            record = active.call(case["upstream_tool"], case["wire_arguments"])
        except (raw.ObservationError, CallsError) as exc:
            # A call that could not be exchanged is a result, stored as its fixed token.
            failure = str(exc)
        seconds = round(time.monotonic() - started, 3)
        stored = raw.serialize({"calls_format_version": CALLS_FORMAT_VERSION, **case,
                                "exchange": record, "exchange_failure": failure})
        (out_dir / (case["id"] + ".json")).write_bytes(stored)
        index.append({**case, "idle_seconds_before": idle_seconds if case["idle"] else 0,
                      "session": sessions.index(active), "seconds": seconds,
                      "file": case["id"] + ".json",
                      "sha256": hashlib.sha256(stored).hexdigest(),
                      "exchange_failure": failure, **summary(record)})
    closed = []
    for item in sessions:
        try:
            closed.append((item.close() or {}).get("status"))
        except raw.ObservationError:
            closed.append("peer_exchange_failed")
    return {"calls_format_version": CALLS_FORMAT_VERSION, "endpoint": endpoint,
            "negotiated_protocol_version": session.protocol,
            "session_header_present": session.session_id is not None,
            "sessions_opened": len(sessions),
            "session_delete_status": closed,
            "calls": index}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", required=True)
    # Absent only for groups with no seed reference (``review_probes``); a
    # seed reference without it is ``seed_value_missing``.
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--idle-seconds", type=float, default=75.0)
    args = parser.parse_args(argv)
    groups = args.groups.split(",")
    if not groups or any(group not in GROUPS for group in groups):
        raise SystemExit("unknown group")
    seed = json.loads(args.seed.read_text(encoding="utf-8")) if args.seed else {}
    planned = plan(args.policy, groups, seed)
    index = run(args.endpoint, planned, args.out, idle_seconds=args.idle_seconds)
    (args.out / "_index.json").write_bytes(raw.serialize(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
