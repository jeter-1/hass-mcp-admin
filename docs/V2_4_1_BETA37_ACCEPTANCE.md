# Engineering 2.4.1-beta.37 acceptance

This contract covers HAMCP-224: `get_incident_logs` reads the `supervisor`,
`host`, `host:<category>` and `addon:<slug>` sources from the host journal,
which the Supervisor mounts read-only into Engineering with the new add-on
option `journald: true`. It follows 2.4.1-beta.36 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA37_RELEASE_NOTES.md`](V2_4_1_BETA37_RELEASE_NOTES.md).
- Design, owner decisions D1-D10 and the start-risk evidence (§0):
  [`HAMCP_224_HOST_JOURNAL_DESIGN.md`](HAMCP_224_HOST_JOURNAL_DESIGN.md).
- The incident-log contract this release extends:
  [`HAMCP_131_INCIDENT_LOGS.md`](HAMCP_131_INCIDENT_LOGS.md) and
  [`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).

**Release target.** `2.4.1-beta.37` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.36. The run consumes the declaration. Open draft PR #311 also
stages `2.4.1-beta.37`; whichever merges second must be restaged to the next
free beta and its documents renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's 2026-10-25 decisions D1-D10 (design §0), including D9: the only
  add-on configuration change is `journald: true`; `hassio_role` stays
  `homeassistant`;
- `2.4.1-beta.37` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **One new permission, read-only.** `journald: true` makes the Supervisor
  bind-mount the host's `/var/log/journal` and `/run/log/journal` read-only
  (Supervisor 2026.09.1 `docker/app.py:650-665`). No Supervisor role, API
  route, provider, fallback, write path or tool is added. The Supervisor
  routes for these sources still need role `manager`; they are never called.
- **The reader** (`incident_logs/host_journal.py`). It:
  - binds only the 14 libsystemd symbols in `BOUND_SYMBOLS` through `ctypes`
    on the base image's own `libsystemd.so.0` (Debian `libsystemd0`
    257.13-1~deb13u1, pinned against the Dockerfile base digest and
    `provenance.json`); no field-enumeration, unique-value, descriptor,
    cursor-seek, inotify or write symbol;
  - reads only the 9 fields in `READ_FIELDS`, each copied up to 16 KiB, at
    most 4 MiB per call;
  - opens the container root `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`
    and passes it with `SD_JOURNAL_OS_ROOT`, so one handle merges the
    runtime and persistent journals; both journal directories must open
    without following a symlink;
  - opens, reads and closes that handle in one worker thread, bounded by the
    call deadline minus one second and 20,000 scanned entries per source
    (the resume walk counts);
  - is the only module that imports `ctypes`, with exactly three `os.open`
    calls (the mount table, the root, each journal directory).
- **Matches.** `host` is the Supervisor's pinned default 52-identifier set
  plus kernel entries; each `host:<category>` is a fixed subset; `supervisor`
  is `hassio_supervisor`. `addon:<slug>` requires `SYSLOG_IDENTIFIER` equal
  to `CONTAINER_NAME`, one of `app_<slug>` or `addon_<slug>`, through
  `_TRANSPORT=journal`. Every yielded entry is rechecked in Python; a failed
  check is never returned and is counted (`match_rejected`).
- **Positions.** A continuation carries the entry's realtime and a keyed
  digest of its libsystemd cursor, never the cursor (it embeds the raw boot
  ID). A digest not found from that realtime is `journal_continuity_broken`
  (stale). The reboot marker `boot_ref` is a 12-character keyed digest.
- **Secrets.** The single `_finish` exit screens each message first: ANSI
  is stripped; unverifiable text, any known-secret or credential form, the
  journal key forms (`psk=`, `network_key:`, ...), PEM private keys and the
  mosquitto `-P`/`--pw` options withhold the **whole** message
  (`credential_text_withheld`, the result is partial). Then the shared
  `sanitize_log_lines` and strict `sanitize_untrusted_data` run once, as for
  every other source.
- **Unavailability.** Decided from `/proc/self/mountinfo` and the open,
  never from an empty result: `journald_not_enabled`,
  `journal_mount_incomplete`, `journal_mount_not_read_only`,
  `journal_unreadable` (with errno), `journal_reader_unavailable`,
  `journal_empty_or_unsupported`, `journal_read_failed`. An unavailable
  source is never `complete` and never an empty success.
- **Unchanged.** `core` and `addon:self` keep their Supervisor routes and
  Home Assistant zone handling. Routing, provider (`supervisor_incident_logs`),
  policy (`incident_window_journal_read_v1`), the Core gate and telemetry are
  unchanged; the capability matrix's justification text and the tool
  description are updated to describe the journal sources.

