# HAMCP-224 host journal sources for `get_incident_logs` (journald option C) — design

Status: **Part 2. Josh's decisions are recorded in §0.** This document does
not authorize a `Dockerfile` change, deployment, or live Home Assistant access.

## 0. Owner decisions and start-risk evidence (Part 2)

**Decisions (Josh, 2026-10-25): D1–D10 all as recommended in §10.**

| # | Decision |
| --- | --- |
| D1 | Bind with `ctypes` to the base image's existing `libsystemd0` |
| D2 | Read in an in-add-on worker thread |
| D3 | `host` (all host logs) and the `host:kernel`, `host:usb`, `host:network`, `host:storage`, `host:os` categories |
| D4 | Any add-on slug |
| D5 | `core` stays on its current `/core/logs` route |
| D6 | The §6 bounds |
| D7 | Withhold the whole credential-looking message; the result is partial |
| D8 | Return the reboot marker (`boot_ref`) and the kernel device fields |
| D9 | **Authorized:** `journald: true` in `hass_mcp_engineering_beta/config.yaml`; `hassio_role` stays `homeassistant` |
| D10 | Generated real-format journal fixtures |

**Host check: inconclusive.** Josh ran it from the Terminal & SSH add-on,
whose container has no journal mount, so both folders were reported missing
there, as expected. The "a missing journal folder prevents the add-on from
starting" risk (§9) was therefore settled from source and precedent instead.
Josh's instruction was to stop if the evidence shows a real start risk on HAOS.
It does not, so Part 2 proceeds.

- **(a) Supervisor.** `journald: true` adds both bind mounts
  **unconditionally**: no existence check, no `CreateMountpoint` (SV
  `supervisor/docker/app.py:650-665`; SVL `:669-683` is identical). They go
  through the Docker Mounts API (`docker/manager.py:399-400`), which refuses a
  missing bind source when the container is created (MB
  `daemon/volume/mounts/linux_parser.go:83-92`, `ParseMountSpec` →
  `validateBindSourceExists=true`, `:315-320`). So the risk is real only if a
  host can lack `/var/log/journal` or `/run/log/journal`.
- **(b) HAOS and systemd.**
  - `/var/log/journal` is the HAOS bind of `/mnt/data/logs`. It is ordered
    before the journal flush (OS
    `rootfs-overlay/usr/lib/systemd/system/var-log-journal.mount:1-14`). The
    Supervisor and its data also live on `/mnt/data`.
  - `/run` is a fresh tmpfs every boot, so journald's flushed flag
    (`/run/systemd/journal/flushed`, SD `journald-server.c:324-336`) is
    absent at boot. journald therefore opens the runtime journal first and
    creates its parents: `mkdir_parents("/run/log/journal/<machine-id>")`
    (SD `journald-server.c:405-415`, path `:2843`). That creates
    `/run/log/journal`.
  - The flush removes only `/run/log/journal/<machine-id>` (`rm_rf(...,
    REMOVE_ROOT)` on `runtime_storage.path`, plus the runtime journals' own
    `<machine-id>` directories, SD `journald-server.c:1420-1450`). It never
    removes `/run/log/journal`. systemd-tmpfiles only adjusts it (`z`, SD
    `tmpfiles.d/systemd.conf.in:24`).
  - HAOS sets `Storage=auto` (not `none`), unchanged from OS 17.2 to 18.3
    (OS `rootfs-overlay/etc/systemd/journald.conf:1-4`).
- **(c) Precedent.** Published, maintained add-ons that ship `journald: true`
  (each read at an exact commit, SHA-256 of the file in brackets):
  - Advanced SSH & Web Terminal 24.2.0, community add-ons
    (`hassio-addons/repository` `282dcf9b`, `ssh/config.yaml:55`
    [`316e24ba…9744`]; source `hassio-addons/app-ssh` `e35d13ca`);
  - Grafana Cloud 0.3.0, Grafana Labs (`grafana/home-assistant-addons`
    `b29794a1`, `grafana_cloud/config.yaml:24` [`d0a34f10…9e32`]);
  - CrowdSec 1.8.0 (`crowdsecurity/home-assistant-addons` `088bb75e`,
    `crowdsec/config.yaml:66` [`a8601ce4…42e6`]);
  - Promtail (`mdegat01/addon-promtail` `ac4976b3`, 2022; the oldest, so it
    predates the current Supervisor and counts least).

**Conclusion: no start risk on HAOS** from the journal folders. Residual
risk, disclosed: a non-HAOS (Supervised) host with `Storage=none` or
volatile-only logging could lack `/var/log/journal`. That is not Josh's
install type, and it is outside what the evidence covers. The acceptance
document's **first** step is still to confirm the add-on starts after the
update. Rollback: reinstall `2.4.1-beta.37`, which has no `journald: true`.

**Implementation notes (Part 2, refining §4–§7 without changing a decision):**

- **Continuation position.** The signed cursor is signed but not encrypted
  (`incident_logs/cursor.py:1-9`). libsystemd's cursor string embeds the raw
  boot ID (`b=`), which D8 keeps out of every output. So a journal source's
  position is carried as its realtime plus a **keyed digest of the libsystemd
  cursor string**, never the string itself. A continuation seeks that realtime,
  then finds the entry whose cursor digest matches among the entries at that
  realtime. If none does, the result is `STALE_CURSOR` /
  `journal_continuity_broken`, as §5 required. So `sd_journal_seek_cursor` and
  `sd_journal_test_cursor` are **not** bound.
- **Exact matches only.** The `host:os` category uses the exact `haos-*`
  identifiers in the pinned 52-identifier set, not a wildcard. Every yielded
  entry is also checked again in Python against its source's match. An entry
  that fails the check is never returned and is counted (`match_rejected`).
- **Correction.** Part 1 said the Supervisor's default host set has 54
  identifiers. The file is 54 lines; it holds **52** identifiers. The count is
  corrected throughout.
- **One merged handle.** The reader opens the container root once
  (`O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`) and passes it with
  `SD_JOURNAL_OS_ROOT`. libsystemd then reads `run/log/journal` and
  `var/log/journal` beneath it, in one merged order (systemd v257.13,
  `70b5d110`, `src/libsystemd/sd-journal/sd-journal.c:2206-2224,2448-2477`),
  and sets the persistent and runtime file flags from those paths
  (`:1568-1574`). It also reads `var/log/journal/remote` if it exists
  (`:2220-2221`). That is host journal data under the same read-only mount,
  and every entry still passes the source match. No inotify watch is created,
  because `sd_journal_get_fd` is not bound.
- **Field size.** In 257.13 `sd_journal_get_data` returns the whole payload;
  the threshold does not shorten it (`journal-file.c:1926-1975`). So the
  reader copies at most 16 KiB of each field itself, and marks the message
  `message_truncated`. `sd_journal_set_data_threshold` is not bound.
- **Two more unavailability reasons** beyond the §8 table:
  `journal_mount_incomplete` (only one of the two journal paths is mounted)
  and `journal_read_failed` (the reader raised, or a libsystemd call failed
  mid-read). Both are `unavailable`, never `complete`. A source that fails on
  one page stays `failed_in_earlier_page` on every later page.
