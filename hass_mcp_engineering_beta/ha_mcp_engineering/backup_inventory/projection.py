"""Positive projection of Core backup evidence into the inventory report.

Only allowlisted fields are read from the two raw results; everything else,
including the stored backup encryption key, is never copied. Every string a
caller sees passes the shared strict sanitizer (HAMCP-191). Any field that
cannot be read is reported as an omission and makes the report partial.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from ..sanitization import RedactionTally, sanitize_untrusted_data
from . import contracts as c


BACKUP_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
DOMAIN = re.compile(r"[a-z0-9_]{1,64}")
# ``{domain}.{unique_id}`` (backup/agent.py). Supervisor mount names, the
# unique id of ``hassio.<mount>`` agents, are ``[A-Za-z0-9_]`` text.
LOCATION_ID = re.compile(r"[a-z0-9_]{1,64}\.[A-Za-z0-9_.:@-]{1,128}")
SLUG = re.compile(r"[A-Za-z0-9_.-]{1,64}")
VERSION = re.compile(r"[A-Za-z0-9_.+-]{1,32}")
CODE = re.compile(r"[a-z0-9_]{1,64}")
TIME = re.compile(r"\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RECURRENCES = ("never", "daily", "custom_days")
MANAGER_STATES = ("idle", "create_backup", "blocked", "receive_backup", "restore_backup")
EVENT_STATES = ("completed", "failed", "in_progress", "core_restart")
LOCAL_IDS = ("hassio.local", "backup.local")
OMISSION_LIMIT = 40


def storage_class(location_id: str | None) -> str:
    """Classify a backup agent by Core's ``{domain}.{unique_id}`` naming."""

    if location_id is None:
        return "unknown"
    if location_id in LOCAL_IDS:
        return "local"
    domain = location_id.split(".", 1)[0]
    if domain == "hassio":
        return "network_storage"
    if domain == "cloud":
        return "home_assistant_cloud"
    return "integration_agent"


