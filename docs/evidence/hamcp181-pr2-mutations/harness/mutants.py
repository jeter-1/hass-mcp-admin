"""Mutant specification: id -> (concept, [(relative file, old, new), ...], fast test modules)."""

P = "hass_mcp_engineering_beta/ha_mcp_engineering/"
DF = P + "f3_runtime/dispatch_facts.py"
RT = P + "f3_runtime/runtime.py"
EX = P + "f3/executor.py"
CA = P + "f3_configuration/adapter.py"
SV = P + "governance/service.py"
RS = P + "models/responses.py"
WS = P + "clients/websocket.py"
HS = P + "governance/helper_state.py"
RES = P + "governance/resources.py"
EM = P + "governance/entity_metadata.py"
FA = P + "fan/adapter.py"
UF = P + "providers/upstream_fan.py"
OS = P + "f3/operational_strategies.py"
OA = P + "f3/operational_adapter.py"

T_DF = "test_hamcp181_dispatch_facts"
T_PROD = "test_hamcp181_proven_no_call_producers"
T_T2 = "test_hamcp181_terminal_outcome_t2"
T_SURF = "test_hamcp181_surface_consistency"
T_RB = "test_hamcp181_rollback_unchanged_forward"
T_214 = "test_hamcp214_plan_verification_rollback_projection"
T_HEALTH = "test_hamcp181_health_core_admission"
T_CAT = "test_hamcp181_catalog_core_admission"
T_ARCH = "test_hamcp181_architecture_guard"
T_LBL = "test_hamcp181_audit_labels"
T_ERR = "test_hamcp181_entity_registry_error_unproven"
T_183 = "test_hamcp183_entity_registry_error_truth"
T_FAN = "test_hamcp181_fan_power_dispatched_at"

M = {}

# ---- M1: a proven no-call reported as dispatched
M["M1a"] = ("aggregate provider_dispatch_occurred ignores (b)", [(DF,
    "provider_dispatch_occurred = not all(item in NOT_SENT_PHASES for item in phases)",
    "provider_dispatch_occurred = not all(item in NO_INTENT_PHASES for item in phases)")],
    [T_DF, T_T2])
M["M1b"] = ("runtime never stores the (b) terminal outcome", [(RT,
    "not_sent = facts.not_sent and (",
    "not_sent = False and (")], [T_T2, T_PROD])
M["M1c"] = ("outcome_code drops the not-sent outcome", [(DF,
    "        if self.not_sent:\n            return NOT_SENT_OUTCOME\n",
    "")], [T_DF, T_T2])
M["M1d"] = ("executor honoured branch drops PROVIDER_CALL_NOT_SENT", [(EX,
    "                            PROVIDER_CALL_NOT_SENT,\n                            callback_failed_after_intent,",
    "                            \"provider_result_uncertain\",\n                            callback_failed_after_intent,")],
    [T_PROD, T_T2])

# ---- M2: a surface bypasses the shared projection
M["M2a"] = ("list_execution_tasks uses legacy facts", [(SV,
    "            facts = self._dispatch_facts(task)\n            summary = self._public_task(task, include_events=False)",
    "            facts = legacy_dispatch_facts(task)\n            summary = self._public_task(task, include_events=False)")],
    [T_SURF, T_T2])
M["M2b"] = ("apply nested execution_task bypasses public_task_facts", [(RT,
    "            \"execution_task\": self.public_task_facts(\n                self.service._public_task(task), task, facts\n            ),",
    "            \"execution_task\": self.service._public_task(task),")],
    [T_SURF, T_T2])
M["M2c"] = ("get_change_plan nested execution_task uses legacy facts", [(SV,
    "        facts = facts if facts is not None else self._dispatch_facts(task)\n        value = {",
    "        facts = legacy_dispatch_facts(task)\n        value = {")], [T_SURF, T_T2])
M["M2d"] = ("compact receipt drops copied dispatch facts", [(RS,
    "        for source in (body, task):",
    "        for source in ():")], [T_SURF, T_T2])
M["M2e"] = ("receipt sheds outcome_code even when it differs", [(RS,
    "        \"outcome_code\" in facts\n        and facts.get(\"terminal_outcome\") == facts[\"outcome_code\"]",
    "        \"outcome_code\" in facts")], [T_SURF, T_T2])
