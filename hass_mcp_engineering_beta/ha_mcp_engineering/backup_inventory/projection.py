"""Positive projection of Core backup evidence into the inventory report.

Only allowlisted fields are read from the two raw results; everything else,
including the stored backup encryption key, is never copied. Every string a
caller sees passes the shared strict sanitizer (HAMCP-191). Any field that
is missing, invalid, out of range or truncated is reported as an omission
and makes the report partial; a field a health signal depends on is also
recorded as an unreadable input for that signal.

When the encryption key is not positively known (its configuration could
not be read, or its field is missing or invalid) no upstream text can be
checked for it. Free text is then withheld, identifiers that structure the
report are replaced by per-response aliases, and only fixed vocabulary and
structured facts (dates, sizes, counts, flags, versions) are shown.
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
# Shown even while the encryption key is unknown: fixed vocabulary and
# structured values that carry no user text.
FIXED_LOCATION_ID = re.compile(r"hassio\.local|backup\.local|cloud\.cloud")
STRUCTURED_VERSION = re.compile(r"\d{4}\.\d{1,2}\.\d{1,3}(?:(?:b|rc|\.dev)\d{1,12})?")
ALIAS_DOMAINS = ("hassio", "backup", "cloud")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RECURRENCES = ("never", "daily", "custom_days")
MANAGER_STATES = ("idle", "create_backup", "blocked", "receive_backup", "restore_backup")
EVENT_STATES = ("completed", "failed", "in_progress", "core_restart")
LOCAL_IDS = ("hassio.local", "backup.local")
OMISSION_LIMIT = 40
MISSING = object()


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
    """Sanitizer chokepoint, omission ledger and signal-input ledger for one response."""

    def __init__(self, *, known_secrets: tuple[str, ...] = (), key_known: bool = False):
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self._folded_secrets = tuple(secret.casefold() for secret in self.known_secrets)
        self.key_known = key_known
        self.tally = RedactionTally()
        self.omissions: dict[str, int] = {}
        # Health-signal inputs that could not be fully read (report.py).
        self.unreadable: set[str] = set()
        self._aliases: dict[tuple[str, str], str] = {}
        if not key_known:
            self.omit("encryption_key_unknown")

    def omit(self, reason: str, count: int = 1) -> None:
        self.omissions[reason] = self.omissions.get(reason, 0) + count

    def invalid(self, pointer: str, *inputs: str) -> None:
        """A missing or invalid field; ``inputs`` are the signal inputs it breaks."""

        self.omit("invalid_field:" + pointer)
        self.unreadable.update(inputs)

    def _case_variant(self, value: str) -> bool:
        """A known secret in another letter case, which exact matching misses."""

        folded = value.casefold()
        return any(secret in folded for secret in self._folded_secrets)

    def _alias(self, kind: str, value: str) -> str:
        alias = self._aliases.get((kind, value))
        if alias is None:
            number = sum(1 for known in self._aliases if known[0] == kind) + 1
            if kind == "location":
                domain = value.split(".", 1)[0]
                alias = f"{domain if domain in ALIAS_DOMAINS else 'agent'}.withheld#{number}"
            else:
                alias = f"withheld#{number}"
            self._aliases[(kind, value)] = alias
        return alias

    def identifier(self, value: Any, pattern: re.Pattern, pointer: str, *,
                   alias: str | None = None, structured: re.Pattern | None = None) -> str | None:
        """An identifier is reported unchanged, as an alias, or not at all.

        While the key is unknown only values matching ``structured`` are
        shown; an ``alias`` kind gets an opaque per-response alias that
        never matches a real identifier, and anything else is withheld.
        """

        if type(value) is not str or pattern.fullmatch(value) is None:
            self.omit("invalid_field:" + pointer)
            return None
        if not self.key_known and (structured is None or structured.fullmatch(value) is None):
            if alias is None:
                self.omit("encryption_key_unknown:" + pointer)
                return None
            if (alias, value) not in self._aliases:
                self.omit("encryption_key_unknown:" + pointer)
            return self._alias(alias, value)
        sanitized = sanitize_untrusted_data(
            value, known_secrets=self.known_secrets, max_string=c.MAX_NAME
        )
        if sanitized.value != value or sanitized.failed_closed or self._case_variant(value):
            self.omit("redacted_by_policy")
            return None
        return value

    def text(self, value: Any, limit: int, pointer: str) -> str | None:
        """User or provider text, sanitized and bounded; redaction is counted."""

        if type(value) is not str:
            self.omit("invalid_field:" + pointer)
            return None
        if not self.key_known:
            self.omit("encryption_key_unknown:" + pointer)
            return None
        sanitized = self.tally.add(sanitize_untrusted_data(
            value, known_secrets=self.known_secrets, max_string=limit
        ))
        if (sanitized.failed_closed or type(sanitized.value) is not str
                or self._case_variant(sanitized.value)):
            self.omit("redacted_by_policy")
            return None
        if sanitized.truncated_field_count:
            self.omit("truncated_field:" + pointer)
        return sanitized.value


def _bool(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 else None


def _required_bool(p: Projector, raw: dict, key: str, pointer: str, *inputs: str) -> bool | None:
    value = _bool(raw.get(key))
    if value is None:
        p.invalid(pointer, *inputs)
    return value


def _nullable_bool(p: Projector, raw: dict, key: str, pointer: str) -> bool | None:
    value = raw.get(key, MISSING)
    if value is None or type(value) is bool:
        return value
    p.invalid(pointer)
    return None


def _time_field(p: Projector, raw: dict, key: str, pointer: str) -> tuple[bool, datetime | None]:
    """A nullable timestamp: (readable, parsed)."""

    value = raw.get(key, MISSING)
    if value is None:
        return True, None
    parsed = parse_time(value)
    if parsed is None:
        p.invalid(pointer)
        return False, None
    return True, parsed


def _retention(p: Projector, value: Any, pointer: str, *, nullable: bool) -> dict | None:
    if value is None and nullable:
        return None
    if type(value) is not dict:
        p.invalid(pointer)
        return None
    result = {}
    for key in ("copies", "days"):
        item = value.get(key, MISSING)
        if item is not None and _count(item) is None:
            p.invalid(pointer + "." + key)
            item = None
        result[key] = item
    return result


def _location_ids(p: Projector, value: Any, pointer: str) -> list[str]:
    if type(value) is not list:
        p.invalid(pointer)
        return []
    result = []
    for item in value[:c.MAX_LOCATIONS]:
        location = p.identifier(item, LOCATION_ID, pointer, alias="location", structured=FIXED_LOCATION_ID)
        if location is not None:
            result.append(location)
    if len(value) > c.MAX_LOCATIONS:
        p.omit("locations_not_listed", len(value) - c.MAX_LOCATIONS)
    return result


def project_config(p: Projector, raw: Any) -> dict:
    """Project ``backup/config/info``; the encryption key becomes a flag."""

    config = raw.get("config") if type(raw) is dict else None
    if type(config) is not dict:
        p.invalid("config", "schedule")
        config = {}

    schedule_raw = config.get("schedule")
    schedule = {"recurrence": None, "days": [], "time": None}
    if type(schedule_raw) is dict:
        recurrence = schedule_raw.get("recurrence")
        if recurrence in RECURRENCES:
            schedule["recurrence"] = recurrence
        else:
            p.invalid("schedule.recurrence", "schedule")
        days = schedule_raw.get("days")
        if type(days) is list and all(day in WEEKDAYS for day in days):
            schedule["days"] = [day for day in WEEKDAYS if day in days]
        else:
            p.invalid("schedule.days", "schedule")
        time_value = schedule_raw.get("time", MISSING)
        if time_value is None or (type(time_value) is str and TIME.fullmatch(time_value)):
            schedule["time"] = time_value
        else:
            p.invalid("schedule.time")
    else:
        p.invalid("schedule", "schedule")

    create = config.get("create_backup")
    includes = {"all_addons": None, "addons": [], "database": None, "folders": []}
    location_ids: list[str] = []
    encryption_configured = None
    if type(create) is dict:
        password = create.get("password", MISSING)
        # Only presence is read. The value never leaves this branch.
        if password is None:
            encryption_configured = False
        elif type(password) is str:
            encryption_configured = bool(password)
        else:
            p.invalid("create_backup.encryption")
        del password
        location_ids = _location_ids(p, create.get("agent_ids"), "create_backup.agent_ids")
        includes["all_addons"] = _required_bool(p, create, "include_all_addons", "create_backup.include_all_addons")
        includes["database"] = _required_bool(p, create, "include_database", "create_backup.include_database")
        addons = create.get("include_addons", MISSING)
        if type(addons) is list:
            for slug in addons[:c.MAX_ADDONS_PER_BACKUP]:
                safe = p.identifier(slug, SLUG, "create_backup.include_addons")
                if safe is not None:
                    includes["addons"].append(safe)
            if len(addons) > c.MAX_ADDONS_PER_BACKUP:
                p.omit("addons_not_listed", len(addons) - c.MAX_ADDONS_PER_BACKUP)
        elif addons is not None:
            p.invalid("create_backup.include_addons")
        folders = create.get("include_folders", MISSING)
        if type(folders) is list and all(item in c.CORE_FOLDERS for item in folders):
            includes["folders"] = sorted(set(folders))
        elif folders is not None:
            p.invalid("create_backup.include_folders")
    else:
        p.invalid("create_backup")

    overrides = {}
    agents = config.get("agents")
    if type(agents) is dict:
        for agent_id in sorted(agents, key=str)[:c.MAX_LOCATIONS]:
            safe = p.identifier(agent_id, LOCATION_ID, "agents.location_id",
                                alias="location", structured=FIXED_LOCATION_ID)
            item = agents[agent_id]
            if type(item) is not dict:
                p.invalid("agents.override")
                continue
            if safe is None:
                continue
            overrides[safe] = {
                "storage_class": storage_class(safe),
                "protected": _required_bool(p, item, "protected", "agents.protected"),
                "retention": _retention(p, item.get("retention", MISSING), "agents.retention", nullable=True),
            }
        if len(agents) > c.MAX_LOCATIONS:
            p.omit("locations_not_listed", len(agents) - c.MAX_LOCATIONS)
    else:
        p.invalid("agents")

    configured = _required_bool(p, config, "automatic_backups_configured", "automatic_backups_configured")
    next_ok, next_scheduled = _time_field(p, config, "next_automatic_backup", "next_automatic_backup")
    return {
        "configured": configured,
        "schedule": schedule,
        "retention": _retention(p, config.get("retention"), "retention", nullable=False),
        "location_ids": location_ids,
        "location_overrides": overrides,
        "includes": includes,
        "encryption_configured": encryption_configured,
        "next_scheduled": iso(next_scheduled),
        "next_scheduled_additional": _required_bool(
            p, config, "next_automatic_backup_additional", "next_automatic_backup_additional"),
        "_last_attempted": _time_field(p, config, "last_attempted_automatic_backup",
                                       "config.last_attempted_automatic_backup"),
        "_last_completed": _time_field(p, config, "last_completed_automatic_backup",
                                       "config.last_completed_automatic_backup"),
    }


def _backup(p: Projector, raw: dict, now: datetime) -> dict | None:
    backup_id = p.identifier(raw.get("backup_id"), BACKUP_ID, "backup.backup_id", alias="backup")
    if backup_id is None:
        p.omit("backup_record_unreadable")
        p.unreadable.add("backup_list")
        return None
    # Groups of this record's fields that could not be fully read; report.py
    # maps them onto the signal inputs that depend on them.
    invalid: set[str] = set()

    def bad(pointer: str, *groups: str) -> None:
        p.invalid("backup." + pointer)
        invalid.update(groups)

    date = parse_time(raw.get("date"))
    if date is None:
        bad("date", "date")
    name = p.text(raw.get("name"), c.MAX_NAME, "backup.name")
    locations = []
    agents = raw.get("agents")
    if type(agents) is dict and agents:
        for agent_id in sorted(agents, key=str)[:c.MAX_LOCATIONS]:
            status = agents[agent_id]
            safe = p.identifier(agent_id, LOCATION_ID, "backup.agents.location_id",
                                alias="location", structured=FIXED_LOCATION_ID)
            if safe is None:
                invalid.add("locations")
            if type(status) is not dict:
                bad("agents.status")
                status = {}
            size = _count(status.get("size"))
            if size is None:
                bad("agents.size")
            protected = _bool(status.get("protected"))
            if protected is None:
                bad("agents.protected")
            locations.append({
                "location_id": safe,
                "storage_class": storage_class(safe),
                "size_bytes": size,
                "protected": protected,
            })
        if len(agents) > c.MAX_LOCATIONS:
            p.omit("locations_not_listed", len(agents) - c.MAX_LOCATIONS)
            invalid.add("locations")
    else:
        bad("agents", "locations")

    addons = []
    raw_addons = raw.get("addons")
    if type(raw_addons) is list:
        for addon in raw_addons[:c.MAX_ADDONS_PER_BACKUP]:
            label = None
            if type(addon) is not dict:
                bad("addons.item")
            elif addon.get("name") is not None:
                label = p.text(addon.get("name"), c.MAX_NAME, "backup.addons.name")
            else:
                label = p.identifier(addon.get("slug"), SLUG, "backup.addons.slug")
            if label is not None:
                addons.append(label)
        if len(raw_addons) > c.MAX_ADDONS_PER_BACKUP:
            p.omit("addons_not_listed", len(raw_addons) - c.MAX_ADDONS_PER_BACKUP)
    else:
        bad("addons")

    def folder_list(key, *groups):
        value = raw.get(key)
        if type(value) is not list or not all(item in c.CORE_FOLDERS for item in value):
            bad(key, *groups)
            return None
        return sorted(set(value))

    def failed_list(key, pattern, cap, alias=None, structured=None):
        value = raw.get(key)
        if type(value) is not list:
            bad(key, "content", "failures")
            return None
        result = []
        for item in value[:cap]:
            if key == "failed_addons":
                item = item.get("slug") if type(item) is dict else None
            safe = p.identifier(item, pattern, "backup." + key, alias=alias, structured=structured)
            # The failure itself is established; only its name is withheld.
            result.append(safe if safe is not None else "withheld")
        if len(value) > cap:
            p.omit(key + "_not_listed", len(value) - cap)
        return result

    folders = folder_list("folders", "content")
    failed_folders = folder_list("failed_folders", "content", "failures")
    failed_locations = failed_list("failed_agent_ids", LOCATION_ID, c.MAX_LOCATIONS,
                                   alias="location", structured=FIXED_LOCATION_ID)
    failed_addons = failed_list("failed_addons", SLUG, c.MAX_ADDONS_PER_BACKUP)
    ha_included = _bool(raw.get("homeassistant_included"))
    if ha_included is None:
        bad("homeassistant_included", "content")
    database = _bool(raw.get("database_included"))
    if database is None:
        bad("database_included", "content")
    version = raw.get("homeassistant_version", MISSING)
    if version is not None:
        version = p.identifier(None if version is MISSING else version, VERSION,
                               "backup.homeassistant_version", structured=STRUCTURED_VERSION)
    automatic = _nullable_bool(p, raw, "with_automatic_settings", "backup.with_automatic_settings")
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
        "automatic": automatic,
        "locations": locations,
        "failed_location_ids": failed_locations,
        "failed_addons": failed_addons,
        "failed_folders": failed_folders,
        "_date": date,
        "_invalid": invalid,
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
        p.invalid("info", "backup_list", "location_errors", "manager_state")
        raw = {}
    items = raw.get("backups")
    backups = []
    if type(items) is list:
        for item in items[:c.MAX_BACKUP_RECORDS]:
            if type(item) is not dict:
                p.omit("backup_record_unreadable")
                p.unreadable.add("backup_list")
                continue
            backup = _backup(p, item, now)
            if backup is not None:
                backups.append(backup)
        if len(items) > c.MAX_BACKUP_RECORDS:
            p.omit("backup_records_not_read", len(items) - c.MAX_BACKUP_RECORDS)
            p.unreadable.add("backup_list")
    else:
        p.invalid("info.backups", "backup_list")

    errors = []
    error_count = None
    agent_errors = raw.get("agent_errors")
    if type(agent_errors) is dict:
        error_count = len(agent_errors)
        for agent_id in sorted(agent_errors, key=str)[:c.MAX_LOCATIONS]:
            safe = p.identifier(agent_id, LOCATION_ID, "info.agent_errors.location_id",
                                alias="location", structured=FIXED_LOCATION_ID)
            errors.append({
                "location_id": safe,
                "storage_class": storage_class(safe),
                "error": p.text(agent_errors[agent_id], c.MAX_ERROR_TEXT, "info.agent_errors.error"),
            })
        if error_count > c.MAX_LOCATIONS:
            p.omit("location_errors_not_listed", error_count - c.MAX_LOCATIONS)
    else:
        p.invalid("info.agent_errors", "location_errors", "backup_list")
    if error_count:
        # A location that could not be read may hold backups not listed.
        p.omit("storage_location_unreadable", error_count)
        p.unreadable.add("backup_list")

    event = raw.get("last_action_event", MISSING)
    last_action = None
    if type(event) is dict:
        manager_state = event.get("manager_state")
        if manager_state not in MANAGER_STATES:
            p.invalid("info.last_action_event.manager_state", "manager_state")
            manager_state = None
        state = event.get("state")
        if state is not None and state not in EVENT_STATES:
            p.invalid("info.last_action_event.state", "manager_state")
            state = None
        last_action = {
            "manager_state": manager_state,
            "state": state,
            "stage": (p.identifier(event.get("stage"), CODE, "info.last_action_event.stage")
                      if event.get("stage") is not None else None),
            "reason": (p.identifier(event.get("reason"), CODE, "info.last_action_event.reason")
                       if event.get("reason") is not None else None),
        }
    elif event is not None:
        p.invalid("info.last_action_event", "manager_state")
    state = raw.get("state")
    if state not in MANAGER_STATES:
        p.invalid("info.state", "manager_state")
        state = None
    manager = {
        "manager_state": state,
        "last_action": last_action,
        "location_error_count": error_count,
        "_last_attempted": _time_field(p, raw, "last_attempted_automatic_backup",
                                       "info.last_attempted_automatic_backup"),
        "_last_completed": _time_field(p, raw, "last_completed_automatic_backup",
                                       "info.last_completed_automatic_backup"),
    }
    backups.sort(key=lambda b: (b["_date"] is not None, b["_date"] or now, b["backup_id"]), reverse=True)
    return backups, manager, errors
