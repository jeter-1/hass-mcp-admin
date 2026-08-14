"""Beta-native governed configuration change MCP tools."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field

from ..governance import GOVERNANCE
from ..tool_framework import run_structured
from .compatibility import SETTINGS


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
        lambda: GOVERNANCE.require().create_backup_plan(
            backup_name=backup_name,
            expiration_minutes=expiration_minutes,
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
        lambda: GOVERNANCE.require().create_reload_plan(
            reload_target=reload_target,
            expiration_minutes=expiration_minutes,
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
        lambda: GOVERNANCE.require().create_helper_state_plan(
            entity_id=entity_id,
            desired_state=desired_state,
            expiration_minutes=expiration_minutes,
        ),
        metadata={
            "resource_type": "input_boolean",
            "resource_id": entity_id,
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
        lambda: GOVERNANCE.require().create_addon_restart_plan(
            addon_slug=addon_slug,
            expiration_minutes=expiration_minutes,
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
        lambda: (
            GOVERNANCE.require().create_home_assistant_restart_plan(
                expiration_minutes=expiration_minutes,
            )
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
        lambda: GOVERNANCE.require().create_plan(
            title=title,
            description=description,
            operation=operation,
            automation_id=automation_id,
            proposed_config=proposed_config,
            expiration_minutes=expiration_minutes,
            caller_context=caller_context,
        ),
        metadata={"resource_type": "automation", "resource_id": automation_id},
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
        lambda: GOVERNANCE.require().create_configuration_plan(
            title=title,
            description=description,
            operations=operations,
            expiration_minutes=expiration_minutes,
            caller_context=caller_context,
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
        lambda: GOVERNANCE.require().create_dashboard_update_plan(
            title=title,
            description=description,
            url_path=url_path,
            patch_operations=patch_operations,
            expiration_minutes=expiration_minutes,
        ),
        metadata={
            "resource_type": "dashboard",
            "resource_id": url_path,
            "operation": "update_dashboard",
        },
        response_limit=SETTINGS.response_size_limit,
    )


async def get_change_plan(plan_id: str) -> str:
    """Return one persisted change plan, including review diff and lifecycle state."""
    return await run_structured(
        "get_change_plan",
        "Returned the requested governed change plan.",
        lambda: GOVERNANCE.require().get_plan(plan_id),
        metadata={"resource_type": "change_plan", "resource_id": plan_id},
        response_limit=SETTINGS.response_size_limit,
    )


async def list_change_plans(status: str = "", limit: int = 20) -> str:
    """List bounded change-plan summaries, optionally filtered by exact status."""
    return await run_structured(
        "list_change_plans",
        "Returned bounded governed change-plan summaries.",
        lambda: GOVERNANCE.require().list_plans(status=status, limit=limit),
        response_limit=SETTINGS.response_size_limit,
    )


async def get_execution_task(
    task_id: Annotated[
        str, Field(pattern=r"^[a-f0-9]{32}$")
    ],
) -> str:
    """Return one durable execution task by its exact opaque identifier."""
    return await run_structured(
        "get_execution_task",
        "Returned the requested durable execution task.",
        lambda: GOVERNANCE.require().get_execution_task(task_id),
        metadata={
            "resource_type": "execution_task",
            "resource_id": task_id,
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
        lambda: GOVERNANCE.require().list_execution_tasks(
            state=state,
            terminal_outcome=terminal_outcome,
            plan_id=plan_id,
            limit=limit,
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
        lambda: GOVERNANCE.require().cancel_execution_task(task_id),
        metadata={
            "resource_type": "execution_task",
            "resource_id": task_id,
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
        lambda: GOVERNANCE.require().approve(
            plan_id, expected_plan_hash, approval_note
        ),
        metadata={"resource_type": "change_plan", "resource_id": plan_id},
        response_limit=SETTINGS.response_size_limit,
    )


async def apply_change_plan(plan_id: str, expected_plan_hash: str = "") -> str:
    """Apply one externally approved plan with stale-state protection and verification."""
    return await run_structured(
        "apply_change_plan",
        "Processed the approved governed configuration change.",
        lambda: GOVERNANCE.require().apply(plan_id, expected_plan_hash),
        metadata={"resource_type": "change_plan", "resource_id": plan_id},
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
        lambda: GOVERNANCE.require().rollback_change(plan_id, expected_plan_hash),
        metadata={"resource_type": "change_plan", "resource_id": plan_id},
        response_limit=SETTINGS.response_size_limit,
    )


GOVERNANCE_TOOLS = (
    create_backup_plan,
    create_reload_plan,
    create_helper_state_plan,
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
    approve_change_plan,
    apply_change_plan,
    rollback_change,
)
