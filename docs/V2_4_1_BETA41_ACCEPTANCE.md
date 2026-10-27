# Engineering 2.4.1-beta.41 acceptance

This contract covers the November Core batch built **inactive**: governed
helper options edits (HAMCP-232), governed input_select option selection
(HAMCP-145) and the owner-added script-trace reads. It follows
2.4.1-beta.40 and includes it. The release **activates nothing new**: each new
family refuses `pending_signed_registry_admission` until an owner-signed Core
registry sequence names its profile. It changes no signed registry. Its base
carries the owner-signed Core registry at sequence 14 (HAMCP-222), which names
none of the three new profiles.

See also:
- Release notes:
  [`V2_4_1_BETA41_RELEASE_NOTES.md`](V2_4_1_BETA41_RELEASE_NOTES.md).
- Design: [`HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md`](HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md)
  (owner decisions section 0, script-trace profile section 4b, Part 2 build
  section 6).
- The beta.40 contract this release keeps:
  [`V2_4_1_BETA40_ACCEPTANCE.md`](V2_4_1_BETA40_ACCEPTANCE.md).

**Release target.** `2.4.1-beta.41` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.40. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-232/145 decisions OD-1 to OD-15 and the script-trace addition
  (design section 0);
- `2.4.1-beta.41` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR;
- the three runtime-only profile fingerprints, recomputed at the review head:

  | Profile | sha256 |
  |---|---|
  | `core.config_entry_helper_options_operation` | `ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8` |
  | `core.typed_input_select_option_operation` | `e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d` |
  | `core.script_trace_read` | `749904fe40b13db4289301d24b958836fc72c810829611729d8c26080f06ca03` |

The review needs a security section covering:
- **New reachability, gated.** New write paths: one options-flow submit per
  child (`POST /config/config_entries/options/flow/{flow_id}`) and one
  `input_select.select_option` WebSocket call. New reads: options-flow open
  and abort for the plan's exact entries only (OD-1), exact
  `GET /states/input_select.*`, and `trace/list` / `trace/get` with
  `domain: script`. Each route is method-bound and reachable only from its
  new gateway; the flow id comes from Core's own response and is validated
  as 32 lowercase hex.
- **Admission first.** Every entry point, including the lowest builder and
  the generic `create_configuration_plan` and resource-write routes, refuses
  before any read, validation read or persistence, and again at the persist
  boundary. Every F3 path (first apply, retained approved plan, resume,
  recovery, read-only reconciliation) re-checks dispatch admission; the
  input_select operational plan gets the new operational admission seam, and
  input_boolean is unchanged. Without a Core runtime the result is
  `core_authority_unavailable`.
- **Closed vocabularies.** Each gateway refuses anything outside its command
  list before I/O, and has an explicit never-sent list: `set_options`,
  relative option moves, every other service, `trace/contexts`, every
  `trace/debug/*` command and the list-all trace form. No fallback to a
  delegated tool or direct Home Assistant call.
- **No blind redispatch.** A held flow is submitted exactly once;
  `mark_provider_send()` marks the exact send boundary; a Core error after
  the input_select send is decided by readback; a submit validation error,
  abort, 404 or timeout is never redispatched.
- **Drift refused at dispatch.** `stale_target_state` when a suggested
  value, `modified_at`, the options list or the current option no longer
  equals the plan. The residual window between the final read and the submit
  is accepted (OD-7) and disclosed in the plan and in the apply response's
  embedded plan.
- **Sanitized trace reads.** Both script-trace results, with the identifiers
  they are returned under, leave through the one HAMCP-225 trace exit
  (`trace_normalization.sanitize_trace_detail`, review 1 H1): the shared
  `credentials.screen` first, then the strict sanitizer, truncation last;
  both call sites are in the HAMCP-191 sanitizer-site inventory.
- **Review 1 corrections.** H2 (no options flow left open, cancellation
  included), H3 (complete intended submit at create and apply), M1 (retained
  pre-intent tasks admitted before persistence) and M2 (disabled triggers
  disclosed as disabled; enablement in the binding fingerprint, so a toggle
  between plan and apply refuses as stale). The H1, H2 and H3 contracts are
  part of the profile material, so two fingerprints above changed at review 1
  (helper options `581234de…5b9e`, script trace `abf10d9b…5789`);
  input_select is unchanged.
- **Correction 2 (H2-R1).** Each options flow's open intent is recorded
  durably before the open, and its flow id before any abort; each recovery
  sweep retries a failed or unknown abort, at most 10 times, until Core
  confirms the flow gone, then reports it orphaned in health. The retry is
  the reviewed DELETE, admitted, in an exact-target Core window. No profile
  command changed, so no fingerprint changed (helper options stays
  `ce2765dc…d98d8`; signed profiles match 22/26/26).
- **Accepted residual (owner decision (b)).** Pinned Core 2026.10.1 has no
  read-only command that lists in-progress options flows:
  `config_entries.py:384-404` (`flow/progress`) and `:407-466`
  (`flow/subscribe`) list config flows only, the options views
  (`:277-318`, `helpers/data_entry_flow.py:104` and `:131`) need a known
  flow id, and `data_entry_flow.py` `async_progress*` is Python-only. A flow
  whose open answer was lost is therefore reported, in server health
  (`helper_options_flows`) and in the task when one exists
  (`options_flow_open_unconfirmed`), as "options flow possibly left open on
  entry X (no flow id received); it holds no configuration change and is
  discarded at the next Home Assistant restart".
