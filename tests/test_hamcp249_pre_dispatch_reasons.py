"""HAMCP-249: every pre-dispatch refusal records a named reason.

Reporting only: each refusal path keeps refusing exactly as before, and the
reason reaches the F3 child, the parent task, the apply result, the
``preflight_failed`` lifecycle event and the add-on log.
"""

from __future__ import annotations

import ast
import copy
import json
import logging
from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime import pre_dispatch_reasons as reasons  # noqa: E402
from ha_mcp_engineering.f3.executor import DispatchAuthorityRefused  # noqa: E402
from ha_mcp_engineering.f3.locks import (  # noqa: E402
    DurableLockStore,
    LockConflict,
    LockStorageError,
    LockWaitTimeout,
)
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance import normalize  # noqa: E402
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
)
from ha_mcp_engineering.logging_config import JsonFormatter  # noqa: E402
from ha_mcp_engineering.tools import governance as governance_tools  # noqa: E402
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
    PROPOSED_AUTOMATION,
    PROPOSED_SCRIPT,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)


SECRETS = ("dev14-test-access-secret", "dev14-test-ha-token")
UNTRUSTED_TEXT = "hamcp249-untrusted-provider-text dev14-test-ha-token"


def _assert_logged_reason(test, record, reason):
    """The add-on log line carries the persisted bounded reason (R1-M2):
    in its context and in the serialized line, and nothing untrusted."""

    context = record.safe_context
    test.assertEqual(context["error_code"], reason["error_code"])
    test.assertEqual(context["check"], reason["check"])
    test.assertEqual(context["failure_information"], reason)
    serialized = JsonFormatter().format(record)
    payload = json.loads(serialized)
    test.assertEqual(payload["context"]["failure_information"], reason)
    test.assertEqual(payload["context"]["check"], reason["check"])
    test.assertNotIn("hamcp249", serialized)
    test.assertNotIn("Apply HVAC comfort", serialized)
    for secret in SECRETS:
        test.assertNotIn(secret, serialized)


def _logged(logs, event, task_id):
    return [
        record for record in logs.records
        if getattr(record, "event", None) == event
        and getattr(record, "safe_context", {}).get("task_id") == task_id
    ]


