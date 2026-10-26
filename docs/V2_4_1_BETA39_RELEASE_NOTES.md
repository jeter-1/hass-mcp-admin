# Engineering 2.4.1-beta.39 release notes

Engineering 2.4.1-beta.39 adds automation trace snapshots and watched
automations (HAMCP-225), so a failing run's trace survives Core's rotation of
its five retained runs (audit finding HAAC-E001). It also routes
`get_automation_trace` through the shared sanitizer (the trace-read part of
HAMCP-194, owner decision O2). It follows 2.4.1-beta.38 and includes it. It
adds no Home Assistant write, Core capability profile, signed registry,
delegated provider, fallback, workflow, permission or dependency.

Acceptance: [`V2_4_1_BETA39_ACCEPTANCE.md`](V2_4_1_BETA39_ACCEPTANCE.md).
Design: [`HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md`](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md)
(owner decisions at the top and in section 8, implementation note in
section 10).

## What changed for a client

- **`capture_automation_trace`.** Stores sanitized copies of an
  automation's retained traces: exactly one run with `run_id`, otherwise up
  to five retained runs, skipping `not_triggered` and (unless
  `include_condition_failures`) `failed_conditions`. Each run reports
  `captured`, `already_captured`, `trace_not_retained`, `run_in_progress`,
  `over_size_bound`, `store_full` or `read_failed`. An optional `note` (at
  most 200 characters) is stored with the snapshot.
- **`list_trace_snapshots`.** Newest first, with store usage and each
  snapshot's origin (`manual` or `trace_watch`), completeness and expiry. No
  Home Assistant call.
- **`get_trace_snapshot`.** Reads one snapshot in pages: the first page
  carries the run summary, config, blueprint inputs and context; later pages
  carry steps in Core's path order. No Home Assistant call.
- **`set_automation_trace_watch`.** `add`, `remove` or `list` watched
  automations (mode `failures` or `executed`, 1 to 168 hours). Engineering
  then captures new stopped runs that match the mode within about 30
  seconds. No change plan or approval is needed, because it is read-only
  toward Home Assistant. Every add and remove is audited with the caller, and
  every response returns the current list.
- **`get_automation_trace`.** Same input and data shape. Credential-like and
  known-secret values are now withheld and strings over 2,000 characters are
  shortened; such a trace is `partial` and names the withheld categories.

## Disclosures

- **New eviction behavior (E2).** Each watch keeps at most 20 snapshots and
  evicts its own oldest when full; the eviction is audited. Manual snapshots
  are never evicted. A manual capture at capacity (200 snapshots or 32 MiB)
  is refused with `store_full`. Snapshots expire after 90 days. A removed
  watch's snapshots stay until they expire.
- **Read-back fidelity (R1).** A string partly redacted at capture is
  withheld whole on read. HAMCP-207 could restore fidelity later.
- **Undetectable resolved secrets.** Core returns `config` with `!secret`
  values already resolved. Such a value is withheld only when its field is
  credential-named or its value credential-shaped, or it equals one of
  Engineering's own secrets. This is unchanged from `get_automation_config`.
  A PEM private key block with its header is withheld whole; a header-less
  key body is not detected.
- **Who can read.** List and read are open to any authenticated caller, like
  every other Engineering read. Their cursors are bound to the caller and end
  at a server restart.
- **Unattended reads.** The watch poller is the only periodic Core read
  Engineering makes: at most 10 `trace/list` commands every 30 seconds, plus
  `trace/get` for new matching runs. It pauses while Core authority is
  unavailable or the version is not admitted; missed runs are counted, never
  backfilled. Server health output is unchanged.
- **Store failures.** A corrupt index fails closed; a write whose commit
  cannot be confirmed blocks further store writes until restart; a record
  whose digest does not match is quarantined.
- **Backups.** Snapshots live under the add-on's `/data` and so are included
  in Home Assistant backups. They are sanitized evidence only.

## Compatibility

- Four new static tools: static 68 to 72; the catalog is **100 tools, 101
  with the optional YAML reader admitted**.
- `get_automation_trace`: input schema unchanged; its description gains one
  sanitization disclosure; its result can now be `partial`.
- New persisted format: `trace-snapshots-v1` under the governance path. No
  existing persisted format changes.
- The signed Core capability profiles, probe fingerprints, the owner-signed
  Core registry, the signed ha-mcp registry and the delete profile's contract
  fingerprint are unchanged (recomputed, base and head; see the PR).
- No workflow, permission, add-on configuration, dependency or stable-v1
  change.

**Protected runtime files.** Josh authorized the HAMCP-225 runtime scope
(`hass_mcp_engineering_beta/ha_mcp_engineering/`) on 2026-10-26. The changed
files are listed in the PR.

## Rollback

Reinstall the 2.4.1-beta.38 artifact. Beta.38 ignores the
`trace-snapshots-v1/` namespace, has no watch poller and does not offer the
four tools; the stored snapshots stay on disk for a re-upgrade.
`get_automation_trace` returns to its beta.38 output.
