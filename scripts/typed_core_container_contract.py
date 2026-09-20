"""Fault injection around real disposable Core/provider calls, never production.

The caller's closed runner/target guards own this lane. REST state interference,
response/readback loss and an advanced recovery clock are explicit test faults;
they must not be described as actual process crashes or Core version changes.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4


async def failure_contract(fan, power, rest, telemetry, kind, output, *, check, save):
    from ha_mcp_engineering.fan.contracts import FanRequest, digest
    from ha_mcp_engineering.power.contracts import PowerRequest

    for family, service, entity in (
        ("fan", fan, "fan.hamcp_contract_fan"),
        ("power", power, "switch.hamcp_contract_switch"),
    ):
        def request(action):
            fields = dict(entity_id=entity, action=action,
                          operation_id=f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}")
            if family == "fan":
                return FanRequest(**fields, percentage=50 if action == "turn_on" else None)
            return PowerRequest(**fields)

        baseline = await rest.request("GET", "/states/" + entity)
        check(baseline["state"] == "off", "fault_fixture_not_off")
        initial_calls = baseline["attributes"].get("synthetic_service_calls")
        original_prepare = service.adapter.prepare
        injected = False

        async def stale_prepare(*args, **kwargs):
            nonlocal injected
            prepared = await original_prepare(*args, **kwargs)
            if not injected:
                injected = True
                await rest.request("POST", "/states/" + entity, {
                    "state": "on", "attributes": baseline["attributes"],
                })
            return prepared

        service.adapter.prepare = stale_prepare
        stale_request = request("turn_on")
        setattr(telemetry, service.binding_attribute, digest(stale_request.model_dump()))
        try:
            refused = await service.control(stale_request)
            check(injected and refused["terminal"] and refused["state"] != "succeeded_verified"
                  and refused["provider_attempt_count"] == 0
                  and not refused["dispatch_intent_recorded"], "stale_state_not_refused")
            check(await service.control(stale_request) == refused, "stale_duplicate_changed")
            save(output, f"{kind}-{family}-stale-refusal.json", refused)
        finally:
            service.adapter.prepare = original_prepare
            await rest.request("POST", "/states/" + entity, {
                "state": "off", "attributes": baseline["attributes"],
            })

        original_dispatch, original_state = service.provider.dispatch, service.provider.state
        lost, dispatches = False, 0

        async def lose_response(*args, **kwargs):
            nonlocal lost, dispatches
            dispatches += 1
            await original_dispatch(*args, **kwargs)
            lost = True
            raise TimeoutError("synthetic response loss after actual provider result")

        async def unavailable_readback(*args, **kwargs):
            if lost:
                raise service.refusal("synthetic_readback_unavailable")
            return await original_state(*args, **kwargs)

        service.provider.dispatch, service.provider.state = lose_response, unavailable_readback
        uncertain_request = request("turn_on")
        setattr(telemetry, service.binding_attribute, digest(uncertain_request.model_dump()))
        replacement = None
        try:
            uncertain = await service.control(uncertain_request)
            check(lost and dispatches == 1 and not uncertain["terminal"]
                  and uncertain["provider_attempt_count"] == 1
                  and uncertain["dispatch_intent_recorded"]
                  and not uncertain["provider_response_received"], "uncertain_result_not_preserved")
            save(output, f"{kind}-{family}-uncertain.json", uncertain)
            replacement = type(service)(service.root.parent, service.provider, service.core,
                                        audit=service.executions.audit)
            waiting = await replacement.control(uncertain_request)
            check(not waiting["terminal"] and dispatches == 1, "owner_lease_bypassed")
            # Simulate elapsed owner expiry without waiting two real minutes.
            # Core's live identity/registry clock and transport are not replaced.
            replacement.now = lambda: datetime.now(timezone.utc) + timedelta(seconds=121)
            lost = False
            recovered = await replacement.control(uncertain_request)
            check(recovered["state"] == "succeeded_verified"
                  and recovered["core_binding"]["version"] == "2026.9.3"
                  and recovered["provider_attempt_count"] == 1
                  and not recovered["provider_response_received"]
                  and dispatches == 1, "read_only_recovery_failed")
            check(await replacement.control(uncertain_request) == recovered,
                  "recovery_duplicate_changed")
            check(any(event["event_type"] == "recovery_claimed" for event in
                      replacement.executions.get(uncertain_request.task_id).events),
                  "recovery_not_claimed")
            readback = await rest.request("GET", "/states/" + entity)
            check(readback["state"] == "on", "recovery_independent_state_mismatch")
            if initial_calls is not None:
                check(readback["attributes"]["synthetic_service_calls"] == initial_calls + 1,
                      "recovery_duplicate_service_call")
            save(output, f"{kind}-{family}-recovered.json", recovered)
        finally:
            service.provider.dispatch, service.provider.state = original_dispatch, original_state
            if replacement is not None:
                await replacement.close()

        restore_request = request("turn_off")
        setattr(telemetry, service.binding_attribute, digest(restore_request.model_dump()))
        restored = await service.control(restore_request)
        check(restored["state"] == "succeeded_verified" and restored["provider_attempt_count"] == 1,
              "recovery_restoration_failed")
        readback = await rest.request("GET", "/states/" + entity)
        check(readback["state"] == "off", "recovery_fixture_not_restored")
        if initial_calls is not None:
            check(readback["attributes"]["synthetic_service_calls"] == initial_calls + 2,
                  "recovery_restoration_dispatch_mismatch")
        check(not service.locks.records() and service.health()["nonterminal_tasks"] == 0
              and service.health()["audit_projection_failures"] == 0
              and service.health()["fallback_count"] == 0, "recovery_not_settled")
        save(output, f"{kind}-{family}-failure-contract.json", {
            "status": "PASS", "stale_dispatches": 0, "uncertain_dispatches": dispatches,
            "restoration": restored, "recovery_clock_advance_seconds": 121,
            "faults": ["REST_state_interference", "lost_response_and_readback", "expired_owner_clock"],
            "actual_process_crash": False, "actual_core_version_change": False,
            "physical_feedback": False,
        })
