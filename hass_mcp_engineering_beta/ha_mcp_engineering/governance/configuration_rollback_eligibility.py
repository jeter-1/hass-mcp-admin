"""Bounded inverse evidence for a durably written HAMCP-135 full transfer.

Structural proof is necessary, never sufficient: the runtime reloads the exact
source plan, task, manifest and five child records at authority boundaries.
No public request field creates this evidence. The forward proof is unchanged.
"""
from __future__ import annotations

import copy
from typing import Any

from .configuration_eligibility import (
    ProofRefused, bound_proof, bounded, member, require,
)
from .normalize import stable_hash
from .resources import RESOURCE_NORMALIZATION_VERSION, normalize_resource_config, resource_fingerprint

MODEL = "hamcp135-exact-inverse-v1"
TRIGGER = "proved_hamcp135_exact_inverse"
DISCLOSURE = (
    "Restoring the shared script returns the original internal retry and its known lack of fresh "
    "caller revalidation. It does not repair that weakness or undo physical commands. "
    "Configuration restoration is non-atomic and does not prove reload or run settlement."
)


def markers(operation: Any) -> list:
    return [v for v in operation.risk.evidence if isinstance(v, dict) and v.get("trigger") == TRIGGER]


def proof_id(snapshot: dict) -> str:
    # One new attempt per exact source task, also when an old prohibited inverse
    # is cached. Deterministic lookup recovers plan-before-backlink crashes.
    return stable_hash({"model": MODEL, "source_plan_id": snapshot["plan_id"],
                        "source_plan_hash": snapshot["plan_hash"], "task_id": snapshot["task_id"]})[:32]


def source_snapshot(runtime: Any, source: Any) -> dict:
    """Read only bounded, identified durable records; never operation status fallback."""
    from .policy import policy_snapshot_matches
    from .task_models import TERMINAL_TASK_STATES

    proof = bound_proof(source.operations)
    require(source.contract_version == 2 and proof is not None and proof["kind"] == "caller_owned_retry")
    require(policy_snapshot_matches(source))
    task = runtime.service.task_repository.get_for_plan(source.plan_id)
    require(task is not None and task.state in TERMINAL_TASK_STATES)
    plan_hash = runtime.service.plan_hash(source)
    require(task.plan_id == source.plan_id and task.plan_hash == plan_hash)
    require(task.legacy_projection.get("execution_authority") == "f3_child_sequence")
    declarations, records = runtime._validate_sequence_state(task)
    require(len(declarations) == len(records) == 5)
    manifest = runtime.children.manifest_for_task(task.task_id)
    require(manifest is not None and manifest["sequence_hash"] == task.legacy_projection["sequence_hash"])
    child_ids = {d["child_id"] for d in declarations}
    require(not any(lock.task_id in child_ids for lock in runtime.locks.records()))
    prefix = 0
    ended = False
    receipts = []
    for index, (op, declaration, record) in enumerate(zip(source.operations, declarations, records, strict=True)):
        require(declaration["plan_id"] == source.plan_id and declaration["plan_hash"] == plan_hash)
        require(declaration["public_task_id"] == task.task_id and declaration["plan_contract_version"] == 2)
        require(declaration["operation_id"] == op.operation_id and declaration["operation_ordinal"] == index)
        require(declaration["operation_dependency_ids"] == op.depends_on)
        require(declaration["target_type"] == op.resource_type and declaration["target_id"] == op.target_id)
        require(declaration["capability_id"] == f"update_{op.resource_type}_configuration")
        require(declaration["request_id"] == task.execution_request_id)
        require(declaration["approval_bundle_hash"] == declarations[0]["approval_bundle_hash"])
        require(declaration["adapter_id"] == "configuration_operation")
        if record is not None:
            record.validate()
            require(record.identity["plan_id"] == source.plan_id and record.identity["task_id"] == declaration["child_id"])
            require(record.identity["request_id"] == declaration["request_id"] and record.identity["attempt_id"] == declaration["attempt_id"])
            require(record.target == {"target_type": op.resource_type, "target_id": op.target_id})
            require(record.prepared_operation_hash == declaration["prepared_operation_hash"])
            require(record.adapter_id == declaration["adapter_id"] and record.operation == declaration["capability_id"])
            require(record.terminal)
            if record.dispatch_intent is not None:
                require(not ended and record.dispatch_count == 1 and record.normalized_outcome == "succeeded_verified")
                require(record.evidence.get("resulting_state_fingerprint") == op.proposed_config_hash)
                prefix += 1
            else:
                # A no-op at a proposed body is not evidence of our forward write.
                require(record.dispatch_count == 0 and record.normalized_outcome != "succeeded_verified")
                ended = True
        else:
            ended = True
        receipts.append({"declaration_hash": declaration["declaration_hash"],
                         "record_hash": None if record is None else stable_hash(record.to_dict())})
    projected = [{"child_execution_id": d["child_id"], "operation_id": d["operation_id"],
                  "ordinal": d["operation_ordinal"], "state": "not_started" if r is None else r.state,
                  "normalized_outcome": None if r is None else r.normalized_outcome,
                  "dispatch_count": 0 if r is None else r.dispatch_count,
                  "terminal": False if r is None else r.terminal}
                 for d, r in zip(declarations, records, strict=True)]
    require(task.verification_summary.get("children") == projected)
    require(task.verification_summary.get("completed_operation_ids") ==
            [op.operation_id for op in source.operations[:prefix]])
    require((task.state.value == "succeeded_verified") == (prefix == 5))
    if prefix:
        require(source.approval.state.value == "consumed" and source.approval.consumed_at is not None)
        runtime._historical_approval_witnesses(source, task, declarations[:prefix])
    task_binding = {key: getattr(task, key) for key in
                    ("plan_id", "plan_hash", "operation", "target", "execution_request_id", "idempotency_key", "approval_reference")}
    return {"plan_id": source.plan_id, "plan_hash": plan_hash, "task_id": task.task_id,
            "task_binding": stable_hash(task_binding), "sequence_hash": manifest["sequence_hash"],
            "forward_proof_digest": proof["digest"], "prefix": prefix,
            "members": [member(op) for op in source.operations], "receipts": receipts}


