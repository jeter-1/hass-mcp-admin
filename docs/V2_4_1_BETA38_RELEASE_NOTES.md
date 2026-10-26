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
- **Secrets.** At the single `_finish` exit, a journal message is screened
  before anything reads it: unverifiable text, the HAMCP-132 credential
  forms, journal key forms (`psk`, `wpa_psk`, `network_key`, ...), PEM
  private keys and the mosquitto `-P`/`--pw` options withhold the **whole**
  message (`credential_text_withheld`; the result is partial). The shared
  `sanitize_log_lines` and the strict sanitizer then run once, as for every
  source; they redact other token forms in place (`redacted_by_policy`).
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
