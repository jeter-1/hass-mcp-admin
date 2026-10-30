"""Offline HAMCP-106 dashboard metadata fixtures.

A synthetic Home Assistant dashboard collection behind the real
``ConfigurationResourceGateway`` and ``DashboardMetadataGateway``, so
end-to-end tests exercise the shipped planner, policy, F3 runtime and the
closed ``lovelace/dashboards/list`` / ``lovelace/dashboards/update`` commands
without any live dependency.

The six storage dashboards are shaped from the architect's read-only
``list_dashboards`` observation (2026-10-30); titles are untrusted data and
``map`` keeps its trailing space.  Ids follow Core ``slugify(url_path)``.
The collection answers as Core 2026.10 ``DashboardsCollection`` does: an
update merges ``{**item, **update}``, ``icon: None`` pops the icon, an
unknown ``dashboard_id`` is a ``not_found`` error result and the result is
the updated item.  Every other lovelace command fails the test.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from ha_mcp_engineering.errors import HomeAssistantApiError
from ha_mcp_engineering.governance.dashboard_metadata import (
    LIST_COMMAND,
    NEVER_SENT_COMMANDS,
    PROFILE as DASHBOARD_METADATA_PROFILE,
    UPDATE_COMMAND,
)

from tests.hamcp069a_entity_fixtures import SyntheticRegistryWebSocket
from tests.hamcp165_fixtures import LabelAdmittingCore, LabelServiceTestCase


# The architect's six live storage dashboards (title, icon, sidebar, admin).
LIVE_DASHBOARDS: tuple[tuple[str, str, str, bool, bool], ...] = (
    ("dashboard-health", "Josh - Health Dashboard", "mdi:face-man-shimmer", True, False),
    ("engineering-acceptance", "Engineering Acceptance", "mdi:storage-tank", False, True),
    ("fire-tablet-dashboard", "Fire Tablet", "mdi:tablet-dashboard", True, False),
    ("home-admin-dashboard", "Home Admin Dashboard", "mdi:security", True, True),
    ("home-main-dashboard", "Home Main Dashboard", "mdi:home-heart", True, False),
    ("map", "Map ", "mdi:map", True, False),
)
ACCEPTANCE = "engineering-acceptance"


def core_dashboard_id(url_path: str) -> str:
    """Core ``slugify(url_path)`` for the reviewed url_path alphabet."""

    return url_path.replace("-", "_")


def storage_row(
    url_path: str,
    title: str,
    icon: str | None,
    show_in_sidebar: bool,
    require_admin: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": core_dashboard_id(url_path),
        "url_path": url_path,
        "mode": "storage",
        "title": title,
        "show_in_sidebar": show_in_sidebar,
        "require_admin": require_admin,
    }
    if icon is not None:
        row["icon"] = icon
    return row


def content_hash(config: Any) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SyntheticDashboardWebSocket(SyntheticRegistryWebSocket):
    """The 106 closed vocabulary over a Core-faithful dashboard collection."""

    def __init__(self) -> None:
        super().__init__()
        self.dashboards: list[dict[str, Any]] = [
            storage_row(*live) for live in LIVE_DASHBOARDS
        ]
        # url_path -> dashboard content (views and cards); never written.
        self.content: dict[str, Any] = {
            row["url_path"]: {"views": [{"title": "Main", "cards": [{"type": "markdown"}]}]}
            for row in self.dashboards
        }
        self.content_reads: list[str] = []
        # When set, an update is answered but the collection is unchanged.
        self.ignore_writes = False
        # When set, an update is applied and these fields are stored instead.
        self.drift: dict[str, Any] = {}
        # When set, an update also changes the dashboard content.
        self.content_change_on_write = False
        # When set, an update answers with this id instead of the item's.
        self.response_id_override: str | None = None
        # Called after the content read (between the two list reads).
        self.after_content_read: Any = None

    def writes(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if c.get("type") == UPDATE_COMMAND]

    def command_types(self) -> set[str]:
        return {str(c.get("type")) for c in self.commands}

    def row(self, url_path: str) -> dict[str, Any] | None:
        for row in self.dashboards:
            if row["url_path"] == url_path:
                return row
        return None

    def set_external(self, url_path: str, **changes: Any) -> None:  # type: ignore[override]
        """Simulate a metadata edit made outside Engineering."""

        row = self.row(url_path)
        for field, value in changes.items():
            if field == "icon" and value is None:
                row.pop("icon", None)
            else:
                row[field] = deepcopy(value)

    def add_yaml(self, url_path: str, title: str = "YAML board") -> None:
        self.dashboards.append(
            {
                "id": core_dashboard_id(url_path),
                "url_path": url_path,
                "mode": "yaml",
                "title": title,
                "icon": "mdi:file",
                "show_in_sidebar": True,
                "require_admin": False,
                "filename": "dashboards/board.yaml",
            }
        )
        self.content[url_path] = {"views": []}

    async def read_content_fingerprint(self, url_path: str) -> str:
        """The admitted ``get_dashboard_config`` read, as runtime binds it."""

        self.content_reads.append(url_path)
        if url_path not in self.content:
            raise ValueError("dashboard content unavailable")
        value = content_hash(self.content[url_path])
        if self.after_content_read is not None:
            hook, self.after_content_read = self.after_content_read, None
            hook()
        return value

    def _update(self, message: dict[str, Any]) -> dict[str, Any]:
        dashboard_id = message["dashboard_id"]
        matches = [row for row in self.dashboards if row["id"] == dashboard_id]
        if not matches:
            raise HomeAssistantApiError(
                "Unable to find dashboard_id",
                details={"status": 404, "provider_response_received": True},
            )
        row = matches[0]
        update = {k: deepcopy(v) for k, v in message.items() if k not in {"type", "dashboard_id"}}
        merged = {**row, **update}
        if update.get("icon", "") is None:
            merged.pop("icon")
        response = deepcopy(merged)
        if self.response_id_override is not None:
            response["id"] = self.response_id_override
        if self.ignore_writes:
            return response
        row.clear()
        row.update(merged)
        for field, value in self.drift.items():
            if field == "icon" and value is None:
                row.pop("icon", None)
            else:
                row[field] = deepcopy(value)
        if self.content_change_on_write:
            self.content[row["url_path"]] = {"views": [{"title": "Changed"}]}
        return response

    async def _answer(self, message: dict[str, Any]) -> Any:
        kind = message.get("type")
        if kind in NEVER_SENT_COMMANDS or (
            isinstance(kind, str) and kind.startswith("lovelace/") and kind not in {
                LIST_COMMAND, UPDATE_COMMAND
            }
        ):
            raise AssertionError(f"never-sent command was sent: {kind}")
        if kind in {LIST_COMMAND, UPDATE_COMMAND}:
            message = deepcopy(message)
            self.commands.append(message)
            if kind in self.failures:
                raise self.failures[kind]
            if kind == LIST_COMMAND:
                return deepcopy(self.dashboards)
            return self._update(message)
        return await super()._answer(message)


class DashboardAdmittingCore(LabelAdmittingCore):
    """Core double gating only the dashboard metadata profile."""

    GATED = frozenset({DASHBOARD_METADATA_PROFILE})


class DashboardServiceTestCase(LabelServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic dashboards."""

    core_admits = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ws = SyntheticDashboardWebSocket()
        self.rest.websocket = self.ws
        gateway = self.resource_gateway
        gateway.websocket_client = self.ws
        gateway.dashboard_metadata.websocket_client = self.ws
        gateway.dashboard_metadata.content_reader = self.ws.read_content_fingerprint
        self.service.dashboard_metadata_gateway = gateway.dashboard_metadata
        self.core = DashboardAdmittingCore(
            DashboardAdmittingCore.GATED if self.core_admits else ()
        )
        await self.restart_runtime()

    async def create(self, **arguments: Any) -> dict[str, Any]:  # type: ignore[override]
        return await self.service.create_dashboard_metadata_plan(arguments)

    async def approved(self, **arguments: Any) -> dict[str, Any]:  # type: ignore[override]
        created = await self.create(**arguments)
        await self.approve(created)
        return created

    def outcomes(self, task_id: str) -> list[str]:
        return [
            record.normalized_outcome
            for record in self.child_records(task_id)
            if record is not None
        ]

    def assert_never_sent(self) -> None:
        self.assertEqual(
            self.ws.command_types() - {LIST_COMMAND, UPDATE_COMMAND}, set()
        )


__all__ = [
    "ACCEPTANCE",
    "DashboardAdmittingCore",
    "DashboardServiceTestCase",
    "LIST_COMMAND",
    "LIVE_DASHBOARDS",
    "SyntheticDashboardWebSocket",
    "UPDATE_COMMAND",
    "content_hash",
    "core_dashboard_id",
    "storage_row",
]
