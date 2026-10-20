"""Fixed read contract for the HAMCP-130 backup inventory.

The tool issues exactly two Core WebSocket commands, both registered with
``require_admin`` by the Home Assistant Core 2026.10.1 backup integration
(``homeassistant/components/backup/websocket.py``):

- ``backup/config/info``: the stored automatic-backup configuration. Its
  ``create_backup.password`` member is the backup encryption key. The
  projection reads only whether it is set; the value is used as a known
  secret for this response's sanitizer and is never copied into output.
- ``backup/info``: backups across every backup agent (storage location),
  per-agent read errors, automatic-backup timestamps and manager state.

No other command, REST route, Supervisor endpoint or delegated tool exists in
this package. Create, restore, delete, download, upload and settings commands
are unreachable by construction.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ..errors import ErrorCode, GovernanceError


CONFIG_COMMAND = MappingProxyType({"type": "backup/config/info"})
INFO_COMMAND = MappingProxyType({"type": "backup/info"})
# Read order: the configuration first so its encryption key, when set, is a
# known secret before any user-named backup text is sanitized.
COMMANDS = (CONFIG_COMMAND, INFO_COMMAND)

CORE_REQUIREMENTS = ("core.basic_websocket_read",)
SCHEMA = "backup-inventory-v1"

DEFAULT_LIMIT = 25
MAX_LIMIT = 50
# Records beyond this are not projected; the response is then partial.
MAX_BACKUP_RECORDS = 500
MAX_LOCATIONS = 32
MAX_ADDONS_PER_BACKUP = 50
MAX_FOLDERS = 16
MAX_FAILURES = 20
MAX_NAME = 160
MAX_ERROR_TEXT = 200
MAX_DATE = 64
# Compact JSON budget for the report inside the 60,000-character envelope.
RESPONSE_BUDGET = 50_000

# Health-signal thresholds. They are stated in every response.
FULL_BACKUP_MAX_AGE_DAYS = 7
AUTOMATIC_MAX_AGE_DAYS = {"daily": 2, "custom_days": 8}

# Folders Core's backup model knows (models.Folder in 2026.10.1).
CORE_FOLDERS = ("addons/local", "media", "share", "ssl")

ALLOWED_ARGUMENTS = frozenset({"limit"})


class BackupInventoryError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    REASONS = frozenset({
        "authority_unavailable", "sources_unavailable", "permission_failure",
        "invalid_arguments",
    })

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "sources_unavailable"
        code = {
            "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
            "permission_failure": ErrorCode.AUTHORIZATION_FAILURE,
            "invalid_arguments": ErrorCode.INVALID_REQUEST,
        }.get(reason, ErrorCode.ANALYSIS_UNAVAILABLE)
        super().__init__(code, details={"reason": reason})
        self.reason = reason


def validate_arguments(arguments: Any) -> int:
    """Validate raw caller arguments before the SDK can coerce them."""

    if not isinstance(arguments, dict) or not set(arguments) <= ALLOWED_ARGUMENTS:
        raise ValueError("Invalid backup inventory arguments.")
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("Invalid backup inventory arguments.")
    return limit