- **Three raw opens, not two (§11).** The reader opens the mount table, the
  container root, and each journal directory (to refuse a symlink with
  `O_NOFOLLOW` before libsystemd reads beneath the root). The static check pins
  exactly these.
- **Withholding scope (§7 step 2).** Whole-message withholding covers the
  HAMCP-132 `credential_text` forms and the journal key extension, as §7
  lists. Other token forms that `credential_text` does not flag (for example
  `Authorization: Bearer ...`, a JWT, `api_key=...`) are redacted in place by
  the shared `sanitize_log_lines` at the same exit (`redacted_by_policy`, the
  result is partial). A test pins that layer for journal lines.
- **Capability text.** Routing, provider and policy are unchanged (§11), but
  the capability matrix's `get_incident_logs` justification text no longer
  says these sources need role `manager`; it describes the journal read.

**Review 1 corrections (architect direction on the PR #313 independent
review, H1/H2 and M1–M3).** These supersede the "Withholding scope" and
"Field size" notes above and §7 steps 1–2.

- **One screen (H1/H2).** The journal keeps no credential screen of its own.
  Every returned journal field — the message, `process`
  (`SYSLOG_IDENTIFIER`), `kernel_subsystem` and `kernel_device` — goes
  through `configuration_definitions/credentials.py` `screen`, the same
  decision `withhold` applies to the configuration-definitions report. The
  order, on each field:
  1. the complete field as read. A field over the 16 KiB read bound was never
     read whole, so it is withheld whole as `[unverifiable_text]`; nothing cut
     is ever screened;
  2. strict UTF-8 (invalid bytes are withheld whole as unverifiable), and
     complete terminal escape sequences removed (review 2, narrowed in
     review 3: SGR colour codes only, and the original screened too);
  3. `unverifiable_text`: invisible, format or control characters (tab, LF
     and CR only as HAMCP-132 allows them) withhold the field whole;
  4. `credential_text`: bounded percent-decoding and NFKC (`canonical`), the
     confusable fold, then the URI, user-info, `password=` and known-secret
     forms on both the text and its squashed form;
  5. the journal opts in to the **labelled forms** (`labelled=True`), read in
     the same canonical, folded and squashed forms: a credential label
     (`password`, `pass`, `pwd`, `pw`, `psk`, `secret`, `token`, `api_key`,
     `access_key`, `private_key`, `network_key`, `encryption_key`,
     `credential(s)`, `authorization`, with any `word_` prefix) followed by
     `=` or `:`; a `Bearer` token; a PEM private-key header; mosquitto `-P`
     and `--pw`;
  6. any hit withholds the whole field; the message is cut to 2000
     characters for display only after it is cleared.

  The configuration-definitions report does **not** opt in to the labelled
  forms, so its behavior is unchanged (its suites pass unchanged). A
  labelled form such as `api%5fkey=` in a configuration definition is
  therefore still decided by HAMCP-132's own forms; extending that report
  is a separate decision (suggested follow-up).
- **One-token `user:pass@host` (HAMCP-234 step 2, 2.4.1-beta.43; supersedes
  step 4 for the journal).** Owner decision (Josh, 2026-10-27), from the
  beta.42 live calibration of `addon:core_matter_server` (37 of 40 lines
  withheld, every one by `user_pass_host.squashed`). The journal reads
  `user_pass_host` in the normal form only, as one contiguous token (no
  whitespace between the user, `:`, the password, `@` and the host). It no
  longer reads it in the squashed form, where a timestamp's `:` and a later
  `@peer` joined into the shape. It is an option of the same shared screen
  (`contiguous_user_pass_host=True`, in `host_journal._JOURNAL_SCREEN`), not
  a fork. Every other rule is unchanged in both forms. The definitions and
  trace paths do not opt in, the trace identity and name fields
  included. Owner clarification (Josh, 2026-10-28, review of aaf1fe8e):
  the token is also judged on the text as given and after each round of
  its percent-decode (each normalized and folded as the normal form is),
  and any match withholds, so decoding `%20`, `%09` or `%0A` never splits
  a token that was whole as written (a proxy setting
  `user:pass%20more@proxy:8080`). Correction D320-H1 (Josh, 2026-10-28):
  the tokens are taken from the raw text, split at its literal whitespace,
  and each is decoded on its own with decoded whitespace kept inside it, so
  an encoded `:` or `@` decoding in the same round as `%20` is withheld too.
  The disclosed residual, accepted by Josh 2026-10-28, is that a
  scheme-less `user:pass@host` split by literal whitespace in the raw text
  is shown in the journal unless another rule meets it.
- **Fail-closed consequences, disclosed.** A `Bearer` token and an
  `api_key=` value are now withheld whole instead of redacted in place.
  Ordinary text with a label followed by `:` or `=` (`token: refreshed`,
  `pass=1`) is withheld too, as is text that literally contains the
  withheld-marker text. These are false positives, never leaks.
- **Completeness (M1).** The configuration-definitions final-envelope scan
  (`credentials.withheld_items`) runs on every journal result: each entry
  carrying a withheld field is named (`withheld:entries[n]`, at most 20,
  then `withheld_items_not_listed:<n>`). A withheld returned field counts in
  the source row's `withheld_fields`. A withheld entry that a text filter
  left out (withheld text is never searched, so the filter cannot tell)
  counts in `withheld_not_searched` and adds `withheld_not_searched:<source>`.
  Any of these makes the source and the result `partial`.
- **Microsecond order (M2).** Journal entries merge on the full-precision
  microsecond `__REALTIME_TIMESTAMP` (a source's running maximum, so a clock
  step never reorders it); a gateway entry's key is its millisecond time ×
  1000. Ties go to the earlier requested source, then the source's own
  order. The continuation keeps the microsecond key. `ts` still shows
  milliseconds, so two entries in the same millisecond can show the same
  `ts`; they are returned in their true order.
- **Shared bounds on every path (M3).** Edge probes (the head and tail
  checks) take the same step bound as the scan and are counted (`probed`,
  included in `entries_scanned`). Each field read checks the deadline before
  `sd_journal_get_data` and the 4 MiB call field-byte bound before the copy,
  so no path — edge, resume or scan — reads past either. The reviewer's
  oversized probe now stops at 4,192,200 of 4,194,304 bytes, with every
  native step counted.

**Review 2 corrections (architect direction on the PR #313 delta review,
H2 residual, M4 and M1 residual).** These supersede steps 2 and 5 above and
the "Fail-closed consequences" note where they differ.