M["M2f"] = ("lifecycle audit uses legacy facts", [(SV,
    "        facts = self._dispatch_facts(task)\n        safe[\"provider_dispatch_occurred\"] = facts.provider_dispatch_occurred",
    "        facts = legacy_dispatch_facts(task)\n        safe[\"provider_dispatch_occurred\"] = facts.provider_dispatch_occurred")],
    [T_T2, T_SURF])
M["M2g"] = ("apply nested plan uses legacy facts", [(RT,
    "                plan, include_configs=False, dispatch_facts=facts\n",
    "                plan, include_configs=False, dispatch_facts=legacy_dispatch_facts(task)\n")],
    [T_SURF, T_T2])

# ---- M3: HAMCP-214 verification not propagated task -> plan
M["M3a"] = ("plan verification block never runs", [(RT,
    "            if plan.operations and all(\n                by_id[operation.operation_id][\"normalized_outcome\"]",
    "            if False and plan.operations and all(\n                by_id[operation.operation_id][\"normalized_outcome\"]")],
    [T_214])
M["M3b"] = ("operation verification loses actual fingerprint", [(RT,
    "                        actual_fingerprint=operation.post_apply_fingerprint,\n                    )\n                    if checked_at is not None:",
    "                        actual_fingerprint=None,\n                    )\n                    if checked_at is not None:")],
    [T_214])
M["M3c"] = ("delete desired fingerprint uses proposed hash", [(RT,
    "    if operation.action == \"delete\":\n        from ..governance.resources import resource_fingerprint",
    "    if False and operation.action == \"delete\":\n        from ..governance.resources import resource_fingerprint")],
    [T_214])
M["M3d"] = ("label plans project rollback available", [(RT,
    "            available, status = False, label_registry_planning.ROLLBACK_NOT_SUPPORTED",
    "            available, status = True, None")], [T_214])
M["M3e"] = ("exposure plans always project rollback available", [(RT,
    "            available = any(dispatched_once(record) for _, record in operations)",
    "            available = True")], [T_214])

# ---- M4: an unproven no-mutation claim
M["M4a"] = ("every confirmed-with-response failure is r-proven", [(DF,
    "        proven = (\n            PROVIDER_REJECTED_MUTATION in codes",
    "        proven = True or (\n            PROVIDER_REJECTED_MUTATION in codes")], [T_DF, T_ERR])
M["M4b"] = ("r-unproven child claims changed_by_engineering False", [(DF,
    "        if self.phase in NOT_SENT_PHASES or self.phase == PHASE_SENT_REJECTED_NO_MUTATION:",
    "        if self.phase in NOT_SENT_PHASES or self.phase in (PHASE_SENT_REJECTED_NO_MUTATION, PHASE_SENT_FAILED_MUTATION_UNPROVEN):")],
    [T_DF, T_RB])
M["M4c"] = ("aggregate treats r-unproven as unchanged", [(DF,
    "        child.phase in {PHASE_SEND_UNCERTAIN, PHASE_SENT_FAILED_MUTATION_UNPROVEN}",
    "        child.phase in {PHASE_SEND_UNCERTAIN}")], [T_DF, T_SURF])
M["M4d"] = ("aggregate treats unverified sent child as unchanged", [(DF,
    "        or (child.phase == PHASE_SENT_RESPONSE_RECEIVED and not child.verified)\n",
    "")], [T_DF, T_SURF])

# ---- M5: redispatch after intent / rollback of unchanged forward
M["M5a"] = ("OD-11 legacy refusal removed", [(RT,
    "            if self.dispatch_facts_for_task(task).changed_by_engineering is False:",
    "            if False:")], [T_RB])
M["M5b"] = ("OD-11 refusal widened to (c)/(r-unproven)", [(RT,
    "            if self.dispatch_facts_for_task(task).changed_by_engineering is False:",
    "            if self.dispatch_facts_for_task(task).changed_by_engineering is not True:")],
    [T_RB])
M["M5c"] = ("contract-v2 rollback includes unverified forward children", [(RT,
    "                    or not previously_verified\n",
    "")], [T_RB, T_214])
