# Engineering 2.4.0-beta.7 acceptance

Configuration verifier correction and supplementary configuration re-verification.
Published beta.6 base: `6e1c9f1412f97b1171bc38aafe6815ca8abe54eb`.
Accepted implementation checkpoint: `1d43fe5c712b43db6398fb53f79eb3f1209f1d78`.
Bind the release-only review, Full/Evidence and CI to the actual final candidate;
bind publication and installation separately to the protected merge and artifact.
No gate is asserted passed by this document. Prior receipts remain immutable.

## 1. Source, review and CI

All three authoritative versions must equal `2.4.0-beta.7`, staging must be
consumed, and `scripts/codex-context.py` must resolve this acceptance document
and the matching release notes exactly. Retain the accepted verifier and
supplementary capability reviews, all correction dispositions and a focused
independent release-only review. No implementation beyond that reviewed checkpoint
is implied. Exclude the separate Alarmo candidate.

Run clean final-head Full/Evidence in the existing locked environment, declaring
every changed protected path. Record exact commands, base/head, counts/skips and
all gate outcomes. An earlier 14/15 receipt with unchanged beta.6 metadata cannot
substitute for the final release gate. Run the focused command when proving the
candidate; its complete-module results within exact-head Full discovery may also
be retained, explicitly labeled as such rather than a separately executed command:

```sh
python -m unittest -v \
  tests.test_trigger_condition_verification \
  tests.test_configuration_reverification \
  tests.test_configuration_reverification_storage \
  tests.test_configuration_reverification_transport \
  tests.test_beta34_automation_verification \
  tests.test_f3_configuration_lifecycle \
  tests.test_f3_configuration_resources \
  tests.test_f3_configuration_identity \
  tests.test_f3_configuration_migration \
  tests.test_f3_configuration_sequence \
  tests.test_rc6_verification_attribution \
  tests.test_beta_observability \
  tests.test_f3_packaging_boundaries
```

| Case | Required proof |
| --- | --- |
| Trigger IDs | String/list and supported nested positions compare exactly; preserve order, duplicates, empty literals and representation. Changed guard/ID/modifier remains mismatch; malformed/unknown/wrong-subtype fields refuse. |
| Useful execution | Synthetic create/update with trigger conditions succeeds with authoritative readback and one dispatch; duplicate execution never redispatches. Unverifiable candidates refuse before that operation's approval consumption/dispatch. |
| Historical positive | Exact shipped beta.6 writer produces the retained mismatch. New reader preserves every original record byte and verifies every approved object, including successful helper children. Original task remains terminal with its failure visible; a durable dated supplement resolves only configuration review. |
| Original authority | Reconstruct the consumed original grant and bind task/declaration/child consumption witnesses, plan/hash, sequence and request identities. Authentic expired historical approvals remain readable history; tampering, missing/unknown dispatch or source drift refuse without writes to HA. |
| Negative observations | Mismatching identity/content, unavailable read, failed configuration check, stale/changed current authority, unsupported history, locks/holds and audit failure retain partial evidence truthfully and never claim resolution. |
| Replay and concurrency | Exact UUID replay performs no HA reads; different binding refuses. Pending/interrupted/fresh failed observations supersede prior resolution. Only an exact interrupted reader's complete owned lock union can be recovered; foreign locks and holds survive. No execution recovery or redispatch is added. |
| Persistence | Corruption/read failure differ from write/uncertain-commit failures. Audit-before-receipt is not atomic. Capacity with one event slot records a bound refusal; a full ledger preserves bytes, reports uncertainty and withholds local resolution. After restart a missing refusal cannot be invented. Invalid binding must not invalidate another receipt. |
| Transport and privacy | Bound reads, deadline, redirects, bodies/frames and connection retry behavior. Prove one attempt with synthetic peers; preserve ordinary client behavior outside this tool. Unknown argument names/values, raw configurations and provider exception text never leak into output/audit. Timeout and caller cancellation remain distinct. |
| Compatibility | Add one native descriptor and optional task projection only; preserve existing inputs, authority, original counters, stable-v1, installation settings, workflows, locks/registries and no-fallback boundaries. |

