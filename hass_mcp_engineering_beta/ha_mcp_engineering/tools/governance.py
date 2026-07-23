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


async def create_change_plan(
    title: str,
    description: str,
    operation: str,
    automation_id: str,
    proposed_config: dict[str, Any],
    expiration_minutes: int = 60,
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
    expiration_minutes: int = 60,
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
    """Request rollback approval, or execute an explicitly approved update rollback.

    First call creates rollback_pending state and returns its plan hash. Request
    approval for that exact hash, have a Home Assistant administrator approve
    it through Ingress, then call rollback_change again with expected_plan_hash.
    Create-automation rollback is unavailable because
    governed deletion is intentionally outside this milestone.
    """
    return await run_structured(
        "rollback_change",
        "Processed the governed rollback lifecycle step.",
        lambda: GOVERNANCE.require().rollback_change(plan_id, expected_plan_hash),
        metadata={"resource_type": "change_plan", "resource_id": plan_id},
        response_limit=SETTINGS.response_size_limit,
    )


GOVERNANCE_TOOLS = (
    create_change_plan,
    create_configuration_plan,
    get_change_plan,
    list_change_plans,
    approve_change_plan,
    apply_change_plan,
    rollback_change,
)
