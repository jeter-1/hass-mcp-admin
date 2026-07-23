"""Approval, application, verification, rollback, and concurrency workflow."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import json
import secrets
import time
from typing import Any, Callable
import uuid

from ..audit import AuditLogger
from ..clients.rest import ExpectedHttpStatus, HomeAssistantRestClient
from ..errors import ErrorCode, GovernanceError, HomeAssistantApiError
from ..logging_config import get_logger, log_event
from ..observability import METRICS
from ..request_context import current_caller_id, current_request_id
from ..sanitization import sanitize_untrusted_data
from .models import (
    ApprovalState,
    ChangeApproval,
    ChangeEvent,
    ChangeOperation,
    ChangePlan,
    ChangeRiskAssessment,
    ChangeRollback,
    ChangeSnapshot,
    ChangeTarget,
    ChangeVerification,
    ConfigurationOperation,
    PlanStatus,
    RiskLevel,
    StepExecutionStatus,
)
from .normalize import (
    AUTOMATION_NORMALIZATION_VERSION,
    normalize_automation,
    stable_hash,
    state_fingerprint,
    structured_diff,
)
from .risk import classify_risk
from .resources import (
    ConfigurationMutationCompletedUnexpectedlyError,
    ConfigurationMutationNotDispatchedError,
    RESOURCE_NORMALIZATION_VERSION,
    normalize_resource_config,
    resource_fingerprint,
    resource_identity_matches,
    persistence_safety_errors,
    structured_resource_diff,
    validate_resource_create_identity,
    validate_resource,
)
from .storage import (
    ChangePlanRepository,
    ChangePlanStorageError,
    is_terminal_plan,
)
from .validation import sanitize_context, validate_automation


APPROVAL_AUTHORITY_VERSION = 2
APPROVAL_CHANNEL = "home_assistant_ingress"
APPROVAL_CHALLENGE_TTL = timedelta(minutes=15)
DEFAULT_APPROVER_PRINCIPAL = "home_assistant_admin_ingress"
CONFIGURATION_PLAN_CONTRACT_VERSION = 2
MAX_CONFIGURATION_OPERATIONS = 8
SUPPORTED_CONFIGURATION_RESOURCES = frozenset({"automation", "script", "helper"})
SUPPORTED_HELPER_TYPES = frozenset({"input_boolean", "input_number"})
SUPPORTED_CONFIGURATION_ACTIONS = frozenset({"create", "update"})
MAX_APPROVAL_PROJECTION_STEPS = 16
MAX_APPROVAL_PROJECTION_METADATA = 10
MAX_APPROVAL_PROJECTION_TARGETS = 8
MAX_APPROVAL_PROJECTION_DATA = 8
MAX_APPROVAL_PROJECTION_DEPTH = 4
MAX_APPROVAL_PROJECTION_CONTROLS = 16
MAX_APPROVAL_PROJECTION_ACTIONS_PER_PLAN = 32
MAX_APPROVAL_PROJECTION_DETAILS_PER_PLAN = 128

_APPROVAL_METADATA_FIELDS = {
    "automation": (
        "id",
        "alias",
        "description",
        "initial_state",
        "mode",
        "max",
        "max_exceeded",
    ),
    "script": (
        "alias",
        "description",
        "icon",
        "mode",
        "max",
        "max_exceeded",
    ),
    "helper": (
        "name",
        "icon",
        "initial",
        "min",
        "max",
        "step",
        "mode",
        "unit_of_measurement",
    ),
}
_APPROVAL_ACTION_ROOTS = ("sequence", "action", "actions")
_APPROVAL_TRIGGER_ROOTS = ("trigger", "triggers")
_APPROVAL_CONDITION_ROOTS = ("condition", "conditions")
_APPROVAL_TARGET_FIELDS = frozenset(
    {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
)
_APPROVAL_ACTION_STRUCTURAL_FIELDS = frozenset(
    {
        "action",
        "actions",
        "alias",
        "choose",
        "conditions",
        "data",
        "data_template",
        "default",
        "else",
        "if",
        "parallel",
        "repeat",
        "sequence",
        "service",
        "target",
        "then",
        "variables",
        "wait_for_trigger",
    }
)
_APPROVAL_ACTION_DIRECTIVES = (
    "delay",
    "wait_template",
    "wait_for_trigger",
    "event",
    "scene",
    "condition",
    "choose",
    "if",
    "repeat",
    "parallel",
    "variables",
    "stop",
)
_APPROVAL_CONTROL_STRUCTURAL_FIELDS = frozenset(
    {
        "alias",
        "condition",
        "conditions",
        "platform",
        "target",
        "trigger",
    }
)
_APPROVAL_ACTION_MAPPING_FIELDS = frozenset(
    {"delay", "event_data", "event_data_template", "timeout"}
)
_APPROVAL_CHOICE_FIELDS = frozenset({"alias", "conditions", "sequence"})
_APPROVAL_REPEAT_FIELDS = frozenset(
    {"count", "for_each", "sequence", "until", "while"}
)
_APPROVAL_COMPLEX_ROOTS = {
    "automation": ("variables", "trace"),
    "script": ("variables", "fields", "trace"),
}
_APPROVAL_OMITTED = object()


def _sanitize_configuration_caller_context(
    context: dict[str, Any] | None,
    *,
    known_secrets: tuple[str, ...],
) -> dict[str, Any]:
    """Retain only bounded scalar context entries with no secret detections."""

    if not isinstance(context, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in context.items():
        detection = sanitize_untrusted_data(
            {key: value},
            known_secrets=known_secrets,
        )
        if detection.failed_closed or detection.redaction_applied:
            continue
        # Preserve the established bounded scalar-only caller-context contract.
        safe.update(sanitize_context({key: value}, known_secrets))
    return safe


def _approval_primitive(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 200 else _APPROVAL_OMITTED
    if isinstance(value, (list, tuple)) and len(value) <= 8:
        projected = []
        for item in value:
            primitive = _approval_primitive(item)
            if primitive is _APPROVAL_OMITTED or isinstance(
                primitive, (list, tuple)
            ):
                return _APPROVAL_OMITTED
            projected.append(primitive)
        return projected
    return _APPROVAL_OMITTED


def _configuration_approval_projection(
    resource_type: str,
    proposed_config: dict[str, Any],
    *,
    known_secrets: tuple[str, ...],
) -> dict[str, Any]:
    """Build a bounded semantic view of hash-bound configuration.

    This deliberately is not a general configuration serializer. It includes
    only allowlisted metadata, ordered action/service and trigger/condition
    identities, explicit target selectors, and bounded primitive data.
    """

    sanitized_source = sanitize_untrusted_data(
        proposed_config,
        known_secrets=known_secrets,
        max_string=200,
    )
    if sanitized_source.failed_closed or not isinstance(
        sanitized_source.value, dict
    ):
        return {
            "status": "unavailable",
            "metadata": [],
            "actions": [],
            "controls": [],
            "redaction_applied": True,
        }
    safe_config = sanitized_source.value

    # A shortened or redacted value is not an informed approval surface. Keep
    # rendering the bounded, sanitized view for diagnosis, but make the
    # projection unapprovable whenever exact semantics were withheld.
    incomplete = (
        sanitized_source.truncated_field_count > 0
        or sanitized_source.redaction_applied
    )

    def bounded_text(value: Any, maximum: int) -> str:
        nonlocal incomplete
        text = str(value)
        if len(text) > maximum:
            incomplete = True
            return text[:maximum]
        return text

    metadata: list[dict[str, Any]] = []
    for field in _APPROVAL_METADATA_FIELDS.get(resource_type, ()):
        if field not in safe_config:
            continue
        primitive = _approval_primitive(safe_config[field])
        if primitive is _APPROVAL_OMITTED:
            incomplete = True
            continue
        if len(metadata) >= MAX_APPROVAL_PROJECTION_METADATA:
            incomplete = True
            break
        metadata.append(
            {"field": bounded_text(field, 120), "value": primitive}
        )

    actions: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []

    def project_values(
        values: Any,
        *,
        prefix: str,
        destination: list[dict[str, Any]],
        maximum: int,
        depth: int = 0,
    ) -> None:
        nonlocal incomplete
        if depth > 2:
            incomplete = True
            return
        if isinstance(values, dict):
            for key in sorted(values, key=lambda item: str(item)):
                if len(destination) >= maximum:
                    incomplete = True
                    return
                name = f"{prefix}.{key}" if prefix else str(key)
                primitive = _approval_primitive(values[key])
                if primitive is not _APPROVAL_OMITTED:
                    destination.append(
                        {
                            "field": bounded_text(name, 120),
                            "value": primitive,
                        }
                    )
                elif isinstance(values[key], dict):
                    project_values(
                        values[key],
                        prefix=name,
                        destination=destination,
                        maximum=maximum,
                        depth=depth + 1,
                    )
                else:
                    incomplete = True
            return
        incomplete = True

    def project_named_value(
        value: Any,
        *,
        prefix: str,
        destination: list[dict[str, Any]],
        maximum: int,
    ) -> None:
        nonlocal incomplete
        primitive = _approval_primitive(value)
        if primitive is not _APPROVAL_OMITTED:
            if len(destination) >= maximum:
                incomplete = True
                return
            destination.append(
                {
                    "field": bounded_text(prefix, 120),
                    "value": primitive,
                }
            )
            return
        if isinstance(value, dict):
            project_values(
                value,
                prefix=prefix,
                destination=destination,
                maximum=maximum,
            )
            return
        incomplete = True

    def add_target(
        destination: list[dict[str, Any]], selector: str, value: Any
    ) -> None:
        nonlocal incomplete
        primitive = _approval_primitive(value)
        if primitive is _APPROVAL_OMITTED:
            incomplete = True
            return
        if len(destination) >= MAX_APPROVAL_PROJECTION_TARGETS:
            incomplete = True
            return
        destination.append(
            {"selector": bounded_text(selector, 64), "value": primitive}
        )

    def project_step(step: dict[str, Any], path: str) -> None:
        nonlocal incomplete
        if len(actions) >= MAX_APPROVAL_PROJECTION_STEPS:
            incomplete = True
            return

        action_name = step.get("service")
        if not isinstance(action_name, str):
            action_name = step.get("action")
        if not isinstance(action_name, str):
            domain = step.get("domain")
            action_type = step.get("type")
            if isinstance(domain, str) and isinstance(action_type, str):
                action_name = f"{domain}.{action_type}"
            else:
                action_name = next(
                    (
                        directive
                        for directive in _APPROVAL_ACTION_DIRECTIVES
                        if directive in step
                    ),
                    "structured_action",
                )
        if action_name == "structured_action":
            if "sequence" in step and set(step) <= {"alias", "sequence"}:
                action_name = "sequence"
            else:
                incomplete = True

        targets: list[dict[str, Any]] = []
        target = step.get("target")
        if target is not None:
            if not isinstance(target, dict):
                incomplete = True
            else:
                for selector in sorted(target):
                    if selector not in _APPROVAL_TARGET_FIELDS:
                        incomplete = True
                        continue
                    add_target(targets, str(selector), target[selector])
        for selector in sorted(_APPROVAL_TARGET_FIELDS):
            if selector in step:
                add_target(targets, selector, step[selector])

        data: list[dict[str, Any]] = []
        for container_name in ("data", "data_template"):
            container = step.get(container_name)
            if container is not None:
                if isinstance(container, dict):
                    container = dict(container)
                    for selector in sorted(_APPROVAL_TARGET_FIELDS):
                        if selector in container:
                            add_target(
                                targets,
                                f"{container_name}.{selector}",
                                container.pop(selector),
                            )
                project_values(
                    container,
                    prefix=container_name,
                    destination=data,
                    maximum=MAX_APPROVAL_PROJECTION_DATA,
                )
        if "alias" in step:
            project_named_value(
                step["alias"],
                prefix="alias",
                destination=data,
                maximum=MAX_APPROVAL_PROJECTION_DATA,
            )
        if "variables" in step:
            project_named_value(
                step["variables"],
                prefix="variables",
                destination=data,
                maximum=MAX_APPROVAL_PROJECTION_DATA,
            )
        repeat_parameters = step.get("repeat")
        if isinstance(repeat_parameters, dict):
            for parameter in ("count", "for_each"):
                if parameter in repeat_parameters:
                    project_named_value(
                        repeat_parameters[parameter],
                        prefix=f"repeat.{parameter}",
                        destination=data,
                        maximum=MAX_APPROVAL_PROJECTION_DATA,
                    )
        for field in sorted(step):
            if (
                field in _APPROVAL_ACTION_STRUCTURAL_FIELDS
                or field in _APPROVAL_TARGET_FIELDS
            ):
                continue
            primitive = _approval_primitive(step[field])
            if primitive is _APPROVAL_OMITTED:
                if (
                    field in _APPROVAL_ACTION_MAPPING_FIELDS
                    and isinstance(step[field], dict)
                ):
                    project_values(
                        step[field],
                        prefix=str(field),
                        destination=data,
                        maximum=MAX_APPROVAL_PROJECTION_DATA,
                    )
                else:
                    incomplete = True
                continue
            project_named_value(
                primitive,
                prefix=str(field),
                destination=data,
                maximum=MAX_APPROVAL_PROJECTION_DATA,
            )

        actions.append(
            {
                "path": bounded_text(path, 160),
                "action": bounded_text(action_name, 200),
                "targets": targets,
                "data": data,
            }
        )

    def project_control(
        control: dict[str, Any], path: str, kind: str
    ) -> None:
        nonlocal incomplete
        if len(controls) >= MAX_APPROVAL_PROJECTION_CONTROLS:
            incomplete = True
            return

        control_type = control.get("platform")
        if not isinstance(control_type, str):
            control_type = control.get("trigger")
        if not isinstance(control_type, str):
            control_type = control.get("condition")
        if not isinstance(control_type, str):
            control_type = "structured"
            incomplete = True

        targets: list[dict[str, Any]] = []
        target = control.get("target")
        if target is not None:
            if not isinstance(target, dict):
                incomplete = True
            else:
                for selector in sorted(target):
                    if selector not in _APPROVAL_TARGET_FIELDS:
                        incomplete = True
                        continue
                    add_target(targets, str(selector), target[selector])
        for selector in sorted(_APPROVAL_TARGET_FIELDS):
            if selector in control:
                add_target(targets, selector, control[selector])

        data: list[dict[str, Any]] = []
        if "alias" in control:
            project_named_value(
                control["alias"],
                prefix="alias",
                destination=data,
                maximum=MAX_APPROVAL_PROJECTION_DATA,
            )
        for field in sorted(control):
            if (
                field in _APPROVAL_CONTROL_STRUCTURAL_FIELDS
                or field in _APPROVAL_TARGET_FIELDS
            ):
                continue
            primitive = _approval_primitive(control[field])
            if primitive is not _APPROVAL_OMITTED:
                if len(data) >= MAX_APPROVAL_PROJECTION_DATA:
                    incomplete = True
                    break
                data.append(
                    {
                        "field": bounded_text(field, 120),
                        "value": primitive,
                    }
                )
            elif isinstance(control[field], dict):
                project_values(
                    control[field],
                    prefix=str(field),
                    destination=data,
                    maximum=MAX_APPROVAL_PROJECTION_DATA,
                )
            else:
                incomplete = True

        controls.append(
            {
                "path": bounded_text(path, 160),
                "kind": kind,
                "type": bounded_text(control_type, 200),
                "targets": targets,
                "data": data,
            }
        )

    def walk_steps(value: Any, path: str, depth: int = 0) -> None:
        nonlocal incomplete
        if depth > MAX_APPROVAL_PROJECTION_DEPTH:
            incomplete = True
            return
        if isinstance(value, dict):
            candidates = [(0, value)]
        elif isinstance(value, list):
            candidates = list(enumerate(value))
        else:
            incomplete = True
            return
        for index, step in candidates:
            if not isinstance(step, dict):
                incomplete = True
                continue
            step_path = f"{path}[{index}]"
            project_step(step, step_path)
            for child in (
                "actions",
                "sequence",
                "then",
                "else",
                "default",
                "parallel",
            ):
                if child in step:
                    walk_steps(
                        step[child],
                        f"{step_path}.{child}",
                        depth + 1,
                    )
            choices = step.get("choose")
            if isinstance(choices, list):
                for choice_index, choice in enumerate(choices):
                    choice_path = (
                        f"{step_path}.choose[{choice_index}]"
                    )
                    if not isinstance(choice, dict):
                        incomplete = True
                        continue
                    if set(choice) - _APPROVAL_CHOICE_FIELDS:
                        incomplete = True
                    branch = {"action": "choose_branch"}
                    if "alias" in choice:
                        branch["alias"] = choice["alias"]
                    project_step(branch, choice_path)
                    if "conditions" in choice:
                        walk_controls(
                            choice["conditions"],
                            f"{choice_path}.conditions",
                            "choose_condition",
                            depth + 1,
                        )
                    else:
                        incomplete = True
                    if "sequence" in choice:
                        walk_steps(
                            choice["sequence"],
                            f"{choice_path}.sequence",
                            depth + 1,
                        )
                    else:
                        incomplete = True
            elif choices is not None:
                incomplete = True
            if_conditions = step.get("if")
            if if_conditions is not None:
                walk_controls(
                    if_conditions,
                    f"{step_path}.if",
                    "if_condition",
                    depth + 1,
                )
            nested_conditions = step.get("conditions")
            if nested_conditions is not None:
                walk_controls(
                    nested_conditions,
                    f"{step_path}.conditions",
                    "condition",
                    depth + 1,
                )
            repeat = step.get("repeat")
            if isinstance(repeat, dict):
                if set(repeat) - _APPROVAL_REPEAT_FIELDS:
                    incomplete = True
                if "sequence" in repeat:
                    walk_steps(
                        repeat["sequence"],
                        f"{step_path}.repeat.sequence",
                        depth + 1,
                    )
                else:
                    incomplete = True
                for condition_kind in ("while", "until"):
                    if condition_kind in repeat:
                        walk_controls(
                            repeat[condition_kind],
                            f"{step_path}.repeat.{condition_kind}",
                            f"repeat_{condition_kind}",
                            depth + 1,
                        )
                if not any(
                    field in repeat
                    for field in ("count", "for_each", "while", "until")
                ):
                    incomplete = True
            elif repeat is not None:
                incomplete = True
            wait_triggers = step.get("wait_for_trigger")
            if wait_triggers is not None:
                walk_controls(
                    wait_triggers,
                    f"{step_path}.wait_for_trigger",
                    "wait_trigger",
                    depth + 1,
                )

    def walk_controls(
        value: Any, path: str, kind: str, depth: int = 0
    ) -> None:
        nonlocal incomplete
        if depth > MAX_APPROVAL_PROJECTION_DEPTH:
            incomplete = True
            return
        if isinstance(value, dict):
            candidates = [(0, value)]
        elif isinstance(value, list):
            candidates = list(enumerate(value))
        else:
            incomplete = True
            return
        for index, control in candidates:
            if not isinstance(control, dict):
                incomplete = True
                continue
            control_path = f"{path}[{index}]"
            project_control(control, control_path, kind)
            nested = control.get("conditions")
            if nested is not None:
                walk_controls(
                    nested,
                    f"{control_path}.conditions",
                    "condition",
                    depth + 1,
                )

    allowed_roots = set(_APPROVAL_METADATA_FIELDS.get(resource_type, ()))
    if resource_type in {"automation", "script"}:
        allowed_roots.update(_APPROVAL_ACTION_ROOTS)
        allowed_roots.update(_APPROVAL_COMPLEX_ROOTS.get(resource_type, ()))
        allowed_roots.add("use_blueprint")
    if resource_type == "automation":
        allowed_roots.update(_APPROVAL_TRIGGER_ROOTS)
        allowed_roots.update(_APPROVAL_CONDITION_ROOTS)
    if set(safe_config) - allowed_roots:
        incomplete = True

    action_root_found = False
    for root in _APPROVAL_ACTION_ROOTS:
        if root in safe_config:
            action_root_found = True
            walk_steps(safe_config[root], root)

    blueprint = safe_config.get("use_blueprint")
    if blueprint is not None:
        action_root_found = True
        if isinstance(blueprint, dict):
            project_step(
                {
                    "action": "use_blueprint",
                    "data": blueprint,
                },
                "use_blueprint",
            )
        else:
            incomplete = True

    for root in _APPROVAL_COMPLEX_ROOTS.get(resource_type, ()):
        if root not in safe_config:
            continue
        value = safe_config[root]
        if not isinstance(value, dict):
            incomplete = True
            continue
        if root == "variables":
            project_step(
                {"action": "variables", "variables": value},
                root,
            )
        else:
            project_step(
                {"action": f"configuration_{root}", "data": value},
                root,
            )

    if resource_type in {"automation", "script"} and not action_root_found:
        incomplete = True

    for root in _APPROVAL_TRIGGER_ROOTS:
        if root in safe_config:
            walk_controls(safe_config[root], root, "trigger")
    for root in _APPROVAL_CONDITION_ROOTS:
        if root in safe_config:
            walk_controls(safe_config[root], root, "condition")

    projection = {
        "status": "incomplete" if incomplete else "complete",
        "metadata": metadata,
        "actions": actions,
        "controls": controls,
    }
    sanitized = sanitize_untrusted_data(
        projection,
        known_secrets=known_secrets,
        max_string=200,
    )
    if sanitized.failed_closed or not isinstance(sanitized.value, dict):
        return {
            "status": "unavailable",
            "metadata": [],
            "actions": [],
            "controls": [],
            "redaction_applied": True,
        }
    safe_projection = sanitized.value
    redaction_applied = (
        sanitized_source.redaction_applied
        or sanitized.redaction_applied
    )
    incomplete = (
        incomplete
        or sanitized.truncated_field_count > 0
        or redaction_applied
    )
    safe_projection["status"] = (
        "incomplete" if incomplete else "complete"
    )
    safe_projection["redaction_applied"] = redaction_applied
    safe_projection["truncation_applied"] = (
        sanitized_source.truncated_field_count > 0
        or sanitized.truncated_field_count > 0
    )
    return safe_projection


class AutomationGateway:
    """Narrow Home Assistant boundary used by governance and test fakes."""

    def __init__(self, client: HomeAssistantRestClient):
        self.client = client

    async def get(self, automation_id: str) -> dict[str, Any] | None:
        value = await self.client.request(
            "GET",
            f"/config/automation/config/{automation_id}",
            expected_statuses=frozenset({404}),
        )
        if isinstance(value, ExpectedHttpStatus) and value.status == 404:
            return None
        if not isinstance(value, dict):
            raise HomeAssistantApiError(
                details={
                    "operation": "automation_config_read",
                    "resource_id": automation_id,
                    "endpoint_category": "config/automation",
                    "reason": "malformed_response",
                }
            )
        return value

    async def write(self, automation_id: str, config: dict[str, Any]) -> Any:
        return await self.client.request(
            "POST", f"/config/automation/config/{automation_id}", body=config
        )

    async def validate(self) -> Any:
        return await self.client.request("POST", "/config/core/check_config")


class ChangeGovernanceService:
    def __init__(
        self,
        repository: ChangePlanRepository,
        gateway: Any,
        audit: AuditLogger | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        sensitive_values: tuple[str, ...] = (),
    ):
        self.repository = repository
        self.gateway = gateway
        self.audit = audit
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sensitive_values = tuple(value for value in sensitive_values if value)
        self.logger = get_logger("governance")
        self._plan_locks: dict[str, asyncio.Lock] = {}
        self._target_locks: dict[object, asyncio.Lock] = {}
        self.repository.cleanup(now=self.now())
        self.repository.recover_incomplete(self._timestamp())

    def _timestamp(self) -> str:
        return self.now().isoformat()

    def _new_id(self) -> str:
        while True:
            candidate = uuid.uuid4().hex
            if self.repository.get(candidate) is None:
                return candidate

    @staticmethod
    def plan_hash(plan: ChangePlan) -> str:
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            immutable_operations = []
            for operation in sorted(plan.operations, key=lambda item: item.order):
                immutable_operations.append(
                    {
                        "operation_id": operation.operation_id,
                        "order": operation.order,
                        "depends_on": list(operation.depends_on),
                        "resource_type": operation.resource_type,
                        "helper_type": operation.helper_type,
                        "action": operation.action,
                        "target_id": operation.target_id,
                        "current_state_fingerprint": operation.current_state_fingerprint,
                        "proposed_config_hash": operation.proposed_config_hash,
                        "raw_proposed_config_hash": stable_hash(operation.proposed_config),
                        "normalized_proposed_config_hash": stable_hash(
                            operation.normalized_proposed_config
                        ),
                        "normalization_version": operation.normalization_version,
                        "risk_level": operation.risk.level.value,
                        "risk_apply_allowed": operation.risk.apply_allowed,
                    }
                )
            immutable = {
                "contract_version": plan.contract_version,
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "operation": plan.operation.value,
                "target_type": plan.target_type,
                "target_id": plan.target_id,
                "expires_at": plan.expires_at,
                "operations": immutable_operations,
                "risk_level": plan.risk.level.value,
                "risk_apply_allowed": plan.risk.apply_allowed,
                "approval_kind": plan.approval.approval_kind,
                "approval_authority_version": plan.approval.authority_version,
            }
            return stable_hash(immutable)

        # Contract-v1 hashing is intentionally unchanged. Historical and
        # in-flight single-automation plans retain their exact approved hashes.
        calculated_proposed_hash = stable_hash(
            normalize_automation(plan.proposed_config) or {}
        )
        immutable = {
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "operation": plan.operation.value,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "expires_at": plan.expires_at,
            "current_state_fingerprint": plan.current_state_fingerprint,
            "proposed_config_hash": calculated_proposed_hash,
            "normalization_version": plan.normalization_version,
            "risk_level": plan.risk.level.value,
            "approval_kind": plan.approval.approval_kind,
            "rollback_expected_fingerprint": plan.rollback.expected_current_fingerprint,
        }
        # Beta 24 plan hashes predate external approval authority. Preserve
        # those historical hashes exactly for readable audit/history while
        # requiring every executable Beta 25 plan to bind authority version 2.
        # Legacy active plans still fail closed before any provider access.
        if plan.approval.authority_version >= APPROVAL_AUTHORITY_VERSION:
            immutable["approval_authority_version"] = plan.approval.authority_version
        return stable_hash(immutable)

    def _load(self, plan_id: str) -> ChangePlan:
        try:
            plan = self.repository.get(plan_id)
        except ChangePlanStorageError as exc:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR) from exc
        if plan is None:
            METRICS.record_classified_outcome("change_plan_not_found")
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_NOT_FOUND, details={"resource_id": plan_id}
            )
        self._require_v2_persisted_plan_safe(plan)
        return plan

    def _save(self, plan: ChangePlan) -> None:
        plan.updated_at = self._timestamp()
        self._require_v2_persisted_plan_safe(plan)
        try:
            self.repository.save(plan)
        except ChangePlanStorageError as exc:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_STORAGE_ERROR) from exc

    def _require_v2_persisted_plan_safe(self, plan: ChangePlan) -> None:
        """Reject any unsafe contract-v2 record without echoing its contents.

        Contract-v2 plans persist exact caller and Home Assistant material for
        later hash-bound execution. A redacted copy cannot retain that
        authority, so every loaded or newly saved v2 record fails closed when
        the shared detector finds secret-bearing or otherwise prohibited data.
        Contract-v1 behavior is intentionally unchanged.
        """

        if plan.contract_version < CONFIGURATION_PLAN_CONTRACT_VERSION:
            return
        try:
            unsafe = bool(
                persistence_safety_errors(
                    plan.to_dict(), self.sensitive_values
                )
            )
        except Exception:
            unsafe = True
        if unsafe:
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
                details={
                    "reason": "unsafe_persisted_configuration_plan",
                },
            )

    def _record(
        self,
        plan: ChangePlan,
        event: str,
        result_status: str,
        *,
        error_code: str | None = None,
        duration_ms: float | None = None,
        approval_principal: str | None = None,
        operation_step: ConfigurationOperation | None = None,
    ) -> None:
        request_id = current_request_id()
        caller_id = current_caller_id()
        plan.events.append(
            ChangeEvent(
                event=event,
                timestamp=self._timestamp(),
                request_id=request_id,
                caller_id=caller_id,
                result_status=result_status,
                error_code=error_code,
                duration_ms=duration_ms,
                operation_id=(
                    operation_step.operation_id if operation_step else None
                ),
                operation_order=operation_step.order if operation_step else None,
                resource_type=(
                    operation_step.resource_type if operation_step else None
                ),
                resource_id=operation_step.target_id if operation_step else None,
            )
        )
        safe = {
            "event": event,
            "request_id": request_id,
            "plan_id": plan.plan_id,
            "target_type": (
                operation_step.resource_type if operation_step else plan.target_type
            ),
            "target_id": (
                operation_step.target_id if operation_step else plan.target_id
            ),
            "operation": (
                f"{operation_step.action}_{operation_step.resource_type}"
                if operation_step
                else plan.operation.value
            ),
            "operation_id": (
                operation_step.operation_id if operation_step else None
            ),
            "operation_order": operation_step.order if operation_step else None,
            "risk_level": (
                operation_step.risk.level.value
                if operation_step
                else plan.risk.level.value
            ),
            "result_status": result_status,
            "error_code": error_code,
            "duration_ms": duration_ms,
            "caller_id": caller_id,
            "approval_state": plan.approval.state.value,
            "approval_authority_version": plan.approval.authority_version,
            "approval_kind": plan.approval.approval_kind,
            "approval_channel": plan.approval.channel,
            "challenge_id": plan.approval.challenge_id,
            "approver_principal": approval_principal,
        }
        if operation_step is None:
            # Contract-v1 audit records predate ordered-operation metadata.
            # Preserve their exact event shape.
            safe.pop("operation_id", None)
            safe.pop("operation_order", None)
        # Persist the event and lifecycle state before emitting a success audit.
        # If storage fails, the caller returns change_plan_storage_error and no
        # misleading success record is produced.
        self._save(plan)
        if self.audit:
            self.audit.write(safe)
        log_event(
            self.logger,
            logging.INFO if result_status == "success" else logging.WARNING,
            event,
            (
                "Governed configuration-plan lifecycle event."
                if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION
                else "Governed automation change lifecycle event."
            ),
            context=safe,
        )

    def _expire_if_needed(self, plan: ChangePlan) -> bool:
        # A terminal plan has already completed its lifecycle transition.  In
        # particular, an expired plan must never be "expired" again merely
        # because a read surface inspects it.
        if is_terminal_plan(plan):
            return False
        if self.now() >= datetime.fromisoformat(plan.expires_at):
            plan.status = PlanStatus.EXPIRED
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.csrf_digest = None
            if plan.approval.challenge_id:
                self._record(
                    plan,
                    "external_approval_invalidated",
                    "rejected",
                    error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value,
                )
            self._record(plan, "change_plan_expired", "rejected", error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value)
            return True
        return False

    def _challenge_has_expired(self, plan: ChangePlan) -> bool:
        """Return the effective clock state for an external-pending challenge."""

        if plan.approval.state != ApprovalState.EXTERNAL_PENDING:
            return False
        try:
            return not plan.approval.challenge_expires_at or self.now() >= datetime.fromisoformat(
                plan.approval.challenge_expires_at
            )
        except ValueError:
            return True

    def _invalidate_terminal_challenge_if_needed(self, plan: ChangePlan) -> bool:
        """Reconcile an impossible persisted pending challenge on a terminal plan."""

        if (
            not is_terminal_plan(plan)
            or plan.approval.state != ApprovalState.EXTERNAL_PENDING
        ):
            return False
        plan.approval.state = ApprovalState.INVALIDATED
        plan.approval.csrf_digest = None
        self._record(
            plan,
            "external_approval_invalidated",
            "rejected",
            error_code=(
                ErrorCode.CHANGE_PLAN_EXPIRED.value
                if plan.status == PlanStatus.EXPIRED
                else ErrorCode.EXTERNAL_APPROVAL_INVALID.value
            ),
        )
        return True

    def _resolve_lifecycle(self, plan: ChangePlan) -> tuple[bool, bool]:
        """Persist each effective plan or challenge expiry transition once.

        Every read and enforcement surface uses this resolver so an expired
        challenge cannot remain actionable until a later apply attempt.
        """

        plan_expired = self._expire_if_needed(plan)
        if self._invalidate_terminal_challenge_if_needed(plan):
            return plan_expired, False
        challenge_expired = self._expire_challenge_if_needed(plan)
        return plan_expired, challenge_expired

    def _public(self, plan: ChangePlan, *, include_configs: bool = True) -> dict[str, Any]:
        self._require_v2_persisted_plan_safe(plan)
        value = plan.to_dict()
        # CSRF material is private to the Ingress authority and must never be
        # returned through MCP plan reads or summaries.
        if isinstance(value.get("approval"), dict):
            value["approval"].pop("csrf_digest", None)
            value["approval"].pop("csrf_issued_at", None)
            evaluated = plan.approval.principal_separation_enforced is not None
            value["approval"]["principal_separation_status"] = {
                "evaluated": evaluated,
                "enforced": plan.approval.principal_separation_enforced if evaluated else None,
                "reason": (
                    "external_administrator_distinct" if plan.approval.principal_separation_enforced
                    else "external_principal_not_distinct" if evaluated
                    else "no_external_approver_exists"
                ),
            }
        approval_lifecycle = self._approval_lifecycle(plan)
        value["approval_lifecycle"] = approval_lifecycle
        value["status_is_legacy"] = True
        value["authoritative_lifecycle_field"] = "approval_lifecycle"
        value["approval_challenge_created"] = bool(plan.approval.challenge_id)
        value["next_required_operation"] = (
            "approve_change_plan" if approval_lifecycle == "approval_not_requested" else None
        )
        value["plan_hash"] = self.plan_hash(plan)
        value["apply_allowed"] = (
            plan.status == PlanStatus.APPROVED
            and plan.approval.state == ApprovalState.APPROVED
            and plan.approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and plan.approval.channel == APPROVAL_CHANNEL
            and bool(plan.approval.approver_principal)
            and plan.approval.principal_separation_enforced
            and plan.risk.apply_allowed
        )
        # Contract-v2 callers receive ordered operation metadata, execution
        # receipts, and verification state, but never raw or normalized
        # configuration/snapshot bodies. Contract-v1 output remains unchanged
        # unless its existing summary-only option is explicitly requested.
        if (
            plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION
            or not include_configs
        ):
            value.pop("proposed_config", None)
            value.pop("current_config", None)
            value.pop("normalized_proposed_config", None)
            value.pop("normalized_current_config", None)
            value.pop("snapshot", None)
            value.pop("events", None)
            for operation in value.get("operations", []):
                if not isinstance(operation, dict):
                    continue
                operation.pop("proposed_config", None)
                operation.pop("current_config", None)
                operation.pop("normalized_proposed_config", None)
                operation.pop("normalized_current_config", None)
                operation.pop("snapshot", None)
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            sanitized = sanitize_untrusted_data(
                value,
                known_secrets=self.sensitive_values,
            )
            if (
                sanitized.failed_closed
                or sanitized.redaction_applied
                or not isinstance(sanitized.value, dict)
            ):
                raise GovernanceError(
                    ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
                    details={
                        "reason": "unsafe_persisted_configuration_plan",
                    },
                )
            return sanitized.value
        return value

    @staticmethod
    def _approval_lifecycle(plan: ChangePlan) -> str:
        return {
            ApprovalState.REQUIRED: "approval_not_requested",
            ApprovalState.EXTERNAL_PENDING: "approval_pending_external",
            ApprovalState.APPROVED: "approved",
            ApprovalState.CONSUMED: "approval_consumed",
            ApprovalState.REJECTED: "approval_rejected",
            ApprovalState.EXPIRED: "approval_expired",
            ApprovalState.INVALIDATED: "approval_invalidated",
        }[plan.approval.state]

    def _summary(self, plan: ChangePlan) -> dict[str, Any]:
        """Return bounded plan inventory; get_change_plan is the detail path."""
        value = {
            "plan_id": plan.plan_id,
            "plan_hash": self.plan_hash(plan),
            "plan_version": plan.plan_version,
            "title": plan.title,
            "status": plan.status.value,
            "approval_lifecycle": self._approval_lifecycle(plan),
            "status_is_legacy": True,
            "authoritative_lifecycle_field": "approval_lifecycle",
            "approval_challenge_created": bool(plan.approval.challenge_id),
            "target": {"target_type": plan.target_type, "target_id": plan.target_id},
            "operation": plan.operation.value,
            "risk_level": plan.risk.level.value,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "expires_at": plan.expires_at,
            "apply_allowed": bool(self._public(plan, include_configs=False)["apply_allowed"]),
        }
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            value.update(
                {
                    "contract_version": plan.contract_version,
                    "operation_count": len(plan.operations),
                    "execution_outcome": plan.execution_outcome,
                    "configuration_check_status": plan.configuration_check_status,
                }
            )
        return value

    @staticmethod
    def _resolved_resource_type(
        resource_type: str, helper_type: str | None
    ) -> str:
        if resource_type == "helper":
            if helper_type not in SUPPORTED_HELPER_TYPES:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            "helper_type must be input_boolean or input_number"
                        ]
                    },
                )
            return helper_type
        if helper_type:
            raise GovernanceError(
                ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                details={
                    "validation_errors": [
                        "helper_type is permitted only for helper operations"
                    ]
                },
            )
        return resource_type

    @classmethod
    def _operation_target_key(
        cls, operation: ConfigurationOperation
    ) -> tuple[str, str]:
        return (
            cls._resolved_resource_type(
                operation.resource_type, operation.helper_type
            ),
            operation.target_id,
        )

    @classmethod
    def _plan_target_keys(cls, plan: ChangePlan) -> set[tuple[str, str]]:
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            return {cls._operation_target_key(item) for item in plan.operations}
        return {(plan.target_type, plan.target_id)}

    async def _read_configuration_resource(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        reader = getattr(self.gateway, "read", None)
        if callable(reader):
            return await reader(resource_type, resource_id)
        # Existing tests and contract-v1 deployments provide the original
        # automation-only fake gateway. Keep that compatibility path narrow.
        if resource_type == "automation":
            return await self.gateway.get(resource_id)
        raise GovernanceError(
            ErrorCode.CONFIGURATION_APPLY_FAILED,
            details={
                "resource_id": resource_id,
                "reason": "resource_provider_unavailable",
            },
        )

    async def _write_configuration_resource(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        config: dict[str, Any],
    ) -> Any:
        writer = getattr(self.gateway, "write", None)
        if callable(writer):
            if hasattr(self.gateway, "read"):
                return await writer(action, resource_type, resource_id, config)
            if resource_type == "automation":
                return await writer(resource_id, config)
        raise GovernanceError(
            ErrorCode.CONFIGURATION_APPLY_FAILED,
            details={
                "resource_id": resource_id,
                "reason": "resource_provider_unavailable",
            },
        )

    async def _validate_all_configuration(self) -> Any:
        validator = getattr(self.gateway, "validate_all", None)
        if callable(validator):
            return await validator()
        return await self.gateway.validate()

    @staticmethod
    def _configuration_risk(
        operation_id: str,
        resource_type: str,
        action: str,
        diff: dict[str, Any],
        proposed: dict[str, Any],
    ) -> ChangeRiskAssessment:
        if resource_type == "helper":
            return ChangeRiskAssessment(
                level=RiskLevel.MEDIUM,
                reasons=[
                    "Creating or changing a helper can alter dependent Home Assistant behavior"
                ],
                apply_allowed=True,
                evidence=[
                    {
                        "field": operation_id,
                        "trigger": "helper_configuration_change",
                    }
                ],
                warnings=[],
            )

        risk_config = proposed
        risk_diff = diff
        if resource_type == "script":
            risk_config = dict(proposed)
            if "sequence" in proposed and not any(
                key in proposed for key in ("action", "actions")
            ):
                risk_config["action"] = proposed["sequence"]
            risk_diff = dict(diff)
            changed_fields = [
                dict(item)
                for item in diff.get("changed_fields", [])
                if isinstance(item, dict)
            ]
            if any(item.get("field") == "sequence" for item in changed_fields):
                changed_fields.append(
                    {
                        "field": "actions",
                        "change_type": "modified",
                    }
                )
            risk_diff["changed_fields"] = changed_fields
        legacy_operation = (
            ChangeOperation.CREATE_AUTOMATION
            if action == "create"
            else ChangeOperation.UPDATE_AUTOMATION
        )
        risk = classify_risk(legacy_operation, risk_diff, risk_config)
        if resource_type == "script":
            risk.reasons = [
                reason.replace("automation", "script")
                for reason in risk.reasons
            ]
        return risk

    @staticmethod
    def _aggregate_configuration_risk(
        operations: list[ConfigurationOperation],
    ) -> ChangeRiskAssessment:
        rank = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        level = max(
            (operation.risk.level for operation in operations),
            key=lambda value: rank[value],
        )
        reasons = sorted(
            {
                f"{operation.operation_id}: {reason}"
                for operation in operations
                for reason in operation.risk.reasons
            }
        )
        evidence = [
            {"operation_id": operation.operation_id, **item}
            for operation in operations
            for item in operation.risk.evidence
        ][:100]
        warnings = sorted(
            {
                f"{operation.operation_id}: {warning}"
                for operation in operations
                for warning in operation.risk.warnings
            }
        )
        return ChangeRiskAssessment(
            level=level,
            reasons=reasons,
            apply_allowed=all(
                operation.risk.apply_allowed for operation in operations
            ),
            evidence=evidence,
            warnings=warnings,
        )

    async def create_plan(
        self,
        *,
        title: str,
        description: str,
        operation: str,
        automation_id: str,
        proposed_config: dict[str, Any],
        expiration_minutes: int = 60,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            change_operation = ChangeOperation(operation)
        except ValueError as exc:
            raise GovernanceError(ErrorCode.UNSUPPORTED_CHANGE_OPERATION) from exc
        expiration_minutes = max(5, min(int(expiration_minutes), 1440))
        valid, errors, warnings = validate_automation(automation_id, proposed_config)
        encoded_proposal = json.dumps(proposed_config, default=str)
        if any(secret in encoded_proposal for secret in self.sensitive_values):
            raise GovernanceError(
                ErrorCode.AUTOMATION_VALIDATION_FAILED,
                details={"validation_errors": ["The proposal contains prohibited sensitive data."]},
            )
        if any(
            "cannot be persisted" in error
            for error in errors
        ):
            raise GovernanceError(
                ErrorCode.AUTOMATION_VALIDATION_FAILED,
                details={"validation_errors": ["The proposal contains prohibited sensitive data."]},
            )
        current = await self.gateway.get(automation_id) if valid else None
        failure_code = ErrorCode.AUTOMATION_VALIDATION_FAILED
        if valid and change_operation == ChangeOperation.CREATE_AUTOMATION and current is not None:
            errors.append("automation_id already exists")
            valid = False
            failure_code = ErrorCode.CONFIGURATION_CONFLICT
        if valid and change_operation == ChangeOperation.UPDATE_AUTOMATION and current is None:
            errors.append("automation_id does not exist")
            valid = False
            failure_code = ErrorCode.AUTOMATION_NOT_FOUND

        normalized_proposed = normalize_automation(proposed_config) or {}
        normalized_current = normalize_automation(current)
        diff = structured_diff(current, proposed_config)
        if valid and change_operation == ChangeOperation.UPDATE_AUTOMATION and not diff["has_changes"]:
            return {
                "outcome": "no_change",
                "plan_created": False,
                "target_type": "automation",
                "target_id": automation_id,
                "dry_run_results": diff,
                "apply_allowed": False,
            }

        now = self.now()
        risk = classify_risk(change_operation, diff, proposed_config)
        plan = ChangePlan(
            plan_id=self._new_id(),
            plan_version=1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=expiration_minutes)).isoformat(),
            status=PlanStatus.AWAITING_APPROVAL if valid else PlanStatus.VALIDATION_FAILED,
            title=title[:160],
            description=description[:1000],
            requested_by=current_caller_id(),
            target=ChangeTarget("automation", automation_id),
            operation=change_operation,
            proposed_config=proposed_config,
            current_config=current,
            normalized_proposed_config=normalized_proposed,
            normalized_current_config=normalized_current,
            current_state_fingerprint=state_fingerprint(current),
            proposed_config_hash=stable_hash(normalized_proposed),
            risk=risk,
            normalization_version=AUTOMATION_NORMALIZATION_VERSION,
            warnings=warnings,
            validation_results={"valid": valid, "errors": errors},
            dry_run_results=diff,
            rollback=ChangeRollback(
                available=False,
                status=("not_yet_available" if change_operation == ChangeOperation.UPDATE_AUTOMATION else "unavailable_for_create"),
            ),
            caller_context=sanitize_context(caller_context, self.sensitive_values),
        )
        self._record(
            plan,
            "change_plan_created" if valid else "change_plan_validation_failed",
            "success" if valid else "failure",
            error_code=None if valid else failure_code.value,
        )
        self._supersede_prior(plan)
        if not valid:
            raise GovernanceError(
                failure_code,
                details={"resource_id": plan.plan_id, "validation_errors": errors},
            )
        return self._public(plan)

    async def create_configuration_plan(
        self,
        *,
        title: str,
        description: str,
        operations: list[dict[str, Any]],
        expiration_minutes: int = 60,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one immutable, ordered contract-v2 configuration plan."""

        if not isinstance(operations, list) or not (
            1 <= len(operations) <= MAX_CONFIGURATION_OPERATIONS
        ):
            raise GovernanceError(
                ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                details={
                    "validation_errors": [
                        "operations must contain between 1 and 8 items"
                    ]
                },
            )

        # Inspect all secret-bearing request surfaces before any Home Assistant
        # read or other persistence. Redacting proposed configuration would
        # mutate the exact operational payload, so secret detection rejects the
        # complete request instead.
        if persistence_safety_errors(
            {
                "plan_title": title,
                "plan_description": description,
                "operations": operations,
            },
            self.sensitive_values,
        ):
            raise GovernanceError(
                ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                details={
                    "validation_errors": [
                        "The proposal contains prohibited sensitive data."
                    ]
                },
            )

        prepared: list[ConfigurationOperation] = []
        seen_operation_ids: set[str] = set()
        seen_targets: set[tuple[str, str]] = set()
        required_operation_keys = {
            "operation_id",
            "resource_type",
            "action",
            "target_id",
            "proposed_config",
        }
        allowed_operation_keys = required_operation_keys | {
            "helper_type",
            "depends_on",
        }
        for index, raw_operation in enumerate(operations):
            if not isinstance(raw_operation, dict):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"operation {index + 1} must be an object"
                        ]
                    },
                )
            unknown_keys = set(raw_operation) - allowed_operation_keys
            missing_keys = required_operation_keys - set(raw_operation)
            if unknown_keys or missing_keys:
                validation_errors = []
                if missing_keys:
                    validation_errors.append(
                        "operation "
                        f"{index + 1} is missing required fields: "
                        + ", ".join(sorted(missing_keys))
                    )
                if unknown_keys:
                    validation_errors.append(
                        "operation "
                        f"{index + 1} contains unsupported fields: "
                        + ", ".join(
                            sorted(str(key) for key in unknown_keys)
                        )
                    )
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={"validation_errors": validation_errors},
                )

            operation_id_value = raw_operation["operation_id"]
            operation_id = (
                operation_id_value
                if isinstance(operation_id_value, str)
                else ""
            )
            if (
                not operation_id
                or len(operation_id) > 64
                or any(
                    character
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                    for character in operation_id
                )
                or operation_id in seen_operation_ids
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"operation {index + 1} has an invalid or duplicate operation_id"
                        ]
                    },
                )
            resource_type_value = raw_operation["resource_type"]
            resource_type = (
                resource_type_value
                if isinstance(resource_type_value, str)
                else ""
            )
            if resource_type not in SUPPORTED_CONFIGURATION_RESOURCES:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: resource_type must be automation, script, or helper"
                        ]
                    },
                )
            helper_type_value = raw_operation.get("helper_type")
            if "helper_type" in raw_operation and not isinstance(
                helper_type_value, str
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: helper_type must be a string"
                        ]
                    },
                )
            if (
                resource_type != "helper"
                and "helper_type" in raw_operation
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: helper_type is permitted only for helper operations"
                        ]
                    },
                )
            helper_type = (
                helper_type_value
                if isinstance(helper_type_value, str)
                else None
            )
            resolved_type = self._resolved_resource_type(
                resource_type, helper_type
            )
            action_value = raw_operation["action"]
            action = (
                action_value if isinstance(action_value, str) else ""
            )
            if action not in SUPPORTED_CONFIGURATION_ACTIONS:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: action must be create or update"
                        ]
                    },
                )
            target_id_value = raw_operation["target_id"]
            target_id = (
                target_id_value
                if isinstance(target_id_value, str)
                else ""
            )
            proposed_config = raw_operation["proposed_config"]
            depends_on = raw_operation.get("depends_on", [])
            if not isinstance(depends_on, list) or any(
                not isinstance(value, str) for value in depends_on
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: depends_on must be a list of operation IDs"
                        ]
                    },
                )
            if len(set(depends_on)) != len(depends_on) or any(
                value not in seen_operation_ids for value in depends_on
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: dependencies must be unique earlier operations"
                        ]
                    },
                )
            target_key = (resolved_type, target_id)
            if target_key in seen_targets:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "validation_errors": [
                            f"{operation_id}: duplicate resource target in one plan"
                        ]
                    },
                )
            valid, errors, warnings = validate_resource(
                resolved_type,
                target_id,
                proposed_config,
                self.sensitive_values,
            )
            if action == "create":
                errors.extend(
                    validate_resource_create_identity(
                        resolved_type,
                        target_id,
                        proposed_config,
                    )
                )
                valid = valid and not errors
            if not valid:
                safe_errors = [
                    "The proposal contains prohibited sensitive data."
                    if (
                        "cannot be persisted" in error
                        or "prohibited sensitive data" in error
                    )
                    else error
                    for error in errors
                ]
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "resource_id": target_id,
                        "operation_id": operation_id,
                        "validation_errors": safe_errors,
                    },
                )

            current = await self._read_configuration_resource(
                resolved_type, target_id
            )
            if current is not None and persistence_safety_errors(
                current, self.sensitive_values
            ):
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "resource_id": target_id,
                        "operation_id": operation_id,
                        "validation_errors": [
                            "The current resource contains prohibited "
                            "sensitive data and cannot be persisted in a "
                            "configuration plan."
                        ],
                    },
                )
            if action == "create" and current is not None:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_CONFLICT,
                    details={
                        "resource_id": target_id,
                        "operation_id": operation_id,
                    },
                )
            if action == "update" and current is None:
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                    details={
                        "resource_id": target_id,
                        "operation_id": operation_id,
                        "validation_errors": [
                            "The update target does not exist."
                        ],
                    },
                )

            normalized_proposed = (
                normalize_resource_config(resolved_type, proposed_config) or {}
            )
            normalized_current = normalize_resource_config(
                resolved_type, current
            )
            diff = structured_resource_diff(
                resolved_type, current, proposed_config
            )
            risk = self._configuration_risk(
                operation_id,
                resource_type,
                action,
                diff,
                proposed_config,
            )
            prepared.append(
                ConfigurationOperation(
                    operation_id=operation_id,
                    order=index,
                    depends_on=list(depends_on),
                    resource_type=resource_type,
                    action=action,
                    target_id=target_id,
                    helper_type=helper_type,
                    proposed_config=proposed_config,
                    current_config=current,
                    normalized_proposed_config=normalized_proposed,
                    normalized_current_config=normalized_current,
                    current_state_fingerprint=resource_fingerprint(
                        resolved_type, current
                    ),
                    proposed_config_hash=stable_hash(normalized_proposed),
                    normalization_version=RESOURCE_NORMALIZATION_VERSION,
                    risk=risk,
                    warnings=warnings,
                    validation_results={"valid": True, "errors": []},
                    dry_run_results=diff,
                )
            )
            seen_operation_ids.add(operation_id)
            seen_targets.add(target_key)

        expiration_minutes = max(5, min(int(expiration_minutes), 1440))
        aggregate_risk = self._aggregate_configuration_risk(prepared)
        now = self.now()
        plan_id = self._new_id()
        plan = ChangePlan(
            plan_id=plan_id,
            plan_version=1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=expiration_minutes)).isoformat(),
            status=PlanStatus.AWAITING_APPROVAL,
            title=title[:160],
            description=description[:1000],
            requested_by=current_caller_id(),
            target=ChangeTarget("configuration_plan", plan_id),
            operation=ChangeOperation.CONFIGURATION_PLAN,
            proposed_config={},
            current_config=None,
            normalized_proposed_config={},
            normalized_current_config=None,
            current_state_fingerprint=stable_hash(
                [
                    operation.current_state_fingerprint
                    for operation in prepared
                ]
            ),
            proposed_config_hash=stable_hash(
                [operation.proposed_config_hash for operation in prepared]
            ),
            risk=aggregate_risk,
            normalization_version=RESOURCE_NORMALIZATION_VERSION,
            warnings=aggregate_risk.warnings,
            validation_results={"valid": True, "errors": []},
            dry_run_results={
                "has_changes": any(
                    operation.dry_run_results.get("has_changes")
                    for operation in prepared
                ),
                "operation_count": len(prepared),
                "operations": [
                    {
                        "operation_id": operation.operation_id,
                        "order": operation.order,
                        "resource_type": operation.resource_type,
                        "helper_type": operation.helper_type,
                        "action": operation.action,
                        "target_id": operation.target_id,
                        "depends_on": list(operation.depends_on),
                        "dry_run_results": operation.dry_run_results,
                    }
                    for operation in prepared
                ],
            },
            rollback=ChangeRollback(
                available=False,
                status="unavailable_for_configuration_plan",
            ),
            caller_context=_sanitize_configuration_caller_context(
                caller_context,
                known_secrets=self.sensitive_values,
            ),
            contract_version=CONFIGURATION_PLAN_CONTRACT_VERSION,
            operations=prepared,
            execution_outcome="not_started",
            configuration_check_status="not_run",
        )
        self._record(plan, "change_plan_created", "success")
        self._supersede_prior(plan)
        return self._public(plan)

    def _supersede_prior(self, new_plan: ChangePlan) -> None:
        new_targets = self._plan_target_keys(new_plan)
        try:
            plans = self.repository.list()
        except ChangePlanStorageError as exc:
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_STORAGE_ERROR
            ) from exc
        for plan in plans:
            self._require_v2_persisted_plan_safe(plan)
            self._resolve_lifecycle(plan)
            if (
                plan.plan_id != new_plan.plan_id
                and bool(self._plan_target_keys(plan) & new_targets)
                and plan.status in {
                    PlanStatus.AWAITING_APPROVAL,
                    PlanStatus.APPROVED,
                    PlanStatus.ROLLBACK_PENDING,
                }
            ):
                plan.status = PlanStatus.SUPERSEDED
                plan.approval.state = ApprovalState.INVALIDATED
                plan.approval.csrf_digest = None
                if plan.approval.challenge_id:
                    self._record(plan, "external_approval_invalidated", "rejected")
                self._record(plan, "change_plan_superseded", "rejected")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        self._resolve_lifecycle(plan)
        return self._public(plan)

    def resolved_plans(self) -> list[ChangePlan]:
        """Return persisted plans after applying the shared effective lifecycle."""

        try:
            plans = self.repository.list()
        except ChangePlanStorageError as exc:
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_STORAGE_ERROR
            ) from exc
        for plan in plans:
            self._require_v2_persisted_plan_safe(plan)
            self._resolve_lifecycle(plan)
        return plans

    def list_plans(self, status: str = "", limit: int = 20) -> dict[str, Any]:
        plans = []
        for plan in self.resolved_plans():
            if status and plan.status.value != status:
                continue
            plans.append(self._summary(plan))
            if len(plans) >= max(1, min(limit, 100)):
                break
        return {"count": len(plans), "plans": plans}

    def approve(self, plan_id: str, expected_plan_hash: str, approval_note: str = "") -> dict[str, Any]:
        """Request external approval without granting authority to the MCP caller."""

        plan = self._load(plan_id)
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_REJECTED)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            raise GovernanceError(
                ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                details={"resource_id": plan.plan_id, "reason": "active_plan_must_be_recreated"},
            )
        self._require_current_normalization(plan)
        calculated = self.plan_hash(plan)
        if expected_plan_hash != calculated:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        if plan.approval.state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}:
            raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.status not in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        if not plan.validation_results.get("valid"):
            raise GovernanceError(
                ErrorCode.CONFIGURATION_VALIDATION_FAILED
                if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION
                else ErrorCode.AUTOMATION_VALIDATION_FAILED
            )
        if plan.risk.level == RiskLevel.HIGH:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.HIGH_RISK_CHANGE_REJECTED.value)
            raise GovernanceError(ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        if self._active_challenge_matches(plan, calculated):
            return self._approval_pending_response(plan)
        if plan.approval.state == ApprovalState.EXTERNAL_PENDING:
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.csrf_digest = None
            self._record(plan, "external_approval_invalidated", "rejected")

        approval_kind = "rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply"
        requested_at = self._timestamp()
        challenge_expires = min(
            self.now() + APPROVAL_CHALLENGE_TTL,
            datetime.fromisoformat(plan.expires_at),
        ).isoformat()
        sanitized_note = sanitize_untrusted_data(
            approval_note[:500],
            known_secrets=self.sensitive_values,
            max_string=500,
        ).value
        plan.approval = ChangeApproval(
            state=ApprovalState.EXTERNAL_PENDING,
            authority_version=APPROVAL_AUTHORITY_VERSION,
            channel=APPROVAL_CHANNEL,
            bound_plan_hash=calculated,
            approval_kind=approval_kind,
            challenge_id=secrets.token_urlsafe(24),
            challenge_requested_at=requested_at,
            challenge_expires_at=challenge_expires,
            challenge_plan_version=plan.plan_version,
            challenge_target_type=plan.target_type,
            challenge_target_id=plan.target_id,
            challenge_operation=plan.operation.value,
            challenge_risk_level=plan.risk.level.value,
            request_note=sanitized_note if isinstance(sanitized_note, str) and sanitized_note else None,
        )
        self._record(plan, "external_approval_requested", "success")
        return self._approval_pending_response(plan)

    def _approval_pending_response(self, plan: ChangePlan) -> dict[str, Any]:
        summary = {
            "status": "approval_pending",
            "approval_lifecycle": "approval_pending_external",
            "approval_challenge_created": True,
            "plan_id": plan.plan_id,
            "approval_kind": plan.approval.approval_kind,
            "bound_plan_hash": plan.approval.bound_plan_hash,
            "external_approval_required": True,
            "approval_channel": APPROVAL_CHANNEL,
            "challenge_id": plan.approval.challenge_id,
            "requested_at": plan.approval.challenge_requested_at,
            "challenge_expires_at": plan.approval.challenge_expires_at,
            "approval_ui": "Open the HA MCP Engineering approval panel in Home Assistant.",
            "plan_expires_at": plan.expires_at,
            "plan_status": plan.status.value,
            "approval_state": plan.approval.state.value,
            "authority_version": APPROVAL_AUTHORITY_VERSION,
        }
        return summary

    def _active_challenge_matches(self, plan: ChangePlan, calculated: str) -> bool:
        approval = plan.approval
        return bool(
            approval.state == ApprovalState.EXTERNAL_PENDING
            and plan.status in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}
            and approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and approval.channel == APPROVAL_CHANNEL
            and approval.bound_plan_hash == calculated
            and approval.challenge_plan_version == plan.plan_version
            and approval.challenge_target_type == plan.target_type
            and approval.challenge_target_id == plan.target_id
            and approval.challenge_operation == plan.operation.value
            and approval.challenge_risk_level == plan.risk.level.value
            and approval.approval_kind
            == ("rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply")
            and not self._challenge_has_expired(plan)
        )

    def _expire_challenge_if_needed(self, plan: ChangePlan) -> bool:
        if not self._challenge_has_expired(plan):
            return False
        plan.approval.state = ApprovalState.EXPIRED
        plan.approval.csrf_digest = None
        self._record(
            plan,
            "external_approval_expired",
            "rejected",
            error_code=ErrorCode.EXTERNAL_APPROVAL_EXPIRED.value,
        )
        return True

    def pending_external_reviews(self) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for plan in self.resolved_plans():
            calculated = self.plan_hash(plan)
            if not self._active_challenge_matches(plan, calculated):
                continue
            reviews.append(self._review_summary(plan))
        return reviews

    def _configuration_approval_review_complete(
        self, plan: ChangePlan
    ) -> bool:
        action_count = 0
        detail_count = 0
        for operation in plan.operations:
            projection = _configuration_approval_projection(
                operation.resource_type,
                operation.proposed_config,
                known_secrets=self.sensitive_values,
            )
            if (
                projection.get("status") != "complete"
                or projection.get("redaction_applied") is True
                or projection.get("truncation_applied") is True
            ):
                return False
            metadata = projection.get("metadata")
            actions = projection.get("actions")
            controls = projection.get("controls")
            if (
                not isinstance(metadata, list)
                or len(metadata) > MAX_APPROVAL_PROJECTION_METADATA
                or not isinstance(actions, list)
                or len(actions) > MAX_APPROVAL_PROJECTION_STEPS
                or not isinstance(controls, list)
                or len(controls) > MAX_APPROVAL_PROJECTION_CONTROLS
            ):
                return False
            action_count += len(actions) + len(controls)
            detail_count += len(metadata)
            for entry in [*actions, *controls]:
                if not isinstance(entry, dict):
                    return False
                targets = entry.get("targets")
                data = entry.get("data")
                if (
                    not isinstance(targets, list)
                    or len(targets) > MAX_APPROVAL_PROJECTION_TARGETS
                    or not isinstance(data, list)
                    or len(data) > MAX_APPROVAL_PROJECTION_DATA
                ):
                    return False
                detail_count += len(targets) + len(data)
        return (
            action_count <= MAX_APPROVAL_PROJECTION_ACTIONS_PER_PLAN
            and detail_count <= MAX_APPROVAL_PROJECTION_DETAILS_PER_PLAN
        )

    def _review_summary(self, plan: ChangePlan) -> dict[str, Any]:
        self._require_v2_persisted_plan_safe(plan)
        changed_fields = []
        for item in plan.dry_run_results.get("changed_fields", [])[:50]:
            if not isinstance(item, dict):
                continue
            sanitized = sanitize_untrusted_data(
                item,
                known_secrets=self.sensitive_values,
                max_string=500,
            )
            item = sanitized.value if isinstance(sanitized.value, dict) else {}
            changed_fields.append(
                {
                    "field": str(item.get("field") or "")[:160],
                    "before": str(item.get("before") or "")[:500],
                    "after": str(item.get("after") or "")[:500],
                }
            )
        summary = {
            "plan_id": plan.plan_id,
            "title": plan.title[:160],
            "description": plan.description[:1000],
            "plan_hash": self.plan_hash(plan),
            "plan_version": plan.plan_version,
            "approval_kind": plan.approval.approval_kind,
            "operation": plan.operation.value,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "risk_level": plan.risk.level.value,
            "expires_at": plan.expires_at,
            "challenge_id": plan.approval.challenge_id,
            "challenge_expires_at": plan.approval.challenge_expires_at,
            "request_note": str(
                sanitize_untrusted_data(
                    plan.approval.request_note or "",
                    known_secrets=self.sensitive_values,
                    max_string=500,
                ).value
            )[:500],
            "changed_fields": changed_fields,
            "warnings": [str(value)[:500] for value in plan.warnings[:20]],
            "validation_valid": bool(plan.validation_results.get("valid")),
            "apply_allowed": self._public(plan, include_configs=False)["apply_allowed"],
            "approval_state": plan.approval.state.value,
            "original_apply_timestamp": plan.applied_at if plan.approval.approval_kind == "rollback" else None,
            "current_post_apply_fingerprint": plan.post_apply_fingerprint if plan.approval.approval_kind == "rollback" else None,
            "snapshot_fingerprint": plan.snapshot.fingerprint if plan.snapshot and plan.approval.approval_kind == "rollback" else None,
            "rollback_target": plan.target_id if plan.approval.approval_kind == "rollback" else None,
        }
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            operation_summaries = []
            for operation in sorted(
                plan.operations, key=lambda item: item.order
            )[:MAX_CONFIGURATION_OPERATIONS]:
                operation_summaries.append(
                    {
                        "operation_id": operation.operation_id,
                        "order": operation.order,
                        "depends_on": list(operation.depends_on),
                        "resource_type": operation.resource_type,
                        "helper_type": operation.helper_type,
                        "action": operation.action,
                        "target_id": operation.target_id,
                        "risk_level": operation.risk.level.value,
                        "risk_reasons": operation.risk.reasons[:20],
                        "warnings": operation.warnings[:20],
                        "validation_valid": bool(
                            operation.validation_results.get("valid")
                        ),
                        "semantic_projection": (
                            _configuration_approval_projection(
                                operation.resource_type,
                                operation.proposed_config,
                                known_secrets=self.sensitive_values,
                            )
                        ),
                        "changed_fields": [
                            {
                                "field": str(item.get("field") or "")[:160],
                                "before": str(item.get("before") or "")[:500],
                                "after": str(item.get("after") or "")[:500],
                            }
                            for item in operation.dry_run_results.get(
                                "changed_fields", []
                            )[:50]
                            if isinstance(item, dict)
                        ],
                    }
                )
            summary["operation_summaries"] = operation_summaries
            summary["operation_count"] = len(plan.operations)
            summary["non_atomic_failure_policy"] = (
                "Operations execute in order and stop on first failure; "
                "successful earlier operations are not automatically rolled back."
            )
        sanitized = sanitize_untrusted_data(
            summary,
            known_secrets=self.sensitive_values,
            max_string=2_000,
        ).value
        if not isinstance(sanitized, dict):
            raise GovernanceError(ErrorCode.INTERNAL_SERVER_ERROR)
        return sanitized

    async def issue_external_csrf(self, plan_id: str, challenge_id: str) -> tuple[dict[str, Any], str]:
        lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with lock:
            plan = self._load(plan_id)
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.approval.state == ApprovalState.EXPIRED:
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_EXPIRED)
            calculated = self.plan_hash(plan)
            if plan.approval.challenge_id != challenge_id or not self._active_challenge_matches(plan, calculated):
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_INVALID)
            nonce = secrets.token_urlsafe(32)
            plan.approval.csrf_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            plan.approval.csrf_issued_at = self._timestamp()
            self._record(plan, "external_approval_viewed", "success")
            return self._review_summary(plan), nonce

    async def decide_external_approval(
        self,
        *,
        plan_id: str,
        challenge_id: str,
        expected_plan_hash: str,
        approval_kind: str,
        csrf_nonce: str,
        decision: str,
        approver_principal: str,
    ) -> dict[str, Any]:
        """Perform the private Ingress-authority decision under the plan lock."""

        lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with lock:
            plan = self._load(plan_id)
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.approval.state == ApprovalState.EXPIRED:
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_EXPIRED)
            calculated = self.plan_hash(plan)
            approval = plan.approval
            if approval.challenge_id != challenge_id or not self._active_challenge_matches(plan, calculated):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            if expected_plan_hash != calculated or approval.bound_plan_hash != calculated:
                self._reject_external_decision(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
            if approval_kind != approval.approval_kind:
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            csrf_digest = hashlib.sha256(csrf_nonce.encode("utf-8")).hexdigest()
            if not approval.csrf_digest or not hmac.compare_digest(approval.csrf_digest, csrf_digest):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            if (
                decision == "approve"
                and plan.contract_version
                >= CONFIGURATION_PLAN_CONTRACT_VERSION
                and not self._configuration_approval_review_complete(plan)
            ):
                self._reject_external_decision(
                    plan, ErrorCode.EXTERNAL_APPROVAL_INVALID
                )
            approval.csrf_digest = None
            approval.csrf_issued_at = None
            principal = (approver_principal or DEFAULT_APPROVER_PRINCIPAL)[:160]
            if decision == "approve":
                approval.state = ApprovalState.APPROVED
                approval.approved_at = self._timestamp()
                approval.approval_expires_at = plan.expires_at
                approval.channel = APPROVAL_CHANNEL
                approval.approver_principal = principal
                approval.principal_separation_enforced = True
                if approval.approval_kind == "apply":
                    plan.status = PlanStatus.APPROVED
                else:
                    plan.rollback.approved_at = approval.approved_at
                self._record(
                    plan,
                    "external_approval_granted",
                    "success",
                    approval_principal=principal,
                )
                return {"status": "approved", "plan_id": plan.plan_id, "approval_kind": approval.approval_kind}
            if decision == "reject":
                approval.state = ApprovalState.REJECTED
                approval.channel = APPROVAL_CHANNEL
                approval.approver_principal = principal
                approval.principal_separation_enforced = True
                plan.status = PlanStatus.REJECTED
                self._record(
                    plan,
                    "external_approval_rejected",
                    "rejected",
                    error_code=ErrorCode.CHANGE_PLAN_REJECTED.value,
                    approval_principal=principal,
                )
                return {"status": "rejected", "plan_id": plan.plan_id, "approval_kind": approval.approval_kind}
            self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    def _reject_external_decision(self, plan: ChangePlan, code: ErrorCode) -> None:
        self._record(
            plan,
            "external_approval_decision_failed",
            "rejected",
            error_code=code.value,
        )
        raise GovernanceError(code)

    async def apply(self, plan_id: str, expected_plan_hash: str = "") -> dict[str, Any]:
        plan_lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with plan_lock:
            plan = self._load(plan_id)
            if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
                return await self._apply_configuration_plan(
                    plan, expected_plan_hash
                )
            # Reuse an already-present legacy bare-ID lock for compatibility
            # with existing in-process callers, then publish the typed key used
            # by both contract versions.
            legacy_target_lock = self._target_locks.get(plan.target_id)
            target_lock = self._target_locks.setdefault(
                ("automation", plan.target_id),
                legacy_target_lock or asyncio.Lock(),
            )
            if target_lock.locked():
                self._record(
                    plan,
                    "change_apply_rejected",
                    "rejected",
                    error_code=ErrorCode.CHANGE_IN_PROGRESS.value,
                )
                raise GovernanceError(ErrorCode.CHANGE_IN_PROGRESS)
            async with target_lock:
                return await self._apply_locked(plan, expected_plan_hash)

    def _configuration_writer_available(
        self, operations: list[ConfigurationOperation]
    ) -> bool:
        if callable(getattr(self.gateway, "read", None)) and callable(
            getattr(self.gateway, "write", None)
        ):
            return True
        return bool(
            callable(getattr(self.gateway, "get", None))
            and callable(getattr(self.gateway, "write", None))
            and all(
                self._resolved_resource_type(
                    operation.resource_type, operation.helper_type
                )
                == "automation"
                for operation in operations
            )
        )

    @staticmethod
    def _operation_receipts(plan: ChangePlan) -> list[dict[str, Any]]:
        return [
            {
                "operation_id": operation.operation_id,
                "order": operation.order,
                "resource_type": operation.resource_type,
                "helper_type": operation.helper_type,
                "action": operation.action,
                "target_id": operation.target_id,
                "execution_status": operation.execution_status.value,
                "execution_receipt": operation.execution_receipt,
                "verification": operation.verification.__dict__,
                "failure_information": operation.failure_information,
            }
            for operation in sorted(plan.operations, key=lambda item: item.order)
        ]

    def _mark_unattempted_operations(
        self,
        plan: ChangePlan,
        *,
        after_order: int,
        failed_operation_id: str,
        error_code: ErrorCode = ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
    ) -> None:
        for operation in sorted(plan.operations, key=lambda item: item.order):
            if (
                operation.order <= after_order
                or operation.execution_status != StepExecutionStatus.PENDING
            ):
                continue
            operation.execution_status = (
                StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE
            )
            operation.execution_receipt = {
                "write_attempted": False,
                "reason": "prior_operation_failed",
                "blocked_by_operation_id": failed_operation_id,
            }
            self._record(
                plan,
                "configuration_operation_not_attempted",
                "rejected",
                error_code=error_code.value,
                operation_step=operation,
            )

    @staticmethod
    def _invalidate_dependency_index() -> None:
        from ..dependency import DEPENDENCY_ANALYSIS

        DEPENDENCY_ANALYSIS.invalidate()

    async def _apply_configuration_plan(
        self, plan: ChangePlan, expected_plan_hash: str
    ) -> dict[str, Any]:
        started = time.perf_counter()
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.risk.level == RiskLevel.HIGH:
            self._reject_apply(plan, ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        self._require_current_normalization(plan)
        calculated = self.plan_hash(plan)
        hash_validation = (
            {"performed": True, "result": "matched"}
            if expected_plan_hash
            else {"performed": False, "reason": "not_supplied"}
        )
        if expected_plan_hash and expected_plan_hash != calculated:
            self._reject_apply(
                plan,
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={
                    "hash_validation": {
                        "performed": True,
                        "result": "mismatch",
                    }
                },
            )

        if plan.status == PlanStatus.APPLIED:
            for operation in sorted(
                plan.operations, key=lambda item: item.order
            ):
                resource_type = self._resolved_resource_type(
                    operation.resource_type, operation.helper_type
                )
                current = await self._read_configuration_resource(
                    resource_type, operation.target_id
                )
                if (
                    not resource_identity_matches(
                        resource_type, operation.target_id, current
                    )
                    or resource_fingerprint(resource_type, current)
                    != operation.proposed_config_hash
                ):
                    raise GovernanceError(
                        ErrorCode.APPROVAL_ALREADY_CONSUMED,
                        details={
                            "resource_id": operation.target_id,
                            "operation_id": operation.operation_id,
                        },
                    )
            return {
                "status": "already_applied",
                "execution_outcome": plan.execution_outcome,
                "hash_validation": hash_validation,
                "operations": self._operation_receipts(plan),
                "plan": self._public(plan, include_configs=False),
            }

        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_REJECTED)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            self._reject_apply(plan, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if not self._valid_external_approval(plan, "apply"):
            self._reject_apply(
                plan,
                ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
                details={"hash_validation": hash_validation},
            )
        if plan.approval.bound_plan_hash != calculated:
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
        if not self._configuration_writer_available(plan.operations):
            self._reject_apply(
                plan,
                ErrorCode.CONFIGURATION_APPLY_FAILED,
                details={"reason": "resource_provider_unavailable"},
            )

        lock_keys = sorted(self._plan_target_keys(plan))
        locks = [
            self._target_locks.setdefault(key, asyncio.Lock())
            for key in lock_keys
        ]
        if any(lock.locked() for lock in locks):
            self._reject_apply(plan, ErrorCode.CHANGE_IN_PROGRESS)

        async with AsyncExitStack() as stack:
            for lock in locks:
                await stack.enter_async_context(lock)

            # Re-read every target while all typed target locks are held. Any
            # unavailable or stale target stops before approval consumption and
            # before the first write.
            preflight: dict[str, dict[str, Any] | None] = {}
            try:
                for operation in sorted(
                    plan.operations, key=lambda item: item.order
                ):
                    resource_type = self._resolved_resource_type(
                        operation.resource_type, operation.helper_type
                    )
                    current = await self._read_configuration_resource(
                        resource_type, operation.target_id
                    )
                    if current is not None and not resource_identity_matches(
                        resource_type, operation.target_id, current
                    ):
                        self._reject_apply(
                            plan,
                            ErrorCode.CONFIGURATION_VERIFICATION_FAILED,
                            details={
                                "resource_id": operation.target_id,
                                "operation_id": operation.operation_id,
                                "mismatch_fields": ["resource_identity"],
                            },
                        )
                    if (
                        resource_fingerprint(resource_type, current)
                        != operation.current_state_fingerprint
                    ):
                        self._record(
                            plan,
                            "change_apply_rejected",
                            "rejected",
                            error_code=ErrorCode.STALE_TARGET_STATE.value,
                            operation_step=operation,
                        )
                        raise GovernanceError(
                            ErrorCode.STALE_TARGET_STATE,
                            details={
                                "resource_id": operation.target_id,
                                "operation_id": operation.operation_id,
                            },
                        )
                    preflight[operation.operation_id] = current
            except GovernanceError:
                raise
            except Exception as exc:
                self._record(
                    plan,
                    "change_apply_rejected",
                    "rejected",
                    error_code=ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                )
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_APPLY_FAILED,
                    details={"reason": "resource_preflight_unavailable"},
                ) from exc

            plan.status = PlanStatus.APPLYING
            plan.execution_outcome = "applying"
            plan.apply_request_id = current_request_id()
            plan.approval.state = ApprovalState.CONSUMED
            plan.approval.consumed_at = self._timestamp()
            self._record(plan, "external_approval_consumed", "success")
            self._record(plan, "change_apply_started", "success")

            attempted_writes = 0
            successful_writes = 0
            verified_writes = 0
            ambiguous_writes = 0
            for operation in sorted(
                plan.operations, key=lambda item: item.order
            ):
                resource_type = self._resolved_resource_type(
                    operation.resource_type, operation.helper_type
                )
                if any(
                    next(
                        (
                            candidate.execution_status
                            for candidate in plan.operations
                            if candidate.operation_id == dependency
                        ),
                        None,
                    )
                    != StepExecutionStatus.APPLIED_VERIFIED
                    for dependency in operation.depends_on
                ):
                    operation.execution_status = (
                        StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE
                    )
                    operation.execution_receipt = {
                        "write_attempted": False,
                        "reason": "dependency_not_applied",
                    }
                    self._record(
                        plan,
                        "configuration_operation_not_attempted",
                        "rejected",
                        error_code=ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
                        operation_step=operation,
                    )
                    continue

                # The target locks coordinate Engineering callers only; Home
                # Assistant and other administrators can still edit a resource
                # after the all-target preflight. Re-read the exact target
                # immediately before this operation can transition to a write.
                # A changed or unavailable target consumes no additional
                # approval and must never be overwritten.
                prewrite_current: dict[str, Any] | None = None
                prewrite_fingerprint: str | None = None
                prewrite_failure_reason: str | None = None
                prewrite_failure_category: str | None = None
                try:
                    prewrite_current = (
                        await self._read_configuration_resource(
                            resource_type, operation.target_id
                        )
                    )
                    if (
                        prewrite_current is not None
                        and not resource_identity_matches(
                            resource_type,
                            operation.target_id,
                            prewrite_current,
                        )
                    ):
                        prewrite_failure_reason = "resource_identity_mismatch"
                    prewrite_fingerprint = resource_fingerprint(
                        resource_type, prewrite_current
                    )
                    if (
                        prewrite_fingerprint
                        != operation.current_state_fingerprint
                        and prewrite_failure_reason is None
                    ):
                        prewrite_failure_reason = "stale_target_state"
                except Exception as exc:
                    prewrite_failure_reason = (
                        "resource_revalidation_unavailable"
                    )
                    prewrite_failure_category = type(exc).__name__

                if prewrite_failure_reason is not None:
                    stale_target = prewrite_failure_reason in {
                        "resource_identity_mismatch",
                        "stale_target_state",
                    }
                    root_error = (
                        ErrorCode.STALE_TARGET_STATE
                        if stale_target
                        else ErrorCode.CONFIGURATION_APPLY_FAILED
                    )
                    operation.execution_status = StepExecutionStatus.FAILED
                    operation.execution_receipt = {
                        "write_attempted": False,
                        "write_completed": False,
                        "readback_completed": (
                            prewrite_failure_category is None
                        ),
                        "outcome": "not_attempted_prewrite_revalidation_failed",
                        "reason": prewrite_failure_reason,
                        "expected_fingerprint": (
                            operation.current_state_fingerprint
                        ),
                        "observed_fingerprint": prewrite_fingerprint,
                    }
                    operation.failure_information = {
                        "error_code": root_error.value,
                        "reason": prewrite_failure_reason,
                        "failure_category": prewrite_failure_category,
                    }
                    self._record(
                        plan,
                        "configuration_operation_prewrite_revalidation_failed",
                        "rejected",
                        error_code=root_error.value,
                        operation_step=operation,
                    )
                    self._mark_unattempted_operations(
                        plan,
                        after_order=operation.order,
                        failed_operation_id=operation.operation_id,
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = (
                        "partial_failure"
                        if attempted_writes
                        else "not_applied"
                    )
                    configuration_check_details: dict[str, Any] | None = None
                    if attempted_writes:
                        (
                            plan.configuration_check_status,
                            configuration_check_details,
                        ) = await self._config_check_with_details()
                        self._invalidate_dependency_index()
                    else:
                        plan.configuration_check_status = "not_run"
                    outward_error = (
                        ErrorCode.CONFIGURATION_PARTIAL_FAILURE
                        if attempted_writes
                        else root_error
                    )
                    plan.failure_information = {
                        "error_code": outward_error.value,
                        "cause_error_code": root_error.value,
                        "failed_operation_id": operation.operation_id,
                        "failure_reason": prewrite_failure_reason,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                    }
                    if configuration_check_details is not None:
                        plan.failure_information[
                            "configuration_check"
                        ] = configuration_check_details
                    self._record(
                        plan,
                        "change_apply_failed",
                        "failure",
                        error_code=outward_error.value,
                    )
                    details = {
                        "resource_id": operation.target_id,
                        "operation_id": operation.operation_id,
                        "failure_reason": prewrite_failure_reason,
                        "cause_error_code": root_error.value,
                        "write_attempted": False,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                        "execution_outcome": plan.execution_outcome,
                        "configuration_check_status": (
                            plan.configuration_check_status
                        ),
                        "operations": self._operation_receipts(plan),
                    }
                    if configuration_check_details is not None:
                        details[
                            "configuration_check"
                        ] = configuration_check_details
                    raise GovernanceError(
                        outward_error,
                        details=details,
                    )

                operation.snapshot = ChangeSnapshot(
                    self._timestamp(),
                    prewrite_current,
                    operation.current_state_fingerprint,
                )
                if (
                    operation.action == "update"
                    and not operation.dry_run_results.get("has_changes")
                ):
                    operation.execution_status = (
                        StepExecutionStatus.APPLIED_VERIFIED
                    )
                    operation.post_apply_fingerprint = (
                        operation.proposed_config_hash
                    )
                    operation.verification = ChangeVerification(
                        status="passed",
                        checked_at=self._timestamp(),
                        desired_fingerprint=operation.proposed_config_hash,
                        actual_fingerprint=operation.proposed_config_hash,
                        config_check_status="deferred",
                        mismatch_fields=[],
                    )
                    operation.execution_receipt = {
                        "write_attempted": False,
                        "write_completed": False,
                        "readback_completed": True,
                        "outcome": "already_desired",
                        "resulting_fingerprint": operation.proposed_config_hash,
                    }
                    self._record(
                        plan,
                        "configuration_operation_verified",
                        "success",
                        operation_step=operation,
                    )
                    continue

                operation.execution_status = StepExecutionStatus.APPLYING
                operation.execution_receipt = {
                    "write_attempted": True,
                    "write_completed": False,
                    "readback_completed": False,
                }
                self._record(
                    plan,
                    "configuration_operation_started",
                    "success",
                    operation_step=operation,
                )
                attempted_writes += 1
                try:
                    await self._write_configuration_resource(
                        operation.action,
                        resource_type,
                        operation.target_id,
                        operation.proposed_config,
                    )
                    successful_writes += 1
                    operation.execution_receipt["write_completed"] = True
                except ConfigurationMutationNotDispatchedError as exc:
                    attempted_writes = max(0, attempted_writes - 1)
                    reason = exc.details.get("reason")
                    if reason not in {
                        "target_already_exists",
                        "target_entity_id_reserved",
                        "helper_create_preflight_unavailable",
                    }:
                        reason = "helper_create_preflight_unavailable"
                    root_error = (
                        ErrorCode.CONFIGURATION_CONFLICT
                        if reason
                        in {
                            "target_already_exists",
                            "target_entity_id_reserved",
                        }
                        else ErrorCode.CONFIGURATION_APPLY_FAILED
                    )
                    operation.execution_status = StepExecutionStatus.FAILED
                    operation.execution_receipt = {
                        "write_attempted": False,
                        "write_completed": False,
                        "readback_completed": False,
                        "write_result": "not_dispatched",
                        "outcome": "not_applied",
                        "reason": reason,
                    }
                    operation.failure_information = {
                        "error_code": root_error.value,
                        "reason": reason,
                        "mutation_dispatched": False,
                    }
                    self._record(
                        plan,
                        "configuration_operation_not_dispatched",
                        "rejected",
                        error_code=root_error.value,
                        operation_step=operation,
                    )
                    has_prior_mutation = attempted_writes > 0
                    self._mark_unattempted_operations(
                        plan,
                        after_order=operation.order,
                        failed_operation_id=operation.operation_id,
                        error_code=(
                            ErrorCode.CONFIGURATION_PARTIAL_FAILURE
                            if has_prior_mutation
                            else root_error
                        ),
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = (
                        "partial_failure"
                        if has_prior_mutation
                        else "not_applied"
                    )
                    configuration_check_details: dict[str, Any] | None = None
                    if has_prior_mutation:
                        (
                            plan.configuration_check_status,
                            configuration_check_details,
                        ) = await self._config_check_with_details()
                        self._invalidate_dependency_index()
                    else:
                        plan.configuration_check_status = "not_run"
                    outward_error = (
                        ErrorCode.CONFIGURATION_PARTIAL_FAILURE
                        if has_prior_mutation
                        else root_error
                    )
                    plan.failure_information = {
                        "error_code": outward_error.value,
                        "cause_error_code": root_error.value,
                        "failed_operation_id": operation.operation_id,
                        "failure_reason": reason,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                    }
                    if configuration_check_details is not None:
                        plan.failure_information[
                            "configuration_check"
                        ] = configuration_check_details
                    self._record(
                        plan,
                        "change_apply_failed",
                        "failure",
                        error_code=outward_error.value,
                    )
                    details = {
                        "resource_id": operation.target_id,
                        "operation_id": operation.operation_id,
                        "failure_reason": reason,
                        "cause_error_code": root_error.value,
                        "write_attempted": False,
                        "write_completed": False,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                        "execution_outcome": plan.execution_outcome,
                        "configuration_check_status": (
                            plan.configuration_check_status
                        ),
                        "operations": self._operation_receipts(plan),
                    }
                    if configuration_check_details is not None:
                        details[
                            "configuration_check"
                        ] = configuration_check_details
                    raise GovernanceError(
                        outward_error,
                        details=details,
                    ) from exc
                except ConfigurationMutationCompletedUnexpectedlyError as exc:
                    successful_writes += 1
                    candidate = exc.details.get("unexpected_resource_id")
                    unexpected_resource_id = "unknown"
                    if isinstance(candidate, str):
                        domain, separator, object_id = candidate.partition(".")
                        if (
                            separator == "."
                            and domain == resource_type
                            and 0 < len(object_id) <= 128
                            and all(
                                character
                                in "abcdefghijklmnopqrstuvwxyz0123456789_"
                                for character in object_id
                            )
                        ):
                            unexpected_resource_id = candidate
                    operation.execution_status = StepExecutionStatus.FAILED
                    operation.execution_receipt.update(
                        {
                            "write_completed": True,
                            "readback_completed": False,
                            "write_result": "completed_unexpectedly",
                            "outcome": "unexpected_resource_created",
                            "unexpected_resource_id": (
                                unexpected_resource_id
                            ),
                            "orphan_risk": True,
                        }
                    )
                    operation.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                        "reason": "generated_identity_mismatch",
                        "mutation_dispatched": True,
                        "mutation_completed": True,
                        "unexpected_resource_id": unexpected_resource_id,
                        "orphan_risk": True,
                    }
                    self._record(
                        plan,
                        "configuration_operation_completed_unexpectedly",
                        "failure",
                        error_code=ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                        operation_step=operation,
                    )
                    self._mark_unattempted_operations(
                        plan,
                        after_order=operation.order,
                        failed_operation_id=operation.operation_id,
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "partial_failure"
                    self._invalidate_dependency_index()
                    (
                        plan.configuration_check_status,
                        configuration_check_details,
                    ) = await self._config_check_with_details()
                    code = ErrorCode.CONFIGURATION_PARTIAL_FAILURE
                    plan.failure_information = {
                        "error_code": code.value,
                        "cause_error_code": (
                            ErrorCode.CONFIGURATION_APPLY_FAILED.value
                        ),
                        "failed_operation_id": operation.operation_id,
                        "failure_reason": "generated_identity_mismatch",
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                        "unexpected_resource_id": unexpected_resource_id,
                        "orphan_risk": True,
                        "configuration_check": configuration_check_details,
                    }
                    self._record(
                        plan,
                        "change_apply_failed",
                        "failure",
                        error_code=code.value,
                    )
                    raise GovernanceError(
                        code,
                        details={
                            "resource_id": operation.target_id,
                            "operation_id": operation.operation_id,
                            "failure_reason": "generated_identity_mismatch",
                            "attempted_write_count": attempted_writes,
                            "successful_write_count": successful_writes,
                            "verified_write_count": verified_writes,
                            "ambiguous_write_count": ambiguous_writes,
                            "execution_outcome": plan.execution_outcome,
                            "unexpected_resource_id": (
                                unexpected_resource_id
                            ),
                            "orphan_risk": True,
                            "configuration_check": (
                                configuration_check_details
                            ),
                            "operations": self._operation_receipts(plan),
                        },
                    ) from exc
                except Exception as exc:
                    ambiguous_writes += 1
                    unexpected_resource_id: str | None = None
                    if isinstance(exc, HomeAssistantApiError):
                        candidate = exc.details.get(
                            "unexpected_resource_id"
                        )
                        if isinstance(candidate, str):
                            domain, separator, object_id = candidate.partition(
                                "."
                            )
                            if (
                                separator == "."
                                and domain == resource_type
                                and 0 < len(object_id) <= 128
                                and all(
                                    character
                                    in "abcdefghijklmnopqrstuvwxyz0123456789_"
                                    for character in object_id
                                )
                            ):
                                unexpected_resource_id = candidate
                    # A transport failure does not prove that Home Assistant
                    # rejected the write. Perform one bounded exact readback,
                    # persist what is known, and stop. Never continue an
                    # ordered plan after an ambiguous write response.
                    actual_after_error: dict[str, Any] | None = None
                    readback_error_category: str | None = None
                    try:
                        actual_after_error = (
                            await self._read_configuration_resource(
                                resource_type, operation.target_id
                            )
                        )
                        operation.execution_receipt[
                            "readback_completed"
                        ] = True
                    except Exception as readback_exc:
                        readback_error_category = type(readback_exc).__name__

                    actual_after_error_fingerprint = resource_fingerprint(
                        resource_type, actual_after_error
                    )
                    desired_state_proven = (
                        resource_identity_matches(
                            resource_type,
                            operation.target_id,
                            actual_after_error,
                        )
                        and actual_after_error_fingerprint
                        == operation.proposed_config_hash
                    )
                    operation.post_apply_fingerprint = (
                        actual_after_error_fingerprint
                        if operation.execution_receipt["readback_completed"]
                        else None
                    )
                    operation.execution_receipt.update(
                        {
                            "write_result": "ambiguous",
                            "outcome": (
                                "state_proven_desired_after_ambiguous_write"
                                if desired_state_proven
                                else "write_and_resulting_state_unconfirmed"
                            ),
                            "resulting_fingerprint": (
                                actual_after_error_fingerprint
                                if operation.execution_receipt[
                                    "readback_completed"
                                ]
                                else None
                            ),
                        }
                    )
                    operation.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                        "failure_category": type(exc).__name__,
                        "readback_failure_category": (
                            readback_error_category
                        ),
                        "desired_state_proven": desired_state_proven,
                    }
                    if unexpected_resource_id is not None:
                        operation.execution_receipt.update(
                            {
                                "unexpected_resource_id": (
                                    unexpected_resource_id
                                ),
                                "orphan_risk": True,
                            }
                        )
                        operation.failure_information.update(
                            {
                                "unexpected_resource_id": (
                                    unexpected_resource_id
                                ),
                                "orphan_risk": True,
                            }
                        )
                    if desired_state_proven:
                        verified_writes += 1
                        operation.execution_status = (
                            StepExecutionStatus.APPLIED_VERIFIED
                        )
                        operation.verification = ChangeVerification(
                            status="passed",
                            checked_at=self._timestamp(),
                            desired_fingerprint=operation.proposed_config_hash,
                            actual_fingerprint=(
                                actual_after_error_fingerprint
                            ),
                            config_check_status="deferred",
                            mismatch_fields=[],
                        )
                    else:
                        operation.execution_status = (
                            StepExecutionStatus.FAILED
                        )
                    self._record(
                        plan,
                        "configuration_operation_write_ambiguous",
                        "failure",
                        error_code=ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                        operation_step=operation,
                    )
                    self._mark_unattempted_operations(
                        plan,
                        after_order=operation.order,
                        failed_operation_id=operation.operation_id,
                    )
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "partial_failure"
                    self._invalidate_dependency_index()
                    (
                        plan.configuration_check_status,
                        configuration_check_details,
                    ) = await self._config_check_with_details()
                    if desired_state_proven:
                        operation.verification.config_check_status = (
                            plan.configuration_check_status
                        )
                    code = ErrorCode.CONFIGURATION_PARTIAL_FAILURE
                    plan.failure_information = {
                        "error_code": code.value,
                        "failed_operation_id": operation.operation_id,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                        "configuration_check": configuration_check_details,
                    }
                    if unexpected_resource_id is not None:
                        plan.failure_information.update(
                            {
                                "unexpected_resource_id": (
                                    unexpected_resource_id
                                ),
                                "orphan_risk": True,
                            }
                        )
                    self._record(
                        plan,
                        "change_apply_failed",
                        "failure",
                        error_code=code.value,
                    )
                    failure_details = {
                            "resource_id": operation.target_id,
                            "operation_id": operation.operation_id,
                            "desired_state_proven": desired_state_proven,
                            "attempted_write_count": attempted_writes,
                            "successful_write_count": successful_writes,
                            "verified_write_count": verified_writes,
                            "ambiguous_write_count": ambiguous_writes,
                            "execution_outcome": plan.execution_outcome,
                            "configuration_check": (
                                configuration_check_details
                            ),
                            "operations": self._operation_receipts(plan),
                        }
                    if unexpected_resource_id is not None:
                        failure_details.update(
                            {
                                "unexpected_resource_id": (
                                    unexpected_resource_id
                                ),
                                "orphan_risk": True,
                            }
                        )
                    raise GovernanceError(
                        code,
                        details=failure_details,
                    ) from exc

                try:
                    actual = await self._read_configuration_resource(
                        resource_type, operation.target_id
                    )
                    operation.execution_receipt["readback_completed"] = True
                except Exception as exc:
                    actual = None
                    operation.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                        "failure_category": type(exc).__name__,
                    }

                actual_fingerprint = resource_fingerprint(
                    resource_type, actual
                )
                mismatch = []
                if not resource_identity_matches(
                    resource_type, operation.target_id, actual
                ):
                    mismatch.append("resource_identity")
                if actual_fingerprint != operation.proposed_config_hash:
                    mismatch.extend(
                        item["field"]
                        for item in structured_resource_diff(
                            resource_type,
                            operation.proposed_config,
                            actual or {},
                        ).get("changed_fields", [])
                        if isinstance(item, dict) and item.get("field")
                    )
                operation.post_apply_fingerprint = actual_fingerprint
                operation.verification = ChangeVerification(
                    status="failed" if mismatch else "passed",
                    checked_at=self._timestamp(),
                    desired_fingerprint=operation.proposed_config_hash,
                    actual_fingerprint=actual_fingerprint,
                    config_check_status="deferred",
                    mismatch_fields=sorted(set(mismatch)),
                )
                operation.execution_receipt[
                    "resulting_fingerprint"
                ] = actual_fingerprint
                if mismatch:
                    operation.execution_status = (
                        StepExecutionStatus.VERIFICATION_FAILED
                    )
                    operation.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                        "mismatch_fields": sorted(set(mismatch)),
                    }
                    self._record(
                        plan,
                        "configuration_operation_verification_failed",
                        "failure",
                        error_code=ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                        operation_step=operation,
                    )
                    self._mark_unattempted_operations(
                        plan,
                        after_order=operation.order,
                        failed_operation_id=operation.operation_id,
                    )
                    plan.status = PlanStatus.VERIFICATION_FAILED
                    plan.execution_outcome = "partial_failure"
                    self._invalidate_dependency_index()
                    (
                        plan.configuration_check_status,
                        configuration_check_details,
                    ) = await self._config_check_with_details()
                    plan.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
                        "failed_operation_id": operation.operation_id,
                        "attempted_write_count": attempted_writes,
                        "successful_write_count": successful_writes,
                        "verified_write_count": verified_writes,
                        "ambiguous_write_count": ambiguous_writes,
                        "configuration_check": configuration_check_details,
                    }
                    self._record(
                        plan,
                        "change_verification_failed",
                        "failure",
                        error_code=ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
                    )
                    raise GovernanceError(
                        ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
                        details={
                            "resource_id": operation.target_id,
                            "operation_id": operation.operation_id,
                            "mismatch_fields": sorted(set(mismatch)),
                            "attempted_write_count": attempted_writes,
                            "successful_write_count": successful_writes,
                            "verified_write_count": verified_writes,
                            "ambiguous_write_count": ambiguous_writes,
                            "execution_outcome": plan.execution_outcome,
                            "configuration_check": (
                                configuration_check_details
                            ),
                            "operations": self._operation_receipts(plan),
                        },
                    )

                operation.execution_status = (
                    StepExecutionStatus.APPLIED_VERIFIED
                )
                verified_writes += 1
                self._record(
                    plan,
                    "configuration_operation_verified",
                    "success",
                    operation_step=operation,
                )

            duration = round((time.perf_counter() - started) * 1000, 3)
            (
                plan.configuration_check_status,
                configuration_check_details,
            ) = await self._config_check_with_details()
            for operation in plan.operations:
                if operation.verification.status == "passed":
                    operation.verification.config_check_status = (
                        plan.configuration_check_status
                    )
            if plan.configuration_check_status != "valid":
                plan.status = PlanStatus.VERIFICATION_FAILED
                plan.execution_outcome = "verification_failed"
                plan.failure_information = {
                    "error_code": ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                    "reason": "configuration_check_failed",
                    "attempted_write_count": attempted_writes,
                    "successful_write_count": successful_writes,
                    "verified_write_count": verified_writes,
                    "ambiguous_write_count": ambiguous_writes,
                    "configuration_check": configuration_check_details,
                }
                if attempted_writes:
                    self._invalidate_dependency_index()
                self._record(
                    plan,
                    "change_verification_failed",
                    "failure",
                    error_code=ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                    duration_ms=duration,
                )
                raise GovernanceError(
                    ErrorCode.CONFIGURATION_VERIFICATION_FAILED,
                    details={
                        "execution_outcome": plan.execution_outcome,
                        "configuration_check_status": plan.configuration_check_status,
                        "configuration_check": configuration_check_details,
                        "operations": self._operation_receipts(plan),
                    },
                )

            plan.status = PlanStatus.APPLIED
            plan.execution_outcome = "applied"
            plan.applied_at = self._timestamp()
            if attempted_writes:
                self._invalidate_dependency_index()
            self._record(
                plan,
                "change_apply_succeeded",
                "success",
                duration_ms=duration,
            )
            return {
                "status": "applied",
                "execution_outcome": plan.execution_outcome,
                "hash_validation": hash_validation,
                "configuration_check_status": plan.configuration_check_status,
                "operations": self._operation_receipts(plan),
                "plan": self._public(plan, include_configs=False),
            }

    async def _apply_locked(self, plan: ChangePlan, expected_plan_hash: str) -> dict[str, Any]:
        started = time.perf_counter()
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        if plan.risk.level == RiskLevel.HIGH:
            self._reject_apply(plan, ErrorCode.HIGH_RISK_CHANGE_REJECTED)
        self._require_current_normalization(plan)
        if _automation_id_mismatch(plan.target_id, plan.proposed_config):
            self._reject_identity_mismatch(plan)
        calculated = self.plan_hash(plan)
        hash_validation = (
            {"performed": True, "result": "matched"}
            if expected_plan_hash
            else {"performed": False, "reason": "not_supplied"}
        )
        if expected_plan_hash and expected_plan_hash != calculated:
            self._reject_apply(
                plan,
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={"hash_validation": {"performed": True, "result": "mismatch"}},
            )
        if plan.status == PlanStatus.APPLIED:
            current = await self.gateway.get(plan.target_id)
            if (
                not _automation_id_mismatch(plan.target_id, current)
                and state_fingerprint(current) == plan.proposed_config_hash
            ):
                return {
                    "status": "already_applied",
                    "hash_validation": hash_validation,
                    "plan": self._public(plan, include_configs=False),
                }
            mismatch = ["automation_id"] if _automation_id_mismatch(plan.target_id, current) else []
            raise GovernanceError(
                ErrorCode.AUTOMATION_VERIFICATION_FAILED
                if mismatch
                else ErrorCode.APPROVAL_ALREADY_CONSUMED,
                details={"resource_id": plan.plan_id, "mismatch_fields": mismatch},
            )
        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_REJECTED)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            self._reject_apply(plan, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if not self._valid_external_approval(plan, "apply"):
            self._reject_apply(
                plan,
                ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
                details={"hash_validation": hash_validation},
            )
        if (
            stable_hash(normalize_automation(plan.proposed_config) or {})
            != plan.proposed_config_hash
            or plan.approval.bound_plan_hash != calculated
        ):
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
        current = await self.gateway.get(plan.target_id)
        if _automation_id_mismatch(plan.target_id, current):
            self._reject_identity_mismatch(plan)
        if state_fingerprint(current) != plan.current_state_fingerprint:
            self._record(plan, "change_apply_rejected", "rejected", error_code=ErrorCode.STALE_TARGET_STATE.value)
            raise GovernanceError(ErrorCode.STALE_TARGET_STATE)

        plan.snapshot = ChangeSnapshot(self._timestamp(), current, state_fingerprint(current))
        plan.status = PlanStatus.APPLYING
        plan.apply_request_id = current_request_id()
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self._timestamp()
        self._record(plan, "external_approval_consumed", "success")
        self._record(plan, "change_apply_started", "success")
        try:
            await self.gateway.write(plan.target_id, plan.proposed_config)
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.FAILED
            plan.failure_information = {"error_code": ErrorCode.AUTOMATION_APPLY_FAILED.value}
            self._record(plan, "change_apply_failed", "failure", error_code=ErrorCode.AUTOMATION_APPLY_FAILED.value)
            raise GovernanceError(ErrorCode.AUTOMATION_APPLY_FAILED) from exc

        duration = round((time.perf_counter() - started) * 1000, 3)
        actual_fingerprint = state_fingerprint(actual)
        desired_normalized = normalize_automation(plan.proposed_config) or {}
        mismatch = _mismatch_fields(desired_normalized, normalize_automation(actual) or {})
        if actual is None:
            mismatch.append("automation_existence")
        elif _automation_id_mismatch(plan.target_id, actual):
            mismatch.append("automation_id")
        config_check = await self._config_check()
        plan.verification = ChangeVerification(
            status="passed" if not mismatch and config_check == "valid" else "failed",
            checked_at=self._timestamp(),
            desired_fingerprint=plan.proposed_config_hash,
            actual_fingerprint=actual_fingerprint,
            config_check_status=config_check,
            mismatch_fields=mismatch,
            duration_ms=duration,
        )
        plan.post_apply_fingerprint = actual_fingerprint
        plan.rollback.available = plan.operation == ChangeOperation.UPDATE_AUTOMATION
        plan.rollback.status = "available" if plan.rollback.available else "unavailable_for_create"
        if plan.verification.status != "passed":
            plan.status = PlanStatus.VERIFICATION_FAILED
            plan.failure_information = {"error_code": ErrorCode.AUTOMATION_VERIFICATION_FAILED.value}
            self._record(plan, "change_verification_failed", "failure", error_code=ErrorCode.AUTOMATION_VERIFICATION_FAILED.value, duration_ms=duration)
            raise GovernanceError(
                ErrorCode.AUTOMATION_VERIFICATION_FAILED,
                details={"resource_id": plan.plan_id, "mismatch_fields": mismatch},
            )
        plan.status = PlanStatus.APPLIED
        plan.applied_at = self._timestamp()
        from ..dependency import DEPENDENCY_ANALYSIS
        DEPENDENCY_ANALYSIS.invalidate()
        self._record(plan, "change_apply_succeeded", "success", duration_ms=duration)
        return {
            "status": "applied",
            "hash_validation": hash_validation,
            "plan": self._public(plan, include_configs=False),
        }

    def _reject_apply(
        self,
        plan: ChangePlan,
        code: ErrorCode,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._record(
            plan,
            "change_apply_rejected",
            "rejected",
            error_code=code.value,
        )
        raise GovernanceError(code, details=details)

    def _reject_identity_mismatch(self, plan: ChangePlan) -> None:
        self._record(
            plan,
            "change_apply_rejected",
            "rejected",
            error_code=ErrorCode.AUTOMATION_VERIFICATION_FAILED.value,
        )
        raise GovernanceError(
            ErrorCode.AUTOMATION_VERIFICATION_FAILED,
            details={
                "resource_id": plan.plan_id,
                "mismatch_fields": ["automation_id"],
            },
        )

    def _valid_external_approval(self, plan: ChangePlan, approval_kind: str) -> bool:
        approval = plan.approval
        try:
            unexpired = bool(
                approval.approval_expires_at
                and self.now() < datetime.fromisoformat(approval.approval_expires_at)
            )
        except ValueError:
            unexpired = False
        return bool(
            plan.status
            == (PlanStatus.APPROVED if approval_kind == "apply" else PlanStatus.ROLLBACK_PENDING)
            and approval.state == ApprovalState.APPROVED
            and approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and approval.channel == APPROVAL_CHANNEL
            and approval.approval_kind == approval_kind
            and approval.principal_separation_enforced
            and approval.approver_principal
            and approval.bound_plan_hash == self.plan_hash(plan)
            and unexpired
        )

    def _require_current_normalization(self, plan: ChangePlan) -> None:
        if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
            self._require_v2_persisted_plan_safe(plan)
            if (
                plan.contract_version != CONFIGURATION_PLAN_CONTRACT_VERSION
                or plan.operation != ChangeOperation.CONFIGURATION_PLAN
                or not 1 <= len(plan.operations) <= MAX_CONFIGURATION_OPERATIONS
            ):
                raise GovernanceError(
                    ErrorCode.APPROVAL_HASH_MISMATCH,
                    details={
                        "resource_id": plan.plan_id,
                        "reason": "configuration_plan_contract_mismatch",
                    },
                )
            seen_ids: set[str] = set()
            seen_targets: set[tuple[str, str]] = set()
            for expected_order, operation in enumerate(
                sorted(plan.operations, key=lambda item: item.order)
            ):
                resource_type = ChangeGovernanceService._resolved_resource_type(
                    operation.resource_type, operation.helper_type
                )
                valid, _, _ = validate_resource(
                    resource_type,
                    operation.target_id,
                    operation.proposed_config,
                    self.sensitive_values,
                )
                target_key = (resource_type, operation.target_id)
                if (
                    not valid
                    or operation.order != expected_order
                    or not operation.operation_id
                    or operation.operation_id in seen_ids
                    or target_key in seen_targets
                    or any(
                        dependency not in seen_ids
                        for dependency in operation.depends_on
                    )
                    or operation.action
                    not in SUPPORTED_CONFIGURATION_ACTIONS
                    or operation.normalization_version
                    != RESOURCE_NORMALIZATION_VERSION
                    or normalize_resource_config(
                        resource_type, operation.proposed_config
                    )
                    != operation.normalized_proposed_config
                    or normalize_resource_config(
                        resource_type, operation.current_config
                    )
                    != operation.normalized_current_config
                    or stable_hash(operation.normalized_proposed_config)
                    != operation.proposed_config_hash
                    or resource_fingerprint(
                        resource_type, operation.current_config
                    )
                    != operation.current_state_fingerprint
                ):
                    raise GovernanceError(
                        ErrorCode.APPROVAL_HASH_MISMATCH,
                        details={
                            "resource_id": plan.plan_id,
                            "operation_id": operation.operation_id,
                            "reason": "configuration_normalization_mismatch",
                        },
                    )
                seen_ids.add(operation.operation_id)
                seen_targets.add(target_key)
            return

        proposed_hash = stable_hash(normalize_automation(plan.proposed_config) or {})
        current_fingerprint = state_fingerprint(plan.current_config)
        if (
            plan.normalization_version != AUTOMATION_NORMALIZATION_VERSION
            or proposed_hash != plan.proposed_config_hash
            or current_fingerprint != plan.current_state_fingerprint
        ):
            raise GovernanceError(
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={
                    "resource_id": plan.plan_id,
                    "reason": "normalization_version_mismatch",
                },
            )

    async def _config_check_with_details(
        self,
    ) -> tuple[str, dict[str, Any]]:
        try:
            result = await self._validate_all_configuration()
        except Exception as exc:
            return (
                "failed",
                {
                    "response_type": "exception",
                    "result_present": False,
                    "result": None,
                    "errors_present": False,
                    "errors": None,
                    "reason": "configuration_check_unavailable",
                    "failure_category": type(exc).__name__,
                },
            )

        is_object = isinstance(result, dict)
        result_present = is_object and "result" in result
        errors_present = is_object and "errors" in result
        raw_result = result.get("result") if is_object else result
        raw_errors = result.get("errors") if is_object else None
        safe_result = sanitize_untrusted_data(
            raw_result,
            known_secrets=self.sensitive_values,
            max_string=2048,
        ).value
        safe_errors = sanitize_untrusted_data(
            raw_errors,
            known_secrets=self.sensitive_values,
            max_string=2048,
        ).value

        if not is_object:
            reason = "malformed_response"
        elif not result_present:
            reason = "missing_result"
        elif raw_result != "valid":
            reason = "configuration_invalid"
        elif not errors_present:
            reason = "missing_errors"
        elif raw_errors is not None:
            reason = "configuration_errors_present"
        else:
            reason = "explicit_valid_result"

        details = {
            "response_type": type(result).__name__,
            "result_present": result_present,
            "result": safe_result,
            "errors_present": errors_present,
            "errors": safe_errors,
            "reason": reason,
        }
        if reason == "explicit_valid_result":
            return "valid", details
        return "failed", details

    async def _config_check(self) -> str:
        # Contract-v1 compatibility path. Historical automation plans accepted
        # the original Home Assistant response variants; contract-v2 callers
        # use the separate strict _config_check_with_details parser.
        try:
            result = await self.gateway.validate()
        except Exception:
            return "failed"
        if isinstance(result, dict):
            if result.get("errors"):
                return "failed"
            return (
                "valid"
                if result.get("result", "valid") == "valid"
                else "failed"
            )
        return (
            "valid"
            if str(result).lower() in {"valid", "ok", "none"}
            else "failed"
        )

    async def rollback_change(self, plan_id: str, expected_plan_hash: str = "") -> dict[str, Any]:
        plan_lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with plan_lock:
            plan = self._load(plan_id)
            self._resolve_lifecycle(plan)
            if plan.status == PlanStatus.EXPIRED:
                raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
            if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION:
                # Dev14 ordered plans persist per-step snapshots and receipts
                # for diagnosis, but batch rollback is deliberately unavailable.
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.operation == ChangeOperation.CREATE_AUTOMATION or not plan.snapshot:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.status in {PlanStatus.APPLIED, PlanStatus.VERIFICATION_FAILED}:
                plan.plan_version += 1
                plan.status = PlanStatus.ROLLBACK_PENDING
                plan.rollback.available = True
                plan.rollback.status = "awaiting_approval"
                plan.rollback.requested_at = self._timestamp()
                plan.rollback.expected_current_fingerprint = plan.post_apply_fingerprint
                plan.approval = ChangeApproval(
                    authority_version=APPROVAL_AUTHORITY_VERSION,
                    approval_kind="rollback",
                )
                self._record(plan, "rollback_requested", "success")
                return {
                    "status": "rollback_pending",
                    "plan_id": plan.plan_id,
                    "approval_required": True,
                    "plan_hash": self.plan_hash(plan),
                }
            if plan.status != PlanStatus.ROLLBACK_PENDING:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
            if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
                raise GovernanceError(ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
            calculated = self.plan_hash(plan)
            hash_validation = (
                {"performed": True, "result": "matched"}
                if expected_plan_hash
                else {"performed": False, "reason": "not_supplied"}
            )
            if expected_plan_hash and expected_plan_hash != calculated:
                self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.APPROVAL_HASH_MISMATCH.value)
                raise GovernanceError(
                    ErrorCode.APPROVAL_HASH_MISMATCH,
                    details={"hash_validation": {"performed": True, "result": "mismatch"}},
                )
            if not self._valid_external_approval(plan, "rollback"):
                self._record(
                    plan,
                    "rollback_failed",
                    "rejected",
                    error_code=ErrorCode.EXTERNAL_APPROVAL_REQUIRED.value,
                )
                raise GovernanceError(
                    ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
                    details={"hash_validation": hash_validation},
                )
            if not expected_plan_hash or plan.approval.bound_plan_hash != calculated:
                self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.APPROVAL_HASH_MISMATCH.value)
                raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
            legacy_target_lock = self._target_locks.get(plan.target_id)
            target_lock = self._target_locks.setdefault(
                ("automation", plan.target_id),
                legacy_target_lock or asyncio.Lock(),
            )
            if target_lock.locked():
                self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.CHANGE_IN_PROGRESS.value)
                raise GovernanceError(ErrorCode.CHANGE_IN_PROGRESS)
            async with target_lock:
                return await self._rollback_locked(plan)

    async def _rollback_locked(self, plan: ChangePlan) -> dict[str, Any]:
        current = await self.gateway.get(plan.target_id)
        if state_fingerprint(current) != plan.rollback.expected_current_fingerprint:
            self._record(plan, "rollback_failed", "rejected", error_code=ErrorCode.STALE_TARGET_STATE.value)
            raise GovernanceError(ErrorCode.STALE_TARGET_STATE)
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = self._timestamp()
        self._record(plan, "external_approval_consumed", "success")
        plan.rollback.status = "applying"
        plan.rollback.request_id = current_request_id()
        self._record(plan, "rollback_started", "success")
        if _automation_id_mismatch(plan.target_id, plan.snapshot.config):
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "verification_failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED)
        try:
            await self.gateway.write(plan.target_id, plan.snapshot.config or {})
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED) from exc
        if (
            actual is None
            or _automation_id_mismatch(plan.target_id, actual)
            or state_fingerprint(actual) != plan.snapshot.fingerprint
            or await self._config_check() != "valid"
        ):
            plan.status = PlanStatus.ROLLBACK_FAILED
            plan.rollback.status = "verification_failed"
            plan.rollback.failure_code = ErrorCode.ROLLBACK_FAILED.value
            self._record(plan, "rollback_failed", "failure", error_code=ErrorCode.ROLLBACK_FAILED.value)
            raise GovernanceError(ErrorCode.ROLLBACK_FAILED)
        plan.status = PlanStatus.ROLLED_BACK
        plan.rollback.status = "rolled_back"
        plan.rollback.rolled_back_at = self._timestamp()
        from ..dependency import DEPENDENCY_ANALYSIS
        DEPENDENCY_ANALYSIS.invalidate()
        self._record(plan, "rollback_succeeded", "success")
        return {"status": "rolled_back", "plan": self._public(plan, include_configs=False)}

    def health_summary(self) -> dict[str, Any]:
        plans = self.resolved_plans()
        storage = self.repository.health()
        events = [event.event for plan in plans for event in plan.events]
        approval_failures = sorted(
            (
                event
                for plan in plans
                for event in plan.events
                if event.event.startswith("external_approval") and event.error_code
            ),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        return {
            "enabled": True,
            "storage": storage,
            "storage_status": storage["status"],
            "storage_corruption_count": storage["corruption_count"],
            "total_plans": len(plans),
            "plans_awaiting_approval": sum(plan.status == PlanStatus.AWAITING_APPROVAL for plan in plans),
            "plans_requiring_approval": sum(
                plan.status in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}
                and plan.approval.state in {ApprovalState.REQUIRED, ApprovalState.EXTERNAL_PENDING}
                for plan in plans
            ),
            "external_approval_enabled": True,
            "ingress_approval_ui_configured": True,
            "approval_authority_version": APPROVAL_AUTHORITY_VERSION,
            "pending_challenge_count": sum(
                plan.approval.state == ApprovalState.EXTERNAL_PENDING
                and self._active_challenge_matches(plan, self.plan_hash(plan))
                for plan in plans
            ),
            "plans_with_pending_external_challenge": sum(
                plan.approval.state == ApprovalState.EXTERNAL_PENDING
                and self._active_challenge_matches(plan, self.plan_hash(plan))
                for plan in plans
            ),
            "externally_approved_plans": sum(
                plan.approval.state == ApprovalState.APPROVED for plan in plans
            ),
            "granted_approval_count": events.count("external_approval_granted"),
            "rejected_approval_count": events.count("external_approval_rejected"),
            "expired_challenge_count": events.count("external_approval_expired"),
            "invalidated_challenge_count": events.count("external_approval_invalidated"),
            "approval_consumption_count": events.count("external_approval_consumed"),
            "last_approval_failure_category": (
                approval_failures[0].error_code if approval_failures else None
            ),
            "rejected_plans": sum(plan.status == PlanStatus.REJECTED for plan in plans),
            "expired_plans": sum(plan.status == PlanStatus.EXPIRED for plan in plans),
            "active_apply_operations": sum(lock.locked() for lock in self._target_locks.values()),
            "failed_apply_count": sum(
                (plan.failure_information or {}).get("error_code")
                in {
                    ErrorCode.AUTOMATION_APPLY_FAILED.value,
                    ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                    ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
                    ErrorCode.CONFIGURATION_VERIFICATION_FAILED.value,
                }
                for plan in plans
            ),
            "rollback_pending_count": sum(plan.status == PlanStatus.ROLLBACK_PENDING for plan in plans),
            "last_successful_change_at": next(
                (plan.applied_at for plan in sorted(plans, key=lambda item: item.applied_at or "", reverse=True) if plan.applied_at),
                None,
            ),
        }


def _mismatch_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return [
        item["field"]
        for item in structured_diff(expected, actual)["changed_fields"]
    ]


def _automation_id_mismatch(
    expected_automation_id: str, config: dict[str, Any] | None
) -> bool:
    """Check identity metadata independently from behavioral normalization."""

    return bool(
        isinstance(config, dict)
        and config.get("id") is not None
        and str(config["id"]) != expected_automation_id
    )
