"""Bounded, non-executing proof of the reviewed caller-owned retry transition.

This is a closed configuration grammar, not a template or policy interpreter.
It grants no physical dispatch, inverse, or authority to unmarked older plans.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .normalize import stable_hash
from .resources import RESOURCE_NORMALIZATION_VERSION, normalize_resource_config, resource_fingerprint

MODEL = "configuration-retry-transfer-v1"
TRIGGER = "proved_configuration_retry_transfer"
RESPONSE = "hamcp135-garage-attempt-v1"
MAX_NODES = 16000
MAX_DEPTH = 40
MAX_BYTES = 400000


class ProofRefused(ValueError):
    """A complete supported proof was not established."""


def require(value: bool) -> None:
    if not value:
        raise ProofRefused("configuration transformation not proved")


def bounded(value: Any) -> None:
    stack = [(value, 0)]
    nodes = 0
    size = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        require(nodes <= MAX_NODES and depth <= MAX_DEPTH)
        if isinstance(item, dict):
            require(nodes + len(stack) + 2 * len(item) <= MAX_NODES)
            require(all(isinstance(k, str) for k in item))
            stack.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif isinstance(item, list):
            require(nodes + len(stack) + len(item) <= MAX_NODES)
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str):
            require(len(item) <= MAX_BYTES - size)
            size += len(item.encode("utf-8"))
            require(size <= MAX_BYTES)
        else:
            require(item is None or type(item) in (bool, int))


def literal(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 4096 and not any(x in value for x in ("{{", "{%", "{#"))


def entity(value: Any, domain: str) -> str:
    require(isinstance(value, str) and re.fullmatch(re.escape(domain) + r"\.[a-z0-9_]+", value) is not None)
    return value


def at(value: Any, path: tuple) -> Any:
    for key in path:
        value = value[key]
    return value


def state(target: str, value: Any, duration: Any = None) -> dict:
    result = {"condition": "state", "entity_id": target, "state": value}
    if duration is not None:
        result["for"] = duration
    return result


def template(value: str) -> dict:
    return {"condition": "template", "value_template": value}


def response(outcome: str, sent: bool) -> dict:
    return {"contract": RESPONSE, "outcome": outcome, "service_call": "returned" if sent else "not_sent"}


RESPONSES = [response("already_closed", False), response("not_open", False), response("closed", True), response("open_after_timeout", True), response("unverified", True)]
TIMEOUT = response("open_after_timeout", True)


def timeout_result(variable: str) -> dict:
    return template("{{ " + variable + " is mapping and " + variable + " == " + repr(TIMEOUT) + " }}")


def recognized(variable: str) -> dict:
    return template("{{ " + variable + " is mapping and " + variable + " in " + repr(RESPONSES) + " }}")


def scrub_reports(value: Any, recipient: str, cover: str) -> Any:
    """Ignore only literal report prose and stop explanations, never controls.

    This is used only on newly introduced regions. Unchanged surrounding
    actions (including old reporting templates) must compare exactly.
    """
    if isinstance(value, list):
        return [scrub_reports(v, recipient, cover) for v in value]
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    if result.get("action") in {recipient, "logbook.log"}:
        data = result.get("data")
        require(isinstance(data, dict))
        fields = {"title", "message"} if result["action"] == recipient else {"name", "message", "entity_id"}
        require(set(data) == fields)
        for key in fields - {"entity_id"}:
            require(literal(data[key]))
            data[key] = "text"
        if "entity_id" in data:
            require(data["entity_id"] == cover)
    if "stop" in result:
        require(literal(result["stop"]))
        result["stop"] = "stop"
    return {k: scrub_reports(v, recipient, cover) for k, v in result.items()}


def reports(recipient: str, cover: str, continued: bool = True) -> list:
    extra = {"continue_on_error": True} if continued else {}
    return [
        {"action": recipient, "data": {"title": "text", "message": "text"}, **extra},
        {"action": "logbook.log", "data": {"name": "text", "message": "text", "entity_id": cover}, **extra},
    ]


def stop() -> dict:
    return {"stop": "stop"}


def finish(outcome: str, sent: bool, notices: list) -> list:
    return [{"variables": {"hamcp135_result": response(outcome, sent)}}, *notices,
            {"stop": "stop", "response_variable": "hamcp135_result"}]


def wait_event(cover: str) -> dict:
    return {"wait_for_trigger": [{"trigger": "state", "entity_id": cover, "to": "closed"}], "timeout": "00:00:30", "continue_on_timeout": True}


def script_baseline(before: dict) -> tuple[str, str]:
    require(before.get("mode") == "single")
    seq = before["sequence"]
    cover = entity(seq[0]["target"]["entity_id"], "cover")
    recipient = entity(seq[2]["choose"][0]["sequence"][0]["action"], "notify")
    close = {"action": "cover.close_cover", "target": {"entity_id": cover}}
    report = reports(recipient, cover, False)
    choice = {"conditions": [state(cover, "closed")], "sequence": report}
    expected = [close, wait_event(cover), {"choose": [choice], "default": [close, wait_event(cover), {"choose": [choice], "default": report}]}]
    require(scrub_reports(seq, recipient, cover) == expected)
    return cover, recipient


def metadata(before: dict, after: dict, body: str) -> None:
    # Registry/identity fields remain exact; only a literal description changes.
    require(literal(after.get("description", "")))
    require({k: v for k, v in before.items() if k not in {body, "description"}} ==
            {k: v for k, v in after.items() if k not in {body, "description"}})


def prove_script(before: dict, after: dict, *, full: bool) -> tuple[str, str]:
    cover, recipient = script_baseline(before)
    metadata(before, after, "sequence")
    notices = reports(recipient, cover)
    if not full:
        expected = copy.deepcopy(before["sequence"])
        expected[2]["default"] = reports(recipient, cover, False)
        # First close, event wait and successful branch must remain exact.
        require(after["sequence"][:2] == before["sequence"][:2])
        require(after["sequence"][2]["choose"] == before["sequence"][2]["choose"])
        require(scrub_reports(after["sequence"], recipient, cover) == scrub_reports(expected, recipient, cover))
    else:
        close_path = [before["sequence"][0],
            {"wait_template": "{{ is_state('" + cover + "', 'closed') }}", "timeout": "00:00:30", "continue_on_timeout": True},
            {"choose": [
                {"conditions": [state(cover, "closed")], "sequence": finish("closed", True, notices)},
                {"conditions": [state(cover, "open"), template("{{ wait.completed is sameas false }}")], "sequence": finish("open_after_timeout", True, notices)},
            ], "default": finish("unverified", True, notices)}]
        expected = [{"choose": [
            {"conditions": [state(cover, "closed")], "sequence": finish("already_closed", False, [])},
            {"conditions": [state(cover, "open")], "sequence": close_path},
        ], "default": finish("not_open", False, notices)}]
        require(scrub_reports(after["sequence"], recipient, cover) == expected)
        success = after["sequence"][0]["choose"][1]["sequence"][2]["choose"][0]["sequence"][1]
        require(success == {**before["sequence"][2]["choose"][0]["sequence"][0], "continue_on_error": True})
    return cover, recipient


def call_block(script: str, policy: list, recipient: str, cover: str) -> list:
    log = reports(recipient, cover)[1]
    call = lambda var: {"action": script, "response_variable": var, "continue_on_error": True}
    return [
        {"variables": {"hamcp135_first": None, "hamcp135_second": None}},
        {"choose": [{"conditions": policy, "sequence": [call("hamcp135_first")]}], "default": [log, stop()]},
        {"choose": [
            {"conditions": [timeout_result("hamcp135_first"), {"condition": "and", "conditions": policy}], "sequence": [call("hamcp135_second"),
                {"choose": [{"conditions": [recognized("hamcp135_second")], "sequence": [log, stop()]}], "default": [*reports(recipient, cover), stop()]}]},
            {"conditions": [recognized("hamcp135_first")], "sequence": [
                {"choose": [{"conditions": [timeout_result("hamcp135_first")], "sequence": [log]}], "default": []}, stop()]},
        ], "default": [*reports(recipient, cover), stop()]},
    ]


def native_policy(conditions: list) -> None:
    require(isinstance(conditions, list) and 1 <= len(conditions) <= 8)
    for condition in conditions:
        if condition.get("condition") == "sun":
            require(condition == {"condition": "sun", "after": "sunset", "before": "sunrise"})
            continue
        require(condition.get("condition") == "state")
        require(set(condition) <= {"condition", "entity_id", "state", "for"})
        require(isinstance(condition["entity_id"], str) and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", condition["entity_id"]) is not None)
        require(condition["state"] in ("on", "off", "open", "not_home", ["off"]))
        if "for" in condition:
            require(condition["for"] in ("00:02:00", "00:05:00", "00:10:00", "00:30:00", {"hours": 0, "milliseconds": 0, "minutes": 5, "seconds": 0}))


def original_action_grammar(sequence: list, script: str, recipient: str) -> None:
    """Reject hidden/dynamic calls or repeat/parallel control in old callers."""
    require(isinstance(sequence, list))
    for step in sequence:
        require(isinstance(step, dict))
        if "action" in step:
            require(step["action"] in {script, recipient, "logbook.log"})
            require(set(step) <= {"action", "data", "continue_on_error"})
        elif "choose" in step:
            require(set(step) <= {"choose", "default"})
            for branch in step["choose"]:
                require(set(branch) == {"conditions", "sequence"})
                original_action_grammar(branch["sequence"], script, recipient)
            original_action_grammar(step.get("default", []), script, recipient)
        elif "wait_for_trigger" in step:
            require(set(step) == {"wait_for_trigger", "timeout", "continue_on_timeout"})
        else:
            require(step.get("condition") in {"state", "sun"})
            require(set(step) <= {"condition", "entity_id", "state", "for", "after", "before"})


def prove_callers(operations: list, cover: str, recipient: str, script: str) -> list:
    # Roles are structural positions in the reviewed composition, not names/IDs.
    paths = [
        [("actions", 0)],
        [("actions", 0, "choose", 0, "sequence", 0), ("actions", 0, "default", 5)],
        [("actions", 0, "choose", 0, "sequence", 0)],
        [("actions", 0, "choose", 0, "sequence", 0, "choose", 0, "sequence", 0)],
    ]
    cleaner = operations[1].current_config
    c = cleaner["conditions"]
    suppress = entity(c[0]["entity_id"], "input_boolean")
    away = entity(c[2]["entity_id"], "input_boolean")
    family = entity(c[3]["entity_id"], "group")
    cleaner_mode = entity(cleaner["triggers"][0]["entity_id"], "input_boolean")
    occupancy = entity(cleaner["actions"][0]["choose"][0]["conditions"][0]["entity_id"], "binary_sensor")
    bed = entity(operations[3].current_config["actions"][0]["choose"][0]["conditions"][1]["entity_id"], "binary_sensor")
    require(len({suppress, away, cleaner_mode}) == 3 and bed != occupancy)
    expected_policies = [
        [state(cover, "open")],
        [state(suppress, "off"), state(cover, "open"), state(away, "on"), state(family, "not_home"), state(cleaner_mode, "off"),
         state(occupancy, ["off"], {"hours": 0, "milliseconds": 0, "minutes": 5, "seconds": 0})],
        [state(cover, "open", "00:30:00"), state(away, "on"), state(family, "not_home"), state(cleaner_mode, "off"), state(occupancy, "off", "00:05:00")],
        [{"condition": "sun", "after": "sunset", "before": "sunrise"}, state(cover, "open", "00:30:00"), state(bed, "on", "00:02:00"), state(occupancy, "off", "00:10:00")],
    ]
    event = operations[0].current_config["triggers"]
    require(len(event) == 1 and set(event[0]) == {"trigger", "event_type", "event_data"})
    require(event[0]["trigger"] == "event" and event[0]["event_type"] == "mobile_app_notification_action")
    require(set(event[0]["event_data"]) == {"action"} and literal(event[0]["event_data"]["action"]))
    sites = []
    for role, operation in enumerate(operations):
        before, after = operation.current_config, operation.proposed_config
        require(before.get("mode") == ("restart" if role == 1 else "single"))
        metadata(before, after, "actions")
        original_action_grammar(before["actions"], script, recipient)
        policy = copy.deepcopy(before["conditions"])
        if role == 0:
            require(policy == [state(cover, "open")])
        else:
            branch_policy = before["actions"][0]["choose"][0]["conditions"]
            if role == 1:
                trigger = before["triggers"][0]
                require(trigger == {"trigger": "state", "entity_id": entity(trigger["entity_id"], "input_boolean"), "to": "off"})
                policy += [state(trigger["entity_id"], "off")]
                require(len(before["conditions"]) == 4 and before["conditions"][0]["state"] == "off")
                require(before["conditions"][1] == state(cover, "open"))
                require(before["actions"][0]["default"][0] == {
                    "wait_for_trigger": [{"entity_id": branch_policy[0]["entity_id"], "for": "00:05:00", "to": "off", "trigger": "state"}],
                    "timeout": "00:10:00", "continue_on_timeout": False})
            policy += copy.deepcopy(branch_policy)
            require(len(policy) == (6 if role == 1 else 5 if role == 2 else 4))
            require(any(c == state(cover, "open", "00:30:00") for c in policy) if role > 1 else True)
        native_policy(policy)
        require(policy == expected_policies[role])
        restored = copy.deepcopy(after)
        for path in paths[role]:
            old_seq = at(before, path[:-1])
            new_seq = at(after, path[:-1])
            index = path[-1]
            require(len(old_seq) == index + 1 and old_seq[index] == {"action": script})
            require(len(new_seq) == index + 3)
            require(scrub_reports(new_seq[index:], recipient, cover) == call_block(script, policy, recipient, cover))
            at(restored, path[:-1])[index:] = [copy.deepcopy(old_seq[index])]
            sites.append({"operation_id": operation.operation_id, "path": list(path), "policy_hash": stable_hash(policy)})
        restored["description"] = before.get("description", "")
        require(restored == before)
        # A sixth site / pre-existing script invocation outside the replaced
        # terminal regions cannot acquire an unreviewed per-run retry budget.
        require(count_calls(before, script) == len(paths[role]))
    return sites


def count_calls(value: Any, script: str) -> int:
    if isinstance(value, dict):
        return int(value.get("action", value.get("service")) == script) + sum(count_calls(v, script) for v in value.values())
    if isinstance(value, list):
        return sum(count_calls(v, script) for v in value)
    return 0


def member(operation: Any) -> dict:
    return {"operation_id": operation.operation_id, "order": operation.order, "depends_on": operation.depends_on,
            "resource_type": operation.resource_type, "target_id": operation.target_id,
            "current_state_fingerprint": operation.current_state_fingerprint, "proposed_config_hash": operation.proposed_config_hash,
            "raw_before": stable_hash(operation.current_config), "raw_after": stable_hash(operation.proposed_config),
            "normalization_version": operation.normalization_version}


def derive_proof(operations: list) -> dict | None:
    """Recompute the entire proof; unsupported inputs receive no eligibility."""
    try:
        require(len(operations) in (1, 5))
        identities = set()
        for i, op in enumerate(operations):
            require(op.resource_type == ("script" if i == 0 else "automation") and op.action == "update" and op.helper_type is None)
            require(op.order == i and op.depends_on == ([] if i == 0 else [operations[i - 1].operation_id]))
            require(op.validation_results.get("valid") is True and op.normalization_version == RESOURCE_NORMALIZATION_VERSION)
            require(isinstance(op.current_config, dict) and isinstance(op.proposed_config, dict))
            bounded([op.current_config, op.proposed_config])
            require((op.resource_type, op.target_id) not in identities)
            identities.add((op.resource_type, op.target_id))
            require(op.normalized_current_config == normalize_resource_config(op.resource_type, op.current_config))
            require(op.normalized_proposed_config == normalize_resource_config(op.resource_type, op.proposed_config))
            require(op.current_state_fingerprint == resource_fingerprint(op.resource_type, op.current_config))
            require(op.proposed_config_hash == stable_hash(op.normalized_proposed_config))
        cover, recipient = prove_script(operations[0].current_config, operations[0].proposed_config, full=len(operations) == 5)
        script = entity("script." + operations[0].target_id, "script")
        sites = prove_callers(operations[1:], cover, recipient, script) if len(operations) == 5 else []
        proof = {"model": MODEL, "kind": "caller_owned_retry" if sites else "minimal_retry_removal",
                 "members": [member(op) for op in operations], "sites": sites,
                 "positive_call_gates": 11 if sites else 0,
                 "limitations": ["non_atomic_configuration_writes", "no_runtime_activation_proof", "no_supported_script_inverse", "no_global_attempt_budget"]}
        return {**proof, "digest": stable_hash(proof)}
    except (ProofRefused, KeyError, IndexError, TypeError, ValueError, RecursionError, OverflowError):
        return None


def markers(operation: Any) -> list:
    return [e for e in operation.risk.evidence if isinstance(e, dict) and e.get("trigger") == TRIGGER]


def bound_proof(operations: list) -> dict | None:
    """Unmarked historical plans are never upgraded by reevaluation."""
    if not operations or not all(len(markers(op)) == 1 for op in operations):
        return None
    proof = derive_proof(operations)
    if proof is None or any(markers(op)[0] != {"trigger": TRIGGER, "proof": proof} for op in operations):
        return None
    return proof


def local_member_marked(operation: Any) -> bool:
    """Member-only review classification; never whole-plan admission."""
    entries = markers(operation)
    if len(entries) != 1:
        return False
    proof = entries[0].get("proof")
    if not isinstance(proof, dict) or not isinstance(proof.get("members"), list):
        return False
    try:
        bounded(proof)
        require(proof.get("model") == MODEL)
        require(proof.get("digest") == stable_hash({k: v for k, v in proof.items() if k != "digest"}))
        return member(operation) in proof["members"]
    except (ProofRefused, TypeError, ValueError, RecursionError, OverflowError):
        return False
