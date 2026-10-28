"""HAMCP-234 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery (disposable copy of the tracked tree
outside the repository, every fragment matching exactly once, fail-fast
suites per mutant) and the HAMCP-224 runner's per-run temporary directory.
The mutations are HAMCP-234's own; earlier mutations keep their recorded
results, and their suites run here in the baseline and against every
mutant. ``test_hamcp234_journal_usability`` runs first, so most mutants fail
fast; the HAMCP-224 journal suites, the incident-log suites, the shared
sanitizer suite and the HAMCP-132 and PR #294 credential suites follow,
unchanged.

The task's four required classes each have mutations:
- ``R*``: a rule id carrying the matched text (``R1`` the message's text,
  ``R2`` its length, ``R3`` the screen's own id with the text, ``R4`` a
  field's text);
- ``K*``: calibration returning any text (``K1`` a message sample, ``K2``
  a process name, ``K3`` the sanitizer's verdict ignored, ``K4`` the
  entries themselves);
- ``P*``: the container level taken from PRIORITY when a known format
  matches (``P1`` the parse skipped, ``P2`` no source a container source,
  ``P3`` the parsed level only when it agrees with PRIORITY);
- ``D*``: a withheld entry dropped by min_level without being counted
  (``D1`` an unverified-level withheld entry left out uncounted, ``D2`` a
  withheld container entry's PRIORITY trusted as its level, ``D3`` a
  withheld entry under min_level counted complete).

Further classes probe the parse (``F*``: a level word anywhere in the line,
a level read from a later line, host sources parsed, withheld text parsed),
the counts (``C*``: fields never counted, the matching rules only the
deciding one, withheld entries made partial) and the row (``W1``:
``withheld_rules`` dropped).

Usage:
    python docs/evidence/hamcp234-mutations/run_mutations.py \\
        --work /var/tmp/hamcp234-mut --output docs/evidence/hamcp234-mutations/results.json
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
INC = base.INC
HJ = f"{E}/incident_logs/host_journal.py"
CL = f"{E}/incident_logs/container_levels.py"
RULE = "a rule id carrying the matched text"
CALIBRATE = "calibration returning any text"
PRIORITY = "the container level taken from PRIORITY when a known format matches"
DROPPED = "a withheld entry dropped by min_level without being counted"
PARSE = "the container level read from the wrong text"
COUNT = "calibration counts not the read's"

MESSAGE_RULE = '            rules = {} if omission is None else {"message": _rule_id(rule)}\n'
LEVEL_KNOWN = ('            level_known = args["min_level"] is None or view["level_basis"] '
               '!= cl.UNVERIFIED_BASIS\n')
CAL_ROW = '                "source": scan.key, "status": status, "reason": scan.failure or scan.finished or stopped,\n'

MUTATIONS = [
    # -- R: rule ids are fixed names, never the text (task A)
    m("R1-rule-id-carries-message-text", RULE,
      (INC, MESSAGE_RULE,
       '            rules = {} if omission is None else {"message": _rule_id(rule) + "="\n'
       '                     + (record.message or b"").decode("utf-8", "replace")[:40]}\n')),
    m("R2-rule-id-carries-message-length", RULE,
      (INC, MESSAGE_RULE,
       '            rules = {} if omission is None else {"message": _rule_id(rule) + "."\n'
       '                     + str(len(record.message or b""))}\n')),
    m("R3-screen-rule-id-carries-text", RULE,
      (HJ, "    return marker, OMISSIONS[marker], rule\n",
       '    return marker, OMISSIONS[marker], f"{rule}:{screened[:40]}"\n'),
      (INC, '    return rule if rule in hj.RULE_IDS else hj.RULE_UNLISTED\n', "    return rule\n")),
    m("R4-field-rule-id-carries-field-text", RULE,
      (INC, "                    rules[name] = _rule_id(field_rule)\n",
       '                    rules[name] = _rule_id(field_rule) + "=" + raw.decode("utf-8", "replace")[:40]\n')),
    # -- K: calibration returns counts only (task B)
    m("K1-calibration-message-sample", CALIBRATE,
      (INC, CAL_ROW, CAL_ROW + '                "sample": (in_window[0].record.message or b"").decode("utf-8", '
                               '"replace") if in_window else None,\n')),
    m("K2-calibration-process-name", CALIBRATE,
      (INC, CAL_ROW, CAL_ROW + '                "processes": sorted({(item.record.identifier or b"").decode('
                               '"utf-8", "replace") for item in in_window}),\n')),
    m("K3-calibration-sanitizer-verdict-ignored", CALIBRATE,
      (INC, "            if result.failed_closed or result.redacted_field_count or result.value != envelope:\n",
       "            if result.failed_closed:\n")),
    m("K4-calibration-returns-entries", CALIBRATE,
      (INC, '        if args["calibration"]:\n', '        if False:\n')),
    # -- P: a known format's level, not PRIORITY (task C)
    m("P1-container-level-from-priority", PRIORITY,
      (INC, "    parsed = cl.parse_level(message)\n", "    parsed = None\n")),
    m("P2-no-container-sources", PRIORITY,
      (CL, '    return key == "supervisor" or key.startswith("addon:")\n', "    return False\n")),
    m("P3-parsed-level-only-when-priority-agrees", PRIORITY,
      (INC, "    if parsed is not None:\n", "    if parsed is not None and parsed == level:\n")),
    # -- D: a withheld entry under min_level is counted (task C)
    m("D1-unverified-withheld-dropped-uncounted", DROPPED,
      (INC, "                elif withheld_entry:\n",
       "                elif withheld_entry and view[\"level_basis\"] != cl.UNVERIFIED_BASIS:\n")),
    m("D2-withheld-priority-trusted-as-level", DROPPED,
      (INC, LEVEL_KNOWN, "            level_known = True\n")),
    m("D3-withheld-level-filtered-complete", DROPPED,
      (INC, "                    if row[\"withheld_level_filtered\"]:\n"
            "                        reasons.append(f\"withheld_level_filtered:{row['source']}\")\n", "")),
    # -- F: the parse reads the start of the screened first line only
    m("F1-level-word-anywhere", PARSE,
      (CL, "        match = form.match(head)\n", "        match = form.search(head)\n")),
    m("F2-level-from-any-line", PARSE,
      (CL, '    head = message.split("\\n", 1)[0][:_HEAD]\n',
       "    head = message[:_HEAD]\n"
       "    for line in message.split(\"\\n\"):\n"
       "        for form in _FORMATS:\n"
       "            if form.match(line):\n"
       "                return _LEVELS[form.match(line).group(1)]\n")),
    m("F3-host-sources-parsed", PARSE,
      (INC, "    if not container:\n", "    if False:\n")),
    m("F4-withheld-text-parsed", PARSE,
      (INC, "            level, basis = _journal_level(container, record.priority, None if omission else body)\n",
       "            level, basis = _journal_level(container, record.priority,\n"
       "                                          (record.message or b\"\").decode(\"utf-8\", \"replace\"))\n")),
    # -- C: the calibration counts are the read's
    m("C1-calibration-fields-never-counted", COUNT,
      (INC, "                for name, raw, truncated in parts:\n",
       "                for name, raw, truncated in parts[:1]:\n")),
    m("C2-matching-rules-only-the-deciding-one", COUNT,
      (INC, "                    for match in dict.fromkeys(_rule_id(each) for each in "
            "hj.screen_text_rules(raw, secrets, truncated)):\n",
       "                    for match in (rule,):\n")),
    m("C3-withheld-entries-make-partial", COUNT,
      (INC, "            elif stopped or rejected or unreadable or scan.start_location == \"retention_edge\":\n",
       "            elif stopped or rejected or unreadable or withheld or scan.start_location == \"retention_edge\":\n")),
    # -- W: the row names the rules
    m("W1-row-withheld-rules-dropped", RULE,
      (INC, '                        row["withheld_rules"] = dict(view["withheld_rules"])\n', "")),
]

base.MODULES = ("test_hamcp234_journal_usability", "test_hamcp224_host_journal",
                "test_hamcp224_review1_corrections", "test_hamcp224_review2_corrections",
                "test_hamcp224_review3_corrections", "test_pr313_delta_residuals", "test_pr313_independent_review",
                "test_review2_ansi_residual", "test_hamcp131_incident_logs",
                "test_hamcp220_incident_log_timestamps", "test_hamcp191_shared_sanitizer",
                "test_hamcp132_configuration_definitions", "test_pr294_delta_review",
                "test_pr294_corr2_extra", "test_pr294_final32_review")
base.MUTATIONS = MUTATIONS

_run_tests = base.run_tests


def run_tests(tree: Path, timeout: int, *, failfast: bool) -> tuple[int, str, float]:
    # Each run gets its own temporary directory, removed after it (HAMCP-224).
    scratch = tempfile.mkdtemp(prefix="hamcp234-mutant-")
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = scratch
    try:
        return _run_tests(tree, timeout, failfast=failfast)
    finally:
        if previous is None:
            del os.environ["TMPDIR"]
        else:
            os.environ["TMPDIR"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


base.run_tests = run_tests


if __name__ == "__main__":
    raise SystemExit(base.main())