class ReasonVocabularyTests(unittest.TestCase):
    def test_every_vocabulary_code_classifies_to_its_check(self):
        for code, check in reasons.REASON_CHECKS.items():
            with self.subTest(code=code):
                self.assertEqual(reasons.classify((code,)), (code, check, None))
                self.assertIn(check, reasons._CHECK_DETAILS)

    def test_priority_is_independent_of_sorted_code_order(self):
        # Stored codes are sorted; the vocabulary order picks the primary.
        self.assertEqual(
            reasons.classify(("lock_cleanup_released", "lock_storage_failure"))[0],
            "lock_storage_failure",
        )
        self.assertEqual(
            reasons.classify(
                (
                    "automation_verifier_invalid_automation_wait_trigger_action",
                    "behavioral_mismatch",
                    "configuration_not_verifiable",
                )
            ),
            (
                "configuration_not_verifiable",
                "readback_verifiability",
                "automation_verifier_invalid_automation_wait_trigger_action",
            ),
        )

    def test_the_earlier_vocabulary_code_always_names_the_reason(self):
        ordered = [code for code, _check in reasons._REASONS]
        for index, first in enumerate(ordered):
            for later in ordered[index + 1:]:
                with self.subTest(first=first, later=later):
                    self.assertEqual(
                        reasons.classify(tuple(sorted((first, later))))[0], first
                    )
        # The live candidates: the verifier refusal outranks the state read.
        self.assertEqual(
            reasons.classify(("configuration_not_verifiable", "stale_target_state"))[1],
            "readback_verifiability",
        )

    def test_restated_bounds_equal_the_execution_record_bounds(self):
        from ha_mcp_engineering.f3 import models

        self.assertEqual(reasons.EVIDENCE_PATTERN.pattern, models.EVIDENCE_PATTERN.pattern)
        self.assertEqual(reasons.MAX_DIAGNOSTIC_ITEMS, models.MAX_DIAGNOSTIC_ITEMS)
        self.assertEqual(reasons.MAX_DIAGNOSTIC_LENGTH, models.MAX_DIAGNOSTIC_LENGTH)
        samples = (
            None, "lock_conflict", ("b", "a", "a"), ["Bad", "ok_code", 7, ""],
            [f"code_{index:02d}" for index in range(40)], ["x" * 200],
            ["z" + "y" * 95, "lock\nconflict"],
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    reasons.bounded_diagnostics(sample),
                    models.bounded_diagnostics(sample),
                )

    def test_reasons_module_imports_nothing_from_f3(self):
        # governance/service.py imports it; HAMCP-181 design section 5 keeps
        # ..f3 out of the service's import graph.
        tree = ast.parse(
            (
                ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
                / "f3_runtime" / "pre_dispatch_reasons.py"
            ).read_text(encoding="utf-8")
        )
        imported = {
            (node.level, node.module or "")
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {
            (0, alias.name)
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertEqual(
            imported, {(0, "__future__"), (0, "re"), (0, "typing")}
        )

    def test_unknown_and_missing_codes_never_fabricate_a_reason(self):
        self.assertEqual(
            reasons.classify(("some_future_code",)),
            (reasons.UNCLASSIFIED, "adapter_preflight", None),
        )
        for codes in (None, ()):
            self.assertEqual(
                reasons.classify(codes),
                (reasons.UNKNOWN_LEGACY, reasons.UNKNOWN_LEGACY, None),
            )
        legacy = reasons.unknown_legacy()
        self.assertEqual(legacy["error_code"], "unknown_legacy")
        self.assertEqual(legacy["reason_source"], "unknown_legacy")
        self.assertEqual(legacy["diagnostic_codes"], [])

    def test_details_are_fixed_bounded_text(self):
        for check, detail in reasons._CHECK_DETAILS.items():
            with self.subTest(check=check):
                self.assertLessEqual(len(detail), 160)
                self.assertTrue(detail.isascii())
        longest = max(len(item) for item in reasons.AUTOMATION_VERIFIER_DETAILS)
        self.assertLessEqual(
            max(len(item) for item in reasons._CHECK_DETAILS.values())
            + len(" Verifier rule: .")
            + longest,
            320,
        )

    def test_verifier_details_match_the_normalizer_literals(self):
        tree = ast.parse(Path(normalize.__file__).read_text(encoding="utf-8"))
        messages = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None)
                == "AutomationVerificationNormalizationError"
            ):
                self.assertEqual(len(node.args), 1)
                self.assertIsInstance(node.args[0], ast.Constant)
                messages.add(node.args[0].value)
        self.assertEqual(
            {
                "automation_verifier_"
                + message.replace("-", "_").replace(" ", "_")
                for message in messages
            },
            set(reasons.AUTOMATION_VERIFIER_DETAILS),
        )

    def test_verifier_detail_code_names_the_rule_only(self):
        config = copy.deepcopy(PROPOSED_AUTOMATION)
        config["action"] = [{"wait_for_trigger": {"platform": "state"}}]
        self.assertEqual(
            normalize.automation_verification_detail_code(config),
            "automation_verifier_invalid_automation_wait_trigger_action",
        )
        self.assertIsNone(
            normalize.automation_verification_detail_code(PROPOSED_AUTOMATION)
        )

    def test_recorded_rejects_foreign_or_out_of_vocabulary_values(self):
        good = reasons.unknown_legacy()
        self.assertEqual(reasons.recorded(good), good)
        self.assertIsNone(reasons.recorded({**good, "reason_model": "other"}))
        self.assertIsNone(reasons.recorded({**good, "error_code": "free text"}))
        self.assertIsNone(reasons.recorded("lock_conflict"))
        # Only the reviewed fields are read back; anything else is dropped.
        self.assertEqual(reasons.recorded({**good, "payload": UNTRUSTED_TEXT}), good)

    def test_log_reason_takes_only_reviewed_fields(self):
        """R1-M2: the log and audit context take a built F3 or legacy F1
        reason in its reviewed fields only, and nothing else."""

        target = {"target_type": "automation", "target_id": "a1"}
        legacy = reasons.legacy_f1_reason("stale_target_state", target)
        self.assertEqual(reasons.log_reason(legacy), legacy)
        built = reasons.unknown_legacy()
        self.assertEqual(reasons.log_reason(built), built)
        self.assertEqual(
            reasons.log_reason({**legacy, "payload": UNTRUSTED_TEXT}), legacy
        )
        for value in (
            None,
            "stale_target_state",
            {**legacy, "reason_model": "other"},
            {**legacy, "error_code": "Free text: " + UNTRUSTED_TEXT},
            {**legacy, "error_code": None},
            {**legacy, "check": "free_text_check"},
            {**legacy, "check": None},
        ):
            with self.subTest(value=value):
                self.assertIsNone(reasons.log_reason(value))

    def test_legacy_f1_reasons(self):
        target = {"target_type": "automation", "target_id": "a1"}
        self.assertEqual(
            reasons.legacy_f1_refusal(None, target)["error_code"],
            "unknown_legacy",
        )
        self.assertEqual(
            reasons.legacy_f1_refusal({"error_code": None}, target)["error_code"],
            "unknown_legacy",
        )
        named = reasons.legacy_f1_refusal(
            {"error_code": "stale_plan"}, target
        )
        self.assertEqual(named["error_code"], "stale_plan")
        self.assertEqual(named["check"], "legacy_f1_apply")
        self.assertEqual(named["target_id"], "a1")
        self.assertEqual(
            reasons.legacy_f1_refusal(
                {"error_code": "Free text: " + UNTRUSTED_TEXT}, target
            )["error_code"],
            "unknown_legacy",
        )
        stored = reasons.legacy_f1_reason("stale_plan", target)
        self.assertEqual(
            reasons.legacy_f1_refusal({"failure_information": stored}, target),
            stored,
        )

    def test_identifiers_drop_untrusted_text(self):
        reason = reasons.build_reason(
            codes=("lock_conflict",),
            declaration={
                "child_id": "child\nwith newline",
                "operation_id": "x" * 400,
                "operation_ordinal": True,
                "target_id": UNTRUSTED_TEXT,
            },
            normalized_outcome="Not An Identifier",
        )
        self.assertIsNone(reason["child_execution_id"])
        self.assertEqual(len(reason["operation_id"]), 160)
        self.assertIsNone(reason["ordinal"])
        self.assertIsNone(reason["target_id"])
        self.assertIsNone(reason["normalized_outcome"])


def _wait_dict_automation():
    config = copy.deepcopy(PROPOSED_AUTOMATION)
    config["action"] = [
        {
            "wait_for_trigger": {
                "platform": "state",
                "entity_id": "input_boolean.hvac_override",
            }
        },
        {"service": "script.set_hvac_comfort"},
    ]
    return config


def _preflight_raises(exc):
    return patch.object(
        ConfigurationOperationAdapter, "preflight", AsyncMock(side_effect=exc)
    )


