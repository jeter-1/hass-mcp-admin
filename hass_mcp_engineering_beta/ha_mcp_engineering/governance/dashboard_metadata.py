"""Exact, code-owned dashboard metadata boundary (HAMCP-106 Part 2).

One storage-mode dashboard's metadata row is the resource, addressed by its
exact ``url_path``.  The governed state is the row
``lovelace/dashboards/list`` returns (Core 2026.10 ``DashboardsCollection``
stored item) plus the content fingerprint of the dashboard's views and cards:

``url_path``, ``id``, ``mode``, ``title``, ``icon`` (``None`` when absent),
``show_in_sidebar``, ``require_admin`` and ``content_fingerprint``.

The gateway sends only a closed command vocabulary: the list read, and one
``lovelace/dashboards/update`` carrying ``dashboard_id`` plus only the fields
the plan changes.  Create, delete, configuration save/delete and resource
commands are never sent.  The content fingerprint is the
``engineering_config_hash`` of the existing admitted ``get_dashboard_config``
read, so a readback proves the views and cards were not changed.

There is no fallback provider.  The resource type stays unadmitted until a
signed Core registry sequence pins ``core.dashboard_metadata_operation``
(November signing batch).
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from ..errors import AuthorizationError, HomeAssistantApiError
from ..request_context import mark_provider_send
from .entity_metadata import (
    EntityMetadataProviderError,
    EntityMetadataUpdateErrorResult,
)


RESOURCE_TYPE = "dashboard_metadata"
ACTION = "update"
CAPABILITY_IDENTITY = "update_dashboard_metadata_configuration"
CAPABILITY_IDENTITIES = {"update": CAPABILITY_IDENTITY}
PROFILE = "core.dashboard_metadata_operation"
PROVIDER = "direct_home_assistant_dashboard_metadata"
PROVIDER_CONTRACT = "direct-ha-exact-dashboard-metadata-v1"
PROVIDER_OPERATIONS = {"update": "update_dashboard_metadata"}
NORMALIZATION_MODEL = "dashboard_metadata_row_projection_v1"
LIST_COMMAND = "lovelace/dashboards/list"
UPDATE_COMMAND = "lovelace/dashboards/update"
ALLOWED_COMMANDS = frozenset({LIST_COMMAND, UPDATE_COMMAND})
NEVER_SENT_COMMANDS = (
    "frontend/set_user_data",
    "frontend/update_panel",
    "lovelace/config/delete",
    "lovelace/config/save",
    "lovelace/dashboards/create",
    "lovelace/dashboards/delete",
    "lovelace/resources/create",
    "lovelace/resources/delete",
    "lovelace/resources/update",
)
UNAUTHORIZED_REASON = "dashboard_metadata_write_unauthorized"
# Core answers ``not_found`` from ``async_update_item`` before it stores
# anything, so that answer is a received rejection, not an uncertain write.
NOT_FOUND_REASON = "dashboard_metadata_target_not_found_rejected"
# 069A OD-15 rule: any other error result to a sent write is not a
# non-mutation proof, so the dispatch stays uncertain and readback decides.
ERROR_RESULT_REASON = "dashboard_metadata_write_error_result"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PENDING_ADMISSION_TEXT = (
    "Dashboard metadata edits are implemented but inactive: pending signed "
    "registry admission (planned: the November Core signing batch). No plan "
    "is created and nothing is dispatched."
)

# The reviewed field vocabulary, in Home Assistant field names.
FIELDS = ("title", "icon", "show_in_sidebar", "require_admin")
STATE_KEYS = ("url_path", "id", "mode", *FIELDS, "content_fingerprint")
# Core stores exactly these keys for a storage dashboard (create schema
# defaults ``require_admin`` and ``show_in_sidebar``; ``icon`` is optional).
_ROW_KEYS = frozenset({"id", "url_path", "mode", *FIELDS})
_REQUIRED_ROW_KEYS = frozenset(
    {"id", "url_path", "mode", "title", "show_in_sidebar", "require_admin"}
)
DEFAULT_DASHBOARD_URL_PATH = "lovelace"
URL_PATH_PATTERN = re.compile(r"^[a-z0-9_-]{1,256}$")
DASHBOARD_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,256}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# A requested icon is a bounded ``prefix:name`` (Core ``cv.icon`` requires a
# colon); a current icon Core already stores is read within the text bound.
ICON_PATTERN = re.compile(r"^[a-z0-9-]{1,31}:[a-z0-9-]{1,63}$")
MAX_ICON_LENGTH = 64
MAX_TITLE_LENGTH = 255
MAX_DASHBOARDS = 500


class DashboardMetadataContractError(ValueError):
    """A dashboard or a proposed change is outside the reviewed contract."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