- **Dormant lanes.** The seq-15 lanes run only with
  `REAL_HA_SEQ15_LANES` explicitly enabled on the candidate support slot;
  the harness sender is loopback-only and refuses routes outside its closed
  pattern before any request.

Preserve the following:
- stable v1.1.2;
- persisted formats of every existing family (new enum values are written
  only after signed admission);
- every existing descriptor and input schema, including
  `create_helper_state_plan`; static tools **76** (72 + 4) on the beta.39
  base, **77** once beta.40 merges in; the catalog is **104 tools, 105 with
  the optional YAML reader admitted** (105 and 106 with beta.40);
- probe fingerprints, the signed Core capability profiles, the owner-signed
  Core registry (sequence 14 at base, unchanged), the signed ha-mcp registry
  and the delete profile's contract fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`;
- the HOLD and the support window;
- add-on permissions and dependencies; `ci.yml` changes are additive and
  dormant only (OD-14), and every other workflow is unchanged.

### Offline proof

No test reaches a live system. Every transport is a synthetic fake that
enforces the real clients' Core-authority check
(`tests/core_authority_fakes.py`).

- `tests/test_hamcp232_helper_options.py` with
  `tests/hamcp232_helper_options_fixtures.py`: admission at every entry and
  the persist boundary, planning through one aborted flow, the per-type
  field rules, defaults disclosed separately, protected and elevated
  classification, apply, verify, separately approved rollback, drift, the
  held flow, orphan disclosure, submit errors without redispatch, and the
  OD-7 disclosure on the apply response.
- `tests/test_hamcp145_input_select_option.py` with
  `tests/hamcp145_input_select_fixtures.py`: admission, per-option trigger
  disclosure, apply, verify, rollback, drift, an option not in the list, lost
  responses and Core errors decided by readback, and input_boolean unchanged.
- `tests/test_hamcp232_script_traces.py` and
  `tests/test_hamcp232_script_trace_cross_path.py`: pending admission with
  zero requests, the closed vocabulary, the shared trace exit on both script
  and automation paths, and not-found.
- `tests/test_pr314_independent_review.py`: the reviewer's suite, byte for
  byte (sha256 `b5f8dc94ea9114518f2fedcd44ad303764436b8f038b5f61dd5ab375e41b341b`).
  `tests/test_hamcp232_review1_superseded.py` pins that hash and registers
  `FlowReview.test_partial_internal_intent_never_submitted` as the one
  expected failure (owner decision: create refuses an incomplete intent).
  It proves no other reviewer method is an expected failure and that the
  superseded one still runs and fails at the create refusal.
- `tests/test_pr314_delta_cleanup.py`: the reviewer's delta cleanup probes,
  byte for byte (sha256
  `e29c568e691e5634cfbd98266cabc5dce7f7fb32cd58e377991e7aaf2a78cfd8`).
  `tests/test_pr314_delta_cleanup_accepted.py` pins that hash and registers
  the two lost-open probes as the only expected failures (owner decision
  (b)); it proves they still fail only at the surviving-flow assertion and
  that the failed-abort probe passes.
- `tests/test_hamcp232_options_flow_ledger.py`: the record before the open
  and before the abort, restart survival, bounded retries to orphaned with
  the health disclosure, confirmation by abort or 404, inert sweeps (no
  record, no admission, a live flow), a refused release handed to the
  sweep, possibly-open disclosure in each phase, refusal with nothing sent
  when the ledger is missing, corrupt or full, eviction, and strict storage
  validation.
- `tests/test_seq15_contract_acceptance.py`: lane definitions, receipts and
  their mutations, fixture cleanup, the loopback route gate, dormancy and the
  lane guard.
- Pin updates across the suite (tool counts, F3 identities 15 → 17,
  capability metadata, allowlists, route templates).
- Mutations: `docs/evidence/hamcp232-mutations/`. `results.json` records each
  mutant and the tests that killed it.

### Downgrade rule

Rollback is reinstalling **2.4.1-beta.40**. Under the current signed
registry (sequence 14) beta.41 writes no new record shape, because every new
family refuses before persistence, and the options-flow ledger is written
only while an admitted flow is open or unconfirmed. After a future signed sequence admits a
family, a downgrade below beta.41 must be re-evaluated before it is
supported; that is not covered here. Never clear F3 journals, task files or
locks.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence. The dormant seq-15 lane steps are expected to be
skipped.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.40 artifacts and the known-good earlier
artifacts for recovery.

## 3. Installed Engineering

Separately authorize installation. All checks are read-only.

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge commit.
2. **Catalog.** `tools/list` shows the materialized release's catalog (104
   tools, 105 with the YAML reader admitted, on the beta.39 base; one more
   each with beta.40), including the four new tools.
3. **Pending admission.** `list_capabilities` lists each new tool with its
   new profile in `core_capabilities` and a `core_admission` other than
   `admitted`, because sequence 14 names none of the three profiles. This
   check is read-only and makes no Core call; it does not invoke the tools.

The post-signing canaries (design section 6.6) are defined here and **not
run** under this release:
1. Edit a harmless filter helper's `window_size`, verify, then roll back to
   the captured options and verify.
2. Select a different option on a harmless input_select, verify, then roll
   back and verify.

They require a signed sequence that admits the profiles and Josh's separate
approval.

## 4. Out of scope

Registry signing (sequence 15 or 16), enabling the seq-15 lanes, the
post-signing canaries, HAMCP-233, utility_meter options and any downgrade are
not authorized by this document.