M["M5d"] = ("stored-intent dispatch predicate ignores provider attempts", [(SV,
    "        return bool(task.dispatched_at or task.provider_attempts)",
    "        return bool(task.dispatched_at)")], [T_ARCH, T_T2, T_RB])

# ---- M6: proof recorded after a send-capable await
M["M6a"] = ("websocket answered unauthorized carries call_not_sent", [(WS,
    "                            raise AuthorizationError(\n                                details=self._error_details(\n                                    category,\n                                    provider_response_received=True,\n                                )\n                            )",
    "                            raise AuthorizationError(\n                                details=self._error_details(\n                                    category,\n                                    provider_response_received=True,\n                                ),\n                                call_not_sent=True,\n                            )")],
    [T_PROD])
M["M6b"] = ("helper_state declares not-sent in a catch-all after the command", [(HS,
    "        try:\n            await before_dispatch()\n        except Exception as exc:\n            # HAMCP-181 b5: the callback raised, so the command was never\n            # sent.  Declare that before any send-capable call.\n            raise HelperStateGatewayError(\n                \"before_dispatch_failed\", call_not_sent=True\n            ) from exc\n        try:\n            await self.websocket_client.command(payload)\n",
    "        try:\n            await before_dispatch()\n            await self.websocket_client.command(payload)\n"),
    (HS,
    "            raise HelperStateGatewayError(\n                \"dispatch_indeterminate\", dispatched=True\n            ) from None\n        return HelperStateDispatchResult(",
    "            raise HelperStateGatewayError(\n                \"dispatch_indeterminate\", dispatched=True\n            ) from None\n        except Exception as exc:\n            raise HelperStateGatewayError(\n                \"before_dispatch_failed\", call_not_sent=True\n            ) from exc\n        return HelperStateDispatchResult(")],
    [T_PROD, T_LBL])
M["M6c"] = ("helper_state answered rejection declared not-sent", [(HS,
    "                \"provider_rejected\" if confirmed else \"dispatch_indeterminate\",\n                dispatched=True,",
    "                \"provider_rejected\" if confirmed else \"dispatch_indeterminate\",\n                call_not_sent=True,")],
    [T_PROD, T_LBL])
M["M6d"] = ("fan post-call authority refusal declared not-sent", [(UF,
    "        if token is None or self._authority(token[0]) != token[2]:\n            raise self.refusal(\"fan_provider_authority_retired\")\n        if telemetry:",
    "        if token is None or self._authority(token[0]) != token[2]:\n            retired = self.refusal(\"fan_provider_authority_retired\")\n            retired.call_not_sent = True\n            raise retired\n        if telemetry:")],
    [T_PROD, T_FAN])

# ---- M7: an (r) case marked not-sent
M["M7a"] = ("config adapter adds PROVIDER_CALL_NOT_SENT to answered rejections", [(CA,
    "                        if not response_received\n                        and getattr(exc, \"call_not_sent\", False) is True",
    "                        if response_received\n                        or getattr(exc, \"call_not_sent\", False) is True")],
    [T_PROD, T_DF])
M["M7b"] = ("child_facts: proof code with response reads as (b)", [(DF,
    "        # A proof code with a received response is a contradiction.\n        return ChildFacts(phase=PHASE_SEND_UNCERTAIN, evidence_conflict=True, **base)",
    "        # A proof code with a received response is a contradiction.\n        return ChildFacts(phase=PHASE_INTENT_RECORDED_NOT_SENT, setter_proof=setter_code, **base)")],
    [T_DF])
M["M7c"] = ("entity_metadata carries call_not_sent on an answered unauthorized", [(EM,
    "                    getattr(exc, \"call_not_sent\", False) is True\n                    and details.get(\"provider_response_received\") is not True\n",
    "                    getattr(exc, \"call_not_sent\", False) is True\n")],
    [T_PROD])