- **The label withholds (H2 residual).** Matching value shapes let quoted,
  argv and split forms through, so the journal's labelled mode now keys on
  the label. After the shared normalization (bounded decode, NFKC, the
  confusable fold, case-folding, Unicode dashes folded to `-`), in the
  normal **or** the squashed form, a strong keyword withholds the whole
  field: `bearer`, `password`, `passwd`, `pwd`, `--pw`, `authorization`,
  `api key`/`apikey`/`api_key`, `secret`, `token`, `private key` (a PEM
  header in any split or spacing). The mosquitto `-P` option is decided by
  `credentials.password_option`, a bounded tokenizer: quotes and
  backslashes are transparent, whitespace and `,;:=[](){}<>|&` separate
  tokens; a token starting with `-P` is the option (`-P x`, `-Px`, `-P'x'`,
  `-P"x"`, `"-P" x`, `'-P' x`, `-P= x`, a Python or JSON argv), and so is a
  lone `-` followed by the token `P`, or by a token starting with `P` after
  anything but plain spaces (`-<tab>P`, `-<newline>P`). Over 65,536
  characters it fails closed. The value shapes stay as a second layer
  (`credentials._value_shape`, tested on its own because the keywords
  overlap it); the
  configuration-definitions (HAMCP-132) default mode is unchanged.
- **False positives, measured.** On the repository's log-shaped fixtures
  (the 32 ordinary journal fixture lines, the 35 HAMCP-191 ordinary
  samples, 164 log-shaped lines of the HAMCP-131 and HAMCP-220 suites; 231
  lines, all synthetic or generated, since the repository holds no
  captured add-on logs) the `-P` rule withholds none and the keyword rule
  withholds 3 (1.3%), all keyword-only HAMCP-191 samples:
  `reset your password in the companion app`, `token bucket refill rate`
  and a line with `password was **REDACTED**`. Known ordinary lines now
  withheld: a real `-P` of another tool (`iptables -P`, `grep -P`,
  `rsync -P`, `xargs -P`) and any line naming a keyword (`token
  refreshed`, `Authorization header present`). Lower-case `-p` (`ssh -p`,
  `mkdir -p`, `docker run -p`), `-avP`, `Kitchen - Pool` and
  `X-Powered-By` stay shown.
- **ANSI, two forms (M4, owner decision).** A well-formed CSI sequence
  (`ESC [`, at most 32 parameter and 4 intermediate bytes, one final byte;
  SGR colours included) is a recognized display format. The field is
  screened twice and either withholds: the text as shown, with every such
  sequence removed (a credential split by a colour code is seen whole), and
  the original with only each sequence's `ESC` removed (a label spelled by
  the sequence's own final byte, `ESC[password=`, is seen). A CSI sequence
  alone never withholds; the shown text carries no escape. Any other
  escape (OSC, a two-character escape, a stray or over-long one, C1 CSI) or
  control character stays in both forms and is withheld as unverifiable.
  The shared screen itself still refuses raw escapes. The reviewer's
  `test_original_control_text_is_screened_before_ansi_removal` expects a
  coloured ordinary line to be withheld; it is superseded by this owner
  decision and runs as a registered expected failure. Review 3 narrowed
  this decision to SGR colour codes (below).
- **Level-filtered withholding (M1 residual).** `min_level` filters on the
  level from the structured journal `PRIORITY` field
  (`incident_logs/service.py` `_journal_views`,
  `hj.LEVELS_BY_PRIORITY.get(record.priority)`), never on message text. A
  withheld entry the level filter leaves out still counts, in the source
  row's `withheld_level_filtered`, and adds
  `withheld_level_filtered:<source>`, so the source and the result are
  `partial`.

**Review 3 correction (architect direction on the PR #313 review 3, H3,
owner-approved).** This supersedes the M4 bullet above where it differs.

- **ANSI, SGR colour codes only (M4 narrowed).** Removing every well-formed
  CSI sequence let two sequences hide a label from both screened forms:
  `ESC[p` takes the `p` of `password` as its final byte, and a colour code
  inside the rest of the word is removed as well, so neither form held the
  label (`ESC[pass ESC[32m word=`, `ESC[Be ESC[0m arer`,
  `ESC[1;To ESC[31m ken=`, spaces added here for reading). The one recognized
  display format is now an SGR colour code: `ESC [`, at most 32 digits or
  semicolons, then `m` (`host_journal._SGR`). Its bytes are never a letter
  but its final `m`, and no credential label holds a digit, a `;` or an `m`,
  so removing it cannot take a label character with it. SGR codes are
  removed in one pass; a code assembled by removing another is a stray
  escape. Every other escape (a CSI with any other final byte, cursor
  movement, erase, OSC, a two-character escape, a stray, malformed or
  over-long escape, C1 CSI) or control character is never removed: it
  withholds the field whole as unverifiable (partial). Both forms are still
  screened and either withholds: the SGR-stripped text as shown, and the
  original with only each SGR code's `ESC` removed. Ordinary lines carrying
  cursor or erase sequences (for example `ESC[2K`) are now withheld.
- **Residual (disclosed, unchanged).** A known secret holding `[`, digits,
  `;` or `m` is matched in the original form. A writer that knows a secret
  holding digits, `;` or `m` can spell those characters as an SGR code's
  own bytes: for a known secret `x1mY`, `x ESC[1m Y` is shown as `xY`, the
  secret less those characters, and neither form matches it. This was
  already so with the review 2 CSI rule, needs a writer that knows the
  secret, shows no complete secret, and is outside the H3 direction.
- **The superseded reviewer method.** It uses only `ESC[31m` and `ESC[0m`,
  still fails only because coloured ordinary lines are shown, and stays the
  single registered expected failure across the three reviewer suites
  (`tests/test_hamcp224_review3_corrections.py` checks both).

Base: `main` at `5030982fe681737c47e52a7c01d1808fca52468b` (Engineering
`2.4.1-beta.37`, 68 static tools, `hassio_role: homeassistant`). Engineering
paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless
they start at the repository root.

**Owner direction (Josh, HAMCP-218 → HAMCP-224).** Use option C (`journald:
true`) so Engineering can diagnose add-on and host problems from their logs.
Examples are Govee, MQTT, Matter and USB dongle drop-outs. The access is
read-only:

- no Supervisor `manager` role;
- no Supervisor API access beyond what exists today;
- no registry signing.

## 1. Summary

- `journald: true` makes the Supervisor bind-mount the host's persistent and
  volatile journal directories into the container, **read-only**. It adds no
  API authority and does not change the security rating. The owner is shown
  nothing about it (§2).
- On HAOS the journal is persistent on the data partition, capped at 500 MB.
  Every container logs through Docker's `journald` driver, tagged with the
  container name. That gives one reliable mapping (§3):
  - add-on `<slug>` → `SYSLOG_IDENTIFIER` `app_<slug>` or legacy
    `addon_<slug>`;
  - Supervisor → `hassio_supervisor`;
  - Core → `homeassistant`;
  - kernel → `_TRANSPORT=kernel`.
- **Recommended reader: none of the brief's options (a)–(c).** Instead, a
  stdlib `ctypes` binding to the `libsystemd0` 257.13 **already in the pinned
  base image** (option d, §4). It needs no new OS package, no new wheel, no
  Dockerfile change and no workflow change. It uses the reference
  implementation, which already handles:
  - compact and keyed-hash files;
  - files that journald is still writing;
  - rotation;
  - seeking by exact realtime.

  Option (a) breaks the build policy and a test. Option (b) cannot be
  installed under the policy. Option (c) is feasible but has the highest
  correctness and maintenance risk.