def parse_time(value: Any) -> datetime | None:
    if type(value) is not str or not 0 < len(value) <= c.MAX_DATE:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def age_days(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    return round((now - value).total_seconds() / 86400, 2)


class Projector:
    """Sanitizer chokepoint and omission ledger for one response."""

    def __init__(self, *, known_secrets: tuple[str, ...] = ()):
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.tally = RedactionTally()
        self.omissions: dict[str, int] = {}

    def omit(self, reason: str, count: int = 1) -> None:
        self.omissions[reason] = self.omissions.get(reason, 0) + count

    def identifier(self, value: Any, pattern: re.Pattern) -> str | None:
        """An identifier is reported unchanged or not at all."""

        if type(value) is not str or pattern.fullmatch(value) is None:
            return None
        sanitized = sanitize_untrusted_data(
            value, known_secrets=self.known_secrets, max_string=c.MAX_NAME
        )
        if sanitized.value != value or sanitized.failed_closed:
            self.omit("redacted_by_policy")
            return None
        return value

    def text(self, value: Any, limit: int) -> str | None:
        """User or provider text, sanitized and bounded; redaction is counted."""

        if type(value) is not str:
            return None
        sanitized = self.tally.add(sanitize_untrusted_data(
            value, known_secrets=self.known_secrets, max_string=limit
        ))
        if sanitized.failed_closed or type(sanitized.value) is not str:
            self.omit("redacted_by_policy")
            return None
        return sanitized.value


def _bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 else None


def _retention(p: Projector, value: Any, pointer: str) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict:
        p.omit("invalid_field:" + pointer)
        return None
    result = {}
    for key in ("copies", "days"):
        item = value.get(key)
        if item is not None and _count(item) is None:
            p.omit("invalid_field:" + pointer + "." + key)
            item = None
        result[key] = item
    return result


def _location_ids(p: Projector, value: Any, pointer: str) -> list[str]:
    if type(value) is not list:
        p.omit("invalid_field:" + pointer)
        return []
    result = []
    for item in value[:c.MAX_LOCATIONS]:
        location = p.identifier(item, LOCATION_ID)
        if location is None:
            p.omit("location_id_withheld")
        else:
            result.append(location)
    if len(value) > c.MAX_LOCATIONS:
        p.omit("locations_not_listed", len(value) - c.MAX_LOCATIONS)
    return result


def project_config(p: Projector, raw: Any) -> dict:
    """Project ``backup/config/info``; the encryption key becomes a flag."""

    config = raw.get("config") if type(raw) is dict else None
    if type(config) is not dict:
        p.omit("invalid_field:config")
        config = {}

    schedule_raw = config.get("schedule")
    schedule = {"recurrence": None, "days": [], "time": None}
    if type(schedule_raw) is dict:
        recurrence = schedule_raw.get("recurrence")
        if recurrence in RECURRENCES:
            schedule["recurrence"] = recurrence
        else:
            p.omit("invalid_field:schedule.recurrence")
        days = schedule_raw.get("days")
        if type(days) is list and all(day in WEEKDAYS for day in days):
            schedule["days"] = [day for day in WEEKDAYS if day in days]
        else:
            p.omit("invalid_field:schedule.days")
        time_value = schedule_raw.get("time")
        if time_value is None or (type(time_value) is str and TIME.fullmatch(time_value)):
            schedule["time"] = time_value
        else:
            p.omit("invalid_field:schedule.time")
    else:
        p.omit("invalid_field:schedule")

    create = config.get("create_backup")
    includes = {"all_addons": None, "addons": [], "database": None, "folders": []}
    location_ids: list[str] = []
    encryption_configured = None
    if type(create) is dict:
        password = create.get("password")
        # Only presence is read. The value never leaves this branch.
        if password is None:
            encryption_configured = False
        elif type(password) is str:
            encryption_configured = bool(password)
        else:
            p.omit("invalid_field:create_backup.encryption")
        del password
        location_ids = _location_ids(p, create.get("agent_ids"), "create_backup.agent_ids")
        includes["all_addons"] = _bool(create.get("include_all_addons"))
        includes["database"] = _bool(create.get("include_database"))
        addons = create.get("include_addons")
        if type(addons) is list:
            for slug in addons[:c.MAX_ADDONS_PER_BACKUP]:
                safe = p.identifier(slug, SLUG)
                if safe is None:
                    p.omit("addon_withheld")
                else:
                    includes["addons"].append(safe)
            if len(addons) > c.MAX_ADDONS_PER_BACKUP:
                p.omit("addons_not_listed", len(addons) - c.MAX_ADDONS_PER_BACKUP)
        elif addons is not None:
            p.omit("invalid_field:create_backup.include_addons")
        folders = create.get("include_folders")
        if type(folders) is list and all(item in c.CORE_FOLDERS for item in folders):
            includes["folders"] = sorted(set(folders))
        elif folders is not None:
            p.omit("invalid_field:create_backup.include_folders")
    else:
        p.omit("invalid_field:create_backup")

    overrides = {}
    agents = config.get("agents")
    if type(agents) is dict:
        for agent_id in sorted(agents, key=str)[:c.MAX_LOCATIONS]:
            safe = p.identifier(agent_id, LOCATION_ID)
            item = agents[agent_id]
            if safe is None or type(item) is not dict:
                p.omit("location_id_withheld")
                continue
            overrides[safe] = {
                "storage_class": storage_class(safe),
                "protected": _bool(item.get("protected")),
                "retention": _retention(p, item.get("retention"), "agents.retention"),
            }
        if len(agents) > c.MAX_LOCATIONS:
            p.omit("locations_not_listed", len(agents) - c.MAX_LOCATIONS)
    elif agents is not None:
        p.omit("invalid_field:agents")

    configured = _bool(config.get("automatic_backups_configured"))
    if configured is None:
        p.omit("invalid_field:automatic_backups_configured")
    next_scheduled = parse_time(config.get("next_automatic_backup"))
    if config.get("next_automatic_backup") is not None and next_scheduled is None:
        p.omit("invalid_field:next_automatic_backup")
    return {
        "configured": configured,
        "schedule": schedule,
        "retention": _retention(p, config.get("retention"), "retention"),
        "location_ids": location_ids,
        "location_overrides": overrides,
        "includes": includes,
        "encryption_configured": encryption_configured,
        "next_scheduled": iso(next_scheduled),
        "next_scheduled_additional": _bool(config.get("next_automatic_backup_additional")),
        "_last_attempted": config.get("last_attempted_automatic_backup"),
        "_last_completed": config.get("last_completed_automatic_backup"),
    }


def _backup(p: Projector, raw: dict, now: datetime) -> dict | None:
    backup_id = p.identifier(raw.get("backup_id"), BACKUP_ID)
    if backup_id is None:
        p.omit("backup_record_unreadable")
        return None
    date = parse_time(raw.get("date"))
    if date is None:
        p.omit("invalid_field:backup.date")
    name = p.text(raw.get("name"), c.MAX_NAME)
    if name is None:
        p.omit("invalid_field:backup.name")
    locations = []
    agents = raw.get("agents")
    if type(agents) is dict:
        for agent_id in sorted(agents, key=str)[:c.MAX_LOCATIONS]:
            status = agents[agent_id]
            safe = p.identifier(agent_id, LOCATION_ID)
            if safe is None:
                p.omit("location_id_withheld")
            if type(status) is not dict:
                p.omit("invalid_field:backup.agents")
                status = {}
            locations.append({
                "location_id": safe,
                "storage_class": storage_class(safe),
                "size_bytes": _count(status.get("size")),
                "protected": _bool(status.get("protected")),
            })
        if len(agents) > c.MAX_LOCATIONS:
            p.omit("locations_not_listed", len(agents) - c.MAX_LOCATIONS)
    else:
        p.omit("invalid_field:backup.agents")

    addons = []
    raw_addons = raw.get("addons")
    if type(raw_addons) is list:
        for addon in raw_addons[:c.MAX_ADDONS_PER_BACKUP]:
            label = None
            if type(addon) is dict:
                label = (p.text(addon.get("name"), c.MAX_NAME) if addon.get("name") is not None
                         else p.identifier(addon.get("slug"), SLUG))
            if label is None:
                p.omit("addon_withheld")
            else:
                addons.append(label)
        if len(raw_addons) > c.MAX_ADDONS_PER_BACKUP:
            p.omit("addons_not_listed", len(raw_addons) - c.MAX_ADDONS_PER_BACKUP)
    else:
        p.omit("invalid_field:backup.addons")

    def folder_list(key):
        value = raw.get(key)
        if type(value) is not list or not all(item in c.CORE_FOLDERS for item in value):
            p.omit("invalid_field:backup." + key)
            return None
        return sorted(set(value))

    def failed_list(key, pattern):
        value = raw.get(key)
        if type(value) is not list:
            p.omit("invalid_field:backup." + key)
            return None
        result = []
        for item in value[:c.MAX_LOCATIONS]:
            if key == "failed_addons":
                item = item.get("slug") if type(item) is dict else None
            safe = p.identifier(item, pattern)
            result.append(safe if safe is not None else "withheld")
        return result

    folders = folder_list("folders")
    failed_folders = folder_list("failed_folders")
    failed_locations = failed_list("failed_agent_ids", LOCATION_ID)
    failed_addons = failed_list("failed_addons", SLUG)
    ha_included = _bool(raw.get("homeassistant_included"))
    database = _bool(raw.get("database_included"))
    version = p.identifier(raw.get("homeassistant_version"), VERSION)
    if raw.get("homeassistant_version") is not None and version is None:
        p.omit("invalid_field:backup.homeassistant_version")
    protections = [item["protected"] for item in locations]
    sizes = [item["size_bytes"] for item in locations if item["size_bytes"] is not None]
    return {
        "backup_id": backup_id,
        "name": name,
        "date": iso(date),
        "age_days": age_days(date, now),
        "size_bytes": max(sizes) if sizes else None,
        "content_scope": None,  # set by classify() once the installation is known
        "homeassistant_included": ha_included,
        "homeassistant_version": version,
        "database_included": database,
        "addons": addons,
        "folders": folders,
        "protected": (None if not protections or None in protections else all(protections)),
        "automatic": _bool(raw.get("with_automatic_settings")),
        "locations": locations,
        "failed_location_ids": failed_locations,
        "failed_addons": failed_addons,
        "failed_folders": failed_folders,
        "_date": date,
    }


def classify(backup: dict, installation: str) -> str:
    """``full``, ``partial`` or ``unknown`` from Core's content fields.

    Core does not report Supervisor's full/partial type
    (hassio/backup.py ``_backup_details_to_agent_backup``) nor whether every
    installed add-on was included, so "full" is the strongest statement Core
    evidence supports: Home Assistant configuration, the database and, on a
    Supervisor installation, every Core-known folder, with nothing failed.
    """

    if (backup["homeassistant_included"] is False or backup["database_included"] is False
            or backup["failed_addons"] or backup["failed_folders"]):
        return "partial"
    if (backup["homeassistant_included"] is None or backup["database_included"] is None
            or backup["folders"] is None or backup["failed_addons"] is None
            or backup["failed_folders"] is None):
        return "unknown"
    if installation == "core":
        return "full"
    if set(backup["folders"]) >= set(c.CORE_FOLDERS):
        return "full"
    return "partial" if installation == "supervisor" else "unknown"


def project_backups(p: Projector, raw: Any, now: datetime) -> tuple[list[dict], dict, list[dict]]:
    """Project ``backup/info``; returns backups (newest first), state, errors."""

    if type(raw) is not dict:
        p.omit("invalid_field:info")
        raw = {}
    items = raw.get("backups")
    backups = []
    if type(items) is list:
        for item in items[:c.MAX_BACKUP_RECORDS]:
            if type(item) is not dict:
                p.omit("backup_record_unreadable")
                continue
            backup = _backup(p, item, now)
            if backup is not None:
                backups.append(backup)
        if len(items) > c.MAX_BACKUP_RECORDS:
            p.omit("backup_records_not_read", len(items) - c.MAX_BACKUP_RECORDS)
    else:
        p.omit("invalid_field:info.backups")

    errors = []
    agent_errors = raw.get("agent_errors")
    if type(agent_errors) is dict:
        for agent_id in sorted(agent_errors, key=str)[:c.MAX_LOCATIONS]:
            safe = p.identifier(agent_id, LOCATION_ID)
            errors.append({
                "location_id": safe,
                "storage_class": storage_class(safe),
                "error": p.text(agent_errors[agent_id], c.MAX_ERROR_TEXT),
            })
        if len(agent_errors) > c.MAX_LOCATIONS:
            p.omit("location_errors_not_listed", len(agent_errors) - c.MAX_LOCATIONS)
    else:
        p.omit("invalid_field:info.agent_errors")
    if errors:
        p.omit("storage_location_unreadable", len(errors))

    event = raw.get("last_action_event")
    last_action = None
    if type(event) is dict:
        last_action = {
            "manager_state": event.get("manager_state") if event.get("manager_state") in MANAGER_STATES else None,
            "state": event.get("state") if event.get("state") in EVENT_STATES else None,
            "stage": p.identifier(event.get("stage"), CODE) if event.get("stage") is not None else None,
            "reason": p.identifier(event.get("reason"), CODE) if event.get("reason") is not None else None,
        }
    elif event is not None:
        p.omit("invalid_field:info.last_action_event")
    state = raw.get("state")
    if state not in MANAGER_STATES:
        p.omit("invalid_field:info.state")
        state = None
    manager = {
        "manager_state": state,
        "last_action": last_action,
        "_last_attempted": raw.get("last_attempted_automatic_backup"),
        "_last_completed": raw.get("last_completed_automatic_backup"),
        "_next": raw.get("next_automatic_backup"),
    }
    backups.sort(key=lambda b: (b["_date"] is not None, b["_date"] or now, b["backup_id"]), reverse=True)
    return backups, manager, errors
