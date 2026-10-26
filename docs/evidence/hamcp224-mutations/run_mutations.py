"""HAMCP-224 mutation runner (offline, deterministic).

Reuses the HAMCP-131 runner's machinery (disposable copy of the tracked tree
outside the repository, every fragment matching exactly once, fail-fast
suites per mutant). The mutations are HAMCP-224's own; the HAMCP-131 and
HAMCP-220 mutations keep their recorded results, and their suites run here
in the baseline and against every mutant. The suites are
``test_hamcp224_host_journal`` (first, so most mutants fail fast), the
incident-log suites and the shared sanitizer suite.

The task's four required classes each have mutations:
- ``F*``: a non-allowlisted field read or returned (``F1``: the machine ID
  read; the raw boot ID or the raw libsystemd cursor returned; an entry key
  outside the row allowlist; a process name returned unscreened);
- ``S*``: the sanitizer or the whole-message withholding bypassed (the
  credential screen, its squashed, confusable-folded, PEM or ``-P`` forms,
  the unverifiable-text check, ANSI stripping, the withheld text returned,
  the shared sanitizer's output ignored);
- ``T*``: text timestamps used instead of the journal realtime (the Home
  Assistant/Supervisor text prefix, or mosquitto's epoch prefix);
- ``O*``: journald off or unreadable reported as complete or empty (the
  mount verdict, the empty-journal cutoff, a failed read finished, an
  unavailable row complete, a failed source's cursor done).

Further classes probe the mounts (``M*``), the bounds (``B*``), the
continuation (``C*``), the source match (``A*``) and the clock and
retention disclosures (``E*``).

Review 1 (HAMCP-224 H1/H2, M1-M3) retargets the ``S*`` mutations at the
shared configuration-definitions screen the journal now calls, and adds
``H*`` (a cut before the screen, a journal-specific screen, the squashed
form removed), ``W*`` (a withheld entry, field or filtered-out entry
reported as complete), ``U*`` (the merge or continuation key at millisecond
precision) and ``L*`` (an edge probe unbounded or uncounted, a field copied
past the call bound or read after the deadline). The HAMCP-132 and PR #294
credential suites run unchanged in the baseline and against every mutant.

Review 2 retargets ``S6`` at the ``-P`` tokenizer and ``S8`` at the CSI
strip, and adds ``K*`` (the label rule: the keyword on the normal form only,
the tokenizer ignoring quotes or a split dash, the dash fold removed, the
labelled mode falling back to value shapes only), ``A*`` (only the stripped
or only the original text screened, a CSI sequence alone withholding) and
``W4``/``W5`` (a withheld entry the level filter left out uncounted or
complete). The reviewer's delta suite runs unchanged with its one
owner-superseded method registered as an expected failure by
``test_hamcp224_review2_corrections`` in the same invocation.

Review 3 (H3) narrows the ANSI exemption to SGR colour codes: ``S8`` and
``A1``-``A3`` follow the SGR names, and ``N*`` add every CSI removed again,
letters allowed in the SGR code, the original form screened after the strip,
the SGR code removed repeatedly and its length unbounded. The reviewer's
combined-ANSI suite (``test_review2_ansi_residual``) and
``test_hamcp224_review3_corrections`` run unchanged in the baseline and
against every mutant.

Usage:
    python docs/evidence/hamcp224-mutations/run_mutations.py \\
        --work /var/tmp/hamcp224-mut --output docs/evidence/hamcp224-mutations/results.json
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hamcp131-mutations"))

import run_mutations as base  # noqa: E402

m = base.m
E = base.E
INC, CON = base.INC, base.CON
HJ = f"{E}/incident_logs/host_journal.py"
CRED = f"{E}/configuration_definitions/credentials.py"
FIELD = "a non-allowlisted field read or returned"
SANITIZE = "the sanitizer or whole-message withholding bypassed"
TEXT_TIME = "text timestamps used instead of the journal realtime"
OFF = "journald off or unreadable reported as complete or empty"
MOUNT = "a journal mount not verified"
BOUND = "a scan or field bound not enforced"
CONTINUE = "a continuation not bound to the consumed entry"
MATCH = "an entry outside its source's match returned"
DISCLOSE = "a clock or retention disclosure dropped"
WITHHELD = "a withheld entry or field reported as complete"
ORDER = "the merge not on the full microsecond realtime"
TS = "                entry = RawEntry(ts_ms=record.rt_us // 1000,"
SGR = r'_SGR = re.compile(r"\x1b\[[0-9;]{0,32}m")'
SGR_ESCAPE = r'_SGR_ESCAPE = re.compile(r"\x1b(?=\[[0-9;]{0,32}m)")'

MUTATIONS = [
    # -- F: a non-allowlisted field
    m("F1-machine-id-read", FIELD,
      (HJ, 'boot, _ = self._field("_BOOT_ID")', 'boot, _ = self._field("_MACHINE_ID")')),
    m("F2-raw-boot-id-returned", FIELD,
      (HJ, 'boot_ref=None if boot is None else self.digest("journal_boot", boot)[:12],',
       'boot_ref=None if boot is None else boot.decode("ascii"),')),
    m("F3-raw-journal-cursor-kept", FIELD,
      (HJ, '            return self.digest("journal_cursor", ctypes.string_at(pointer.value))',
       '            return ctypes.string_at(pointer.value).decode("ascii")')),
    m("F4-entry-key-outside-allowlist", FIELD,
      (INC, "                    row[\"boot_ref\"] = item.record.boot_ref\n",
       "                    row[\"boot_ref\"] = item.record.boot_ref\n"
       "                    row[\"journal_entry\"] = item.digest\n")),
    m("F5-process-returned-unscreened", FIELD,
      (INC, "                fields[name], field_omission = hj.screen_text(raw, secrets, name in record.truncated_fields)",
       '                fields[name], field_omission = raw.decode("utf-8", "replace"), None')),
    # -- S: the sanitizer or withholding (the shared screen, HAMCP-224 H1/H2)
    m("S1-credential-screen-skipped", SANITIZE,
      (HJ, "    marker = credentials.screen(text, known_secrets, labelled=True)", "    marker = None")),
    m("S2-labelled-forms-not-opted-in", SANITIZE,
      (HJ, "    marker = credentials.screen(text, known_secrets, labelled=True)",
       "    marker = credentials.screen(text, known_secrets)")),
    m("S3-squashed-labelled-form-dropped", SANITIZE,
      (CRED, "        or _SQUASHED_LABELLED_FIELD.search(squashed) is not None\n", "")),
    m("S4-confusables-not-folded", SANITIZE,
      (CRED, "    text = fold_confusables(text)\n", "")),
    m("S5-pem-private-key-kept", SANITIZE,
      (CRED, "        or _PEM_PRIVATE.search(text) is not None or _PEM_PRIVATE.search(squashed) is not None\n", "")),
    m("S6-mosquitto-password-option-kept", SANITIZE,
      (CRED, "        or password_option(decoded)\n", "")),
    m("S7-unverifiable-text-kept", SANITIZE,
      (CRED, "    if unverifiable_text(value):\n        return UNVERIFIABLE_MARKER\n"
             "    if credential_text(value, known_secrets, labelled=labelled):",
       "    if credential_text(value, known_secrets, labelled=labelled):")),
    m("S8-ansi-not-stripped", SANITIZE,
      (HJ, '    text = _SGR.sub("", original)\n', "    text = original\n")),
    m("S9-withheld-text-returned-with-omission", SANITIZE,
      (HJ, "        return marker, OMISSIONS[marker]", "        return text, OMISSIONS[marker]")),
    m("S10-shared-sanitizer-output-ignored", SANITIZE,
      (INC, "            safe = list(sanitized[offset:offset + len(entry_lines)])",
       "            safe = list(entry_lines)")),
    m("S11-withheld-message-searchable", SANITIZE,
      (INC, '"search": "" if withheld else ((fields["process"] or "") + ": " + body).casefold(),',
       '"search": ((fields["process"] or "") + ": "'
       ' + (item.record.message or b"").decode("utf-8", "replace")).casefold(),')),
    # -- H: one screen, before any cut (HAMCP-224 review 1 H1/H2)
    m("H1-truncated-before-screening", SANITIZE,
      (INC, 'hj.screen_text(record.message or b"", self.known_secrets, record.message_truncated)',
       'hj.screen_text((record.message or b"")[:c.MAX_MESSAGE_CHARS], self.known_secrets)')),
    m("H2-journal-specific-screen", SANITIZE,
      (HJ, "    marker = credentials.screen(text, known_secrets, labelled=True)",
       '    marker = WITHHELD_MARKER if re.search(r"(?i)\\b(?:password|token|api_key)\\s*[:=]", text) else None')),
    m("H3-squashed-form-removed", SANITIZE,
      (CRED, "    squashed = squash(text)\n", "    squashed = text\n")),
    # -- T: text timestamps
    m("T1-text-prefix-time-used", TEXT_TIME,
      (INC, TS,
       '                found = re.search(rb"(\\d{4}-\\d\\d-\\d\\d \\d\\d:\\d\\d:\\d\\d)\\.(\\d{3})",'
       ' record.message or b"")\n'
       '                ts = (int(datetime.strptime(found[1].decode(), "%Y-%m-%d %H:%M:%S")'
       '.replace(tzinfo=timezone.utc).timestamp()) * 1000 + int(found[2])) if found else record.rt_us // 1000\n'
       '                entry = RawEntry(ts_ms=ts,')),
    m("T2-epoch-prefix-time-used", TEXT_TIME,
      (INC, TS,
       '                found = re.match(rb"(\\d{10}): ", record.message or b"")\n'
       '                ts = int(found[1]) * 1000 if found else record.rt_us // 1000\n'
       '                entry = RawEntry(ts_ms=ts,')),
    # -- O: journald off or unreadable
    m("O1-journald-off-not-detected", OFF,
      (HJ, '    if not present:\n        return "journald_not_enabled"\n', '    if not present:\n        return None\n')),
    m("O2-empty-journal-read-as-empty", OFF,
      (HJ, '            self.result.unavailable = "journal_empty_or_unsupported"\n            return\n',
       "            pass\n")),
    m("O3-unavailable-read-finished", OFF,
      (INC, '                scan.failure = read.unavailable or "journal_read_failed"\n',
       '                scan.finished = "tail_reached"\n')),
    m("O4-unavailable-row-complete", OFF,
      (INC, '            status = "unavailable" if not taken else "failed"', '            status = "complete"')),
    m("O5-failed-source-cursor-done", OFF,
      (INC, '            return {"failed": 1}\n        if scan.finished is not None and count == len(scan.items):',
       '            return {"done": 1}\n        if scan.finished is not None and count == len(scan.items):')),
    m("O6-reader-exception-escapes", OFF,
      (INC, "        except Exception:\n            read = hj.JournalRead(unavailable=\"journal_read_failed\")\n",
       "        except ZeroDivisionError:\n            read = hj.JournalRead(unavailable=\"journal_read_failed\")\n")),
    # -- M: mounts
    m("M1-read-write-mount-accepted", MOUNT,
      (HJ, '    if any("ro" not in mounts[point] for point in present):\n'
           '        return "journal_mount_not_read_only"\n', "")),
    m("M2-incomplete-mount-accepted", MOUNT,
      (HJ, '    if len(present) != len(MOUNT_POINTS):\n        return "journal_mount_incomplete"\n', "")),
    m("M3-lowest-mount-wins", MOUNT,
      (HJ, '        mounts[point] = frozenset(parts[5].split(","))',
       '        mounts.setdefault(point, frozenset(parts[5].split(",")))')),
    m("M4-mount-escapes-not-decoded", MOUNT,
      (HJ, "        point = _MOUNT_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), parts[4])",
       "        point = parts[4]")),
    m("M5-symlinks-followed", MOUNT,
      (HJ, "OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC",
       "OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC")),
    m("M6-journal-directories-not-opened", MOUNT,
      (HJ, '                    os.close(os.open(point.lstrip("/"), OPEN_FLAGS, dir_fd=root))',
       "                    pass")),
    # -- B: bounds
    m("B1-field-cap-ignored", BOUND,
      (HJ, "        take = min(size.value, len(prefix) + MAX_FIELD_BYTES)", "        take = size.value")),
    m("B2-truncation-not-reported", BOUND,
      (HJ, "        return raw[len(prefix):], size.value > take", "        return raw[len(prefix):], False")),
    m("B3-resume-walk-uncounted", BOUND,
      (HJ, "        if scan.scanned + scan.resume_walked + scan.probed >= MAX_SCAN_ENTRIES:",
       "        if scan.scanned + scan.probed >= MAX_SCAN_ENTRIES:")),
    m("B4-field-bytes-uncounted", BOUND,
      (HJ, "        self.result.field_bytes += take\n", "        pass\n")),
    m("B5-deadline-ignored", BOUND,
      (HJ, '        if time.monotonic() >= self.deadline:\n            return "deadline_reached"\n', "")),
    m("B6-deadline-not-reserved", BOUND,
      (INC, "                                           budget.deadline - 1.0)",
       "                                           budget.deadline)")),
    m("B7-bound-stop-not-disclosed", BOUND,
      (INC, "        stopped = None if result is None or scan.failure is not None or scan.finished else result.stopped",
       "        stopped = None")),
    m("B8-read-on-the-event-loop", BOUND,
      (INC, "read = await asyncio.to_thread(self.journal_reader.read, plans, self.codec.digest,",
       "read = await asyncio.sleep(0) or self.journal_reader.read(plans, self.codec.digest,")),
    # -- C: continuation
    m("C1-resume-without-digest-match", CONTINUE,
      (HJ, "            if self._cursor_digest() == wanted:", "            if True:")),
    m("C2-stale-continuation-restarted", CONTINUE,
      (INC, '                # The consumed entry is gone, or a clock step hides it.\n'
            '                raise c.cursor_error(True, "journal_continuity_broken")\n',
       "                scan.journal_state = None\n")),
    m("C3-state-names-the-wrong-entry", CONTINUE,
      (INC, "            item = scan.items[count - 1]\n            # The entry's realtime",
       "            item = scan.items[0]\n            # The entry's realtime")),
    m("C4-state-digest-unchecked", CONTINUE,
      (INC, '        and type(state["jd"]) is str and _HEX32.match(state["jd"]) is not None\n',
       '        and type(state["jd"]) is str\n')),
    # -- A: source match
    m("A1-addon-equality-unchecked", MATCH,
      (HJ, "    if match.equal and len({values.get(name) for name in match.equal}) != 1:\n        return False\n",
       "")),
    m("A2-addon-transport-unchecked", MATCH,
      (HJ, 'CONTAINER_NAME=names, _TRANSPORT="journal"),),', "CONTAINER_NAME=names),),")),
    m("A3-rejected-entries-in-window", MATCH,
      (INC, "                in_window = record.rejected is None and start_us <= record.rt_us < end_us",
       "                in_window = start_us <= record.rt_us < end_us")),
    m("A4-addon-self-as-journal-source", MATCH,
      (CON, "    return source_key not in READABLE_ROUTES", "    return source_key != SOURCE_CORE")),
    m("A5-sshd-in-host-set", MATCH,
      (HJ, '    "wpa_supplicant",\n})', '    "wpa_supplicant", "sshd",\n})')),
    m("A6-error-read-as-warning", MATCH,
      (HJ, 'b"3": "error"', 'b"3": "warning"')),
    # -- E: clock and retention disclosures
    m("E1-clock-regression-uncounted", DISCLOSE,
      (HJ, "            scan.clock_regressions += 1\n", "            pass\n")),
    m("E2-retention-edge-gap-dropped", DISCLOSE,
      (INC, "                if read.cutoff_from_us is not None and read.cutoff_from_us > start_us:",
       "                if False:")),
    # -- W: completeness of a withheld read (HAMCP-224 review 1 M1)
    m("W1-final-envelope-scan-removed", WITHHELD,
      (INC, "                items = credentials.withheld_items(result.value)\n", "                items = []\n")),
    m("W2-withheld-field-not-reported", WITHHELD,
      (INC, "            withheld = omission or next(iter(fields_withheld.values()), None)\n",
       "            withheld = omission\n")),
    m("W3-filtered-withheld-uncounted", WITHHELD,
      (INC, "                    unsearched[position] += 1\n", "                    pass\n")),
    # -- W, review 2 M1 residual: a withheld entry the level filter left out
    m("W4-level-filtered-withheld-uncounted", WITHHELD,
      (INC, "                    level_filtered[position] += 1\n", "                    pass\n")),
    m("W5-level-filtered-withheld-complete", WITHHELD,
      (INC, "              or unsearched[scan.position] or level_filtered[scan.position]\n",
       "              or unsearched[scan.position]\n")),
    # -- K: the label rule (review 2 H2 residual)
    m("K1-keyword-normal-form-only", SANITIZE,
      (CRED, "_KEYWORD.search(text) is not None or _KEYWORD.search(squashed) is not None",
       "_KEYWORD.search(text) is not None")),
    m("K2-option-tokenizer-ignores-quotes", SANITIZE,
      (CRED, """_OPTION_QUOTES = frozenset("'\\"`\\\\")""", "_OPTION_QUOTES = frozenset()")),
    m("K3-option-split-dash-ignored", SANITIZE,
      (CRED, '        if word != "-" or index + 1 == len(tokens):', "        if True:")),
    m("K4-dash-fold-removed", SANITIZE,
      (CRED, "    text, squashed = text.translate(_DASHES), squashed.translate(_DASHES)\n", "")),
    m("K5-labelled-mode-shape-only", SANITIZE,
      (CRED, "        _KEYWORD.search(text) is not None or _KEYWORD.search(squashed) is not None\n"
             "        or password_option(decoded)\n"
             "        # The value shapes, an additional layer.\n"
             "        or _value_shape(text, squashed)\n",
       "        _value_shape(text, squashed)\n")),
    # -- A: two-form ANSI screening (review 2 M4, owner decision)
    m("A1-stripped-text-only-screened", SANITIZE,
      (HJ, "    if marker is None and text != original:\n"
           '        marker = credentials.screen(_SGR_ESCAPE.sub("", original), known_secrets, labelled=True)\n', "")),
    m("A2-original-text-only-screened", SANITIZE,
      (HJ, "    marker = credentials.screen(text, known_secrets, labelled=True)\n"
           "    if marker is None and text != original:\n",
       "    marker = None\n    if True:\n")),
    m("A3-csi-sequence-alone-withholds", SANITIZE,
      (HJ, 'credentials.screen(_SGR_ESCAPE.sub("", original), known_secrets', "credentials.screen(original, known_secrets")),
    # -- N: SGR colour codes only (review 3 H3, architect direction)
    m("N1-any-csi-stripped", SANITIZE,
      (HJ, SGR + "\n" + SGR_ESCAPE + "\n",
       r'_SGR = re.compile(r"\x1b\[[0-?]{0,32}[ -/]{0,4}[@-~]")' + "\n"
       + r'_SGR_ESCAPE = re.compile(r"\x1b(?=\[[0-?]{0,32}[ -/]{0,4}[@-~])")' + "\n")),
    m("N2-sgr-pattern-allows-letters", SANITIZE,
      (HJ, SGR, r'_SGR = re.compile(r"\x1b\[[0-9;A-Za-z]{0,32}m")')),
    m("N3-original-form-stripped-first", SANITIZE,
      (HJ, 'credentials.screen(_SGR_ESCAPE.sub("", original), known_secrets',
       'credentials.screen(_SGR.sub("", original), known_secrets')),
    m("N4-sgr-removed-repeatedly", SANITIZE,
      (HJ, '    text = _SGR.sub("", original)\n',
       "    text = original\n    while _SGR.search(text):\n        text = _SGR.sub(\"\", text)\n")),
    m("N5-sgr-length-unbounded", SANITIZE,
      (HJ, SGR, r'_SGR = re.compile(r"\x1b\[[0-9;]*m")')),
    # -- U: microsecond order (HAMCP-224 review 1 M2)
    m("U1-merge-precision-dropped", ORDER,
      (INC, "                running = max(running, record.rt_us)\n",
       "                running = max(running, record.rt_us // 1000 * 1000)\n")),
    m("U2-continuation-key-in-milliseconds", ORDER,
      (INC, '"t": item.key_us}', '"t": item.key_us // 1000 * 1000}')),
    # -- L: shared bounds on every path (HAMCP-224 review 1 M3)
    m("L1-edge-probe-unbounded", BOUND,
      (HJ, "            stop = self._bounded(scan)\n            if stop is not None:\n"
           "                raise _Bound(stop)\n            if not self._check(step(",
       "            if not self._check(step(")),
    m("L2-edge-probe-uncounted", BOUND,
      (HJ, "            scan.probed += 1\n", "            pass\n")),
    m("L3-field-copy-unchecked", BOUND,
      (HJ, '        if self.result.field_bytes + take > MAX_CALL_FIELD_BYTES:\n'
           '            raise _Bound("field_bytes_limit")\n', "")),
    m("L4-field-read-after-deadline", BOUND,
      (HJ, '        if time.monotonic() >= self.deadline:\n            raise _Bound("deadline_reached")\n', "")),
    m("E3-unreadable-entries-complete", DISCLOSE,
      (INC, "        elif (stopped or rejected or unreadable or withheld[scan.position]",
       "        elif (stopped or rejected or withheld[scan.position]")),
]

base.MODULES = ("test_hamcp224_host_journal", "test_hamcp224_review1_corrections",
                "test_hamcp224_review2_corrections", "test_hamcp224_review3_corrections",
                "test_pr313_delta_residuals", "test_pr313_independent_review", "test_review2_ansi_residual", "test_hamcp131_incident_logs",
                "test_hamcp220_incident_log_timestamps", "test_hamcp191_shared_sanitizer",
                "test_hamcp132_configuration_definitions", "test_pr294_delta_review",
                "test_pr294_corr2_extra", "test_pr294_final32_review")
base.MUTATIONS = MUTATIONS


if __name__ == "__main__":
    raise SystemExit(base.main())
