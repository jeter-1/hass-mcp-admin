"""Beta-native governed configuration change MCP tools."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field
from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from ..entity_ids import ENTITY_ID_PATTERN, MAX_ENTITY_ID_LENGTH
from ..errors import ErrorCode, GovernanceError
from ..request_context import current_telemetry

from ..governance import GOVERNANCE
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


GOVERNANCE_TOOLS = (
    create_backup_plan,
    create_reload_plan,
    create_helper_state_plan,
    create_entity_metadata_plan,
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
