"""HAMCP-476 bounded automation run counting over the native logbook read.

One call holds the single logbook slot for its whole run (a second logbook
read is refused as busy). Each batch of at most 10 automations is read over
the whole window first; a batch whose body exceeds a logbook processing
limit is bisected (queued behind the other batches) until the GET or time
budget runs out or a sub-window is shorter than two minimum splits.

Split without a gap or a double count: Core's bounds are both exclusive, so
the later half of a split at ``m`` is requested from ``m - 1 us`` and every
row is assigned by its own ``when``: the earlier half owns ``when < m`` and
the later half owns ``when >= m`` (the outermost halves own everything
before the window start or after its end). A row inside the 1 us overlap is
returned by both requests and counted by one.
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import time

from ..clients import logbook
from ..clients.logbook import LOGBOOK
from ..clients.paths import rest_path
from ..errors import ErrorCode, GovernanceError, InvalidRequestError
from . import contracts as c


ONE_US = timedelta(microseconds=1)
MIN_SPLIT = timedelta(seconds=c.MIN_SPLIT_SECONDS)


async def _automation_ids(client):
    """Every current automation entity, from one states read.

    Unlike ``resolve_name``, a malformed item is an error rather than
    skipped: all mode promises every automation, so it cannot drop one.
    """

    states = await client.request("GET", rest_path("/states"))
    if not isinstance(states, list):
        raise logbook._states_error()
    found = set()
    for item in states:
        if not isinstance(item, dict) or type(item.get("entity_id")) is not str:
            raise logbook._states_error()
        entity_id = item["entity_id"]
        if entity_id.startswith("automation."):
            if not c.valid_automation_id(entity_id):
                raise logbook._states_error()
            found.add(entity_id)
    if len(found) > c.MAX_ALL_AUTOMATIONS:
        raise InvalidRequestError(
            f"More than {c.MAX_ALL_AUTOMATIONS} automations; name up to 10 with automation_ids instead.",
            details={"reason": "automation_count_exceeds_bound", "automation_count": len(found),
                     "limit": c.MAX_ALL_AUTOMATIONS})
    return sorted(found)


def _count(text, ids, lo, hi, owns_before, owns_after):
    """Runs and ignored rows that this sub-window owns, from one body."""

    records = logbook.decode_records(text)
    wanted = set(ids)
    runs, state_rows, other_rows = {}, 0, 0
    for record in records:
        when = logbook.record_when(record)
        if when is None:
            raise logbook.invalid_response()
        if (not owns_before and when < lo) or (not owns_after and when >= hi):
            continue
        entity_id = record.get("entity_id")
        if c.is_run(record) and type(entity_id) is str and entity_id in wanted:
            found = runs.setdefault(entity_id, [0, when, when])
            found[0] += 1
            found[1], found[2] = min(found[1], when), max(found[2], when)
        elif "state" in record:
            state_rows += 1
        else:
            other_rows += 1
    return runs, state_rows, other_rows


def _limit_cause(exc):
    if exc.code != ErrorCode.LOGBOOK_RESPONSE_LIMIT_EXCEEDED:
        return None
    reason = exc.details.get("reason")
    return reason if reason in c.LIMIT_CAUSES else None


def _intervals(items):
    """Sorted uncovered intervals, adjacent same-cause ones merged."""

    merged = []
    for lo, hi, cause in sorted(items, key=lambda item: (item[0], item[1])):
        if merged and merged[-1][1] == lo and merged[-1][2] == cause:
            merged[-1][1] = hi
        else:
            merged.append([lo, hi, cause])
    return [{"start": lo.isoformat(), "end": hi.isoformat(), "cause": cause} for lo, hi, cause in merged]


async def count_runs(client, checked, *, now=None, clock=time.monotonic):
    started = clock()
    window = logbook.resolve_window(
        now or datetime.now(timezone.utc), checked["hours"], checked["start_time"],
        checked["end_time"], default_hours=c.DEFAULT_HOURS)
    all_mode = checked["mode"] == "all_automations"
    max_gets, max_seconds = ((c.ALL_MAX_GETS, c.ALL_MAX_SECONDS) if all_mode
                             else (c.IDS_MAX_GETS, c.IDS_MAX_SECONDS))
    deadline = started + max_seconds
    LOGBOOK.acquire()
    worker = None
    try:
        ids = await _automation_ids(client) if all_mode else list(checked["automation_ids"])
        groups = c.batches(ids)
        start, end = window["start"], window["end"]
        queue = deque((index, start, end) for index in range(len(groups)))
        totals = {automation_id: [0, None, None] for automation_id in ids}
        uncovered = [[] for _ in groups]
        ignored = {"state_rows": 0, "other_rows": 0}
        gets = 0
        while queue:
            index, lo, hi = queue[0]
            remaining = deadline - clock()
            cause = ("request_budget" if gets >= max_gets
                     else "time_budget" if remaining <= 0 else None)
            if cause is None:
                queue.popleft()
                gets += 1
                query_start = start if lo == start else lo - ONE_US
                try:
                    text = await asyncio.wait_for(
                        logbook.fetch_text(client, query_start.isoformat(), hi.isoformat(), groups[index]),
                        remaining)
                except asyncio.TimeoutError:
                    queue.appendleft((index, lo, hi))
                    cause = "time_budget"
                except GovernanceError as exc:
                    limit = _limit_cause(exc)
                    if limit is None:
                        raise
                    text, cause = None, limit
                if cause is None:
                    worker = asyncio.create_task(asyncio.to_thread(
                        _count, text, groups[index], lo, hi, lo == start, hi == end))
                    try:
                        runs, state_rows, other_rows = await asyncio.shield(worker)
                    except GovernanceError as exc:
                        limit = _limit_cause(exc)
                        if limit is None:
                            raise
                        cause = limit
                    else:
                        ignored["state_rows"] += state_rows
                        ignored["other_rows"] += other_rows
                        for automation_id, (count, first, last) in runs.items():
                            total = totals[automation_id]
                            total[0] += count
                            total[1] = first if total[1] is None else min(total[1], first)
                            total[2] = last if total[2] is None else max(total[2], last)
                        continue
                if cause in c.LIMIT_CAUSES:
                    if hi - lo >= 2 * MIN_SPLIT:
                        middle = lo + (hi - lo) // 2
                        queue.append((index, lo, middle))
                        queue.append((index, middle, hi))
                    else:
                        uncovered[index].append((lo, hi, cause))
                    continue
            # A spent budget leaves this and every queued sub-window uncovered.
            for index, lo, hi in queue:
                uncovered[index].append((lo, hi, cause))
            queue.clear()
    finally:
        LOGBOOK.release(worker)

    batch_of = {automation_id: index for index, group in enumerate(groups) for automation_id in group}
    counts = []
    for automation_id in ids:
        runs, first, last = totals[automation_id]
        counts.append({"automation_id": automation_id, "runs": runs,
                       "first_run": first.isoformat() if first else None,
                       "last_run": last.isoformat() if last else None,
                       "complete": not uncovered[batch_of[automation_id]]})
    if all_mode:
        counts.sort(key=lambda row: (-row["runs"], row["automation_id"]))
    reasons = sorted({cause for items in uncovered for _, _, cause in items})
    notes = [*logbook.BASE_NOTES, "failed_condition_triggers_not_counted", "mode_dropped_runs_not_counted"]
    if all_mode:
        notes.append("automations_from_current_states")
        if reasons:
            notes.append("ranking_uses_lower_bound_counts")
    else:
        notes.append("automation_existence_not_checked")
    return {
        "mode": checked["mode"],
        "requested_interval": window["requested_interval"],
        "effective_interval": window["effective_interval"],
        "interval_bounds": "exclusive",
        "counted_event": c.COUNTED_EVENT,
        "ranking": "runs_desc_then_automation_id" if all_mode else "request_order",
        "automation_count": len(ids),
        "counts": counts,
        "ignored_rows": ignored,
        "requests": {"logbook_gets": gets, "states_gets": int(all_mode),
                     "max_logbook_gets": max_gets, "max_seconds": max_seconds},
        "complete": not reasons,
        "partial_reasons": reasons,
        "uncovered": [{"automation_ids": list(groups[index]), "intervals": _intervals(items)}
                      for index, items in enumerate(uncovered) if items],
        "completeness_notes": notes,
    }