- **Time basis:** every entry's own `__REALTIME_TIMESTAMP`, through
  `sd_journal_get_realtime_usec`. No text is parsed. This is stronger than
  today's Supervisor-route basis (HAMCP-220), where only each page's first
  entry has exact UTC (§5).
- **Secrets:** fields are read only by name, from a fixed allowlist. No API
  that enumerates fields is ever bound. Every message passes through the
  existing single exit point. A message that the HAMCP-132 credential
  classifier flags is withheld whole, with a journal-specific key extension.
  Any withheld marker makes the result partial (§7).
- Decisions for Josh: §10.

## 2. What `journald: true` provides (question 1)

Pins and abbreviations:

| Abbreviation | Repository | Ref | Commit |
| --- | --- | --- | --- |
| SV | home-assistant/supervisor (project pin, as HAMCP-131/196/218) | `2026.09.1` | `40e3ee7640a3c44abe67f1a1397f39c3cd949806` |
| SVL | home-assistant/supervisor (latest stable, drift check) | `2026.10.4` | `d92eb0e373a304c1e6a66dcef66585854a2002d1` |
| OS | home-assistant/operating-system (latest stable) | `18.3` | `b22f929d3bc8895f5cf84d13d71a24714b59eed4` |
| OS-old | home-assistant/operating-system (drift check) | `17.2` | `41425d0c0979a98307e3dc76067558102bb5633a` |
| BR | home-assistant/buildroot (OS 18.3 submodule) | — | `d2b75e0548bf1f0c56d17cac44d5fe9a0e17e60a` |
| SD | systemd/systemd (the version HAOS ships, `BR package/systemd/systemd.mk:22-23`) | `v256.17` | `009adf6c0e435376c80fbc11675d581e0a94d350` |
| MB | moby/moby (Docker shipped by OS 18.3, `BR package/docker-engine/docker-engine.mk:7-8`) | `docker-v29.7.2` | `6a43e3d5afddf4111da0f864bbc7cae5d7e95001` |
| FE | home-assistant/frontend | `20260930.5` | `901c0a61ad44c7f52f50a986f6b5a81371f97a0a` |
| IMG | Engineering base `python:3.12-slim@sha256:78387bc3…184ea` (`hass_mcp_engineering_beta/Dockerfile:1,10`) | Debian 13.6 "trixie", Python 3.12.14 | inspected read-only with `--network=none` |

The deployed HAOS and Supervisor versions are **unknown**. No live system was
accessed. Drift between OS 17.2 and 18.3, and between SV and SVL, is noted
where it matters. None of the facts below changed between them.

| Fact | Evidence |
| --- | --- |
| Option `journald`, boolean, default false | SV `supervisor/apps/validate.py:553`; accessor `with_journald` `supervisor/apps/model.py:664-666` |
| Host paths: persistent `/var/log/journal`, volatile `/run/log/journal` | SV `supervisor/const.py:51-52` |
| Both paths are bind-mounted to the **same path** in the container, `read_only=True`, through the Docker Mounts API | SV `supervisor/docker/app.py:650-665`; `docker/manager.py:399-400`; `docker/const.py:121-131` |
| A missing host source path makes Docker refuse the container (no `CreateMountpoint`) | MB `daemon/volume/mounts/linux_parser.go:83-92,315-316`. On HAOS both exist: `/var/log/journal` is a bind of `/mnt/data/logs` (OS `buildroot-external/rootfs-overlay/usr/lib/systemd/system/var-log-journal.mount:7-11`). journald removes only `/run/log/journal/<machine-id>` after flushing, not the directory (SD `src/journal/journald-server.c:1430,2843`) |
| Persistent vs volatile: HAOS `Storage=auto`, so the journal is persistent because `/var/log/journal` exists. `Compress=yes`, `SystemMaxUse=500M`, no drop-ins | OS `buildroot-external/rootfs-overlay/etc/systemd/journald.conf:1-4`; SD `man/journald.conf.xml:72-84` |
| File mode 0640; directory `2755 root:systemd-journal` | SD `journald-server.c:296,307,678`; `tmpfiles.d/systemd.conf.in:39-40`. The ACL lines need `HAVE_ACL`; ACL is not selectable from the HAOS defconfigs (inference, see §9) |
| Root in the container can read them: Docker's default capabilities include `CAP_DAC_OVERRIDE` | MB `daemon/pkg/oci/caps/defaults.go:7` |
| AppArmor does not restrict the read. Engineering has no `apparmor.txt`, so it gets `SECURITY_DEFAULT`; the Supervisor sets no profile, and docker-default allows `file,` | SV `apps/model.py:377-383`; `docker/app.py:317-324`; MB `vendor/github.com/moby/profiles/apparmor/template.go:29-73` |
| journald's gateway socket is **not** exposed to add-ons. It is mounted only into the Supervisor container | OS `rootfs-overlay/usr/lib/systemd/system/systemd-journal-gatewayd.socket:15`; `rootfs-overlay/usr/sbin/haos-supervisor:110-111` |
| Journal format on HAOS: systemd 256.17. New files are keyed-hash and compact by default. Compression is chosen at build time. zstd, lz4 and xz are enabled only when their buildroot packages are selected, and none is reachable from the OS 18.3 defconfigs. So HAOS files are **probably uncompressed and unsealed** (inference, §9) | SD `src/libsystemd/sd-journal/journal-file.c:317-349,380-414`; BR `package/systemd/systemd.mk:215-253` |

Read-only-ness is enforced by the kernel bind mount, so it does not depend on
the reader. The reader is also read-only by construction: libsystemd opens
every journal file `O_RDONLY` (SD `sd-journal.c:1530-1532,1585`).

## 3. What is in it, and the identifiers (question 2)

