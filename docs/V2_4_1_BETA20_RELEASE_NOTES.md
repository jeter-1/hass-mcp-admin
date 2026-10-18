# Engineering 2.4.1-beta.20 release notes

Engineering 2.4.1-beta.20 gives every tool one shared sanitizer with
identity-only redaction markers (HAMCP-191).
- It closes the P1 and P2 leaks: a known secret wrapped in a forged marker,
  and a credential anchor split from its value by one.
- It applies Josh's decisions D1–D11 to the design in
  `docs/HAMCP_191_SHARED_SANITIZER_DESIGN.md` (PR #271).
- How the implementation maps to that design, and every addition to it, is
  in [`HAMCP_191_IMPLEMENTATION_NOTES.md`](HAMCP_191_IMPLEMENTATION_NOTES.md).

It builds on beta.19 (HAMCP-192).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, workflow or stable-v1 change is included. The tool catalog is
unchanged from beta.19. No new dispatch, service-call or fallback path is
reachable. Acceptance:
[`V2_4_1_BETA20_ACCEPTANCE.md`](V2_4_1_BETA20_ACCEPTANCE.md).

**What was wrong (beta.19 and earlier).** Most callers kept any text shaped
like `[REDACTED:<category>]` as if Engineering had written it.
- **P1.** A secret wrapped in forged marker text could pass through.
- **P2.** A forged marker between a credential anchor (for example `Bearer`)
  and its value broke the pattern match, so the value passed through.
- HAMCP-162 added a strict mode for the YAML reader only. Other tools,
  including the generic delegated read, dashboards, logs, analyses, plan
  views and the audit sink, used the tolerant mode.
- A redacted delegated read could still report `complete` (Finding 11).

## Changes

1. **Strict mode is the only mode (D1, D2).**
   - Every sanitizer caller is strict, and `identity_markers_only=False`
     raises.
   - Every rule runs first. Then any string that still looks like a marker,
     including keys and encoded, folded or full-width spellings, is replaced
     as a whole by `[REDACTED:redaction_marker_collision]`.
2. **A per-request identity record (D4).**
   - Markers that Engineering itself produced in the same request are kept,
     checked by object identity plus the producing pass's known secrets.
     They include the approval review, plan views, impact, integrity and
     reliability analyses, gateway-withheld evidence and the audit sink.
   - A byte-identical copy that arrived from upstream does not match the
     record, so it collides.
   - Each request gets a new record. A dependency snapshot's record is
     inherited read-only by the analyses that use it.
3. **Stored plans keep beta.19's tolerance (D11).**
   - Re-reading a persisted plan (approval review, plan views, health
     verdicts) still keeps canonical markers in it, and scans the text
     between them with every rule.
   - This is the one explicit exception. It is reached only through
     `rescan_stored_plan`, from six allow-listed call sites, and is tested.
   - Write time is strict, so no new plan can store a forged marker.
     Removing the exception is HAMCP-207.
4. **Health counters (D3).**
   - `get_server_health` adds `sanitization` with `strict_marker_mode: true`,
     `marker_collisions` and `marker_collision_refusals`. These are process
     counts with no content.
   - Look-alike text can now block a governed edit whose path has a detector
     gate. Each such refusal is counted.
5. **Truthful completeness (Finding 11).** When redaction applied, the
   generic delegated read, the held-read canary, the YAML reader and
   dashboard get/list report:
   - `completeness: partial`;
   - a bounded `metadata.redaction` summary (categories with counts);
   - for the delegated read, the warning "Sensitive values in the untrusted
     upstream response were redacted; the result is not complete."
6. **Smaller fixes.**
   - The raw-key failure branch scans the key or withholds it; it never
     writes the raw key back.
   - The evidence-truncation metric counts from a structural flag, not from
     searching the text (X1).
   - Incident correlation shows `(authorization header withheld)` instead of
     a marker-shaped placeholder (X2).
   - HAMCP-162's look-alike regex was replaced by an equivalent linear scan.

## Visible changes

- Ordinary data is unchanged. Of 61 corpus samples recorded on the exact
  beta.19 base, 50 are byte-identical and 11 change by decision:
  - P1 ×2 and P2 ×2: the base leaked; beta.20 does not;
  - forged markers ×4 (D1);
  - a forged marker beside text (D2);
  - look-alike prose ×2 (D3).
- A Core log download is sanitized as one string. So one marker-like line
  replaces that whole window with the collision marker (D2). No secret is
  shown, and the collision is counted.
- A collided system-log or trace entry no longer correlates in reliability
  analysis.

## Compatibility and rollback

- **Stored formats** are unchanged. Plans written by beta.19 re-read exactly
  as before (D11).
- **Responses** gain only the additive health `sanitization` object and
  `metadata.redaction`. Redacted reads say `partial`. Look-alike untrusted
  text becomes the collision marker.
- **Rollback** is reinstalling the beta.19 artifact, which reads every record
  this release writes. Rolling back restores the P1 and P2 leaks.
