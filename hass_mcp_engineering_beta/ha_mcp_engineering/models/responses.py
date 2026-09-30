"""Typed, JSON-serializable response contracts for migrated beta tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any

from ..request_context import current_request_id

MAX_CHARS = 60_000


@dataclass
class Timing:
    total_ms: float
    tool_ms: float | None = None
    home_assistant_ms: float | None = None
    home_assistant_cumulative_attempt_ms: float | None = None
    home_assistant_wall_clock_span_ms: float | None = None
    home_assistant_request_count: int = 0
    upstream_attempted: bool = False
    upstream_ms: float = 0.0
    upstream_wall_clock_span_ms: float = 0.0
    upstream_request_count: int = 0
    provider_operations_concurrent: bool = False
    retry_count: int = 0
    timeout_occurred: bool = False


@dataclass
class SuccessResponse:
    operation: str
    summary: str
    data: Any = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: Timing | dict[str, Any] = field(default_factory=lambda: Timing(total_ms=0.0))
    request_id: str = field(default_factory=current_request_id)
    success: bool = field(default=True, init=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, limit: int = MAX_CHARS) -> str:
        return dump_json(self.as_dict(), limit=limit)


@dataclass
class FailureResponse:
    operation: str
    error: str
    error_code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timing: Timing | dict[str, Any] = field(default_factory=lambda: Timing(total_ms=0.0))
    request_id: str = field(default_factory=current_request_id)
    success: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, limit: int = MAX_CHARS) -> str:
        return dump_json(self.as_dict(), limit=limit)


# Compatibility alias retained for code that imported the scaffold model.
EngineeringResponse = SuccessResponse


# Reconciliation fields are retained only through known response branches.
# Untrusted configuration/evidence dictionaries are not interpreted as receipts.
_RECEIPT_BRANCHES = frozenset({
    "data", "details", "plan", "execution_task", "operational", "dispatch",
    "verification", "verification_summary", "target", "approval", "risk",
    "policy_decision", "canonical_summary", "failure_information", "rollback",
    "provider_attempts", "children", "metadata", "routing",
})
_RECEIPT_FACTS = frozenset({
    "operation", "request_id", "success", "error", "error_code", "retryable",
    "plan_id", "plan_hash", "task_id", "task_state", "state", "status",
    "outcome", "final_outcome", "terminal_outcome", "execution_outcome",
    "provider", "provider_identity", "provider_dispatch_occurred",
    "dispatch_count", "provider_attempt_count", "provider_response_received",
    "response_received", "dispatched", "verified", "verification_status",
    "redispatch_performed", "plan_created", "applied_at", "completed_at",
    "dispatched_at", "expires_at", "approved_at", "consumed_at",
    "bound_plan_hash", "apply_allowed", "execution_eligible",
    "execution_contract_complete", "consequence_evidence_complete",
    "physical_consequence", "level", "policy_class", "fallback",
    "fallback_occurred", "target_id", "target_type", "entity_id",
    "resource_id", "resource_type", "authoritative_lifecycle_field",
    "authoritative_verification_field", "status_is_legacy",
    "execution_request_id", "reason", "reason_code", "failure_category",
    "diagnostic_code", "error_category", "operation_id", "operation_hash",
    "dispatch_intent_recorded", "redispatch_prohibited", "authorization",
})


def _receipt_paths(value: Any, path: tuple = ()) -> set[tuple]:
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            child = (*path, key)
            if (
                key in _RECEIPT_FACTS or key.endswith(("_hash", "_fingerprint"))
            ) and not isinstance(item, (dict, list)):
                paths.add(child)
            elif key in _RECEIPT_BRANCHES:
                paths.update(_receipt_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.update(_receipt_paths(item, (*path, index)))
    return paths


def _compact_json(value: Any) -> str:
    # ASCII escaping means the character bound also bounds exported UTF-8 bytes.
    return json.dumps(value, separators=(",", ":"), default=str)


def _retrieval(value: Any) -> list[dict[str, Any]]:
    """Suggest existing read-only operations, never a repeated mutation."""
    identities: dict[str, str] = {}
    for path in sorted(_receipt_paths(value), key=str):
        if path[-1] not in {"plan_id", "task_id"}:
            continue
        item = value
        for key in path:
            item = item[key]
        if isinstance(item, str):
            identities.setdefault(path[-1], item)
    reads = []
    if "task_id" in identities:
        reads.append({"tool": "get_execution_task",
                      "arguments": {"task_id": identities["task_id"]}})
    if "plan_id" in identities:
        reads.append({"tool": "get_change_plan",
                      "arguments": {"plan_id": identities["plan_id"],
                                    "page_size": 1},
                      "detail_sections": ["summary", "obligation_evidence",
                                          "downstream_profiles"]})
    return reads


def _minimal_receipt(value: dict[str, Any], limit: int) -> str:
    """Project one action's primary receipt for the smallest configured budget."""
    def mapping(item: Any) -> dict:
        return item if isinstance(item, dict) else {}

    if value.get("operation") == "get_logbook" and value.get("success") is True:
        # The native reader already bounds whole records. At small configured
        # budgets omit envelope detail, never tear apart that selected prefix.
        receipt = {key: value[key] for key in ("operation", "request_id", "success", "data")
                   if key in value}
        metadata = mapping(value.get("metadata"))
        receipt["metadata"] = {key: metadata[key] for key in ("routing", "source_coverage")
                               if key in metadata}
        routing = mapping(receipt["metadata"].get("routing"))
        receipt["metadata"]["routing"] = {key: routing[key] for key in ("provider", "fallback_occurred")
                                          if key in routing}
        coverage = receipt["metadata"].get("source_coverage")
        if isinstance(coverage, list):
            receipt["metadata"]["source_coverage"] = [
                {key: entry[key] for key in ("provider", "completeness", "fallback_occurred") if key in entry}
                for entry in coverage if isinstance(entry, dict)
            ]
        receipt["response_completeness"] = {
            "truncated": True, "reason": "response_size_limit", "limit_chars": limit,
            "notice": f"... [truncated at {limit} chars]",
        }
        rendered = _compact_json(receipt)
        if len(rendered) > limit:
            raise ValueError("response bound cannot preserve the logbook receipt")
        return rendered

    body_key = "data" if value.get("success") else "details"
    body = mapping(value.get(body_key))
    plan = mapping(body.get("plan"))
    # The task reader returns a flat task; apply returns a nested task. Use
    # either authoritative shape without deriving dispatch from attempt counts.
    task = (
        body if value.get("operation") == "get_execution_task"
        else mapping(body.get("execution_task"))
    )
    operational = mapping(plan.get("operational"))
    verification = mapping(operational.get("verification"))
    receipt = {key: value[key] for key in (
        "operation", "request_id", "success", "error", "error_code", "retryable"
    ) if key in value}
    facts = {key: body[key] for key in (
        "plan_id", "plan_hash", "task_id", "task_state", "status", "outcome",
        "terminal_outcome", "provider_dispatch_occurred", "redispatch_performed",
        "verified", "plan_created", "reason", "provider", "fallback",
        "fallback_occurred", "state", "provider_attempt_count", "dispatched_at",
        "operation_id", "operation_hash", "dispatch_intent_recorded",
        "provider_response_received", "redispatch_prohibited"
    ) if key in body and not isinstance(body[key], (dict, list))}
    for key in ("plan_id", "plan_hash"):
        if key not in facts and key in plan:
            facts[key] = plan[key]
    for key in ("task_id", "terminal_outcome", "provider_attempt_count"):
        if key not in facts and key in task:
            facts[key] = task[key]
    if "task_state" not in facts and "state" not in facts and "state" in task:
        facts["task_state"] = task["state"]
    if "status" in verification:
        facts["verification_status"] = verification["status"]
    if "provider" not in facts and "provider" in operational:
        facts["provider"] = operational["provider"]
    original_metadata = mapping(value.get("metadata"))
    routing = mapping(original_metadata.get("routing"))
    for key in ("provider", "fallback", "fallback_occurred", "task_id", "operation_id", "operation_hash"):
        if key not in facts:
            if key in original_metadata:
                facts[key] = original_metadata[key]
            elif key in routing:
                facts[key] = routing[key]
    task_verification = mapping(task.get("verification_summary"))
    if "status" in task_verification:
        facts["task_verification_status"] = task_verification["status"]
    receipt[body_key] = facts
    reads = _retrieval(value)
    completeness = {
        "truncated": True,
        # The existing routing metrics recognize this notice. Keep it inside
        # JSON rather than appending text outside the response envelope.
        "notice": f"... [truncated at {limit} chars]",
    }
    if reads:
        completeness.update({
            "reason": "response_size_limit",
            "projection": "primary_reconciliation_receipt",
            "approval_disclosures_complete": False,
            "retrieval": reads,
        })
    receipt["response_completeness"] = completeness
    output = _compact_json(receipt)
    if len(output) > limit and reads:
        # These are navigation hints, not disclosure data or tool arguments.
        # The existing reader exposes the supported detail selections itself.
        for read in reads:
            read.pop("detail_sections", None)
        # Keep the authoritative task outcome without repeating its identical
        # terminal label. The notice already explains the size limitation.
        state = facts.get("task_state", facts.get("state"))
        if state is not None and facts.get("terminal_outcome") == state:
            facts.pop("terminal_outcome", None)
        completeness.pop("reason", None)
        output = _compact_json(receipt)
    if len(output) > limit:
        raise ValueError("response bound cannot contain reconciliation identity")
    return output


