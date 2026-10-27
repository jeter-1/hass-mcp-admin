"""HAMCP-232/HAMCP-145 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery (disposable copy of the tracked tree
outside the repository, every fragment matching exactly once before anything
runs, a full baseline, then fail-fast suites per mutant).  The mutations
target every new gateway and policy rule of the November batch, built
inactive (design note section 6.1 item 9):

- ``A*``: admission removed (service entry, persistence and apply-context
  checks; adapter preflight, pre-dispatch and readback; the runtime's
  operational-profile seam; script-trace authority, generation and caller);
- ``D*``: drift accepted (entry ``modified_at``, the held flow's step and
  visible form, the post-open entry check, planning-time movement, the
  input_select option list, state and plan-time option check, a refused
  pre-dispatch window ignored);
- ``C*``: contract rules loosened (pinned fields, no-op intents, the flow-id
  shape, visible-form equality, a 4xx submit, the exact send boundary);
- ``O*``: orphaned flows hidden or a held flow never released;
- ``R*``: readback accepting an unverified result, or a rejection after the
  send treated as never dispatched;
- ``P*``: planning policy (target limit, one domain per plan, elevated
  classification, the OD-7 disclosure, injected defaults, closed vocabularies);
- ``S*``: script-trace reads (the HAMCP-225 trace exit bypassed for a
  parallel screen or skipped, known secrets dropped, identifiers returned
  unscreened, not-found mapping, the script-id shape);
- ``L*``: the dormant seq-15 lanes (the enable switch and loopback gates,
  the raw-route allowlist);
- ``K*``: automation lock projection for input_select (a select_option
  observer losing the guard, or an unrelated filter gaining it);
- ``F*``: the durable options-flow ledger and its recovery sweep (correction
  2, H2-R1): a failed abort's record dropped or treated as gone, a lost open
  forgotten, an open sent without its recorded intent, the sweep unadmitted,
  unwindowed, unbounded, never run, or reaching a live flow, and a refused
  release never handed to the sweep.

Usage:
    python docs/evidence/hamcp232-mutations/run_mutations.py \\
        --work /var/tmp/hamcp232-mut --output docs/evidence/hamcp232-mutations/results.json
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
HOA = f"{E}/f3_configuration/helper_options.py"
HOC = f"{E}/governance/helper_options.py"
HOP = f"{E}/governance/helper_options_planning.py"
HOF = f"{E}/governance/helper_options_flows.py"
SVC = f"{E}/governance/service.py"
ISO = f"{E}/governance/input_select_option.py"
OPS = f"{E}/f3/operational_strategies.py"
RUN = f"{E}/f3_runtime/runtime.py"
TRC = f"{E}/script_traces.py"
LAN = "scripts/seq15_contract_acceptance.py"
EXT = f"{E}/dependency/extraction.py"
LCK = f"{E}/f3_configuration/locks.py"
STV = f"{E}/dependency/state_trigger_values.py"
ISD = f"{E}/governance/input_select_dependency.py"
PRV = f"{E}/dependency/provider.py"
ADMISSION = "admission removed"
DRIFT = "drift accepted"
CONTRACT = "contract rule loosened"
ORPHAN = "orphaned flow hidden or not released"
READBACK = "readback accepts an unverified result"
POLICY = "planning policy weakened"
TRACE = "script-trace read unsanitized or widened"
LANE = "dormant lane gate opened"
LOCK = "input_select lock projection loosened or widened"
TRIGGER = "disabled trigger disclosed as firing"
FLOWS = "options-flow record dropped or sweep widened"
LOOPBACK = '{"127.0.0.1", "localhost", "::1"}'
# Review H1 mutants S2/S6: the pre-correction parallel screen in place of the
# HAMCP-225 trace exit.
PARALLEL_SCREEN = '__import__("ha_mcp_engineering.sanitization", fromlist=["_"]).sanitize_untrusted_data'

base.MODULES = (
    "test_hamcp232_helper_options",
    "test_hamcp145_input_select_option",
    "test_hamcp232_script_traces",
    "test_hamcp232_script_trace_cross_path",
    "test_pr314_independent_review",
    "test_hamcp232_review1_superseded",
    "test_hamcp232_options_flow_ledger",
    "test_pr314_delta_cleanup",
    "test_pr314_delta_cleanup_accepted",
    "test_seq15_contract_acceptance",
    "test_f3_configuration_identity",
)

base.MUTATIONS = [
    # --- admission -------------------------------------------------------
    m("A1-helper-options-entry-admission", ADMISSION,
      (SVC, '        self._helper_options_core()\n        try:\n'
            '            request = helper_options_planning.validate_request(arguments)',
       '        try:\n            request = helper_options_planning.validate_request(arguments)')),
    m("A2-helper-options-persist-admission", ADMISSION,
      (SVC, "        # current Core lease; neither ever depends on the caller alone.\n"
            "        self._helper_options_core()\n",
       "        # current Core lease; neither ever depends on the caller alone.\n")),
    m("A3-helper-options-apply-context-admission", ADMISSION,
      (SVC, "        if helper_options:\n            self._helper_options_core()",
       "        if False:\n            self._helper_options_core()")),
    m("A4-adapter-preflight-admission", ADMISSION,
      (HOA, "        if not self._admitted():\n            return self._preflight_rejected(",
       "        if False:\n            return self._preflight_rejected(")),
    m("A5-adapter-predispatch-admission", ADMISSION,
      (HOA, "        if not self._admitted():\n            return (CORE_ADMISSION_UNAVAILABLE,)",
       "        if False:\n            return (CORE_ADMISSION_UNAVAILABLE,)")),
    m("A6-adapter-readback-admission", ADMISSION,
      (HOA, "        if not self._admitted():\n            return self._incomplete_observation(",
       "        if False:\n            return self._incomplete_observation(")),
    m("A7-runtime-helper-options-admitted-always", ADMISSION,
      (RUN, "            status = core.route_status((helper_options_contract.PROFILE,))\n"
            "        except Exception:\n            return False\n"
            '        return isinstance(status, dict) and status.get("available") is True',
       "            status = core.route_status((helper_options_contract.PROFILE,))\n"
       "        except Exception:\n            return False\n        return True")),
    m("A8-runtime-operational-profile-seam", ADMISSION,
      (RUN, "    if operational is not None:\n        profiles.add(operational)",
       "    if False:\n        profiles.add(operational)")),
    m("A9-input-select-entry-admission", ADMISSION,
      (SVC, '        self._input_select_core()\n        try:\n'
            '            request = input_select_option_planning.validate_request(arguments)',
       '        try:\n            request = input_select_option_planning.validate_request(arguments)')),
    m("A10-script-trace-profile-authority", ADMISSION,
      (TRC, '        if (status.get("available") is not True or telemetry is None',
       "        if (False or telemetry is None")),
    m("A11-script-trace-generation-recheck", ADMISSION,
      (TRC, "        if self.authority() != generation:", "        if False:")),
    m("A12-script-trace-anonymous-caller", ADMISSION,
      (TRC, '        if current_caller_id() == "anonymous":', "        if False:")),
    m("A13-retained-pre-intent-entry-admission", ADMISSION,
      (RUN, "            if task.state not in TERMINAL_TASK_STATES and self._pre_intent_task(task):\n"
            "                self._refuse_unadmitted_entry(plan)",
       "            if False:\n                self._refuse_unadmitted_entry(plan)")),
    m("A14-pre-intent-recovery-admission", ADMISSION,
      (RUN, "                        and not self._entry_admitted(plan)\n",
       "                        and False\n")),
    # --- drift -----------------------------------------------------------
    m("D1-entry-modified-at-moved", DRIFT,
      (HOA, '        if facts.modified_at != intent["modified_at"]:\n            # Core moves',
       "        if False:\n            # Core moves")),
    m("D2-held-flow-form-differs", DRIFT,
      (HOA, "                return await refuse(STALE)\n            # ``modified_at`` read after the open",
       "                pass\n            # ``modified_at`` read after the open")),
    m("D3-post-open-entry-check", DRIFT,
      (HOA, '        if code != "state_matches_plan":\n            return await refuse(code)\n        return ()',
       "        if False:\n            return await refuse(code)\n        return ()")),
    m("D4-planning-entry-moved", DRIFT,
      (HOP, "            or after.modified_at != facts.modified_at", "            or False")),
    m("D5-input-select-option-removed", DRIFT,
      (OPS, "        if not isinstance(options, list) or option not in options:",
       "        if not isinstance(options, list):")),
    m("D6-input-select-state-moved", DRIFT,
      (OPS, "        if current != _input_select_state(prepared.baseline):", "        if False:")),
    m("D7-input-select-plan-option-unoffered", DRIFT,
      (SVC, '            if request.option not in baseline["options"]:', "            if False:")),
    m("D8-input-select-predispatch-refusal-ignored", DRIFT,
      (OPS, "        refusal = await self._windowed_predispatch_refusal(prepared)\n        if refusal:",
       "        refusal = await self._windowed_predispatch_refusal(prepared)\n        if False:")),
    # --- contract --------------------------------------------------------
    m("C1-pinned-field-value-accepted", CONTRACT,
      (HOC, '    if spec.pinned:\n        raise HelperOptionsContractError("options_field_pinned"',
       '    if False:\n        raise HelperOptionsContractError("options_field_pinned"')),
    m("C2-intent-pinned-change-accepted", CONTRACT,
      (HOC, '                errors.append("options intent changes a pinned field")',
       "                pass")),
    m("C3-intent-no-op-accepted", CONTRACT,
      (HOC, '        errors.append("options intent is a no-op")', "        pass")),
    m("C4-flow-id-shape-loosened", CONTRACT,
      (HOC, 'FLOW_ID_PATTERN = re.compile(r"[0-9a-f]{32}\\Z")',
       'FLOW_ID_PATTERN = re.compile(r"[^?#]{1,64}\\Z")')),
    m("C5-visible-equal-subset", CONTRACT,
      (HOC, "    return set(left) == set(right) and all(", "    return set(left) <= set(right) and all(")),
    m("C6-submit-4xx-accepted", CONTRACT,
      (HOC, "            if type(status) is int and 400 <= status < 500:\n"
            "                return SubmitOutcome(False, SUBMIT_REJECTED_REASON, status)",
       "            if type(status) is int and 400 <= status < 500:\n"
       "                return SubmitOutcome(True, SUBMIT_REJECTED_REASON, status)")),
    m("C7-helper-options-send-boundary-unmarked", CONTRACT,
      (HOC, "        mark_provider_send()  # the exact send boundary\n        try:\n"
            "            response = await self.rest_client.request(",
       "        try:\n            response = await self.rest_client.request(")),
    m("C8-input-select-send-boundary-unmarked", CONTRACT,
      (ISO, "        mark_provider_send()  # the exact send boundary\n        try:\n"
            "            await self.websocket_client.command(payload)",
       "        try:\n            await self.websocket_client.command(payload)")),
    # --- orphans and release ---------------------------------------------
    m("O1-release-orphan-unrecorded", ORPHAN,
      (HOA, "        if not aborted:\n            self._orphaned.add(key)",
       "        if False:\n            self._orphaned.add(key)")),
    m("O2-before-dispatch-failure-flow-kept", ORPHAN,
      (HOA, "                await self._release(key, windowed=True)\n                raise",
       "                raise")),
    m("O3-before-dispatch-release-unwindowed", ORPHAN,
      (HOA, "                await self._release(key, windowed=True)\n                raise",
       "                await self._release(key)\n                raise")),
    m("O4-dispatch-orphan-disclosure-dropped", ORPHAN,
      (HOA, "            codes = (*codes, FLOW_ORPHANED)", "            codes = codes")),
    m("O5-readback-orphan-disclosure-dropped", ORPHAN,
      (HOA, "        if not aborted:\n            codes += (FLOW_ORPHANED,)",
       "        if False:\n            codes += (FLOW_ORPHANED,)")),
    m("O6-planning-orphan-count-dropped", ORPHAN,
      (HOP, "        if not aborted:\n            orphan_flows += 1",
       "        if False:\n            orphan_flows += 1")),
    # Review H2: no options flow is ever left open.
    m("O7-submit-abort-guard-skips-cancellation", ORPHAN,
      (HOA, "        except BaseException:\n            # A lost or uncertain response",
       "        except Exception:\n            # A lost or uncertain response")),
    m("O8-held-flow-registered-after-checks", ORPHAN,
      (HOA, "        self._held[key] = held\n\n        async def refuse",
       "\n        async def refuse"),
      (HOA, '            return await refuse(code)\n        return ()',
       '            return await refuse(code)\n        self._held[key] = held\n        return ()')),
    m("O9-abort-404-not-gone", ORPHAN,
      (HOC, '                details.get("status") == 404\n                and',
       "                False\n                and")),
    m("O10-lost-open-undisclosed", ORPHAN,
      (HOA, "                *((contract.FLOW_OPEN_UNCONFIRMED,) if contract.open_unconfirmed(exc) else ()),",
       "                *(),")),
    m("O11-cancelled-abort-abandoned", ORPHAN,
      (HOC, "        try:\n            await asyncio.wait({task}, timeout=ABORT_TIMEOUT_SECONDS)\n        finally:",
       "        try:\n            pass\n        finally:")),
    # --- review H3: the submit is the complete intended set ----------------
    m("I1-intent-completeness-unchecked", CONTRACT,
      (HOC, "    errors.extend(_completeness_errors(intent, step_fields(domain, step_id)))\n", "")),
    m("I2-intent-submit-incomplete-accepted", CONTRACT,
      (HOC, "    if not values_equal(intent[\"submit\"], nest(flat)):", "    if False:")),
    m("I3-intent-unrequested-change-accepted", CONTRACT,
      (HOC, "    if not visible_equal(proposed, {**flat, **defaults}):", "    if False:")),
    m("I4-intent-normalization-forgery-accepted", CONTRACT,
      (HOC, "    if set(injected) != set(defaults) or any(", "    if False and any(")),
    m("I5-submit-only-the-requested-fields", CONTRACT,
      (HOC, "    submit = nest(flat)\n    return Proposal(",
       "    submit = nest({name: flat[name] for name in set_values if name in flat})\n    return Proposal(")),
    # --- disabled-trigger disclosure -------------------------------------
    m("T1-option-effect-ignores-enablement", TRIGGER,
      (STV, "    if record.enablement == DISABLED:\n        return DISABLED_EFFECT\n"
            "    if record.enablement != ENABLED or not record.complete:",
       "    if not record.complete:")),
    m("T2-disclosure-ignores-enablement", TRIGGER,
      (ISD, '        if record.get("enablement") == "disabled":\n            effect = DISABLED_EFFECT\n'
            '        elif record.get("complete") is True and record.get("enablement") == "enabled":',
       '        if record.get("complete") is True:')),
    m("T3-automation-off-not-disabling", TRIGGER,
      (STV, '    automation_disabled = source_state == "off"', "    automation_disabled = False")),
    m("T4-provider-drops-automation-state", TRIGGER,
      (PRV, "                    secret=self.secret,\n"
            '                    source_state=state.get("state"),\n                )\n            )\n',
       "                    secret=self.secret,\n                )\n            )\n")),
    m("T5-trigger-enabled-false-ignored", TRIGGER,
      (STV, '    if enabled is False:\n        return DISABLED, "trigger_disabled"',
       "    if enabled is False:\n        return ENABLED, None")),
    # --- readback --------------------------------------------------------
    m("R1-helper-readback-entry-unhealthy-accepted", READBACK,
      (HOA, "        if proposed and moved and loaded and registered:", "        if proposed and moved:")),
    m("R2-helper-readback-unmoved-accepted", READBACK,
      (HOA, "        if proposed and moved and loaded and registered:",
       "        if proposed and loaded and registered:")),
    m("R3-input-select-readback-unverified", READBACK,
      (OPS, "        # An error result is never proof: this exact readback decides.\n"
            '        verified = state.get("state") == prepared.requested_name',
       "        # An error result is never proof: this exact readback decides.\n"
       "        verified = True")),
    m("R4-input-select-rejection-treated-unsent", READBACK,
      (ISO, '                "provider_rejected" if confirmed else "dispatch_indeterminate",\n'
            "                dispatched=True,",
       '                "provider_rejected" if confirmed else "dispatch_indeterminate",\n'
       "                dispatched=False,")),
    # --- planning policy -------------------------------------------------
    m("P1-target-limit-widened", POLICY,
      (HOP, "    if len(changes) > MAX_CHANGES:", "    if len(changes) > MAX_CHANGES + 1:")),
    m("P2-mixed-domains-accepted", POLICY,
      (HOP, '            raise Refusal("helper_options_mixed_domains", **target)', "            pass")),
    m("P3-classification-not-elevated", POLICY,
      (HOP, '        "policy": "elevated" if basis else "standard",', '        "policy": "standard",')),
    m("P4-od7-disclosure-dropped", POLICY,
      (HOP, "        warnings = [CONCURRENCY_DISCLOSURE, HIDDEN_KEYS_DISCLOSURE,",
       "        warnings = [HIDDEN_KEYS_DISCLOSURE,")),
    m("P5-injected-defaults-undisclosed", POLICY,
      (HOP, '            "normalization_changes": normalization,', '            "normalization_changes": [],')),
    m("P6-domain-vocabulary-widened", POLICY,
      (HOC, 'DOMAINS = ("filter", "statistics", "template")',
       'DOMAINS = ("filter", "statistics", "template", "utility_meter")')),
    m("P7-template-type-vocabulary-widened", POLICY,
      (HOC, 'TEMPLATE_TYPES = ("binary_sensor", "sensor")',
       'TEMPLATE_TYPES = ("binary_sensor", "sensor", "switch")')),
    # --- script traces ---------------------------------------------------
    m("S1-exit-known-secrets-dropped", TRACE,
      (TRC, "        return sanitize_trace_detail(value, known_secrets=self.known_secrets)",
       "        return sanitize_trace_detail(value, known_secrets=())")),
    m("S2-list-bypasses-shared-sanitizer", TRACE,
      (TRC, '        sanitation = self._exit({"script_id": payload["item_id"], "traces": result})',
       f"        sanitation = {PARALLEL_SCREEN}(\n"
       '            {"script_id": payload["item_id"], "traces": result},\n'
       "            known_secrets=self.known_secrets, max_string=2000)")),
    m("S3-get-trace-unsanitized", TRACE,
      (TRC, '            "trace": screened["trace"],', '            "trace": result,')),
    m("S4-trace-not-found-unmapped", TRACE,
      (TRC, '                if payload["type"] == GET_COMMAND and details.get("status") == 404',
       "                if False")),
    m("S5-script-id-shape-loosened", TRACE,
      (TRC, 'SCRIPT_ID_PATTERN = re.compile(r"[a-z0-9_]{1,128}\\Z")',
       'SCRIPT_ID_PATTERN = re.compile(r".{1,128}\\Z")')),
    m("S6-get-bypasses-shared-sanitizer", TRACE,
      (TRC, "        sanitation = self._exit(\n"
            '            {"script_id": payload["item_id"], "run_id": payload["run_id"], "trace": result}\n'
            "        )",
       f"        sanitation = {PARALLEL_SCREEN}(\n"
       '            {"script_id": payload["item_id"], "run_id": payload["run_id"], "trace": result},\n'
       "            known_secrets=self.known_secrets, max_string=2000)")),
    m("S7-get-identifiers-unscreened", TRACE,
      (TRC, '            "script_id": screened.get("script_id"),\n'
            '            "run_id": screened.get("run_id"),',
       '            "script_id": payload["item_id"],\n'
       '            "run_id": payload["run_id"],')),
    m("S8-list-identifier-unscreened", TRACE,
      (TRC, '            "script_id": screened.get("script_id"),\n'
            '            "traces": [header.public() for header in normalized.headers],',
       '            "script_id": payload["item_id"],\n'
       '            "traces": [header.public() for header in normalized.headers],')),
    # --- dormant lanes ---------------------------------------------------
    m("L1-lanes-enabled-by-default", LANE,
      (LAN, "    return os.environ.get(ENABLE_FLAG) == ENABLED", "    return True")),
    m("L2-raw-client-loopback-dropped", LANE,
      (LAN, f"        if urlsplit(self.api_url).hostname not in {LOOPBACK}:", "        if False:")),
    m("L3-lane-guard-loopback-dropped", LANE,
      (LAN, f"            or urlsplit(configured.ha_url).hostname not in {LOOPBACK}", "            or False")),
    m("L4-raw-route-state-widened", LANE,
      (LAN, r'    r"|/states/(?:input_select|script)\.seq15_[0-9a-z_]{1,64}")',
       r'    r"|/states/(?:input_select|script)\.[0-9a-z_]{1,64}")')),
    # --- input_select lock projection ------------------------------------
    m("K1-select-option-observer-unguarded", LOCK,
      (LCK, "                if not call_service_exclusions_cover_input_select(config):",
       "                if False:")),
    m("K2-select-option-service-dropped", LOCK,
      (EXT, 'INPUT_SELECT_EVENT_SERVICES = frozenset({"select_option"})',
       'INPUT_SELECT_EVENT_SERVICES = frozenset()')),
    m("K3-every-exclusion-guarded", LOCK,
      (LCK, "                if not call_service_exclusions_cover_input_select(config):",
       "                if True:")),
    # --- correction 2 (H2-R1): the durable options-flow ledger ---------------
    m("F1-failed-abort-record-dropped", FLOWS,
      (HOF, '            if record["status"] == STATUS_OPEN:\n'
            '                record["status"] = STATUS_ABORT_FAILED\n            return True',
       '            return False')),
    m("F2-failed-abort-recorded-as-gone", FLOWS,
      (HOC, "            if gone:\n                ledger.flow_gone(flow_id)",
       "            if True:\n                ledger.flow_gone(flow_id)")),
    m("F3-lost-open-intent-forgotten", FLOWS,
      (HOC, "ledger.open_failed(record_id, unconfirmed=open_unconfirmed(exc))",
       "ledger.open_failed(record_id, unconfirmed=False)")),
    m("F4-open-sent-without-recorded-intent", FLOWS,
      (HOC, "        except flows.FlowLedgerError:\n"
            "            raise HelperOptionsProviderError(FLOW_LEDGER_UNAVAILABLE) from None\n"
            "        try:\n            response = await",
       "        except flows.FlowLedgerError:\n            record_id = \"\"\n"
       "        try:\n            response = await")),
    m("F5-sweep-admission-skipped", FLOWS,
      (RUN, "            if not ledger.retry_candidates() or not self._helper_options_admitted():",
       "            if not ledger.retry_candidates():")),
    m("F6-sweep-abort-unwindowed", FLOWS,
      (HOC, '                async with lease(record["entry_id"]):',
       '                async with __import__("contextlib").nullcontext():')),
    m("F7-sweep-retries-unbounded", FLOWS,
      (HOF, '                if record["attempts"] >= MAX_SWEEP_ABORT_ATTEMPTS',
       "                if False")),
    m("F8-sweep-never-runs", FLOWS,
      (RUN, "            await self._retry_helper_options_flows(deadline_reached)",
       "            pass")),
    m("F9-sweep-reaches-a-live-flow", FLOWS,
      (HOF, '            if record["status"] in RETRY_STATUSES and record["record_id"] not in self.live',
       '            if record["status"] in RETRY_STATUSES')),
    m("F10-refused-release-not-handed-to-sweep", FLOWS,
      (HOA, "            self.gateway.options_flow_unreleased(held.flow_id)\n", "")),
]

_run_tests = base.run_tests


def run_tests(tree: Path, timeout: int, *, failfast: bool):
    """The seq-15 receipt names the source commit: the copy gets its own one-commit repository."""

    if not (tree / ".git").exists():
        git = ["git", "-c", "user.name=mutation", "-c", "user.email=mutation@invalid",
               "-c", "commit.gpgsign=false"]
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "mutation baseline"]):
            base.subprocess.run(git + command, cwd=tree, check=True, capture_output=True)
    return _run_tests(tree, timeout, failfast=failfast)


base.run_tests = run_tests

if __name__ == "__main__":
    raise SystemExit(base.main())