| Producer | How it reaches the journal | Identifier to match | Evidence |
| --- | --- | --- | --- |
| Every container | Docker `log-driver: journald`, `log-opts.tag: "{{.Name}}"` | `SYSLOG_IDENTIFIER` = container name (Docker strips the leading `/`) | OS `rootfs-overlay/etc/docker/daemon.json:1-9`; MB `daemon/logger/journald/journald.go:103-120`, `daemon/logger/loginfo.go:142-143`. The Supervisor flags the system unsupported if the driver is not journald (SV `resolution/evaluations/docker_configuration.py:10,52`) |
| Add-on `<slug>` | Container `app_<slug>`. A legacy `addon_<slug>` is renamed in place | `SYSLOG_IDENTIFIER` ∈ {`addon_<slug>`, `app_<slug>`}, exactly as the Supervisor's own add-on log route does | SV `docker/app.py:100-102,139-170`; `api/__init__.py:706-713` (SVL :756-758) |
| Supervisor | Container `hassio_supervisor` | `SYSLOG_IDENTIFIER=hassio_supervisor` | SV `const.py:17,22`; `api/__init__.py:580-583` |
| Home Assistant Core | Container `homeassistant` | `SYSLOG_IDENTIFIER=homeassistant` | SV `docker/homeassistant.py:39`; `api/__init__.py:648` |
| Supervisor plugins | Containers | `hassio_dns`, `hassio_audio`, `hassio_multicast` | SV `api/__init__.py:441,947,977` |
| Kernel (USB, disk, block, network drivers) | kmsg | `_TRANSPORT=kernel`. `SYSLOG_IDENTIFIER=kernel` is set when the facility is KERN. Device fields: `_KERNEL_SUBSYSTEM`, `_KERNEL_DEVICE`, `_UDEV_SYSNAME`, `_UDEV_DEVNODE` | SD `src/journal/journald-kmsg.c:200-232,260,271` |
| USB disconnect and reconnect (example) | kernel `dev_info` | "USB disconnect, device number %d"; "%s %s USB device number %d using %s" | gregkh/linux `v6.18.52` (`8f3741e6`) `drivers/usb/core/hub.c:2331,5000-5002`; kernel 6.18.52 per OS `buildroot-external/configs/generic_x86_64_defconfig:21` |
| Host services | native, syslog or stdout | the Supervisor's default host set: 52 identifiers, including `NetworkManager`, `wpa_supplicant`, `systemd-udevd`, `dockerd`, `containerd`, `rauc`, `os-agent`, `haos-*`, `systemd-fsck`, `fstrim`, `systemd-timesyncd`, `kernel` | SV `supervisor/data/syslog-identifiers.json:1-54` (byte-identical in SVL) |

Fields the Docker driver writes on every container entry (MB `journald.go`):

- `CONTAINER_ID`, `CONTAINER_ID_FULL`, `CONTAINER_NAME`, `CONTAINER_TAG`,
  `IMAGE_NAME`, `SYSLOG_IDENTIFIER`, `CONTAINER_LOG_EPOCH` (:112-120);
- `SYSLOG_TIMESTAMP`, Docker's time as RFC 3339 text (:156-158);
- the `CONTAINER_PARTIAL_*` fields for lines over Docker's buffer (:159-166);
- `CONTAINER_LOG_ORDINAL` (:168-169);
- `PRIORITY`: stderr is 3 (err), stdout is 6 (info) (:171-174).

**Proposed slug mapping.** A match requires all three of:

- `SYSLOG_IDENTIFIER` = `app_<slug>` or `addon_<slug>`;
- `CONTAINER_NAME` equal to that same value;
- `_TRANSPORT=journal`.

Fields that start with `_` are set by journald, not by the sender.
`CONTAINER_NAME` is set by the Docker driver. Requiring both ties the entry to
a Docker container log, not to any process that chose that identifier. Add-ons
cannot write to the host journal directly: the mount holds journal files, not
journald's socket. Their own output reaches the journal only as message text.

**Unverified on the device:** that `CONTAINER_NAME` and `_TRANSPORT` appear on
real HAOS entries exactly as above. Part 2 checks this in the acceptance
reads. If they do not appear, the match falls back to the Supervisor's own
rule, `SYSLOG_IDENTIFIER` only. The live result is reported either way.

The slug must pass the existing token rules: `[a-z0-9][a-z0-9_-]{0,63}` and
the HAMCP-200 `path_segment` guard (`incident_logs/contracts.py:105-106`).
Third-party store slugs carry a repository hash prefix, for example
`<hash>_zigbee2mqtt`. They must therefore be given exactly; Engineering does
not guess slugs.

Under Docker 29, a running container renamed from `addon_` to `app_` probably
keeps logging under the old tag until it restarts. The tag is computed when
the logger is created (MB `journald.go:103-120`). This is inference, and is
why both names are matched.

## 4. How to read it (question 3)

Constraints in force today:

- Wheel-only, hashed and index-free installs (`hass_mcp_engineering_beta/Dockerfile:3-7,30-33`).
- No apt resolution beyond the pinned base (`docs/BUILD_INPUTS.md:17-18,32-35`).
- `tests/test_build_inputs.py:144-152` forbids `apt-get` in the Dockerfile.
- The runtime has no `subprocess` use.
- The incident-logs static check bans bare `open` and file reads in
  `incident_logs/` (`tests/test_hamcp131_incident_logs.py:773-796`).

| | (a) `journalctl` binary | (b) systemd Python bindings | (c) pure-Python reader | **(d) `ctypes` over the base image's `libsystemd0`** | (e) `cysystemd` wheel |
| --- | --- | --- | --- | --- | --- |
| What | Debian trixie `systemd` 257.13-1~deb13u1 (Installed-Size 9944 KiB, pre-depends `libsystemd-shared` 6825 KiB, plus 9 more dependencies) | `systemd-python` 235 (2023) | Our own parser of the journal file format | stdlib `ctypes` over `libsystemd.so.0` 257.13-1~deb13u1, already in IMG (links only libc and libcap; libzstd 1.5.7, liblz4 1.10.0 and liblzma 5.8.1 present and loaded on demand) | `cysystemd` 2.0.5 manylinux_2_34 wheels |
| Build policy | **Breaks it:** apt in the Dockerfile fails `test_build_inputs.py:150`, and the apt resolution is unpinned unless pinned to snapshot.debian.org | **Cannot install:** sdist only; needs a C toolchain and `libsystemd-dev` against `--only-binary=:all:` | No change | **No change:** no package, wheel, Dockerfile or workflow edit | New hashed wheel on two architectures. It bundles AlmaLinux 9 systemd-libs 252, zstd, lz4, xz, libgcrypt and libcap: a second, vendored systemd |
| Runtime surface | Exec of an external binary (new `subprocess` path); output parsing | In-process C extension | Python only; full parser written by us | In-process native library. Our risk is the binding: wrong signatures or lifetimes can crash the process | In-process C extension plus vendored native libraries |
| Format coverage | Full | Full | Must implement header validation, compact and regular items, entry-array chains, files still being written, rotation and multi-file merge. Compressed objects would need zstd or lz4 decoders (Python 3.12 has neither in stdlib; only xz) | Full: compact needs ≥ 252, keyed hash and zstd ≥ 246 (SD `NEWS:3542-3548,7251-7259`); 257 ≥ 256 (HAOS writer) | Full at 252, provided that RHEL backport handles HAOS 256 files |
| Time seek and cursors | `--since`, `--cursor` | native | Write bisection over entry arrays | native `sd_journal_seek_realtime_usec`, `sd_journal_seek_cursor`, `sd_journal_test_cursor` | native |
| Supply chain | Moderate size; Debian-signed, but unpinned unless pinned to a snapshot | — | Nothing new | Nothing new. Already covered by the pinned base digest. Part 2 adds a build smoke that pins the exact package version and symbols | A single-maintainer project vendoring a crypto library |
| Maintenance | Low | — | **High**: format changes (new incompatible flags) need our code | Low. The `sd_journal_*` API is stable (since 2012); a base bump is a reviewed build-inputs change | Medium |

