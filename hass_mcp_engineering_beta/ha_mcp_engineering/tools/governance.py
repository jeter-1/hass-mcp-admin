"""Beta-native governed configuration change MCP tools."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field
from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from ..entity_ids import ENTITY_ID_PATTERN, MAX_ENTITY_ID_LENGTH
from ..errors import ErrorCode, GovernanceError
from ..request_context import current_telemetry

from ..governance import GOVERNANCE
from ..governance import dashboard_metadata as dashboard_metadata_contract
from ..governance import helper_options as helper_options_contract
from ..governance import scene_configuration as scene_configuration_contract
from ..governance.label_registry import (
    COLOR_HEX_PATTERN,
    LABEL_ID_PATTERN,
    MAX_DESCRIPTION_LENGTH,
    NAME_PATTERN,
    THEME_COLORS,
)
from ..governance.label_registry_planning import MAX_CHILDREN as LABEL_MAX_CHILDREN
from ..sanitization import sanitize_untrusted_data
from ..tool_framework import run_structured
from .compatibility import SETTINGS


def _known_secrets() -> tuple[str, ...] | None:
    try:
        return tuple(GOVERNANCE.require().sensitive_values)
    except Exception:
        return None


def _identifier_is_safe(value: Any, known_secrets: tuple[str, ...]) -> bool:
    """True only when the strict sanitizer leaves the value unchanged."""

    try:
        result = sanitize_untrusted_data(value, known_secrets=known_secrets)
    except Exception:
        return False
    return (
        not result.failed_closed
        and not result.redaction_applied
        and type(result.value) is type(value)
        and result.value == value
    )


def _identifier_metadata(**fields: Any) -> dict[str, Any]:
    """HAMCP-191 H1g: caller identifiers echoed in response metadata.

    Each one passes the strict sanitizer with the governance known secrets;
    one the sanitizer would change is withheld (omitted), on success and on
    refusal alike. Without a governance service every identifier is
    withheld. Fixed metadata values are not caller input.
    """

    known_secrets = _known_secrets()
    if known_secrets is None:
        return {}
    return {
        key: value
        for key, value in fields.items()
        if _identifier_is_safe(value, known_secrets)
    }


#: HAMCP-191 H1g-residual: the caller-supplied identifier inputs of every
#: governance tool, screened once at the tool boundary before any tool work.
#: ``name[].field`` selects one field of each item of a list input; a whole
#: list or mapping input is screened with its keys. Every other input is a
#: closed literal, a bounded number, an opaque server-issued cursor, or free
#: text and configuration that the plan persistence chokepoint screens.
IDENTIFIER_INPUTS: dict[str, tuple[str, ...]] = {
    "create_backup_plan": (),
    "create_reload_plan": (),
    "create_helper_state_plan": ("entity_id",),
    "create_entity_metadata_plan": (
        "entity_id",
        "area_id",
        "add_labels",
        "remove_labels",
        "categories",
    ),
    "create_entity_availability_exposure_plan": ("entity_ids",),
    "create_label_plan": ("operations[].label_id", "operations[].entity_ids"),
    "create_configuration_delete_plan": (
        "deletions[].resource_type",
        "deletions[].target_id",
    ),
    "create_input_select_option_plan": ("entity_id",),
    "create_helper_options_plan": ("changes[].entity_id",),
    "create_dashboard_metadata_plan": ("url_path",),
    "create_scene_configuration_plan": ("config_id", "entity_id"),
    "create_addon_restart_plan": ("addon_slug",),
    "create_home_assistant_restart_plan": (),
    "create_change_plan": ("operation", "automation_id"),
    "create_configuration_plan": (
        "operations[].operation_id",
        "operations[].resource_type",
        "operations[].action",
        "operations[].target_id",
        "operations[].helper_type",
        "operations[].depends_on",
    ),
    "create_dashboard_update_plan": (
        "url_path",
        "patch_operations[].operation_id",
        "patch_operations[].operation",
        "patch_operations[].path",
    ),
    "get_change_plan": ("plan_id",),
    "list_change_plans": ("status",),
    "get_execution_task": ("task_id",),
    "list_execution_tasks": ("state", "terminal_outcome", "plan_id"),
    "cancel_execution_task": ("task_id",),
    "reverify_configuration_task": ("task_id", "expected_plan_hash", "request_id"),
    "approve_change_plan": ("plan_id", "expected_plan_hash"),
    "apply_change_plan": ("plan_id", "expected_plan_hash"),
    "rollback_change": ("plan_id", "expected_plan_hash"),
}

IDENTIFIER_REFUSAL_REASON = "identifier_contains_prohibited_sensitive_data"


def _identifier_values(arguments: Any, path: str) -> list[Any]:
    if not isinstance(arguments, dict):
        return []
    name, _, field = path.partition("[].")
    value = arguments.get(name)
    if not field:
        return [] if value is None else [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [
        item[field]
        for item in value
        if isinstance(item, dict) and item.get(field) is not None
    ]


def screen_identifiers(operation: str, arguments: Any) -> None:
    """Refuse a request whose identifier inputs the strict sanitizer would change.

    The refusal carries one fixed reason and never the identifier, so no
    early-return or error path of the tool can echo it.
    """

    known_secrets = _known_secrets() or ()
    for path in IDENTIFIER_INPUTS[operation]:
        for value in _identifier_values(arguments, path):
            if not _identifier_is_safe(value, known_secrets):
                raise GovernanceError(
                    ErrorCode.INVALID_REQUEST,
                    details={"reason": IDENTIFIER_REFUSAL_REASON},
                )


def _screened(operation: str, arguments: Any, action):
    """Run ``action`` only after the boundary identifier screen passes.

    ``arguments`` is the tool's own ``locals()`` (its inputs), or the raw
    argument object of a tool that receives one.
    """

    def screened():
        screen_identifiers(operation, arguments)
        return action()

    return screened


class ConfigurationOperation(TypedDict):
    """One explicit operation in a bounded ordered configuration plan."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    operation_id: str
    resource_type: Literal["automation", "script", "helper"]
    action: Literal["create", "update"]
    target_id: str
    proposed_config: dict[str, Any]
    helper_type: NotRequired[Literal["input_boolean", "input_number"]]
    depends_on: NotRequired[Annotated[list[str], Field(max_length=8)]]


