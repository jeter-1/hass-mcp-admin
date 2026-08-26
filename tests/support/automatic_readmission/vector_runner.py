"""Implementation-neutral contract-vector runner and reference adapter."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from typing import Any, Callable, Mapping, Protocol

from .coordinator import CapabilityAdmissionCoordinator
from .harness import OfflineUpdateHarness
from .models import (
    AdmissionDisposition,
    AuthorityBundle,
    CompatibilityModelError,
    MAX_ACTIVE_COMMITS,
    MAX_ISSUED_LEASES,
    MAX_RETIREMENT_DIAGNOSTICS,
    UpstreamSurface,
    canonical_json,
    classify_registry_refresh,
    evidence_fingerprint,
)


VECTOR_SCHEMA_VERSION = 2
MAX_VECTORS = 96
MAX_STEPS_PER_VECTOR = 64
MAX_MUTATIONS = 16


class ContractAdapter(Protocol):
    """Adapter boundary reusable by a later production coordinator."""

    def execute(self, operation: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Execute one data-defined operation and return a normalized result."""


def run_contract_suite(
    suite: Mapping[str, Any],
    adapter_factory: Callable[[], ContractAdapter],
) -> dict[str, Any]:
    """Validate and replay one bounded versioned data-only vector suite."""

    if set(suite) != {"schema_version", "foundation_fixture", "vectors"}:
        raise CompatibilityModelError("vector_suite_fields_invalid")
    if type(suite["schema_version"]) is not int or (
        suite["schema_version"] != VECTOR_SCHEMA_VERSION
    ):
        raise CompatibilityModelError("vector_schema_unsupported")
    foundation_fixture = _text(
        suite["foundation_fixture"], "vector_foundation_fixture_invalid"
    )
    vectors = _list(suite["vectors"], "vectors_invalid", MAX_VECTORS)
    if not vectors:
        raise CompatibilityModelError("vectors_invalid")
    reports = []
    vector_ids: set[str] = set()
    for raw_vector in vectors:
        vector = _mapping(raw_vector, "vector_invalid")
        vector_id = _text(vector.get("vector_id"), "vector_id_invalid")
        if vector_id in vector_ids:
            raise CompatibilityModelError("vector_id_duplicate")
        vector_ids.add(vector_id)
        reports.append(run_contract_vector(vector, adapter_factory()))
    report = {
        "vector_schema_version": VECTOR_SCHEMA_VERSION,
        "foundation_fixture": foundation_fixture,
        "vector_count": len(reports),
        "step_count": sum(item["step_count"] for item in reports),
        "matched": all(item["matched"] for item in reports),
        "mismatch_count": sum(item["mismatch_count"] for item in reports),
        "reports": reports,
    }
    canonical_json(report)
    return report