**Recommendation: (d).** It reads with the same library that journald's own
tools use. It satisfies every existing build rule, and the image already
contains it. Part 2 hardening of (d):

- **Binding scope.** One new module binds an explicit symbol allowlist, and
  the static test pins it:
  - `sd_journal_open_directory_fd`, `sd_journal_close`;
  - `sd_journal_add_match`, `sd_journal_add_disjunction`, `sd_journal_add_conjunction`, `sd_journal_flush_matches`;
  - `sd_journal_seek_realtime_usec`, `sd_journal_seek_cursor`, `sd_journal_seek_head`, `sd_journal_seek_tail`;
  - `sd_journal_next`, `sd_journal_previous`;
  - `sd_journal_get_realtime_usec`, `sd_journal_get_cursor`, `sd_journal_test_cursor`, `sd_journal_get_data`;
  - `sd_journal_set_data_threshold`, `sd_journal_get_cutoff_realtime_usec`;
  - `sd_journal_has_persistent_files`, `sd_journal_has_runtime_files`;
  - `free` from libc.

  The static test also asserts that `sd_journal_enumerate_data`,
  `sd_journal_enumerate_fields`, `sd_journal_query_unique` and every write or
  send symbol are **not** bound. That makes the field allowlist (§7)
  structural.
- **Path safety (HAMCP-200 style).**
  - Each of the two fixed paths is opened with
    `os.open(path, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)`. No caller value
    ever reaches a path.
  - The descriptor is handed to `sd_journal_open_directory_fd` with flags 0.
    libsystemd then opens files with `openat` on that descriptor (SD
    `sd-journal.c:1530`, `2382-2419`) and reads only `*.journal` and
    `*.journal~` files, plus 128-bit-ID subdirectories at the root
    (`:1800-1815,1940-1960`).
  - That subdirectory rule admits `DT_LNK`. Symlinks are host-owned, on a
    read-only mount, and only journal files are parsed. A fixture covers it.
- **Execution.**
  - Each call opens, reads and closes its handles in **one worker thread**
    (`asyncio.to_thread`). libsystemd objects are bound to the thread and
    process that created them (`journal_origin_changed`, SD
    `sd-journal.c:233`).
  - The existing deadlines are checked between `sd_journal_next` steps.
  - Each value is capped by `sd_journal_set_data_threshold`.
  - A reader subprocess is the alternative (D2).
- **Build pin.** `build_inputs.py smoke` (not a protected path) asserts:
  - `libsystemd0` is the exact recorded version in dpkg status, on both
    architectures;
  - every allowlisted symbol resolves;
  - a read of an empty directory descriptor returns no files.

  `build-inputs.json` records the version. A base-image bump that changes it
  fails the build until reviewed. The CI `validate_packaging` job already runs
  smoke on amd64 and arm64 (`.github/workflows/ci.yml:271-339`), so **no
  workflow change** is needed.
- **SBOM and provenance.** Unchanged. `libsystemd0` is part of the base image
  that `sbom: true` already describes (`publish-rc-image.yml:996-997`).
  `verify_sbom_inventory` compares only PyPI purls
  (`scripts/verify_publication_source_image.py:317-332`), so it is unaffected.
  The base package is recorded in `build-inputs.json`, not in the hashed
  Python locks, which stay unchanged.

If Josh rejects in-process native code, the fallback is (c), restricted to
what HAOS writes. Files with a compression flag, or objects marked compressed,
would be reported `journal_format_unsupported` instead of decoded. That keeps
"no new dependency" but carries the maintenance risk above.

## 5. Timestamps, ordering, cursors and window edges (question 4)

- **Basis.** `__REALTIME_TIMESTAMP` is `CLOCK_REALTIME` in microseconds, taken
  when journald received the entry (SD
  `man/systemd.journal-fields.xml:274-280,635-645`; `journald-server.c:1202-1206`).
  It is UTC by definition, so the HAMCP-220 time-zone binding is not needed
  for these sources. It is read with `sd_journal_get_realtime_usec` per entry.
  - Every entry gets `ts_basis: journal_realtime_utc`. No `uncertain`
    candidates exist for journal sources.
  - `SYSLOG_TIMESTAMP`, `_SOURCE_REALTIME_TIMESTAMP` and any time in the
    message text are **never read**. Confirmed as the brief asks.
- **Caveat: clock steps.** journald refuses to append an entry older than the
  file's tail: the write fails with `EREMCHG` and the file rotates (SD
  `journal-file.c:2321-2325`; `journald-server.c:920`). So realtime is
  monotonic within one file, but not across files or boots. A boot before NTP
  sync (an RTC-less board) can record wrong wall-clock times. Engineering
  reports them as recorded; it cannot correct them. A source whose scan sees
  a realtime decrease reports `clock_regression: true` and the count.
  Membership in `[start, end)` is decided per entry on its own timestamp.
- **Window.**
  - `sd_journal_seek_realtime_usec(start)`, then `next` (the seek bisects the
    entry arrays; no galloping probes);
  - every entry with `start ≤ rt < end` is kept;
  - the scan stops at the first entry with `rt ≥ end`, as today (the existing
    HAMCP-131 limitation applies);
  - `earliest_available` and `latest_available` come from
    `sd_journal_get_cutoff_realtime_usec`.
- **Ordering.** Within a source: libsystemd's merge order. Across sources: the
  existing `timestamp_then_source_journal_order` merge
  (`incident_logs/service.py:793-826`). Ties are broken by the journal's own
  order.
- **Cursors.**
  - Each journal source's position is libsystemd's cursor string
    (`s=…;i=…;b=…;m=…;t=…;x=…`, SD `sd-journal.c:1185-1191`). It is carried
    inside the existing HMAC-signed, process-keyed, 1-hour cursor
    (`incident_logs/cursor.py:275,289-328`), bound to the query fingerprint.
  - A continuation calls `seek_cursor`, then `next`. `test_cursor` must
    confirm the **exact** last-consumed entry is still present.
  - If it is gone (vacuumed or rotated away), the result is
    `STALE_CURSOR`/`journal_continuity_broken`. This replaces the
    digest-reread check of the Supervisor route.

## 6. Scope and authority (question 5, owner decisions)

Source tokens that already exist (`incident_logs/contracts.py:105`) are
`supervisor`, `host` and `addon:<slug>`. Today they return
`unavailable`/`authority_not_granted` with zero requests. Proposed meaning:

| Token | Match | Note |
| --- | --- | --- |
| `supervisor` | `SYSLOG_IDENTIFIER=hassio_supervisor` | The same identifier the Supervisor's own route uses |
| `host` | The Supervisor's pinned 52-identifier default set (SV `data/syslog-identifiers.json`), plus `_TRANSPORT=kernel` | The same scope as the Supervisor's `/host/logs`. The set is pinned in code, not read live |
| `host:kernel`, `host:usb`, `host:network`, `host:storage`, `host:os` (D3) | Named subsets: kernel = `_TRANSPORT=kernel`; usb = kernel with `_KERNEL_SUBSYSTEM=usb`, plus `systemd-udevd`; network = `NetworkManager`, `wpa_supplicant`, `systemd-resolved`, `systemd-timesyncd`; storage = kernel block/scsi/ata/nvme/mmc subsystems, plus `systemd-fsck`, `fstrim`, `systemd-growfs`, `haos-data-disk-detach`; os = `rauc`, `os-agent`, `dockerd`, `containerd`, `haos-*` | Narrower and cheaper for the AI. Each is a fixed match set |
| `addon:<slug>` | §3 mapping | Every add-on (D4) |
| `addon:self` | unchanged (Supervisor `api_bypass` route) | No change |
| `core` | unchanged (`/core/logs` route) | D5 |