Preserve the following:
- stable v1.1.2;
- persisted formats (the signed cursor gains a journal state shape; cursors
  are process-keyed and expire within an hour, so no stored format exists);
- every input schema; the catalog is **96 tools, 97 with the optional YAML
  reader admitted**; only the `get_incident_logs` description changes;
- probe fingerprints, the signed Core capability profiles and both signed
  registries; the Core registry is fetched from `main` and this release does
  not change it;
- the HOLD and the support window;
- every workflow and dependency; `hassio_role: homeassistant`.

### Offline proof

No test reaches a live system or a real host journal. Every value is
synthetic.

- **Real-format fixtures** (D10, `tests/fixtures/hamcp224_journal/`):
  persistent and runtime journal files, uncompressed and zstd, written by
  systemd v257.13 `systemd-journal-remote` from committed export streams
  (`generate.py`, `provenance.json`). They cover Supervisor entries, add-ons
  under both container-name forms, a near-miss slug, identifier spoofs,
  kernel USB disconnect and reconnect, storage, network and OS entries, a
  20 KiB message, a partial Docker line, a missing priority, planted
  credentials in several forms, fields that must never be read, three
  entries in one microsecond, a reboot and a clock step backwards. The
  `mountinfo-*` files cover journald off, on, incomplete, not read-only,
  remounted read-write and escaped mount points.
- `tests/test_hamcp224_host_journal.py`: the reader over both variants, the
  merge, every category, the field and symbol allowlists (a recording
  library), continuation within one microsecond, stale digests, every bound,
  every unavailability reason (including a symlinked journal directory and an
  unreadable root), the screening forms, the service over every journal
  source with a planted-value and field-allowlist sweep, pagination equality,
  a mixed `core` and `supervisor` read, and static boundary checks (the only
  `ctypes` module, the three `os.open` calls, no write API, the
  `config.yaml` pin, the library pin).
- The HAMCP-131 and HAMCP-220 incident-log suites pass unchanged in intent;
  their former "manager role" expectations now expect the journal sources.
- Mutations: `docs/evidence/hamcp224-mutations/`. `results.json` records
  each mutant and the tests that killed it.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.36 artifacts and the known-good earlier
artifacts for recovery.

No stored format changes. **Rollback is reinstalling the 2.4.1-beta.36
artifact**, which has no `journald: true`; the Supervisor then removes both
journal mounts.

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only.

1. **The add-on starts after the update (first).** After installing
   beta.37, confirm the Engineering add-on reaches `started` and its MCP
   endpoint answers, before anything else. `journald: true` makes the
   Supervisor create the container with two bind mounts it does not check;
   Docker refuses a missing bind source. Design §0 found no such risk on
   HAOS, but this step is the proof. If the add-on does not start, stop and
   reinstall 2.4.1-beta.36.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge commit.
3. **Catalog.** `tools/list` shows 96 tools (97 with the YAML reader
   admitted).
4. **Supervisor window.** `get_incident_logs` with `sources: ["supervisor"]`
   over a recent window returns entries with `reader: host_journal`,
   `timestamp_basis.basis: journal_realtime_utc`, a `boot_ref` on each entry,
   and a source status other than `unavailable`.
5. **One named add-on.** `sources: ["addon:<an installed slug>"]` returns
   that add-on's lines. `match_rejected` stays 0 for ordinary output. A
   non-zero count, or no entries while the add-on is logging, means the
   host's Docker journald driver does not set `CONTAINER_NAME` or
   `_TRANSPORT=journal` as the fixtures assume (design §9); record it and
   stop, do not widen the match.
6. **Host and kernel.** `sources: ["host"]` and `["host:kernel"]` over a
   recent window return entries; kernel entries carry `kernel_subsystem` or
   `kernel_device` where the kernel recorded them.
7. **Withholding.** Any withheld message shows `withheld:
   credential_text_withheld` and a partial result; no credential text
   appears.
8. **journald-off reason (optional).** Only if Josh separately authorizes a
   temporary reinstall of beta.36: the same `supervisor` read there is the
   beta.36 `authority_not_granted` refusal. The beta.37 `journald_not_enabled`
   reason is proven offline (`mountinfo-off`); it cannot be produced on
   beta.37 without editing add-on metadata.

## 4. Not covered

No live canary, write, registry signing or Supervisor role change is part of
this release. A non-HAOS Supervised host with `Storage=none` or volatile-only
logging could lack `/var/log/journal` (design §0, residual risk); that host
type is outside the evidence.
