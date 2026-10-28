# Engineering 2.4.1-beta.43 acceptance

This contract covers HAMCP-234 step 2: one relaxation of the host journal
screen, proven by the beta.42 live calibration. In the journal path only,
`user_pass_host` is read as **one contiguous token in the normal form** and
no longer in the squashed form. Every other rule is unchanged in both forms,
and the configuration-definitions and trace paths are unchanged. It also
adds regression tests showing `calibration` is reachable in the served
schema. It follows 2.4.1-beta.42 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA43_RELEASE_NOTES.md`](V2_4_1_BETA43_RELEASE_NOTES.md).
- The contract this release extends:
  [`V2_4_1_BETA42_ACCEPTANCE.md`](V2_4_1_BETA42_ACCEPTANCE.md) and
  [`HAMCP_224_HOST_JOURNAL_DESIGN.md`](HAMCP_224_HOST_JOURNAL_DESIGN.md).

**Owner decision (Josh, 2026-10-27).** On beta.42, the live calibration of
`addon:core_matter_server` (2026-10-27, about 23:25Z) scanned 40 lines and
withheld 37, all as `user_pass_host.squashed`. These are matter.js lines
with colon-rich timestamps and an `@` later in the line. The decision: in
the journal path only, `user_pass_host` applies to the normal form and needs
one contiguous token (no whitespace between the user, `:`, the password, `@`
and the host). It no longer runs on the squashed form there. Every other
rule is unchanged in both forms. The HAMCP-132 definitions path and the
HAMCP-225 trace path are unchanged.

**Release target.** `2.4.1-beta.43` is the next free beta after `main` at
2.4.1-beta.42 (#316). If another release reaches `main` first, this change
is restaged at the next free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-234 step 2 scope (the one-token `user_pass_host` in the journal
  path; calibration reachability tests; no other screening change);
- `2.4.1-beta.43` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, with both Real HA jobs;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **One shared screen, one option.** `credentials.screen`, `screen_rule`,
  `screen_rules`, `credential_text`, `credential_rule` and
  `credential_rules` take a keyword-only `contiguous_user_pass_host`,
  default `False`. When it is set, the squashed form skips only
  `user_pass_host` (`_credential_form_rules(..., user_pass_host=False)`).
  The normal-form `_USER_PASS_HOST` already excludes whitespace, so it is
  the contiguous-token rule. No other rule or form reads the option.
- **Only the journal sets it.** `host_journal._JOURNAL_SCREEN` (read-only:
  `labelled=True, contiguous_user_pass_host=True`) is used by all five
  screen calls of `screen_text_rule` and `screen_text_rules`. The
  definitions path (default mode) and the trace path
  (`trace_normalization.screen_trace_value`, labelled and default modes) do
  not pass it. Their suites pass unchanged: HAMCP-132
  (`test_hamcp132_configuration_definitions`, `test_pr294_*`) and HAMCP-225
  (`test_hamcp225_*`).
- **Rule ids.** `host_journal.RULE_IDS` drops `user_pass_host.squashed`
  (44 ids). `credentials.RULE_IDS` keeps it (42) for the other paths.
- **Disclosed cost.** In the journal, a scheme-less `user:pass@host` split
  by whitespace (literally or percent-encoded) is shown unless another rule
  meets it. A `scheme://user:pass@host` is `credential_url` in the normal
  form, unchanged. The labelled rules (`password`, `token`, `-P`, `Bearer`,
  …), known secrets (normal and squashed), PEM headers, invisible and
  control characters and the value shapes are unchanged in both forms.
- **Calibration reachability.** There was no source defect. `calibration`
  was already a strict optional boolean on `get_incident_logs`. A test now
  reads it from the production application's served `tools/list`. Another
  makes a `calibration: true` call over the authenticated gateway.

Preserve the following:
- stable v1.1.2;
- persisted formats (none change);
- every input schema, tool and the catalog count;
- routing, provider (`supervisor_incident_logs`), policy
  (`incident_window_journal_read_v1`), the Core gate and telemetry;
- probe fingerprints, the signed Core capability profiles and both signed
  registries;
- every workflow and dependency. `config.yaml` changes only its version;
  `hassio_role: homeassistant` and `journald: true` are unchanged.

### Offline proof

No test reaches a live system or a real host journal. Every value is
synthetic. Every credential payload is assembled at run time, so no
committed file holds secret-shaped text.

