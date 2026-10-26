# Engineering 2.4.1-beta.38 release notes

Engineering 2.4.1-beta.38 lets `get_incident_logs` read the `supervisor`,
`host`, `host:<category>` and `addon:<slug>` sources from the host journal
(HAMCP-224). It follows 2.4.1-beta.37 and includes it. It adds no
dependency change; both hashed locks are beta.37's. The only add-on
configuration change is `journald: true`.

Acceptance: [`V2_4_1_BETA38_ACCEPTANCE.md`](V2_4_1_BETA38_ACCEPTANCE.md).
Design, owner decisions D1-D10 and the start-risk evidence (§0):
[`HAMCP_224_HOST_JOURNAL_DESIGN.md`](HAMCP_224_HOST_JOURNAL_DESIGN.md).

## Why

Through beta.37 these sources were refused with `authority_not_granted`: the
Supervisor serves their log routes only to role `manager`, and Engineering
runs as `homeassistant`. Josh decided (D1-D10, 2026-10-25) to read them from
the host journal instead of raising the role.

## The change

- **`journald: true`** (`config.yaml`). The Supervisor bind-mounts the host's
  `/var/log/journal` and `/run/log/journal` read-only into the container.
  `hassio_role` stays `homeassistant`. No Supervisor route that needs
  `manager` is called.
- **A read-only journal reader** (`incident_logs/host_journal.py`). It binds
  14 fixed `libsystemd.so.0` symbols through `ctypes` on the base image's
  own library (Debian `libsystemd0` 257.13-1~deb13u1; no new package) and
  reads 9 fixed fields. One handle opened with `SD_JOURNAL_OS_ROOT` merges
  the runtime and persistent journals. Each field is copied up to 16 KiB;
  a call reads at most 4 MiB of fields and 20,000 entries per source (the
  resume walk counts), and stops one second before the call deadline. The
  read runs in one worker thread.
- **Fixed matches.** `supervisor` is `hassio_supervisor`; `host` is the
  Supervisor's pinned default 52-identifier set plus kernel entries;
  `host:<category>` is a fixed subset; `addon:<slug>` needs
  `SYSLOG_IDENTIFIER` equal to `CONTAINER_NAME` (`app_<slug>` or
  `addon_<slug>`) through `_TRANSPORT=journal`. Every entry is rechecked in
  Python; a failed recheck is counted (`match_rejected`), never returned.
- **Timestamps** are the journal's realtime (`journal_realtime_utc`); no
  text is parsed. Each entry carries a keyed `boot_ref`.
- **Positions.** A continuation holds the entry's realtime and a keyed
  digest of its libsystemd cursor, never the raw cursor. A position that is
  no longer found is `journal_continuity_broken`.
- **Secrets.** At the single `_finish` exit, every returned journal field
  (message, `process`, `kernel_subsystem`, `kernel_device`) is screened
  before anything reads it, by the **same** screen as the
  configuration-definitions report (`credentials.screen`; the journal has no
  screen of its own). The complete field is screened before any cut: a field
  over 16 KiB, or bytes that are not UTF-8, are withheld whole as
  unverifiable. Invisible, format or control characters, the HAMCP-132
  credential and known-secret forms (after bounded percent-decoding, NFKC
  and the confusable fold, on the text and its squashed form) and the
  labelled forms the journal opts in to (a credential label with `=` or `:`,
  `Bearer`, PEM private keys) withhold the **whole** field
  (`credential_text_withheld` or `unverifiable_text_withheld`). In the
  journal a strong credential keyword alone (`password`, `token`, `secret`,
  `api key`, `authorization`, `bearer`, `--pw`, …, in the normal or
  squashed form) or the mosquitto `-P` option in any quoting, argv or split
  form withholds the field. Coloured lines are shown without their ANSI
  colour (SGR) codes; a credential hidden by or across a colour code is still
  withheld, and any other escape (cursor movement, erase, OSC, a malformed
  escape) or control character withholds the field as unverifiable. The
  final-envelope scan names each withheld entry (`withheld:entries[n]`); a
  withheld entry a text filter left out counts in `withheld_not_searched`,
  and one the level filter left out in `withheld_level_filtered`. Any of
  these makes the result partial. The shared `sanitize_log_lines`
  and the strict sanitizer then run once, as for every source. Fail closed:
  ordinary text with a label and `:` or `=` (`token: refreshed`), any line
  naming a credential keyword (`token refreshed`) and another tool's `-P`
  option (`iptables -P`) are withheld too. The configuration-definitions report does not opt in to the labelled
  forms and is unchanged.
- **Order and bounds.** Host journal entries merge on the full microsecond
  realtime (`ts` shows milliseconds; ties go to the earlier requested
  source). Edge probes count toward the 20,000-entry bound, and each field
  read checks the deadline and the 4 MiB call bound before it copies.
- **Unavailability** comes from `/proc/self/mountinfo` and the open, never
  from an empty result: `journald_not_enabled`, `journal_mount_incomplete`,
  `journal_mount_not_read_only`, `journal_unreadable` (with errno),
  `journal_reader_unavailable`, `journal_empty_or_unsupported`,
  `journal_read_failed`.

`core` and `addon:self` keep their Supervisor routes and Home Assistant
time-zone handling.

## Compatibility

- No input schema change. The catalog is still **96 tools, 97 with the
  optional YAML reader admitted**. Only the `get_incident_logs` description
  and its capability-matrix justification text change.
- Routing, provider (`supervisor_incident_logs`), policy
  (`incident_window_journal_read_v1`), the Core gate and telemetry are
  unchanged.
- The signed Core capability profiles, probe fingerprints and both signed
  registries are unchanged; the Core registry is fetched from `main` and
  this release does not touch it.
- No persisted format change: the signed incident-log cursor gains a journal
  state shape, but cursors are process-keyed and expire within an hour.
- No workflow, Dockerfile, dependency, Supervisor role or stable-v1 change.

**Protected runtime files.** Josh authorized this scoped HAMCP-224 change
(D1-D10, D9 for `config.yaml`): `incident_logs/host_journal.py` (new),
`incident_logs/contracts.py`, `incident_logs/service.py`,
`tools/incident_logs.py`, `capabilities.py` (justification text),
`config.yaml` (`journald: true`) and the version.

**Release collision resolved.** PR #311 merged first as `2.4.1-beta.37`, so
this change is restaged at `2.4.1-beta.38` on a base that carries beta.37.

## Rollback

Reinstall the 2.4.1-beta.37 artifact. It has no `journald: true`, so the
Supervisor removes both journal mounts. No stored format changes.
