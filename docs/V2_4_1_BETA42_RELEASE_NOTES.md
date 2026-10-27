# Engineering 2.4.1-beta.42 release notes

Engineering 2.4.1-beta.42 makes `get_incident_logs` host journal reads
usable on real add-on log formats (HAMCP-234 step 1). It follows
2.4.1-beta.41 and includes it. There is no dependency or add-on
configuration change, and **nothing withheld is relaxed**.

Acceptance: [`V2_4_1_BETA42_ACCEPTANCE.md`](V2_4_1_BETA42_ACCEPTANCE.md).

## Why

Live beta.38 reads found three problems:
- **Every container line read as `error`.** The `supervisor` and
  `addon:<slug>` sources reported every line at level `error`: the
  container log driver journals stderr at PRIORITY 3, and Python logging
  writes every record to stderr.
- **Most of one add-on was withheld.** In `addon:core_matter_server`, 342
  of 368 entries were withheld, mostly false positives, with no way to see
  which rule withheld them.
- **The description was stale.** It said these sources need role
  `manager`.

This release names the rules and adds a text-free way to count them, so the
next step can relax the screen from evidence.

## The change

- **Rule names.** A withheld host journal entry has `withheld_rules`, which
  maps each withheld part (`message`, `process`, `kernel_subsystem`,
  `kernel_device`) to the fixed id of the rule that withheld it:
  `password_field`, `labelled_keyword:token`, `value_shape:labelled_field`,
  `invisible_or_control`, `not_utf8`, `field_too_long`, and so on. There are
  45 ids, all constants, listed in a calibration's `rule_ids`. The shared
  `credentials.screen` exposes them as data (`screen_rule`), from the same
  ordered chain that decides, so what is withheld is unchanged. An id never
  carries the text, its length or the matched part.
- **Calibration.** `calibration: true`, for host journal sources only and
  without `min_level`, `text` or `cursor`, uses the same sources and window
  bounds as a read. It returns only per-source counts:
  - scanned, shown and withheld entries, with withheld messages and fields;
  - `withheld_by_rule`, by the rule that withheld each part;
  - `matched_by_rule`, by every rule that matches it.

  It counts at most 2,000 entries per source. It returns no entry, text,
  process or cursor. The strict sanitizer still runs on the result, and any
  change it would make fails the call closed.
- **Container levels.** For `supervisor` and `addon:<slug>`, the level is
  parsed from the screened text's first line in a known format:
  - Supervisor `LEVEL (Thread) [module]`;
  - matter.js (the Matter Server add-on);
  - Python `LEVEL:name:` and ` - LEVEL - `;
  - bashio `[hh:mm:ss] LEVEL:`.

  A parsed level is `level_basis: parsed_text`; a line in no known format
  keeps its priority (`journal_priority`). A withheld message is never
  parsed and keeps its priority as `priority_unverified`. `min_level` never
  returns it: it is counted in `withheld_level_filtered`, and the result is
  partial. `host`, `host:<category>` and kernel entries keep the journal
  priority.
- **Description.** These sources come from the host journal mounted by the
  `journald` option; no manager role is needed.

## Compatibility

- One new optional input, `calibration` (strict boolean, default `false`),
  on `get_incident_logs`. No tool is added or removed.
- A journal row gains `withheld_rules` when withheld. A container row's
  `level` and `level_basis` can now be `parsed_text` or
  `priority_unverified`. A `min_level` read of a container source can
  return different entries than beta.41, because levels are now parsed.
- Routing, provider, policy, the Core gate, telemetry, the signed profiles,
  probe fingerprints and both signed registries are unchanged.
- No persisted format, workflow, Dockerfile, dependency, Supervisor role,
  add-on option or stable-v1 change.

**Protected runtime files.** The HAMCP-234 task authorized this scoped
change:
- `configuration_definitions/credentials.py` (rule ids as data);
- `incident_logs/host_journal.py`, `incident_logs/container_levels.py`
  (new), `incident_logs/service.py` and `incident_logs/contracts.py`;
- `tools/incident_logs.py`;
- the version.

## Step 2 (not in this release)

Any relaxation of the journal screen, and level inheritance for unparsed
continuation lines, comes after a live calibration of
`addon:core_matter_server`, `supervisor` and other add-ons with this
release.

## Rollback

Reinstall the 2.4.1-beta.41 artifact. No stored format changes.