# ---- M8: provider_call_not_sent for backup or add-on/Core lifecycle
M["M8a"] = ("executor records (b) for any callback failure that raises", [(EX,
    "            if not persisted.terminal:\n                persisted = self.execution_repository.record_dispatch_result(\n                    identity.task_id,\n                    owner_id=identity.owner_id,\n                    claim_generation=claim.claim_generation,\n                    outcome=\"dispatch_indeterminate\",",
    "            if not persisted.terminal and callback_failed_after_intent is not None:\n                record = self.execution_repository.record_dispatch_result(\n                    identity.task_id,\n                    owner_id=identity.owner_id,\n                    claim_generation=claim.claim_generation,\n                    outcome=\"dispatch_failed_confirmed\",\n                    provider_response_received=False,\n                    diagnostic_codes=(PROVIDER_CALL_NOT_SENT, callback_failed_after_intent),\n                    now=self.now(),\n                )\n                self._release_safely(handle)\n                return self._result(record)\n            if not persisted.terminal:\n                persisted = self.execution_repository.record_dispatch_result(\n                    identity.task_id,\n                    owner_id=identity.owner_id,\n                    claim_generation=claim.claim_generation,\n                    outcome=\"dispatch_indeterminate\",")],
    [T_PROD])
M["M8b"] = ("backup strategy declares call_not_sent; helper-only guard removed", [(OS,
    "        except OperationalGatewayError as exc:\n            if not exc.dispatched:\n                raise\n            confirmed = exc.category in {\n                \"permission_failure\",\n                \"backup_rejected\",",
    "        except OperationalGatewayError as exc:\n            if not exc.dispatched:\n                return StrategyDispatch(\n                    response_received=False,\n                    response_evidence_hash=None,\n                    diagnostic_code=\"before_dispatch_failed\",\n                    call_not_sent=True,\n                )\n            confirmed = exc.category in {\n                \"permission_failure\",\n                \"backup_rejected\","),
    (OA,
    "        if result.call_not_sent and operation.operation == SET_INPUT_BOOLEAN_STATE:",
    "        if result.call_not_sent:")],
    [T_PROD])
M["M8c"] = ("backup gateway audit label says provider_call_not_sent", [(RT,
    "                if getattr(exc, \"dispatched\", False)\n                else \"provider_invocation_refused\"\n            )\n            raise\n        self.auditor(\"provider_response_received\")\n        self.recorder(",
    "                if getattr(exc, \"dispatched\", False)\n                else \"provider_call_not_sent\"\n            )\n            raise\n        self.auditor(\"provider_response_received\")\n        self.recorder(")],
    [T_PROD, T_LBL])

# ---- M9: a lost response marked not-sent
M["M9a"] = ("child_facts: final no-response fallthrough reads as (b)", [(DF,
    "\n    return ChildFacts(phase=PHASE_SEND_UNCERTAIN, **base)",
    "\n    return ChildFacts(phase=PHASE_INTENT_RECORDED_NOT_SENT, **base)")], [T_DF])
M["M9b"] = ("fan adapter: any post-boundary failure is proven not sent", [(FA,
    "            if isinstance(exc, self.refusal) and getattr(exc, \"call_not_sent\", False) is True:",
    "            if True:")], [T_PROD, T_FAN])
M["M9c"] = ("helper_state: a dispatched error keeps call_not_sent", [(HS,
    "        self.call_not_sent = call_not_sent is True and not dispatched",
    "        self.call_not_sent = call_not_sent is True or dispatched")], [T_PROD])

# ---- M10: not-sent attribute set at a post-send governance site
M["M10a"] = ("resources P1 REST 4xx declares call_not_sent", [(RES,
    "                        \"provider_status\": status,\n                        \"provider_response_received\": True,\n                    }\n                ) from exc",
    "                        \"provider_status\": status,\n                        \"provider_response_received\": True,\n                    },\n                    call_not_sent=True,\n                ) from exc")],
    [T_PROD])
M["M10b"] = ("entity_metadata AuthorizationError always call_not_sent", [(EM,
    "                call_not_sent=(\n                    getattr(exc, \"call_not_sent\", False) is True\n                    and details.get(\"provider_response_received\") is not True\n                ),",
    "                call_not_sent=True,")], [T_PROD])

