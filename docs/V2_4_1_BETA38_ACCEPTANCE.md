# Engineering 2.4.1-beta.38 acceptance

This contract covers HAMCP-224: `get_incident_logs` reads the `supervisor`,
`host`, `host:<category>` and `addon:<slug>` sources from the host journal,
which the Supervisor mounts read-only into Engineering with the new add-on
option `journald: true`. It follows 2.4.1-beta.37 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA38_RELEASE_NOTES.md`](V2_4_1_BETA38_RELEASE_NOTES.md).
- Design, owner decisions D1-D10 and the start-risk evidence (§0):
  [`HAMCP_224_HOST_JOURNAL_DESIGN.md`](HAMCP_224_HOST_JOURNAL_DESIGN.md).
- The incident-log contract this release extends:
  [`HAMCP_131_INCIDENT_LOGS.md`](HAMCP_131_INCIDENT_LOGS.md) and
  [`HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md`](HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md).

**Release target.** `2.4.1-beta.38` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.37. The run consumes the declaration. PR #311 merged first as beta.37, so this
change was restaged at beta.38 and its documents renamed.

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
- `2.4.1-beta.38` materialized in all three version authorities, with exact
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
- **Secrets.** The single `_finish` exit screens every returned journal
  field (message, `process`, `kernel_subsystem`, `kernel_device`) first,
  with the configuration-definitions screen (`credentials.screen`, labelled
  forms opted in), on the complete field before any cut. A field over
  16 KiB or bytes that are not UTF-8 are unverifiable; invisible or control
  characters, any known-secret or credential form (percent-decoded, NFKC,
  confusable-folded, normal and squashed), a credential label with `=` or
  `:`, `Bearer`, PEM private keys and the mosquitto `-P`/`--pw` options
  withhold the **whole** field. The final-envelope withheld-item scan and
  `withheld_not_searched` make any withholding partial. Then the shared
  `sanitize_log_lines` and strict `sanitize_untrusted_data` run once, as for
  every other source.
- **Order and bounds.** Host journal entries merge on the full microsecond
  realtime. Edge probes count toward the entry bound; each field read checks
  the deadline and the 4 MiB call bound first.
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
- `tests/test_pr313_independent_review.py` (the independent reviewer's
  suite, added unchanged) and `tests/test_hamcp224_review1_corrections.py`:
  every credential case of the PR #294 suites (96 cases) through the journal
  message and each returned field, all withheld and partial; the
  final-envelope scan, withheld fields and `withheld_not_searched`;
  sub-millisecond cross-source order; the reviewer's oversized probe within
  the call bound with every native step counted; an expired deadline with no
  step and no field read. The HAMCP-132 and PR #294 suites pass unchanged.
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
image identities. Retain the beta.37 artifacts and the known-good earlier
artifacts for recovery.

No stored format changes. **Rollback is reinstalling the 2.4.1-beta.37
artifact**, which has no `journald: true`; the Supervisor then removes both
journal mounts.

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only.

1. **The add-on starts after the update (first).** After installing
   beta.38, confirm the Engineering add-on reaches `started` and its MCP
   endpoint answers, before anything else. `journald: true` makes the
   Supervisor create the container with two bind mounts it does not check;
   Docker refuses a missing bind source. Design §0 found no such risk on
   HAOS, but this step is the proof. If the add-on does not start, stop and
   reinstall 2.4.1-beta.37.
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
7. **Withholding.** Any withheld message or field shows `withheld:
   credential_text_withheld` (or `unverifiable_text_withheld`), the omission
   `withheld:entries[n]` and a partial result; no credential text appears.
8. **journald-off reason (optional).** Only if Josh separately authorizes a
   temporary reinstall of beta.37: the same `supervisor` read there is the
   beta.37 `authority_not_granted` refusal. The beta.38 `journald_not_enabled`
   reason is proven offline (`mountinfo-off`); it cannot be produced on
   beta.38 without editing add-on metadata.

## 4. Not covered

No live canary, write, registry signing or Supervisor role change is part of
this release. A non-HAOS Supervised host with `Storage=none` or volatile-only
logging could lack `/var/log/journal` (design §0, residual risk); that host
type is outside the evidence.
