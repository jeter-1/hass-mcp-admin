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
TEXT_AS_UTC = "text parsed as UTC"
SILENT = "an ambiguous or nonexistent time resolved silently"
UTC_FALLBACK = "time-zone fallback to UTC"
CALIBRATION = "realtime calibration bypassed"
UTC_ZONE = "Zone(UTC_NAME, timezone.utc)"

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
        (INC, "                source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted, edge))",
         "                if scan.failure is None:\n"
         "                    source_rows.append(self._source_row(scan, consumed, skipped, outside, emitted, edge))")),
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
      (INC, "possible and not entry.certain_in(args[\"start_ms\"], args[\"end_ms\"])))",
       "False))")),
    m("T3a-unknown-zone-falls-back-to-utc", UTC_FALLBACK,
      (TBS, "        raise TimeZoneUnavailable(\"time_zone_unknown\") from None",
       f"        return {UTC_ZONE}")),
    m("T3b-missing-zone-falls-back-to-utc", UTC_FALLBACK,
      (TBS, "        raise TimeZoneUnavailable(\"time_zone_invalid\")", f"        return {UTC_ZONE}")),
    m("T3c-service-zone-failure-reads-utc", UTC_FALLBACK,
      (INC, "            args[\"zone\"] = None\n", "            args[\"zone\"] = load_zone(\"UTC\")\n")),
    m("T4a-rendering-mismatch-accepted", CALIBRATION,
      (TBS, "        if not surviving:\n            raise TimeBasisMismatch(\"timestamp_basis_mismatch\")",
       "        if not surviving:\n            surviving = (zone,)")),
    m("T4b-unverified-page-reported-complete", CALIBRATION,
      (INC, "                    reasons.append(f\"timestamp_basis_unverified:{row['source']}\")",
       "                    pass")),
]

base.MODULES = base.MODULES + ("test_hamcp220_incident_log_timestamps",)
base.MUTATIONS = TIMESTAMP_MUTATIONS + [RETARGETED.get(item["id"], item) for item in base.MUTATIONS]


if __name__ == "__main__":
    raise SystemExit(base.main())