# ---- M11: HomeAssistantUnavailableError treated as a no-send proof
M["M11a"] = ("config adapter: Unavailable is confirmed and not sent", [(CA,
    "            confirmed_no_mutation = bool(\n                getattr(exc, \"mutation_dispatched\", None) is False\n            )",
    "            unavailable = type(exc).__name__ == \"HomeAssistantUnavailableError\"\n            confirmed_no_mutation = bool(\n                getattr(exc, \"mutation_dispatched\", None) is False or unavailable\n            )"),
    (CA,
    "                    else \"provider_response_indeterminate\",\n                ),\n                provider_mutation_count=(0 if confirmed_no_mutation else None),",
    "                    else \"provider_response_indeterminate\",\n                    *((PROVIDER_CALL_NOT_SENT,) if unavailable else ()),\n                ),\n                provider_mutation_count=(0 if confirmed_no_mutation else None),")],
    [T_PROD])
M["M11b"] = ("helper_state: Unavailable declared not sent", [(HS,
    "        except (HomeAssistantTimeoutError, HomeAssistantUnavailableError):\n            raise HelperStateGatewayError(\n                \"dispatch_indeterminate\", dispatched=True\n            ) from None",
    "        except HomeAssistantUnavailableError:\n            raise HelperStateGatewayError(\n                \"dispatch_indeterminate\", call_not_sent=True\n            ) from None\n        except HomeAssistantTimeoutError:\n            raise HelperStateGatewayError(\n                \"dispatch_indeterminate\", dispatched=True\n            ) from None")],
    [T_PROD])
M["M11c"] = ("websocket: connection error after send carries call_not_sent", [(WS,
    "            raise HomeAssistantUnavailableError(\n                details=self._error_details(category)\n            ) from exc\n        finally:\n            # Scoped transport refusals/cancellation may exit before a response.",
    "            unavailable = HomeAssistantUnavailableError(\n                details=self._error_details(category)\n            )\n            unavailable.call_not_sent = True\n            raise unavailable from exc\n        finally:\n            # Scoped transport refusals/cancellation may exit before a response.")],
    [T_PROD])

# ---- M12: entity-registry error result as no-mutation, or P3 widened
M["M12a"] = ("config adapter: registry error result is confirmed P3", [(CA,
    "                NormalizedOperationOutcome.DISPATCH_INDETERMINATE,\n                intent=True,\n                invocation_count=1,\n                may_have_dispatched=True,\n                response_received=True,\n                codes=(\n                    ERROR_RESULT_AFTER_SEND,\n                    \"provider_response_indeterminate\",\n                ),\n                provider_mutation_count=None,",
    "                NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,\n                intent=True,\n                invocation_count=1,\n                may_have_dispatched=True,\n                response_received=True,\n                codes=(\n                    \"provider_rejected_mutation\",\n                    \"provider_permission_denied\",\n                ),\n                provider_mutation_count=0,")],
    [T_ERR, T_183])
M["M12b"] = ("P3 widened: permission_denied not required", [(DF,
    "                    target_type in _P3_TARGET_TYPES\n                    and PROVIDER_PERMISSION_DENIED in codes\n",
    "                    target_type in _P3_TARGET_TYPES\n")], [T_DF, T_ERR])
M["M12c"] = ("entity_metadata: error result raised as not-dispatched", [(EM,
    "                raise EntityMetadataUpdateErrorResult(\n                    details={",
    "                raise ConfigurationMutationNotDispatchedError(\n                    details={")],
    [T_183, T_ERR, T_PROD])
M["M12d"] = ("child_facts: (c) uncertainty codes no longer pin send-uncertain", [(DF,
    "    if codes & _C_UNCERTAINTY_CODES:",
    "    if False:")], [T_DF, T_ERR])

# ---- OD-11 (also covered by M5a/M5b)
# ---- HEALTH: core_admission
M["HEALTH1"] = ("health admits a non-boolean truthy answer", [(SV,
    "            admitted = status.get(\"available\") is True\n",
    "            admitted = bool(status.get(\"available\"))\n")],
    [T_HEALTH, T_CAT])
M["HEALTH2"] = ("health admits on a failing read", [(SV,
    "        except Exception:\n            admitted = False\n            generation = None",
    "        except Exception:\n            admitted = True\n            generation = None")],
    [T_HEALTH, T_CAT])
M["HEALTH3"] = ("health admits a partial pair (first capability only)", [(SV,
    "            status = core.route_status(capabilities) if core is not None else {}",
    "            status = core.route_status(tuple(capabilities)[:1]) if core is not None else {}")],
    [T_HEALTH, T_CAT])