ContentReader = Callable[[str], Awaitable[str]]


def provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": PROVIDER,
        "provider_contract_model": PROVIDER_CONTRACT,
        "provider_operations": dict(PROVIDER_OPERATIONS),
        "transport": "home_assistant_websocket",
        "readback_transport": "home_assistant_websocket_lovelace_dashboards_list",
        "content_fingerprint": "get_dashboard_config_engineering_config_hash",
        "argument_constraints": {
            "commands": {"read": LIST_COMMAND, "write": UPDATE_COMMAND},
            "target": "exact_existing_storage_dashboard_url_path_not_lovelace",
            "dashboard_id": "exact_id_of_the_one_listed_row",
            "fields": list(FIELDS),
            "unrequested_fields": "never_sent",
            "never_sent": list(NEVER_SENT_COMMANDS),
            "arbitrary_payload_allowed": False,
            "fallback_allowed": False,
        },
        "admission": PENDING_ADMISSION_REASON,
        "fallback": "none",
        "fallback_occurred": False,
    }


def requested_value_valid(field: str, value: Any) -> bool:
    """A value a plan may request; stricter than what Core already stores."""

    if field == "title":
        # Stored exactly: no trim, no case change.  Core accepts any string;
        # an empty or whitespace-only title is refused here.
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= MAX_TITLE_LENGTH
        )
    if field == "icon":
        return value is None or (
            isinstance(value, str)
            and len(value) <= MAX_ICON_LENGTH
            and ICON_PATTERN.fullmatch(value) is not None
        )
    if field in ("show_in_sidebar", "require_admin"):
        return type(value) is bool
    return False


def _stored_value_valid(field: str, value: Any) -> bool:
    if field == "title":
        return isinstance(value, str) and len(value) <= MAX_TITLE_LENGTH
    if field == "icon":
        return value is None or (
            isinstance(value, str) and ":" in value and len(value) <= MAX_TITLE_LENGTH
        )
    return type(value) is bool


def url_path_valid(url_path: Any) -> bool:
    return isinstance(url_path, str) and URL_PATH_PATTERN.fullmatch(url_path) is not None


