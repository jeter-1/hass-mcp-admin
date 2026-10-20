"""Summary, automatic-backup status and health signals.

Health signals are facts with their thresholds stated, never alarms. Each
has a status of ``observed``, ``not_observed``, ``not_applicable`` or
``unknown`` and declares the inputs it is decided from (``depends_on``). When
a source, location or field behind any of those inputs could not be fully
read, the signal is ``unknown`` with ``evidence_complete`` false and the
inputs named in ``unreadable_inputs``; it is never decided from partial input.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import contracts as c
from .projection import OMISSION_LIMIT, Projector, age_days, classify, iso, parse_time, storage_class


def _automatic_time(manager: dict | None, config: dict | None, key: str) -> tuple[bool, datetime | None]:
    """Both sources carry Core's stored value; (readable, parsed).

    Readable only when at least one source was read and every source read
    holds a readable value; then the first present value is used.
    """

    present = [source[key] for source in (manager, config) if source is not None]
    if not present or not all(readable for readable, _ in present):
        return False, None
    return True, next((value for _, value in present if value is not None), None)


def needs_manager_state(attempted, completed) -> bool:
    """Only an attempt not followed by a completion is decided by the manager state."""

    return attempted is not None and (completed is None or completed < attempted)


def automatic_result(attempted, completed, manager_state, last_action) -> str:
    """Core sets ``last_attempted`` when an automatic backup starts and
    ``last_completed`` only when it finished with no location error
    (backup/manager.py), so "not completed" is the truthful label for an
    attempt that was not followed by a completion."""

    if attempted is None:
        return "completed" if completed is not None else "never_attempted"
    if completed is not None and completed >= attempted:
        return "completed"
    if (manager_state == "create_backup" and last_action is not None
            and last_action.get("state") == "in_progress"):
        return "in_progress"
    return "not_completed"


def _signal(name, threshold, depends_on, unreadable, decide):
    """A signal is decided only from inputs that were fully read.

    Any declared input that could not be fully read makes the signal
    ``unknown`` with ``evidence_complete`` false and names the input.
    """

    missing = [item for item in depends_on if item in unreadable]
    if missing:
        status, fact = "unknown", "Not established: " + ", ".join(missing) + " could not be fully read."
    else:
        status, fact = decide()
    return {"signal": name, "status": status, "threshold": threshold, "fact": fact,
            "evidence_complete": not missing, "depends_on": list(depends_on),
            "unreadable_inputs": missing}


def build(
    p: Projector,
    *,
    now: datetime,
    limit: int,
    config: dict | None,
    info: tuple[list[dict], dict, list[dict]] | None,
    sources: list[dict],
) -> dict:
    backups, manager, errors = info if info is not None else ([], None, [])
    info_ok = info is not None
    config_ok = config is not None

    # Installation kind from Core agent naming: ``hassio.*`` agents exist only
    # with the Supervisor; ``backup.local`` only without it.
    seen = {loc["location_id"] for b in backups for loc in b["locations"]}
    seen |= {item["location_id"] for item in errors}
    if config_ok:
        seen |= set(config["location_ids"]) | set(config["location_overrides"])
    if any(type(item) is str and item.startswith("hassio.") for item in seen):
        installation = "supervisor"
    elif "backup.local" in seen:
        installation = "core"
    else:
        installation = "unknown"
    for backup in backups:
        backup["content_scope"] = classify(backup, installation)

    dated = [b for b in backups if b["_date"] is not None]
    newest = dated[0] if dated else None
    oldest = dated[-1] if dated else None
    # The newest backup whose content Core attests as full. A newer partial
    # or unknown-scope backup never stands in for it.
    newest_full = next((b for b in dated if b["content_scope"] == "full"), None)
    full_days = c.FULL_BACKUP_MAX_AGE_DAYS

    # Signal inputs that could not be fully read: from projection, from a
    # failed source, and from invalid fields of the backups a signal uses.
    unreadable = set(p.unreadable)
    if not info_ok:
        unreadable |= {"backup_list", "backup_dates", "location_errors", "manager_state"}
    if not config_ok:
        unreadable.add("schedule")
    if any("date" in b["_invalid"] for b in backups):
        unreadable.add("backup_dates")
    if any("content" in b["_invalid"] and b["age_days"] <= full_days for b in dated):
        unreadable.add("recent_backup_content")
    if newest is not None and ("locations" in newest["_invalid"]
                               or any(loc["location_id"] is None for loc in newest["locations"])):
        unreadable.add("newest_backup_locations")
    if newest is not None and "failures" in newest["_invalid"]:
        unreadable.add("newest_backup_failures")

    def brief(backup):
        if backup is None:
            return None
        return {"backup_id": backup["backup_id"], "name": backup["name"],
                "date": backup["date"], "age_days": backup["age_days"],
                "content_scope": backup["content_scope"],
                "storage_classes": sorted({loc["storage_class"] for loc in backup["locations"]})}

    def offsite(backup):
        if backup is None:
            return None
        classes = {loc["storage_class"] for loc in backup["locations"]}
        if "home_assistant_cloud" in classes:
            return "confirmed"
        if classes - {"local"}:
            return "off_host_not_confirmed_off_site"
        return "none"

    # Storage locations seen in backups, errors and configuration.
    locations: dict[str, dict] = {}

    def location(location_id):
        return locations.setdefault(location_id, {
            "location_id": location_id, "storage_class": None, "backup_count": 0,
            "newest_backup_date": None, "read_error": False,
            "selected_for_automatic_backups": False,
        })

    for backup in backups:
        for loc in backup["locations"]:
            if loc["location_id"] is None:
                continue
            item = location(loc["location_id"])
            item["backup_count"] += 1
            if backup["date"] and (item["newest_backup_date"] is None
                                   or backup["_date"] > parse_time(item["newest_backup_date"])):
                item["newest_backup_date"] = backup["date"]
    for error in errors:
        if error["location_id"] is not None:
            location(error["location_id"])["read_error"] = True
    if config_ok:
        for location_id in config["location_ids"]:
            location(location_id)["selected_for_automatic_backups"] = True
    for item in locations.values():
        item["storage_class"] = storage_class(item["location_id"])
    storage = sorted(locations.values(), key=lambda item: item["location_id"])
    if len(storage) > c.MAX_LOCATIONS:
        p.omit("storage_locations_not_listed", len(storage) - c.MAX_LOCATIONS)
        storage = storage[:c.MAX_LOCATIONS]

    # Automatic backups.
    attempted_ok, attempted = _automatic_time(manager, config, "_last_attempted")
    completed_ok, completed = _automatic_time(manager, config, "_last_completed")
    if not (attempted_ok and completed_ok):
        unreadable.add("automatic_times")
    # The manager state is an input only when it decides the result.
    automatic_inputs = ["automatic_times"]
    if "automatic_times" not in unreadable and needs_manager_state(attempted, completed):
        automatic_inputs.append("manager_state")
    if any(item in unreadable for item in automatic_inputs):
        result = "unknown"
    else:
        result = automatic_result(attempted, completed, manager["manager_state"] if manager else None,
                                  manager["last_action"] if manager else None)
    automatic = {
        "configured": config["configured"] if config_ok else None,
        "schedule": config["schedule"] if config_ok else None,
        "retention": config["retention"] if config_ok else None,
        "location_ids": config["location_ids"] if config_ok else None,
        "location_overrides": config["location_overrides"] if config_ok else None,
        "includes": config["includes"] if config_ok else None,
        "encryption_configured": config["encryption_configured"] if config_ok else None,
        "next_scheduled": config["next_scheduled"] if config_ok else None,
        "next_scheduled_additional": config["next_scheduled_additional"] if config_ok else None,
        "last_attempted": iso(attempted),
        "last_completed": iso(completed),
        "last_completed_age_days": age_days(completed, now),
        "last_result": result,
    }

    # Recent failures, newest first, bounded.
    failures: list[dict] = []
    if manager and manager["last_action"] and manager["last_action"]["state"] == "failed":
        failures.append({"kind": "last_backup_action_failed", **manager["last_action"]})
    if result == "not_completed":
        failures.append({"kind": "automatic_backup_not_completed",
                         "last_attempted": iso(attempted), "last_completed": iso(completed)})
    for error in errors:
        failures.append({"kind": "storage_location_unreadable", **error})
    for backup in backups:
        if backup["failed_location_ids"] or backup["failed_addons"] or backup["failed_folders"]:
            failures.append({
                "kind": "backup_partially_failed", "backup_id": backup["backup_id"],
                "date": backup["date"],
                "failed_location_ids": backup["failed_location_ids"],
                "failed_addons": backup["failed_addons"],
                "failed_folders": backup["failed_folders"],
            })
    failure_count = len(failures)
    if failure_count > c.MAX_FAILURES:
        p.omit("recent_failures_not_listed", failure_count - c.MAX_FAILURES)
        failures = failures[:c.MAX_FAILURES]

    # Health signals. Each declares the inputs it is decided from.
    signals = []

    signals.append(_signal(
        "no_backups", None, ["backup_list"], unreadable,
        lambda: ("observed" if not backups else "not_observed",
                 f"{len(backups)} backup(s) found in readable storage locations.")))

    def no_full():
        if newest_full is not None and newest_full["age_days"] <= full_days:
            return "not_observed", f"Newest full backup is {newest_full['age_days']} day(s) old."
        if any(b["content_scope"] == "unknown" and b["age_days"] <= full_days for b in dated):
            return "unknown", "A recent backup's content scope could not be established."
        if newest_full is not None:
            return "observed", f"Newest full backup is {newest_full['age_days']} day(s) old."
        return "observed", "No full backup was found."

    signals.append(_signal(
        "no_full_backup_within_threshold", {"max_age_days": full_days},
        ["backup_list", "backup_dates", "recent_backup_content"], unreadable, no_full))

    newest_inputs = ["backup_list", "backup_dates", "newest_backup_locations"]

    def only_here():
        if newest is None:
            return "not_applicable", "No dated backup was found."
        return ("observed" if offsite(newest) == "none" else "not_observed",
                "Storage classes of the newest backup: "
                + (", ".join(sorted({loc["storage_class"] for loc in newest["locations"]})) or "none") + ".")

    def no_offsite():
        if newest is None:
            return "not_applicable", "No dated backup was found."
        return ("not_observed" if offsite(newest) == "confirmed" else "observed",
                "Off-site is confirmed only for Home Assistant Cloud; network storage and "
                "other integration agents are off-host but Core does not attest their site.")

    signals.append(_signal("newest_backup_only_on_this_host", None, newest_inputs, unreadable, only_here))
    signals.append(_signal("newest_backup_no_confirmed_offsite_copy", None, newest_inputs,
                           unreadable, no_offsite))

    signals.append(_signal(
        "last_automatic_backup_not_completed", None, automatic_inputs, unreadable,
        lambda: ("observed" if result == "not_completed" else "not_observed",
                 f"Last automatic backup result: {result}.")))

    schedule = config["schedule"] if config_ok else None
    recurrence = schedule["recurrence"] if schedule else None
    scheduled = "schedule" not in unreadable and not (
        recurrence == "never" or (recurrence == "custom_days" and not schedule["days"]))
    signals.append(_signal(
        "automatic_backups_not_scheduled", None, ["schedule"], unreadable,
        lambda: ("not_observed" if scheduled else "observed", f"Schedule recurrence: {recurrence}.")))

    def stale():
        if not scheduled:
            return "not_applicable", "No automatic schedule is configured."
        age = age_days(completed, now)
        return ("observed" if age is None or age > c.AUTOMATIC_MAX_AGE_DAYS[recurrence] else "not_observed",
                "No automatic backup has completed." if age is None
                else f"Last completed automatic backup is {age} day(s) old.")

    stale_threshold = ({"max_age_days": c.AUTOMATIC_MAX_AGE_DAYS[recurrence], "recurrence": recurrence}
                       if scheduled else {"max_age_days": c.AUTOMATIC_MAX_AGE_DAYS})
    # The completion time is an input only when a schedule is configured.
    signals.append(_signal(
        "last_completed_automatic_backup_stale", stale_threshold,
        ["schedule", "automatic_times"] if scheduled or "schedule" in unreadable else ["schedule"],
        unreadable, stale))

    error_count = manager["location_error_count"] if manager else None
    signals.append(_signal(
        "storage_location_unreadable", None, ["location_errors"], unreadable,
        lambda: ("observed" if error_count else "not_observed",
                 f"{error_count} storage location(s) reported a read error.")))

    def incomplete():
        if newest is None:
            return "not_applicable", "No dated backup was found."
        parts = [newest["failed_location_ids"], newest["failed_addons"], newest["failed_folders"]]
        return ("observed" if any(parts) else "not_observed",
                "Failed locations, add-ons or folders recorded for the newest backup.")

    signals.append(_signal(
        "newest_backup_incomplete", None, ["backup_list", "backup_dates", "newest_backup_failures"],
        unreadable, incomplete))

    listed = backups[:limit]
    for backup in backups:
        backup.pop("_date", None)
        backup.pop("_invalid", None)
    omissions = [{"reason": reason, "count": count}
                 for reason, count in sorted(p.omissions.items())][:OMISSION_LIMIT]
    redaction = p.tally.summary() if p.tally.applied else None
    complete = (all(source["status"] == "complete" for source in sources)
                and not p.omissions and redaction is None)
    return {
        "schema": c.SCHEMA,
        "completeness": "complete" if complete else "partial",
        "omissions": omissions,
        "omission_reason_count": len(p.omissions),
        "redaction": redaction,
        "sources": sources,
        "thresholds": {
            "full_backup_max_age_days": full_days,
            "automatic_backup_max_age_days": dict(c.AUTOMATIC_MAX_AGE_DAYS),
        },
        "definitions": {
            "full": ("Home Assistant configuration and database included, every Core-known "
                     "folder included on a Supervisor installation, and no add-on or folder "
                     "failed. Core does not report whether every installed add-on was included."),
            "off_site": ("Confirmed only for Home Assistant Cloud. Network storage and other "
                         "integration agents are off-host; Core does not attest their site."),
            "withheld": ("While the backup encryption key cannot be read, no upstream text can be "
                         "checked for it: names and error text are withheld, and backup and "
                         "storage-location identifiers are replaced by per-response aliases such "
                         "as withheld#1 or hassio.withheld#1 that keep the storage class."),
        },
        "installation": installation,
        "summary": {
            "backup_count": len(backups) if info_ok else None,
            "listed_count": len(listed),
            "listed_limit": limit,
            "more_backups_not_listed": max(0, len(backups) - len(listed)),
            "newest_backup": brief(newest),
            "oldest_backup": brief(oldest),
            "newest_full_backup": brief(newest_full),
            "newest_backup_offsite_copy": offsite(newest),
            "newest_full_backup_offsite_copy": offsite(newest_full),
            "storage_locations": storage,
            "manager_state": manager["manager_state"] if manager else None,
            "last_action": manager["last_action"] if manager else None,
        },
        "automatic_backups": automatic,
        "recent_failures": failures,
        "recent_failure_count": failure_count,
        "health_signals": signals,
        "backups": listed,
    }