def _lock_raises(exc):
    return patch.object(DurableLockStore, "acquire", AsyncMock(side_effect=exc))


def _dispatch_refuses_pre_intent():
    return patch.object(
        ConfigurationOperationAdapter,
        "dispatch",
        AsyncMock(
            return_value=SimpleNamespace(
                outcome="failed_pre_dispatch",
                dispatch_intent_recorded=False,
                mutating_invocation_count=0,
                may_have_dispatched=False,
                provider_response_received=False,
                diagnostic_codes=("stale_target_state",),
            )
        ),
    )


def _dispatch_raises_before_boundary():
    return patch.object(
        ConfigurationOperationAdapter,
        "dispatch",
        AsyncMock(side_effect=RuntimeError(UNTRUSTED_TEXT)),
    )


class _Case(SimpleNamespace):
    pass


# Part 1 trace rows (handoff table) -> injected condition -> expected reason.
REFUSAL_CASES = (
    _Case(
        path="E1 lock conflict",
        arrange=lambda test: _lock_raises(LockConflict(("automation:x",))),
        code="lock_conflict", check="lock_acquisition", outcome="lock_conflict",
    ),
    _Case(
        path="E1 lock wait timeout",
        arrange=lambda test: _lock_raises(LockWaitTimeout(UNTRUSTED_TEXT)),
        code="wait_timeout", check="lock_acquisition", outcome="lock_conflict",
    ),
    _Case(
        path="E2 lock storage failure",
        arrange=lambda test: _lock_raises(LockStorageError(UNTRUSTED_TEXT)),
        code="lock_storage_failure", check="lock_storage", outcome=None,
        # Existing behavior: a lock-storage failure faults execution
        # readiness, so a repeat apply refuses before reaching the task.
        storage_fault=True,
    ),
    _Case(
        path="E4 readback verifier (real condition)",
        arrange="wait_dict",
        code="configuration_not_verifiable", check="readback_verifiability",
        outcome="preflight_rejected",
        rule="automation_verifier_invalid_automation_wait_trigger_action",
    ),
    _Case(
        path="E4 Home Assistant check_config (real condition)",
        arrange="check_config",
        code="configuration_validation_failed",
        check="home_assistant_config_check", outcome="preflight_rejected",
    ),
    _Case(
        path="E4 stale target state (real condition)",
        arrange="stale_target",
        code="stale_target_state", check="target_state",
        outcome="preflight_rejected",
    ),
    _Case(
        path="E5 preflight raised",
        arrange=lambda test: _preflight_raises(RuntimeError(UNTRUSTED_TEXT)),
        code="preflight_failed", check="preflight_exception",
        outcome="provider_unavailable_pre_dispatch",
    ),
    _Case(
        path="E6 dispatch authority refused in preflight",
        arrange=lambda test: _preflight_raises(
            DispatchAuthorityRefused("core_authority_unavailable")
        ),
        code="core_authority_unavailable", check="dispatch_authority",
        outcome="provider_unavailable_pre_dispatch",
    ),
    _Case(
        path="E6 out-of-vocabulary authority refusal collapses",
        arrange=lambda test: _preflight_raises(
            DispatchAuthorityRefused(UNTRUSTED_TEXT)
        ),
        code="dispatch_authority_unavailable", check="dispatch_authority",
        outcome="provider_unavailable_pre_dispatch",
    ),
    _Case(
        path="E8 adapter pre-intent refusal at dispatch",
        arrange=lambda test: _dispatch_refuses_pre_intent(),
        code="stale_target_state", check="target_state",
        outcome="failed_pre_dispatch",
    ),
    _Case(
        path="E9 adapter failed before the irreversible boundary",
        arrange=lambda test: _dispatch_raises_before_boundary(),
        code="adapter_failed_before_irreversible_boundary",
        check="adapter_pre_dispatch", outcome="failed_pre_dispatch",
    ),
    _Case(
        path="E13 active legacy task conflict (real condition)",
        arrange="legacy_conflict",
        code="legacy_active_task_conflict", check="legacy_task_conflict",
        outcome="preflight_rejected",
    ),
)


class PreDispatchReasonSurfaceTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def _plan(self, proposed=None):
        created = await self.service.create_configuration_plan(
            title="HAMCP-249 refusal fixture",
            description="Pre-dispatch refusal reason reporting",
            operations=[
                {
                    "operation_id": "update_comfort_automation",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "apply_hvac_comfort",
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(
                        proposed or PROPOSED_AUTOMATION
                    ),
                }
            ],
        )
        await self.approve(created)
        return created

    async def _arrange(self, case):
        if case.arrange == "wait_dict":
            return await self._plan(_wait_dict_automation()), None
        if case.arrange == "legacy_conflict":
            # The active legacy F1 task predates the current plan, as in
            # test_active_legacy_configuration_task_blocks_conflicting_f3_plan.
            historical = await self.create_automation_plan()
            await self.approve(historical)
            legacy = self.service._create_task_for_plan(
                self.service._load(historical["plan_id"]),
                historical["plan_hash"],
            )
            self.service._record_task_event(
                legacy,
                "preflight_started",
                new_state=ExecutionTaskState.PREFLIGHT,
                changes={"started_at": self.service._timestamp()},
            )
            return await self._plan(), None
        created = await self._plan()
        if case.arrange == "check_config":
            self.gateway.validation_result = {
                "result": "invalid",
                "errors": UNTRUSTED_TEXT,
            }
            return created, None
        if case.arrange == "stale_target":
            key = ("automation", "apply_hvac_comfort")
            self.gateway.configs[key] = {
                **self.gateway.configs[key],
                "description": UNTRUSTED_TEXT,
            }
            return created, None
        return created, case.arrange(self)

    def _assert_reason(self, reason, case, *, surface):
        self.assertIsInstance(reason, dict, surface)
        self.assertEqual(sorted(reason), sorted(reasons.REASON_FIELDS), surface)
        self.assertEqual(reason["error_code"], case.code, surface)
        self.assertEqual(reason["check"], case.check, surface)
        self.assertEqual(reason["reason_source"], "child_terminal_event", surface)
        self.assertEqual(reason["operation_id"], "update_comfort_automation")
        self.assertEqual(reason["target_type"], "automation")
        self.assertEqual(reason["target_id"], "apply_hvac_comfort")
        self.assertIn(case.code, reason["diagnostic_codes"], surface)
        self.assertEqual(
            reason["verifier_rule"], getattr(case, "rule", None), surface
        )
        if getattr(case, "rule", None):
            self.assertIn("Verifier rule: invalid automation wait trigger action.", reason["detail"])
            # The comparison's closed mismatch category is kept with the rule.
            self.assertEqual(
                sorted(reason["diagnostic_codes"]),
                sorted((case.code, case.rule, "automation_verification_structure")),
                surface,
            )
        if case.outcome is not None:
            self.assertEqual(reason["normalized_outcome"], case.outcome)
        text = json.dumps(reason, sort_keys=True)
        self.assertNotIn("hamcp249", text)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertNotIn("Apply HVAC comfort", text)

    async def test_every_refusal_path_names_its_reason_on_every_surface(self):
        for case in REFUSAL_CASES:
            with self.subTest(path=case.path):
                await self.asyncTearDown()
                await self.asyncSetUp()
                created, patcher = await self._arrange(case)
                with self.assertLogs(
                    "ha_mcp_engineering.governance", level=logging.INFO
                ) as logs:
                    if patcher is None:
                        result = await self.service.apply(
                            created["plan_id"], created["plan_hash"]
                        )
                    else:
                        with patcher:
                            result = await self.service.apply(
                                created["plan_id"], created["plan_hash"]
                            )

                self.assertEqual(result["task_state"], "failed_pre_dispatch")
                self.assertEqual(
                    sum(call[0] == "write" for call in self.gateway.calls), 0
                )
                # apply_change_plan result
                self._assert_reason(
                    result["pre_dispatch_refusal"], case, surface="result"
                )
                self.assertEqual(
                    result["execution_task"]["last_error"]["error_code"],
                    case.code,
                )
                # parent task (stored)
                task = self.service.task_repository.get(result["task_id"])
                self._assert_reason(
                    task.verification_summary["pre_dispatch_refusal"],
                    case, surface="verification_summary",
                )
                self.assertEqual(task.last_error["error_code"], case.code)
                self.assertEqual(task.last_error["failure_category"], case.check)
                self._assert_reason(
                    task.last_error["failure_information"],
                    case, surface="last_error",
                )
                # F3 child (get_execution_task projection)
                public = self.service.get_execution_task(result["task_id"])
                self._assert_reason(
                    public["f3_children"][0]["pre_dispatch_reason"],
                    case, surface="child",
                )
                self._assert_reason(
                    public["pre_dispatch_refusal"], case, surface="task view"
                )
                # preflight_failed lifecycle event
                events = [
                    event for event in task.events
                    if event.event_type == "preflight_failed"
                ]
                self.assertEqual(len(events), 1)
                self.assertEqual(
                    events[0].changes["last_error"]["error_code"], case.code
                )
                self._assert_reason(
                    events[0].changes["verification_summary"][
                        "pre_dispatch_refusal"
                    ],
                    case, surface="event",
                )
                # add-on log and durable audit
                logged = [
                    record for record in logs.records
                    if getattr(record, "event", None) == "preflight_failed"
                ]
                self.assertEqual(len(logged), 1)
                self.assertEqual(logged[0].safe_context["error_code"], case.code)
                _assert_logged_reason(
                    self, logged[0], task.last_error["failure_information"]
                )
                audited = [
                    entry for entry in self._audit_entries()
                    if entry.get("event") == "preflight_failed"
                ]
                self.assertEqual(len(audited), 1)
                self.assertEqual(audited[0]["error_code"], case.code)
                self.assertEqual(
                    audited[0]["failure_information"],
                    task.last_error["failure_information"],
                )
                artifacts = self.persisted_artifact_text()
                self.assertNotIn("hamcp249-untrusted", artifacts)
                # A repeated apply reports the same stored reason.
                if getattr(case, "storage_fault", False):
                    self.assertFalse(
                        self.runtime.readiness_state()["execution_ready"]
                    )
                    continue
                with self.assertRaises(GovernanceError) as repeated:
                    await self.service.apply(
                        created["plan_id"], created["plan_hash"]
                    )
                self.assertEqual(
                    repeated.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT
                )
                details = repeated.exception.details or {}
                self._assert_reason(
                    details.get("pre_dispatch_refusal"),
                    case, surface="repeat apply " + repr(repeated.exception),
                )

    def _audit_entries(self):
        entries = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            entries.append(value.get("record", value) if isinstance(value, dict) else {})
        return entries

    async def test_e10_expired_unclaimed_child_names_plan_expiry(self):
        created = await self._plan()
        plan = self.service._load(created["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, created["plan_hash"]
        )
        start = self.service.now()
        self.service.now = lambda: start + timedelta(days=30)
        case = _Case(
            code="plan_expired", check="plan_expiry", outcome=None,
        )
        with self.assertLogs(
            "ha_mcp_engineering.governance", level=logging.INFO
        ) as logs:
            await self.runtime.recover_once("test")
        stored = self.service.task_repository.get(task.task_id)
        self.assertEqual(stored.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(stored.last_error["error_code"], "plan_expired")
        self._assert_reason(
            stored.verification_summary["pre_dispatch_refusal"],
            case, surface="verification_summary",
        )
        public = self.service.get_execution_task(task.task_id)
        self._assert_reason(
            public["f3_children"][0]["pre_dispatch_reason"], case, surface="child"
        )
        [event] = [
            item for item in stored.events if item.event_type == "preflight_failed"
        ]
        self.assertEqual(event.changes["last_error"]["error_code"], "plan_expired")
        self.assertEqual(
            [
                record.safe_context["error_code"]
                for record in logs.records
                if getattr(record, "event", None) == "preflight_failed"
            ],
            ["plan_expired"],
        )
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def test_valid_plan_still_applies_without_a_refusal(self):
        created = await self._plan()
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertNotIn("pre_dispatch_refusal", result)
        public = self.service.get_execution_task(result["task_id"])
        self.assertNotIn("pre_dispatch_refusal", public)
        self.assertIsNone(public["f3_children"][0]["pre_dispatch_reason"])
        task = self.service.task_repository.get(result["task_id"])
        self.assertNotIn("pre_dispatch_refusal", task.verification_summary)
        self.assertIsNone(task.last_error)
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"],
            [("write", "update", "automation", "apply_hvac_comfort")],
        )
        self.assertEqual(
            self.gateway.configs[("automation", "apply_hvac_comfort")]["action"],
            PROPOSED_AUTOMATION["action"],
        )

    async def _reconcile_terminal_projection(self, task_id):
        """Drive the terminal-recovery projection: a known child fault on a
        terminal parent is reconciled exactly, and only an exact projection
        clears the fault."""
        task = self.service.task_repository.get(task_id)
        declarations, _records = self.runtime._validate_sequence_state(task)
        keys = ["recovery_child:" + item["child_id"] for item in declarations]
        for key in keys:
            self.runtime._readiness_fault(key)
        await self.runtime.recover_once("test")
        for key in keys:
            self.assertNotIn(key, self.runtime._readiness_faults)

    async def _legacy_shaped_failed_task(self):
        created = await self._plan()
        with _preflight_raises(RuntimeError(UNTRUSTED_TEXT)):
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        task = self.service.task_repository.get(result["task_id"])
        # Plant the pre-HAMCP-249 writer's shape (base d2158ace): the
        # preflight_failed event and task carried no last_error change and no
        # summary reason.  Only the two HAMCP-249 additions are removed; the
        # record is planted as already-stored history (append-only storage
        # refuses rewriting it through save).
        record = task.to_dict()
        record["last_error"] = None
        record["verification_summary"].pop("pre_dispatch_refusal")
        for event in record["events"]:
            if event["event_type"] == "preflight_failed":
                event["changes"].pop("last_error")
                event["changes"]["verification_summary"].pop(
                    "pre_dispatch_refusal"
                )
        repository = self.service.task_repository
        repository._path(task.task_id, plan_id=task.plan_id).write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        planted = repository.get(task.task_id)
        self.assertIsNone(planted.last_error)
        self.assertNotIn("pre_dispatch_refusal", planted.verification_summary)
        return created, planted

    async def test_legacy_reasonless_task_reads_the_durable_child_reason(self):
        _created, task = await self._legacy_shaped_failed_task()
        before = self.service.task_repository.get(task.task_id).to_dict()
        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(public["pre_dispatch_refusal"]["error_code"], "preflight_failed")
        self.assertEqual(
            public["f3_children"][0]["pre_dispatch_reason"]["error_code"],
            "preflight_failed",
        )
        self.assertIsNone(public["last_error"])
        # Terminal recovery projects without raising or rewriting: adding a
        # reason to stored history would leave the child fault unresolved.
        await self._reconcile_terminal_projection(task.task_id)
        self.assertEqual(
            self.service.task_repository.get(task.task_id).to_dict(), before
        )

    async def test_the_persisted_reason_wins_over_the_derived_one(self):
        created = await self._plan()
        with _preflight_raises(RuntimeError(UNTRUSTED_TEXT)):
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        task = self.service.task_repository.get(result["task_id"])
        record = task.to_dict()
        # Plant stored history whose reviewed reason differs from what the
        # child's codes derive; the stored reason is what is reported.
        stored = {
            **record["verification_summary"]["pre_dispatch_refusal"],
            "error_code": "plan_expired",
        }
        record["verification_summary"]["pre_dispatch_refusal"] = stored
        for event in record["events"]:
            if event["event_type"] == "preflight_failed":
                event["changes"]["verification_summary"][
                    "pre_dispatch_refusal"
                ] = stored
        repository = self.service.task_repository
        repository._path(task.task_id, plan_id=task.plan_id).write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(public["pre_dispatch_refusal"], stored)
        self.assertEqual(
            public["f3_children"][0]["pre_dispatch_reason"]["error_code"],
            "preflight_failed",
        )

    async def test_legacy_task_without_any_codes_reads_unknown_legacy(self):
        with patch.object(reasons, "_terminal_codes", return_value=None):
            _created, task = await self._legacy_shaped_failed_task()
            public = self.service.get_execution_task(task.task_id)
        self.assertEqual(public["pre_dispatch_refusal"]["error_code"], "unknown_legacy")
        self.assertEqual(
            public["pre_dispatch_refusal"]["reason_source"], "unknown_legacy"
        )
        self.assertEqual(public["pre_dispatch_refusal"]["diagnostic_codes"], [])

    async def _two_operation_plan(self):
        created = await self.service.create_configuration_plan(
            title="HAMCP-249 partial sequence",
            description="Verified first child; stale second child",
            operations=[
                {
                    "operation_id": "first",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "apply_hvac_comfort",
                    "depends_on": [],
                    "proposed_config": {
                        **copy.deepcopy(PROPOSED_AUTOMATION),
                        "action": [
                            {
                                "action": "persistent_notification.create",
                                "data": {"message": "First child"},
                            }
                        ],
                    },
                },
                {
                    "operation_id": "second",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "set_hvac_comfort",
                    "depends_on": ["first"],
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                },
            ],
        )
        await self.approve(created)
        return created

    def _stale_second_preflight(self):
        original = ConfigurationOperationAdapter.preflight
        gateway = self.gateway

        async def stale_second(adapter, operation, **kwargs):
            if operation.target.target_id == "set_hvac_comfort":
                key = ("script", "set_hvac_comfort")
                gateway.configs[key] = {
                    **gateway.configs[key], "description": UNTRUSTED_TEXT,
                }
            return await original(adapter, operation, **kwargs)

        return patch.object(ConfigurationOperationAdapter, "preflight", stale_second)

    def _assert_second_child_reason(self, reason, *, surface):
        self.assertIsInstance(reason, dict, surface)
        self.assertEqual(sorted(reason), sorted(reasons.REASON_FIELDS), surface)
        self.assertEqual(reason["error_code"], "stale_target_state", surface)
        self.assertEqual(reason["check"], "target_state", surface)
        self.assertEqual(reason["reason_source"], "child_terminal_event", surface)
        self.assertEqual(reason["normalized_outcome"], "preflight_rejected", surface)
        self.assertEqual(reason["operation_id"], "second", surface)
        self.assertEqual(reason["ordinal"], 1, surface)
        self.assertEqual(reason["target_type"], "script", surface)
        self.assertEqual(reason["target_id"], "set_hvac_comfort", surface)
        text = json.dumps(reason, sort_keys=True)
        self.assertNotIn("hamcp249", text, surface)
        for secret in SECRETS:
            self.assertNotIn(secret, text, surface)

    async def test_later_child_refusal_after_a_completed_child_names_its_reason(self):
        """R1-M1: the second child is refused before dispatch after the first
        child was written and verified; the parent stays in the existing
        manual-review state and every surface names the second child."""

        created = await self._two_operation_plan()
        with self.assertLogs(
            "ha_mcp_engineering.governance", level=logging.INFO
        ) as logs:
            with self._stale_second_preflight():
                result = await self.service.apply(
                    created["plan_id"], created["plan_hash"]
                )
        # Existing partial-execution state, unchanged.
        self.assertEqual(result["task_state"], "manual_review_required")
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"],
            [("write", "update", "automation", "apply_hvac_comfort")],
        )
        task = self.service.task_repository.get(result["task_id"])
        self.assertEqual(task.state, ExecutionTaskState.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(task.terminal_outcome, "manual_review_required")
        self.assertEqual(task.manual_review_reason, "f3_sequence_incomplete")
        self.assertEqual(
            task.verification_summary["completed_operation_ids"], ["first"]
        )
        self.assertEqual(
            [
                (child["operation_id"], child["normalized_outcome"],
                 child["dispatch_count"])
                for child in task.verification_summary["children"]
            ],
            [("first", "succeeded_verified", 1),
             ("second", "preflight_rejected", 0)],
        )
        self.assertFalse(
            any(event.event_type == "preflight_failed" for event in task.events)
        )
        reason = task.last_error["failure_information"]
        # 1. child
        public = self.service.get_execution_task(result["task_id"])
        self.assertIsNone(public["f3_children"][0]["pre_dispatch_reason"])
        self._assert_second_child_reason(
            public["f3_children"][1]["pre_dispatch_reason"], surface="child"
        )
        self.assertEqual(public["f3_children"][1]["pre_dispatch_reason"], reason)
        # 2. parent error and summary
        self._assert_second_child_reason(reason, surface="last_error")
        self.assertEqual(task.last_error["error_code"], "stale_target_state")
        self.assertEqual(task.last_error["failure_category"], "target_state")
        self.assertEqual(
            task.verification_summary["pre_dispatch_refusal"], reason
        )
        self.assertEqual(public["pre_dispatch_refusal"], reason)
        # 3. first apply details
        self.assertEqual(result["pre_dispatch_refusal"], reason)
        # 4. refusal event and log, child-identified, no state transition
        [refusal_event] = [
            event for event in task.events
            if event.event_type == reasons.CHILD_PREFLIGHT_FAILED_EVENT
        ]
        self.assertEqual(refusal_event.state_before, refusal_event.state_after)
        self.assertEqual(
            refusal_event.changes["last_error"]["failure_information"], reason
        )
        [review_event] = [
            event for event in task.events
            if event.event_type == "manual_review_required"
        ]
        self.assertEqual(
            review_event.state_after,
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED.value,
        )
        [logged] = _logged(
            logs, reasons.CHILD_PREFLIGHT_FAILED_EVENT, result["task_id"]
        )
        _assert_logged_reason(self, logged, reason)
        self.assertEqual(logged.safe_context["result_status"], "failure")
        self.assertTrue(logged.safe_context["approval_consumed"])
        [review_logged] = _logged(logs, "manual_review_required", result["task_id"])
        _assert_logged_reason(self, review_logged, reason)
        audited = [
            entry for entry in self._audit_entries()
            if entry.get("event") == reasons.CHILD_PREFLIGHT_FAILED_EVENT
        ]
        self.assertEqual(len(audited), 1)
        self.assertEqual(audited[0]["failure_information"], reason)
        self.assertNotIn("hamcp249-untrusted", self.persisted_artifact_text())
        # 5. repeated apply: same reason, no further dispatch
        with self.assertRaises(GovernanceError) as repeated:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(
            repeated.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT
        )
        self.assertEqual(
            repeated.exception.details["pre_dispatch_refusal"], reason
        )
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)
        # Terminal recovery reconciles without rewriting the stored history.
        before = self.service.task_repository.get(result["task_id"]).to_dict()
        await self._reconcile_terminal_projection(result["task_id"])
        self.assertEqual(
            self.service.task_repository.get(result["task_id"]).to_dict(), before
        )

    async def test_later_child_post_dispatch_failure_names_no_refusal(self):
        """Negative R1-M1: a later child that failed after its dispatch ends
        in the same manual review but is not reported as a pre-dispatch
        refusal on any surface."""

        created = await self._two_operation_plan()
        self.gateway.fail_write_target = ("script", "set_hvac_comfort")
        with self.assertLogs(
            "ha_mcp_engineering.governance", level=logging.INFO
        ) as logs:
            result = await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(result["task_state"], "manual_review_required")
        self.assertNotIn("pre_dispatch_refusal", result)
        task = self.service.task_repository.get(result["task_id"])
        self.assertEqual(
            [child["dispatch_count"] for child in task.verification_summary["children"]],
            [1, 1],
        )
        self.assertNotIn("pre_dispatch_refusal", task.verification_summary)
        self.assertFalse(
            any(
                event.event_type
                in {reasons.CHILD_PREFLIGHT_FAILED_EVENT, "preflight_failed"}
                for event in task.events
            )
        )
        public = self.service.get_execution_task(result["task_id"])
        self.assertNotIn("pre_dispatch_refusal", public)
        self.assertEqual(
            [child["pre_dispatch_reason"] for child in public["f3_children"]],
            [None, None],
        )
        self.assertFalse(
            any(
                "failure_information" in getattr(record, "safe_context", {})
                for record in logs.records
            )
        )
        with self.assertRaises(GovernanceError) as repeated:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertNotIn("pre_dispatch_refusal", repeated.exception.details)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 2)

    async def test_later_child_refusal_is_evented_once_across_recovery(self):
        """R1-M1: if the parent's terminal save is interrupted after the
        child refusal event, recovery completes the same manual review and
        does not record the refusal event again."""

        created = await self._two_operation_plan()
        record = self.service._record_task_event
        interrupted = []

        def interrupt_terminal_save(task, event_type, *args, **kwargs):
            if event_type == "manual_review_required" and not interrupted:
                interrupted.append(event_type)
                raise RuntimeError("synthetic interruption")
            return record(task, event_type, *args, **kwargs)

        with self._stale_second_preflight(), patch.object(
            self.service, "_record_task_event", interrupt_terminal_save
        ):
            with self.assertRaises(RuntimeError):
                await self.service.apply(created["plan_id"], created["plan_hash"])
        [task] = [
            item for item in self.service.task_repository.list()
            if item.plan_id == created["plan_id"]
        ]
        self.assertNotIn(task.state, {
            ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
            ExecutionTaskState.FAILED_PRE_DISPATCH,
        })
        await self.runtime.recover_once("test")
        task = self.service.task_repository.get(task.task_id)
        self.assertEqual(task.state, ExecutionTaskState.MANUAL_REVIEW_REQUIRED)
        self.assertEqual(
            [
                event.event_type for event in task.events
                if event.event_type == reasons.CHILD_PREFLIGHT_FAILED_EVENT
            ],
            [reasons.CHILD_PREFLIGHT_FAILED_EVENT],
        )
        self._assert_second_child_reason(
            task.last_error["failure_information"], surface="recovered"
        )
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)

    async def test_terminal_recovery_keeps_a_persisted_reason_verbatim(self):
        created = await self._plan(_wait_dict_automation())
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        before = self.service.task_repository.get(result["task_id"]).to_dict()
        await self._reconcile_terminal_projection(result["task_id"])
        self.assertEqual(
            self.service.task_repository.get(result["task_id"]).to_dict(), before
        )