def state_errors(url_path: str, state: Any) -> list[str]:
    """Errors in one complete governed state (current or proposed)."""

    if not isinstance(state, dict):
        return ["dashboard metadata state must be an object"]
    errors: list[str] = []
    if set(state) != set(STATE_KEYS):
        errors.append("dashboard metadata state has unreviewed or missing fields")
        return errors
    if state["url_path"] != url_path or not url_path_valid(url_path):
        errors.append("dashboard metadata url_path must match the target")
    if url_path == DEFAULT_DASHBOARD_URL_PATH:
        errors.append("the default dashboard is outside the reviewed contract")
    if state["mode"] != "storage":
        errors.append("dashboard metadata mode must be storage")
    if not isinstance(state["id"], str) or not DASHBOARD_ID_PATTERN.fullmatch(
        state["id"]
    ):
        errors.append("dashboard metadata id is malformed")
    for field in FIELDS:
        if not _stored_value_valid(field, state[field]):
            errors.append(f"dashboard metadata {field} is invalid")
    fingerprint = state["content_fingerprint"]
    if not isinstance(fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(
        fingerprint
    ):
        errors.append("dashboard metadata content_fingerprint is malformed")
    return errors


def proposed_errors(url_path: str, proposed: Any) -> list[str]:
    """Errors in a proposed final state; requested values use the strict rules."""

    errors = state_errors(url_path, proposed)
    if errors:
        return errors
    if not requested_value_valid("title", proposed["title"]):
        errors.append("dashboard metadata title is outside the reviewed subset")
    if not requested_value_valid("icon", proposed["icon"]):
        errors.append("dashboard metadata icon is outside the reviewed subset")
    return errors


def normalize_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """The exact binding projection: every state key, compared exactly."""

    if state is None:
        return None
    if not isinstance(state, dict) or set(state) != set(STATE_KEYS):
        raise TypeError("dashboard metadata state must carry exactly the state keys")
    if state_errors(state["url_path"], state):
        # Exact types too: a bool compared with 1 is never a match.
        raise TypeError("dashboard metadata state is outside the reviewed shape")
    return {key: state[key] for key in STATE_KEYS}


def changed_fields(current: dict[str, Any], proposed: dict[str, Any]) -> tuple[str, ...]:
    """Fields whose proposed value differs exactly from the current value."""

    return tuple(
        field
        for field in FIELDS
        if type(current[field]) is not type(proposed[field])
        or current[field] != proposed[field]
    )


def write_command(
    target_id: str, current: dict[str, Any] | None, proposed: dict[str, Any]
) -> dict[str, Any]:
    """Build the one closed update: ``dashboard_id`` plus changed fields only."""

    if current is None:
        raise DashboardMetadataContractError("dashboard_metadata_current_required")
    if state_errors(target_id, current) or proposed_errors(target_id, proposed):
        raise DashboardMetadataContractError("dashboard_metadata_state_invalid")
    for key in ("url_path", "id", "mode", "content_fingerprint"):
        if current[key] != proposed[key]:
            raise DashboardMetadataContractError("dashboard_metadata_identity_changed")
    changed = changed_fields(current, proposed)
    if not changed:
        raise DashboardMetadataContractError("dashboard_metadata_no_change")
    return {
        "type": UPDATE_COMMAND,
        "dashboard_id": current["id"],
        **{field: proposed[field] for field in changed},
    }


def command_errors(command: Any) -> list[str]:
    """Re-check the closed command shape at the provider boundary."""

    if not isinstance(command, dict):
        return ["command must be an object"]
    errors: list[str] = []
    if command.get("type") != UPDATE_COMMAND:
        errors.append("command type is outside the closed vocabulary")
    dashboard_id = command.get("dashboard_id")
    if not isinstance(dashboard_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(
        dashboard_id
    ):
        errors.append("dashboard_id is malformed")
    fields = set(command) - {"type", "dashboard_id"}
    if not fields:
        errors.append("command must change at least one field")
    if not fields <= set(FIELDS):
        errors.append("command carries an unreviewed field")
    for field in fields & set(FIELDS):
        if not requested_value_valid(field, command[field]):
            errors.append(f"command {field} is outside the reviewed subset")
    return errors


def _row_identity(row: Any) -> tuple[Any, Any, Any]:
    if not isinstance(row, dict):
        raise EntityMetadataProviderError("dashboard_list_malformed")
    return row.get("url_path"), row.get("mode"), row.get("id")


def resolve_row(rows: list[dict[str, Any]], url_path: str) -> dict[str, Any] | None:
    """The one storage row for ``url_path``; refuses yaml, default or ambiguity."""

    if url_path == DEFAULT_DASHBOARD_URL_PATH:
        raise DashboardMetadataContractError("dashboard_metadata_default_dashboard")
    matches = [row for row in rows if _row_identity(row)[0] == url_path]
    if not matches:
        return None
    if len(matches) > 1:
        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")
    row = matches[0]
    _, mode, dashboard_id = _row_identity(row)
    if mode == "yaml":
        raise DashboardMetadataContractError("dashboard_metadata_yaml_dashboard")
    if mode != "storage":
        raise DashboardMetadataContractError("dashboard_metadata_target_unreviewable")
    same_id = [row for row in rows if _row_identity(row)[2] == dashboard_id]
    if len(same_id) != 1:
        raise DashboardMetadataContractError("dashboard_metadata_target_ambiguous")
    keys = set(row)
    if not keys <= _ROW_KEYS or not _REQUIRED_ROW_KEYS <= keys:
        raise DashboardMetadataContractError("dashboard_metadata_target_unreviewable")
    if not isinstance(dashboard_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(
        dashboard_id
    ):
        raise DashboardMetadataContractError("dashboard_metadata_target_unreviewable")
    for field in FIELDS:
        if field == "icon" and "icon" not in row:
            continue
        if field == "icon" and row["icon"] is None:
            # Core pops a cleared icon; a stored ``None`` is not its shape.
            raise DashboardMetadataContractError(
                "dashboard_metadata_target_unreviewable"
            )
        if not _stored_value_valid(field, row[field]):
            raise DashboardMetadataContractError(
                "dashboard_metadata_target_unreviewable"
            )
    return row


def state_from_row(row: dict[str, Any], content_fingerprint: str) -> dict[str, Any]:
    if not isinstance(content_fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(
        content_fingerprint
    ):
        raise EntityMetadataProviderError("dashboard_content_fingerprint_unavailable")
    return {
        "url_path": row["url_path"],
        "id": row["id"],
        "mode": row["mode"],
        "title": row["title"],
        "icon": row.get("icon"),
        "show_in_sidebar": row["show_in_sidebar"],
        "require_admin": row["require_admin"],
        "content_fingerprint": content_fingerprint,
    }


class DashboardMetadataGateway:
    """Permit only the reviewed list read and the one closed update."""

    def __init__(self, websocket_client: Any) -> None:
        self.websocket_client = websocket_client
        # Set by the governance runtime: an async ``(url_path) -> sha256``
        # over the admitted ``get_dashboard_config`` read.  Absent means the
        # content cannot be proven unchanged, which fails closed.
        self.content_reader: ContentReader | None = None

    async def _send(self, command: dict[str, Any]) -> Any:
        if command.get("type") not in ALLOWED_COMMANDS:
            raise DashboardMetadataContractError(
                "dashboard_metadata_command_outside_vocabulary"
            )
        return await self.websocket_client.command(dict(command))

    async def rows(self) -> list[dict[str, Any]]:
        """Every listed dashboard row, bounded; content is never read here."""

        value = await self._send({"type": LIST_COMMAND})
        if not isinstance(value, list) or len(value) > MAX_DASHBOARDS:
            raise EntityMetadataProviderError("dashboard_list_malformed")
        for row in value:
            path, mode, _ = _row_identity(row)
            if not isinstance(path, str) or not isinstance(mode, str):
                raise EntityMetadataProviderError("dashboard_list_malformed")
        return [dict(row) for row in value]

    async def content_fingerprint(self, url_path: str) -> str:
        reader = self.content_reader
        if reader is None:
            raise EntityMetadataProviderError(
                "dashboard_content_fingerprint_unavailable"
            )
        try:
            value = await reader(url_path)
        except AuthorizationError:
            raise
        except Exception:
            raise EntityMetadataProviderError(
                "dashboard_content_fingerprint_unavailable"
            ) from None
        if not isinstance(value, str) or not FINGERPRINT_PATTERN.fullmatch(value):
            raise EntityMetadataProviderError(
                "dashboard_content_fingerprint_unavailable"
            )
        return value

    async def read_state(self, url_path: str) -> dict[str, Any] | None:
        """The exact governed state, or ``None`` when no row has the path."""

        if not url_path_valid(url_path):
            raise ValueError("dashboard_metadata resource_id must be a url_path")
        row = resolve_row(await self.rows(), url_path)
        if row is None:
            return None
        fingerprint = await self.content_fingerprint(url_path)
        # The row is re-read after the content read so a metadata edit that
        # lands between the two reads is never bound to stale content.
        again = resolve_row(await self.rows(), url_path)
        if again != row:
            raise EntityMetadataProviderError("dashboard_metadata_read_unstable")
        return state_from_row(row, fingerprint)

    async def write(self, url_path: str, command: dict[str, Any]) -> dict[str, Any]:
        """Send the one closed update built by ``write_command``."""

        from .resources import ConfigurationMutationNotDispatchedError

        operation = PROVIDER_OPERATIONS[ACTION]
        if not url_path_valid(url_path) or command_errors(command):
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": url_path if url_path_valid(url_path) else None,
                    "reason": "closed_payload_violation",
                },
                call_not_sent=True,
            )
        try:
            mark_provider_send()  # OD-6(ii): the exact send boundary
            result = await self._send(command)
        except AuthorizationError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            raise ConfigurationMutationNotDispatchedError(
                details={
                    "operation": operation,
                    "resource_id": url_path,
                    "reason": UNAUTHORIZED_REASON,
                    "provider_response_received": (
                        details.get("provider_response_received") is True
                    ),
                }
            ) from exc
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("provider_response_received") is True:
                if details.get("status") == 404:
                    raise ConfigurationMutationNotDispatchedError(
                        details={
                            "operation": operation,
                            "resource_id": url_path,
                            "reason": NOT_FOUND_REASON,
                            "provider_response_received": True,
                            "provider_status": 404,
                        }
                    ) from exc
                raise EntityMetadataUpdateErrorResult(
                    details={
                        "operation": operation,
                        "resource_id": url_path,
                        "reason": ERROR_RESULT_REASON,
                        "provider_response_received": True,
                        **(
                            {"provider_status": details["status"]}
                            if type(details.get("status")) is int
                            else {}
                        ),
                    }
                ) from exc
            raise
        returned = result.get("id") if isinstance(result, dict) else None
        if returned != command["dashboard_id"]:
            raise EntityMetadataProviderError("dashboard_metadata_write_identity")
        return {"core_response": {"dashboard_id": returned}}


__all__ = [
    "ACTION",
    "CAPABILITY_IDENTITIES",
    "CAPABILITY_IDENTITY",
    "DashboardMetadataContractError",
    "DashboardMetadataGateway",
    "FIELDS",
    "NOT_FOUND_REASON",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "RESOURCE_TYPE",
    "STATE_KEYS",
    "UNAUTHORIZED_REASON",
    "changed_fields",
    "command_errors",
    "normalize_state",
    "proposed_errors",
    "provider_evidence",
    "requested_value_valid",
    "resolve_row",
    "state_errors",
    "write_command",
]
