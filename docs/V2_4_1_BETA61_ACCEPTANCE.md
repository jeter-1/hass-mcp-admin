# Engineering 2.4.1-beta.61 acceptance

This contract covers HAMCP-253: bounded read-only inspection of one stuck
governance record, an Ingress-only administrator acknowledgement, and health
that warns only on unacknowledged records. It follows 2.4.1-beta.60 and
includes it.

See also the release notes:
[`V2_4_1_BETA61_RELEASE_NOTES.md`](V2_4_1_BETA61_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.61`, following 2.4.1-beta.60. The release
transition accepts only beta N to N+1, so 60 to 62 is refused. Draft #353
(HAMCP-285) also stages beta.61: whichever merges first keeps the number and
the other restages to the next free beta with full Evidence. The release is materialized in all three version authorities, with
no staged declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the HAMCP-253 row in [`FEATURE_READINESS_MAP.md`](FEATURE_READINESS_MAP.md)
  and the design in
  [`HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md`](HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md);
- Josh's decision (2026-11-03) that acknowledgement is an Ingress action and
  that MCP gets the read-only inspect tool only;
- the non-goals: no change to projection verification, startup validation,
  the approve, apply or recovery paths, or persisted plan and task formats;
  no fix for the prohibited-plan event allowlist (a follow-up);
- `2.4.1-beta.61` materialized before Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **New write path.** One append-only ledger file under Engineering's
  governance storage, reachable only through the Ingress acknowledge route
  behind the administrator check and the one-shot CSRF token. It has no MCP
  write tool, no Home Assistant request and no provider dispatch.
- **No execution authority.** No approve, apply, recovery or verification
  path reads the ledger.
- **Disclosure.** Inspection uses allowlisted metadata with closed tokens.
  The reason is screened by the sanitizer, and audit records only its
  length.
- **Integrity.** The hash chain is validated on every read. A damaged ledger
  makes health warn as if nothing were acknowledged and refuses new
  acknowledgements.

Required offline evidence is in `tests/test_hamcp253_*.py`:
- inspection of each failure category from real code-path fixtures, with
  their provenance, including records that the plan tools refuse and
  unreadable records;
- acknowledging a historical record: the warning clears, the counts move,
  and the record bytes are unchanged; reclassify and revoke, after which the
  warning returns; persistence across restart;
- every refusal in the design, with zero Home Assistant and provider calls
  asserted;
- redaction: a secret-like value never appears in inspection,
  acknowledgement, health, audit or the ledger;
- regression: approve and apply of an acknowledged failed plan are still
  refused;
- the Ingress routes: the administrator gate, CSRF, content type, body
  bounds and escaping;
- mutation testing in a copied tree, counting only assertion failures as
  kills and listing equivalent mutants with reasons;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

**Deploy freeze.** Nothing newer than beta.57 deploys before the HAMCP-278
cutover. This stage runs only after Josh separately authorizes deploying
this release.

The architect inspects the five live targets **read-only** with
`inspect_governance_record`:

| Kind | Record ID | Expected |
|---|---|---|
| `change_plan` | `2ab4764fe8304f429ea3b533f66caa4a` | projection failed, `approval_sequence_inconsistent` |
| `change_plan` | `faba2fbdb10c4da0b33193718636e19c` | projection failed, `approval_sequence_inconsistent` |
| `change_plan` | `4cf748d13c1e452fb510d93c8a4e3a0c` | `set_input_select_option`, prohibited, bundle `prohibited` |
| `execution_task` | `6896e7a1938144b788f29708f27ea20e` | `manual_review_required`, child verification mismatch |
| `execution_task` | `d63e3a87d7834236a585376180388609` | `manual_review_required`, dashboard update |

For each target, confirm:
- `readable: true`, or an explicit `unreadable_reason`;
- the type, status, policy class, failure code and category, and ordered
  approval events;
- `acknowledgeable` and a 64-character `record_fingerprint`;
- no payload, configuration, principal or token anywhere in the response.

Confirm that `get_server_health` shows `record_acknowledgement` with three
unacknowledged plans and two unacknowledged tasks, and still shows both
warnings.

**Acknowledgement needs Josh's explicit approval for each record.** Only
after Josh approves acknowledging a specific record, and with the
classification he chooses, does Josh (or the architect at his direction)
use the Ingress **Stuck records** page to acknowledge that one record. Then
confirm:
- the outcome `acknowledged`;
- health counts moved for that record only;
- `inspect_governance_record` shows the acknowledgement state;
- `get_change_plan` still refuses a projection-failed plan as before.

PASS needs every inspection check. Acknowledgements are optional and
per-record. If a step fails, record the response in HAMCP-253. Whether to
roll back is Josh's decision.