def run_contract_vector(
    vector: Mapping[str, Any],
    adapter: ContractAdapter,
) -> dict[str, Any]:
    """Compare literal expected results without exposing raw mismatched values."""

    if set(vector) != {"vector_id", "requirements", "steps"}:
        raise CompatibilityModelError("vector_fields_invalid")
    vector_id = _text(vector["vector_id"], "vector_id_invalid")
    requirements = _string_list(vector["requirements"], "vector_requirements_invalid")
    steps = _list(vector["steps"], "vector_steps_invalid", MAX_STEPS_PER_VECTOR)
    seen: set[str] = set()
    mismatches: list[dict[str, str]] = []
    for raw_step in steps:
        step = _mapping(raw_step, "vector_step_invalid")
        if set(step) != {"step_id", "operation", "arguments", "expected"}:
            raise CompatibilityModelError("vector_step_fields_invalid")
        step_id = _text(step["step_id"], "vector_step_id_invalid")
        if step_id in seen:
            raise CompatibilityModelError("vector_step_id_duplicate")
        seen.add(step_id)
        operation = _text(step["operation"], "vector_operation_invalid")
        arguments = _mapping(step["arguments"], "vector_arguments_invalid")
        expected = _mapping(step["expected"], "vector_expected_invalid")
        actual = _mapping(adapter.execute(operation, arguments), "adapter_result_invalid")
        if actual != expected:
            mismatches.append(
                {
                    "step_id": step_id,
                    "expected_fingerprint": evidence_fingerprint(expected),
                    "actual_fingerprint": evidence_fingerprint(actual),
                }
            )
    report = {
        "vector_schema_version": VECTOR_SCHEMA_VERSION,
        "vector_id": vector_id,
        "requirements": list(requirements),
        "step_count": len(steps),
        "matched": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    canonical_json(report)
    return report


class ReferenceContractAdapter:
    """Drive the non-authoritative model through only the generic adapter API."""

    def __init__(self, foundation: Mapping[str, Any]):
        self._foundation = deepcopy(dict(foundation))
        harness = OfflineUpdateHarness.from_mapping(self._foundation)
        self._coordinator = CapabilityAdmissionCoordinator(harness.profiles)
        self._attempts: dict[str, Any] = {}
        self._leases: dict[str, Any] = {}
        self._commits: dict[str, Any] = {}
        self._last_reconciliation: Any | None = None

    def execute(self, operation: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        handlers = {
            "reconcile": self._reconcile,
            "begin_reconciliation": self._begin,
            "complete_reconciliation": self._complete,
            "acquire_lease": self._acquire,
            "validate_lease": self._validate,
            "commit_lease": self._commit,
            "commit_lease_race": self._commit_race,
            "release_lease": self._release,
            "finish_commit": self._finish,
            "registry_refresh": self._registry_refresh,
            "validate_fixture": self._validate_fixture,
            "health": self._health,
            "audit": self._audit,
            "reconcile_churn": self._reconcile_churn,
            "probe_capability": self._probe_capability,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise CompatibilityModelError("vector_operation_unsupported")
        return handler(arguments)

    def _harness(self, arguments: Mapping[str, Any]) -> OfflineUpdateHarness:
        fixture = deepcopy(self._foundation)
        _apply_mutations(fixture, arguments.get("fixture_mutations", []))
        return OfflineUpdateHarness.from_mapping(fixture)

    @staticmethod
    def _authority(
        harness: OfflineUpdateHarness,
        arguments: Mapping[str, Any],
    ) -> AuthorityBundle:
        authority_ids = arguments.get("authority_ids")
        if authority_ids is None:
            return harness.authority(
                _text(arguments.get("authority_id"), "vector_authority_invalid")
            )
        identifiers = _string_list(authority_ids, "vector_authorities_invalid")
        if not identifiers:
            raise CompatibilityModelError("vector_authorities_invalid")
        bundles = tuple(harness.authority(identifier) for identifier in identifiers)
        return AuthorityBundle(
            evaluated_at_epoch=max(item.evaluated_at_epoch for item in bundles),
            decisions=tuple(
                decision
                for bundle in bundles
                for decision in bundle.decisions
            ),
        )

    def _reconcile(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        harness = self._harness(arguments)
        result = self._coordinator.reconcile(
            harness.observation(_text(arguments.get("observation_id"), "vector_observation_invalid")),
            self._authority(harness, arguments),
        )
        self._last_reconciliation = result
        return _normalized_reconciliation(result)

    def _begin(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        harness = self._harness(arguments)
        attempt_id = _text(arguments.get("attempt_id"), "vector_attempt_invalid")
        attempt = self._coordinator.begin_reconciliation(
            harness.observation(_text(arguments.get("observation_id"), "vector_observation_invalid")),
            self._authority(harness, arguments),
        )
        self._attempts[attempt_id] = attempt
        return {
            "generation": attempt.generation,
            "surface": attempt.observation.surface.value,
            "retired_generation": attempt.retired_generation,
            "idempotent": attempt.idempotent,
        }

    def _complete(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        attempt = self._attempts.get(
            _text(arguments.get("attempt_id"), "vector_attempt_invalid")
        )
        if attempt is None:
            raise CompatibilityModelError("vector_attempt_unknown")
        result = self._coordinator.complete_reconciliation(attempt)
        self._last_reconciliation = result
        return _normalized_reconciliation(result)

    def _acquire(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease_id = _text(arguments.get("lease_id"), "vector_lease_invalid")
        lease = self._coordinator.acquire_route(
            _text(arguments.get("capability_id"), "vector_capability_invalid"),
            session_id=_text(arguments.get("session_id"), "vector_session_invalid"),
        )
        if lease is not None:
            self._leases[lease_id] = lease
        return {
            "granted": lease is not None,
            "surface": lease.surface.value if lease else None,
            "generation": lease.generation if lease else None,
        }

    def _validate(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease = self._leases.get(_text(arguments.get("lease_id"), "vector_lease_invalid"))
        return {
            "valid": bool(
                lease
                and self._coordinator.validate_pre_dispatch(
                    lease,
                    session_id=_text(arguments.get("session_id"), "vector_session_invalid"),
                )
            )
        }

    def _commit(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease_id = _text(arguments.get("lease_id"), "vector_lease_invalid")
        lease = self._leases.get(lease_id)
        commit = (
            self._coordinator.commit_route(
                lease,
                session_id=_text(arguments.get("session_id"), "vector_session_invalid"),
            )
            if lease
            else None
        )
        if commit is not None:
            self._commits[lease_id] = commit
        return {"committed": commit is not None}

    def _commit_race(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease_id = _text(arguments.get("lease_id"), "vector_lease_invalid")
        lease = self._leases.get(lease_id)
        session_id = _text(arguments.get("session_id"), "vector_session_invalid")
        attempts = arguments.get("attempt_count", 2)
        if type(attempts) is not int or attempts < 2 or attempts > 8:
            raise CompatibilityModelError("vector_commit_race_count_invalid")
        if lease is None:
            return {"success_count": 0, "refused_count": attempts}
        barrier = Barrier(attempts)

        def commit_once():
            barrier.wait()
            return self._coordinator.commit_route(lease, session_id=session_id)

        with ThreadPoolExecutor(max_workers=attempts) as executor:
            futures = tuple(executor.submit(commit_once) for _index in range(attempts))
            commits = tuple(item.result(timeout=5) for item in futures)
        winner = next((item for item in commits if item is not None), None)
        if winner is not None:
            self._commits[lease_id] = winner
        successes = sum(item is not None for item in commits)
        return {
            "success_count": successes,
            "refused_count": attempts - successes,
        }

    def _release(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease = self._leases.get(
            _text(arguments.get("lease_id"), "vector_lease_invalid")
        )
        return {
            "released": bool(lease and self._coordinator.release_route(lease))
        }

    def _finish(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        lease_id = _text(arguments.get("lease_id"), "vector_lease_invalid")
        commit = self._commits.get(lease_id)
        return {
            "finished": bool(commit and self._coordinator.finish_committed(commit))
        }

    @staticmethod
    def _registry_refresh(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = classify_registry_refresh(**dict(arguments))
        return {
            "status": result.status.value,
            "accepted": result.accepted,
            "idempotent": result.idempotent,
        }

    def _validate_fixture(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            harness = self._harness(arguments)
            access = _mapping(arguments.get("access"), "vector_access_invalid")
            kind = access.get("kind")
            if kind == "observation":
                harness.observation(_text(access.get("id"), "vector_observation_invalid"))
            elif kind == "authority":
                harness.authority(_text(access.get("id"), "vector_authority_invalid"))
            elif kind == "coordinator":
                CapabilityAdmissionCoordinator(harness.profiles)
            else:
                raise CompatibilityModelError("vector_access_invalid")
        except CompatibilityModelError as exc:
            return {"accepted": False, "error_code": exc.code}
        return {"accepted": True, "error_code": None}

    def _health(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if arguments:
            raise CompatibilityModelError("vector_health_arguments_invalid")
        projection = self._coordinator.health_projection()
        encoded = canonical_json(projection)
        sensitive_markers = (
            b"synthetic-session",
            b"synthetic-ha-mcp",
            b"1.0.0-synthetic",
            b"Bearer",
            b"https://",
            b'"catalog"',
            b'"credential"',
            b'"endpoint"',
            b'"exception"',
            b'"headers"',
            b'"identity"',
            b'"registry"',
            b'"schema"',
            b'"session"',
            b'"signature"',
            b'"token"',
        )
        return {
            "model_version": projection["model_version"],
            "surface_count": projection["surface_count"],
            "admitted_count": projection["admitted_count"],
            "fallback_count": projection["fallback_count"],
            "issued_lease_count": projection["issued_lease_count"],
            "active_commit_count": projection["active_commit_count"],
            "retained_retirement_diagnostic_count": projection[
                "retained_retirement_diagnostic_count"
            ],
            "capacity_exhaustion_count": projection["capacity_exhaustion_count"],
            "capacity_exhaustion_reason": projection[
                "capacity_exhaustion_reason"
            ],
            "issued_lease_capacity": MAX_ISSUED_LEASES,
            "active_commit_capacity": MAX_ACTIVE_COMMITS,
            "surface_generations": {
                item["surface"]: item["generation"]
                for item in projection["surfaces"]
            },
            "bounded": len(encoded) <= 32_768,
            "sensitive_material_present": any(
                item in encoded for item in sensitive_markers
            ),
        }

    def _audit(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if arguments:
            raise CompatibilityModelError("vector_audit_arguments_invalid")
        if self._last_reconciliation is None:
            raise CompatibilityModelError("vector_audit_result_unavailable")
        projection = self._coordinator.audit_projection(self._last_reconciliation)
        encoded = canonical_json(projection)
        sensitive_markers = (
            b"synthetic-session",
            b"synthetic-ha-mcp",
            b"1.0.0-synthetic",
            b"Bearer",
            b"https://",
            b'"catalog"',
            b'"credential"',
            b'"endpoint"',
            b'"exception"',
            b'"headers"',
            b'"identity"',
            b'"registry"',
            b'"schema"',
            b'"session"',
            b'"signature"',
            b'"token"',
        )
        return {
            "model_version": projection["model_version"],
            "event": projection["event"],
            "reason_code": projection["reason_code"],
            "fallback_count": projection["fallback_count"],
            "issued_lease_count": projection["issued_lease_count"],
            "active_commit_count": projection["active_commit_count"],
            "retained_retirement_diagnostic_count": projection[
                "retained_retirement_diagnostic_count"
            ],
            "capacity_exhaustion_count": projection["capacity_exhaustion_count"],
            "capacity_exhaustion_reason": projection[
                "capacity_exhaustion_reason"
            ],
            "bounded": len(encoded) <= 32_768,
            "sensitive_material_present": any(
                item in encoded for item in sensitive_markers
            ),
        }

    def _reconcile_churn(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        cycles = _list(arguments.get("cycles"), "vector_churn_cycles_invalid", 8)
        if not cycles:
            raise CompatibilityModelError("vector_churn_cycles_invalid")
        count = arguments.get("count")
        if type(count) is not int or count < 1 or count > 128:
            raise CompatibilityModelError("vector_churn_count_invalid")
        final = None
        for index in range(count):
            cycle = _mapping(
                cycles[index % len(cycles)],
                "vector_churn_cycle_invalid",
            )
            harness = self._harness(cycle)
            final = self._coordinator.reconcile(
                harness.observation(
                    _text(cycle.get("observation_id"), "vector_observation_invalid")
                ),
                self._authority(harness, cycle),
            )
        projection = self._coordinator.health_projection()
        self._last_reconciliation = final
        return {
            "iteration_count": count,
            "final_generation": (
                final.generation.generation
                if final is not None and final.generation is not None
                else None
            ),
            "retained_retirement_diagnostic_count": projection[
                "retained_retirement_diagnostic_count"
            ],
            "issued_lease_count": projection["issued_lease_count"],
            "active_commit_count": projection["active_commit_count"],
            "within_retirement_bound": projection[
                "retained_retirement_diagnostic_count"
            ] <= MAX_RETIREMENT_DIAGNOSTICS,
        }

    def _probe_capability(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        harness = self._harness(arguments)
        observation = harness.observation(
            _text(arguments.get("observation_id"), "vector_observation_invalid")
        )
        result = self._coordinator.reconcile(
            observation,
            self._authority(harness, arguments),
        )
        capability_id = _text(
            arguments.get("capability_id"),
            "vector_capability_invalid",
        )
        selected = (
            result.generation.decision_for(capability_id)
            if result.generation is not None
            else None
        )
        lease = self._coordinator.acquire_route(
            capability_id,
            session_id=_text(arguments.get("session_id"), "vector_session_invalid"),
        )
        commit = (
            self._coordinator.commit_route(
                lease,
                session_id=observation.session_id,
            )
            if lease is not None
            else None
        )
        self._last_reconciliation = result
        return {
            "disposition": selected.disposition.value if selected else None,
            "reason_code": selected.reason_code if selected else "decision_missing",
            "adapter_present": bool(selected and selected.adapter_id),
            "lease_granted": lease is not None,
            "committed": commit is not None,
            "fallback_count": 0,
            "write_action_reachability": 0,
        }


def _normalized_reconciliation(result: Any) -> dict[str, Any]:
    generation = result.generation
    decisions = generation.decisions if generation is not None else ()
    return {
        "published": result.published,
        "idempotent": result.idempotent,
        "surface": generation.surface.value if generation else None,
        "generation": generation.generation if generation else None,
        "retired_generation": result.retired_generation,
        "disposition": result.disposition.value,
        "admitted": sorted(
            item.capability_id for item in decisions if item.disposition.admitted
        ),
        "quarantined": sorted(
            [
                {
                    "capability_id": item.capability_id,
                    "reason_code": item.reason_code,
                }
                for item in decisions
                if item.disposition is AdmissionDisposition.QUARANTINED
            ],
            key=lambda item: (item["capability_id"], item["reason_code"]),
        ),
        "unavailable": sorted(
            [
                {
                    "capability_id": item.capability_id,
                    "reason_code": item.reason_code,
                }
                for item in decisions
                if item.disposition is AdmissionDisposition.UNAVAILABLE
            ],
            key=lambda item: (item["capability_id"], item["reason_code"]),
        ),
        "write_action_reachability": 0,
    }


def _apply_mutations(root: Any, raw_mutations: Any) -> None:
    mutations = _list(raw_mutations, "vector_mutations_invalid", MAX_MUTATIONS)
    for raw in mutations:
        mutation = _mapping(raw, "vector_mutation_invalid")
        operation = mutation.get("operation")
        path = _list(mutation.get("path"), "vector_mutation_path_invalid", 16)
        parent, key = _resolve_parent(root, path)
        if operation == "set":
            parent[key] = deepcopy(mutation.get("value"))
        elif operation == "append":
            target = parent[key]
            if not isinstance(target, list):
                raise CompatibilityModelError("vector_mutation_target_invalid")
            target.append(deepcopy(mutation.get("value")))
        elif operation == "repeat":
            target = parent[key]
            count = mutation.get("count")
            source_index = mutation.get("source_index", 0)
            if (
                not isinstance(target, list)
                or type(count) is not int
                or count < 0
                or count > 1024
                or type(source_index) is not int
                or source_index < 0
                or source_index >= len(target)
            ):
                raise CompatibilityModelError("vector_mutation_repeat_invalid")
            parent[key] = [deepcopy(target[source_index]) for _index in range(count)]
        elif operation == "reverse":
            target = parent[key]
            if not isinstance(target, list):
                raise CompatibilityModelError("vector_mutation_target_invalid")
            parent[key] = list(reversed(target))
        else:
            raise CompatibilityModelError("vector_mutation_operation_invalid")


def _resolve_parent(root: Any, path: list[Any]) -> tuple[Any, Any]:
    if not path:
        raise CompatibilityModelError("vector_mutation_path_invalid")
    current = root
    for part in path[:-1]:
        if isinstance(current, Mapping) and isinstance(part, str) and part in current:
            current = current[part]
        elif isinstance(current, list) and type(part) is int and 0 <= part < len(current):
            current = current[part]
        else:
            raise CompatibilityModelError("vector_mutation_path_invalid")
    key = path[-1]
    if isinstance(current, Mapping) and isinstance(key, str) and key in current:
        return current, key
    if isinstance(current, list) and type(key) is int and 0 <= key < len(current):
        return current, key
    raise CompatibilityModelError("vector_mutation_path_invalid")


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompatibilityModelError(code)
    return value


def _list(value: Any, code: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise CompatibilityModelError(code)
    return value


def _string_list(value: Any, code: str) -> tuple[str, ...]:
    items = _list(value, code, 32)
    result = tuple(_text(item, code) for item in items)
    if len(result) != len(set(result)):
        raise CompatibilityModelError(code)
    return result


def _text(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CompatibilityModelError(code)
    return value
