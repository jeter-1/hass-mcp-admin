# Engineering 2.4.1-beta.48 acceptance

This contract covers HAMCP-248, the baseline capture's final fence failing
with `malformed_response` on every full capture. It follows 2.4.1-beta.47
and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA48_RELEASE_NOTES.md`](V2_4_1_BETA48_RELEASE_NOTES.md).
- The contract this release corrects:
  [`AUTOMATION_BASELINE_CAPTURE.md`](AUTOMATION_BASELINE_CAPTURE.md).

**Release target.** `2.4.1-beta.48` is the next free beta after
2.4.1-beta.47 (#327, HAMCP-249). That PR was open when this change was
prepared, and `main` was at 2.4.1-beta.46. The canonical transition accepts
only beta.47 → beta.48, so this branch merges `main` after #327 and then
materializes beta.48 with `scripts/promote_next_release.py --apply`. If
another release reaches `main` first, this change is restaged at the next
free beta and these documents are renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-248 scope:
  - the root fix of the final fence;
  - a distinct reason for each final-fence failure class;
  - the compare-row reason correction;
  - the living capture documentation;
- that nothing else changes: the budget, concurrency, reserve, resume, D4,
  the stored format and every other tool;
- `2.4.1-beta.48` materialized in all three version authorities, with exact
  document resolution and no staged declaration left over;
- the PR head SHA, its passing `validate` run, and the Evidence tier log
  named in the PR;
- the mutation evidence under `docs/evidence/hamcp248-mutations/`.

The review needs a security section covering the following.

**No new reachability.**
- No Core command, REST path, new Home Assistant application-message type,
  registry entry, provider, route, write or fallback is added.
- The only new frame the capture sends is a websocket PONG control frame.
  It echoes only the peer-supplied PING payload, which is not inspected:
  the echo is bounded by the control-frame limit (125 bytes), not proven
  free of arbitrary text.
- The bounded control handling applies to every capture websocket read,
  including the handshake reads before authentication completes. A PING
  that precedes `auth_required` is answered before the credential is sent;
  a fifth refuses the capture without sending it. A PONG authorizes no Home
  Assistant command.

**Bounded.**
- Control frames (PING, PONG) only, at most four per read, inside that
  read's deadline, which they do not reset; the fifth is refused before it
  is answered.
- Their bytes are charged to the shared capture byte budget.
- No background task, retry or reconnect. The frame size bound,
  `TOTAL_BYTES`, the 45 s collection bound and the 5 s read bound are
  unchanged.

**Fail-closed.**
- Every other non-TEXT frame is still refused, now with its own fixed,
  content-free reason (`connection_closed`, `transport_error`,
  `unexpected_frame`, `protocol_error`, `control_frame_limit`,
  `response_limit`). Public error codes and retryability are those of the
  reason each replaced.
- Identity drift, authority loss, access denial and an unverifiable identity
  still refuse the capture with nothing stored.

**Owner decision (Josh, 2026-10-29): replaces "Unexpected/control frames
are refused, not automatically ponged".** That rule came from the original
beta.14 capture (PR #228, commit `ed51a55d`) and was preserved by
HAMCP-228. The bounded control-frame rule above replaces it for PING and
PONG only. The reviewer should confirm that answering a PING sends no Home
Assistant data and does not change what is read.

### Offline proof

No test reaches a live system. Every value is synthetic.

`tests/test_hamcp248_final_fence.py`:
- **Post-collection condition reproduced.** A loopback Home Assistant, used
  through the real capture client and provider, sends a websocket PING while
  116 configurations are collected. Expected:
  - the final fence is `complete` and the installation `established`;
  - five after-fence reads and 127 requests;
  - the PING is answered with its payload **after** the final fence's
    principal command is sent, before its result is accepted, and the
    client sends nothing else (the exact event order is asserted).

  Against the pre-fix client, this test fails with `unavailable` /
  `malformed_response`, the live result.
- **The resume path is unchanged** over the real transport. Only the
  missing record is read, and both fences complete.
- **Each frame class has its own reason** (review 1, R1-M1), injected one
  at a time and checked to be distinct:
  - a server close (with any close code) or an aborted connection is
    `connection_closed`; a local CLOSING or CLOSED is too;
  - a non-protocol ERROR, or a failure sending the PONG, is
    `transport_error`; a timeout or cancellation while sending it is not;
  - a binary or CONTINUATION frame is `unexpected_frame`;
  - a websocket protocol error, including a TEXT frame that is not UTF-8,
    is `protocol_error`;
  - a fifth keepalive frame is `control_frame_limit`, refused before it
    is answered;
  - an over-bound frame is `response_limit`;
  - a silent read is `timeout`;
  - invalid JSON, the only `malformed_response`;
  - a cancellation propagates.

  No reason reflects close text, a frame payload or exception text. The new
  reasons keep their former public error code and retryability.
- **Final-fence read-back.** A real loopback full capture whose final
  principal read gets each injected outcome stores `unavailable` with that
  reason (distinct per outcome), the installation `unestablished`, and no
  synthetic secret. A cancelled final fence propagates with nothing stored.
- **Handshake keepalive.** A PING before `auth_required` is answered
  before the credential is sent (exact event order), and a pre-auth flood
  is `control_frame_limit` with no credential sent.
- **The keepalive bound** (4), its byte charge, and the 45 s and 5 s
  bounds.
- **Final-fence reasons.** A failed final principal read is `unavailable`
  with its own reason, and a failed final states or registry read is
  `identity_only` with the installation established. Each reason is
  checked: `timeout`, `response_limit`, `source_unavailable`,
  `malformed_response`, `structural_limit` and the five new frame reasons.
  Identity and authority refusals (`access_denied`, `authority_unavailable`,
  `authority_drift`, `identity_unverified`, `identity_drift`) still refuse
  the capture with nothing stored.
- **Compare rows.** A missing earlier or later digest gives only the
  missing-digest, unreadable and coverage reasons, never
  `record_fingerprint_model_mismatch`. Two digests of different models
  remain a mismatch. In a recorded-fingerprint compare, a capture row
  without a digest gives no per-row model claim. A different caller model
  still marks the row.

The HAMCP-235 suites stay green unchanged: 115/115 coverage, the reserve
bounds, the D4 refusals and beta.43 compatibility. So do the capture,
transport, store and comparator suites.

## 2. Release CI

The exact materialized head's `validate` check passes, both Real HA jobs
included. Pending or unavailable jobs are not passing evidence.

## 3. Publication

Only through Josh's Ready action and the repository's head-bound workflow.
Keep the beta.47 artifacts for recovery. **Rollback is reinstalling
2.4.1-beta.47.**

## 4. Installed Engineering acceptance (separately authorized)

Every step after step 1 is read-only to Home Assistant. Record each result
in HAMCP-248. The architect's live re-run (steps 3 to 5) is required.

1. **The add-on starts.** The Engineering add-on reaches `started` and its
   MCP endpoint answers. If not, stop and reinstall 2.4.1-beta.47.
2. **Installed-image binding and schema.** Bind the running add-on to the
   published image, tag and `main` merge commit. Re-read `tools/list` with a
   fresh client session: the tool count and input schemas are unchanged.
3. **Two full captures (required).** Run `capture_automation_baseline`
   twice, a few seconds apart, with no automation edited between them.
   Record for each:
   - `installation.status`;
   - `diagnostics.final_fence` and `final_fence_reason`;
   - `final_fence_reserve_seconds` and `requests`;
   - `configuration_incomplete_count`;
   - `consistency.inventory_drift`.

   Expected: **both establish the installation.** `final_fence` is
   `complete`, or `identity_only` with an established installation and a
   recorded reason other than `malformed_response`. With nothing
   incomplete, `requests` is 127 on 116 automations. A final fence that is
   `unavailable`, or any `malformed_response`, fails this step: stop and
   record it.
4. **Compare (required).** Run `compare_automation_baselines` on the two
   captures and page to the end. Expected:
   - every automation whose digests are present on both sides is
     `UNCHANGED`;
   - the caveats exclude `installation_identity_unestablished`;
   - each `UNKNOWN` row names only its true reasons. A row with a missing
     digest does not also list `record_fingerprint_model_mismatch`.
5. **One resume, compared with a capture taken after it (required).**
   1. Resume (`resume_capture_id`) a step 3 capture that has a timed-out
      record, a few minutes later, with no automation added, removed or
      re-mapped. Expected: the resume succeeds, `resumed_from` names the
      base, the final fence is `complete` and the installation is
      established.
   2. **After the resume returns**, take one new full capture (step 3's
      expectations apply).
   3. Compare the resume (earlier) with that new capture (later).
      Expected: `UNCHANGED` rows with the caveats, and no
      `capture_intervals_not_ordered`.

   Do **not** compare the resume with the other step 3 capture. The resume
   inherits its base's start time, so a capture taken between the base and
   the resume overlaps it. That compare correctly yields
   `capture_intervals_not_ordered`. If neither step 3 capture has a
   timed-out record, the resume is refused as `resume_not_needed`. In that
   case, record that sub-steps 1 to 3 could not be exercised; no other
   resume base is substituted.

## 5. Not covered

This release includes no new Core command, live canary, write, registry
signing or Supervisor role change. Short-lived websockets elsewhere in the
add-on (dashboard analysis, generic websocket reads) keep their frame
handling. They do not idle for the proxy's 30 s heartbeat. A non-HAOS
Supervised host, and the owner's exact Supervisor version, remain outside
the evidence.