def _validate(proof: dict, operations: list, plan_id: str | None = None) -> None:
    bounded(proof)
    require(set(proof) == {"model", "inverse_plan_id", "source", "selected", "observed", "complete", "members", "digest"})
    require(proof["model"] == MODEL)
    require(proof["digest"] == stable_hash({k: v for k, v in proof.items() if k != "digest"}))
    source = proof["source"]
    require(proof["inverse_plan_id"] == proof_id(source))
    if plan_id is not None:
        require(proof["inverse_plan_id"] == plan_id)
    require(len(source["members"]) == len(source["receipts"]) == len(proof["observed"]) == 5)
    prefix, selected = source["prefix"], proof["selected"]
    require(type(prefix) is int and 1 <= prefix <= 5)
    require(isinstance(selected, list) and 1 <= len(selected) <= prefix)
    require(all(type(i) is int and 0 <= i < prefix for i in selected))
    require(selected == sorted(set(selected), reverse=True))
    require(type(proof["complete"]) is bool)
    if proof["complete"]:
        require(selected == list(reversed(range(prefix))))
        require(proof["observed"] == [m["proposed_config_hash"] if i < prefix else m["current_state_fingerprint"]
                                      for i, m in enumerate(source["members"])])
    else:
        require(0 not in selected)
    require(len(operations) == len(selected))
    require(proof["members"] == [member(op) for op in operations])
    for ordinal, (op, index) in enumerate(zip(operations, selected, strict=True)):
        old = source["members"][index]
        bounded([op.current_config, op.proposed_config])
        require(op.order == ordinal and op.depends_on == ([] if ordinal == 0 else [operations[ordinal-1].operation_id]))
        require(op.resource_type == old["resource_type"] and op.target_id == old["target_id"])
        require(op.action == "update" and op.helper_type is None)
        require(op.normalization_version == RESOURCE_NORMALIZATION_VERSION and op.validation_results.get("valid") is True)
        require(stable_hash(op.proposed_config) == old["raw_before"])
        require(op.normalized_proposed_config == normalize_resource_config(op.resource_type, op.proposed_config))
        require(op.proposed_config_hash == stable_hash(op.normalized_proposed_config) == old["current_state_fingerprint"])
        require(op.normalized_current_config == normalize_resource_config(op.resource_type, op.current_config))
        require(op.current_state_fingerprint == resource_fingerprint(op.resource_type, op.current_config)
                == old["proposed_config_hash"] == proof["observed"][index])


def build(plan_id: str, operations: list, context: dict) -> dict:
    proof = {"model": MODEL, "inverse_plan_id": plan_id, "source": copy.deepcopy(context["snapshot"]),
             "selected": list(context["selected"]), "observed": list(context["observed"]),
             "complete": context["complete"], "members": [member(op) for op in operations]}
    proof["digest"] = stable_hash(proof)
    _validate(proof, operations, plan_id)
    return proof


def bound(plan: Any) -> dict | None:
    try:
        if not plan.operations or not all(len(markers(op)) == 1 for op in plan.operations):
            return None
        proof = markers(plan.operations[0])[0]["proof"]
        require(all(markers(op)[0] == {"trigger": TRIGGER, "proof": proof} for op in plan.operations))
        _validate(proof, plan.operations, plan.plan_id)
        return proof
    except (ProofRefused, KeyError, IndexError, TypeError, ValueError, AttributeError, RecursionError):
        return None


def local_member_marked(operation: Any) -> bool:
    # Only review classification; bound(plan) and durable source validation are
    # additionally mandatory. No marker alone grants execution authority.
    try:
        entries = markers(operation)
        require(len(entries) == 1)
        proof = entries[0]["proof"]
        bounded(proof)
        require(proof["model"] == MODEL and proof["digest"] == stable_hash({k:v for k,v in proof.items() if k != "digest"}))
        return member(operation) in proof["members"]
    except (ProofRefused, KeyError, TypeError, ValueError, RecursionError):
        return False