- `tests/test_hamcp234_step2_journal_relaxation.py`, live-shaped first:
  - **Live-shaped lines shown.** Matter.js lines are built from the live
    sample's format (`tests/hamcp234_step2_corpus.py`). A colon-rich
    timestamp is followed by `peer@1:5a3c`, `secure#2b71@peer@1:5a3c`,
    `@1:5a3c`, IPv6 and `host:port` forms. All are shown by the journal
    screen. On the beta.42 screen they were withheld, nearly all as
    `user_pass_host.squashed`.
  - **Contiguous credentials withheld.** The same lines with
    `https://user:pass@host`, `mqtt://user:pass@host:port` or
    `user:pass@host` appended are withheld (`credential_url`,
    `connection_uri`, `user_pass_host`). The password never appears in the
    output.
  - **Labelled credentials and known secrets withheld in both forms.**
    `password=`, a split `pass word=`, `-P`, a tab-split `-P`, `Bearer`, a
    split `Be arer`, `token` and a split `to ken`, `psk:`, a known secret
    plain and split, a PEM header, and an invisible character.
  - **End to end.** The lines through a read of `addon:core_matter_server`
    come back verbatim, and every planted credential is withheld. A
    calibration of the same source counts every live-shaped line `shown`,
    and the one `credential_url` line withheld. No count names
    `user_pass_host.squashed`.
  - **Cross-path checks.** The definitions default mode, the labelled mode,
    `withhold` and `screen_trace_value` still withhold the live-shaped lines
    as `user_pass_host.squashed`. With the option on, every other rule's
    deciding case decides the same. Every HAMCP-132 and HAMCP-224 credential
    case is still withheld by the journal.
  - **Corpus report.** The committed
    `docs/evidence/hamcp234-step2/corpus-report.json` equals its
    computation, and no rule other than the squashed shape changes any
    line's decision.
  - **Served schema.** The production application (`create_application`)
    serves `tools/list` over the authenticated endpoint. Its
    `get_incident_logs` `inputSchema` has
    `calibration: {"type": "boolean", "default": false}` among exactly eight
    properties, with `additionalProperties: false`.
  - **Calibration over the wire.** A `tools/call` with `calibration: true`
    through the authenticated gateway returns `mode: calibration` and the
    per-rule counts, and no line text.
- Two edits to existing suites; every other expectation is unchanged:
  - `tests/test_hamcp234_journal_usability.py` (the step 1 suite) no longer
    expects a journal-reported `user_pass_host.squashed`. It counts 44 ids
    and checks that the shared screen still reports the squashed shape
    outside the journal.
  - `tests/test_hamcp224_review1_corrections.py`
    `test_the_journal_screen_is_the_configuration_definitions_screen` now
    expects the journal's screen call with `**_JOURNAL_SCREEN`.

  The HAMCP-132 (`test_pr294_*`), HAMCP-224 (`test_pr313_*`,
  `test_review2_ansi_residual`) and HAMCP-225 suites are byte-unchanged.
- Mutations (`docs/evidence/hamcp234-step2/`). Each mutant runs its own
  suites and counts as killed only when a named test fails:
  - `S1`-`S3`: `user_pass_host` still on the squashed form in the journal.
    The live-shaped benign tests must fail.
  - `L1`-`L4`: the relaxation leaking into the definitions or trace paths.
    Only the unchanged HAMCP-132 or HAMCP-225 suites run, and they must
    fail.
  - `C1`-`C2`: `calibration` missing from the served schema. The
    served-schema test must fail.

## 2. Release CI and publication

Require exact-head CI, both Real HA jobs included, before release
acceptance. Pending or unavailable jobs are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the merged source, the release tag, the immutable version
and commit images, provenance and the SBOM with the shipped verifiers. Never
invent future image identities. Keep the beta.42 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. **Rollback is reinstalling the 2.4.1-beta.42
artifact.**

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only. Record each result in HAMCP-234.

1. **The add-on starts after the update (first).** Confirm the Engineering
   add-on reaches `started` and its MCP endpoint answers. If it does not,
   stop and reinstall 2.4.1-beta.42.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
3. **Served schema (required).** Re-read `tools/list` with a fresh client
   session, not a cached schema. Expected: the `get_incident_logs`
   `inputSchema` lists `calibration` (boolean, default `false`). If it is
   missing, stop: steps 4 and 5 cannot run.
4. **Calibration returns counts (required).** Call `get_incident_logs` with
   `sources: ["addon:core_matter_server"]` and `calibration: true` over a
   recent window. Expected:
   - `mode: calibration`, with per-source counts only, and no entry, message,
     process or cursor anywhere in the response;
   - `rule_ids` has 44 ids and does not include `user_pass_host.squashed`;
   - `withheld_by_rule` and `matched_by_rule` contain only ids from
     `rule_ids`;
   - `scanned` equals `shown` plus `withheld_entries`.

   Record the counts beside the beta.42 baseline (40 scanned, 37 withheld,
   all `user_pass_host.squashed`).
5. **Live re-read of `addon:core_matter_server` (required).** A normal read
   of the same source over a recent window, paged to the end. Expected:
   - **most lines are readable**: more than half of the entries are shown,
     not withheld;
   - the shown matter.js lines carry their timestamps and `@peer`, session
     and exchange addresses verbatim;
   - every withheld entry's `withheld_rules` names an id from step 4's
     `rule_ids`, never `user_pass_host.squashed`, and holds no credential
     text;
   - the number of entries and withheld entries matches step 4's `scanned`
     and `withheld_entries` for the same window.

   If most lines are still withheld, record `withheld_by_rule` and stop;
   that is evidence for a separate owner decision, not a reason to widen
   this one.
6. **Supervisor unchanged.** A `calibration: true` call for
   `sources: ["supervisor"]`: record the counts. Supervisor lines withheld
   by other rules on beta.42 stay withheld (for example a labelled `token`).
7. **A credential is still withheld.** If any source's step 4 or 6 counts
   show `credential_url`, `user_pass_host`, a labelled rule or
   `known_secret`, the matching read entries stay withheld. Do not plant a
   credential in a live log to test this; that is covered offline.

## 4. Not covered

No other screening change, live canary, write, registry signing or
Supervisor role change is part of this release. A contiguous
`name:tag@sha256:…` image reference, a line with `://` and a later `@`,
and a line with a labelled keyword stay withheld. Each needs its own owner
decision. Multi-line level inheritance remains future work. A non-HAOS
Supervised host remains outside the evidence, as in beta.38.