# ---- SEND: send time published for (b)
M["SEND1"] = ("summary send-time map stores a captured time for (b)", [(RT,
    "            times[child_id] = (\n                None\n                if phase == dispatch_projection.PHASE_INTENT_RECORDED_NOT_SENT\n                else self._provider_call_started_at.get(child_id)\n            )",
    "            times[child_id] = self._provider_call_started_at.get(child_id)")],
    [T_T2, T_FAN])
M["SEND2"] = ("(b) parent publishes the stored time as a provider call start", [(DF,
    "        dispatched_at = None\n        basis = DISPATCHED_AT_CALL_NOT_SENT",
    "        dispatched_at = stored_dispatched_at\n        basis = DISPATCHED_AT_PROVIDER_CALL_START")],
    [T_FAN, T_DF, T_T2])
M["SEND3"] = ("(b) child basis reads a captured time as a provider call start", [(DF,
    "    if child.phase == PHASE_INTENT_RECORDED_NOT_SENT:\n        return DISPATCHED_AT_CALL_NOT_SENT\n    if child.send_time_captured:\n        return (\n            DISPATCHED_AT_PROVIDER_CALL_START\n            if child.phase in SENT_PHASES\n            and child.provider_call_started_at is not None",
    "    if child.send_time_captured:\n        return (\n            DISPATCHED_AT_PROVIDER_CALL_START\n            if child.provider_call_started_at is not None")],
    [T_DF, T_SURF, T_T2])
M["SEND4"] = ("send_times include (b) children; (b) branch publishes them", [(DF,
    "        if child.phase in SENT_PHASES and child.provider_call_started_at is not None\n    ]",
    "        if child.provider_call_started_at is not None\n    ]"),
    (DF,
    "        dispatched_at = None\n        basis = DISPATCHED_AT_CALL_NOT_SENT",
    "        dispatched_at = _earliest(send_times) if send_times else None\n        basis = DISPATCHED_AT_PROVIDER_CALL_START if send_times else DISPATCHED_AT_CALL_NOT_SENT")],
    [T_DF, T_T2, T_FAN])

# ---- PR2 review 1 corrections
T_SB = "test_hamcp181_send_time_boundary"
M["SEND5"] = ("M1: send time captured at gateway entry, not the send boundary", [(RT,
    "        child_id = _ACTIVE_F3_CHILD.get()\n        if child_id is not None:\n            self._emit_f3_audit_event(\n                {\"event_type\": event_type, \"task_id\": child_id}",
    "        child_id = _ACTIVE_F3_CHILD.get()\n        if child_id is not None:\n            if event_type == \"provider_invocation_started\":\n                self._provider_call_started_at.setdefault(child_id, self.service._timestamp())\n            self._emit_f3_audit_event(\n                {\"event_type\": event_type, \"task_id\": child_id}")],
    [T_SB])
M["L1"] = ("P1 claimed without the rejection proof code", [(DF,
    "            PROVIDER_REJECTED_MUTATION in codes\n            and not (codes & _R_PROVEN_CONFLICTING_CODES)",
    "            True\n            and not (codes & _R_PROVEN_CONFLICTING_CODES)")],
    [T_DF])
CAP = "hass_mcp_engineering_beta/ha_mcp_engineering/capabilities.py"
CMP = "hass_mcp_engineering_beta/ha_mcp_engineering/tools/compatibility.py"
M["CAT1"] = ("M2: the catalog reads its static text, not the current admission", [(CAP,
    "            item[\"core_admission\"] = current\n",
    "            item[\"core_admission\"] = item[\"core_admission\"]\n")],
    [T_CAT])
M["CAT2"] = ("M2: list_capabilities does not pass the admission snapshot", [(CMP,
    "                service.core_admission_snapshot() if service is not None else None",
    "                None")],
    [T_CAT])
M["CAT3"] = ("M2: groups read from different generations are accepted", [(SV,
    "            if len(generations) == 1:\n                [generation] = generations\n",
    "            if True:\n                generation = reads[0][1]\n")],
    [T_CAT])
M["CAT4"] = ("M2: a snapshot with no admitted generation is reported as current", [(SV,
    "                else \"current\"\n                if generation is not None\n",
    "                else \"current\"\n                if True\n")],
    [T_CAT])
