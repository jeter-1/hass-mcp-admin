# Engineering 2.4.1-beta.24 acceptance

This contract covers HAMCP-130: the read-only `get_backup_inventory` tool. It
builds on 2.4.1-beta.23 (HAMCP-209).

See also:
- Release notes:
  [`V2_4_1_BETA24_RELEASE_NOTES.md`](V2_4_1_BETA24_RELEASE_NOTES.md).
- Design and Part 1 verification:
  [`HAMCP_130_BACKUP_INVENTORY_DESIGN.md`](HAMCP_130_BACKUP_INVENTORY_DESIGN.md).

**Release target.** `2.4.1-beta.24`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.23. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-130 objective: a read-only backup inventory with no create,
  restore, delete, download or settings path;
- `2.4.1-beta.24` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- the Part 1 source choice and authority (design §1): Core WebSocket
  `backup/config/info` and `backup/info` through the existing Supervisor-proxy
  admin authority, with no new permission or add-on configuration;
- zero new write, dispatch, service-call, fallback or forwarding reachability
  (design §4);
- the plaintext encryption key in `backup/config/info`: never projected, used
  only as a per-response known secret, and when it is not positively known
  every free-text field is withheld and identifiers are aliased (design §3.4);
- per-field completeness and the declared inputs of each health signal
  (design §3.1 and §3.3);
- sanitizer coverage of names, error text and identifiers;
- the HAMCP-164 Core gate mapping.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema;
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows and dependencies.

### Offline proof

No test reaches a live system.

- `tests/test_hamcp130_backup_inventory.py` (79 tests) uses synthetic
  fixtures modeled on Core 2026.10.1's response shapes. It covers:
  - six scenarios: local only; local + cloud; failed automatic backup;
    encrypted; partial backups; empty;
  - summary and health-signal logic;
  - partial reporting when a source fails;
  - sanitizer coverage;
  - twelve key-unknown variants, with the planted key absent from the
    response, errors, audit and DEBUG logs;
  - per-field validity, truncation and list caps;
  - every signal `unknown` when a declared input is unreadable, and decided
    when all are complete;
  - a static and runtime no-write-path check;
  - Core-gate mapping, registration and audit privacy;
  - sample drift.
- Mutations: 38 of 38 killed (`docs/evidence/hamcp130-mutations/`), including:
  - a failed read reported as complete (F1 to F7);
  - newest-full picking a partial (N1 to N6);
  - free text or identifiers shown while the key is unknown (H1 to H6);
  - truncation or an invalid field not making the report partial (M1a to
    M1g);
  - a signal decided from partial inputs (M2a to M2g).
- Count suites: 59 → 60 static tools, 87 → 88 advertised and 88 → 89 with the
  optional YAML reader admitted.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.24;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.23 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.23 artifact, which
removes the tool.

## 3. Installed Engineering

All installed checks below are read-only. None of them creates, restores,
deletes or downloads a backup, or changes backup settings.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.24 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 88 tools, or 89 with the optional YAML reader admitted;
   - `get_backup_inventory` present with `readOnlyHint: true`,
     `destructiveHint: false` and the `limit` schema (integer 1–50,
     `additionalProperties: false`);
   - every other descriptor unchanged from beta.23.

   Also confirm Core signed selection, storage and audit health, and zero
   fallback.
3. **Live read of Josh's real backup inventory (read-only).** Josh runs this
   check, or authorizes it explicitly.
   - Call `get_backup_inventory` with the default limit, and once with
     `limit: 1`.
   - Record the following:
     - `completeness` and any `omissions`;
     - `installation`;
     - `summary.backup_count`;
     - newest backup and newest full backup (id, date, age, storage classes);
     - `newest_backup_offsite_copy`;
     - the storage locations and any read errors;
     - `automatic_backups` schedule, retention, `last_attempted`,
       `last_completed` and `last_result`;
     - each health signal's `status`.
   - Compare against Home Assistant's own **Settings → System → Backups**
     page. The following must agree:
     - the backup count;
     - the newest backup's date and locations;
     - the automatic-backup schedule and retention;
     - the last automatic backup's time and result.
   - Confirm the following:
     - the response contains no encryption key and no backup-download path;
     - `encryption_configured` matches the Backups page's encryption setting;
     - a backup Home Assistant shows as encrypted reports `protected: true`.
   - A `partial` result is acceptable if its reasons are true. For example, an
     unreachable network-storage location should appear as
     `storage_location_unreadable`, with that location's `read_error`.
4. **Audit.** In `get_audit_log`, the call's entry records only:
   - `limit`;
   - provider `engineering`;
   - `upstream_calls: 0`;
   - `fallback: none`;
   - per-source outcomes and the backup count.

   It must contain no backup name or key.
5. **No change.** The Backups page shows the same backups and settings before
   and after steps 3 and 4.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
