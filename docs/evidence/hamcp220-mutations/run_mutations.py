"""HAMCP-220 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery (disposable copy of the tracked tree
outside the repository, every fragment matching exactly once, fail-fast
suites per mutant) and its 40 mutations. Five HAMCP-131 fragments moved with
the HAMCP-220 change and are retargeted at the same decision in its new
form (``W2``-``W5``, ``U3``); the rest apply unchanged. The suites are the
HAMCP-131 set plus ``test_hamcp220_incident_log_timestamps``.

The task's three required classes each have mutations:
- ``T1*``: text parsed as UTC (the rendering hypotheses, the per-entry
  reading, or the no-realtime fallback read in UTC);
- ``T2*``: an ambiguous or nonexistent local time resolved silently (one
  fold chosen, a multi-candidate entry given one time, the edge
  uncertainty dropped);
- ``T3*``: a time-zone read failure falling back to UTC (in ``load_zone``
  or in the service).

``T4*`` probes the realtime calibration: a page whose text matches no
rendering accepted, and an unverified page reported complete.

Review 1 corrections (H1, M1) add two classes and two suites (the
reviewer's ``test_hamcp220_review1_probes`` and
``test_hamcp220_review1_tool_local_probe``, unchanged):
- ``H1*``: Home Assistant's ``/api/config`` zone bypassed (``H1a``, the
  task's required one: the add-on TZ used when it differs; the add-on TZ
  preferred, or used when the zone is unreadable; a disagreement ignored;
  the reviewer's own mutation, the first-entry tolerance widened to one
  hour; the zone reader's lease revalidation, its post-read check, or its
  error-code restore dropped);
- ``M1*``: ambiguous-span ordering (``M1a``, the task's required one:
  earliest-candidate ordering across ambiguous pages; the bound kept as the
  latest entry, not the running maximum; the ``ordering_uncertain`` mark,
  its extension, its omission or its cursor carry dropped; the tail
  resolution removed; a disagreeing resolution probe accepted; the
  per-source reserve ignored).

``T2d``, ``T3c`` and ``U3`` follow their decisions into the corrected
source (the edge flag is now one ``Item`` field of several; the zone
failure is decided in ``_zones``; a source row carries its ordering count).

Usage:
    python docs/evidence/hamcp220-mutations/run_mutations.py \\
        --work ~/.cache/hamcp220/mut --output docs/evidence/hamcp220-mutations/results.json
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
INC, JRN, TBS = base.INC, f"{E}/incident_logs/journal.py", f"{E}/incident_logs/timebasis.py"
ZON = f"{E}/incident_logs/zone.py"
TEXT_AS_UTC = "text parsed as UTC"
SILENT = "an ambiguous or nonexistent time resolved silently"
UTC_FALLBACK = "time-zone fallback to UTC"
CALIBRATION = "realtime calibration bypassed"
UTC_ZONE = "Zone(UTC_NAME, timezone.utc)"
ZONE_BYPASS = "Home Assistant's configured zone bypassed"
ORDERING = "ambiguous-span ordering asserted"

RETARGETED = {
    "W2-incident-start-exclusive": m(
        "W2-incident-start-exclusive", base.WINDOW,
        (JRN, "        return any(start_ms <= value < end_ms for value in self.readings)",
         "        return any(start_ms < value < end_ms for value in self.readings)")),
    "W3-locate-head-at-start-not-retention-edge": m(
        "W3-locate-head-at-start-not-retention-edge", base.WINDOW,
        (INC, "        if head.latest >= start:", "        if head.latest > start:")),
    "W4-locate-tail-at-start-treated-after-tail": m(
        "W4-locate-tail-at-start-treated-after-tail", base.WINDOW,
        (INC, "        if scan.tail_entry.latest_ms < start:", "        if scan.tail_entry.latest_ms <= start:")),
    "W5-locate-tail-page-start-edge": m(
        "W5-locate-tail-page-start-edge", base.WINDOW,
        (INC, "        if first.latest_ms < start:", "        if first.latest_ms <= start:")),
    "U3-failed-source-row-dropped": m(
        "U3-failed-source-row-dropped", base.OMITTED,
        (INC, "                source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted, edge, "
              "disordered))",
         "                if scan.failure is None:\n"
         "                    source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted, edge, "
         "disordered))")),
}

TIMESTAMP_MUTATIONS = [
    m("T1a-renderings-utc-only", TEXT_AS_UTC,
      (TBS, f"    return (zone, {UTC_ZONE})", f"    return ({UTC_ZONE},)")),
    m("T1b-entry-text-read-as-utc", TEXT_AS_UTC,
      (TBS, "        values, reason = local_candidates(entry.stamp, rendering.tz)",
       "        values, reason = local_candidates(entry.stamp, timezone.utc)")),
    m("T1c-unverified-fallback-read-as-utc", TEXT_AS_UTC,
      (TBS, "        surviving = (zone,)", f"        surviving = ({UTC_ZONE},)")),
    m("T2a-ambiguous-fold-0-chosen", SILENT,
      (TBS, "        return candidates, AMBIGUOUS", "        return (first,), None")),
    m("T2b-nonexistent-fold-0-chosen", SILENT,
      (TBS, "    return candidates, NONEXISTENT", "    return (first,), None")),
    m("T2c-uncertain-entry-given-earliest-time", SILENT,
      (TBS, "    if len(ordered) == 1 and not reasons:", "    if ordered:")),
    m("T2d-window-edge-uncertainty-dropped", SILENT,
      (INC, "possible and not entry.certain_in(args[\"start_ms\"], args[\"end_ms\"]),",
       "False,")),
    m("T3a-unknown-zone-falls-back-to-utc", UTC_FALLBACK,
      (TBS, "        raise TimeZoneUnavailable(\"time_zone_unknown\") from None",
       f"        return {UTC_ZONE}")),
    m("T3b-missing-zone-falls-back-to-utc", UTC_FALLBACK,
      (TBS, "        raise TimeZoneUnavailable(\"time_zone_invalid\")", f"        return {UTC_ZONE}")),
    m("T3c-service-zone-failure-reads-utc", UTC_FALLBACK,
      (INC, "            args[\"zone_status\"] = \"unavailable\"\n            return\n",
       "            zone = load_zone(\"UTC\")\n")),
    m("T4a-rendering-mismatch-accepted", CALIBRATION,
      (TBS, "        if not surviving:\n            raise TimeBasisMismatch(\"timestamp_basis_mismatch\")",
       "        if not surviving:\n            surviving = (zone,)")),
    m("T4b-unverified-page-reported-complete", CALIBRATION,
      (INC, "                    reasons.append(f\"timestamp_basis_unverified:{row['source']}\")",
       "                    pass")),
]

REVIEW1_MUTATIONS = [
    m("H1a-addon-tz-used-when-it-differs", ZONE_BYPASS,
      (INC, '        args["addon_zone_status"] = "differs"\n',
       '        args["addon_zone_status"] = "differs"\n        args["zone"] = addon\n        return\n')),
    m("H1b-addon-tz-preferred", ZONE_BYPASS,
      (INC, "            zone = load_zone(name)\n", "            zone = load_zone(self.addon_zone_source() or name)\n")),
    m("H1c-unreadable-zone-falls-back-to-addon-tz", ZONE_BYPASS,
      (INC, '            # Never the add-on TZ, never UTC: no source is read without it.\n'
            '            args["zone_status"] = "unavailable"\n            return\n',
       "            zone = load_zone(self.addon_zone_source())\n")),
    m("H1d-addon-offset-difference-ignored", ZONE_BYPASS,
      (INC, "        if at is None:\n", "        if True:\n")),
    m("H1e-first-entry-tolerance-one-hour", ZONE_BYPASS,
      (TBS, "CALIBRATION_TOLERANCE_MS = 1\n", "CALIBRATION_TOLERANCE_MS = 3_600_000\n")),
    m("H1f-zone-lease-not-revalidated", ZONE_BYPASS,
      (ZON, "            return not closed and core.revalidate(authority, commits) is True",
       "            return not closed")),
    m("H1g-zone-post-read-check-dropped", ZONE_BYPASS,
      (ZON, "            if not authorize():\n                return None\n", "            pass\n")),
    m("H1h-zone-read-error-code-leaks", ZONE_BYPASS,
      (ZON, "telemetry.core_dispatch_authorizer, telemetry.error_code = prior, prior_error",
       "telemetry.core_dispatch_authorizer = prior")),
    m("M1a-earliest-candidate-order-across-pages", ORDERING,
      (INC, "                                   scan.max_ts))\n", "                                   entry.ts_ms))\n")),
    m("M1b-bound-is-latest-entry-not-running-max", ORDERING,
      (INC, "            scan.max_ts = max(scan.max_ts, entry.ts_ms)\n", "            scan.max_ts = entry.ts_ms\n")),
    m("M1c-uncertain-entry-not-marked", ORDERING,
      (INC, "                if entry.uncertain or item.key > entry.ts_ms or (until",
       "                if item.key > entry.ts_ms or (until")),
    m("M1d-uncertain-span-not-extended", ORDERING,
      (INC, "                    until = entry.latest_ms if until is None else max(until, entry.latest_ms)\n",
       "                    pass\n")),
    m("M1e-ordering-omission-dropped", ORDERING,
      (INC, '                    reasons.append(f"ordering_uncertain:{row[\'source\']}")\n',
       "                    pass\n")),
    m("M1f-ordering-carry-dropped", ORDERING,
      (INC, '                if until is not None:\n                    payload["o"] = until\n',
       '                if False:\n                    payload["o"] = until\n')),
    m("M1g-tail-not-resolved", ORDERING,
      (INC, "        scan.tail_entry = await self._resolve(scan, args, budget, tail.first_cursor, len(tail.entries) - 1,\n"
            "                                              tail.entries[-1], 0, tally=False)\n",
       "        scan.tail_entry = tail.entries[-1]\n")),
    m("M1h-disagreeing-resolution-accepted", ORDERING,
      (INC, '            scan.zone_mismatch = {"reason": "entry_realtime_disagrees"}\n'
            '            raise PageFailure("timestamp_basis_mismatch")\n',
       "            pass\n")),
    m("M1i-resolution-reserve-ignored", ORDERING,
      (INC, "                or budget.requests + reserve >= c.MAX_REQUESTS_PER_CALL):",
       "                or budget.requests >= c.MAX_REQUESTS_PER_CALL):")),
]

base.MODULES = base.MODULES + ("test_hamcp220_incident_log_timestamps", "test_hamcp220_review1_probes",
                               "test_hamcp220_review1_tool_local_probe")
base.MUTATIONS = (REVIEW1_MUTATIONS + TIMESTAMP_MUTATIONS
                  + [RETARGETED.get(item["id"], item) for item in base.MUTATIONS])


if __name__ == "__main__":
    raise SystemExit(base.main())