def _bounded_json(output: str, limit: int) -> str:
    # Work on a decoded copy: projection must not mutate a persisted result or
    # a provider's evidence. Default serialization semantics remain unchanged.
    value = json.loads(output)
    if not isinstance(value, dict):
        value = {"data": value}
    protected = _receipt_paths(value)
    if value.get("operation") == "get_logbook" and value.get("success") is True:
        protected.add(("data",))
    omission = {
        "truncated": True,
        "reason": "response_size_limit",
        "notice": f"... [truncated at {limit} chars]",
        "limit_chars": limit,
        "original_chars": len(output),
        "omitted_paths": [],
        "omitted_path_count": 0,
        "omitted_paths_complete": True,
        "retrieval": _retrieval(value),
        "approval_disclosures_complete": False,
    }
    # This is response completeness, not provider completeness or action status.
    # Keep original metadata separate, including provider attribution.
    value["response_completeness"] = omission
    protected.add(("response_completeness",))
    size = len(_compact_json(value))

    def candidates(node: Any, prefixes: set[tuple], path: tuple = ()):
        if isinstance(node, dict):
            for key, item in node.items():
                child = (*path, key)
                if child in protected:
                    continue
                if child in prefixes:
                    yield from candidates(item, prefixes, child)
                else:
                    yield len(_compact_json(item)), node, key, child
        elif isinstance(node, list):
            # Never change list offsets while traversing it. Whole optional
            # lists are removed by their owning dictionary above.
            for index, item in enumerate(node):
                yield from candidates(item, prefixes, (*path, index))

    # Candidates are disjoint optional subtrees. Size each once per pass and
    # account for JSON punctuation and bounded omission metadata incrementally.
    # Rebuilding/serializing the remaining tree after every deletion can starve
    # the event loop on ordinary logbook lists with protected entity/state keys.
    for _ in range(2):
        prefixes = {p[:i] for p in protected for i in range(1, len(p))}
        options = sorted(candidates(value, prefixes),
                         key=lambda item: (item[0], str(item[3])), reverse=True)
        for item_size, parent, key, path in options:
            if size <= limit:
                return _compact_json(value)
            size -= item_size + len(_compact_json(key)) + 1 + (len(parent) > 1)
            del parent[key]
            previous = omission["omitted_path_count"]
            count = previous + 1
            size += len(str(count)) - len(str(previous))
            omission["omitted_path_count"] = count
            if count <= 16:
                pointer = "/" + "/".join(
                    str(part).replace("~", "~0").replace("/", "~1") for part in path
                )
                size += len(_compact_json(pointer)) + (previous > 0)
                omission["omitted_paths"].append(pointer)
            elif count == 17:
                size += 1  # JSON false is one character longer than true.
                omission["omitted_paths_complete"] = False
        if size <= limit:
            return _compact_json(value)
        # One further pass may remove secondary detail, never primary IDs.
        protected = {p for p in protected
                     if len(p) <= 2 or p[-1] in {"plan_id", "plan_hash", "task_id"}}
    return _minimal_receipt(json.loads(output), limit)


def dump_json(data: Any, limit: int = MAX_CHARS) -> str:
    if type(limit) is not int or limit < 2:
        raise ValueError("response bound must be an integer of at least two")
    output = json.dumps(data, indent=2, default=str)
    if len(output) <= limit:
        return output
    compact = _compact_json(data)
    if len(compact) <= limit:
        return compact
    return _bounded_json(output, limit)