Proposed bounds (D6). Unchanged from today unless stated:

- window ≤ 6 h; `end` at most 120 s ahead;
- 1–4 sources per call;
- `limit` 1–200, default 100;
- message ≤ 2000 characters;
- response ≤ 44,000 bytes;
- 25 s per call.

New for journal sources:

- at most 20,000 entries scanned per source per call (`skipped_by_filter`
  plus `outside_window` count against it);
- `sd_journal_set_data_threshold` of 16 KiB per field;
- 4 MiB of field data read per call.

Reaching a bound gives `partial` with a resumable cursor.

## 7. Secrets (question 6)

Other add-ons' logs can contain any of these:

- MQTT credentials, broker URLs with passwords and Wi-Fi keys;
- Zigbee network keys and ESPHome API encryption keys;
- tokens, and Supervisor messages that echo invalid option values.

Engineering cannot read those add-ons' options (they are redacted for
`homeassistant`, SV `api/apps.py:221-232`). It therefore cannot treat their
secrets as known values; detection is by form only.

**Field allowlist** (read by name with `sd_journal_get_data`; nothing else is
ever read):

| Field | Use | Returned? |
| --- | --- | --- |
| `__REALTIME_TIMESTAMP` (via `get_realtime_usec`) | time | as `ts` |
| cursor (via `get_cursor`) | continuation | only inside the signed cursor |
| `MESSAGE` | text | sanitized `message` |
| `PRIORITY` | level | as `level`, `level_basis: journal_priority`: 0-2 critical, 3 error, 4 warning, 5-6 info, 7 debug |
| `SYSLOG_IDENTIFIER` | match and `process` | yes, as `process` |
| `CONTAINER_NAME`, `_TRANSPORT` | match only | no |
| `_KERNEL_SUBSYSTEM`, `_UDEV_SYSNAME` | kernel and USB context (D8) | yes, kernel entries only, sanitized |
| `_BOOT_ID` | reboot boundaries (D8) | as `boot_ref`: an HMAC with the process key, so the raw ID is never returned |
| `CONTAINER_PARTIAL_MESSAGE` | detects a split long line | as `partial_line: true` |

**Never read:**

- process context: `_CMDLINE`, `_EXE`, `_COMM`, `_PID`, `_UID`, `_GID`,
  `_CAP_EFFECTIVE`;
- host and unit context: `_HOSTNAME`, `_MACHINE_ID`, `_SYSTEMD_*`,
  `_SELINUX_CONTEXT`, `_AUDIT_*`, `INVOCATION_ID`;
- crash data: all `COREDUMP_*` fields (`COREDUMP_ENVIRON`, `COREDUMP_CMDLINE`,
  `COREDUMP_PROC_*` hold environments and command lines);
- message metadata: `MESSAGE_ID`, `DOCUMENTATION`, `CODE_*`, `ERRNO`;
- Docker fields: `CONTAINER_ID*`, `IMAGE_NAME`, `SYSLOG_TIMESTAMP`,
  `CONTAINER_LOG_*`;
- `_SOURCE_*`;
- every custom structured field.

This is structural: no field-enumeration symbol is bound (§4).

**Sanitization** stays at the single exit, `incident_logs/service.py` `_finish`
(L625-843). Journal entries join the same list before it, so no new sanitizer
call site is added to `test_hamcp191_shared_sanitizer.SANITIZER_SITES`.
**Steps 1–2 are superseded by the review 1 corrections in §0:** the
journal's own key extension was removed, and every returned field goes
through the shared `credentials.screen` before any cut. The Part 1 order was:

1. Decode as UTF-8 with replacement. Strip ANSI escapes. Truncate to 16 KiB
   (the read threshold).
2. **Withhold whole message.** If `configuration_definitions/credentials.py`
   `credential_text(message, known_secrets)` (L238-260) or `unverifiable_text`
   (L217) fires, the message becomes `[withheld:credential_text]` or
   `[withheld:unverifiable_text]`. `credential_text` covers:
   - URI user-info and connection schemes;
   - `user:pass@host`;
   - `password=`, including in squashed form;
   - known secrets.

   The journal extension adds key forms common in add-on logs, applied the
   same way (folded and squashed): `psk`, `wpa_psk`, `wpa_passphrase`,
   `network_key`, `encryption_key`, `noise_psk`, `private_key`, `api_password`,
   `mqtt_pass`, mosquitto `-P <value>` and `--pw <value>`, and PEM
   `-----BEGIN … PRIVATE KEY-----`. The extension is a new frozen set in the
   journal module. HAMCP-132's definitions are not changed.
3. Then the existing `sanitize_log_lines` multi-line window and the strict
   `sanitize_untrusted_data`. Known secrets are `SUPERVISOR_TOKEN`, the HA
   token and `access_secret` (`incident_logs/service.py:165-169,940-949`).
4. **Any withheld or redacted marker makes the source and the result
   `partial`:** omission `credential_text_withheld`, or the existing
   `redacted_by_policy`.

The text filter matches only sanitized, non-withheld text, as today.

**Residual risk (disclosed):** a bare secret with no key, URI form or known
value can pass. An example is an unlabeled 32-hex Zigbee key printed alone.
This is the same class of risk the Core log source carries today. The
planted-secret sweep in Part 2 covers every returned field.

## 8. The `config.yaml` change (question 7)

- **Change:** add `journald: true` to `hass_mcp_engineering_beta/config.yaml`.
  This is a protected path and needs explicit authorization (D9).
  `hassio_role` stays `homeassistant`, and no other key changes.
  `scripts/validate_addon_metadata.py` checks only the options schema
  (`EXPECTED_BETA_SCHEMA`, L56, L249-251). Part 2 adds a test pinning
  `journald: true` and `hassio_role: homeassistant` together.
- **Security rating:** unchanged. `rating_security` (SV `apps/utils.py:25-95`)
  has no `journald` term. What does move it: AppArmor, ingress, auth API,
  signing, privileges, the `manager`/`admin` roles, host
  network/PID/UTS, and Docker API or full access.
- **What the owner sees:** nothing specific. The Supervisor info API does not
  emit `journald` (SV `api/apps.py:221-313`). The frontend's add-on model and
  capability badges have no journald field (FE `src/data/hassio/addon.ts:60-117`;
  `src/panels/config/apps/app-view/info/supervisor-app-info.ts:355-460`; `grep
  -ri journal src/` finds nothing). Josh sees only an ordinary version update.
  The release notes and SECURITY doc must therefore state the new access
  plainly.