ConfigurationOperations = Annotated[
    list[ConfigurationOperation],
    Field(min_length=1, max_length=8),
]


class DashboardPatchOperation(TypedDict):
    """One bounded declarative dashboard JSON Pointer change."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    operation_id: Annotated[
        str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    ]
    operation: Literal["add", "replace", "remove"]
    path: Annotated[str, Field(min_length=1, max_length=1024)]
    value: NotRequired[Any]


DashboardPatchOperations = Annotated[
    list[DashboardPatchOperation], Field(min_length=1, max_length=16)
]


async def create_backup_plan(
    backup_name: Annotated[str, Field(max_length=96)] = "",
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose one governed local backup; planning never dispatches creation.

    The operation is fixed to the reviewed snapshot/create provider contract.
    Restore, delete, download, partial selection, retention, credentials, and
    arbitrary provider arguments are not accepted. Exact external administrator
    approval and a later apply_change_plan call are required.
    """
    return await run_structured(
        "create_backup_plan",
        "Created a governed backup proposal without dispatching backup creation.",
        _screened(
            "create_backup_plan",
            locals(),
            lambda: GOVERNANCE.require().create_backup_plan(
                backup_name=backup_name,
                expiration_minutes=expiration_minutes,
            ),
        ),
        metadata={
            "resource_type": "backup",
            "operation": "create_full_backup",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_reload_plan(
    reload_target: Literal[
        "automation",
        "script",
        "input_boolean",
        "input_number",
    ],
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose one exact controlled reload; planning never dispatches it.

    Only the four declared Home Assistant domains are reachable. Arbitrary
    services, service data, entity targets, integration entries, and reload-all
    behavior are excluded. External administrator approval and the shared
    apply_change_plan lifecycle are required.
    """
    return await run_structured(
        "create_reload_plan",
        "Created a governed controlled-reload proposal without dispatching it.",
        _screened(
            "create_reload_plan",
            locals(),
            lambda: GOVERNANCE.require().create_reload_plan(
                reload_target=reload_target,
                expiration_minutes=expiration_minutes,
            ),
        ),
        metadata={
            "resource_type": "reload_domain",
            "operation": "controlled_reload",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_helper_state_plan(
    entity_id: Annotated[
        str,
        Field(
            min_length=15,
            max_length=128,
            pattern=r"^input_boolean\.[a-z0-9_]{1,114}$",
        ),
    ],
    desired_state: Literal["on", "off"],
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose one exact input_boolean on/off transition without dispatch.

    Planning reads the exact current state. If it already matches, the tool
    returns a verified no-change result and creates no plan. Otherwise the
    exact target and desired state require external administrator approval and
    the shared apply_change_plan lifecycle. Toggle, arbitrary services,
    service data, physical domains, and fallback are unreachable.
    """
    return await run_structured(
        "create_helper_state_plan",
        "Created or resolved one exact governed input-boolean state proposal.",
        _screened(
            "create_helper_state_plan",
            locals(),
            lambda: GOVERNANCE.require().create_helper_state_plan(
                entity_id=entity_id,
                desired_state=desired_state,
                expiration_minutes=expiration_minutes,
            ),
        ),
        metadata={
            "resource_type": "input_boolean",
            **_identifier_metadata(resource_id=entity_id),
            "operation": "set_input_boolean_state",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_addon_restart_plan(
    addon_slug: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$",
        ),
    ],
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose restarting one exact installed add-on without dispatching it.

    The provider argument is fixed to action=restart for the exact planned
    slug. Start, stop, install, uninstall, update, options mutation, proxy
    requests, and arbitrary arguments are unreachable.
    """
    return await run_structured(
        "create_addon_restart_plan",
        "Created a governed add-on restart proposal without dispatching it.",
        _screened(
            "create_addon_restart_plan",
            locals(),
            lambda: GOVERNANCE.require().create_addon_restart_plan(
                addon_slug=addon_slug,
                expiration_minutes=expiration_minutes,
            ),
        ),
        metadata={
            "resource_type": "addon",
            "operation": "restart_addon",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_home_assistant_restart_plan(
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose one Home Assistant restart without dispatching it.

    Full configuration validation and exact runtime evidence are captured at
    planning and rechecked before the shared externally approved apply path.
    No restart variants or arbitrary service data are accepted.
    """
    return await run_structured(
        "create_home_assistant_restart_plan",
        "Created a governed Home Assistant restart proposal without dispatching it.",
        _screened(
            "create_home_assistant_restart_plan",
            locals(),
            lambda: (
                GOVERNANCE.require().create_home_assistant_restart_plan(
                    expiration_minutes=expiration_minutes,
                )
            ),
        ),
        metadata={
            "resource_type": "home_assistant",
            "operation": "restart_home_assistant",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_change_plan(
    title: str,
    description: str,
    operation: str,
    automation_id: str,
    proposed_config: dict[str, Any],
    expiration_minutes: int = 120,
    caller_context: dict = None,
) -> str:
    """Dry-run a create_automation or update_automation proposal.

    This validates, normalizes, diffs, fingerprints, and risk-classifies the
    proposal without writing to Home Assistant. High-risk plans are reviewable
    but cannot be approved or applied in this milestone.
    """
    return await run_structured(
        "create_change_plan",
        "Created a dry-run automation change plan without writing to Home Assistant.",
        _screened(
            "create_change_plan",
            locals(),
            lambda: GOVERNANCE.require().create_plan(
                title=title,
                description=description,
                operation=operation,
                automation_id=automation_id,
                proposed_config=proposed_config,
                expiration_minutes=expiration_minutes,
                caller_context=caller_context,
            ),
        ),
        metadata={
            "resource_type": "automation",
            **_identifier_metadata(resource_id=automation_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_configuration_plan(
    title: str,
    description: str,
    operations: ConfigurationOperations,
    expiration_minutes: int = 120,
    caller_context: dict = None,
) -> str:
    """Dry-run one bounded, ordered configuration proposal.

    Supported operation types are validated by governance. Planning performs no
    Home Assistant write. The exact ordered plan requires one external
    administrator approval before stop-on-first-failure governed apply.
    """
    return await run_structured(
        "create_configuration_plan",
        "Created a bounded ordered configuration plan without writing to Home Assistant.",
        _screened(
            "create_configuration_plan",
            locals(),
            lambda: GOVERNANCE.require().create_configuration_plan(
                title=title,
                description=description,
                operations=operations,
                expiration_minutes=expiration_minutes,
                caller_context=caller_context,
            ),
        ),
        metadata={"resource_type": "configuration_plan"},
        response_limit=SETTINGS.response_size_limit,
    )


async def create_dashboard_update_plan(
    title: Annotated[str, Field(min_length=1, max_length=160)],
    description: Annotated[str, Field(max_length=2000)],
    url_path: Annotated[
        str, Field(pattern=r"^[a-z0-9_-]{1,256}$")
    ],
    patch_operations: DashboardPatchOperations,
    expiration_minutes: Annotated[int, Field(ge=5, le=1440)] = 120,
) -> str:
    """Propose one bounded update to an existing storage-mode dashboard.

    Planning performs exact readback and creates no Home Assistant write. The
    proposal requires external administrator approval and later application.
    Dashboard save is explicitly non-atomic with external UI editors; do not
    edit the target dashboard while an approved update is executing.
    """

    return await run_structured(
        "create_dashboard_update_plan",
        "Created a governed dashboard update proposal without dispatching it.",
        _screened(
            "create_dashboard_update_plan",
            locals(),
            lambda: GOVERNANCE.require().create_dashboard_update_plan(
                title=title,
                description=description,
                url_path=url_path,
                patch_operations=patch_operations,
                expiration_minutes=expiration_minutes,
            ),
        ),
        metadata={
            "resource_type": "dashboard",
            **_identifier_metadata(resource_id=url_path),
            "operation": "update_dashboard",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def get_change_plan(
    plan_id: str,
    detail_section: Literal[
        "summary", "obligation_evidence", "downstream_profiles"
    ] = "summary",
    cursor: Annotated[str, Field(max_length=2048)] = "",
    page_size: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """Return one bounded persisted plan summary and optional evidence page."""
    return await run_structured(
        "get_change_plan",
        "Returned the requested governed change plan.",
        _screened(
            "get_change_plan",
            locals(),
            lambda: GOVERNANCE.require().get_plan_observability(
                plan_id,
                detail_section=detail_section,
                cursor=cursor,
                page_size=page_size,
                response_limit=SETTINGS.response_size_limit,
            ),
        ),
        metadata={
            "resource_type": "change_plan",
            **_identifier_metadata(resource_id=plan_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def list_change_plans(status: str = "", limit: int = 20) -> str:
    """List bounded change-plan summaries, optionally filtered by exact status."""
    return await run_structured(
        "list_change_plans",
        "Returned bounded governed change-plan summaries.",
        _screened(
            "list_change_plans",
            locals(),
            lambda: GOVERNANCE.require().list_plans(status=status, limit=limit),
        ),
        response_limit=SETTINGS.response_size_limit,
    )


async def _get_execution_task(task_id):
    from ..fan.service import FAN_OPERATIONS
    service = FAN_OPERATIONS.service
    if service is not None and service.load(task_id) is not None:
        return await service.reconcile(task_id)
    from ..power.service import POWER_OPERATIONS
    service = POWER_OPERATIONS.service
    if service is not None and service.load(task_id) is not None:
        return await service.reconcile(task_id)
    return GOVERNANCE.require().get_execution_task(task_id)


async def get_execution_task(
    task_id: Annotated[
        str, Field(pattern=r"^[a-f0-9]{32}$")
    ],
) -> str:
    """Return one durable execution task by its exact opaque identifier."""
    return await run_structured(
        "get_execution_task",
        "Returned the requested durable execution task.",
        _screened(
            "get_execution_task",
            locals(),
            lambda: _get_execution_task(task_id),
        ),
        metadata={
            "resource_type": "execution_task",
            **_identifier_metadata(resource_id=task_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def list_execution_tasks(
    state: Literal[
        "",
        "created",
        "preflight",
        "dispatching",
        "observing",
        "verifying",
        "succeeded_verified",
        "failed_pre_dispatch",
        "failed_post_dispatch",
        "manual_review_required",
        "cancelled_pre_dispatch",
    ] = "",
    terminal_outcome: Annotated[str, Field(max_length=96)] = "",
    plan_id: Annotated[
        str, Field(pattern=r"^$|^[a-f0-9]{32}$")
    ] = "",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """List bounded execution-task summaries with exact optional filters."""
    return await run_structured(
        "list_execution_tasks",
        "Returned bounded durable execution-task summaries.",
        _screened(
            "list_execution_tasks",
            locals(),
            lambda: GOVERNANCE.require().list_execution_tasks(
                state=state,
                terminal_outcome=terminal_outcome,
                plan_id=plan_id,
                limit=limit,
            ),
        ),
        metadata={"resource_type": "execution_task"},
        response_limit=SETTINGS.response_size_limit,
    )


async def cancel_execution_task(
    task_id: Annotated[
        str, Field(pattern=r"^[a-f0-9]{32}$")
    ],
) -> str:
    """Cancel one task only while it is durably pre-dispatch.

    Cancellation is not rollback or compensation. Once dispatch was attempted,
    the task continues through readback-only verification or manual review.
    """
    return await run_structured(
        "cancel_execution_task",
        "Processed the bounded pre-dispatch task cancellation request.",
        _screened(
            "cancel_execution_task",
            locals(),
            lambda: GOVERNANCE.require().cancel_execution_task(task_id),
        ),
        metadata={
            "resource_type": "execution_task",
            **_identifier_metadata(resource_id=task_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def approve_change_plan(
    plan_id: str, expected_plan_hash: str, approval_note: str = ""
) -> str:
    """Request administrator approval for the exact immutable plan hash.

    This MCP tool never grants approval. A Home Assistant administrator must
    approve or reject the challenge in the admin-only Ingress panel.
    """
    return await run_structured(
        "approve_change_plan",
        "Requested external administrator approval bound to the exact plan content.",
        _screened(
            "approve_change_plan",
            locals(),
            lambda: GOVERNANCE.require().approve(
                plan_id, expected_plan_hash, approval_note
            ),
        ),
        metadata={
            "resource_type": "change_plan",
            **_identifier_metadata(resource_id=plan_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def apply_change_plan(plan_id: str, expected_plan_hash: str = "") -> str:
    """Apply one externally approved plan with stale-state protection and verification."""
    return await run_structured(
        "apply_change_plan",
        "Processed the approved governed configuration change.",
        _screened(
            "apply_change_plan",
            locals(),
            lambda: GOVERNANCE.require().apply(plan_id, expected_plan_hash),
        ),
        metadata={
            "resource_type": "change_plan",
            **_identifier_metadata(resource_id=plan_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def rollback_change(plan_id: str, expected_plan_hash: str = "") -> str:
    """Create a separate governed reverse-update plan from exact evidence.

    The request performs no Home Assistant mutation. Approve and apply the
    returned rollback plan through the ordinary governance tools. Creation and
    operational rollback remain unavailable because deletion and generalized
    compensation are outside this milestone.
    """
    return await run_structured(
        "rollback_change",
        "Processed the governed rollback lifecycle step.",
        _screened(
            "rollback_change",
            locals(),
            lambda: GOVERNANCE.require().rollback_change(plan_id, expected_plan_hash),
        ),
        metadata={
            "resource_type": "change_plan",
            **_identifier_metadata(resource_id=plan_id),
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def reverify_configuration_task(
    task_id: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)],
    expected_plan_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)],
    request_id: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", min_length=36, max_length=36)],
) -> str:
    """Record a dated readback of one retained configuration task and exact plan.

    Reads all approved objects and checks configuration; never rewrites them,
    consumes approval, or dispatches a device action. Original failed execution
    remains unchanged. A separate durable receipt may resolve its review.
    Reads are non-atomic. The same request ID replays retained evidence; a fresh
    observation requires a new request ID. Interrupted checks remain unresolved.
    """
    metadata = {"provider": "engineering", "fallback": "none",
                "home_assistant_mutation_allowed": False, "completeness": "failed"}

    async def action():
        data = await GOVERNANCE.require().reverify_configuration_task(
            task_id=task_id, expected_plan_hash=expected_plan_hash, request_id=request_id,
        )
        completeness = ("complete" if data["review_resolved"] else
                        "partial" if data.get("operations") else "failed")
        metadata.update(completeness=completeness, observation_status=data["status"])
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.completeness = completeness
            telemetry.result_status = "success" if data["review_resolved"] else "partial" if data.get("operations") else "failure"
        return data

    return await run_structured(
        "reverify_configuration_task",
        "Processed the supplementary configuration verification request.",
        _screened("reverify_configuration_task", locals(), action),
        metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )


class ConfigurationReverificationTool(Tool):
    """Reject unknown/coerced/private input before SDK coercion or error rendering."""

    async def run(self, arguments, context=None, convert_result=False):
        from ..governance.configuration_reverification import validate_binding, ReverificationRefused
        try:
            if not isinstance(arguments, dict) or set(arguments) != {"task_id", "expected_plan_hash", "request_id"}:
                raise ReverificationRefused("invalid_request")
            validate_binding(**arguments)
        except ReverificationRefused:
            async def invalid():
                raise ValueError("Invalid configuration re-verification arguments.")
            rendered = await run_structured(
                "reverify_configuration_task", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit,
            )
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_reverification_tool():
    tool = ConfigurationReverificationTool.from_function(
        reverify_configuration_task,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    tool.parameters["additionalProperties"] = False
    return tool


_ENTITY_METADATA_TEXT = {
    "anyOf": [
        {"type": "string", "minLength": 1, "maxLength": 100},
        {"type": "null"},
    ]
}
_ENTITY_METADATA_REFERENCE = {"type": "string", "minLength": 1, "maxLength": 255}
_ENTITY_METADATA_REFERENCES = {
    "type": "array",
    "maxItems": 16,
    "uniqueItems": True,
    "items": _ENTITY_METADATA_REFERENCE,
}
#: The closed published input schema (HAMCP-069A-1 §1.1).  Nullable fields
#: carry no default: ``null`` is a request to clear or revert, so a field
#: that is not supplied must stay absent rather than become ``null``.
ENTITY_METADATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_entity_metadata_planArguments",
    "additionalProperties": False,
    "required": ["entity_id"],
    "properties": {
        "entity_id": {
            "type": "string",
            "maxLength": MAX_ENTITY_ID_LENGTH,
            "pattern": ENTITY_ID_PATTERN.pattern,
        },
        "name": _ENTITY_METADATA_TEXT,
        "icon": {
            "anyOf": [
                {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": r"^[a-z0-9-]+:[a-z0-9-]+$",
                },
                {"type": "null"},
            ]
        },
        "aliases": {
            "type": "array",
            "maxItems": 16,
            "items": _ENTITY_METADATA_TEXT,
        },
        "area_id": {"anyOf": [_ENTITY_METADATA_REFERENCE, {"type": "null"}]},
        "hidden": {"type": "boolean"},
        "add_labels": _ENTITY_METADATA_REFERENCES,
        "remove_labels": _ENTITY_METADATA_REFERENCES,
        "categories": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 8,
            "propertyNames": {"pattern": "^[a-z0-9_]+$", "maxLength": 255},
            "additionalProperties": {
                "anyOf": [_ENTITY_METADATA_REFERENCE, {"type": "null"}]
            },
        },
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _entity_metadata_plan(arguments: Any) -> str:
    return await run_structured(
        "create_entity_metadata_plan",
        "Created or resolved one governed entity registry metadata proposal "
        "without dispatching it.",
        _screened(
            "create_entity_metadata_plan",
            arguments,
            lambda: GOVERNANCE.require().create_entity_metadata_plan(arguments),
        ),
        metadata={
            "resource_type": "entity_registry_metadata",
            "operation": "update_entity_registry_metadata",
            "provider": "direct_home_assistant_entity_registry",
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_entity_metadata_plan(
    entity_id: str,
    name: str | None = None,
    icon: str | None = None,
    aliases: list[str | None] | None = None,
    area_id: str | None = None,
    hidden: bool | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    categories: dict[str, str | None] | None = None,
    expiration_minutes: int = 120,
) -> str:
    """Propose one governed entity registry metadata update without dispatch.

    One exact entity_id. Supply only the fields to change: name, icon, the
    complete aliases list, area_id, hidden, add_labels/remove_labels, and
    per-scope categories. null clears name, icon, area_id or a category scope.
    Renaming, enable/disable, exposure, options, device fields, label creation
    and multi-entity targets are refused before any Home Assistant read. If
    nothing would change, no plan is created. Otherwise external administrator
    approval (plus the elevated acknowledgement when classified elevated) and
    apply_change_plan are required. Rollback restores the prior values.
    """
    # The served tool passes the raw argument object, which preserves the
    # difference between an absent field and an explicit null.  A direct call
    # cannot express null, so only supplied values are forwarded.
    supplied = {
        key: value
        for key, value in {
            "name": name,
            "icon": icon,
            "aliases": aliases,
            "area_id": area_id,
            "hidden": hidden,
            "add_labels": add_labels,
            "remove_labels": remove_labels,
            "categories": categories,
        }.items()
        if value is not None
    }
    return await _entity_metadata_plan(
        {
            "entity_id": entity_id,
            **supplied,
            "expiration_minutes": expiration_minutes,
        }
    )


class EntityMetadataPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation.

    SDK coercion is never applied: unknown keys are refused with
    ``entity_metadata_field_not_supported`` and every value is type-exact,
    before any Home Assistant read.
    """

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _entity_metadata_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_entity_metadata_tool():
    tool = EntityMetadataPlanTool.from_function(
        create_entity_metadata_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = ENTITY_METADATA_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-069B).  One operation over 1-8
#: exact entities; ``assistant`` is required for expose/unexpose and refused
#: otherwise by the planner.  Alexa is not offered (OD-3).
ENTITY_AVAILABILITY_EXPOSURE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_entity_availability_exposure_planArguments",
    "additionalProperties": False,
    "required": ["operation", "entity_ids"],
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["disable", "enable", "expose", "unexpose"],
        },
        "entity_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "maxLength": MAX_ENTITY_ID_LENGTH,
                "pattern": ENTITY_ID_PATTERN.pattern,
            },
        },
        "assistant": {
            "type": "string",
            "enum": ["conversation", "cloud.google_assistant"],
        },
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _entity_availability_exposure_plan(arguments: Any) -> str:
    return await run_structured(
        "create_entity_availability_exposure_plan",
        "Created one governed entity availability or exposure proposal "
        "without dispatching it.",
        _screened(
            "create_entity_availability_exposure_plan",
            arguments,
            lambda: GOVERNANCE.require().create_entity_availability_exposure_plan(
                arguments
            ),
        ),
        metadata={
            "resource_types": [
                "entity_registry_availability",
                "entity_assistant_exposure",
            ],
            "provider": "direct_home_assistant_entity_registry",
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_entity_availability_exposure_plan(
    operation: str,
    entity_ids: list[str],
    assistant: str | None = None,
    expiration_minutes: int = 120,
) -> str:
    """Propose governed entity enable/disable or assistant exposure, no dispatch.

    operation is disable, enable, expose or unexpose over 1-8 exact
    registered entity_ids. expose/unexpose require assistant (conversation
    for Assist, or cloud.google_assistant); Alexa is refused. An enable plan
    covers one config entry. Devices, config entries, Core-internal disables
    and already-applied states are refused before any plan is stored.
    Protected entities need the elevated acknowledgement. External
    administrator approval and apply_change_plan are required; children run
    in order, non-atomically, stopping on the first failure. Until a signed
    registry admits these capabilities the tool refuses with
    pending_signed_registry_admission.
    """
    return await _entity_availability_exposure_plan(
        {
            "operation": operation,
            "entity_ids": entity_ids,
            **({"assistant": assistant} if assistant is not None else {}),
            "expiration_minutes": expiration_minutes,
        }
    )


class EntityAvailabilityExposurePlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation."""

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _entity_availability_exposure_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_entity_availability_exposure_tool():
    tool = EntityAvailabilityExposurePlanTool.from_function(
        create_entity_availability_exposure_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = ENTITY_AVAILABILITY_EXPOSURE_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-165).  1-8 ordered label
#: operations; create needs ``name`` and update needs ``label_id`` plus at
#: least one field.  The planner's closed validator is authoritative.
LABEL_PLAN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_label_planArguments",
    "additionalProperties": False,
    "required": ["operations"],
    "properties": {
        "operations": {
            "type": "array",
            "minItems": 1,
            "maxItems": LABEL_MAX_CHILDREN,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation"],
                "properties": {
                    "operation": {"type": "string", "enum": ["create", "update"]},
                    "label_id": {
                        "type": "string",
                        "pattern": LABEL_ID_PATTERN.pattern,
                    },
                    "name": {"type": "string", "pattern": NAME_PATTERN.pattern},
                    "color": {
                        "anyOf": [
                            {"type": "string", "enum": list(THEME_COLORS)},
                            {"type": "string", "pattern": COLOR_HEX_PATTERN.pattern},
                            {"type": "null"},
                        ]
                    },
                    "icon": {
                        "anyOf": [
                            {"type": "string", "pattern": "^[a-z0-9-]+:[a-z0-9-]+$"},
                            {"type": "null"},
                        ]
                    },
                    "description": {
                        "anyOf": [
                            {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_DESCRIPTION_LENGTH,
                            },
                            {"type": "null"},
                        ]
                    },
                    "entity_ids": {
                        "type": "array",
                        "maxItems": LABEL_MAX_CHILDREN,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "maxLength": MAX_ENTITY_ID_LENGTH,
                            "pattern": ENTITY_ID_PATTERN.pattern,
                        },
                    },
                },
            },
        },
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _label_plan(arguments: Any) -> str:
    return await run_structured(
        "create_label_plan",
        "Created one governed label proposal without dispatching it.",
        _screened(
            "create_label_plan",
            arguments,
            lambda: GOVERNANCE.require().create_label_plan(arguments),
        ),
        metadata={
            "resource_types": ["label_registry_entry", "entity_registry_metadata"],
            "provider": "direct_home_assistant_label_registry",
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_label_plan(
    operations: list[dict[str, Any]],
    expiration_minutes: int = 120,
) -> str:
    """Propose governed label create/edit and entity assignment, no dispatch.

    operations is 1-8 ordered items: create {name, color?, icon?,
    description?, entity_ids?} or update {label_id, name?, color?, icon?,
    description?, entity_ids?}. color is a Home Assistant theme colour,
    #RRGGBB or null; icon is prefix:name or null. entity_ids assigns the
    label to registered entities after it exists. Duplicate names, unknown
    labels and no-op edits are refused before any plan is stored; labels are
    never deleted. External administrator approval and apply_change_plan are
    required; children run in order, non-atomically, stopping on the first
    failure; rollback is not available. Until a signed registry admits this
    capability the tool refuses with pending_signed_registry_admission.
    """
    return await _label_plan(
        {"operations": operations, "expiration_minutes": expiration_minutes}
    )


class LabelPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation."""

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _label_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_label_plan_tool():
    tool = LabelPlanTool.from_function(
        create_label_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = LABEL_PLAN_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-155).  1-8 exact automation or
#: script configuration deletions; the planner enforces the exact identifier
#: rules per resource type, duplicates and the reason bound.
CONFIGURATION_DELETE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_configuration_delete_planArguments",
    "additionalProperties": False,
    "required": ["deletions", "reason"],
    "properties": {
        "deletions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["resource_type", "target_id"],
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "enum": ["automation", "script"],
                    },
                    "target_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": "^[A-Za-z0-9_-]+$",
                    },
                },
            },
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _configuration_delete_plan(arguments: Any) -> str:
    return await run_structured(
        "create_configuration_delete_plan",
        "Created one governed automation or script deletion proposal "
        "without dispatching it.",
        _screened(
            "create_configuration_delete_plan",
            arguments,
            lambda: GOVERNANCE.require().create_configuration_delete_plan(
                arguments
            ),
        ),
        metadata={
            "resource_types": ["automation", "script"],
            "provider": "direct_home_assistant_configuration",
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_configuration_delete_plan(
    deletions: list[dict[str, str]],
    reason: str,
    expiration_minutes: int = 120,
) -> str:
    """Propose governed deletion of 1-8 automations or scripts, no dispatch.

    deletions lists {resource_type: automation|script, target_id}. Each
    target must exist with a registry entry; its exact configuration is
    captured as the rollback pre-image. Consumers found by the dependency
    reader and the evidence that could not be checked are disclosed;
    deleting a protected effect or with incomplete evidence needs the
    elevated acknowledgement. External administrator approval and
    apply_change_plan are required; children run in order, non-atomically,
    stopping on the first failure, and rollback_change recreates a verified
    deletion from its pre-image. Until a signed registry admits this
    capability the tool refuses with pending_signed_registry_admission.
    """
    return await _configuration_delete_plan(
        {
            "deletions": deletions,
            "reason": reason,
            "expiration_minutes": expiration_minutes,
        }
    )


class ConfigurationDeletePlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation."""

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _configuration_delete_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_configuration_delete_tool():
    tool = ConfigurationDeletePlanTool.from_function(
        create_configuration_delete_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = CONFIGURATION_DELETE_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-145).  One exact input_select
#: and the exact, case-sensitive option; the planner refuses an option the
#: entity does not currently offer.
INPUT_SELECT_OPTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_input_select_option_planArguments",
    "additionalProperties": False,
    "required": ["entity_id", "option", "reason"],
    "properties": {
        "entity_id": {
            "type": "string",
            "minLength": 14,
            "maxLength": 127,
            "pattern": "^input_select\\.[a-z0-9_]{1,114}$",
        },
        "option": {"type": "string", "minLength": 1, "maxLength": 255},
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _input_select_option_plan(arguments: Any) -> str:
    return await run_structured(
        "create_input_select_option_plan",
        "Created one governed input_select option proposal without "
        "dispatching it.",
        _screened(
            "create_input_select_option_plan",
            arguments,
            lambda: GOVERNANCE.require().create_input_select_option_plan(
                arguments
            ),
        ),
        metadata={
            "resource_type": "input_select",
            "provider": "direct_home_assistant_state",
            "operation": "set_input_select_option",
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_input_select_option_plan(
    entity_id: str,
    option: str,
    reason: str,
    expiration_minutes: int = 120,
) -> str:
    """Propose selecting one exact option on one input_select, no dispatch.

    The option must exactly (case-sensitively) match one the entity offers
    now; an option not offered, or the one already selected, creates no
    plan. The plan discloses the automations that depend on the helper and,
    where their state triggers name literal options, whether this change
    fires them; incomplete evidence raises the approval tier. External
    administrator approval and apply_change_plan are required; dispatch
    re-reads the entity and refuses if the option or the options list
    changed, then sends exactly input_select.select_option and verifies by
    exact readback. rollback_change plans a separately approved return to
    the prior option. Until a signed registry admits this capability the
    tool refuses with pending_signed_registry_admission.
    """
    return await _input_select_option_plan(
        {
            "entity_id": entity_id,
            "option": option,
            "reason": reason,
            "expiration_minutes": expiration_minutes,
        }
    )


class InputSelectOptionPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation."""

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _input_select_option_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_input_select_option_tool():
    tool = InputSelectOptionPlanTool.from_function(
        create_input_select_option_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = INPUT_SELECT_OPTION_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-232).  Field names are the
#: compiled editable vocabulary; per-domain, per-step membership and every
#: value are validated by the planner against the entry's own options step.
HELPER_OPTIONS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_helper_options_planArguments",
    "additionalProperties": False,
    "required": ["changes", "reason"],
    "properties": {
        "changes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_id"],
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 255,
                        "pattern": "^(sensor|binary_sensor)\\.[a-z0-9_]+$",
                    },
                    "set": {
                        "type": "object",
                        "maxProperties": 16,
                        "propertyNames": {
                            "enum": sorted(
                                helper_options_contract.EDITABLE_FIELD_NAMES
                            )
                        },
                    },
                    "clear": {
                        "type": "array",
                        "maxItems": 16,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": sorted(
                                helper_options_contract.EDITABLE_FIELD_NAMES
                            ),
                        },
                    },
                },
            },
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _helper_options_plan(arguments: Any) -> str:
    return await run_structured(
        "create_helper_options_plan",
        "Created one governed helper options proposal without dispatching it.",
        _screened(
            "create_helper_options_plan",
            arguments,
            lambda: GOVERNANCE.require().create_helper_options_plan(arguments),
        ),
        metadata={
            "resource_type": helper_options_contract.RESOURCE_TYPE,
            "provider": helper_options_contract.PROVIDER,
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_helper_options_plan(
    changes: list[dict[str, Any]],
    reason: str,
    expiration_minutes: int = 120,
) -> str:
    """Propose options edits of 1-8 UI template, filter or statistics helpers.

    Each change names one sensor or binary_sensor of a UI-created template
    (sensor or binary_sensor type), filter or statistics helper, the fields
    to set, and the optional fields to clear; one helper domain per plan.
    Planning reads each entry's current options by opening and aborting its
    options flow and creates no plan when nothing would change. The plan
    shows every requested change, any default Home Assistant would add,
    dependent automations, scripts and templates, and that the save reloads
    the helper; dependency coverage of helper chains is incomplete, so it
    always needs elevated approval. External administrator approval and
    apply_change_plan are required; dispatch re-reads each entry and refuses
    if anything changed, submits exactly once, and verifies by exact
    readback. A change made elsewhere in the brief window between that final
    read and the submit is overwritten (disclosed). rollback_change plans a
    separately approved restore. Until a signed registry admits this
    capability the tool refuses with pending_signed_registry_admission.
    """
    return await _helper_options_plan(
        {
            "changes": changes,
            "reason": reason,
            "expiration_minutes": expiration_minutes,
        }
    )


class HelperOptionsPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation."""

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _helper_options_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_helper_options_tool():
    tool = HelperOptionsPlanTool.from_function(
        create_helper_options_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = HELPER_OPTIONS_INPUT_SCHEMA
    return tool


#: The closed published input schema (HAMCP-106).  The planner re-validates
#: every value before any Home Assistant read; the title is stored exactly.
DASHBOARD_METADATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_dashboard_metadata_planArguments",
    "additionalProperties": False,
    "required": ["url_path"],
    "minProperties": 2,
    "properties": {
        "url_path": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "pattern": dashboard_metadata_contract.URL_PATH_PATTERN.pattern,
            "not": {"const": dashboard_metadata_contract.DEFAULT_DASHBOARD_URL_PATH},
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": dashboard_metadata_contract.MAX_TITLE_LENGTH,
        },
        "icon": {
            "anyOf": [
                {
                    "type": "string",
                    "maxLength": dashboard_metadata_contract.MAX_ICON_LENGTH,
                    "pattern": dashboard_metadata_contract.ICON_PATTERN.pattern,
                },
                {"type": "null"},
            ]
        },
        "show_in_sidebar": {"type": "boolean"},
        "require_admin": {"type": "boolean"},
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _dashboard_metadata_plan(arguments: Any) -> str:
    return await run_structured(
        "create_dashboard_metadata_plan",
        "Created one governed dashboard metadata proposal without dispatching it.",
        _screened(
            "create_dashboard_metadata_plan",
            arguments,
            lambda: GOVERNANCE.require().create_dashboard_metadata_plan(arguments),
        ),
        metadata={
            "resource_type": dashboard_metadata_contract.RESOURCE_TYPE,
            "provider": dashboard_metadata_contract.PROVIDER,
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_dashboard_metadata_plan(
    url_path: str,
    title: str | None = None,
    icon: str | None = None,
    show_in_sidebar: bool | None = None,
    require_admin: bool | None = None,
    expiration_minutes: int = 120,
) -> str:
    """Propose a title, icon, sidebar or require_admin change to one dashboard.

    Names one existing storage-mode dashboard by exact url_path (not the
    default "lovelace" dashboard, not a YAML dashboard) and any of title
    (stored exactly as given), icon ("prefix:name", or null to clear),
    show_in_sidebar and require_admin. Fields not given are never sent and
    never changed; views and cards are never sent. Planning binds the
    dashboard's metadata and content fingerprint and creates no plan when
    nothing would change. The plan shows each field before and after and
    the visibility consequence. External administrator approval and
    apply_change_plan are required; dispatch re-reads the dashboard and
    refuses if anything changed, sends one update, and verifies the
    requested fields, the unrequested fields and the content by readback.
    rollback_change plans a separately approved restore. Until a signed
    registry admits this capability the tool refuses with
    pending_signed_registry_admission.
    """
    del url_path, title, icon, show_in_sidebar, require_admin
    del expiration_minutes
    raise RuntimeError("dashboard metadata planning receives raw arguments")


class DashboardMetadataPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation.

    Field presence matters (an omitted field is never sent, ``icon: null``
    clears the icon), so defaults are never materialized.
    """

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _dashboard_metadata_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_dashboard_metadata_tool():
    tool = DashboardMetadataPlanTool.from_function(
        create_dashboard_metadata_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = DASHBOARD_METADATA_INPUT_SCHEMA
    return tool


#: One bounded member value: a mapping with ``state`` plus attributes.  The
#: planner re-validates depth, size and every leaf before any read (HAMCP-071).
_SCENE_MEMBER_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["state"],
    "maxProperties": scene_configuration_contract.MAX_ATTRIBUTES,
    "properties": {
        "state": {
            "type": "string",
            "maxLength": scene_configuration_contract.MAX_NAME_LENGTH,
        },
    },
}

#: The closed published input schema (HAMCP-071).  The planner re-validates
#: every value before any Home Assistant read; values are stored exactly.
SCENE_CONFIGURATION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "create_scene_configuration_planArguments",
    "additionalProperties": False,
    "required": ["config_id"],
    "minProperties": 2,
    "properties": {
        "config_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": scene_configuration_contract.CONFIG_ID_PATTERN.pattern,
        },
        "entity_id": {
            "type": "string",
            "minLength": 7,
            "maxLength": 255,
            "pattern": "^scene\\.",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": scene_configuration_contract.MAX_NAME_LENGTH,
        },
        "icon": {
            "anyOf": [
                {
                    "type": "string",
                    "maxLength": scene_configuration_contract.MAX_ICON_LENGTH,
                    "pattern": scene_configuration_contract.ICON_PATTERN.pattern,
                },
                {"type": "null"},
            ]
        },
        "entities": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": scene_configuration_contract.MAX_ENTITIES,
            "additionalProperties": {
                "anyOf": [_SCENE_MEMBER_SNAPSHOT_SCHEMA, {"type": "null"}]
            },
        },
        "expiration_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "default": 120,
        },
    },
}


async def _scene_configuration_plan(arguments: Any) -> str:
    return await run_structured(
        "create_scene_configuration_plan",
        "Created one governed scene configuration proposal without dispatching it.",
        _screened(
            "create_scene_configuration_plan",
            arguments,
            lambda: GOVERNANCE.require().create_scene_configuration_plan(arguments),
        ),
        metadata={
            "resource_type": scene_configuration_contract.RESOURCE_TYPE,
            "provider": scene_configuration_contract.PROVIDER,
            "fallback": "none",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def create_scene_configuration_plan(
    config_id: str,
    entity_id: str | None = None,
    name: str | None = None,
    icon: str | None = None,
    entities: dict[str, Any] | None = None,
    expiration_minutes: int = 120,
) -> str:
    """Propose a name, icon or member change to one existing UI scene.

    Names one existing UI-managed scene by its exact config id (the scene's
    "id" attribute, not its entity_id; entity_id is an optional cross-check)
    and any of name (stored exactly), icon ("prefix:name", or null to
    remove) and entities, a patch mapping each member entity_id to its full
    stored value (an object with "state" and any attributes) or to null to
    remove it. Members and fields not given are kept exactly. Integration,
    YAML and disabled scenes are refused, as are members that do not exist.
    Planning creates no plan when nothing would change. The plan shows each
    field and member before and after, the automations and scripts that may
    activate the scene, and the reload of all scenes. Saving never activates
    the scene. External administrator approval and apply_change_plan are
    required; dispatch re-reads the scene and refuses if anything changed,
    saves once, and verifies the stored configuration and the scene's
    registry identity exactly by readback. Engineering verifies the saved
    scene and its registry identity; it does not verify the scene Home
    Assistant has loaded. rollback_change plans a separately approved restore of the exact prior
    configuration. Until a signed registry admits this capability the tool
    refuses with pending_signed_registry_admission.
    """
    del config_id, entity_id, name, icon, entities, expiration_minutes
    raise RuntimeError("scene configuration planning receives raw arguments")


class SceneConfigurationPlanTool(Tool):
    """Pass the exact raw argument object to the closed planner validation.

    Field presence matters (an omitted field is kept exactly, ``icon: null``
    removes the icon, a ``null`` member removes it), so defaults are never
    materialized.
    """

    async def run(self, arguments, context=None, convert_result=False):
        rendered = await _scene_configuration_plan(arguments)
        return (
            self.fn_metadata.convert_result(rendered)
            if convert_result
            else rendered
        )


def registered_scene_configuration_tool():
    tool = SceneConfigurationPlanTool.from_function(
        create_scene_configuration_plan,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    tool.parameters = SCENE_CONFIGURATION_INPUT_SCHEMA
    return tool


GOVERNANCE_TOOLS = (
    create_backup_plan,
    create_reload_plan,
    create_helper_state_plan,
    create_entity_metadata_plan,
    create_entity_availability_exposure_plan,
    create_label_plan,
    create_configuration_delete_plan,
    create_input_select_option_plan,
    create_helper_options_plan,
    create_dashboard_metadata_plan,
    create_scene_configuration_plan,
    create_addon_restart_plan,
    create_home_assistant_restart_plan,
    create_change_plan,
    create_configuration_plan,
    create_dashboard_update_plan,
    get_change_plan,
    list_change_plans,
    get_execution_task,
    list_execution_tasks,
    cancel_execution_task,
    reverify_configuration_task,
    approve_change_plan,
    apply_change_plan,
    rollback_change,
)
