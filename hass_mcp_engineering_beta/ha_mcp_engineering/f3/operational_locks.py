"""Complete canonical lock-set calculation for F3 operational adapters."""

from __future__ import annotations

from .locks import normalize_lock_requests
from .operational_models import (
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    LockRequest,
    PreparedOperationalOperation,
)


class OperationalLockSetCalculator:
    """Calculate resource and provider dependencies before durable intent.

    All four operations depend on connected Home Assistant and the exact
    admitted ha-mcp add-on.  Home Assistant Core and the provider are shared
    dependencies except when the operation mutates that exact resource.  The
    F3-A normalizer unions a provider/resource duplicate and applies exclusive
    dominance, which is required when restarting the ha-mcp add-on itself.
    """

    def calculate(
        self, operation: PreparedOperationalOperation
    ) -> tuple[LockRequest, ...]:
        operation.validate()
        provider_key = f"addon:{operation.authoritative_provider_slug}"
        requests: list[LockRequest] = [
            LockRequest(
                key="home_assistant:core",
                scopes=("resource",),
                mode=(
                    "exclusive"
                    if operation.operation == RESTART_HOME_ASSISTANT
                    else "shared"
                ),
                reason_codes=(
                    "home_assistant_core_mutation"
                    if operation.operation == RESTART_HOME_ASSISTANT
                    else "home_assistant_availability_dependency",
                ),
            ),
            LockRequest(
                key=provider_key,
                scopes=("provider",),
                mode="shared",
                reason_codes=("upstream_provider_dependency",),
            ),
        ]
        if operation.operation == CREATE_FULL_BACKUP:
            requests.append(
                LockRequest(
                    key="backup:local_full_backup",
                    scopes=("resource",),
                    mode="exclusive",
                    reason_codes=("full_backup_mutation",),
                )
            )
        elif operation.operation == CONTROLLED_RELOAD:
            requests.append(
                LockRequest(
                    key=f"reload:{operation.target.target_id}",
                    scopes=("resource",),
                    mode="exclusive",
                    reason_codes=("configuration_domain_reload",),
                )
            )
        elif operation.operation == RESTART_ADDON:
            requests.append(
                LockRequest(
                    key=f"addon:{operation.target.target_id}",
                    scopes=("resource",),
                    mode="exclusive",
                    reason_codes=("installed_addon_restart",),
                )
            )
        elif operation.operation != RESTART_HOME_ASSISTANT:
            raise ValueError("unknown operational lock model")

        normalized = normalize_lock_requests(requests)
        return tuple(
            LockRequest(
                key=item.key,
                scopes=item.scopes,
                mode=item.mode,
                reason_codes=item.reason_codes,
            )
            for item in normalized
        )


def exact_manual_review_hold(
    operation: str, target_id: str
) -> tuple[tuple[str, ...], int]:
    """Declare the exact target hold and bounded F3-D handoff policy.

    F3-A currently promotes every held key at once.  This declaration is used
    by conformance tests and is intentionally not presented as activated until
    F3-D adds selective hold promotion/release to the shared integration.
    """

    if operation == CREATE_FULL_BACKUP:
        return ("backup:local_full_backup",), 86_400
    if operation == CONTROLLED_RELOAD:
        return (f"reload:{target_id}",), 900
    if operation == RESTART_ADDON:
        return (f"addon:{target_id}",), 1_800
    if operation == RESTART_HOME_ASSISTANT:
        return ("home_assistant:core",), 1_800
    raise ValueError("unknown operational hold model")
