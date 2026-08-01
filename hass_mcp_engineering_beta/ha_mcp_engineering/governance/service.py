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
from typing import Any, Awaitable, Callable
import uuid

from ..audit import AuditLogger
from ..clients.rest import ExpectedHttpStatus, HomeAssistantRestClient
from ..errors import (
    EngineeringServerError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ..logging_config import get_logger, log_event
from ..observability import METRICS
from ..request_context import (
    REQUEST_ID_PATTERN,
    current_caller_id,
    current_request_id,
)
from ..sanitization import sanitize_untrusted_data
from .models import (
    ApprovalActionKind,
    ApprovalActionRecord,
    ApprovalPolicyClass,
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
    OperationalPlanDetails,
    PlanStatus,
    RecoveryVerification,
    RiskLevel,
    StepExecutionStatus,
)
from .config_validation import normalize_configuration_validation
from .normalize import (
    AUTOMATION_NORMALIZATION_VERSION,
    normalize_automation,
    stable_hash,
    state_fingerprint,
    structured_diff,
)
from .risk import classify_risk
from .policy import evaluate_change_policy, policy_snapshot_matches
from .resources import (
    ConfigurationMutationCompletedUnexpectedlyError,
    ConfigurationMutationNotDispatchedError,
    RESOURCE_NORMALIZATION_VERSION,
    ResourceVerificationComparison,
    compare_resource_verification,
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
from .task_models import (
    ExecutionTask,
    ExecutionTaskState,
    TERMINAL_TASK_STATES,
    new_execution_task,
    parse_task_timestamp,
)
from .task_storage import (
    ExecutionTaskRepository,
    ExecutionTaskStorageError,
)
from .operational import (
    BackupAdministrationGateway,
    OperationalGatewayError,
    normalize_backup_name,
)
from .operational_lifecycle import (
    LifecycleGatewayError,
    OperationalLifecycleGateway,
    RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS,
    RELOAD_SERVICES,
)
from .validation import sanitize_context, validate_automation


APPROVAL_AUTHORITY_VERSION = 3
APPROVAL_CHANNEL = "home_assistant_ingress"
APPROVAL_CHALLENGE_TTL = timedelta(minutes=60)
DEFAULT_APPROVER_PRINCIPAL = "home_assistant_admin_ingress"
CONFIGURATION_PLAN_CONTRACT_VERSION = 2
OPERATIONAL_PLAN_CONTRACT_VERSION = 3
LIFECYCLE_OPERATIONS = frozenset(
    {
        ChangeOperation.CONTROLLED_RELOAD,
        ChangeOperation.RESTART_ADDON,
        ChangeOperation.RESTART_HOME_ASSISTANT,
    }
)
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
AUTOMATION_PROVIDER_RESPONSE_EVENTS = frozenset(
    {
        "automation_provider_completed",
        "automation_provider_failed",
    }
)
PROVIDER_RESPONSE_EVIDENCE_INCONSISTENT = (
    "provider_response_evidence_inconsistent"
)


def _usable_invocation_request_id(value: object) -> str | None:
    if not isinstance(value, str) or not REQUEST_ID_PATTERN.fullmatch(value):
        return None
    return value


def _reconciled_persisted_invocation_count(
    plan: ChangePlan,
    task: ExecutionTask | None,
    *,
    plan_event_matches: Callable[[ChangeEvent], bool],
    task_event_types: frozenset[str],
) -> int:
    """Count distinct persisted invocations across legacy and task eras."""

    plan_events = [
        event for event in plan.events if plan_event_matches(event)
    ]
    if task is None:
        return len(plan_events)

    request_ids: set[str] = set()
    anonymous_task_events: set[tuple[int, str]] = set()
    for event in task.events:
        if event.event_type not in task_event_types:
            continue
        request_id = _usable_invocation_request_id(event.request_id)
        if request_id is not None:
            request_ids.add(request_id)
        else:
            anonymous_task_events.add((event.sequence, event.event_type))

    try:
        task_created_at = parse_task_timestamp(task.created_at)
    except (TypeError, ValueError):
        task_created_at = None

    anonymous_legacy_events = 0
    for event in plan_events:
        request_id = _usable_invocation_request_id(event.request_id)
        if request_id is not None:
            request_ids.add(request_id)
            continue
        if task_created_at is None:
            continue
        try:
            event_timestamp = parse_task_timestamp(event.timestamp)
        except (TypeError, ValueError):
            continue
        if event_timestamp < task_created_at:
            anonymous_legacy_events += 1

    return (
        len(request_ids)
        + len(anonymous_task_events)
        + anonymous_legacy_events
    )


MAX_APPROVAL_PROJECTION_ACTIONS_PER_PLAN = 32
MAX_APPROVAL_PROJECTION_DETAILS_PER_PLAN = 128
MAX_OPERATIONAL_RECONCILIATIONS_PER_PASS = 20
OPERATIONAL_RECONCILIATION_TIME_BUDGET_SECONDS = 10.0
EXECUTION_TASK_POST_DISPATCH_DEADLINE = timedelta(hours=24)
HOME_ASSISTANT_OUTAGE_CATEGORIES = frozenset(
    {"provider_timeout", "provider_unavailable"}
)
HOME_ASSISTANT_RESTART_EVIDENCE_SOURCES = frozenset(
    {
        "home_assistant_core_connection_probe",
        "home_assistant_core_reconnected",
    }
)
# Eight is the practical cardinality limit for the closed evidence-source set.
MAX_RESTART_EVIDENCE_SOURCES = 8
# The larger count bound is defensive against malformed persisted records.
MAX_RESTART_OUTAGE_OBSERVATIONS = 10_000

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


def _parse_governance_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _earliest_governance_timestamp(
    left: Any, right: str
) -> str:
    left_parsed = _parse_governance_timestamp(left)
    right_parsed = _parse_governance_timestamp(right)
    if left_parsed is None:
        return right
    if right_parsed is None or left_parsed <= right_parsed:
        return str(left)
    return right


def _latest_governance_timestamp(left: Any, right: str) -> str:
    left_parsed = _parse_governance_timestamp(left)
    right_parsed = _parse_governance_timestamp(right)
    if left_parsed is None:
        return right
    if right_parsed is None or left_parsed >= right_parsed:
        return str(left)
    return right


def _bounded_restart_evidence_sources(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:MAX_RESTART_EVIDENCE_SOURCES]:
        if (
            isinstance(item, str)
            and item in HOME_ASSISTANT_RESTART_EVIDENCE_SOURCES
            and item not in result
        ):
            result.append(item)
    return result


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
        operational_gateway: BackupAdministrationGateway | Any | None = None,
        lifecycle_gateway: OperationalLifecycleGateway | Any | None = None,
        task_repository: ExecutionTaskRepository | None = None,
    ):
        self.repository = repository
        self.gateway = gateway
        self.audit = audit
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sensitive_values = tuple(value for value in sensitive_values if value)
        self.operational_gateway = operational_gateway
        self.lifecycle_gateway = lifecycle_gateway
        self.task_repository = task_repository or ExecutionTaskRepository(
            repository.root,
            retention_days=repository.retention_days,
        )
        self.logger = get_logger("governance")
        self._plan_locks: dict[str, asyncio.Lock] = {}
        self._target_locks: dict[object, asyncio.Lock] = {}
        self._active_lifecycle_reconciliations = 0
        self._active_task_ids_by_plan: dict[str, str] = {}
        self._task_reconciliation_runs = 0
        self.repository.cleanup(now=self.now())
        self.repository.recover_incomplete(self._timestamp())
        self.task_repository.cleanup(now=self.now())

    def _timestamp(self) -> str:
        return self.now().isoformat()

    def _new_id(self) -> str:
        while True:
            candidate = uuid.uuid4().hex
            if self.repository.get(candidate) is None:
                return candidate

    @staticmethod
    def plan_hash(plan: ChangePlan) -> str:
        if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
            operational = plan.operational
            if operational is None:
                return stable_hash({"invalid_operational_plan": plan.plan_id})
            immutable = {
                "contract_version": plan.contract_version,
                "plan_family": plan.plan_family,
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "operation": plan.operation.value,
                "target_type": plan.target_type,
                "target_id": plan.target_id,
                "expires_at": plan.expires_at,
                "requested_name": operational.requested_name,
                "provider": operational.provider,
                "provider_capability_evidence": (
                    operational.provider_capability_evidence
                ),
                "expected_effects": operational.expected_effects,
                "preconditions": operational.preconditions,
                "verification_contract": operational.verification_contract,
                "baseline": operational.baseline,
                "limitations": operational.limitations,
                "rollback_available": operational.rollback_available,
                "risk_level": plan.risk.level.value,
                "risk_apply_allowed": plan.risk.apply_allowed,
                "approval_kind": plan.approval.approval_kind,
                "approval_authority_version": plan.approval.authority_version,
            }
            if (
                plan.approval.authority_version
                >= APPROVAL_AUTHORITY_VERSION
                and plan.policy_decision is not None
            ):
                immutable["policy_decision"] = (
                    plan.policy_decision.to_dict()
                )
            return stable_hash(immutable)
        if plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION:
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
            if plan.policy_decision is not None:
                immutable["policy_decision"] = (
                    plan.policy_decision.to_dict()
                )
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
        if plan.approval.authority_version >= 2:
            immutable["approval_authority_version"] = plan.approval.authority_version
        if (
            plan.approval.authority_version
            >= APPROVAL_AUTHORITY_VERSION
            and plan.policy_decision is not None
        ):
            immutable["policy_decision"] = plan.policy_decision.to_dict()
        return stable_hash(immutable)

    @staticmethod
    def _bind_new_plan_policy(plan: ChangePlan) -> None:
        decision = plan.policy_decision
        if decision is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        plan.approval.authority_version = APPROVAL_AUTHORITY_VERSION
        plan.approval.policy_decision_hash = (
            decision.policy_decision_hash
        )
        plan.approval.policy_class = decision.policy_class.value
        plan.approval.bundle_state = (
            "prohibited"
            if decision.policy_class == ApprovalPolicyClass.PROHIBITED
            else "pending_plan_approval"
        )
        plan.approval.same_principal_confirmed = None
        plan.approval.elevated_risk_acknowledgement = (
            ApprovalActionRecord(
                kind=(
                    ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
                )
            )
            if decision.policy_class
            == ApprovalPolicyClass.ELEVATED_ADMIN
            else None
        )

    def _require_policy_snapshot(self, plan: ChangePlan) -> None:
        if plan.policy_decision is None:
            raise GovernanceError(
                ErrorCode.POLICY_SNAPSHOT_REQUIRED,
                details={"resource_id": plan.plan_id},
            )
        if not policy_snapshot_matches(plan):
            METRICS.record_classified_outcome(
                ErrorCode.POLICY_SNAPSHOT_MISMATCH.value
            )
            raise GovernanceError(
                ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                details={"resource_id": plan.plan_id},
            )
        bundle_error = self._approval_bundle_integrity_error(plan)
        if bundle_error is not None:
            METRICS.record_classified_outcome(bundle_error.value)
            raise GovernanceError(
                bundle_error,
                details={"resource_id": plan.plan_id},
            )

    def _approval_bundle_integrity_error(
        self,
        plan: ChangePlan,
    ) -> ErrorCode | None:
        """Validate persisted authority-v3 state without upgrading legacy data."""

        decision = plan.policy_decision
        if decision is None:
            return None
        approval = plan.approval
        if approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            return ErrorCode.APPROVAL_AUTHORITY_MISMATCH
        if (
            approval.policy_decision_hash
            != decision.policy_decision_hash
            or approval.policy_class != decision.policy_class.value
        ):
            return ErrorCode.POLICY_SNAPSHOT_MISMATCH

        acknowledgement = approval.elevated_risk_acknowledgement
        if decision.policy_class == ApprovalPolicyClass.PROHIBITED:
            return (
                None
                if self._is_effectively_prohibited_plan(
                    plan, policy_snapshot_validated=True
                )
                else ErrorCode.APPROVAL_SEQUENCE_FAILURE
            )
        if decision.policy_class == ApprovalPolicyClass.STANDARD_ADMIN:
            if acknowledgement is not None:
                return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        elif (
            acknowledgement is None
            or acknowledgement.kind
            != ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE

        expected_state_pairs: dict[
            str, tuple[ApprovalState, ApprovalState | None]
        ] = {
            "pending_plan_approval": (
                approval.state,
                (
                    acknowledgement.state
                    if acknowledgement is not None
                    else None
                ),
            ),
            "pending_elevated_risk_acknowledgement": (
                ApprovalState.APPROVED,
                ApprovalState.EXTERNAL_PENDING,
            ),
            "fully_approved": (
                ApprovalState.APPROVED,
                (
                    ApprovalState.APPROVED
                    if acknowledgement is not None
                    else None
                ),
            ),
            "consumed": (
                ApprovalState.CONSUMED,
                (
                    ApprovalState.CONSUMED
                    if acknowledgement is not None
                    else None
                ),
            ),
            "rejected": (
                ApprovalState.REJECTED,
                (
                    ApprovalState.REJECTED
                    if acknowledgement is not None
                    else None
                ),
            ),
            "expired": (
                ApprovalState.EXPIRED,
                (
                    ApprovalState.EXPIRED
                    if acknowledgement is not None
                    else None
                ),
            ),
            "invalidated": (
                ApprovalState.INVALIDATED,
                (
                    ApprovalState.INVALIDATED
                    if acknowledgement is not None
                    else None
                ),
            ),
        }
        bundle_state = approval.bundle_state or ""
        if bundle_state not in expected_state_pairs:
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        top_state, acknowledgement_state = expected_state_pairs[bundle_state]
        if bundle_state == "pending_plan_approval":
            if approval.state not in {
                ApprovalState.REQUIRED,
                ApprovalState.EXTERNAL_PENDING,
            }:
                return ErrorCode.APPROVAL_SEQUENCE_FAILURE
            if acknowledgement is not None and acknowledgement.state != (
                ApprovalState.REQUIRED
            ):
                return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        elif approval.state != top_state or (
            acknowledgement is not None
            and acknowledgement.state != acknowledgement_state
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE

        if approval.state in {
            ApprovalState.EXTERNAL_PENDING,
            ApprovalState.APPROVED,
            ApprovalState.CONSUMED,
        } and (
            approval.channel != APPROVAL_CHANNEL
            or approval.bound_plan_hash
            != ChangeGovernanceService.plan_hash(plan)
            or not approval.challenge_id
            or not approval.challenge_expires_at
            or approval.challenge_plan_version != plan.plan_version
            or approval.challenge_target_type != plan.target_type
            or approval.challenge_target_id != plan.target_id
            or approval.challenge_operation != plan.operation.value
            or approval.challenge_risk_level != plan.risk.level.value
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        if bundle_state in {"fully_approved", "consumed"} and (
            not approval.approver_principal
            or not approval.approved_at
            or not approval.approval_expires_at
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        if acknowledgement is not None and acknowledgement.state in {
            ApprovalState.EXTERNAL_PENDING,
            ApprovalState.APPROVED,
            ApprovalState.CONSUMED,
        } and (
            not acknowledgement.challenge_id
            or not acknowledgement.challenge_expires_at
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        if acknowledgement is not None and acknowledgement.challenge_id and (
            acknowledgement.authority_version
            != APPROVAL_AUTHORITY_VERSION
            or acknowledgement.bound_plan_hash
            != ChangeGovernanceService.plan_hash(plan)
            or acknowledgement.policy_decision_hash
            != decision.policy_decision_hash
            or acknowledgement.policy_class
            != decision.policy_class.value
            or acknowledgement.risk_delta != decision.risk_delta.value
            or acknowledgement.physical_consequence
            != decision.physical_consequence.value
        ):
            return ErrorCode.APPROVAL_SEQUENCE_FAILURE
        if bundle_state in {"fully_approved", "consumed"} and (
            acknowledgement is not None
            and (
                not acknowledgement.granted_at
                or acknowledgement.approver_principal
                != approval.approver_principal
                or approval.same_principal_confirmed is not True
            )
        ):
            return ErrorCode.APPROVAL_PRINCIPAL_MISMATCH
        return None

    @staticmethod
    def _prohibited_approval_has_authority_evidence(plan: ChangePlan) -> bool:
        """Return whether a prohibited record contains actionable authority."""

        approval = plan.approval
        return bool(
            approval.channel is not None
            or approval.approver_principal is not None
            or approval.principal_separation_enforced is not None
            or approval.approved_at is not None
            or approval.approving_caller_id is not None
            or approval.approval_note is not None
            or approval.bound_plan_hash is not None
            or approval.consumed_at is not None
            or approval.approval_expires_at is not None
            or approval.challenge_id is not None
            or approval.challenge_requested_at is not None
            or approval.challenge_expires_at is not None
            or approval.challenge_plan_version is not None
            or approval.challenge_target_type is not None
            or approval.challenge_target_id is not None
            or approval.challenge_operation is not None
            or approval.challenge_risk_level is not None
            or approval.request_note is not None
            or approval.csrf_digest is not None
            or approval.csrf_issued_at is not None
            or approval.same_principal_confirmed is not None
            or approval.elevated_risk_acknowledgement is not None
        )

    @staticmethod
    def _prohibited_plan_has_execution_evidence(plan: ChangePlan) -> bool:
        """Reject compatibility when any dispatch or successful work is present."""

        if (
            plan.applied_at is not None
            or plan.apply_request_id is not None
            or plan.post_apply_fingerprint is not None
            or plan.snapshot is not None
            or plan.failure_information is not None
            or plan.verification.status != "not_run"
            or plan.verification.checked_at is not None
            or plan.rollback.requested_at is not None
            or plan.rollback.approved_at is not None
            or plan.rollback.rolled_back_at is not None
            or plan.rollback.request_id is not None
            or plan.rollback.failure_code is not None
            or plan.execution_outcome
            not in {None, "not_started", "not_applied"}
        ):
            return True
        if any(
            operation.execution_status != StepExecutionStatus.PENDING
            or operation.execution_receipt is not None
            or operation.post_apply_fingerprint is not None
            or operation.failure_information is not None
            or operation.verification.status != "not_run"
            for operation in plan.operations
        ):
            return True
        if plan.operational is not None:
            dispatch = plan.operational.dispatch
            if (
                bool(dispatch.get("dispatched"))
                or dispatch.get("attempt_count") not in {None, 0}
                or dispatch.get("attempted_at") is not None
                or dispatch.get("provider_operation_id") is not None
                or dispatch.get("provider_response_received") is True
                or plan.operational.final_outcome is not None
                or plan.operational.verification.status != "not_run"
                or plan.operational.verification.attempt_count != 0
            ):
                return True
        allowed_events = {
            "change_plan_created": None,
            "policy_approval_rejected": ErrorCode.PROHIBITED_CHANGE.value,
            "change_apply_rejected": ErrorCode.PROHIBITED_CHANGE.value,
            "change_plan_superseded": None,
        }
        for event in plan.events:
            if event.event not in allowed_events:
                return True
            required_code = allowed_events[event.event]
            if required_code is not None and event.error_code != required_code:
                return True
        return False

    def _is_effectively_prohibited_plan(
        self,
        plan: ChangePlan,
        *,
        policy_snapshot_validated: bool = False,
    ) -> bool:
        """Recognize current and exact safe Beta 6 prohibited records.

        Beta 6 could supersede a prohibited contract-v1 plan when a later plan
        targeted the same automation. That transition changed only the legacy
        lifecycle fields to ``superseded``/``invalidated``. The immutable F2
        policy snapshot remained prohibited, with no acknowledgement,
        challenge, task, dispatch, apply, or rollback authority. Beta 8 treats
        that source-established shape as terminal without rewriting it.
        """

        decision = plan.policy_decision
        approval = plan.approval
        if (
            decision is None
            or decision.policy_class != ApprovalPolicyClass.PROHIBITED
            or decision.required_acknowledgements
            or plan.risk.apply_allowed
            or approval.authority_version != APPROVAL_AUTHORITY_VERSION
            or approval.policy_decision_hash
            != decision.policy_decision_hash
            or approval.policy_class != decision.policy_class.value
            or approval.approval_kind != "apply"
            or (
                not policy_snapshot_validated
                and not policy_snapshot_matches(plan)
            )
            or self._prohibited_approval_has_authority_evidence(plan)
            or self._prohibited_plan_has_execution_evidence(plan)
        ):
            return False
        try:
            if self.task_repository.get_for_plan(plan.plan_id) is not None:
                return False
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc

        current = bool(
            approval.bundle_state == "prohibited"
            and approval.state == ApprovalState.REQUIRED
            and plan.status == PlanStatus.AWAITING_APPROVAL
        )
        beta6_superseded = bool(
            plan.contract_version < CONFIGURATION_PLAN_CONTRACT_VERSION
            and plan.status == PlanStatus.SUPERSEDED
            and approval.state == ApprovalState.INVALIDATED
            and approval.bundle_state == "invalidated"
            and any(
                event.event == "change_plan_superseded"
                for event in plan.events
            )
        )
        return current or beta6_superseded

    @staticmethod
    def _approval_bundle_state(plan: ChangePlan) -> str:
        approval = plan.approval
        if approval.bundle_state:
            return approval.bundle_state
        if approval.state == ApprovalState.CONSUMED:
            return "consumed"
        if approval.state == ApprovalState.REJECTED:
            return "rejected"
        if approval.state == ApprovalState.EXPIRED:
            return "expired"
        if approval.state == ApprovalState.INVALIDATED:
            return "invalidated"
        if approval.state == ApprovalState.APPROVED:
            return "fully_approved"
        return "pending_plan_approval"

    @staticmethod
    def _elevated_acknowledgement(
        plan: ChangePlan,
    ) -> ApprovalActionRecord | None:
        return plan.approval.elevated_risk_acknowledgement

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
        if plan.policy_decision is not None:
            self._require_policy_snapshot(plan)
        return plan

    def _load_task(self, task_id: str) -> ExecutionTask:
        try:
            task = self.task_repository.get(task_id)
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        if task is None:
            METRICS.record_classified_outcome("execution_task_not_found")
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_NOT_FOUND,
                details={"resource_id": task_id},
            )
        return task

    def _save_task(self, task: ExecutionTask) -> None:
        try:
            self.task_repository.save(task)
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc

    @staticmethod
    def _task_target(plan: ChangePlan) -> dict[str, Any]:
        return {
            "target_type": plan.target_type,
            "target_id": plan.target_id,
        }

    @staticmethod
    def _task_approval_reference(plan: ChangePlan) -> dict[str, Any]:
        """Persist bounded authority references without approval principals."""

        acknowledgement = (
            plan.approval.elevated_risk_acknowledgement
        )
        value = {
            "approval_kind": plan.approval.approval_kind,
            "authority_version": plan.approval.authority_version,
            "bound_plan_hash": plan.approval.bound_plan_hash,
            "approval_state": plan.approval.state.value,
            "challenge_id": plan.approval.challenge_id,
            "approval_expires_at": plan.approval.approval_expires_at,
            "policy_class": plan.approval.policy_class,
            "policy_decision_hash": (
                plan.approval.policy_decision_hash
            ),
            "approval_bundle_state": (
                ChangeGovernanceService._approval_bundle_state(plan)
            ),
            "same_principal_confirmed": (
                plan.approval.same_principal_confirmed
            ),
            "plan_approval": {
                "kind": ApprovalActionKind.PLAN_APPROVAL.value,
                "challenge_id": plan.approval.challenge_id,
                "state": plan.approval.state.value,
                "granted_at": plan.approval.approved_at,
                "consumed_at": plan.approval.consumed_at,
            },
        }
        if acknowledgement is not None:
            value["elevated_risk_acknowledgement"] = {
                "kind": acknowledgement.kind.value,
                "authority_version": acknowledgement.authority_version,
                "bound_plan_hash": acknowledgement.bound_plan_hash,
                "policy_decision_hash": (
                    acknowledgement.policy_decision_hash
                ),
                "policy_class": acknowledgement.policy_class,
                "risk_delta": acknowledgement.risk_delta,
                "physical_consequence": (
                    acknowledgement.physical_consequence
                ),
                "challenge_id": acknowledgement.challenge_id,
                "state": acknowledgement.state.value,
                "granted_at": acknowledgement.granted_at,
                "consumed_at": acknowledgement.consumed_at,
            }
        return value

    def _task_idempotency_key(
        self, plan: ChangePlan, plan_hash: str
    ) -> str:
        return stable_hash(
            {
                "task_schema_version": 1,
                "plan_id": plan.plan_id,
                "plan_hash": plan_hash,
                "operation": plan.operation.value,
                "target": self._task_target(plan),
                "execution_intent": "apply_change_plan",
            }
        )

    def _new_task_id(self) -> str:
        while True:
            candidate = uuid.uuid4().hex
            if self.task_repository.get(candidate) is None:
                return candidate

    def _task_audit(
        self,
        task: ExecutionTask,
        event_type: str,
        result_status: str,
        *,
        error_code: str | None = None,
    ) -> None:
        safe = {
            "event": event_type,
            "request_id": current_request_id(),
            "access": "write",
            "operation_class": "execution_task_lifecycle",
            "task_id": task.task_id,
            "plan_id": task.plan_id,
            "operation": task.operation,
            "target_type": task.target.get("target_type"),
            "target_id": task.target.get("target_id"),
            "task_state": task.state.value,
            "terminal_outcome": task.terminal_outcome,
            "result_status": result_status,
            "error_code": error_code,
            "provider_dispatch_occurred": bool(task.dispatched_at),
            "approval_consumed": self._task_approval_consumed(task),
            "approval_authority_version": (
                task.approval_reference.get("authority_version")
            ),
            "policy_class": task.approval_reference.get(
                "policy_class"
            ),
            "policy_decision_hash": task.approval_reference.get(
                "policy_decision_hash"
            ),
            "same_principal_requirement": (
                task.approval_reference.get("policy_class")
                == ApprovalPolicyClass.ELEVATED_ADMIN.value
            ),
            "fallback_occurred": False,
            "fallback": "none",
        }
        if self.audit:
            self.audit.write(safe)
        log_event(
            self.logger,
            (
                logging.INFO
                if result_status == "success"
                else logging.WARNING
            ),
            event_type,
            "Durable governed execution-task lifecycle event.",
            context=safe,
        )

    def _record_task_event(
        self,
        task: ExecutionTask,
        event_type: str,
        *,
        new_state: ExecutionTaskState | None = None,
        changes: dict[str, Any] | None = None,
        result_status: str = "success",
        error_code: str | None = None,
    ) -> None:
        try:
            task.append_event(
                event_type,
                self._timestamp(),
                new_state=new_state,
                changes=changes,
                request_id=current_request_id(),
            )
        except ValueError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_INVALID_STATE,
                details={
                    "task_id": task.task_id,
                    "task_state": task.state.value,
                },
            ) from exc
        self._save_task(task)
        self._task_audit(
            task,
            event_type,
            result_status,
            error_code=error_code,
        )

    def _create_task_for_plan(
        self, plan: ChangePlan, plan_hash: str
    ) -> ExecutionTask:
        timestamp = self._timestamp()
        task = new_execution_task(
            task_id=self._new_task_id(),
            plan_id=plan.plan_id,
            plan_hash=plan_hash,
            operation=plan.operation.value,
            target=self._task_target(plan),
            timestamp=timestamp,
            execution_request_id=current_request_id(),
            idempotency_key=self._task_idempotency_key(plan, plan_hash),
            approval_reference=self._task_approval_reference(plan),
            legacy_projection={
                "record_kind": "f1_execution_task",
                "plan_status": plan.status.value,
                "execution_outcome": plan.execution_outcome,
            },
        )
        self._save_task(task)
        self._task_audit(task, "task_created", "success")
        return task

    def _resolve_task_for_apply(
        self, plan: ChangePlan, expected_plan_hash: str
    ) -> tuple[ExecutionTask | None, bool]:
        calculated = self.plan_hash(plan)
        try:
            existing = self.task_repository.get_for_plan(plan.plan_id)
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        if existing is not None:
            if (
                existing.plan_hash != calculated
                or existing.idempotency_key
                != self._task_idempotency_key(plan, calculated)
            ):
                raise GovernanceError(
                    ErrorCode.EXECUTION_TASK_STORAGE_ERROR,
                    details={
                        "task_id": existing.task_id,
                        "reason": "task_plan_authority_mismatch",
                    },
                )
            return existing, True

        # Historical completed plans remain taskless legacy records. Invalid or
        # unapproved apply requests are rejected by the existing plan path
        # before they can acquire an execution opportunity.
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.APPLIED:
            return None, False
        if (
            not expected_plan_hash
            or expected_plan_hash != calculated
            or not self._valid_external_approval(plan, "apply")
        ):
            return None, False
        return self._create_task_for_plan(plan, calculated), False

    @staticmethod
    def _task_is_dispatched(task: ExecutionTask) -> bool:
        return bool(task.dispatched_at or task.provider_attempts)

    @staticmethod
    def _task_approval_consumed(task: ExecutionTask) -> bool:
        return task.approval_reference.get("approval_state") == (
            ApprovalState.CONSUMED.value
        )

    def _task_plan_projection(
        self, task: ExecutionTask
    ) -> dict[str, Any]:
        return {
            "record_kind": "f1_execution_task",
            "task_id": task.task_id,
            "task_state": task.state.value,
            "terminal_outcome": task.terminal_outcome,
            "updated_at": task.updated_at,
            "provider_dispatch_occurred": self._task_is_dispatched(task),
        }

    def _public_task(
        self, task: ExecutionTask, *, include_events: bool = True
    ) -> dict[str, Any]:
        value = task.to_dict()
        events = list(value.pop("events", []))
        value["provider_attempt_count"] = len(task.provider_attempts)
        value["event_count"] = len(events)
        if include_events:
            value["lifecycle_events"] = events[-100:]
            value["events_truncated"] = len(events) > 100
        return value

    def get_execution_task(self, task_id: str) -> dict[str, Any]:
        return self._public_task(self._load_task(task_id))

    def list_execution_tasks(
        self,
        *,
        state: str = "",
        terminal_outcome: str = "",
        plan_id: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        if state:
            try:
                ExecutionTaskState(state)
            except ValueError as exc:
                raise GovernanceError(ErrorCode.INVALID_REQUEST) from exc
        try:
            tasks = self.task_repository.list()
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        summaries = []
        for task in tasks:
            if state and task.state.value != state:
                continue
            if (
                terminal_outcome
                and task.terminal_outcome != terminal_outcome
            ):
                continue
            if plan_id and task.plan_id != plan_id:
                continue
            summaries.append(self._public_task(task, include_events=False))
            if len(summaries) >= max(1, min(int(limit), 100)):
                break
        return {"count": len(summaries), "tasks": summaries}

    async def cancel_execution_task(
        self, task_id: str
    ) -> dict[str, Any]:
        task = self._load_task(task_id)
        plan_lock = self._plan_locks.setdefault(
            task.plan_id, asyncio.Lock()
        )
        async with plan_lock:
            task = self._load_task(task_id)
            if self._task_is_dispatched(task) or task.state not in {
                ExecutionTaskState.CREATED,
                ExecutionTaskState.PREFLIGHT,
            }:
                self._record_task_event(
                    task,
                    "task_cancellation_rejected",
                    result_status="rejected",
                    error_code=(
                        ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH.value
                    ),
                )
                raise GovernanceError(
                    ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH,
                    details={
                        "task_id": task.task_id,
                        "task_state": task.state.value,
                        "provider_dispatch_occurred": (
                            self._task_is_dispatched(task)
                        ),
                    },
                )
            self._record_task_event(
                task,
                "task_cancelled_pre_dispatch",
                new_state=ExecutionTaskState.CANCELLED_PRE_DISPATCH,
                changes={
                    "completed_at": self._timestamp(),
                    "terminal_outcome": "cancelled_pre_dispatch",
                    "legacy_projection": {
                        **task.legacy_projection,
                        "task_state": "cancelled_pre_dispatch",
                    },
                },
            )
            return {
                "status": "cancelled_pre_dispatch",
                "provider_dispatch_occurred": False,
                "approval_consumed": self._task_approval_consumed(task),
                "task": self._public_task(task),
            }

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

    def _project_plan_event_to_task(
        self,
        plan: ChangePlan,
        event: str,
        *,
        operation_step: ConfigurationOperation | None = None,
    ) -> None:
        """Project only irreversible/progress facts into the active F1 task."""

        task_id = self._active_task_ids_by_plan.get(plan.plan_id)
        if task_id is None:
            return
        task = self._load_task(task_id)
        if task.state in TERMINAL_TASK_STATES:
            return

        if event == "external_approval_consumed":
            self._record_task_event(
                task,
                "approval_consumed",
                changes={
                    "approval_reference": self._task_approval_reference(
                        plan
                    ),
                },
            )
            return

        dispatch_event = (
            (
                plan.contract_version
                == OPERATIONAL_PLAN_CONTRACT_VERSION
                and event.endswith("_dispatch_recorded")
            )
            or (
                plan.contract_version
                == CONFIGURATION_PLAN_CONTRACT_VERSION
                and event == "configuration_operation_started"
            )
            or (
                plan.contract_version
                < CONFIGURATION_PLAN_CONTRACT_VERSION
                and event == "change_apply_started"
            )
        )
        if dispatch_event:
            if task.approval_reference.get("approval_state") != (
                ApprovalState.CONSUMED.value
            ):
                self._record_task_event(
                    task,
                    "approval_consumed",
                    changes={
                        "approval_reference": (
                            self._task_approval_reference(plan)
                        ),
                    },
                )
            attempted_at = self._timestamp()
            provider = "engineering_configuration_provider"
            if (
                plan.contract_version
                == OPERATIONAL_PLAN_CONTRACT_VERSION
                and plan.operational is not None
            ):
                attempted_at = str(
                    plan.operational.dispatch.get("attempted_at")
                    or attempted_at
                )
                provider = plan.operational.provider
            elif plan.contract_version < CONFIGURATION_PLAN_CONTRACT_VERSION:
                attempted_at = (
                    plan.approval.consumed_at or attempted_at
                )
                provider = "direct_home_assistant_automation"
            parsed = parse_task_timestamp(attempted_at)
            attempts = list(task.provider_attempts)
            attempts.append(
                {
                    "attempt": len(attempts) + 1,
                    "attempted_at": attempted_at,
                    "provider": provider,
                    "operation_id": (
                        operation_step.operation_id
                        if operation_step is not None
                        else None
                    ),
                    "response_received": False,
                }
            )
            first_dispatch = task.dispatched_at is None
            deadline = task.maximum_post_dispatch_deadline
            if first_dispatch:
                deadline = (
                    parsed + EXECUTION_TASK_POST_DISPATCH_DEADLINE
                ).isoformat()
            self._record_task_event(
                task,
                "dispatch_attempted",
                new_state=(
                    ExecutionTaskState.DISPATCHING
                    if first_dispatch
                    else None
                ),
                changes={
                    "started_at": task.started_at or attempted_at,
                    "dispatched_at": task.dispatched_at or attempted_at,
                    "maximum_post_dispatch_deadline": deadline,
                    "provider_attempts": attempts,
                    "legacy_projection": {
                        **task.legacy_projection,
                        "plan_status": plan.status.value,
                        "execution_outcome": plan.execution_outcome,
                    },
                },
            )
            return

        if event.endswith(("_provider_completed", "_provider_failed")):
            response_received = False
            response_recorded_at: str | None = None
            if (
                plan.contract_version
                == CONFIGURATION_PLAN_CONTRACT_VERSION
                and operation_step is not None
                and isinstance(operation_step.execution_receipt, dict)
            ):
                response_received = (
                    operation_step.execution_receipt.get(
                        "provider_response_received"
                    )
                    is True
                )
                recorded = operation_step.execution_receipt.get(
                    "provider_response_recorded_at"
                )
                if isinstance(recorded, str):
                    response_recorded_at = recorded
            elif (
                plan.contract_version
                < CONFIGURATION_PLAN_CONTRACT_VERSION
                and event in AUTOMATION_PROVIDER_RESPONSE_EVENTS
                and plan.events
                and plan.events[-1].event == event
            ):
                # A contract-v1 automation provider event is emitted only
                # after the REST transport returned a response (including an
                # empty successful body) or exposed a bounded received-error
                # marker. The persisted event timestamp is the durable receipt
                # time; readback never manufactures this evidence.
                response_received = True
                response_recorded_at = plan.events[-1].timestamp
            elif plan.operational is not None:
                response_received = bool(
                    plan.operational.dispatch.get(
                        "provider_response_received"
                    )
                )
                recorded = plan.operational.dispatch.get(
                    "provider_response_at"
                )
                if isinstance(recorded, str):
                    response_recorded_at = recorded
            if not response_received:
                self._record_task_event(
                    task,
                    "verification_evidence_updated",
                    new_state=(
                        ExecutionTaskState.OBSERVING
                        if task.state == ExecutionTaskState.DISPATCHING
                        else None
                    ),
                    changes={
                        "verification_summary": {
                            **task.verification_summary,
                            "status": "pending",
                            "provider_response_received": False,
                        },
                    },
                    result_status="partial",
                )
                return
            attempts = list(task.provider_attempts)
            if attempts:
                attempts[-1] = {
                    **attempts[-1],
                    "response_received": True,
                    "response_recorded_at": str(
                        response_recorded_at or self._timestamp()
                    ),
                }
            self._record_task_event(
                task,
                "provider_response_recorded",
                new_state=(
                    ExecutionTaskState.OBSERVING
                    if task.state == ExecutionTaskState.DISPATCHING
                    else None
                ),
                changes={
                    "provider_attempts": attempts,
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "pending",
                        "provider_response_received": True,
                    },
                },
                result_status=(
                    "failure"
                    if event.endswith("_provider_failed")
                    else "success"
                ),
            )
            return

        if event.endswith("_dispatch_indeterminate"):
            # A lost provider response is dispatch evidence, not a response.
            # Preserve the original attempt unchanged and move into
            # readback-only observation without manufacturing response timing.
            self._record_task_event(
                task,
                "verification_evidence_updated",
                new_state=(
                    ExecutionTaskState.OBSERVING
                    if task.state == ExecutionTaskState.DISPATCHING
                    else None
                ),
                changes={
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "pending",
                        "provider_response_received": False,
                    },
                },
                result_status="partial",
            )
            return

        if event.endswith("_verification_started") and task.state in {
            ExecutionTaskState.DISPATCHING,
            ExecutionTaskState.OBSERVING,
        }:
            self._record_task_event(
                task,
                "verification_started",
                new_state=ExecutionTaskState.VERIFYING,
                changes={
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "verifying",
                    },
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
        approval_action: str | None = None,
        operation_step: ConfigurationOperation | None = None,
        failure_category: str | None = None,
        failure_stage: str | None = None,
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
            "approval_action": approval_action,
            "approval_channel": plan.approval.channel,
            "challenge_id": plan.approval.challenge_id,
            "approval_principal_present": bool(approval_principal),
            "approval_bundle_state": self._approval_bundle_state(plan),
            "same_principal_requirement": bool(
                plan.policy_decision
                and plan.policy_decision.policy_class
                == ApprovalPolicyClass.ELEVATED_ADMIN
            ),
            "same_principal_confirmed": (
                plan.approval.same_principal_confirmed
            ),
        }
        if plan.policy_decision is not None:
            safe.update(
                {
                    "policy_class": (
                        plan.policy_decision.policy_class.value
                    ),
                    "risk_delta": plan.policy_decision.risk_delta.value,
                    "physical_consequence": (
                        plan.policy_decision.physical_consequence.value
                    ),
                    "policy_version": (
                        plan.policy_decision.policy_version
                    ),
                    "policy_decision_hash": (
                        plan.policy_decision.policy_decision_hash
                    ),
                }
            )
        if operation_step is None:
            # Contract-v1 audit records predate ordered-operation metadata.
            # Preserve their exact event shape.
            safe.pop("operation_id", None)
            safe.pop("operation_order", None)
        if (
            plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION
            and plan.operational is not None
        ):
            safe.update(
                {
                    "plan_family": plan.plan_family,
                    "provider": plan.operational.provider,
                    "provider_dispatch_occurred": bool(
                        plan.operational.dispatch.get("dispatched")
                    ),
                    "provider_operation_id": (
                        plan.operational.dispatch.get(
                            "provider_operation_id"
                        )
                    ),
                    "fallback_occurred": False,
                    "fallback": "none",
                    "rollback_available": False,
                }
            )
            if failure_category is not None:
                safe["failure_category"] = failure_category
            if failure_stage is not None:
                safe["failure_stage"] = failure_stage
        # Persist the event and lifecycle state before emitting a success audit.
        # If storage fails, the caller returns change_plan_storage_error and no
        # misleading success record is produced.
        self._save(plan)
        self._project_plan_event_to_task(
            plan,
            event,
            operation_step=operation_step,
        )
        if self.audit:
            self.audit.write(safe)
        log_event(
            self.logger,
            logging.INFO if result_status == "success" else logging.WARNING,
            event,
            (
                "Governed operational-administration lifecycle event."
                if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION
                else "Governed configuration-plan lifecycle event."
                if plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION
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
        if (
            plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION
            and plan.status
            in {PlanStatus.APPLYING, PlanStatus.VERIFICATION_REQUIRED}
            and plan.operational is not None
            and plan.operational.dispatch.get("attempt_count") == 1
        ):
            # Expiration limits authority to dispatch. Once exact-once
            # dispatch evidence exists, read-only verification must remain
            # available indefinitely and must never reopen write authority.
            return False
        if self.now() >= datetime.fromisoformat(plan.expires_at):
            plan.status = PlanStatus.EXPIRED
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.bundle_state = "invalidated"
            plan.approval.csrf_digest = None
            if plan.approval.elevated_risk_acknowledgement is not None:
                plan.approval.elevated_risk_acknowledgement.state = (
                    ApprovalState.INVALIDATED
                )
                plan.approval.elevated_risk_acknowledgement.csrf_digest = None
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

        action, _challenge_id, _requested_at, expires_at = (
            self._active_challenge_projection(plan)
        )
        pending = (
            plan.approval.state == ApprovalState.EXTERNAL_PENDING
            if action == ApprovalActionKind.PLAN_APPROVAL.value
            else bool(
                plan.approval.elevated_risk_acknowledgement
                and plan.approval.elevated_risk_acknowledgement.state
                == ApprovalState.EXTERNAL_PENDING
            )
        )
        if not pending:
            return False
        try:
            return not expires_at or self.now() >= datetime.fromisoformat(
                expires_at
            )
        except (TypeError, ValueError):
            return True

    def _invalidate_terminal_challenge_if_needed(self, plan: ChangePlan) -> bool:
        """Reconcile an impossible persisted pending challenge on a terminal plan."""

        if (
            not is_terminal_plan(plan)
            or not (
                plan.approval.state == ApprovalState.EXTERNAL_PENDING
                or bool(
                    plan.approval.elevated_risk_acknowledgement
                    and plan.approval.elevated_risk_acknowledgement.state
                    == ApprovalState.EXTERNAL_PENDING
                )
            )
        ):
            return False
        plan.approval.state = ApprovalState.INVALIDATED
        plan.approval.bundle_state = "invalidated"
        plan.approval.csrf_digest = None
        if plan.approval.elevated_risk_acknowledgement is not None:
            plan.approval.elevated_risk_acknowledgement.state = (
                ApprovalState.INVALIDATED
            )
            plan.approval.elevated_risk_acknowledgement.csrf_digest = (
                None
            )
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
        prohibited = self._is_effectively_prohibited_plan(plan)
        if prohibited:
            # Authority-v3 persists the legacy pre-approval enum values to
            # preserve the closed task/plan schema. Public projections must
            # nevertheless describe the authoritative F2 terminal lifecycle,
            # not an approval action that can never exist.
            value["status"] = "prohibited"
        # CSRF material is private to the Ingress authority and must never be
        # returned through MCP plan reads or summaries.
        if isinstance(value.get("approval"), dict):
            if prohibited:
                value["approval"]["state"] = "prohibited"
            value["approval"].pop("csrf_digest", None)
            value["approval"].pop("csrf_issued_at", None)
            value["approval"].pop("approver_principal", None)
            acknowledgement = value["approval"].get(
                "elevated_risk_acknowledgement"
            )
            if isinstance(acknowledgement, dict):
                acknowledgement.pop("csrf_digest", None)
                acknowledgement.pop("csrf_issued_at", None)
                acknowledgement.pop("approver_principal", None)
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
        value["approval_bundle_state"] = self._effective_approval_bundle_state(
            plan
        )
        value["status_is_legacy"] = not prohibited
        value["authoritative_lifecycle_field"] = "approval_lifecycle"
        value["approval_actionable"] = self._approval_is_actionable(plan)
        value["approval_challenge_created"] = bool(plan.approval.challenge_id)
        value["next_required_operation"] = (
            "approve_change_plan"
            if approval_lifecycle == "approval_not_requested"
            and (
                plan.policy_decision is None
                or plan.policy_decision.policy_class
                != ApprovalPolicyClass.PROHIBITED
            )
            else None
        )
        value["plan_hash"] = self.plan_hash(plan)
        value["apply_allowed"] = self._valid_external_approval(
            plan, "apply"
        )
        try:
            task = self.task_repository.get_for_plan(plan.plan_id)
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        value["execution_task"] = (
            self._task_plan_projection(task)
            if task is not None
            else {
                "record_kind": "legacy_plan",
                "task_id": None,
                "task_state": None,
                "terminal_outcome": None,
                "provider_dispatch_occurred": False,
            }
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
            if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
                # Contract-v3 operational plans use the durable operational
                # verification record.  Removing the legacy configuration
                # field avoids a contradictory "not_run" next to a verified
                # restart, reload, or backup result.
                value.pop("verification", None)
                value["authoritative_verification_field"] = (
                    "operational.verification"
                )
                value["generic_configuration_verification_applicable"] = (
                    False
                )
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

    def _effective_approval_bundle_state(self, plan: ChangePlan) -> str:
        return (
            "prohibited"
            if self._is_effectively_prohibited_plan(plan)
            else self._approval_bundle_state(plan)
        )

    def _approval_lifecycle(self, plan: ChangePlan) -> str:
        bundle_state = self._effective_approval_bundle_state(plan)
        if bundle_state == "pending_elevated_risk_acknowledgement":
            return "pending_elevated_risk_acknowledgement"
        if bundle_state == "fully_approved":
            return "approved"
        if bundle_state == "prohibited":
            return "prohibited"
        return {
            ApprovalState.REQUIRED: "approval_not_requested",
            ApprovalState.EXTERNAL_PENDING: "approval_pending_external",
            ApprovalState.APPROVED: "approved",
            ApprovalState.CONSUMED: "approval_consumed",
            ApprovalState.REJECTED: "approval_rejected",
            ApprovalState.EXPIRED: "approval_expired",
            ApprovalState.INVALIDATED: "approval_invalidated",
        }[plan.approval.state]

    def _effective_plan_status(self, plan: ChangePlan) -> str:
        return (
            "prohibited"
            if self._is_effectively_prohibited_plan(plan)
            else plan.status.value
        )

    def _approval_is_actionable(self, plan: ChangePlan) -> bool:
        """Project actionability without upgrading legacy authority records."""

        decision = plan.policy_decision
        if decision is None:
            return bool(
                plan.status
                in {
                    PlanStatus.AWAITING_APPROVAL,
                    PlanStatus.ROLLBACK_PENDING,
                }
                and plan.approval.state
                in {
                    ApprovalState.REQUIRED,
                    ApprovalState.EXTERNAL_PENDING,
                }
            )
        if (
            self._is_effectively_prohibited_plan(plan)
            or not decision.required_acknowledgements
        ):
            return False
        return bool(
            plan.status
            in {
                PlanStatus.AWAITING_APPROVAL,
                PlanStatus.ROLLBACK_PENDING,
            }
            and self._approval_lifecycle(plan)
            in {
                "approval_not_requested",
                "approval_pending_external",
                "pending_elevated_risk_acknowledgement",
            }
        )

    def _summary(self, plan: ChangePlan) -> dict[str, Any]:
        """Return bounded plan inventory; get_change_plan is the detail path."""
        value = {
            "plan_id": plan.plan_id,
            "plan_hash": self.plan_hash(plan),
            "plan_version": plan.plan_version,
            "title": plan.title,
            "status": self._effective_plan_status(plan),
            "approval_lifecycle": self._approval_lifecycle(plan),
            "approval_bundle_state": self._effective_approval_bundle_state(
                plan
            ),
            "status_is_legacy": not self._is_effectively_prohibited_plan(
                plan
            ),
            "authoritative_lifecycle_field": "approval_lifecycle",
            "approval_actionable": self._approval_is_actionable(plan),
            "approval_challenge_created": bool(plan.approval.challenge_id),
            "target": {"target_type": plan.target_type, "target_id": plan.target_id},
            "operation": plan.operation.value,
            "risk_level": plan.risk.level.value,
            "policy_class": (
                plan.policy_decision.policy_class.value
                if plan.policy_decision is not None
                else None
            ),
            "risk_delta": (
                plan.policy_decision.risk_delta.value
                if plan.policy_decision is not None
                else None
            ),
            "physical_consequence": (
                plan.policy_decision.physical_consequence.value
                if plan.policy_decision is not None
                else None
            ),
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "expires_at": plan.expires_at,
            "apply_allowed": bool(self._public(plan, include_configs=False)["apply_allowed"]),
        }
        if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
            value.update(
                {
                    "contract_version": plan.contract_version,
                    "plan_family": plan.plan_family,
                    "execution_outcome": plan.execution_outcome,
                    "rollback_available": False,
                }
            )
        elif plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION:
            value.update(
                {
                    "contract_version": plan.contract_version,
                    "operation_count": len(plan.operations),
                    "execution_outcome": plan.execution_outcome,
                    "configuration_check_status": plan.configuration_check_status,
                }
            )
        public = self._public(plan, include_configs=False)
        value["execution_task"] = public["execution_task"]
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
        if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
            if plan.operation == ChangeOperation.CREATE_FULL_BACKUP:
                return {("operational_backup", "global")}
            return {
                (
                    f"operational_{plan.operation.value}",
                    plan.target_id,
                )
            }
        if plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION:
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

    async def create_backup_plan(
        self,
        *,
        backup_name: str = "",
        title: str = "Create governed Home Assistant backup",
        description: str = (
            "Create one reviewed local Home Assistant configuration and add-on backup."
        ),
        expiration_minutes: int = 120,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a proposal only; provider creation is unreachable here."""

        if self.operational_gateway is None:
            raise GovernanceError(ErrorCode.BACKUP_PROVIDER_UNAVAILABLE)
        now = self.now()
        try:
            normalized_name = normalize_backup_name(
                backup_name, generated_at=now
            )
        except (TypeError, ValueError) as exc:
            raise GovernanceError(
                ErrorCode.INVALID_REQUEST,
                details={"reason": "invalid_backup_name"},
            ) from exc
        if any(secret and secret in normalized_name for secret in self.sensitive_values):
            raise GovernanceError(
                ErrorCode.INVALID_REQUEST,
                details={"reason": "backup_name_contains_sensitive_data"},
            )
        expiration_minutes = max(5, min(int(expiration_minutes), 1440))
        try:
            evidence = await self.operational_gateway.planning_evidence()
        except OperationalGatewayError as exc:
            raise GovernanceError(
                self._operational_error_code(exc.category, dispatched=False)
            ) from None
        provider_evidence = evidence.get("provider")
        baseline = evidence.get("baseline")
        if not isinstance(provider_evidence, dict) or not isinstance(
            baseline, dict
        ):
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        if (
            baseline.get("inventory_readable") is not True
            or not isinstance(baseline.get("backup_ids"), list)
            or baseline.get("operation_state") != "idle"
        ):
            raise GovernanceError(ErrorCode.BACKUP_PROVIDER_UNAVAILABLE)
        risk = ChangeRiskAssessment(
            level=RiskLevel.MEDIUM,
            reasons=[
                "Backup creation is an administrative infrastructure write.",
                "The operation consumes local storage and cannot be automatically rolled back.",
            ],
            apply_allowed=True,
            evidence=[
                {
                    "field": "operation",
                    "trigger": "governed_full_backup_creation",
                }
            ],
            warnings=[
                "The reviewed provider excludes the recorder database.",
                "Archive-content integrity is not independently validated.",
            ],
        )
        operational = OperationalPlanDetails(
            schema_version=1,
            family="operational_administration",
            operation=ChangeOperation.CREATE_FULL_BACKUP.value,
            requested_name=normalized_name,
            provider=str(provider_evidence.get("provider") or ""),
            provider_capability_evidence=provider_evidence,
            expected_effects=[
                "Create one new local Home Assistant backup archive.",
                "Include Home Assistant configuration and all add-ons when supervised.",
                "Exclude the recorder database under the reviewed upstream contract.",
            ],
            preconditions=[
                "Home Assistant backup inventory is readable.",
                "The exact reviewed upstream backup contract is admitted.",
                "One unexpired external administrator approval is bound to this plan hash.",
            ],
            verification_contract={
                "version": 1,
                "required": [
                    "new_backup_identifier",
                    "not_in_preapply_baseline",
                    "completed_operation_state",
                    "readable_metadata",
                    "exact_name",
                    "creation_time_in_apply_window",
                    "nonzero_size_when_reported",
                    "post_apply_inventory_readable",
                ],
                "archive_integrity_validation": "unsupported",
                "no_blind_redispatch": True,
            },
            baseline=baseline,
            dispatch={
                "attempt_count": 0,
                "dispatched": False,
                "request_id": None,
                "attempted_at": None,
                "provider_operation_id": None,
                "backup_id": None,
            },
            verification=RecoveryVerification(),
            limitations=[
                "Recorder database content is excluded by the reviewed provider.",
                "Archive-content integrity is not independently validated.",
                "Restore, delete, download, retention, and external-storage operations are unavailable.",
            ],
            rollback_available=False,
        )
        plan = ChangePlan(
            plan_id=self._new_id(),
            plan_version=1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=expiration_minutes)).isoformat(),
            status=PlanStatus.AWAITING_APPROVAL,
            title=title[:160],
            description=description[:1000],
            requested_by=current_caller_id(),
            target=ChangeTarget("backup", "local_full_backup"),
            operation=ChangeOperation.CREATE_FULL_BACKUP,
            proposed_config={},
            current_config=None,
            normalized_proposed_config={},
            normalized_current_config=None,
            current_state_fingerprint=stable_hash(baseline),
            proposed_config_hash=stable_hash(
                {
                    "operation": ChangeOperation.CREATE_FULL_BACKUP.value,
                    "name": normalized_name,
                }
            ),
            risk=risk,
            normalization_version=1,
            warnings=list(risk.warnings),
            validation_results={
                "valid": True,
                "planning_write_performed": False,
                "provider_available": True,
                "home_assistant_connected": True,
            },
            dry_run_results={
                "operation": ChangeOperation.CREATE_FULL_BACKUP.value,
                "provider_dispatch_occurred": False,
                "rollback_available": False,
            },
            rollback=ChangeRollback(available=False, status="unavailable"),
            caller_context=sanitize_context(caller_context),
            contract_version=OPERATIONAL_PLAN_CONTRACT_VERSION,
            plan_family="operational_administration",
            operational=operational,
            execution_outcome="not_applied",
        )
        plan.policy_decision = evaluate_change_policy(plan)
        self._bind_new_plan_policy(plan)
        self._supersede_prior(plan)
        self._record(plan, "operational_backup_plan_created", "success")
        return {
            "status": "awaiting_approval",
            "proposal_only": True,
            "provider_dispatch_occurred": False,
            "plan": self._public(plan, include_configs=False),
        }

    async def create_reload_plan(
        self,
        *,
        reload_target: str,
        expiration_minutes: int = 120,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Propose one exact allowlisted reload without dispatching it."""

        if reload_target not in RELOAD_SERVICES:
            raise GovernanceError(
                ErrorCode.INVALID_REQUEST,
                details={"reason": "unsupported_reload_target"},
            )
        return await self._create_lifecycle_plan(
            operation=ChangeOperation.CONTROLLED_RELOAD,
            target_type="reload_domain",
            target_id=reload_target,
            title=f"Reload Home Assistant {reload_target} configuration",
            description=(
                "Run one exact reviewed domain reload after configuration "
                "validation and external administrator approval."
            ),
            expiration_minutes=expiration_minutes,
            caller_context=caller_context,
        )

    async def create_addon_restart_plan(
        self,
        *,
        addon_slug: str,
        expiration_minutes: int = 120,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Propose one exact installed add-on restart without dispatching it."""

        return await self._create_lifecycle_plan(
            operation=ChangeOperation.RESTART_ADDON,
            target_type="addon",
            target_id=addon_slug,
            title=f"Restart installed add-on {addon_slug}"[:160],
            description=(
                "Restart one exact installed add-on through the reviewed "
                "restart-only provider contract."
            ),
            expiration_minutes=expiration_minutes,
            caller_context=caller_context,
        )

    async def create_home_assistant_restart_plan(
        self,
        *,
        expiration_minutes: int = 120,
        caller_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Propose one governed Home Assistant restart without dispatching it."""

        return await self._create_lifecycle_plan(
            operation=ChangeOperation.RESTART_HOME_ASSISTANT,
            target_type="home_assistant",
            target_id="core",
            title="Restart Home Assistant",
            description=(
                "Restart Home Assistant once after full configuration "
                "validation and external administrator approval."
            ),
            expiration_minutes=expiration_minutes,
            caller_context=caller_context,
        )

    async def _create_lifecycle_plan(
        self,
        *,
        operation: ChangeOperation,
        target_type: str,
        target_id: str,
        title: str,
        description: str,
        expiration_minutes: int,
        caller_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Create a fail-closed plan from complete load-bearing evidence.

        Normalization, hashing, approval, pre-dispatch revalidation, and
        recovery bind the operation, exact target and target fingerprint,
        reviewed provider capability, required planning validation, and the
        operation-specific verification contract. Self-restart additionally
        requires a process-instance baseline; Home Assistant restart requires
        Home Assistant identity; and disruptive recovery binds runtime/build
        and tool counts, upstream identity/catalog admission, governance and
        audit persistence, and the expected verification contract. Incomplete
        or synthetic evidence is intentionally rejected by those later
        fail-closed checks rather than normalized into an applicable plan.
        """

        if self.lifecycle_gateway is None:
            raise GovernanceError(
                ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE
            )
        expiration_minutes = max(5, min(int(expiration_minutes), 1440))
        try:
            evidence = await self.lifecycle_gateway.planning_evidence(
                operation.value, target_id
            )
        except (LifecycleGatewayError, KeyError) as exc:
            category = getattr(exc, "category", "invalid_request")
            code = self._lifecycle_error_code(
                category, dispatched=False
            )
            if code == ErrorCode.ADDON_NOT_FOUND:
                METRICS.record_classified_outcome(category)
            raise GovernanceError(
                code
            ) from None
        provider_evidence = evidence.get("provider")
        baseline = evidence.get("baseline")
        if not isinstance(provider_evidence, dict) or not isinstance(
            baseline, dict
        ):
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        validation = baseline.get("configuration_validation")
        if operation in {
            ChangeOperation.CONTROLLED_RELOAD,
            ChangeOperation.RESTART_HOME_ASSISTANT,
        } and (
            not isinstance(validation, dict)
            or validation.get("status") != "valid"
        ):
            raise GovernanceError(
                ErrorCode.OPERATIONAL_VALIDATION_FAILED,
                details={
                    "failure_stage": "planning",
                    "provider_dispatch_occurred": False,
                },
            )
        now = self.now()
        is_high_risk = operation in {
            ChangeOperation.RESTART_ADDON,
            ChangeOperation.RESTART_HOME_ASSISTANT,
        }
        risk = ChangeRiskAssessment(
            level=RiskLevel.HIGH if is_high_risk else RiskLevel.MEDIUM,
            reasons=[
                (
                    "The exact add-on restart is a disruptive high-risk "
                    "infrastructure action."
                    if operation == ChangeOperation.RESTART_ADDON
                    else "The Home Assistant restart is a disruptive high-risk infrastructure action."
                    if operation == ChangeOperation.RESTART_HOME_ASSISTANT
                    else "The controlled reload is an infrastructure write."
                ),
                "The action cannot be automatically rolled back.",
            ],
            apply_allowed=True,
            evidence=[
                {
                    "field": "operation",
                    "trigger": operation.value,
                }
            ],
            warnings=[
                "Temporary provider or Home Assistant unavailability may be expected.",
                "A dispatched operation is never blindly repeated.",
            ],
        )
        validation_required = operation in {
            ChangeOperation.CONTROLLED_RELOAD,
            ChangeOperation.RESTART_HOME_ASSISTANT,
        }
        operational = OperationalPlanDetails(
            schema_version=1,
            family="operational_administration",
            operation=operation.value,
            requested_name=target_id,
            provider=str(provider_evidence.get("provider") or ""),
            provider_capability_evidence=provider_evidence,
            expected_effects=_lifecycle_expected_effects(
                operation, target_id
            ),
            preconditions=[
                "The exact reviewed upstream contract remains admitted.",
                "One unexpired external administrator approval is bound to this plan hash.",
                *(
                    [
                        "Full Home Assistant configuration validation remains valid immediately before dispatch."
                    ]
                    if validation_required
                    else []
                ),
            ],
            verification_contract={
                "version": 1,
                "operation": operation.value,
                "required": _lifecycle_verification_requirements(operation),
                "no_blind_redispatch": True,
                "bounded_initial_response": True,
                "startup_reconciliation": True,
            },
            baseline=baseline,
            dispatch={
                "attempt_count": 0,
                "dispatched": False,
                "request_id": None,
                "attempted_at": None,
                "provider_response_received": False,
                "restart_dispatch_confirmed": False,
                "expected_disruption_observed": False,
                **(
                    {"outage_observation_deadline": None}
                    if operation
                    == ChangeOperation.RESTART_HOME_ASSISTANT
                    else {}
                ),
            },
            verification=RecoveryVerification(),
            limitations=[
                "Rollback is unavailable.",
                "A lost provider response can require readback-only reconciliation or manual review.",
            ],
            rollback_available=False,
        )
        plan = ChangePlan(
            plan_id=self._new_id(),
            plan_version=1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(
                now + timedelta(minutes=expiration_minutes)
            ).isoformat(),
            status=PlanStatus.AWAITING_APPROVAL,
            title=title[:160],
            description=description[:1000],
            requested_by=current_caller_id(),
            target=ChangeTarget(target_type, target_id),
            operation=operation,
            proposed_config={},
            current_config=None,
            normalized_proposed_config={},
            normalized_current_config=None,
            current_state_fingerprint=stable_hash(baseline),
            proposed_config_hash=stable_hash(
                {
                    "operation": operation.value,
                    "target_type": target_type,
                    "target_id": target_id,
                }
            ),
            risk=risk,
            normalization_version=1,
            warnings=list(risk.warnings),
            validation_results={
                "valid": True,
                "planning_write_performed": False,
                "provider_available": True,
                "configuration_validation_required": validation_required,
                "configuration_validation": validation,
            },
            dry_run_results={
                "operation": operation.value,
                "provider_dispatch_occurred": False,
                "rollback_available": False,
            },
            rollback=ChangeRollback(available=False, status="unavailable"),
            caller_context=_sanitize_configuration_caller_context(
                caller_context,
                known_secrets=self.sensitive_values,
            ),
            contract_version=OPERATIONAL_PLAN_CONTRACT_VERSION,
            plan_family="operational_administration",
            operational=operational,
            execution_outcome="not_applied",
        )
        plan.policy_decision = evaluate_change_policy(plan)
        self._bind_new_plan_policy(plan)
        self._supersede_prior(plan)
        self._record(
            plan,
            f"{operation.value}_plan_created",
            "success",
        )
        return {
            "status": "awaiting_approval",
            "proposal_only": True,
            "provider_dispatch_occurred": False,
            "plan": self._public(plan, include_configs=False),
        }

    async def create_plan(
        self,
        *,
        title: str,
        description: str,
        operation: str,
        automation_id: str,
        proposed_config: dict[str, Any],
        expiration_minutes: int = 120,
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
        plan.policy_decision = evaluate_change_policy(plan)
        self._bind_new_plan_policy(plan)
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
        expiration_minutes: int = 120,
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
        plan.policy_decision = evaluate_change_policy(plan)
        self._bind_new_plan_policy(plan)
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
            if plan.plan_id == new_plan.plan_id or not bool(
                self._plan_target_keys(plan) & new_targets
            ):
                continue
            if plan.policy_decision is not None:
                try:
                    self._require_policy_snapshot(plan)
                except GovernanceError as exc:
                    if exc.code in {
                        ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                        ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                        ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
                        ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                    }:
                        # Invalid historical authority is left byte-for-byte
                        # untouched. It cannot be approved or applied, but it
                        # must not prevent creation of a replacement plan.
                        continue
                    raise
            self._resolve_lifecycle(plan)
            if is_terminal_plan(plan):
                continue
            if plan.status in {
                PlanStatus.AWAITING_APPROVAL,
                PlanStatus.APPROVED,
                PlanStatus.ROLLBACK_PENDING,
            }:
                plan.status = PlanStatus.SUPERSEDED
                plan.approval.state = ApprovalState.INVALIDATED
                plan.approval.bundle_state = "invalidated"
                plan.approval.csrf_digest = None
                if plan.approval.elevated_risk_acknowledgement is not None:
                    plan.approval.elevated_risk_acknowledgement.state = (
                        ApprovalState.INVALIDATED
                    )
                    plan.approval.elevated_risk_acknowledgement.csrf_digest = None
                if plan.approval.challenge_id:
                    self._record(plan, "external_approval_invalidated", "rejected")
                self._record(plan, "change_plan_superseded", "rejected")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self._load(plan_id)
        self._resolve_lifecycle(plan)
        return self._public(plan)

    def resolved_plans(
        self, *, validate_policy: bool = True
    ) -> list[ChangePlan]:
        """Return persisted plans after applying the shared effective lifecycle."""

        try:
            plans = self.repository.list()
        except ChangePlanStorageError as exc:
            raise GovernanceError(
                ErrorCode.CHANGE_PLAN_STORAGE_ERROR
            ) from exc
        for plan in plans:
            self._require_v2_persisted_plan_safe(plan)
            if validate_policy and plan.policy_decision is not None:
                self._require_policy_snapshot(plan)
            self._resolve_lifecycle(plan)
        return plans

    def list_plans(self, status: str = "", limit: int = 20) -> dict[str, Any]:
        plans = []
        for plan in self.resolved_plans():
            if status and self._effective_plan_status(plan) != status:
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
        self._require_policy_snapshot(plan)
        decision = plan.policy_decision
        if decision is None:
            raise GovernanceError(ErrorCode.POLICY_SNAPSHOT_REQUIRED)
        if decision.policy_class == ApprovalPolicyClass.PROHIBITED:
            self._record(
                plan,
                "policy_approval_rejected",
                "rejected",
                error_code=ErrorCode.PROHIBITED_CHANGE.value,
            )
            raise GovernanceError(ErrorCode.PROHIBITED_CHANGE)
        if plan.approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            raise GovernanceError(
                ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                details={"resource_id": plan.plan_id, "reason": "active_plan_must_be_recreated"},
            )
        self._require_current_normalization(plan)
        calculated = self.plan_hash(plan)
        if expected_plan_hash != calculated:
            raise GovernanceError(ErrorCode.APPROVAL_HASH_MISMATCH)
        if self._active_challenge_matches(plan, calculated):
            return self._approval_pending_response(plan)
        if self._approval_bundle_state(plan) == "fully_approved" or (
            plan.approval.state == ApprovalState.CONSUMED
        ):
            raise GovernanceError(ErrorCode.APPROVAL_ALREADY_CONSUMED)
        if plan.status not in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_NOT_APPROVED)
        if not plan.validation_results.get("valid"):
            raise GovernanceError(
                ErrorCode.CONFIGURATION_VALIDATION_FAILED
                if plan.contract_version >= CONFIGURATION_PLAN_CONTRACT_VERSION
                else ErrorCode.AUTOMATION_VALIDATION_FAILED
            )
        if plan.approval.state == ApprovalState.EXTERNAL_PENDING:
            plan.approval.state = ApprovalState.INVALIDATED
            plan.approval.bundle_state = "invalidated"
            plan.approval.csrf_digest = None
            if plan.approval.elevated_risk_acknowledgement is not None:
                plan.approval.elevated_risk_acknowledgement.state = (
                    ApprovalState.INVALIDATED
                )
                plan.approval.elevated_risk_acknowledgement.csrf_digest = None
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
            policy_decision_hash=decision.policy_decision_hash,
            policy_class=decision.policy_class.value,
            bundle_state="pending_plan_approval",
            elevated_risk_acknowledgement=(
                ApprovalActionRecord(
                    kind=(
                        ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT
                    ),
                    authority_version=APPROVAL_AUTHORITY_VERSION,
                    bound_plan_hash=calculated,
                    policy_decision_hash=(
                        decision.policy_decision_hash
                    ),
                    policy_class=decision.policy_class.value,
                    risk_delta=decision.risk_delta.value,
                    physical_consequence=(
                        decision.physical_consequence.value
                    ),
                )
                if decision.policy_class
                == ApprovalPolicyClass.ELEVATED_ADMIN
                else None
            ),
        )
        self._record(plan, "external_approval_requested", "success")
        return self._approval_pending_response(plan)

    def _approval_pending_response(self, plan: ChangePlan) -> dict[str, Any]:
        action, challenge_id, requested_at, expires_at = (
            self._active_challenge_projection(plan)
        )
        summary = {
            "status": "approval_pending",
            "approval_lifecycle": self._approval_lifecycle(plan),
            "approval_bundle_state": self._approval_bundle_state(plan),
            "approval_challenge_created": True,
            "plan_id": plan.plan_id,
            "approval_kind": plan.approval.approval_kind,
            "approval_action": action,
            "bound_plan_hash": plan.approval.bound_plan_hash,
            "policy_decision_hash": plan.approval.policy_decision_hash,
            "policy_class": plan.approval.policy_class,
            "external_approval_required": True,
            "approval_channel": APPROVAL_CHANNEL,
            "challenge_id": challenge_id,
            "requested_at": requested_at,
            "challenge_expires_at": expires_at,
            "approval_ui": "Open the HA MCP Engineering approval panel in Home Assistant.",
            "plan_expires_at": plan.expires_at,
            "plan_status": plan.status.value,
            "approval_state": plan.approval.state.value,
            "authority_version": APPROVAL_AUTHORITY_VERSION,
        }
        return summary

    @staticmethod
    def _active_challenge_projection(
        plan: ChangePlan,
    ) -> tuple[str, str | None, str | None, str | None]:
        acknowledgement = (
            plan.approval.elevated_risk_acknowledgement
        )
        if (
            plan.approval.state == ApprovalState.APPROVED
            and acknowledgement is not None
            and acknowledgement.state == ApprovalState.EXTERNAL_PENDING
        ):
            return (
                acknowledgement.kind.value,
                acknowledgement.challenge_id,
                acknowledgement.challenge_requested_at,
                acknowledgement.challenge_expires_at,
            )
        return (
            ApprovalActionKind.PLAN_APPROVAL.value,
            plan.approval.challenge_id,
            plan.approval.challenge_requested_at,
            plan.approval.challenge_expires_at,
        )

    def _active_challenge_matches(self, plan: ChangePlan, calculated: str) -> bool:
        approval = plan.approval
        action, challenge_id, _requested_at, expires_at = (
            self._active_challenge_projection(plan)
        )
        action_pending = (
            approval.state == ApprovalState.EXTERNAL_PENDING
            if action == ApprovalActionKind.PLAN_APPROVAL.value
            else bool(
                approval.elevated_risk_acknowledgement
                and approval.elevated_risk_acknowledgement.state
                == ApprovalState.EXTERNAL_PENDING
            )
        )
        return bool(
            action_pending
            and plan.status in {PlanStatus.AWAITING_APPROVAL, PlanStatus.ROLLBACK_PENDING}
            and approval.authority_version == APPROVAL_AUTHORITY_VERSION
            and approval.channel == APPROVAL_CHANNEL
            and approval.bound_plan_hash == calculated
            and approval.policy_decision_hash
            == (
                plan.policy_decision.policy_decision_hash
                if plan.policy_decision is not None
                else None
            )
            and approval.policy_class
            == (
                plan.policy_decision.policy_class.value
                if plan.policy_decision is not None
                else None
            )
            and approval.challenge_plan_version == plan.plan_version
            and approval.challenge_target_type == plan.target_type
            and approval.challenge_target_id == plan.target_id
            and approval.challenge_operation == plan.operation.value
            and approval.challenge_risk_level == plan.risk.level.value
            and approval.approval_kind
            == ("rollback" if plan.status == PlanStatus.ROLLBACK_PENDING else "apply")
            and challenge_id
            and expires_at
            and not self._challenge_has_expired(plan)
        )

    def _expire_challenge_if_needed(self, plan: ChangePlan) -> bool:
        if not self._challenge_has_expired(plan):
            return False
        if plan.approval.elevated_risk_acknowledgement is not None:
            acknowledgement = plan.approval.elevated_risk_acknowledgement
            acknowledgement.state = ApprovalState.EXPIRED
            acknowledgement.csrf_digest = None
        plan.approval.state = ApprovalState.EXPIRED
        plan.approval.bundle_state = "expired"
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
            "policy_class": (
                plan.policy_decision.policy_class.value
                if plan.policy_decision is not None
                else None
            ),
            "risk_delta": (
                plan.policy_decision.risk_delta.value
                if plan.policy_decision is not None
                else None
            ),
            "physical_consequence": (
                plan.policy_decision.physical_consequence.value
                if plan.policy_decision is not None
                else None
            ),
            "policy_reason_codes": (
                list(plan.policy_decision.reason_codes)
                if plan.policy_decision is not None
                else []
            ),
            "policy_decision_hash": (
                plan.policy_decision.policy_decision_hash
                if plan.policy_decision is not None
                else None
            ),
            "expires_at": plan.expires_at,
            "challenge_id": self._active_challenge_projection(plan)[1],
            "challenge_expires_at": (
                self._active_challenge_projection(plan)[3]
            ),
            "approval_action": self._active_challenge_projection(plan)[0],
            "approval_bundle_state": self._approval_bundle_state(plan),
            "same_principal_requirement": bool(
                plan.policy_decision
                and plan.policy_decision.policy_class
                == ApprovalPolicyClass.ELEVATED_ADMIN
            ),
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
        if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
            operational = plan.operational
            if operational is None:
                raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
            summary["operational_review"] = {
                "family": operational.family,
                "operation": operational.operation,
                "requested_name": operational.requested_name,
                "provider": operational.provider,
                "expected_effects": operational.expected_effects[:10],
                "preconditions": operational.preconditions[:10],
                "verification_contract": operational.verification_contract,
                "limitations": operational.limitations[:10],
                "rollback_available": False,
                "provider_arguments": (
                    {
                        "scope": "snapshot",
                        "action": "create",
                        "name": operational.requested_name,
                    }
                    if plan.operation
                    == ChangeOperation.CREATE_FULL_BACKUP
                    else {
                        "target": plan.target_id
                    }
                    if plan.operation
                    == ChangeOperation.CONTROLLED_RELOAD
                    else {
                        "slug": plan.target_id,
                        "action": "restart",
                    }
                    if plan.operation == ChangeOperation.RESTART_ADDON
                    else {"confirm": True}
                ),
            }
        elif plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION:
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
            action, active_challenge, _requested, _expires = (
                self._active_challenge_projection(plan)
            )
            if active_challenge != challenge_id or not self._active_challenge_matches(plan, calculated):
                raise GovernanceError(ErrorCode.EXTERNAL_APPROVAL_INVALID)
            nonce = secrets.token_urlsafe(32)
            digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            if (
                action
                == ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
                and plan.approval.elevated_risk_acknowledgement
                is not None
            ):
                acknowledgement = (
                    plan.approval.elevated_risk_acknowledgement
                )
                acknowledgement.csrf_digest = digest
                acknowledgement.csrf_issued_at = self._timestamp()
            else:
                plan.approval.csrf_digest = digest
                plan.approval.csrf_issued_at = self._timestamp()
            self._record(
                plan,
                "external_approval_viewed",
                "success",
                approval_action=action,
            )
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
        approval_action: str = ApprovalActionKind.PLAN_APPROVAL.value,
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
            self._require_policy_snapshot(plan)
            calculated = self.plan_hash(plan)
            approval = plan.approval
            (
                active_action,
                active_challenge,
                _requested_at,
                _expires_at,
            ) = self._active_challenge_projection(plan)
            if approval_action != active_action:
                self._reject_external_decision(
                    plan, ErrorCode.APPROVAL_SEQUENCE_FAILURE
                )
            if active_challenge != challenge_id or not self._active_challenge_matches(plan, calculated):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            if expected_plan_hash != calculated or approval.bound_plan_hash != calculated:
                self._reject_external_decision(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
            if (
                plan.policy_decision is None
                or approval.policy_decision_hash
                != plan.policy_decision.policy_decision_hash
                or approval.policy_class
                != plan.policy_decision.policy_class.value
            ):
                self._reject_external_decision(
                    plan, ErrorCode.POLICY_SNAPSHOT_MISMATCH
                )
            if approval_kind != approval.approval_kind:
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            csrf_digest = hashlib.sha256(csrf_nonce.encode("utf-8")).hexdigest()
            acknowledgement = approval.elevated_risk_acknowledgement
            expected_csrf = (
                acknowledgement.csrf_digest
                if active_action
                == ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
                and acknowledgement is not None
                else approval.csrf_digest
            )
            if not expected_csrf or not hmac.compare_digest(
                expected_csrf, csrf_digest
            ):
                self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)
            if (
                decision == "approve"
                and active_action
                == ApprovalActionKind.PLAN_APPROVAL.value
                and plan.contract_version
                == CONFIGURATION_PLAN_CONTRACT_VERSION
                and not self._configuration_approval_review_complete(plan)
            ):
                self._reject_external_decision(
                    plan, ErrorCode.EXTERNAL_APPROVAL_INVALID
                )
            if (
                active_action
                == ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
                and acknowledgement is not None
            ):
                acknowledgement.csrf_digest = None
                acknowledgement.csrf_issued_at = None
            else:
                approval.csrf_digest = None
                approval.csrf_issued_at = None
            principal = (approver_principal or DEFAULT_APPROVER_PRINCIPAL)[:160]
            if (
                active_action
                == ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
                and (
                    acknowledgement is None
                    or approval.approver_principal != principal
                )
            ):
                self._record(
                    plan,
                    "elevated_risk_acknowledgement_failed",
                    "rejected",
                    error_code=(
                        ErrorCode.APPROVAL_PRINCIPAL_MISMATCH.value
                    ),
                    approval_action=active_action,
                )
                raise GovernanceError(
                    ErrorCode.APPROVAL_PRINCIPAL_MISMATCH
                )
            if decision == "approve":
                if active_action == ApprovalActionKind.PLAN_APPROVAL.value:
                    if (
                        plan.policy_decision.policy_class
                        == ApprovalPolicyClass.ELEVATED_ADMIN
                        and principal == DEFAULT_APPROVER_PRINCIPAL
                    ):
                        self._reject_external_decision(
                            plan,
                            ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
                        )
                    approval.state = ApprovalState.APPROVED
                    approval.approved_at = self._timestamp()
                    approval.approval_expires_at = plan.expires_at
                    approval.channel = APPROVAL_CHANNEL
                    approval.approver_principal = principal
                    approval.principal_separation_enforced = True
                    if (
                        plan.policy_decision.policy_class
                        == ApprovalPolicyClass.ELEVATED_ADMIN
                    ):
                        if acknowledgement is None:
                            self._reject_external_decision(
                                plan,
                                ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                            )
                        requested_at = self._timestamp()
                        acknowledgement.state = (
                            ApprovalState.EXTERNAL_PENDING
                        )
                        acknowledgement.challenge_id = (
                            secrets.token_urlsafe(24)
                        )
                        acknowledgement.challenge_requested_at = (
                            requested_at
                        )
                        acknowledgement.authority_version = (
                            APPROVAL_AUTHORITY_VERSION
                        )
                        acknowledgement.bound_plan_hash = calculated
                        acknowledgement.policy_decision_hash = (
                            plan.policy_decision.policy_decision_hash
                        )
                        acknowledgement.policy_class = (
                            plan.policy_decision.policy_class.value
                        )
                        acknowledgement.risk_delta = (
                            plan.policy_decision.risk_delta.value
                        )
                        acknowledgement.physical_consequence = (
                            plan.policy_decision.physical_consequence.value
                        )
                        acknowledgement.challenge_expires_at = min(
                            self.now() + APPROVAL_CHALLENGE_TTL,
                            datetime.fromisoformat(plan.expires_at),
                        ).isoformat()
                        approval.bundle_state = (
                            "pending_elevated_risk_acknowledgement"
                        )
                        approval.same_principal_confirmed = None
                        self._record(
                            plan,
                            "external_approval_granted",
                            "success",
                            approval_principal=principal,
                            approval_action=active_action,
                        )
                        self._record(
                            plan,
                            "elevated_risk_acknowledgement_requested",
                            "success",
                            approval_action=(
                                ApprovalActionKind.
                                ELEVATED_RISK_ACKNOWLEDGEMENT.value
                            ),
                        )
                        return self._approval_pending_response(plan)
                    approval.bundle_state = "fully_approved"
                    if approval.approval_kind == "apply":
                        plan.status = PlanStatus.APPROVED
                    else:
                        plan.rollback.approved_at = approval.approved_at
                    self._record(
                        plan,
                        "external_approval_granted",
                        "success",
                        approval_principal=principal,
                        approval_action=active_action,
                    )
                    return {
                        "status": "approved",
                        "plan_id": plan.plan_id,
                        "approval_kind": approval.approval_kind,
                        "approval_action": active_action,
                        "approval_bundle_state": "fully_approved",
                    }

                if acknowledgement is None:
                    self._reject_external_decision(
                        plan, ErrorCode.APPROVAL_SEQUENCE_FAILURE
                    )
                acknowledgement.state = ApprovalState.APPROVED
                acknowledgement.granted_at = self._timestamp()
                acknowledgement.approver_principal = principal
                approval.same_principal_confirmed = True
                approval.bundle_state = "fully_approved"
                if approval.approval_kind == "apply":
                    plan.status = PlanStatus.APPROVED
                else:
                    plan.rollback.approved_at = approval.approved_at
                self._record(
                    plan,
                    "elevated_risk_acknowledgement_granted",
                    "success",
                    approval_principal=principal,
                    approval_action=active_action,
                )
                return {
                    "status": "approved",
                    "plan_id": plan.plan_id,
                    "approval_kind": approval.approval_kind,
                    "approval_action": active_action,
                    "approval_bundle_state": "fully_approved",
                }
            if decision == "reject":
                approval.state = ApprovalState.REJECTED
                approval.bundle_state = "rejected"
                approval.channel = APPROVAL_CHANNEL
                approval.approver_principal = principal
                approval.principal_separation_enforced = True
                if acknowledgement is not None:
                    acknowledgement.state = ApprovalState.REJECTED
                    acknowledgement.approver_principal = principal
                plan.status = PlanStatus.REJECTED
                self._record(
                    plan,
                    "external_approval_rejected",
                    "rejected",
                    error_code=ErrorCode.CHANGE_PLAN_REJECTED.value,
                    approval_principal=principal,
                    approval_action=active_action,
                )
                return {
                    "status": "rejected",
                    "plan_id": plan.plan_id,
                    "approval_kind": approval.approval_kind,
                    "approval_action": active_action,
                }
            self._reject_external_decision(plan, ErrorCode.EXTERNAL_APPROVAL_INVALID)

    def _reject_external_decision(self, plan: ChangePlan, code: ErrorCode) -> None:
        action = self._active_challenge_projection(plan)[0]
        self._record(
            plan,
            "external_approval_decision_failed",
            "rejected",
            error_code=code.value,
            approval_action=action,
        )
        raise GovernanceError(code)

    def _terminal_task_apply_result(
        self, task: ExecutionTask, plan: ChangePlan
    ) -> dict[str, Any]:
        self._record_task_event(
            task,
            "duplicate_apply_prevented",
            changes={},
        )
        if task.state != ExecutionTaskState.SUCCEEDED_VERIFIED:
            raise GovernanceError(
                (
                    ErrorCode.EXECUTION_TASK_INVALID_STATE
                    if task.state
                    == ExecutionTaskState.CANCELLED_PRE_DISPATCH
                    else ErrorCode.DUPLICATE_APPLY_ATTEMPT
                ),
                details={
                    "task_id": task.task_id,
                    "task_state": task.state.value,
                    "provider_dispatch_occurred": (
                        self._task_is_dispatched(task)
                    ),
                },
            )
        return {
            "status": "already_applied",
            "task_id": task.task_id,
            "task_state": task.state.value,
            "task_reused": True,
            "redispatch_performed": False,
            "provider_dispatch_occurred": self._task_is_dispatched(task),
            "terminal_outcome": task.terminal_outcome,
            "task": self._public_task(task),
            "plan": self._public(plan, include_configs=False),
        }

    def _task_deadline_expired(self, task: ExecutionTask) -> bool:
        if (
            not self._task_is_dispatched(task)
            or task.maximum_post_dispatch_deadline is None
        ):
            return False
        try:
            return self.now() >= parse_task_timestamp(
                task.maximum_post_dispatch_deadline
            )
        except ValueError:
            return True

    def _manual_review_task(
        self,
        task: ExecutionTask,
        reason: str,
        plan: ChangePlan | None,
    ) -> None:
        if task.state in TERMINAL_TASK_STATES:
            return
        if task.state not in {
            ExecutionTaskState.DISPATCHING,
            ExecutionTaskState.OBSERVING,
            ExecutionTaskState.VERIFYING,
        }:
            # A contradictory pre-dispatch task cannot be promoted into a
            # dispatched history. Fail it before dispatch instead.
            self._record_task_event(
                task,
                "preflight_failed",
                new_state=ExecutionTaskState.FAILED_PRE_DISPATCH,
                changes={
                    "completed_at": self._timestamp(),
                    "terminal_outcome": "failed_pre_dispatch",
                    "last_error": {"failure_category": reason},
                    "legacy_projection": {
                        **task.legacy_projection,
                        "plan_status": (
                            plan.status.value if plan is not None else "missing"
                        ),
                        "execution_outcome": (
                            plan.execution_outcome
                            if plan is not None
                            else "unavailable"
                        ),
                    },
                },
                result_status="failure",
                error_code=reason,
            )
            return
        self._record_task_event(
            task,
            "manual_review_required",
            new_state=ExecutionTaskState.MANUAL_REVIEW_REQUIRED,
            changes={
                "completed_at": self._timestamp(),
                "terminal_outcome": "manual_review_required",
                "manual_review_reason": reason,
                "last_error": {"failure_category": reason},
                "legacy_projection": {
                    **task.legacy_projection,
                    "plan_status": (
                        plan.status.value if plan is not None else "missing"
                    ),
                    "execution_outcome": (
                        plan.execution_outcome
                        if plan is not None
                        else "unavailable"
                    ),
                },
            },
            result_status="partial",
            error_code=reason,
        )

    def _project_task_after_apply(
        self,
        task: ExecutionTask,
        plan: ChangePlan,
        *,
        error_code: str | None = None,
    ) -> None:
        if task.state not in TERMINAL_TASK_STATES:
            self._reconcile_configuration_task_response_evidence(
                task, plan
            )
        if task.state in TERMINAL_TASK_STATES:
            return
        if self._configuration_response_projection_mismatch(task, plan):
            # New authority-v3 executions with affirmative provider-response
            # evidence must not become successful while the durable task says
            # otherwise. Historical terminal tasks are never inspected here.
            self._manual_review_task(
                task,
                PROVIDER_RESPONSE_EVIDENCE_INCONSISTENT,
                plan,
            )
            return
        dispatched = self._task_is_dispatched(task)
        legacy = {
            **task.legacy_projection,
            "plan_status": plan.status.value,
            "execution_outcome": plan.execution_outcome,
        }
        if plan.status == PlanStatus.APPLIED:
            if not dispatched:
                self._record_task_event(
                    task,
                    "task_completed",
                    new_state=ExecutionTaskState.SUCCEEDED_VERIFIED,
                    changes={
                        "completed_at": self._timestamp(),
                        "terminal_outcome": (
                            plan.execution_outcome
                            or "already_desired_verified"
                        ),
                        "verification_summary": {
                            **task.verification_summary,
                            "status": "verified",
                            "plan_status": plan.status.value,
                            "provider_dispatch_occurred": False,
                            **self._configuration_task_verification_evidence(
                                plan
                            ),
                        },
                        "legacy_projection": legacy,
                    },
                )
                return
            if task.state in {
                ExecutionTaskState.DISPATCHING,
                ExecutionTaskState.OBSERVING,
            }:
                self._record_task_event(
                    task,
                    "verification_started",
                    new_state=ExecutionTaskState.VERIFYING,
                    changes={
                        "verification_summary": {
                            **task.verification_summary,
                            "status": "verifying",
                        }
                    },
                )
            self._record_task_event(
                task,
                "task_completed",
                new_state=ExecutionTaskState.SUCCEEDED_VERIFIED,
                changes={
                    "completed_at": self._timestamp(),
                    "terminal_outcome": (
                        plan.operational.final_outcome
                        if plan.operational is not None
                        else plan.execution_outcome or "applied_verified"
                    ),
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "verified",
                        "plan_status": plan.status.value,
                        "provider_response_received": bool(
                            task.provider_attempts
                            and task.provider_attempts[-1].get(
                                "response_received"
                            )
                            is True
                        ),
                        **self._configuration_task_verification_evidence(
                            plan
                        ),
                    },
                    "legacy_projection": legacy,
                },
            )
            return
        if not dispatched:
            if error_code is None:
                return
            self._record_task_event(
                task,
                "preflight_failed",
                new_state=ExecutionTaskState.FAILED_PRE_DISPATCH,
                changes={
                    "completed_at": self._timestamp(),
                    "terminal_outcome": "failed_pre_dispatch",
                    "last_error": {"error_code": error_code},
                    "legacy_projection": legacy,
                },
                result_status="failure",
                error_code=error_code,
            )
            return
        if plan.status in {
            PlanStatus.FAILED,
            PlanStatus.VERIFICATION_FAILED,
        }:
            if task.state in {
                ExecutionTaskState.DISPATCHING,
                ExecutionTaskState.OBSERVING,
            }:
                self._record_task_event(
                    task,
                    "verification_started",
                    new_state=ExecutionTaskState.VERIFYING,
                    changes={},
                )
            self._record_task_event(
                task,
                "task_completed",
                new_state=ExecutionTaskState.FAILED_POST_DISPATCH,
                changes={
                    "completed_at": self._timestamp(),
                    "terminal_outcome": "failed_post_dispatch",
                    "last_error": {
                        "error_code": (
                            error_code
                            or (plan.failure_information or {}).get(
                                "error_code"
                            )
                            or "verification_failed"
                        )
                    },
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "failed",
                        "provider_response_received": bool(
                            task.provider_attempts
                            and task.provider_attempts[-1].get(
                                "response_received"
                            )
                            is True
                        ),
                        **self._configuration_task_verification_evidence(
                            plan
                        ),
                    },
                    "legacy_projection": legacy,
                },
                result_status="failure",
                error_code=error_code,
            )
            return
        if task.state == ExecutionTaskState.DISPATCHING:
            self._record_task_event(
                task,
                "verification_started",
                new_state=ExecutionTaskState.OBSERVING,
                changes={
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "pending",
                        "provider_response_received": bool(
                            task.provider_attempts
                            and task.provider_attempts[-1].get(
                                "response_received"
                            )
                            is True
                        ),
                    },
                    "last_error": (
                        {"error_code": error_code}
                        if error_code
                        else task.last_error
                    ),
                    "legacy_projection": legacy,
                },
                result_status="partial",
                error_code=error_code,
            )
        elif task.state == ExecutionTaskState.VERIFYING:
            self._record_task_event(
                task,
                "verification_evidence_updated",
                new_state=ExecutionTaskState.OBSERVING,
                changes={
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "pending",
                    },
                    "last_error": (
                        {"error_code": error_code}
                        if error_code
                        else task.last_error
                    ),
                    "legacy_projection": legacy,
                },
                result_status="partial",
                error_code=error_code,
            )

    @staticmethod
    def _decorate_task_result(
        result: dict[str, Any],
        task: ExecutionTask | None,
        *,
        reused: bool,
    ) -> dict[str, Any]:
        if task is None:
            return result
        return {
            **result,
            "task_id": task.task_id,
            "task_state": task.state.value,
            "task_reused": reused,
            "execution_task": {
                "task_id": task.task_id,
                "state": task.state.value,
                "terminal_outcome": task.terminal_outcome,
                "provider_dispatch_occurred": bool(
                    task.dispatched_at or task.provider_attempts
                ),
            },
        }

    async def apply(self, plan_id: str, expected_plan_hash: str = "") -> dict[str, Any]:
        plan_lock = self._plan_locks.setdefault(plan_id, asyncio.Lock())
        async with plan_lock:
            plan = self._load(plan_id)
            task, reused = self._resolve_task_for_apply(
                plan, expected_plan_hash
            )
            if (
                task is not None
                and task.state in TERMINAL_TASK_STATES
                and task.state
                != ExecutionTaskState.SUCCEEDED_VERIFIED
            ):
                return self._terminal_task_apply_result(task, plan)
            if task is not None and plan.status in {
                PlanStatus.APPLIED,
                PlanStatus.FAILED,
                PlanStatus.VERIFICATION_FAILED,
            }:
                # A crash may persist the operation-specific terminal plan
                # evidence before its task projection. Recover that
                # authoritative terminal result before applying the 24-hour
                # unresolved-evidence deadline.
                self._project_task_after_apply(task, plan)
                task = self._load_task(task.task_id)
                if (
                    task.state in TERMINAL_TASK_STATES
                    and task.state
                    != ExecutionTaskState.SUCCEEDED_VERIFIED
                ):
                    return self._terminal_task_apply_result(task, plan)
            if (
                task is not None
                and task.state not in TERMINAL_TASK_STATES
                and self._task_deadline_expired(task)
            ):
                self._manual_review_task(
                    task, "maximum_post_dispatch_deadline_exceeded", plan
                )
                return self._terminal_task_apply_result(task, plan)
            if (
                task is not None
                and task.state == ExecutionTaskState.CREATED
            ):
                self._record_task_event(
                    task,
                    "preflight_started",
                    new_state=ExecutionTaskState.PREFLIGHT,
                    changes={"started_at": self._timestamp()},
                )
            elif (
                task is not None
                and reused
                and self._task_is_dispatched(task)
            ):
                self._record_task_event(
                    task,
                    "duplicate_apply_prevented",
                    changes={},
                )
            if task is not None:
                self._active_task_ids_by_plan[plan.plan_id] = task.task_id
            try:
                if (
                    plan.contract_version
                    == OPERATIONAL_PLAN_CONTRACT_VERSION
                ):
                    if (
                        plan.operation
                        == ChangeOperation.CREATE_FULL_BACKUP
                    ):
                        result = await self._apply_operational_backup(
                            plan, expected_plan_hash
                        )
                    else:
                        result = await self._apply_operational_lifecycle(
                            plan, expected_plan_hash
                        )
                elif (
                    plan.contract_version
                    == CONFIGURATION_PLAN_CONTRACT_VERSION
                ):
                    result = await self._apply_configuration_plan(
                        plan, expected_plan_hash
                    )
                else:
                    # Reuse an already-present legacy bare-ID lock for
                    # compatibility with existing in-process callers, then
                    # publish the typed key used by both contract versions.
                    legacy_target_lock = self._target_locks.get(
                        plan.target_id
                    )
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
                        raise GovernanceError(
                            ErrorCode.CHANGE_IN_PROGRESS
                        )
                    async with target_lock:
                        result = await self._apply_locked(
                            plan, expected_plan_hash
                        )
            except GovernanceError as exc:
                if task is not None:
                    persisted_plan = self._load(plan.plan_id)
                    task = self._load_task(task.task_id)
                    self._project_task_after_apply(
                        task,
                        persisted_plan,
                        error_code=exc.code.value,
                    )
                raise
            finally:
                self._active_task_ids_by_plan.pop(plan.plan_id, None)
            if task is not None:
                persisted_plan = self._load(plan.plan_id)
                task = self._load_task(task.task_id)
                self._project_task_after_apply(task, persisted_plan)
                task = self._load_task(task.task_id)
            return self._decorate_task_result(
                result, task, reused=reused
            )

    async def _apply_operational_lifecycle(
        self, plan: ChangePlan, expected_plan_hash: str
    ) -> dict[str, Any]:
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        self._require_current_normalization(plan)
        operational = plan.operational
        if operational is None or self.lifecycle_gateway is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        calculated = self.plan_hash(plan)
        if not expected_plan_hash or expected_plan_hash != calculated:
            self._reject_apply(
                plan,
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={
                    "hash_validation": {
                        "performed": bool(expected_plan_hash),
                        "result": (
                            "mismatch"
                            if expected_plan_hash
                            else "not_supplied"
                        ),
                    }
                },
            )
        if plan.status == PlanStatus.APPLIED:
            self._record(
                plan,
                f"{plan.operation.value}_apply_attempted",
                "success",
            )
            self._record(
                plan,
                f"{plan.operation.value}_no_redispatch_prevented",
                "success",
            )
            return {
                "status": "already_applied",
                "provider_dispatch_occurred": True,
                "redispatch_performed": False,
                "plan": self._public(plan, include_configs=False),
            }
        if (
            plan.status == PlanStatus.APPLYING
            and operational.dispatch.get("attempt_count") == 1
        ):
            self._record(
                plan,
                f"{plan.operation.value}_apply_attempted",
                "success",
            )
            self._record(
                plan,
                f"{plan.operation.value}_no_redispatch_prevented",
                "success",
            )
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            plan.execution_outcome = "verification_pending"
            operational.final_outcome = "verification_pending"
            operational.verification.status = "verification_pending"
            self._record(
                plan,
                f"{plan.operation.value}_dispatch_recovered",
                "partial",
                error_code=(
                    ErrorCode.OPERATIONAL_DISPATCH_INDETERMINATE.value
                ),
            )
            return await self._resume_lifecycle_verification(plan)
        if plan.status == PlanStatus.VERIFICATION_REQUIRED:
            self._record(
                plan,
                f"{plan.operation.value}_apply_attempted",
                "success",
            )
            self._record(
                plan,
                f"{plan.operation.value}_no_redispatch_prevented",
                "success",
            )
            return await self._resume_lifecycle_verification(plan)
        if plan.status in {
            PlanStatus.FAILED,
            PlanStatus.VERIFICATION_FAILED,
        } or plan.approval.state == ApprovalState.CONSUMED:
            raise GovernanceError(ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        if plan.status == PlanStatus.REJECTED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_REJECTED)
        self._require_dispatch_approval(plan)
        if plan.approval.bound_plan_hash != calculated:
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)
        self._record(
            plan,
            f"{plan.operation.value}_apply_attempted",
            "success",
        )

        target_lock = self._target_locks.setdefault(
            (
                f"operational_{plan.operation.value}",
                plan.target_id,
            ),
            asyncio.Lock(),
        )
        if target_lock.locked():
            self._reject_apply(plan, ErrorCode.CHANGE_IN_PROGRESS)
        async with target_lock:
            try:
                fresh = await self.lifecycle_gateway.planning_evidence(
                    plan.operation.value, plan.target_id
                )
            except LifecycleGatewayError as exc:
                code = self._lifecycle_error_code(
                    exc.category, dispatched=False
                )
                self._record(
                    plan,
                    f"{plan.operation.value}_preflight_failed",
                    "failure",
                    error_code=code.value,
                    failure_category=exc.category,
                    failure_stage="pre_dispatch",
                )
                raise GovernanceError(
                    code,
                    details=_operational_failure_details(
                        exc.category, dispatched=False
                    ),
                ) from None
            fresh_provider = fresh.get("provider")
            fresh_baseline = fresh.get("baseline")
            if (
                not isinstance(fresh_provider, dict)
                or not isinstance(fresh_baseline, dict)
                or not self._lifecycle_preflight_matches(
                    plan, fresh_provider, fresh_baseline
                )
            ):
                self._record(
                    plan,
                    f"{plan.operation.value}_preflight_failed",
                    "failure",
                    error_code=ErrorCode.STALE_TARGET_STATE.value,
                    failure_category="stale_target_state",
                    failure_stage="pre_dispatch",
                )
                raise GovernanceError(
                    ErrorCode.STALE_TARGET_STATE,
                    details=_operational_failure_details(
                        "stale_target_state", dispatched=False
                    ),
                )
            apply_validation = fresh_baseline.get(
                "configuration_validation"
            )
            if plan.operation in {
                ChangeOperation.CONTROLLED_RELOAD,
                ChangeOperation.RESTART_HOME_ASSISTANT,
            }:
                operational.dispatch["apply_validation"] = apply_validation
                operational.dispatch["validation_changed_since_planning"] = (
                    _configuration_validation_changed(
                        operational.baseline.get(
                            "configuration_validation"
                        ),
                        apply_validation,
                    )
                )
                if (
                    not isinstance(apply_validation, dict)
                    or apply_validation.get("status") != "valid"
                ):
                    self._record(
                        plan,
                        f"{plan.operation.value}_validation_failed",
                        "failure",
                        error_code=(
                            ErrorCode.OPERATIONAL_VALIDATION_FAILED.value
                        ),
                        failure_category="configuration_invalid",
                        failure_stage="pre_dispatch",
                    )
                    raise GovernanceError(
                        ErrorCode.OPERATIONAL_VALIDATION_FAILED,
                        details=_operational_failure_details(
                            "configuration_invalid", dispatched=False
                        ),
                    )

            async def before_dispatch() -> None:
                self._require_policy_snapshot(plan)
                self._require_dispatch_approval(plan)
                if operational.dispatch.get("attempt_count") not in {
                    0,
                    None,
                }:
                    raise GovernanceError(
                        ErrorCode.DUPLICATE_APPLY_ATTEMPT
                    )
                self._consume_approval_bundle(plan)
                plan.status = PlanStatus.APPLYING
                plan.execution_outcome = "dispatching"
                plan.apply_request_id = current_request_id()
                attempted_at = self._timestamp()
                dispatch_record = {
                    "attempt_count": 1,
                    "dispatched": True,
                    "request_id": plan.apply_request_id,
                    "attempted_at": attempted_at,
                }
                if (
                    plan.operation
                    == ChangeOperation.RESTART_HOME_ASSISTANT
                ):
                    parsed_attempted_at = (
                        _parse_governance_timestamp(attempted_at)
                    )
                    if parsed_attempted_at is None:
                        raise GovernanceError(
                            ErrorCode.INTERNAL_INVARIANT_VIOLATION
                        )
                    dispatch_record[
                        "outage_observation_deadline"
                    ] = (
                        parsed_attempted_at
                        + timedelta(
                            seconds=(
                                RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
                            )
                        )
                    ).isoformat()
                operational.dispatch.update(dispatch_record)
                self._record(
                    plan,
                    f"{plan.operation.value}_dispatch_recorded",
                    "success",
                )

            try:
                dispatch = await self._dispatch_lifecycle(
                    plan, before_dispatch=before_dispatch
                )
                operational.dispatch.update(
                    {
                        "provider_response_received": (
                            dispatch.provider_response_received
                        ),
                        "provider_response_at": self._timestamp(),
                        "provider_result": dispatch.response,
                        "restart_dispatch_confirmed": (
                            plan.operation
                            == ChangeOperation.RESTART_HOME_ASSISTANT
                            and dispatch.provider_response_received
                        ),
                    }
                )
                self._record(
                    plan,
                    f"{plan.operation.value}_provider_completed",
                    "success",
                )
            except LifecycleGatewayError as exc:
                if not exc.dispatched:
                    code = self._lifecycle_error_code(
                        exc.category, dispatched=False
                    )
                    plan.failure_information = {
                        "error_code": code.value,
                        **_operational_failure_details(
                            exc.category, dispatched=False
                        ),
                    }
                    self._record(
                        plan,
                        f"{plan.operation.value}_dispatch_rejected",
                        "failure",
                        error_code=code.value,
                        failure_category=exc.category,
                        failure_stage="pre_dispatch",
                    )
                    raise GovernanceError(
                        code,
                        details=_operational_failure_details(
                            exc.category, dispatched=False
                        ),
                    ) from None
                operational.dispatch["provider_response_received"] = False
                operational.dispatch["failure_category"] = exc.category
                plan.status = PlanStatus.VERIFICATION_REQUIRED
                plan.execution_outcome = "verification_pending"
                operational.final_outcome = "verification_pending"
                self._record(
                    plan,
                    f"{plan.operation.value}_dispatch_indeterminate",
                    "partial",
                    error_code=(
                        ErrorCode.OPERATIONAL_DISPATCH_INDETERMINATE.value
                    ),
                    failure_category=exc.category,
                    failure_stage="post_dispatch",
                )
            return await self._resume_lifecycle_verification(plan)

    async def _dispatch_lifecycle(
        self,
        plan: ChangePlan,
        *,
        before_dispatch: Callable[[], None | Awaitable[None]],
    ):
        if self.lifecycle_gateway is None:
            raise LifecycleGatewayError("provider_unavailable")
        if plan.operation == ChangeOperation.CONTROLLED_RELOAD:
            return await self.lifecycle_gateway.dispatch_reload(
                plan.target_id, before_dispatch=before_dispatch
            )
        if plan.operation == ChangeOperation.RESTART_ADDON:
            return await self.lifecycle_gateway.dispatch_addon_restart(
                plan.target_id, before_dispatch=before_dispatch
            )
        if plan.operation == ChangeOperation.RESTART_HOME_ASSISTANT:
            return await self.lifecycle_gateway.dispatch_home_assistant_restart(
                before_dispatch=before_dispatch
            )
        raise LifecycleGatewayError("invalid_request")

    async def _resume_lifecycle_verification(
        self, plan: ChangePlan
    ) -> dict[str, Any]:
        operational = plan.operational
        if (
            operational is None
            or self.lifecycle_gateway is None
            or operational.dispatch.get("attempt_count") != 1
        ):
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        verification = operational.verification
        verification.attempt_count += 1
        verification.checked_at = self._timestamp()
        self._record(
            plan,
            f"{plan.operation.value}_verification_started",
            "success",
        )
        try:
            if plan.operation == ChangeOperation.CONTROLLED_RELOAD:
                result = await self.lifecycle_gateway.verify_reload(
                    plan.target_id
                )
            elif plan.operation == ChangeOperation.RESTART_ADDON:
                result = (
                    await self.lifecycle_gateway.verify_addon_restart(
                        plan.target_id,
                        baseline=operational.baseline,
                        provider_response_received=bool(
                            operational.dispatch.get(
                                "provider_response_received"
                            )
                        ),
                        provider_evidence=(
                            operational.provider_capability_evidence
                        ),
                    )
                )
            elif (
                plan.operation
                == ChangeOperation.RESTART_HOME_ASSISTANT
            ):
                outage_state = (
                    self._home_assistant_outage_evidence_state(
                        plan,
                        operational.verification.evidence,
                    )
                )
                result = (
                    await self.lifecycle_gateway.verify_home_assistant_restart(
                        baseline=operational.baseline,
                        restart_dispatch_confirmed=bool(
                            operational.dispatch.get(
                                "restart_dispatch_confirmed"
                            )
                        ),
                        authoritative_outage_observed=bool(
                            outage_state["authoritative"]
                        ),
                        outage_observation_window_open=(
                            outage_state["window_status"] == "open"
                        ),
                        outage_observation_deadline=(
                            operational.dispatch.get(
                                "outage_observation_deadline"
                            )
                        ),
                    )
                )
            else:
                raise LifecycleGatewayError("invalid_request")
        except LifecycleGatewayError as exc:
            result = {
                "status": "pending",
                "mismatch_fields": ["provider_unavailable"],
                "evidence": {
                    "failure_category": exc.category,
                    "redispatch_performed": False,
                },
            }
        if plan.operation == ChangeOperation.RESTART_HOME_ASSISTANT:
            result = self._merge_home_assistant_restart_verification(
                plan, result
            )
        verification.status = str(result.get("status") or "failed")
        verification.operation_completed = (
            verification.status == "verified"
        )
        verification.inventory_readable = (
            verification.status in {"verified", "failed"}
        )
        evidence = dict(result.get("evidence") or {})
        if evidence.get("expected_disruption_observed") is True:
            operational.dispatch["expected_disruption_observed"] = True
        verification.mismatch_fields = [
            str(value)[:160]
            for value in (result.get("mismatch_fields") or [])[:20]
        ]
        verification.evidence = evidence
        if verification.status == "verified":
            plan.status = PlanStatus.APPLIED
            plan.applied_at = self._timestamp()
            plan.execution_outcome = "applied_verified"
            operational.final_outcome = (
                f"{plan.operation.value}_and_verified"
            )
            self._record(
                plan,
                f"{plan.operation.value}_verified",
                "success",
            )
            return {
                "status": "applied",
                "provider_dispatch_occurred": True,
                "provider_response_received": bool(
                    operational.dispatch.get(
                        "provider_response_received"
                    )
                ),
                "expected_disruption_observed": bool(
                    operational.dispatch.get(
                        "expected_disruption_observed"
                    )
                ),
                "redispatch_performed": False,
                "fallback": "none",
                "fallback_occurred": False,
                "verification": verification.__dict__,
                "rollback_available": False,
                "plan": self._public(plan, include_configs=False),
            }
        if verification.status == "pending":
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            plan.execution_outcome = "verification_pending"
            operational.final_outcome = "verification_pending"
            self._record(
                plan,
                f"{plan.operation.value}_verification_deferred",
                "partial",
                error_code=(
                    ErrorCode.OPERATIONAL_VERIFICATION_PENDING.value
                ),
            )
            return {
                "status": "verification_pending",
                "provider_dispatch_occurred": True,
                "redispatch_performed": False,
                "fallback": "none",
                "fallback_occurred": False,
                "verification": verification.__dict__,
                "plan": self._public(plan, include_configs=False),
            }
        plan.status = PlanStatus.VERIFICATION_FAILED
        plan.execution_outcome = "verification_failed"
        operational.final_outcome = "verification_failed"
        plan.failure_information = {
            "error_code": (
                ErrorCode.OPERATIONAL_VERIFICATION_FAILED.value
            ),
            "mismatch_fields": verification.mismatch_fields,
        }
        self._record(
            plan,
            f"{plan.operation.value}_verification_failed",
            "failure",
            error_code=ErrorCode.OPERATIONAL_VERIFICATION_FAILED.value,
        )
        raise GovernanceError(
            ErrorCode.OPERATIONAL_VERIFICATION_FAILED,
            details={
                "provider_dispatch_occurred": True,
                "failure_stage": "verification",
                "fallback": "none",
                "fallback_occurred": False,
            },
        )

    def _home_assistant_outage_evidence_state(
        self,
        plan: ChangePlan,
        evidence: dict[str, Any],
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Validate one complete persisted Core-outage evidence record."""

        operational = plan.operational
        if operational is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        dispatch = operational.dispatch
        attempted_at = _parse_governance_timestamp(
            dispatch.get("attempted_at")
        )
        deadline_value = dispatch.get("outage_observation_deadline")
        deadline = _parse_governance_timestamp(deadline_value)
        checked_at = _parse_governance_timestamp(
            now if now is not None else self._timestamp()
        )
        expected_deadline = (
            attempted_at
            + timedelta(
                seconds=(
                    RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
                )
            )
            if attempted_at is not None
            else None
        )
        deadline_valid = (
            isinstance(deadline_value, str)
            and deadline is not None
            and expected_deadline is not None
            and deadline == expected_deadline
            and deadline_value == expected_deadline.isoformat()
        )
        attempt_count = dispatch.get("attempt_count")
        first_unavailable = _parse_governance_timestamp(
            evidence.get("first_unavailable_at")
        )
        last_unavailable = _parse_governance_timestamp(
            evidence.get("last_unavailable_at")
        )
        unavailable_count = evidence.get(
            "unavailable_observation_count"
        )
        sources = _bounded_restart_evidence_sources(
            evidence.get("restart_evidence_sources")
        )
        authoritative = (
            evidence.get("outage_observed") is True
            and evidence.get("home_assistant_core_unavailable") is True
            and plan.approval.state == ApprovalState.CONSUMED
            and dispatch.get("dispatched") is True
            and isinstance(attempt_count, int)
            and not isinstance(attempt_count, bool)
            and attempt_count >= 1
            and attempted_at is not None
            and deadline_valid
            and first_unavailable is not None
            and last_unavailable is not None
            and isinstance(unavailable_count, int)
            and not isinstance(unavailable_count, bool)
            and 1
            <= unavailable_count
            <= MAX_RESTART_OUTAGE_OBSERVATIONS
            and (
                "home_assistant_core_connection_probe"
                in sources
            )
            and evidence.get("outage_failure_category")
            in HOME_ASSISTANT_OUTAGE_CATEGORIES
            and attempted_at <= first_unavailable
            and first_unavailable <= last_unavailable
            and last_unavailable <= deadline
            and (
                checked_at is None
                or last_unavailable <= checked_at
            )
        )
        if authoritative:
            window_status = "qualified"
            reason = None
        elif not deadline_valid:
            window_status = "invalid"
            reason = "restart_evidence_contract_invalid"
        elif checked_at is not None and checked_at > deadline:
            window_status = "expired"
            reason = "restart_evidence_window_expired"
        else:
            window_status = "open"
            reason = "restart_evidence"
        return {
            "authoritative": authoritative,
            "window_status": window_status,
            "reason": reason,
            "attempted_at": (
                attempted_at.isoformat()
                if attempted_at is not None
                else None
            ),
            "deadline": (
                deadline.isoformat() if deadline is not None else None
            ),
            "sources": sources,
            "first_unavailable_at": (
                first_unavailable.isoformat()
                if first_unavailable is not None
                else None
            ),
            "last_unavailable_at": (
                last_unavailable.isoformat()
                if last_unavailable is not None
                else None
            ),
            "unavailable_observation_count": (
                unavailable_count if authoritative else 0
            ),
            "outage_failure_category": (
                evidence.get("outage_failure_category")
                if authoritative
                else None
            ),
        }

    def _home_assistant_reconnection_evidence_state(
        self,
        evidence: dict[str, Any],
        *,
        outage_state: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        """Validate an explicit successful identity read after the outage."""

        reconnected_at = _parse_governance_timestamp(
            evidence.get("reconnected_at")
        )
        first_unavailable = _parse_governance_timestamp(
            outage_state.get("first_unavailable_at")
        )
        checked_at = _parse_governance_timestamp(now)
        sources = _bounded_restart_evidence_sources(
            evidence.get("restart_evidence_sources")
        )
        authoritative = (
            outage_state.get("authoritative") is True
            and evidence.get("home_assistant_reconnected") is True
            and reconnected_at is not None
            and first_unavailable is not None
            and reconnected_at > first_unavailable
            and (
                checked_at is None
                or reconnected_at <= checked_at
            )
            and "home_assistant_core_reconnected" in sources
        )
        return {
            "authoritative": authoritative,
            "reconnected_at": (
                reconnected_at.isoformat()
                if authoritative and reconnected_at is not None
                else None
            ),
        }

    def _merge_home_assistant_restart_verification(
        self,
        plan: ChangePlan,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize and monotonically merge authoritative Core evidence."""

        operational = plan.operational
        if operational is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        verification = operational.verification
        persisted = dict(verification.evidence)
        current = dict(result.get("evidence") or {})
        observed_at = self._timestamp()
        persisted_state = self._home_assistant_outage_evidence_state(
            plan, persisted, now=observed_at
        )
        persisted_reconnection_state = (
            self._home_assistant_reconnection_evidence_state(
                persisted,
                outage_state=persisted_state,
                now=observed_at,
            )
        )
        current_sources = _bounded_restart_evidence_sources(
            current.get("restart_evidence_sources")
        )
        current_observed_at = current.get("outage_observed_at")
        current_candidate = {
            "outage_observed": current.get("outage_observed"),
            "home_assistant_core_unavailable": current.get(
                "home_assistant_core_unavailable"
            ),
            "first_unavailable_at": current_observed_at,
            "last_unavailable_at": current_observed_at,
            "unavailable_observation_count": 1,
            "outage_failure_category": current.get(
                "failure_category"
            ),
            "restart_evidence_sources": current_sources,
        }
        current_state = self._home_assistant_outage_evidence_state(
            plan,
            current_candidate,
            now=observed_at,
        )

        outage_fields = {
            "outage_observed",
            "expected_disruption_observed",
            "home_assistant_core_unavailable",
            "outage_observed_at",
            "first_unavailable_at",
            "last_unavailable_at",
            "unavailable_observation_count",
            "outage_failure_category",
            "last_unavailable_failure_category",
            "outage_window_status",
            "dispatch_attempted_at",
            "outage_observation_deadline",
        }
        recovery_fields = {
            "home_assistant_reconnected",
            "reconnected_at",
        }
        merged = dict(persisted)
        if not persisted_state["authoritative"]:
            for field in outage_fields | recovery_fields:
                merged.pop(field, None)
            merged["restart_evidence_sources"] = [
                source
                for source in persisted_state["sources"]
                if source
                not in {
                    "home_assistant_core_connection_probe",
                    "home_assistant_core_reconnected",
                }
            ]
            if persisted.get("outage_observed") is True:
                merged["outage_evidence_rejected_reason"] = (
                    persisted_state["reason"]
                )
        for field, value in current.items():
            if (
                field in outage_fields
                or field in recovery_fields
                or field == "restart_evidence_sources"
                or (
                    field == "failure_category"
                    and current.get("outage_observed") is True
                )
            ):
                continue
            if isinstance(value, bool) and isinstance(
                merged.get(field), bool
            ):
                merged[field] = bool(merged[field] or value)
            else:
                merged[field] = value

        if persisted_state["authoritative"]:
            merged.update(
                {
                    "outage_observed": True,
                    "expected_disruption_observed": True,
                    "home_assistant_core_unavailable": True,
                    "first_unavailable_at": persisted_state[
                        "first_unavailable_at"
                    ],
                    "last_unavailable_at": persisted_state[
                        "last_unavailable_at"
                    ],
                    "unavailable_observation_count": persisted_state[
                        "unavailable_observation_count"
                    ],
                    "outage_failure_category": persisted_state[
                        "outage_failure_category"
                    ],
                }
            )
            sources = list(persisted_state["sources"])
        else:
            sources = [
                source
                for source in _bounded_restart_evidence_sources(
                    merged.get("restart_evidence_sources")
                )
                if source
                not in {
                    "home_assistant_core_connection_probe",
                    "home_assistant_core_reconnected",
                }
            ]

        if current_state["authoritative"]:
            merged.update(
                {
                    "outage_observed": True,
                    "expected_disruption_observed": True,
                    "home_assistant_core_unavailable": True,
                    "first_unavailable_at": (
                        _earliest_governance_timestamp(
                            merged.get("first_unavailable_at"),
                            str(
                                current_state[
                                    "first_unavailable_at"
                                ]
                            ),
                        )
                    ),
                    "last_unavailable_at": (
                        _latest_governance_timestamp(
                            merged.get("last_unavailable_at"),
                            str(
                                current_state[
                                    "last_unavailable_at"
                                ]
                            ),
                        )
                    ),
                    "unavailable_observation_count": min(
                        MAX_RESTART_OUTAGE_OBSERVATIONS,
                        int(
                            merged.get(
                                "unavailable_observation_count"
                            )
                            or 0
                        )
                        + 1,
                    ),
                    "outage_failure_category": current_state[
                        "outage_failure_category"
                    ],
                }
            )
            for source in current_state["sources"]:
                if source not in sources:
                    sources.append(source)

        merged["restart_evidence_sources"] = sources[
            :MAX_RESTART_EVIDENCE_SOURCES
        ]
        normalized_state = self._home_assistant_outage_evidence_state(
            plan, merged, now=observed_at
        )
        merged["dispatch_attempted_at"] = operational.dispatch.get(
            "attempted_at"
        )
        merged["outage_observation_deadline"] = (
            operational.dispatch.get("outage_observation_deadline")
        )
        merged["outage_window_status"] = normalized_state[
            "window_status"
        ]
        merged["outage_observed"] = bool(
            normalized_state["authoritative"]
        )
        merged["expected_disruption_observed"] = bool(
            normalized_state["authoritative"]
        )
        if not normalized_state["authoritative"]:
            for field in (
                "home_assistant_core_unavailable",
                "first_unavailable_at",
                "last_unavailable_at",
                "unavailable_observation_count",
                "outage_failure_category",
            ):
                merged.pop(field, None)
            sources = [
                source
                for source in sources
                if source
                not in {
                    "home_assistant_core_connection_probe",
                    "home_assistant_core_reconnected",
                }
            ]
        current_reconnection_state = (
            self._home_assistant_reconnection_evidence_state(
                current,
                outage_state=normalized_state,
                now=observed_at,
            )
        )
        if (
            normalized_state["authoritative"]
            and persisted_reconnection_state["authoritative"]
        ):
            merged["home_assistant_reconnected"] = True
            merged["reconnected_at"] = persisted_reconnection_state[
                "reconnected_at"
            ]
            if "home_assistant_core_reconnected" not in sources:
                sources.append("home_assistant_core_reconnected")
        elif (
            normalized_state["authoritative"]
            and current_reconnection_state["authoritative"]
        ):
            merged["home_assistant_reconnected"] = True
            merged["reconnected_at"] = current_reconnection_state[
                "reconnected_at"
            ]
            for source in current_sources:
                if source not in sources:
                    sources.append(source)
            merged.pop("failure_category", None)
        else:
            merged.pop("home_assistant_reconnected", None)
            merged.pop("reconnected_at", None)
            sources = [
                source
                for source in sources
                if source != "home_assistant_core_reconnected"
            ]
        merged["restart_evidence_sources"] = sources[
            :MAX_RESTART_EVIDENCE_SOURCES
        ]
        merged_reconnection_state = (
            self._home_assistant_reconnection_evidence_state(
                merged,
                outage_state=normalized_state,
                now=observed_at,
            )
        )

        operational.dispatch["expected_disruption_observed"] = bool(
            normalized_state["authoritative"]
        )
        restart_dispatch_confirmed = bool(
            operational.dispatch.get("restart_dispatch_confirmed")
        )
        merged["restart_dispatch_confirmed"] = (
            restart_dispatch_confirmed
        )
        merged["redispatch_performed"] = False
        restart_evidence_complete = (
            restart_dispatch_confirmed
            and normalized_state["authoritative"]
            and merged_reconnection_state["authoritative"]
        )
        mismatch_fields = [
            str(value)[:160]
            for value in (result.get("mismatch_fields") or [])[:20]
            if str(value)
            not in {
                "restart_evidence",
                "restart_evidence_contract_invalid",
                "restart_evidence_window_expired",
            }
        ]
        if not restart_evidence_complete:
            mismatch_fields.append(
                str(normalized_state["reason"] or "restart_evidence")
            )
        status = str(result.get("status") or "failed")
        if status == "verified" and mismatch_fields:
            status = "pending"
        return {
            **result,
            "status": status,
            "mismatch_fields": list(dict.fromkeys(mismatch_fields)),
            "evidence": merged,
        }

    @staticmethod
    def _eligible_lifecycle_reconciliation(plan: ChangePlan) -> bool:
        operational = plan.operational
        return (
            plan.contract_version
            == OPERATIONAL_PLAN_CONTRACT_VERSION
            and plan.operation in LIFECYCLE_OPERATIONS
            and plan.status
            in {
                PlanStatus.APPLYING,
                PlanStatus.VERIFICATION_REQUIRED,
            }
            and operational is not None
            and operational.dispatch.get("attempt_count") == 1
            and operational.dispatch.get("dispatched") is True
            and plan.approval.state == ApprovalState.CONSUMED
        )

    async def reconcile_execution_tasks(
        self,
        *,
        trigger: str = "periodic",
        max_tasks: int = MAX_OPERATIONAL_RECONCILIATIONS_PER_PASS,
    ) -> dict[str, Any]:
        """Rehydrate task authority without invoking any provider action."""

        if trigger not in {"startup", "periodic", "manual"}:
            raise ValueError("unsupported execution-task trigger")
        self._task_reconciliation_runs += 1
        try:
            tasks = self.task_repository.list()
        except ExecutionTaskStorageError as exc:
            raise GovernanceError(
                ErrorCode.EXECUTION_TASK_STORAGE_ERROR
            ) from exc
        checked = completed = pending = manual_review = failed = 0
        for task in tasks:
            if checked >= max(1, min(int(max_tasks), 100)):
                break
            if task.state in TERMINAL_TASK_STATES:
                continue
            checked += 1
            plan_lock = self._plan_locks.setdefault(
                task.plan_id, asyncio.Lock()
            )
            if plan_lock.locked():
                pending += 1
                continue
            async with plan_lock:
                task = self._load_task(task.task_id)
                try:
                    plan = self._load(task.plan_id)
                except GovernanceError as exc:
                    if exc.code in {
                        ErrorCode.CHANGE_PLAN_NOT_FOUND,
                        ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                        ErrorCode.APPROVAL_AUTHORITY_MISMATCH,
                        ErrorCode.APPROVAL_PRINCIPAL_MISMATCH,
                        ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                    }:
                        # A missing immutable authority can never be recovered
                        # by creating or dispatching something new. Invalid F2
                        # authority is likewise terminal for this task owner.
                        dispatched = self._task_is_dispatched(task)
                        self._manual_review_task(
                            task,
                            (
                                "immutable_plan_unavailable"
                                if exc.code
                                == ErrorCode.CHANGE_PLAN_NOT_FOUND
                                else "immutable_plan_authority_invalid"
                            ),
                            None,
                        )
                        if dispatched:
                            manual_review += 1
                        else:
                            failed += 1
                        continue
                    raise
                if (
                    task.plan_hash != self.plan_hash(plan)
                    or task.operation != plan.operation.value
                    or task.target != self._task_target(plan)
                ):
                    self._manual_review_task(
                        task, "task_plan_authority_mismatch", plan
                    )
                    manual_review += 1
                    continue
                if plan.status in {
                    PlanStatus.APPLIED,
                    PlanStatus.FAILED,
                    PlanStatus.VERIFICATION_FAILED,
                }:
                    self._project_task_after_apply(task, plan)
                    task = self._load_task(task.task_id)
                    if (
                        task.state
                        == ExecutionTaskState.SUCCEEDED_VERIFIED
                    ):
                        completed += 1
                    else:
                        failed += 1
                    continue
                if self._task_deadline_expired(task):
                    self._manual_review_task(
                        task,
                        "maximum_post_dispatch_deadline_exceeded",
                        plan,
                    )
                    manual_review += 1
                    continue
                if not self._task_is_dispatched(task):
                    if (
                        task.state
                        in {
                            ExecutionTaskState.CREATED,
                            ExecutionTaskState.PREFLIGHT,
                        }
                        and self._valid_external_approval(plan, "apply")
                    ):
                        pending += 1
                        continue
                    self._project_task_after_apply(
                        task,
                        plan,
                        error_code="pre_dispatch_authority_invalid",
                    )
                    failed += 1
                    continue
                if (
                    plan.contract_version
                    == OPERATIONAL_PLAN_CONTRACT_VERSION
                    and self._eligible_lifecycle_reconciliation(plan)
                ):
                    pending += 1
                    continue
                self._project_task_after_apply(task, plan)
                task = self._load_task(task.task_id)
                if task.state == ExecutionTaskState.SUCCEEDED_VERIFIED:
                    completed += 1
                elif task.state in TERMINAL_TASK_STATES:
                    failed += 1
                else:
                    self._manual_review_task(
                        task,
                        "dispatched_task_has_no_readback_reconciler",
                        plan,
                    )
                    manual_review += 1
        return {
            "checked": checked,
            "completed": completed,
            "pending": pending,
            "manual_review_required": manual_review,
            "failed": failed,
            "provider_dispatches": 0,
            "trigger": trigger,
        }

    async def reconcile_operational_plans(
        self,
        *,
        trigger: str = "periodic",
        max_plans: int = MAX_OPERATIONAL_RECONCILIATIONS_PER_PASS,
        time_budget_seconds: float = (
            OPERATIONAL_RECONCILIATION_TIME_BUDGET_SECONDS
        ),
    ) -> dict[str, Any]:
        """Boundedly resume eligible readback-only work without redispatch."""

        if trigger not in {"startup", "periodic", "manual"}:
            raise ValueError("unsupported operational reconciliation trigger")
        max_plans = max(1, min(int(max_plans), 100))
        time_budget_seconds = max(
            0.01, min(float(time_budget_seconds), 60.0)
        )
        started = time.monotonic()
        selected = checked = completed = pending = failed = 0
        bounded = False
        for candidate in self.resolved_plans():
            if not self._eligible_lifecycle_reconciliation(candidate):
                continue
            if (
                selected >= max_plans
                or time.monotonic() - started >= time_budget_seconds
            ):
                bounded = True
                break
            selected += 1
            plan_lock = self._plan_locks.setdefault(
                candidate.plan_id, asyncio.Lock()
            )
            if plan_lock.locked():
                pending += 1
                continue
            async with plan_lock:
                plan = self._load(candidate.plan_id)
                if not self._eligible_lifecycle_reconciliation(plan):
                    continue
                try:
                    task = self.task_repository.get_for_plan(plan.plan_id)
                except ExecutionTaskStorageError as exc:
                    raise GovernanceError(
                        ErrorCode.EXECUTION_TASK_STORAGE_ERROR
                    ) from exc
                if task is not None:
                    if task.state in TERMINAL_TASK_STATES:
                        continue
                    if not self._task_is_dispatched(task):
                        self._manual_review_task(
                            task,
                            "plan_dispatch_without_task_dispatch_event",
                            plan,
                        )
                        failed += 1
                        continue
                    if self._task_deadline_expired(task):
                        self._manual_review_task(
                            task,
                            "maximum_post_dispatch_deadline_exceeded",
                            plan,
                        )
                        failed += 1
                        continue
                    self._active_task_ids_by_plan[
                        plan.plan_id
                    ] = task.task_id
                target_lock = self._target_locks.setdefault(
                    (
                        f"operational_{plan.operation.value}",
                        plan.target_id,
                    ),
                    asyncio.Lock(),
                )
                if target_lock.locked():
                    self._active_task_ids_by_plan.pop(
                        plan.plan_id, None
                    )
                    pending += 1
                    continue
                checked += 1
                async with target_lock:
                    if plan.status == PlanStatus.APPLYING:
                        plan.status = PlanStatus.VERIFICATION_REQUIRED
                        plan.execution_outcome = "verification_pending"
                        plan.operational.final_outcome = (
                            "verification_pending"
                        )
                        plan.operational.verification.status = (
                            "verification_pending"
                        )
                    self._record(
                        plan,
                        (
                            f"{plan.operation.value}_{trigger}_"
                            "reconciliation_started"
                        ),
                        "success",
                    )
                    remaining = (
                        time_budget_seconds
                        - (time.monotonic() - started)
                    )
                    if remaining <= 0:
                        pending += 1
                        bounded = True
                        break
                    self._active_lifecycle_reconciliations += 1
                    try:
                        result = await asyncio.wait_for(
                            self._resume_lifecycle_verification(plan),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        pending += 1
                        bounded = True
                        self._record(
                            plan,
                            (
                                f"{plan.operation.value}_{trigger}_"
                                "reconciliation_deferred"
                            ),
                            "partial",
                            error_code=(
                                ErrorCode.OPERATIONAL_VERIFICATION_PENDING.value
                            ),
                        )
                        if task is not None:
                            task = self._load_task(task.task_id)
                            self._project_task_after_apply(
                                task,
                                self._load(plan.plan_id),
                                error_code=(
                                    ErrorCode.OPERATIONAL_VERIFICATION_PENDING.value
                                ),
                            )
                        continue
                    except GovernanceError as exc:
                        failed += 1
                        if task is not None:
                            task = self._load_task(task.task_id)
                            self._project_task_after_apply(
                                task,
                                self._load(plan.plan_id),
                                error_code=exc.code.value,
                            )
                        continue
                    except Exception as exc:
                        failed += 1
                        log_event(
                            self.logger,
                            logging.WARNING,
                            "operational_reconciliation_plan_failed",
                            (
                                "Operational readback reconciliation will "
                                "retry without redispatch."
                            ),
                            context={
                                "operation": plan.operation.value,
                                "trigger": trigger,
                                "error_type": type(exc).__name__,
                            },
                        )
                        try:
                            self._record(
                                plan,
                                (
                                    f"{plan.operation.value}_{trigger}_"
                                    "reconciliation_deferred"
                                ),
                                "partial",
                                error_code=(
                                    ErrorCode.OPERATIONAL_VERIFICATION_PENDING.value
                                ),
                            )
                        except Exception:
                            pass
                        if task is not None:
                            task = self._load_task(task.task_id)
                            self._project_task_after_apply(
                                task,
                                self._load(plan.plan_id),
                                error_code=(
                                    ErrorCode.OPERATIONAL_VERIFICATION_PENDING.value
                                ),
                            )
                        continue
                    finally:
                        self._active_lifecycle_reconciliations -= 1
                        self._active_task_ids_by_plan.pop(
                            plan.plan_id, None
                        )
                    if task is not None:
                        task = self._load_task(task.task_id)
                        self._project_task_after_apply(
                            task, self._load(plan.plan_id)
                        )
            if result.get("status") == "applied":
                completed += 1
            else:
                pending += 1
        return {
            "checked": checked,
            "completed": completed,
            "pending": pending,
            "failed": failed,
            "bounded": bounded,
        }

    def _lifecycle_preflight_matches(
        self,
        plan: ChangePlan,
        fresh_provider: dict[str, Any],
        fresh_baseline: dict[str, Any],
    ) -> bool:
        operational = plan.operational
        if operational is None:
            return False
        planned_provider = operational.provider_capability_evidence
        provider_match = all(
            fresh_provider.get(field) == planned_provider.get(field)
            for field in (
                "provider",
                "server_name",
                "server_version",
                "protocol_version",
                "compatibility_entry_id",
                "catalog_fingerprint",
                "tool_contract_fingerprints",
                "argument_constraints",
            )
        )
        if not provider_match:
            return False
        if plan.operation == ChangeOperation.RESTART_ADDON:
            planned_addon = operational.baseline.get("addon")
            fresh_addon = fresh_baseline.get("addon")
            identity_matches = isinstance(
                planned_addon, dict
            ) and isinstance(
                fresh_addon, dict
            ) and all(
                planned_addon.get(field) == fresh_addon.get(field)
                for field in ("slug", "name", "version")
            )
            if not identity_matches:
                return False
            planned_class = operational.baseline.get("target_class")
            if planned_class != fresh_baseline.get("target_class"):
                return False
            planned_target_identity = operational.baseline.get(
                "target_identity"
            )
            fresh_target_identity = fresh_baseline.get("target_identity")
            if isinstance(planned_target_identity, dict) and (
                not isinstance(fresh_target_identity, dict)
                or any(
                    planned_target_identity.get(field)
                    != fresh_target_identity.get(field)
                    for field in (
                        "requested_slug",
                        "resolved_slug",
                        "resolved_name",
                        "resolved_version",
                        "resolved_repository",
                        "identity_source",
                        "authoritative_self_match",
                        "authoritative_upstream_match",
                        "target_class",
                    )
                )
            ):
                return False
            planned_upstream_identity = operational.baseline.get(
                "upstream_addon_identity"
            )
            fresh_upstream_identity = fresh_baseline.get(
                "upstream_addon_identity"
            )
            if isinstance(planned_upstream_identity, dict) and (
                not isinstance(fresh_upstream_identity, dict)
                or planned_upstream_identity != fresh_upstream_identity
            ):
                return False
            if planned_class == "engineering_addon":
                planned_runtime = operational.baseline.get("runtime")
                fresh_runtime = fresh_baseline.get("runtime")
                return isinstance(planned_runtime, dict) and isinstance(
                    fresh_runtime, dict
                ) and all(
                    planned_runtime.get(field)
                    == fresh_runtime.get(field)
                    for field in (
                        "server_version",
                        "build_sha",
                        "registered_tool_count",
                        "engineering_tool_count",
                        "delegated_tool_count",
                    )
                )
            if planned_class == "upstream_ha_mcp_addon":
                planned_runtime = operational.baseline.get("runtime")
                fresh_runtime = fresh_baseline.get("runtime")
                return isinstance(planned_runtime, dict) and isinstance(
                    fresh_runtime, dict
                ) and all(
                    planned_runtime.get(field)
                    == fresh_runtime.get(field)
                    for field in (
                        "upstream_version",
                        "upstream_protocol",
                        "upstream_catalog_fingerprint",
                        "upstream_admission_status",
                    )
                )
            return planned_class == "other_addon"
        if (
            plan.operation
            == ChangeOperation.RESTART_HOME_ASSISTANT
        ):
            planned_ha = operational.baseline.get("home_assistant")
            fresh_ha = fresh_baseline.get("home_assistant")
            planned_runtime = operational.baseline.get("runtime")
            fresh_runtime = fresh_baseline.get("runtime")
            return (
                isinstance(planned_ha, dict)
                and isinstance(fresh_ha, dict)
                and all(
                    planned_ha.get(field) == fresh_ha.get(field)
                    for field in ("location_name", "version")
                )
                and isinstance(planned_runtime, dict)
                and isinstance(fresh_runtime, dict)
                and all(
                    planned_runtime.get(field)
                    == fresh_runtime.get(field)
                    for field in (
                        "server_version",
                        "build_sha",
                        "registered_tool_count",
                        "upstream_version",
                    )
                )
            )
        return bool(fresh_baseline.get("service_available"))

    async def _apply_operational_backup(
        self, plan: ChangePlan, expected_plan_hash: str
    ) -> dict[str, Any]:
        self._resolve_lifecycle(plan)
        if plan.status == PlanStatus.EXPIRED:
            raise GovernanceError(ErrorCode.CHANGE_PLAN_EXPIRED)
        self._require_current_normalization(plan)
        operational = plan.operational
        if operational is None or self.operational_gateway is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        calculated = self.plan_hash(plan)
        if not expected_plan_hash or expected_plan_hash != calculated:
            self._reject_apply(
                plan,
                ErrorCode.APPROVAL_HASH_MISMATCH,
                details={
                    "hash_validation": {
                        "performed": bool(expected_plan_hash),
                        "result": (
                            "mismatch"
                            if expected_plan_hash
                            else "not_supplied"
                        ),
                    }
                },
            )
        if plan.status == PlanStatus.APPLIED:
            return {
                "status": "already_applied",
                "provider_dispatch_occurred": True,
                "redispatch_performed": False,
                "plan": self._public(plan, include_configs=False),
            }
        if (
            plan.status == PlanStatus.APPLYING
            and operational.dispatch.get("attempt_count") == 1
        ):
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            plan.execution_outcome = "indeterminate"
            operational.final_outcome = "verification_required"
            operational.verification.status = "verification_required"
            operational.verification.evidence = {
                "reason": "resumed_after_incomplete_apply",
                "redispatch_performed": False,
            }
            self._record(
                plan,
                "operational_backup_dispatch_indeterminate",
                "partial",
                error_code=(
                    ErrorCode.BACKUP_DISPATCH_INDETERMINATE.value
                ),
            )
            return await self._resume_operational_verification(plan)
        if plan.status == PlanStatus.VERIFICATION_REQUIRED:
            return await self._resume_operational_verification(plan)
        if plan.status in {
            PlanStatus.FAILED,
            PlanStatus.VERIFICATION_FAILED,
        } or plan.approval.state == ApprovalState.CONSUMED:
            raise GovernanceError(ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        if plan.status == PlanStatus.REJECTED or plan.approval.state == ApprovalState.REJECTED:
            self._reject_apply(plan, ErrorCode.CHANGE_PLAN_REJECTED)
        self._require_dispatch_approval(plan)
        if plan.approval.bound_plan_hash != calculated:
            self._reject_apply(plan, ErrorCode.APPROVAL_HASH_MISMATCH)

        target_lock = self._target_locks.setdefault(
            ("operational_backup", "global"), asyncio.Lock()
        )
        if target_lock.locked():
            self._reject_apply(plan, ErrorCode.CHANGE_IN_PROGRESS)
        async with target_lock:
            try:
                fresh = await self.operational_gateway.planning_evidence()
            except OperationalGatewayError as exc:
                self._record(
                    plan,
                    "operational_backup_preflight_failed",
                    "failure",
                    error_code=self._operational_error_code(
                        exc.category, dispatched=False
                    ).value,
                )
                raise GovernanceError(
                    self._operational_error_code(
                        exc.category, dispatched=False
                    )
                ) from None
            planned_provider = operational.provider_capability_evidence
            observed_provider = fresh.get("provider")
            observed_baseline = fresh.get("baseline")
            if (
                not isinstance(observed_provider, dict)
                or not isinstance(observed_baseline, dict)
                or any(
                    observed_provider.get(field)
                    != planned_provider.get(field)
                    for field in (
                        "provider",
                        "server_name",
                        "server_version",
                        "protocol_version",
                        "compatibility_entry_id",
                        "catalog_fingerprint",
                        "tool_contract_fingerprint",
                    )
                )
                or observed_baseline.get("backup_ids")
                != operational.baseline.get("backup_ids")
                or observed_baseline.get("operation_state") != "idle"
            ):
                self._record(
                    plan,
                    "operational_backup_preflight_failed",
                    "failure",
                    error_code=ErrorCode.STALE_TARGET_STATE.value,
                )
                raise GovernanceError(ErrorCode.STALE_TARGET_STATE)

            async def before_dispatch() -> None:
                self._require_policy_snapshot(plan)
                self._require_dispatch_approval(plan)
                if operational.dispatch.get("attempt_count") not in {0, None}:
                    raise GovernanceError(
                        ErrorCode.DUPLICATE_APPLY_ATTEMPT
                    )
                self._consume_approval_bundle(plan)
                plan.status = PlanStatus.APPLYING
                plan.execution_outcome = "dispatching"
                plan.apply_request_id = current_request_id()
                operational.dispatch.update(
                    {
                        "attempt_count": 1,
                        "dispatched": True,
                        "request_id": plan.apply_request_id,
                        "attempted_at": self._timestamp(),
                    }
                )
                self._record(
                    plan,
                    "operational_backup_dispatch_recorded",
                    "success",
                )

            try:
                dispatch = await self.operational_gateway.create_full_backup(
                    operational.requested_name,
                    before_dispatch=before_dispatch,
                )
                operational.dispatch.update(
                    {
                        "provider_operation_id": dispatch.operation_id,
                        "backup_id": dispatch.backup_id,
                        "provider_response_received": True,
                        "provider_response_at": self._timestamp(),
                    }
                )
                self._record(
                    plan,
                    "operational_backup_provider_completed",
                    "success",
                )
            except OperationalGatewayError as exc:
                if not exc.dispatched:
                    code = self._operational_error_code(
                        exc.category, dispatched=False
                    )
                    failure_details = {
                        "failure_category": exc.category,
                        "failure_stage": "pre_dispatch",
                        "provider_dispatch_occurred": False,
                        "backup_creation_attempted": False,
                        "fallback": "none",
                        "fallback_occurred": False,
                        "required_action": (
                            "refresh_provider_evidence_and_replan"
                        ),
                    }
                    plan.failure_information = {
                        "error_code": code.value,
                        **failure_details,
                    }
                    self._record(
                        plan,
                        "operational_backup_dispatch_rejected",
                        "failure",
                        error_code=code.value,
                        failure_category=exc.category,
                        failure_stage="pre_dispatch",
                    )
                    raise GovernanceError(
                        code, details=failure_details
                    ) from None
                if exc.category in {
                    "permission_failure",
                    "backup_rejected",
                    "backup_failed",
                }:
                    code = self._operational_error_code(
                        exc.category, dispatched=True
                    )
                    operational.final_outcome = "provider_rejected"
                    plan.status = PlanStatus.FAILED
                    plan.execution_outcome = "failed"
                    operational.dispatch["provider_response_received"] = True
                    operational.dispatch["failure_category"] = exc.category
                    plan.failure_information = {
                        "error_code": code.value,
                        "failure_category": exc.category,
                        "provider_dispatch_occurred": True,
                        "redispatch_performed": False,
                    }
                    self._record(
                        plan,
                        "operational_backup_provider_failed",
                        "failure",
                        error_code=code.value,
                    )
                    raise GovernanceError(code) from None
                operational.final_outcome = "verification_required"
                plan.status = PlanStatus.VERIFICATION_REQUIRED
                plan.execution_outcome = "indeterminate"
                operational.dispatch["provider_response_received"] = False
                operational.dispatch["failure_category"] = exc.category
                self._record(
                    plan,
                    "operational_backup_dispatch_indeterminate",
                    "partial",
                    error_code=ErrorCode.BACKUP_DISPATCH_INDETERMINATE.value,
                )
                return await self._resume_operational_verification(
                    plan, initial_error=exc.category
                )
            return await self._resume_operational_verification(plan)

    async def _resume_operational_verification(
        self,
        plan: ChangePlan,
        *,
        initial_error: str | None = None,
    ) -> dict[str, Any]:
        operational = plan.operational
        if operational is None or self.operational_gateway is None:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        if operational.dispatch.get("attempt_count") != 1:
            raise GovernanceError(ErrorCode.INTERNAL_INVARIANT_VIOLATION)
        verification = operational.verification
        verification.attempt_count += 1
        verification.checked_at = self._timestamp()
        self._record(
            plan,
            "operational_backup_verification_started",
            "success",
        )
        try:
            result = await self.operational_gateway.verify_full_backup(
                requested_name=operational.requested_name,
                baseline_ids=list(
                    operational.baseline.get("backup_ids") or []
                ),
                apply_started_at=str(
                    operational.dispatch.get("attempted_at") or plan.updated_at
                ),
                backup_id=operational.dispatch.get("backup_id"),
                operation_id=operational.dispatch.get(
                    "provider_operation_id"
                ),
            )
        except OperationalGatewayError as exc:
            verification.status = "verification_required"
            verification.inventory_readable = False
            verification.evidence = {
                "failure_category": exc.category,
                "redispatch_performed": False,
            }
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            plan.execution_outcome = "indeterminate"
            operational.final_outcome = "verification_required"
            code = self._operational_error_code(
                exc.category, dispatched=True
            )
            self._record(
                plan,
                "operational_backup_verification_deferred",
                "partial",
                error_code=code.value,
            )
            raise GovernanceError(code) from None

        verification.status = str(result.get("status") or "failed")
        verification.operation_completed = result.get(
            "operation_completed"
        )
        verification.inventory_readable = result.get("inventory_readable")
        verification.archive_integrity_validated = False
        verification.mismatch_fields = [
            str(item)[:160]
            for item in (result.get("mismatch_fields") or [])[:20]
        ]
        verification.evidence = dict(result.get("evidence") or {})
        if verification.status == "verified":
            plan.status = PlanStatus.APPLIED
            plan.applied_at = self._timestamp()
            plan.execution_outcome = "applied_verified"
            operational.final_outcome = "backup_created_and_verified"
            self._record(
                plan,
                "operational_backup_verified",
                "success",
            )
            return {
                "status": "applied",
                "provider_dispatch_occurred": True,
                "redispatch_performed": False,
                "verification": verification.__dict__,
                "rollback_available": False,
                "plan": self._public(plan, include_configs=False),
            }
        if verification.status == "pending":
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            plan.execution_outcome = "indeterminate"
            operational.final_outcome = "verification_required"
            code = (
                ErrorCode.BACKUP_DISPATCH_INDETERMINATE
                if initial_error
                else ErrorCode.BACKUP_VERIFICATION_TIMEOUT
            )
            self._record(
                plan,
                "operational_backup_verification_deferred",
                "partial",
                error_code=code.value,
            )
            raise GovernanceError(code)
        plan.status = PlanStatus.VERIFICATION_FAILED
        plan.execution_outcome = "verification_failed"
        operational.final_outcome = "verification_failed"
        plan.failure_information = {
            "error_code": ErrorCode.BACKUP_VERIFICATION_FAILED.value,
            "mismatch_fields": verification.mismatch_fields,
        }
        self._record(
            plan,
            "operational_backup_verification_failed",
            "failure",
            error_code=ErrorCode.BACKUP_VERIFICATION_FAILED.value,
        )
        raise GovernanceError(ErrorCode.BACKUP_VERIFICATION_FAILED)

    @staticmethod
    def _operational_error_code(
        category: str, *, dispatched: bool
    ) -> ErrorCode:
        if dispatched and category in {
            "indeterminate_dispatch",
            "provider_timeout",
            "provider_unavailable",
        }:
            return ErrorCode.BACKUP_DISPATCH_INDETERMINATE
        if not dispatched and category in {
            "catalog_mismatch",
            "reviewed_contract_mismatch",
            "server_identity_mismatch",
            "upstream_version_mismatch",
            "unsupported_protocol_version",
            "required_tool_missing",
            "invalid_response",
            "protocol_error",
            "provider_error",
        }:
            return ErrorCode.BACKUP_PROVIDER_UNAVAILABLE
        return {
            "invalid_request": ErrorCode.INVALID_REQUEST,
            "provider_unavailable": ErrorCode.BACKUP_PROVIDER_UNAVAILABLE,
            "permission_failure": ErrorCode.BACKUP_PERMISSION_FAILURE,
            "backup_rejected": ErrorCode.BACKUP_CREATION_REJECTED,
            "backup_failed": ErrorCode.BACKUP_CREATION_FAILED,
            "provider_timeout": ErrorCode.BACKUP_OPERATION_TIMEOUT,
            "verification_timeout": ErrorCode.BACKUP_VERIFICATION_TIMEOUT,
            "verification_failed": ErrorCode.BACKUP_VERIFICATION_FAILED,
            "indeterminate_dispatch": ErrorCode.BACKUP_DISPATCH_INDETERMINATE,
            "internal_invariant_violation": (
                ErrorCode.INTERNAL_INVARIANT_VIOLATION
            ),
        }.get(category, ErrorCode.BACKUP_CREATION_FAILED)

    @staticmethod
    def _lifecycle_error_code(
        category: str, *, dispatched: bool
    ) -> ErrorCode:
        if dispatched and category in {
            "indeterminate_dispatch",
            "provider_timeout",
            "provider_unavailable",
        }:
            return ErrorCode.OPERATIONAL_DISPATCH_INDETERMINATE
        if category in {
            "catalog_mismatch",
            "reviewed_contract_mismatch",
            "server_identity_mismatch",
            "upstream_version_mismatch",
            "unsupported_protocol_version",
            "required_tool_missing",
            "upstream_addon_identity_unavailable",
        }:
            return ErrorCode.OPERATIONAL_CONTRACT_MISMATCH
        if category in {
            "configuration_invalid",
            "invalid_request",
            "service_unavailable",
        }:
            return ErrorCode.OPERATIONAL_VALIDATION_FAILED
        if category == "resource_not_found":
            return ErrorCode.RESOURCE_NOT_FOUND
        if category == "addon_not_found":
            return ErrorCode.ADDON_NOT_FOUND
        if category == "self_addon_identity_unavailable":
            return ErrorCode.SELF_ADDON_IDENTITY_UNAVAILABLE
        if category == "permission_failure":
            return ErrorCode.AUTHORIZATION_FAILURE
        if category in {
            "provider_unavailable",
            "provider_timeout",
            "invalid_response",
            "protocol_error",
            "provider_error",
        }:
            return ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE
        if category in {"operation_rejected", "operation_failed"}:
            return ErrorCode.OPERATIONAL_ACTION_REJECTED
        if category == "verification_failed":
            return ErrorCode.OPERATIONAL_VERIFICATION_FAILED
        if category == "indeterminate_dispatch":
            return ErrorCode.OPERATIONAL_DISPATCH_INDETERMINATE
        return ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE

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

    @staticmethod
    def _verification_receipt_evidence(
        comparison: ResourceVerificationComparison,
    ) -> dict[str, Any]:
        return {
            "raw_approved_fingerprint": (
                comparison.raw_approved_fingerprint
            ),
            "raw_observed_fingerprint": (
                comparison.raw_observed_fingerprint
            ),
            "binding_approved_fingerprint": (
                comparison.binding_approved_fingerprint
            ),
            "binding_observed_fingerprint": (
                comparison.binding_observed_fingerprint
            ),
            "normalized_approved_fingerprint": (
                comparison.normalized_approved_fingerprint
            ),
            "normalized_observed_fingerprint": (
                comparison.normalized_observed_fingerprint
            ),
            "canonicalization_categories": list(
                comparison.canonicalization_categories
            ),
            "mismatch_categories": list(
                comparison.mismatch_categories
            ),
            "verification_normalization_version": (
                comparison.verification_normalization_version
            ),
            "observed_available": comparison.observed_available,
            "semantic_verification_result": (
                "matched"
                if comparison.semantic_match
                else "unavailable"
                if not comparison.observed_available
                else "mismatch"
                if comparison.normalization_valid
                else "invalid"
            ),
        }

    def _reconcile_configuration_task_response_evidence(
        self,
        task: ExecutionTask,
        plan: ChangePlan,
    ) -> None:
        """Recover task response truth from a persisted operation receipt.

        Plan storage is committed before task projection. If task storage is
        briefly unavailable after Home Assistant has responded, the immutable
        operation receipt and its matching plan event may safely restore only
        the missing response flag. This path never creates an attempt, reads
        Home Assistant, or dispatches a provider.
        """

        if plan.contract_version < CONFIGURATION_PLAN_CONTRACT_VERSION:
            response_event = next(
                (
                    event
                    for event in reversed(plan.events)
                    if event.event in AUTOMATION_PROVIDER_RESPONSE_EVENTS
                ),
                None,
            )
            if response_event is None:
                return
            try:
                parse_task_timestamp(response_event.timestamp)
            except (TypeError, ValueError):
                return
            attempts = [dict(item) for item in task.provider_attempts]
            if not attempts or attempts[-1].get("response_received") is True:
                return
            attempts[-1] = {
                **attempts[-1],
                "response_received": True,
                "response_recorded_at": response_event.timestamp,
            }
            self._record_task_event(
                task,
                "provider_response_recorded",
                new_state=(
                    ExecutionTaskState.OBSERVING
                    if task.state == ExecutionTaskState.DISPATCHING
                    else None
                ),
                changes={
                    "provider_attempts": attempts,
                    "verification_summary": {
                        **task.verification_summary,
                        "status": "pending",
                        "provider_response_received": True,
                        "response_evidence_source": (
                            "persisted_automation_provider_event"
                        ),
                    },
                },
            )
            return
        if plan.contract_version != CONFIGURATION_PLAN_CONTRACT_VERSION:
            return
        response_events = {
            event.operation_id
            for event in plan.events
            if event.event
            in {
                "configuration_operation_provider_completed",
                "configuration_operation_provider_failed",
            }
            and isinstance(event.operation_id, str)
        }
        operations = {
            operation.operation_id: operation
            for operation in plan.operations
        }
        attempts = [dict(item) for item in task.provider_attempts]
        changed = False
        for index, attempt in enumerate(attempts):
            if attempt.get("response_received") is True:
                continue
            operation_id = attempt.get("operation_id")
            operation = operations.get(operation_id)
            if operation is None or operation_id not in response_events:
                continue
            receipt = operation.execution_receipt
            if (
                not isinstance(receipt, dict)
                or receipt.get("provider_response_received") is not True
            ):
                continue
            recorded_at = receipt.get("provider_response_recorded_at")
            try:
                parse_task_timestamp(recorded_at)
            except (TypeError, ValueError):
                continue
            attempts[index] = {
                **attempt,
                "response_received": True,
                "response_recorded_at": recorded_at,
            }
            changed = True
        if not changed:
            return
        self._record_task_event(
            task,
            "provider_response_recorded",
            new_state=(
                ExecutionTaskState.OBSERVING
                if task.state == ExecutionTaskState.DISPATCHING
                else None
            ),
            changes={
                "provider_attempts": attempts,
                "verification_summary": {
                    **task.verification_summary,
                    "status": "pending",
                    "provider_response_received": bool(
                        attempts
                        and attempts[-1].get("response_received") is True
                    ),
                    "response_evidence_source": (
                        "persisted_configuration_operation"
                    ),
                },
            },
        )

    @staticmethod
    def _configuration_response_projection_mismatch(
        task: ExecutionTask,
        plan: ChangePlan,
    ) -> bool:
        """Detect new affirmative response evidence missing from its task."""

        if (
            task.approval_reference.get("authority_version")
            != APPROVAL_AUTHORITY_VERSION
        ):
            return False
        if plan.contract_version < CONFIGURATION_PLAN_CONTRACT_VERSION:
            has_response = any(
                event.event in AUTOMATION_PROVIDER_RESPONSE_EVENTS
                for event in plan.events
            )
            return bool(
                has_response
                and (
                    not task.provider_attempts
                    or task.provider_attempts[-1].get(
                        "response_received"
                    )
                    is not True
                )
            )
        if plan.contract_version != CONFIGURATION_PLAN_CONTRACT_VERSION:
            return False
        response_operation_ids = {
            operation.operation_id
            for operation in plan.operations
            if isinstance(operation.execution_receipt, dict)
            and operation.execution_receipt.get(
                "provider_response_received"
            )
            is True
        }
        for operation_id in response_operation_ids:
            matching = [
                attempt
                for attempt in task.provider_attempts
                if attempt.get("operation_id") == operation_id
            ]
            if not matching or matching[-1].get(
                "response_received"
            ) is not True:
                return True
        return False

    @staticmethod
    def _configuration_response_was_received(
        error: BaseException,
    ) -> bool:
        return (
            isinstance(error, EngineeringServerError)
            and error.details.get("provider_response_received") is True
        )

    @staticmethod
    def _configuration_task_verification_evidence(
        plan: ChangePlan,
    ) -> dict[str, Any]:
        if plan.contract_version != CONFIGURATION_PLAN_CONTRACT_VERSION:
            return {}
        projected: list[dict[str, Any]] = []
        allowed_receipt_fields = (
            "raw_approved_fingerprint",
            "raw_observed_fingerprint",
            "binding_approved_fingerprint",
            "binding_observed_fingerprint",
            "normalized_approved_fingerprint",
            "normalized_observed_fingerprint",
            "canonicalization_categories",
            "verification_normalization_version",
            "observed_available",
            "semantic_verification_result",
        )
        for operation in sorted(
            plan.operations, key=lambda item: item.order
        )[:MAX_CONFIGURATION_OPERATIONS]:
            receipt = (
                operation.execution_receipt
                if isinstance(operation.execution_receipt, dict)
                else {}
            )
            evidence = {
                key: receipt.get(key) for key in allowed_receipt_fields
            }
            mismatch_categories = receipt.get("mismatch_categories")
            if not isinstance(mismatch_categories, list):
                mismatch_categories = operation.verification.mismatch_fields
            bounded_mismatches = sorted(
                str(item)[:120]
                for item in mismatch_categories[:20]
                if isinstance(item, str) and item
            )
            evidence["mismatch_category"] = (
                bounded_mismatches[0] if bounded_mismatches else None
            )
            evidence["operation_id"] = operation.operation_id
            projected.append(evidence)
        return {"configuration_operations": projected}

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
                comparison = compare_resource_verification(
                    resource_type,
                    operation.proposed_config,
                    current,
                )
                if (
                    not resource_identity_matches(
                        resource_type, operation.target_id, current
                    )
                    or not comparison.semantic_match
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
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        self._require_dispatch_approval(plan)
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

            self._require_policy_snapshot(plan)
            self._require_dispatch_approval(plan)
            self._consume_approval_bundle(plan)
            plan.status = PlanStatus.APPLYING
            plan.execution_outcome = "applying"
            plan.apply_request_id = current_request_id()
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
                            "provider_response_received": True,
                            "provider_response_recorded_at": (
                                self._timestamp()
                            ),
                            "write_result": "completed_unexpectedly",
                            "outcome": "unexpected_resource_created",
                            "unexpected_resource_id": (
                                unexpected_resource_id
                            ),
                            "orphan_risk": True,
                        }
                    )
                    self._record(
                        plan,
                        "configuration_operation_provider_completed",
                        "success",
                        operation_step=operation,
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
                    if self._configuration_response_was_received(exc):
                        operation.execution_receipt.update(
                            {
                                "provider_response_received": True,
                                "provider_response_recorded_at": (
                                    self._timestamp()
                                ),
                            }
                        )
                        self._record(
                            plan,
                            "configuration_operation_provider_failed",
                            "failure",
                            operation_step=operation,
                        )
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

                    comparison = compare_resource_verification(
                        resource_type,
                        operation.proposed_config,
                        actual_after_error,
                        observed_available=bool(
                            operation.execution_receipt[
                                "readback_completed"
                            ]
                        ),
                    )
                    actual_after_error_fingerprint = (
                        comparison.binding_observed_fingerprint
                    )
                    desired_state_proven = (
                        resource_identity_matches(
                            resource_type,
                            operation.target_id,
                            actual_after_error,
                        )
                        and comparison.semantic_match
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
                            **self._verification_receipt_evidence(
                                comparison
                            ),
                        }
                    )
                    ambiguous_mismatches = list(
                        comparison.mismatch_categories
                    )
                    if not resource_identity_matches(
                        resource_type,
                        operation.target_id,
                        actual_after_error,
                    ):
                        ambiguous_mismatches.append("resource_identity")
                    operation.failure_information = {
                        "error_code": ErrorCode.CONFIGURATION_APPLY_FAILED.value,
                        "failure_category": type(exc).__name__,
                        "readback_failure_category": (
                            readback_error_category
                        ),
                        "desired_state_proven": desired_state_proven,
                        "mismatch_fields": sorted(
                            set(ambiguous_mismatches)
                        ),
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
                else:
                    successful_writes += 1
                    operation.execution_receipt.update(
                        {
                            "write_completed": True,
                            "provider_response_received": True,
                            "provider_response_recorded_at": (
                                self._timestamp()
                            ),
                        }
                    )
                    self._record(
                        plan,
                        "configuration_operation_provider_completed",
                        "success",
                        operation_step=operation,
                    )

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

                comparison = compare_resource_verification(
                    resource_type,
                    operation.proposed_config,
                    actual,
                    observed_available=bool(
                        operation.execution_receipt["readback_completed"]
                    ),
                )
                actual_fingerprint = (
                    comparison.binding_observed_fingerprint
                )
                mismatch = list(comparison.mismatch_categories)
                if not resource_identity_matches(
                    resource_type, operation.target_id, actual
                ):
                    mismatch.append("resource_identity")
                operation.post_apply_fingerprint = actual_fingerprint
                operation.verification = ChangeVerification(
                    status="failed" if mismatch else "passed",
                    checked_at=self._timestamp(),
                    desired_fingerprint=operation.proposed_config_hash,
                    actual_fingerprint=actual_fingerprint,
                    config_check_status="deferred",
                    mismatch_fields=sorted(set(mismatch)),
                )
                operation.execution_receipt.update(
                    {
                        "resulting_fingerprint": actual_fingerprint,
                        "desired_state_proven": not mismatch,
                        **self._verification_receipt_evidence(comparison),
                    }
                )
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
        if plan.approval.state == ApprovalState.CONSUMED:
            self._reject_apply(plan, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        self._require_dispatch_approval(plan)
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

        self._require_policy_snapshot(plan)
        self._require_dispatch_approval(plan)
        self._consume_approval_bundle(plan)
        plan.snapshot = ChangeSnapshot(self._timestamp(), current, state_fingerprint(current))
        plan.status = PlanStatus.APPLYING
        plan.apply_request_id = current_request_id()
        self._record(plan, "external_approval_consumed", "success")
        self._record(plan, "change_apply_started", "success")
        try:
            await self.gateway.write(plan.target_id, plan.proposed_config)
        except Exception as exc:
            if self._configuration_response_was_received(exc):
                self._record(
                    plan,
                    "automation_provider_failed",
                    "failure",
                    error_code=(
                        exc.code.value
                        if isinstance(exc, EngineeringServerError)
                        else ErrorCode.AUTOMATION_APPLY_FAILED.value
                    ),
                )
            plan.status = PlanStatus.FAILED
            plan.failure_information = {"error_code": ErrorCode.AUTOMATION_APPLY_FAILED.value}
            self._record(plan, "change_apply_failed", "failure", error_code=ErrorCode.AUTOMATION_APPLY_FAILED.value)
            raise GovernanceError(ErrorCode.AUTOMATION_APPLY_FAILED) from exc
        else:
            # Normal return from the transport proves that a response arrived,
            # even when Home Assistant used an empty success body. Persist this
            # fact before readback so later verification cannot erase it.
            self._record(
                plan,
                "automation_provider_completed",
                "success",
            )

        try:
            actual = await self.gateway.get(plan.target_id)
        except Exception as exc:
            plan.status = PlanStatus.FAILED
            plan.failure_information = {
                "error_code": ErrorCode.AUTOMATION_APPLY_FAILED.value
            }
            self._record(
                plan,
                "change_apply_failed",
                "failure",
                error_code=ErrorCode.AUTOMATION_APPLY_FAILED.value,
            )
            raise GovernanceError(
                ErrorCode.AUTOMATION_APPLY_FAILED
            ) from exc

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

    def _approval_requirement_error(
        self, plan: ChangePlan, approval_kind: str
    ) -> ErrorCode | None:
        if plan.policy_decision is None:
            return ErrorCode.POLICY_SNAPSHOT_REQUIRED
        if not policy_snapshot_matches(plan):
            return ErrorCode.POLICY_SNAPSHOT_MISMATCH
        if (
            plan.policy_decision.policy_class
            == ApprovalPolicyClass.PROHIBITED
        ):
            return ErrorCode.PROHIBITED_CHANGE
        approval = plan.approval
        try:
            unexpired = bool(
                approval.approval_expires_at
                and self.now() < datetime.fromisoformat(approval.approval_expires_at)
            )
        except ValueError:
            unexpired = False
        expected_status = (
            PlanStatus.APPROVED
            if approval_kind == "apply"
            else PlanStatus.ROLLBACK_PENDING
        )
        if approval.authority_version != APPROVAL_AUTHORITY_VERSION:
            return ErrorCode.APPROVAL_AUTHORITY_MISMATCH
        if (
            approval.state != ApprovalState.APPROVED
            or approval.channel != APPROVAL_CHANNEL
            or approval.approval_kind != approval_kind
            or not approval.principal_separation_enforced
            or not approval.approver_principal
            or approval.bound_plan_hash != self.plan_hash(plan)
            or approval.policy_decision_hash
            != plan.policy_decision.policy_decision_hash
            or approval.policy_class
            != plan.policy_decision.policy_class.value
            or not unexpired
        ):
            return ErrorCode.EXTERNAL_APPROVAL_REQUIRED
        if (
            plan.policy_decision.policy_class
            == ApprovalPolicyClass.ELEVATED_ADMIN
        ):
            acknowledgement = approval.elevated_risk_acknowledgement
            if (
                acknowledgement is None
                or acknowledgement.state != ApprovalState.APPROVED
                or not acknowledgement.granted_at
            ):
                return (
                    ErrorCode.ELEVATED_RISK_ACKNOWLEDGEMENT_REQUIRED
                )
            if (
                acknowledgement.approver_principal
                != approval.approver_principal
                or approval.same_principal_confirmed is not True
            ):
                return ErrorCode.APPROVAL_PRINCIPAL_MISMATCH
        if plan.status != expected_status:
            return ErrorCode.EXTERNAL_APPROVAL_REQUIRED
        if self._approval_bundle_state(plan) != "fully_approved":
            return ErrorCode.EXTERNAL_APPROVAL_REQUIRED
        return None

    def _valid_external_approval(
        self, plan: ChangePlan, approval_kind: str
    ) -> bool:
        return self._approval_requirement_error(plan, approval_kind) is None

    def _require_dispatch_approval(
        self, plan: ChangePlan, approval_kind: str = "apply"
    ) -> None:
        error = self._approval_requirement_error(plan, approval_kind)
        if error is not None:
            self._reject_apply(plan, error)

    def _consume_approval_bundle(self, plan: ChangePlan) -> None:
        """Consume every granted action at the existing dispatch boundary."""

        self._require_policy_snapshot(plan)
        self._require_dispatch_approval(
            plan, plan.approval.approval_kind
        )
        consumed_at = self._timestamp()
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.consumed_at = consumed_at
        plan.approval.bundle_state = "consumed"
        acknowledgement = (
            plan.approval.elevated_risk_acknowledgement
        )
        if acknowledgement is not None:
            acknowledgement.state = ApprovalState.CONSUMED
            acknowledgement.consumed_at = consumed_at

    def _require_current_normalization(self, plan: ChangePlan) -> None:
        if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION:
            operational = plan.operational
            constraints = (
                operational.provider_capability_evidence.get(
                    "argument_constraints"
                )
                if operational
                and isinstance(
                    operational.provider_capability_evidence, dict
                )
                else None
            )
            invalid = (
                operational is None
                or operational.schema_version != 1
                or operational.family != "operational_administration"
                or plan.plan_family != "operational_administration"
                or operational.operation != plan.operation.value
                or not isinstance(constraints, dict)
                or operational.rollback_available
                or plan.rollback.available
                or stable_hash(operational.baseline)
                != plan.current_state_fingerprint
            )
            if plan.operation == ChangeOperation.CREATE_FULL_BACKUP:
                try:
                    normalized_name = normalize_backup_name(
                        (
                            operational.requested_name
                            if operational
                            else None
                        ),
                        generated_at=self.now(),
                    )
                except (TypeError, ValueError):
                    normalized_name = ""
                invalid = invalid or any(
                    (
                        plan.target_type != "backup",
                        plan.target_id != "local_full_backup",
                        normalized_name
                        != (
                            operational.requested_name
                            if operational
                            else None
                        ),
                        (
                            operational.provider
                            if operational
                            else None
                        )
                        != "upstream_operational_backup",
                        constraints.get("scope") != "snapshot",
                        constraints.get("action") != "create",
                        constraints.get("restore_allowed") is not False,
                        constraints.get("delete_allowed") is not False,
                        constraints.get("arbitrary_arguments_allowed")
                        is not False,
                        stable_hash(
                            {
                                "operation": (
                                    ChangeOperation.CREATE_FULL_BACKUP.value
                                ),
                                "name": (
                                    operational.requested_name
                                    if operational
                                    else None
                                ),
                            }
                        )
                        != plan.proposed_config_hash,
                    )
                )
            elif plan.operation in LIFECYCLE_OPERATIONS:
                expected_target_type = {
                    ChangeOperation.CONTROLLED_RELOAD: "reload_domain",
                    ChangeOperation.RESTART_ADDON: "addon",
                    ChangeOperation.RESTART_HOME_ASSISTANT: (
                        "home_assistant"
                    ),
                }[plan.operation]
                invalid = invalid or any(
                    (
                        plan.target_type != expected_target_type,
                        (
                            operational.requested_name
                            if operational
                            else None
                        )
                        != plan.target_id,
                        (
                            operational.provider
                            if operational
                            else None
                        )
                        != "upstream_operational_lifecycle",
                        constraints.get("arbitrary_arguments_allowed")
                        is not False,
                        (
                            plan.operation
                            == ChangeOperation.CONTROLLED_RELOAD
                            and (
                                plan.target_id not in RELOAD_SERVICES
                                or constraints.get("entry_id_allowed")
                                is not False
                                or constraints.get("reload_all_allowed")
                                is not False
                            )
                        ),
                        (
                            plan.operation
                            == ChangeOperation.RESTART_ADDON
                            and (
                                constraints.get("action") != "restart"
                                or constraints.get(
                                    "other_actions_allowed"
                                )
                                is not False
                            )
                        ),
                        (
                            plan.operation
                            == ChangeOperation.RESTART_HOME_ASSISTANT
                            and constraints.get("confirm") is not True
                        ),
                        stable_hash(
                            {
                                "operation": plan.operation.value,
                                "target_type": plan.target_type,
                                "target_id": plan.target_id,
                            }
                        )
                        != plan.proposed_config_hash,
                    )
                )
            else:
                invalid = True
            if invalid:
                raise GovernanceError(
                    ErrorCode.APPROVAL_HASH_MISMATCH,
                    details={
                        "resource_id": plan.plan_id,
                        "reason": "operational_plan_contract_mismatch",
                    },
                )
            self._require_v2_persisted_plan_safe(plan)
            return
        elif plan.contract_version == CONFIGURATION_PLAN_CONTRACT_VERSION:
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

        return normalize_configuration_validation(
            result,
            known_secrets=self.sensitive_values,
        )

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
                plan.policy_decision = evaluate_change_policy(plan)
                self._bind_new_plan_policy(plan)
                plan.approval.approval_kind = "rollback"
                self._record(plan, "rollback_requested", "success")
                return {
                    "status": "rollback_pending",
                    "plan_id": plan.plan_id,
                    "approval_required": True,
                    "plan_hash": self.plan_hash(plan),
                }
            if plan.status != PlanStatus.ROLLBACK_PENDING:
                raise GovernanceError(ErrorCode.ROLLBACK_NOT_AVAILABLE)
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
            error = self._approval_requirement_error(plan, "rollback")
            if error is not None:
                self._record(
                    plan,
                    "rollback_failed",
                    "rejected",
                    error_code=error.value,
                )
                raise GovernanceError(
                    error,
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
        self._consume_approval_bundle(plan)
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
        # Health remains available when a hand-built F2 record is invalid, but
        # invalid snapshots are never counted as an authoritative policy class.
        plans = self.resolved_plans(validate_policy=False)
        storage = self.repository.health()
        try:
            tasks = self.task_repository.list()
            task_storage = self.task_repository.health()
        except ExecutionTaskStorageError:
            tasks = []
            task_storage = {
                "configured": True,
                "status": "error",
                "record_count": 0,
                "event_count": 0,
                "corruption_count": self.task_repository.corruption_count,
                "write_failures": self.task_repository.write_failures,
                "event_write_failures": (
                    self.task_repository.event_write_failures
                ),
                "materialization_failures": (
                    self.task_repository.materialization_failures
                ),
                "rehydration_attempts": (
                    self.task_repository.rehydration_attempts
                ),
            }
        events = [event.event for plan in plans for event in plan.events]
        task_events = [
            event.event_type for task in tasks for event in task.events
        ]
        operational_plans = [
            plan
            for plan in plans
            if plan.contract_version == OPERATIONAL_PLAN_CONTRACT_VERSION
        ]
        backup_plans = [
            plan
            for plan in operational_plans
            if plan.operation == ChangeOperation.CREATE_FULL_BACKUP
        ]
        operational_failures = sorted(
            (
                event
                for plan in operational_plans
                for event in plan.events
                if event.error_code
            ),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        approval_failures = sorted(
            (
                event
                for plan in plans
                for event in plan.events
                if event.error_code
                and (
                    event.event.startswith("external_approval")
                    or event.event.startswith("elevated_risk")
                    or event.event.startswith("policy_")
                )
            ),
            key=lambda event: event.timestamp,
            reverse=True,
        )
        valid_policy_plans = {
            plan.plan_id: bool(
                plan.policy_decision is not None
                and policy_snapshot_matches(plan)
                and self._approval_bundle_integrity_error(plan) is None
            )
            for plan in plans
        }
        plans_by_policy_class = {
            policy_class.value: sum(
                valid_policy_plans[plan.plan_id]
                and plan.policy_decision is not None
                and plan.policy_decision.policy_class == policy_class
                for plan in plans
            )
            for policy_class in ApprovalPolicyClass
        }
        plans_by_policy_class["legacy_without_policy_snapshot"] = sum(
            plan.policy_decision is None for plan in plans
        )

        def has_active_action(
            plan: ChangePlan, action: ApprovalActionKind
        ) -> bool:
            if not valid_policy_plans[plan.plan_id]:
                return False
            active_action = self._active_challenge_projection(plan)[0]
            return bool(
                active_action == action.value
                and self._active_challenge_matches(
                    plan, self.plan_hash(plan)
                )
            )

        summary = {
            "enabled": True,
            "storage": storage,
            "storage_status": storage["status"],
            "storage_corruption_count": storage["corruption_count"],
            "total_plans": len(plans),
            "plans_awaiting_approval": sum(
                plan.status == PlanStatus.AWAITING_APPROVAL
                and self._approval_is_actionable(plan)
                for plan in plans
            ),
            "plans_requiring_approval": sum(
                self._approval_is_actionable(plan)
                for plan in plans
            ),
            "external_approval_enabled": True,
            "ingress_approval_ui_configured": True,
            "approval_authority_version": APPROVAL_AUTHORITY_VERSION,
            "plans_by_policy_class": plans_by_policy_class,
            "pending_plan_approvals": sum(
                has_active_action(
                    plan, ApprovalActionKind.PLAN_APPROVAL
                )
                for plan in plans
            ),
            "pending_elevated_acknowledgements": sum(
                has_active_action(
                    plan,
                    ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT,
                )
                for plan in plans
            ),
            "granted_elevated_acknowledgements": events.count(
                "elevated_risk_acknowledgement_granted"
            ),
            "consumed_standard_approval_bundles": sum(
                bool(
                    plan.policy_decision
                    and plan.policy_decision.policy_class
                    == ApprovalPolicyClass.STANDARD_ADMIN
                    and any(
                        event.event == "external_approval_consumed"
                        for event in plan.events
                    )
                )
                for plan in plans
            ),
            "consumed_elevated_approval_bundles": sum(
                bool(
                    plan.policy_decision
                    and plan.policy_decision.policy_class
                    == ApprovalPolicyClass.ELEVATED_ADMIN
                    and any(
                        event.event == "external_approval_consumed"
                        for event in plan.events
                    )
                )
                for plan in plans
            ),
            "prohibited_policy_decisions": plans_by_policy_class[
                ApprovalPolicyClass.PROHIBITED.value
            ],
            "policy_snapshot_mismatches": sum(
                event.error_code
                == ErrorCode.POLICY_SNAPSHOT_MISMATCH.value
                for plan in plans
                for event in plan.events
            )
            + sum(
                plan.policy_decision is not None
                and not policy_snapshot_matches(plan)
                for plan in plans
            ),
            "approval_principal_mismatches": sum(
                event.error_code
                == ErrorCode.APPROVAL_PRINCIPAL_MISMATCH.value
                for plan in plans
                for event in plan.events
            )
            + sum(
                self._approval_bundle_integrity_error(plan)
                == ErrorCode.APPROVAL_PRINCIPAL_MISMATCH
                for plan in plans
                if plan.policy_decision is not None
                and policy_snapshot_matches(plan)
            ),
            "approval_sequence_failures": sum(
                event.error_code
                == ErrorCode.APPROVAL_SEQUENCE_FAILURE.value
                for plan in plans
                for event in plan.events
            )
            + sum(
                self._approval_bundle_integrity_error(plan)
                == ErrorCode.APPROVAL_SEQUENCE_FAILURE
                for plan in plans
                if plan.policy_decision is not None
                and policy_snapshot_matches(plan)
            ),
            "pending_challenge_count": sum(
                self._active_challenge_matches(
                    plan, self.plan_hash(plan)
                )
                for plan in plans
            ),
            "plans_with_pending_external_challenge": sum(
                self._active_challenge_matches(
                    plan, self.plan_hash(plan)
                )
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
            "execution_tasks": {
                "storage": task_storage,
                "storage_configured": bool(
                    task_storage.get("configured")
                ),
                "storage_status": task_storage.get("status"),
                "record_count": len(tasks),
                "event_count": sum(
                    len(task.events) for task in tasks
                ),
                "active_tasks_by_state": {
                    state.value: sum(
                        task.state == state for task in tasks
                    )
                    for state in ExecutionTaskState
                    if state not in TERMINAL_TASK_STATES
                    and state.value
                    not in {
                        "waiting_for_lock",
                        "compensating",
                        "partial_application",
                        "compensated",
                        "superseded",
                    }
                },
                "nonterminal_tasks": sum(
                    task.state not in TERMINAL_TASK_STATES
                    for task in tasks
                ),
                "tasks_verifying": sum(
                    task.state == ExecutionTaskState.VERIFYING
                    for task in tasks
                ),
                "tasks_manual_review": sum(
                    task.state
                    == ExecutionTaskState.MANUAL_REVIEW_REQUIRED
                    for task in tasks
                ),
                "tasks_created": task_events.count("task_created"),
                "verified_successes": sum(
                    task.state
                    == ExecutionTaskState.SUCCEEDED_VERIFIED
                    for task in tasks
                ),
                "failed_pre_dispatch": sum(
                    task.state
                    == ExecutionTaskState.FAILED_PRE_DISPATCH
                    for task in tasks
                ),
                "failed_post_dispatch": sum(
                    task.state
                    == ExecutionTaskState.FAILED_POST_DISPATCH
                    for task in tasks
                ),
                "cancellations": sum(
                    task.state
                    == ExecutionTaskState.CANCELLED_PRE_DISPATCH
                    for task in tasks
                ),
                "manual_review_outcomes": sum(
                    task.state
                    == ExecutionTaskState.MANUAL_REVIEW_REQUIRED
                    for task in tasks
                ),
                "no_blind_redispatch_preventions": (
                    task_events.count("duplicate_apply_prevented")
                ),
                "rehydration_attempts": task_storage.get(
                    "rehydration_attempts", 0
                ),
                "reconciliation_runs": self._task_reconciliation_runs,
                "event_write_failures": task_storage.get(
                    "event_write_failures", 0
                ),
                "materialization_failures": task_storage.get(
                    "materialization_failures", 0
                ),
                "last_task_failure_category": next(
                    (
                        str(task.last_error.get("error_code")
                            or task.last_error.get("failure_category"))
                        for task in sorted(
                            tasks,
                            key=lambda item: item.updated_at,
                            reverse=True,
                        )
                        if isinstance(task.last_error, dict)
                        and (
                            task.last_error.get("error_code")
                            or task.last_error.get("failure_category")
                        )
                    ),
                    None,
                ),
            },
        }
        summary["approval_consumption_count"] += sum(
            event.event.endswith("_dispatch_recorded")
            for event in (
                event
                for plan in operational_plans
                for event in plan.events
            )
        )
        lifecycle_types = tuple(
            operation.value
            for operation in (
                ChangeOperation.CREATE_FULL_BACKUP,
                ChangeOperation.CONTROLLED_RELOAD,
                ChangeOperation.RESTART_ADDON,
                ChangeOperation.RESTART_HOME_ASSISTANT,
            )
        )
        tasks_by_plan = {task.plan_id: task for task in tasks}

        def apply_attempt_count(plan: ChangePlan) -> int:
            def plan_event_matches(event: ChangeEvent) -> bool:
                if event.event.endswith("_apply_attempted"):
                    return True
                return (
                    plan.operation == ChangeOperation.CREATE_FULL_BACKUP
                    and event.event
                    == "operational_backup_dispatch_recorded"
                )

            return _reconciled_persisted_invocation_count(
                plan,
                tasks_by_plan.get(plan.plan_id),
                plan_event_matches=plan_event_matches,
                task_event_types=frozenset(
                    {"preflight_started", "duplicate_apply_prevented"}
                ),
            )

        def no_redispatch_prevention_count(plan: ChangePlan) -> int:
            return _reconciled_persisted_invocation_count(
                plan,
                tasks_by_plan.get(plan.plan_id),
                plan_event_matches=lambda event: (
                    event.event.endswith("_no_redispatch_prevented")
                    or event.event.endswith("_dispatch_recovered")
                ),
                task_event_types=frozenset(
                    {"duplicate_apply_prevented"}
                ),
            )

        backup_provider_health = (
            self.operational_gateway.health_snapshot()
            if self.operational_gateway is not None
            else {
                "provider": "operational_backup_provider",
                "configured": False,
                "operational_status": "unavailable",
                "fallback_count": 0,
                "fallback_policy": "none",
            }
        )
        lifecycle_provider_health = (
            self.lifecycle_gateway.health_snapshot()
            if self.lifecycle_gateway is not None
            else {
                "provider": "upstream_operational_lifecycle",
                "configured": False,
                "operational_status": "unavailable",
                "fallback_count": 0,
                "fallback_policy": "none",
            }
        )
        by_type: dict[str, dict[str, Any]] = {}
        for operation in lifecycle_types:
            operation_plans = [
                plan
                for plan in operational_plans
                if plan.operation.value == operation
            ]
            prefix = (
                "operational_backup"
                if operation == ChangeOperation.CREATE_FULL_BACKUP.value
                else operation
            )
            provider_health = (
                backup_provider_health
                if operation
                == ChangeOperation.CREATE_FULL_BACKUP.value
                else lifecycle_provider_health
            )
            by_type[operation] = {
                "plans_created": sum(
                    event.event == f"{prefix}_plan_created"
                    or (
                        operation
                        == ChangeOperation.CREATE_FULL_BACKUP.value
                        and event.event
                        == "operational_backup_plan_created"
                    )
                    for plan in operation_plans
                    for event in plan.events
                ),
                "apply_attempts": sum(
                    apply_attempt_count(plan)
                    for plan in operation_plans
                ),
                "dispatch_attempts": sum(
                    int(
                        (
                            plan.operational.dispatch.get(
                                "attempt_count"
                            )
                            if plan.operational
                            else 0
                        )
                        or 0
                    )
                    for plan in operation_plans
                ),
                "dispatch_successes": sum(
                    event.event.endswith("_provider_completed")
                    for plan in operation_plans
                    for event in plan.events
                ),
                "verified_successes": sum(
                    plan.status == PlanStatus.APPLIED
                    for plan in operation_plans
                ),
                "pre_dispatch_failures": sum(
                    event.error_code is not None
                    and (
                        event.event.endswith("_preflight_failed")
                        or event.event.endswith(
                            "_validation_failed"
                        )
                        or event.event.endswith(
                            "_dispatch_rejected"
                        )
                    )
                    for plan in operation_plans
                    for event in plan.events
                ),
                "post_dispatch_failures": sum(
                    plan.status
                    in {
                        PlanStatus.FAILED,
                        PlanStatus.VERIFICATION_FAILED,
                    }
                    and bool(
                        plan.operational
                        and plan.operational.dispatch.get(
                            "dispatched"
                        )
                    )
                    for plan in operation_plans
                ),
                "verification_failures": sum(
                    plan.status == PlanStatus.VERIFICATION_FAILED
                    for plan in operation_plans
                ),
                "verification_pending_plans": sum(
                    plan.status == PlanStatus.VERIFICATION_REQUIRED
                    for plan in operation_plans
                ),
                "indeterminate_outcomes": sum(
                    plan.execution_outcome
                    in {"indeterminate", "verification_pending"}
                    for plan in operation_plans
                ),
                "active_reconciliations": (
                    self._active_lifecycle_reconciliations
                    if operation
                    in {
                        ChangeOperation.CONTROLLED_RELOAD.value,
                        ChangeOperation.RESTART_ADDON.value,
                        ChangeOperation.RESTART_HOME_ASSISTANT.value,
                    }
                    else 0
                ),
                "eligible_readback_reconciliations": sum(
                    self._eligible_lifecycle_reconciliation(plan)
                    for plan in operation_plans
                ),
                "no_blind_redispatch_preventions": sum(
                    no_redispatch_prevention_count(plan)
                    for plan in operation_plans
                ),
                "last_successful_operation_timestamp": next(
                    (
                        plan.applied_at
                        for plan in sorted(
                            operation_plans,
                            key=lambda item: item.applied_at or "",
                            reverse=True,
                        )
                        if plan.applied_at
                    ),
                    None,
                ),
                "last_failure_category": next(
                    (
                        event.error_code
                        for event in sorted(
                            (
                                event
                                for plan in operation_plans
                                for event in plan.events
                                if event.error_code
                            ),
                            key=lambda item: item.timestamp,
                            reverse=True,
                        )
                    ),
                    None,
                ),
                "fallback_count": 0,
                "provider_identity": provider_health.get("provider"),
                "provider_availability": provider_health.get(
                    "operational_status"
                ),
                "provider_contract_status": (
                    "exact"
                    if provider_health.get("operational_status")
                    == "available"
                    else "unavailable_or_unverified"
                ),
            }
        summary["operational_administration"] = {
            "counter_sources": {
                "plans_and_outcomes": (
                    "persistent_governance_and_execution_task_state"
                ),
                "active_applies": "current_process_state",
                "provider": "cumulative_process_state",
            },
            "plans_by_type": {
                operation: sum(
                    plan.operation.value == operation
                    for plan in operational_plans
                )
                for operation in lifecycle_types
            },
            "operations": by_type,
            "backup_plans_created": events.count(
                "operational_backup_plan_created"
            ),
            "backup_applies_attempted": events.count(
                "operational_backup_dispatch_recorded"
            ),
            "successful_backups": sum(
                plan.status == PlanStatus.APPLIED
                for plan in backup_plans
            ),
            "failed_backups": sum(
                plan.status
                in {PlanStatus.FAILED, PlanStatus.VERIFICATION_FAILED}
                for plan in backup_plans
            ),
            "indeterminate_outcomes": sum(
                plan.status == PlanStatus.VERIFICATION_REQUIRED
                for plan in operational_plans
            ),
            "verification_failures": sum(
                plan.status == PlanStatus.VERIFICATION_FAILED
                for plan in operational_plans
            ),
            "active_operational_applies": sum(
                lock.locked()
                for key, lock in self._target_locks.items()
                if (
                    key == ("operational_backup", "global")
                    or (
                        isinstance(key, tuple)
                        and isinstance(key[0], str)
                        and key[0].startswith("operational_")
                    )
                )
            ),
            "last_successful_backup_at": next(
                (
                    plan.applied_at
                    for plan in sorted(
                        backup_plans,
                        key=lambda item: item.applied_at or "",
                        reverse=True,
                    )
                    if plan.applied_at
                ),
                None,
            ),
            "last_operational_failure_category": (
                operational_failures[0].error_code
                if operational_failures
                else None
            ),
            "provider": backup_provider_health,
            "lifecycle_provider": lifecycle_provider_health,
            "fallback_count": 0,
            "rollback_available": False,
        }
        return summary


def _mismatch_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return [
        item["field"]
        for item in structured_diff(expected, actual)["changed_fields"]
    ]


def _configuration_validation_changed(
    planned: Any, current: Any
) -> bool:
    """Compare validation meaning without treating check time as drift."""

    if not isinstance(planned, dict) or not isinstance(current, dict):
        return planned != current
    fields = ("status", "failure_category", "evidence")
    return any(planned.get(field) != current.get(field) for field in fields)


def _lifecycle_expected_effects(
    operation: ChangeOperation, target_id: str
) -> list[str]:
    if operation == ChangeOperation.CONTROLLED_RELOAD:
        return [
            f"Invoke exactly {target_id}.reload with no service data.",
            "Keep Home Assistant available while the selected configuration domain reloads.",
        ]
    if operation == ChangeOperation.RESTART_ADDON:
        return [
            f"Restart exactly the installed add-on {target_id}.",
            "Temporarily interrupt that add-on while preserving its installed version and configuration.",
        ]
    return [
        "Restart Home Assistant exactly once.",
        "Temporarily interrupt Home Assistant and dependent providers before full reconciliation.",
    ]


def _lifecycle_verification_requirements(
    operation: ChangeOperation,
) -> list[str]:
    if operation == ChangeOperation.CONTROLLED_RELOAD:
        return [
            "provider_completion",
            "home_assistant_connected",
            "post_reload_configuration_valid",
            "reload_service_available",
            "domain_state_inventory_readable",
        ]
    if operation == ChangeOperation.RESTART_ADDON:
        return [
            "exact_slug",
            "exact_name",
            "installed_version_unchanged",
            "running_state",
            "restart_evidence_beyond_current_running_state",
            "upstream_readmission_when_applicable",
            "engineering_process_recovery_when_self_restart",
        ]
    return [
        "restart_dispatch_evidence",
        "home_assistant_reconnected",
        "home_assistant_identity_unchanged",
        "engineering_runtime_restored",
        "tool_catalog_restored",
        "governance_storage_healthy",
        "audit_storage_healthy",
        "upstream_exact_admission_restored",
        "dependency_index_recovery_reported",
        "post_restart_configuration_valid",
        "zero_fallback",
    ]


def _operational_failure_details(
    category: str, *, dispatched: bool
) -> dict[str, Any]:
    return {
        "failure_category": category,
        "failure_stage": (
            "post_dispatch" if dispatched else "pre_dispatch"
        ),
        "provider_dispatch_occurred": dispatched,
        "action_attempted": dispatched,
        "fallback": "none",
        "fallback_occurred": False,
        "redispatch_performed": False,
        "required_action": (
            "resume_readback_only_verification"
            if dispatched
            else "refresh_provider_evidence_and_replan"
        ),
    }


def _automation_id_mismatch(
    expected_automation_id: str, config: dict[str, Any] | None
) -> bool:
    """Check identity metadata independently from behavioral normalization."""

    return bool(
        isinstance(config, dict)
        and config.get("id") is not None
        and str(config["id"]) != expected_automation_id
    )
