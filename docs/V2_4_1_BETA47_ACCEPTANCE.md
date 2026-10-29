# Engineering 2.4.1-beta.47 acceptance

Beta 47 stages HAMCP-249. Engineering continues to advertise 2.4.1-beta.45
on this branch; `.release/next-version` declares `2.4.1-beta.47` and is not
materialized.

See also:
- Release notes:
  [`V2_4_1_BETA47_RELEASE_NOTES.md`](V2_4_1_BETA47_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.47` is materialized by one owner-authorized
run of `scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.46 (HAMCP-247, #325). Merge `origin/main` in first (never
rebase) and rerun Evidence. If another release merges first, restage at the
next free version and rename these documents.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-249 direction: every pre-dispatch refusal records a
  closed-vocabulary reason; reporting only; additive schema; legacy records
  read `unknown_legacy`;
- `2.4.1-beta.47` materialized in all three version authorities, with exact
  document resolution and no staged declaration left over;
- the PR head SHA, its passing `validate` run, and the Evidence tier log
  named in the PR;
- the mutation evidence under `docs/evidence/hamcp249-mutations/`.

The review needs a security section covering:
- **No new reachability.** No Home Assistant write, service call, reload,
  provider, route, fallback or upstream dispatch. No preflight check,
  approval binding, lock or at-most-once guard changes.
- **Bounded, sanitized output.** `error_code` and `check` come from fixed
  tables; `detail` is fixed text; identifiers are printable ASCII and at
  most 160 characters; the verifier rule comes from the verifier's fixed
  literal messages. No Home Assistant payload, exception text or
  configuration content is stored.
- **Persistence.** Task storage stays append-only; terminal recovery keeps a
  stored task exactly as stored. A stored reason is accepted back only in
  its reviewed shape.

Required offline evidence:
- `tests/test_hamcp249_pre_dispatch_reasons.py` (table of refusal paths,
  every surface, sanitization, legacy `unknown_legacy` and derived reasons,
  terminal recovery, legacy F1, positive apply);
- the existing F3 runtime, configuration execution and verification suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes.

## 3. Publication

Only through Josh's Ready action and the repository's head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

1. `get_execution_task` on one of the seven 2026-10-28 tasks (for example
   `9977ce7a2b7242c083d2798fdfbd826f`) shows `pre_dispatch_refusal` derived
   from its child's stored codes, or `unknown_legacy`; nothing is rewritten.
2. One owner-approved retry of a previously rejected plan shape (a fresh
   plan, approved by Josh). Accept either:
   - it applies and reads back exactly; or
   - it is refused with a named, actionable reason: non-null `error_code`
     and `check` on the apply result, `last_error`, the `preflight_failed`
     event and the add-on log line, plus `verifier_rule` when the check is
     `readback_verifiability`.
3. The named reason is recorded in HAMCP-249. Whether that check is a
   legitimate safety check is Josh's decision; this release does not change
   it.