- **When it takes effect:** the installed add-on keeps a copy of the store
  config taken at install or update (SV `apps/data.py:41-43,57-59`), refreshed
  on `update()` and `rebuild()` (`apps/app.py:1084,1143`). Every start
  recreates the container (`docker/interface.py:542-553`;
  `docker/manager.py:909-937`). So the mount appears after updating to the
  release and starting it.
- **Detection and honest unavailability.** The reader decides from
  `/proc/self/mountinfo`, never from an empty result:

  | Condition | Status | Reason |
  | --- | --- | --- |
  | Neither path is a mount point | `unavailable` | `journald_not_enabled`: "the add-on option journald is not enabled for this Engineering container; the Supervisor mounts the host journal only when it is" |
  | A mount exists but is not read-only | `unavailable` | `journal_mount_not_read_only` (fail closed) |
  | The directory cannot be opened | `unavailable` | `journal_unreadable`, with the errno name |
  | `libsystemd.so.0` or a symbol is missing | `unavailable` | `journal_reader_unavailable` |
  | No journal file opened (`has_persistent_files` and `has_runtime_files` both false) | `unavailable` | `journal_empty_or_unsupported` |

  None of these is ever reported as `complete` with no entries.
- **Rollback:**
  - Remove `journald: true` in a new release, or deploy the previous version
    (`2.4.1-beta.37` or whichever precedes it). After update and start, the
    mount is gone.
  - The journal sources then report `journald_not_enabled`, and every other
    source is unchanged.
  - Nothing is persisted. Cursors are process-keyed.

## 9. Not verified (open risks)

- The deployed HAOS, Supervisor and Docker versions.
- The real journal header flags on the device. "Uncompressed, unsealed, no
  ACLs" is inferred from a Kconfig select-closure over the OS 18.3 defconfigs,
  not observed. Option (d) reads any of these; option (c) depends on it.
- That `CONTAINER_NAME` and `_TRANSPORT=journal` are present on HAOS container
  entries (§3); NetworkManager and udev message wording; whether a renamed
  running container keeps its `addon_` tag.
- That `/run/log/journal` exists on every HAOS board. Docker refuses the
  container if it does not (§2). This is the main install-time risk. Part 2
  asks Josh to read the acceptance result before relying on it.
- The libsystemd citations above are at systemd v256.17, the HAOS writer
  version. The reader in the image is 257.13 (Debian). Part 2 re-cites the
  bound functions at Debian's `257.13-1~deb13u1` source, and verifies aarch64
  through the CI smoke.
- Real-format fixtures (D10): no HAOS-written journal file is available
  without live access.

## 10. Decisions for Josh

| # | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| D1 | Reader | (a) `journalctl`, (b) systemd-python, (c) pure Python, (d) `ctypes` + base `libsystemd0`, (e) `cysystemd` | **(d)**. Fallback (c) if in-process native code is unacceptable |
| D2 | Where the reader runs | in-process worker thread; or a spawned Python reader process from the same image (no external binary), which isolates a native crash | **Worker thread**: simpler, the library is mature, and the files are written by the host's journald. A process boundary adds IPC and a second process lifecycle |
| D3 | Host scope | `host` = the Supervisor's 52-identifier set plus kernel; and/or named subsets `host:kernel`, `host:usb`, `host:network`, `host:storage`, `host:os` | **Both** |
| D4 | Add-on scope | any installed slug; a code allowlist; or a new owner-configured option (an options-schema change) | **Any slug**, under §7 withholding. Govee and other third-party add-ons then need no release. A narrower option is safer but needs a release, or a config edit, each time a slug is added |
| D5 | Core via the journal | keep `core` on `/core/logs`; switch it; or add a separate `core:journal` token | **Keep `core` unchanged.** HAMCP-220 behavior stays as reviewed. Revisit after the journal path is deployed |
| D6 | Bounds | §6 values | **§6 as proposed** |
| D7 | Secret handling | withhold the whole message on a credential form (§7); or redact only the matched span | **Whole message**, partial result |
| D8 | Extra returned fields | `boot_ref` (HMAC of the boot ID); `kernel_subsystem` and `kernel_device` for kernel entries; or neither | **Both**: they are the evidence for reboot and dongle drop-out timelines |
| D9 | `config.yaml` | authorize `journald: true` only, with `hassio_role: homeassistant` unchanged | Authorize, in the Part 2 release PR |
| D10 | Fixtures | generate real-format journal files with `systemd-journal-remote` from a committed export-format text, in a digest-pinned Debian trixie image (257.13), using the inferred HAOS flags (keyed hash and compact) plus a zstd-compressed variant. Provenance records the image digest, tool version, command and SHA-256 values; or Josh supplies a sanitized export from his device (a live action he would perform) | **Generated fixtures**. The acceptance live reads confirm the real format |

## 11. Part 2 outline (after decisions)

- **Code:**
  - a new `incident_logs/host_journal.py` (binding, mount detection,
    matches, scan);
  - journal-source wiring in `incident_logs/contracts.py` and `service.py`;
  - signed-cursor journal state;
  - an amended static check (only this module may use `ctypes` and the two
    fixed `os.open` calls; symbol allowlist; no enumeration or send
    symbols).
- **Not changed:**
  - the tool schema shape (`sources` items keep the same pattern, apart from
    the `host:<category>` tokens if D3 is accepted);
  - the tool count (68);
  - routing, provider (`supervisor_incident_logs`) and policy
    (`incident_window_journal_read_v1`);
  - the Core gate (journal reads are local and Core-independent);
  - stable v1.
- **Tests:**
  - real-format fixtures covering Docker entries for several add-ons (both
    name forms), kernel USB disconnect and reconnect, Supervisor entries,
    clock regression across files, and a compressed variant;
  - slug mapping, including a near-miss identifier;
  - windows, cursors, vacuumed-cursor staleness and ordering on realtime;
  - each unavailability reason in §8;
  - a planted-secret sweep across every returned field;
  - a field-allowlist proof;
  - path safety, including a symlink fixture;
  - static read-only checks;
  - the `config.yaml` pin.
- **Mutations:**
  - a non-allowlisted field returned;
  - sanitization bypassed for journal entries;
  - text timestamps used instead of `__REALTIME_TIMESTAMP`;
  - journald off reported as complete and empty.
- **Release:**
  - `journald: true` and the next free beta (`promote_next_release.py --apply`);
  - beta docs, CHANGELOG, README, `TOOL_AUDIT.md` and `docs/SECURITY.md`;
  - an acceptance document with three live reads: a recent Supervisor
    window, one named add-on, and a host/kernel window.

## 12. Non-actions

- No change to runtime code, tests, `config.yaml`, the `Dockerfile`, locks,
  build inputs, workflows, release declarations, registries or stable v1.
- No live Home Assistant, Supervisor, deployed MCP or credential access.
- The pinned base image was pulled and run read-only, with no network, only
  to list its packages.
- Upstream sources were read as data; no upstream build or script was run.
