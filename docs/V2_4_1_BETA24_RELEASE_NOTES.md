# Engineering 2.4.1-beta.24 release notes

Engineering 2.4.1-beta.24 adds one read-only tool, `get_backup_inventory`
(HAMCP-130). It answers "are my backups healthy?" from Home Assistant Core's
own backup manager. It builds on 2.4.1-beta.23 (HAMCP-209).

The tool cannot create, restore, delete, download or change backups or backup
settings. It has no such option, hidden or otherwise.

Design and Part 1 source verification:
[`HAMCP_130_BACKUP_INVENTORY_DESIGN.md`](HAMCP_130_BACKUP_INVENTORY_DESIGN.md).
Acceptance:
[`V2_4_1_BETA24_ACCEPTANCE.md`](V2_4_1_BETA24_ACCEPTANCE.md).

## What it reports

Each call makes two fixed Core WebSocket reads, in order:
`backup/config/info` and then `backup/info`. Both are admin-only Core commands.
Engineering already holds that authority through the Supervisor proxy and
already used `backup/info` for governed backup verification. No permission,
add-on option or `config.yaml` permission changes.

The report contains:
- **Per backup, newest first** (`limit` 1–50, default 25):
  - id, name, date and age;
  - size;
  - content scope (`full`, `partial` or `unknown`);
  - whether Home Assistant and the database are included, and the Home
    Assistant version;
  - add-on names and folders;
  - a `protected` (encrypted) flag;
  - whether it was automatic;
  - each storage location that holds a copy, with its storage class, size and
    protection;
  - any failed locations, add-ons or folders.
- **Summary:**
  - backup count;
  - newest and oldest backup;
  - newest **full** backup and its age;
  - whether the newest backup and the newest full backup have a confirmed
    off-site copy;
  - every storage location with its count, newest date and read error.
- **Automatic backups:**
  - schedule and retention as configured;
  - target locations and contents;
  - an `encryption_configured` flag, never the key;
  - next scheduled time;
  - last attempted and last completed times;
  - `last_result`.
- **Recent failures:** up to 20.
- **Health signals:** nine facts, each with its threshold stated, such as "no
  full backup in 7 days", "newest backup only on this host" and "last automatic
  backup not completed". They are observations, not alarms.

## Truthful completeness

- The report is `complete` only when both reads succeeded, every field was in
  contract and nothing was redacted or trimmed.
- Otherwise it is `partial`, with reasons in `omissions`. Partial causes
  include:
  - an unreadable storage location;
  - one failed read;
  - a malformed, missing or out-of-range field, reported per field
    (`invalid_field:<pointer>`);
  - a truncated name or error text (`truncated_field:<pointer>`);
  - a list cap;
  - the response budget;
  - redaction;
  - an encryption key that could not be positively read
    (`encryption_key_unknown`).
- Each health signal declares the inputs it depends on (`depends_on`). If any
  of them is unreadable or partial, the signal is `unknown`, with
  `evidence_complete: false` and the inputs named in `unreadable_inputs`.
- When both reads fail, the call returns `analysis_unavailable`.
- A permission failure, or a change of Core authority between reads, fails
  closed.

## Definitions and limits

- **`full`**:
  - Supervisor installations: Home Assistant, the database and every
    Core-known folder are included, and no add-on or folder failed.
  - Core installations: Home Assistant and the database are included.
  - Core does not report Supervisor's full or partial type, or whether every
    installed add-on was included. `full` cannot attest the latter.
- **Off-site** is confirmed only for Home Assistant Cloud. Network storage and
  integration agents are reported as off-host, not confirmed off-site.
- **Thresholds** are fixed and stated in every response:
  - full backup: 7 days;
  - last completed automatic backup: 2 days for a daily schedule, 8 days for
    custom days.
- The tool reads what Core records. It does not verify archive integrity or
  test a restore.

## Security

- **Encryption key.** Core's `backup/config/info` returns the backup
  encryption key in plaintext. Engineering:
  - never projects it;
  - reports only `encryption_configured`;
  - uses the key for that one response as a known secret in the shared strict
    sanitizer (HAMCP-191), so a backup name or error that contains it, in
    any letter case, is withheld;
  - fails closed when the key cannot be positively read (the config read
    failed, or the key field is missing or malformed): every name and error
    text is withheld, free-form identifiers are replaced by stable aliases
    such as `withheld#1`, and only structured facts (dates, sizes, flags,
    fixed location ids, versions) are shown.
- **Untrusted text.**
  - Backup names, error text and every identifier pass through the shared
    sanitizer.
  - Forged redaction markers are neutralized.
  - Identifiers must match closed patterns.
  - Any redaction makes the report partial.
- **Fixed reads.** The only dispatch is the two fixed read commands. Static
  and runtime tests prove that there is no other command, client method or
  write import.
- **Core gate (HAMCP-164).** The tool is mapped to
  `core.basic_websocket_read`, and refused when that route is not attested.
- **Validation and audit.**
  - Arguments are validated before the SDK.
  - Invalid input is refused without echo.
  - Audit records only the profile, the limit, `upstream_calls: 0` and
    `fallback: none`, plus per-source outcomes and the backup count. It never
    records names or keys.

## Catalog

| | Before | After |
|---|---|---|
| Static Engineering tools | 59 | **60** |
| Advertised catalog | 87 | **88** (60 native + 28 delegated) |
| Advertised catalog, optional YAML reader admitted | 88 | **89** (60 + 29) |

Every existing descriptor and input schema is unchanged. Also unchanged:
- signed registries and the HOLD;
- the support window;
- Core capability profiles and probe fingerprints;
- persisted formats;
- workflows and stable v1.

## Rollback

The tool is additive and has no persisted state. Reinstalling beta.23 removes
it.
