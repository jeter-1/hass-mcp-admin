"""Summary, automatic-backup status and health signals.

Health signals are facts with their thresholds stated, never alarms. Each
has a status of ``observed``, ``not_observed``, ``not_applicable`` or
``unknown``; ``evidence_complete`` is false when a source, location or field
the signal depends on could not be read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import contracts as c
from .projection import Projector, age_days, classify, iso, parse_time, storage_class


def _time(p: Projector, *values: Any, pointer: str) -> tuple[datetime | None, bool]:
    """First present value; (parsed, readable)."""

    for value in values:
        if value is None:
            continue
        parsed = parse_time(value)
        if parsed is None:
            p.omit("invalid_field:" + pointer)
            return None, False
        return parsed, True
    return None, True


def automatic_result(attempted, completed, readable, manager_state, last_action) -> str:
    """Core sets ``last_attempted`` when an automatic backup starts and
    ``last_completed`` only when it finished with no location error
    (backup/manager.py), so "not completed" is the truthful label for an
    attempt that was not followed by a completion."""

    if not readable:
        return "unknown"
    if attempted is None:
        return "completed" if completed is not None else "never_attempted"
    if completed is not None and completed >= attempted:
        return "completed"
    if (manager_state == "create_backup" and last_action is not None
            and last_action.get("state") == "in_progress"):
        return "in_progress"
    return "not_completed"


def _signal(name, status, threshold, fact, complete):
    return {"signal": name, "status": status, "threshold": threshold,
            "fact": fact, "evidence_complete": complete}


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

    listing_complete = info_ok and not errors and not any(
        key in p.omissions for key in ("backup_records_not_read", "backup_record_unreadable")
    )
    dated = [b for b in backups if b["_date"] is not None]
    newest = dated[0] if dated else None
    oldest = dated[-1] if dated else None
    # The newest backup whose content Core attests as full. A newer partial
    # or unknown-scope backup never stands in for it.
    newest_full = next((b for b in dated if b["content_scope"] == "full"), None)

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
    storage = sorted(locations.values(), key=lambda item: item["location_id"])[:c.MAX_LOCATIONS]

    # Automatic backups.
    attempted, attempted_ok = _time(
        p, manager and manager["_last_attempted"],
        config and config["_last_attempted"], pointer="last_attempted_automatic_backup")
    completed, completed_ok = _time(
        p, manager and manager["_last_completed"],
        config and config["_last_completed"], pointer="last_completed_automatic_backup")
    readable = (info_ok or config_ok) and attempted_ok and completed_ok
    result = automatic_result(attempted, completed, readable,
                              manager and manager["manager_state"],
                              manager and manager["last_action"])
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
    failures = failures[:c.MAX_FAILURES]

    # Health signals.
    full_days = c.FULL_BACKUP_MAX_AGE_DAYS
    signals = []
    if not info_ok:
        signals.append(_signal("no_backups", "unknown", None, "The backup list could not be read.", False))
    else:
        signals.append(_signal(
            "no_backups", "observed" if not backups else "not_observed", None,
            f"{len(backups)} backup(s) found in readable storage locations.", listing_complete))

    if not info_ok:
        status, fact = "unknown", "The backup list could not be read."
    elif newest_full is not None and newest_full["age_days"] <= full_days:
        status, fact = "not_observed", f"Newest full backup is {newest_full['age_days']} day(s) old."
    elif any(b["content_scope"] == "unknown" and b["age_days"] is not None
             and b["age_days"] <= full_days for b in dated):
        status, fact = "unknown", "A recent backup's content scope could not be established."
    elif newest_full is not None:
        status, fact = "observed", f"Newest full backup is {newest_full['age_days']} day(s) old."
    else:
        status, fact = "observed", "No full backup was found."
    signals.append(_signal("no_full_backup_within_threshold", status,
                           {"max_age_days": full_days}, fact, listing_complete))

    if newest is None:
        signals.append(_signal("newest_backup_only_on_this_host",
                               "unknown" if not info_ok else "not_applicable", None,
                               "No dated backup was found.", listing_complete))
        signals.append(_signal("newest_backup_no_confirmed_offsite_copy",
                               "unknown" if not info_ok else "not_applicable", None,
                               "No dated backup was found.", listing_complete))
    else:
        where = offsite(newest)
        known = all(loc["location_id"] is not None for loc in newest["locations"])
        signals.append(_signal(
            "newest_backup_only_on_this_host",
            "observed" if where == "none" else "not_observed", None,
            "Storage classes of the newest backup: "
            + (", ".join(sorted({loc["storage_class"] for loc in newest["locations"]})) or "none") + ".",
            listing_complete and known))
        signals.append(_signal(
            "newest_backup_no_confirmed_offsite_copy",
            "not_observed" if where == "confirmed" else "observed", None,
            "Off-site is confirmed only for Home Assistant Cloud; network storage and "
            "other integration agents are off-host but Core does not attest their site.",
            listing_complete and known))

    if result == "unknown":
        status = "unknown"
    else:
        status = "observed" if result == "not_completed" else "not_observed"
    signals.append(_signal("last_automatic_backup_not_completed", status, None,
                           f"Last automatic backup result: {result}.", readable))

    schedule = config["schedule"] if config_ok else None
    recurrence = schedule["recurrence"] if schedule else None
    if recurrence is None:
        signals.append(_signal("automatic_backups_not_scheduled", "unknown", None,
                               "The automatic-backup schedule could not be read.", False))
    else:
        unscheduled = recurrence == "never" or (recurrence == "custom_days" and not schedule["days"])
        signals.append(_signal("automatic_backups_not_scheduled",
                               "observed" if unscheduled else "not_observed", None,
                               f"Schedule recurrence: {recurrence}.", True))

    if recurrence is None or not readable:
        signals.append(_signal("last_completed_automatic_backup_stale", "unknown",
                               {"max_age_days": c.AUTOMATIC_MAX_AGE_DAYS}, "Inputs could not be read.", False))
    elif recurrence not in c.AUTOMATIC_MAX_AGE_DAYS or (recurrence == "custom_days" and not schedule["days"]):
        signals.append(_signal("last_completed_automatic_backup_stale", "not_applicable",
                               {"max_age_days": c.AUTOMATIC_MAX_AGE_DAYS},
                               "No automatic schedule is configured.", True))
    else:
        limit_days = c.AUTOMATIC_MAX_AGE_DAYS[recurrence]
        age = age_days(completed, now)
        stale = age is None or age > limit_days
        signals.append(_signal(
            "last_completed_automatic_backup_stale", "observed" if stale else "not_observed",
            {"max_age_days": limit_days, "recurrence": recurrence},
            "No automatic backup has completed." if age is None
            else f"Last completed automatic backup is {age} day(s) old.", True))

    if not info_ok:
        signals.append(_signal("storage_location_unreadable", "unknown", None,
                               "The backup list could not be read.", False))
    else:
        signals.append(_signal("storage_location_unreadable",
                               "observed" if errors else "not_observed", None,
                               f"{len(errors)} storage location(s) reported a read error.", True))

    if newest is None:
        signals.append(_signal("newest_backup_incomplete",
                               "unknown" if not info_ok else "not_applicable", None,
                               "No dated backup was found.", listing_complete))
    else:
        parts = [newest["failed_location_ids"], newest["failed_addons"], newest["failed_folders"]]
        if any(part is None for part in parts):
            status = "unknown"
        else:
            status = "observed" if any(parts) else "not_observed"
        signals.append(_signal("newest_backup_incomplete", status, None,
                               "Failed locations, add-ons or folders recorded for the newest backup.",
                               status != "unknown"))

    listed = backups[:limit]
    for backup in backups:
        backup.pop("_date", None)
    omissions = [{"reason": reason, "count": count}
                 for reason, count in sorted(p.omissions.items())][:40]
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
