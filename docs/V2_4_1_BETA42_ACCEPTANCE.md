# Engineering 2.4.1-beta.42 acceptance

This contract covers HAMCP-234 step 1: `get_incident_logs` host journal
reads that are usable on real add-on log formats. A withheld entry names the
fixed screen rule that withheld each part; a text-free `calibration` mode
returns per-source counts only; `supervisor` and `addon:<slug>` levels are
parsed from known log formats; and the tool description no longer says a
manager role is needed. **Nothing withheld is relaxed**: what the screen
withholds is unchanged. Relaxations are step 2, after a live calibration.
It follows 2.4.1-beta.41 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA42_RELEASE_NOTES.md`](V2_4_1_BETA42_RELEASE_NOTES.md).
- The host journal contract this release extends:
  [`V2_4_1_BETA38_ACCEPTANCE.md`](V2_4_1_BETA38_ACCEPTANCE.md) and
  [`HAMCP_224_HOST_JOURNAL_DESIGN.md`](HAMCP_224_HOST_JOURNAL_DESIGN.md).

**Release target.** `2.4.1-beta.42` follows the agreed merge order (#315 as
beta.39, the HAMCP-226/228 change as beta.40, #314 as beta.41). The
`.release/next-version` declaration accepts only the current version plus
one, so it is written, and `scripts/promote_next_release.py --apply` run, in
a separate materialization commit after `main` reaches 2.4.1-beta.41 and is
merged into this branch. If the order changes, this change is restaged at
the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-234 step 1 scope (rule names, calibration, container levels,
  the description; no relaxation);
- `2.4.1-beta.42` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No change to what is withheld.** The decision is still
  `credentials.screen`'s alone, and `credentials.screen` is now
  `screen_rule(...)[0]`. `credential_text` is
  `credential_rule(...) is not None`, so the same ordered chain decides. The
  journal reader reads a rule id only for text the screen already withheld
  (`host_journal.screen_text_rule`). The HAMCP-132 (`test_pr294_*`) and
  HAMCP-224 (`test_pr313_*`, `test_review2_ansi_residual`) reviewer suites
  pass unchanged.
- **Rule ids are fixed names.** Each id is a constant: the 42 in
  `credentials.RULE_IDS`, plus the reader's own `field_too_long`, `not_utf8`
  and `unverifiable_escape` (`host_journal.RULE_IDS`, 45 in total). A keyword
  id is the named group of the fixed `_KEYWORD` pattern
  (`labelled_keyword:api_key`), never the matched text. A rule met only in
  the squashed form carries `.squashed`. Any value outside `RULE_IDS` is
  reported as `unlisted_rule` (`service._rule_id`). An id never carries the
  withheld text, its length or the matched substring.
- **Calibration is counts only.** `calibration: true` is accepted for host
  journal sources only, and never with `min_level`, `text` or `cursor`
  (`invalid_arguments`). It walks the same sources and window bounds as a
  read, at most 2,000 entries per source (`MAX_FILTER_SCAN_ENTRIES`), and
  screens every in-window entry with the same `screen_text_rule`. It
  returns, per source:
  - the status and reason, and the reader;
  - the window coverage times;
  - `scanned`, `shown`, `withheld_entries`, `withheld_messages` and
    `withheld_fields`;
  - `withheld_by_rule` (the deciding rule) and `matched_by_rule` (every
    rule that matches);
  - `match_rejected`, `entries_unreadable` and gaps.

  It returns no entry, message, field, process, identifier, cursor or
  `boot_ref`. The strict sanitizer still runs on the envelope, at the single
  `_finish` exit. Any change it would make fails the call closed
  (`sources_unavailable`), never a partly sanitized success. That is the
  one new sanitizer call site, listed in the HAMCP-191 table.
- **Levels are a decision, never output text.** For `supervisor` and
  `addon:<slug>` only, the level is parsed from the start of the screened,
  sanitized first line, in five anchored formats (`container_levels.py`,
  each cited to pinned source). A matched level is `level_basis:
  parsed_text`; an unmatched line keeps its journal priority
  (`journal_priority`). A withheld message is never parsed: it keeps its
  priority as `priority_unverified`. Under `min_level` it is never returned
  and never silently dropped: it counts in `withheld_level_filtered`, which
  makes the result partial. `host`, `host:<category>` and kernel entries
  keep the journal priority.
- **Description.** `supervisor`, `host`, `host:<category>` and
  `addon:<slug>` come from the host journal (the `journald` option); no
  manager role is needed.

Preserve the following:
- stable v1.1.2;
- persisted formats (none change; calibration builds no cursor);
- every input schema except one new optional `calibration` boolean on
  `get_incident_logs` (strict, default `false`); no tool is added or
  removed, so the catalog count is the base's;
- routing, provider (`supervisor_incident_logs`), policy
  (`incident_window_journal_read_v1`), the Core gate and telemetry;
- probe fingerprints, the signed Core capability profiles and both signed
  registries;
- every workflow and dependency; `config.yaml` changes only its version;
  `hassio_role: homeassistant` and `journald: true` are unchanged.

### Offline proof

No test reaches a live system or a real host journal. Every value is
synthetic.

- `tests/test_hamcp234_journal_usability.py`:
  - **Rule ids:** each deciding rule produces its id; the four ids that
    only ever match (the bearer token and PEM value shapes, normal and
    squashed); coverage of all 45 ids; the decision unchanged against
    `screen`; and a planted-secret sweep showing no id carries any text.
  - **Rows:** `withheld_rules` per part (`password_field`,
    `labelled_keyword:api_key` on `process`) across every returned field.
  - **Calibration:** the counts-only structure; calibration over four
    sources at a time; counts equal to a normal paged read of the same
    window; status `complete` only when finished with nothing rejected,
    unreadable or missing; no text under any input; rewritten records
    counted by rule; invalid arguments refused; a sanitizer change failing
    closed.
  - **Levels:** real-format fixtures (Supervisor 2026.09.1 and 2026.10.4,
    matter.js with ANSI colour, Python `BASIC_FORMAT` and asctime, bashio),
    including a stderr PRIORITY 3 `INFO` line read as `info`; lines that
    match no format; a level word later in the line or on a later line; host
    sources unparsed; a withheld line kept `priority_unverified` and counted
    under `min_level`.
  - **Description:** checked through the SDK registry.
- Three edits to the HAMCP-224 author suites; every other expectation is
  unchanged:
  - `tests/test_hamcp224_host_journal.py` allows the new row key
    `withheld_rules`;
  - the same file's static check admits the rule-reporting forms of the one
    screen (`screen_text_rule`, `screen_text_rules`) and the
    `_calibration_envelope` caller;
  - `tests/test_hamcp224_review2_corrections.py`
    `test_the_priority_field_sets_the_level` moves from `supervisor`, now
    parsed, to `host:kernel`, which keeps PRIORITY.

  The reviewer suites are byte-unchanged.
- `tests/test_hamcp191_shared_sanitizer.py`: the calibration sanitizer call
  site is added to `SANITIZER_SITES`.
- Mutations (`docs/evidence/hamcp234-mutations/`) cover the four required
  classes:
  - `R1`-`R4`: a rule id carrying the matched text;
  - `K1`-`K4`: calibration returning any text;
  - `P1`-`P3`: the container level taken from PRIORITY when a known format
    matches;
  - `D1`-`D3`: a withheld entry dropped by `min_level` without being
    counted.

  Further classes probe the parse (`F1`-`F4`), the counts (`C1`-`C3`) and
  the row (`W1`). Run 1 stopped at its baseline on the HAMCP-191 table
  test (`run1-baseline-failed.log`): the calibration exit's sanitizer call
  was not yet listed. With it listed, run 2 killed 22 of 22 (`results.json`,
  `run2.log`); `results.json` records each mutant and the tests that killed
  it. After the merge of `main` at 2.4.1-beta.41 (which changed
  `credentials.py`), run 3 on Python 3.12.14 killed 22 of 22 again
  (`results-run3.json`, `run3.log`).

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.41 artifacts and the known-good earlier
artifacts for recovery.

No stored format changes. **Rollback is reinstalling the 2.4.1-beta.41
artifact.**

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only. Record each calibration result in HAMCP-234: it is the evidence
for any step-2 relaxation.

1. **The add-on starts after the update (first).** Confirm the Engineering
   add-on reaches `started` and its MCP endpoint answers. If it does not,
   stop and reinstall 2.4.1-beta.41.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge commit.
3. **Description.** The `get_incident_logs` description in `tools/list`
   says no manager role is needed and documents `calibration`.
4. **Calibration of `addon:core_matter_server`.** Call `get_incident_logs`
   with `sources: ["addon:core_matter_server"]` and `calibration: true` over
   a recent window. Expected:
   - `mode: calibration`, with per-source counts only and no entry, message,
     process or cursor anywhere in the response;
   - `withheld_by_rule` and `matched_by_rule` contain only ids from
     `rule_ids`;
   - `scanned` equals `shown` plus `withheld_entries`.

   Record the counts: beta.38 withheld 342 of 368 entries in this source.
5. **Calibration of `supervisor` and two more add-ons.** Repeat step 4 for
   `sources: ["supervisor"]` and for two other installed add-ons
   (`addon:<slug>`), and record their counts.
6. **Calibration matches a read.** For one source of steps 4-5, a normal
   read of the same window (paged to the end) has as many entries as
   `scanned`, and as many withheld entries as `withheld_entries`.
7. **Supervisor warnings.** `sources: ["supervisor"]` with
   `min_level: warning` over a recent window returns only real warnings:
   - every returned entry is `warning`, `error` or `critical`, with
     `level_basis: parsed_text`;
   - its text shows a `WARNING`, `ERROR` or `CRITICAL` Supervisor level;
   - no `INFO` or `DEBUG` line is returned;
   - a withheld Supervisor line is not returned but counted in
     `withheld_level_filtered`, with a partial result.
8. **Container levels without a filter.** The same `supervisor` read without
   `min_level` shows `info` entries with `level_basis: parsed_text`, which
   beta.41 reported as `error`.
9. **Rule names.** Any withheld entry has `withheld_rules`, naming each
   withheld part (`message`, `process`, …) with an id from the calibration's
   `rule_ids`, and no credential text.

## 4. Not covered

No relaxation of the screen, live canary, write, registry signing or
Supervisor role change is part of this release. A line in no known format
keeps its stream's priority and is not given a neighbour's level (multi-line
level inheritance is step 2). A non-HAOS Supervised host remains outside the
evidence, as in beta.38.