Preserve historical-writer/generator/hash provenance from
[`tests/fixtures/configuration_reverification/`](../tests/fixtures/configuration_reverification/README.md).
Do not manually invent persisted records or commit household data. Retain exact
Core 2026.9.3 (`6de5eb18cd4502f94af44cfff3a02250d88716ed`) schema/condition and
disposable Script evidence identified in
[the verifier contract](TRIGGER_CONDITION_VERIFICATION.md#exact-core-authority).
Reusing that evidence requires an unchanged verifier and authenticated receipts;
it is library execution, not a full HA/ha-mcp container or installed test.

Require the aggregate `validate` and every required CI family, including the
pinned disposable Core 2026.9.3 / ha-mcp 8.5.0 lane, image/build checks, exact-image
gateway and add-on runtime lanes. Verify their actual checkout/merge-tree binds
the final PR head and base. Existing disposable lanes prove their declared
scenarios, not all new synthetic receipt/fault cases. Leave the PR draft for
Josh's Ready decision. Ready may authorize protected merge/publication under
repository policy; it does not authorize installation or the live evidence write.

## 2. Publication and deployment preparation

Identify the approved protected merge, beta.7 version, both architecture artifacts,
OCI index/platform-manifest/configuration chain, SBOM, provenance and locked build
inputs. Preserve compatible recovery artifacts and current durable state through
the separately authorized private operator route. Never restore stale execution
records over newer dispatch history to satisfy acceptance.

Original plan/task/approval/child formats remain untouched. The optional new
receipt namespace must be preserved through downgrade; older binaries cannot
display it. Beta.6's expanded lock-token records are still incompatible with
beta.5 readers above sixteen tokens. Inherited ordinary recovery may settle
qualifying internal records after restart; beta.7 adds no automatic historical
re-verification. Preserve the previously accepted F027 qualification/exception;
new observations cannot reconstruct a missing original pre-state.

No backup, restart, deployment, forced recovery or live evidence call is authorized
by this document. Agree those concrete actions separately. No additional access,
options, credentials, Core update or registry activation is required by this release.

## 3. Installed identity, catalog and read continuity

After authorized deployment and bounded read authorization:

1. Match beta.7 runtime source/build/clean flag to the protected release. Bind
   the running platform/configuration digest through verified manifest bytes to
   the published OCI index. Version/source labels alone do not prove this gate.
2. Confirm REST/WebSocket agreement at Core 2026.9.3, exact admitted ha-mcp 8.5.0,
   current accepted/cache-valid signed Core authority, all nineteen compatible
   profiles and no unexplained fallback or withheld authority.
3. Record governance/task/receipt/audit health, plans/tasks/events, approvals,
   active applies/rollbacks, recovery work, locks/holds and Core leases/commits.
   Historical failure counters retain their meaning. Attribute concurrent owner
   work explicitly; do not claim the whole system was idle from one final sample.
4. Run one useful native read and one admitted delegated read against an existing,
   freshly confirmed harmless fixture. Retain attribution, completeness, truncation
   and fallback facts. No device operation or repeated old acceptance campaign.
5. Read existing successful history and dependency state. Allow ordinary prewarm;
   at most one justified `refresh_index=false` request, no forced rebuild loop.
   Partial inventory and static-analysis limitations remain explicit.

Capture a separate fresh public `initialize` → `notifications/initialized` →
all paginated `tools/list` session with **zero `tools/call`**, plus contemporaneous
runtime brackets. Compare all **80 = 55 static + 25 delegated** descriptors.
Existing descriptors must match their release baseline; the added tool has exact
task ID/hash/UUID inputs, no target/provider/configuration overrides, and annotations
`readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true`,
`openWorldHint=false`. It writes Engineering evidence; HA access is read-and-validate.
Cached inventory or runtime counts do not replace this capture. Refresh a stale
connector schema before use. Obtain a timestamped render-only observation of the
existing approval panel without creating or approving anything.

## 4. One separately approved historical configuration observation

Keep household identifiers and original receipt hashes in a private installed
acceptance record. Freshly read the intended F028 task and immutable plan; derive
the automation's internal configuration ID and helper target from those records,
not by stripping an entity prefix. Confirm the exact plan SHA-256, consumed grant,
all operation candidates/children, terminal manual-review state, exactly one
dispatch per child, at least one verification mismatch, and no uncertain dispatch
or original locks/holds. Bind the current Core authority and target state. If the
record differs or legitimate later edits exist, stop and reconcile; do not recreate
the old configuration or manufacture an eligible history.

Present the confirmed task/hash and one new canonical UUID for Josh's explicit
approval of this bounded **supplementary evidence write**. Existing consumed
approval is history, not a renewed execution grant. The proposal includes all
approved object reads and Core configuration validation; no fan command, helper
state change, plan/apply, configuration resend or restoration.

Invoke `reverify_configuration_task` once. Require:

- `current_configuration_verified`, `review_resolved=true` and
  `receipt_persisted=true`; envelope success alone is insufficient.
- Every object identity and semantic candidate match, successful configuration
  validation, unchanged original binding and exact provider/Core attribution.
- Saved receipt/audit identity, timestamps and partial/completeness facts, with
  zero configuration mutation, approval consumption, new execution/dispatch or
  fallback. Application counts are not a wire-attempt measurement.

Read `get_execution_task` once afterward. Its historical `manual_review_required`
and original error remain; `review_resolution` cites the new dated saved evidence.
All original child dispatch counts stay one. Compare original plan/task/approval
and event identities; explain permitted receipt/audit changes separately.

Stop on mismatch, authority drift, locks, uncertain read/commit or receipt/audit
failure, preserving the exact outcome. Do not retry configuration operations,
delete evidence, reopen the task or repeat requests to force a pass. A lost response
may be reconciled only through reviewed exact-request replay semantics; it cannot
authorize fresh reads or redispatch. A fresh observation needs a new UUID and a
new bounded decision. No duplicate live request is necessary to repeat offline tests.

## 5. Final settlement and verdict

Finish with no-probe health and reconcile every relevant delta: no new execution
tasks/dispatches, approvals consumed, held locks, pending applies/rollbacks,
recovery failures, fallback or active Core leases/commits attributable to acceptance.
Supplementary receipts/audit and their exact read-lock lifetime are expected;
historical task/error counters must not be reset or mislabeled as new failures.

Report source/CI, publication, installed-image binding, authority/read continuity,
raw catalog, panel, the exact configuration observation and settlement separately.
Preserve request IDs, timestamps, hashes and manifests in a new private package;
never rewrite accepted earlier evidence. Unknown/unavailable gates remain explicit.
The real F028 configuration review is resolved only after this useful installed
success, not by deploying the verifier or obtaining a green source suite. Natural
bedtime shutdown, manual-ON preservation, rearming and restart behavior remain
separate household acceptance. No physical canary is needed for this release.