class LegacyF1PreDispatchReasonTests(ConfigurationPlanTestCase):
    """L2: the legacy F1 apply path (no F3 runtime) keeps its error code and
    now also persists bounded failure_information."""

    async def test_f1_stale_target_refusal_names_its_reason(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        key = ("automation", "apply_hvac_comfort")
        self.gateway.configs[key] = {
            **self.gateway.configs[key], "description": UNTRUSTED_TEXT,
        }
        with self.assertLogs(
            "ha_mcp_engineering.governance", level=logging.INFO
        ) as logs:
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.STALE_TARGET_STATE)
        [task] = [
            item for item in self.service.task_repository.list()
            if item.plan_id == created["plan_id"]
        ]
        self.assertEqual(task.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(task.last_error["error_code"], "stale_target_state")
        information = task.last_error["failure_information"]
        self.assertEqual(sorted(information), sorted(reasons.REASON_FIELDS))
        self.assertEqual(information["error_code"], "stale_target_state")
        self.assertEqual(information["check"], "legacy_f1_apply")
        self.assertEqual(information["reason_source"], "parent_last_error")
        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(public["last_error"]["failure_information"], information)
        terminal_logs = [
            record.safe_context["error_code"]
            for record in logs.records
            if getattr(record, "safe_context", {}).get("task_id") == task.task_id
            and getattr(record, "safe_context", {}).get("task_state")
            == "failed_pre_dispatch"
        ]
        self.assertIn("stale_target_state", terminal_logs)
        [logged] = _logged(logs, "preflight_failed", task.task_id)
        _assert_logged_reason(self, logged, information)
        [event] = [
            item for item in task.events if item.event_type == "preflight_failed"
        ]
        self.assertEqual(
            event.changes["last_error"]["failure_information"], information
        )
        with self.assertRaises(GovernanceError) as repeated:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(
            repeated.exception.details["pre_dispatch_refusal"], information
        )
        text = json.dumps(information)
        self.assertNotIn("hamcp249", text)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def test_f1_refusal_reads_back_through_the_f3_task_view(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        key = ("automation", "apply_hvac_comfort")
        self.gateway.configs[key] = {
            **self.gateway.configs[key], "description": UNTRUSTED_TEXT,
        }
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        [task] = [
            item for item in self.service.task_repository.list()
            if item.plan_id == created["plan_id"]
        ]
        # A later F3 runtime decorates the legacy F1 task with its stored
        # reason, unchanged.
        self.service.f3_runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        await self.service.f3_runtime.recover_once("startup")
        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(
            public["pre_dispatch_refusal"], task.last_error["failure_information"]
        )
        self.assertEqual(public["pre_dispatch_refusal"]["error_code"], "stale_target_state")
        self.assertNotIn("f3_children", public)

    async def test_l1_contradictory_pre_dispatch_task_names_its_reason(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        plan = self.service._load(created["plan_id"])
        task = self.service._create_task_for_plan(plan, created["plan_hash"])
        self.service._record_task_event(
            task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        task = self.service.task_repository.get(task.task_id)
        with self.assertLogs(
            "ha_mcp_engineering.governance", level=logging.INFO
        ) as logs:
            self.service._manual_review_task(
                task, "restart_reconciliation_unproven", plan
            )
        stored = self.service.task_repository.get(task.task_id)
        self.assertEqual(stored.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(
            stored.last_error["failure_category"], "restart_reconciliation_unproven"
        )
        information = stored.last_error["failure_information"]
        self.assertEqual(sorted(information), sorted(reasons.REASON_FIELDS))
        self.assertEqual(information["error_code"], "restart_reconciliation_unproven")
        self.assertEqual(information["check"], "legacy_f1_apply")
        self.assertEqual(information["target_id"], stored.target["target_id"])
        public = self.service.get_execution_task(task.task_id)
        self.assertEqual(public["last_error"]["failure_information"], information)
        [logged] = _logged(logs, "preflight_failed", task.task_id)
        _assert_logged_reason(self, logged, information)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def _tool_apply(self, created):
        with patch.object(
            governance_tools.GOVERNANCE, "require", return_value=self.service
        ):
            return json.loads(
                await governance_tools.apply_change_plan(
                    created["plan_id"], created["plan_hash"]
                )
            )

    async def test_first_f1_refusal_through_the_tool_names_its_reason(self):
        """R1-M3: the first legacy F1 refusal returned by the public tool
        carries the reason just persisted; a repeat keeps it."""

        created = await self.create_automation_plan()
        await self.approve(created)
        key = ("automation", "apply_hvac_comfort")
        self.gateway.configs[key] = {
            **self.gateway.configs[key], "description": UNTRUSTED_TEXT,
        }
        envelope = await self._tool_apply(created)
        self.assertFalse(envelope["success"])
        # The original error is unchanged.
        self.assertEqual(envelope["error_code"], "stale_target_state")
        self.assertEqual(
            envelope["message"], "Home Assistant state changed after planning."
        )
        self.assertEqual(envelope["details"]["resource_id"], "apply_hvac_comfort")
        self.assertEqual(
            envelope["details"]["operation_id"], "update_comfort_automation"
        )
        [task] = [
            item for item in self.service.task_repository.list()
            if item.plan_id == created["plan_id"]
        ]
        self.assertEqual(task.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        information = task.last_error["failure_information"]
        self.assertEqual(envelope["details"]["pre_dispatch_refusal"], information)
        [event] = [
            item for item in task.events if item.event_type == "preflight_failed"
        ]
        self.assertEqual(
            event.changes["last_error"]["failure_information"], information
        )
        text = json.dumps(envelope)
        self.assertNotIn("hamcp249", text)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        repeated = await self._tool_apply(created)
        self.assertEqual(repeated["error_code"], "duplicate_apply_attempt")
        self.assertEqual(repeated["details"]["pre_dispatch_refusal"], information)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def test_post_dispatch_f1_error_through_the_tool_is_not_decorated(self):
        """Negative R1-M3: an error after the write is not a pre-dispatch
        refusal and carries no refusal reason."""

        self.gateway.canonicalize_automation_action_alias = True
        self.gateway.behavioral_mismatch_after_write_target = (
            "automation", "apply_hvac_comfort",
        )
        created = await self.create_automation_plan()
        await self.approve(created)
        envelope = await self._tool_apply(created)
        self.assertFalse(envelope["success"])
        self.assertEqual(
            envelope["error_code"],
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
        )
        self.assertNotIn("pre_dispatch_refusal", envelope.get("details") or {})
        [task] = [
            item for item in self.service.task_repository.list()
            if item.plan_id == created["plan_id"]
        ]
        self.assertNotEqual(task.state, ExecutionTaskState.FAILED_PRE_DISPATCH)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)


if __name__ == "__main__":
    unittest.main()
