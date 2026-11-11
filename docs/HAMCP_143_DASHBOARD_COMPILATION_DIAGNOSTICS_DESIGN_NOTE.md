# HAMCP-143 dashboard patch compilation diagnostics (2.6.0 design note)

Design only. No runtime, test, schema, version or protected-path change is made
by this note. Base: `origin/release/2.6.0` =
`7246f2d21693f039504542574569f03b71476489`. All `file:line` references are at
that base; runtime paths are under `hass_mcp_engineering_beta/ha_mcp_engineering/`.

## 0. Disposition (2026-11-08): historical baseline, current scope, deferred options

- **Historical baseline.** Sections 1-9 were first written at design base `7246f2d2`, before the row was reopened.
  The broad five-category proposal and the closure question in that draft are superseded by the narrowed scope
  below. Superseded text is kept only under "Superseded (historical)" labels.
- **Current scope (register HAMCP-143, Next Action as of 2026-11-08).** The row is *reopened and narrowed*
  (2026-11-07). Codex confirmed that PR #213 delivers the semantic-leaf limit diagnostic, but not clear diagnostics
  for every compiler limit. The remaining scope is a clear refusal diagnostic for each remaining dashboard
  compiler limit: operation count, byte bounds, pointer and JSON depth, node counts, and traversal and growth
  bounds. The live limits stay **256 semantic leaves and 16 patch operations**, and neither changes.
- **Built (2026-11-09 record).** The narrowed scope is built and merged into `release/2.6.0` as
  [PR #412](https://github.com/jeter-1/hass-mcp-admin/pull/412): head `583f063fdd1d688bde593ad174cd178059c9615b`,
  merge commit `50e127e5fd3fef0e22036082f440d29c50e91ffc`, merged 2026-11-08 2:13 PM CST.
  `git merge-base --is-ancestor 50e127e5 origin/release/2.6.0` passes. It is not on `main` (the same check
  against `origin/main` fails). This note claims no release, deployment or live acceptance for it. Superseded
  (historical): "Status: Backlog, a later 2.6 wave (wave 2 in the orchestrator's 2.6 plan), nothing built. No
  delivery is claimed."
- **Not reopened.** Whether to close HAMCP-143 is not a question any more; the row answers it. The question is
  removed from section 9.
- **Decided: not expanded.** Josh decision (in-session 2026-11-08), item 23: HAMCP-143 is not expanded beyond the
  narrowed compiler-limit diagnostics. These are not in scope:
  - categorizing the non-limit exits (`malformed_operation`, `unsupported_operation`, `target_mismatch`,
    `internal_failure`);
  - per-operation capacity enrichment;
  - path echo.
  The build emits only `category="capacity_exceeded"` on limit exits, indices and tokens only (section 9).
  Superseded (historical): "Deferred, optional. These are optional future scope".

## 1. Register outcome and binding decisions

> A dashboard change that exceeds compiler limits returns a bounded, actionable
> diagnostic that distinguishes capacity from malformed input or another
> compilation failure without weakening approval, readback, or provider
> protections. (HAMCP-143 Outcome)

Binding constraints (HAMCP-143 Notes and the 2026-11-07 2.6.0 wave-1 brief): no
capacity change; no weakening of shifted-array accounting, approval projection,
stale-state refusal, readback or one-dispatch behavior.

**Stale premise (stop condition, reported).** The row (updated 2026-09-23) and
the task prompt describe a 16-leaf capacity and say the four-operation fixture
(54 leaves) gets only `dashboard_patch_compilation_failed`. Current source
differs. On 2026-09-25 Josh chose **256 leaves plus diagnostics**. That decision
is recorded in
[F3_DASHBOARD_WRITE_CONTRACT.md](F3_DASHBOARD_WRITE_CONTRACT.md#semantic-capacity-and-safe-diagnostics)
and [2_1_ROADMAP.md](2_1_ROADMAP.md), and was implemented in `d861d9f9`
(PR #213, merged 2026-09-25):
- `MAX_SEMANTIC_LEAF_CHANGES = 256` (`f3_dashboard/constants.py:56`).
- The 54-leaf fixture now compiles and applies (`tests/test_f3_dashboard_patch.py:389-403`,
  `tests/test_dashboard_update_mvp.py:704`).
- An over-capacity proposal already returns `constraint`/`stage`/`observed`/`limit`
  (`governance/service.py:5604-5605`, `tests/test_dashboard_update_mvp.py:781-812`).

This note binds to the **current 256 capacity** and changes it in neither
direction. The narrowed remaining scope (section 0) gives every other compiler *limit* the same
bounded diagnostic, and no more (Josh decision (in-session 2026-11-08), item 23). Superseded (historical): the first draft designed full
categorization of malformed and other failures and asked in section 9 whether that
remainder was still wanted. The reopened row now answers that.

## 2. Current state at base

- **Pipeline (corrected, Codex F3).** `create_dashboard_update_plan` (`governance/service.py:5583-5609`)
  enters `f3_dashboard/planning.py`. That module first awaits the admitted, bounded provider preread
  (`reader.preread`, `planning.py:108`). It then compiles against the preread configuration at `planning.py:143`,
  which calls `compile_dashboard_patch` (`f3_dashboard/patch.py:373-423`). Setter provider admission
  (`admit_provider_contract`) comes after that (`planning.py:158-195`). The compiler is the only caller of
  `canonicalize_patch` (`patch.py:138-161`). So compilation runs **after one provider read** and before any plan,
  artifact, task, lock, setter admission or provider mutation. A compilation failure creates no plan, task,
  artifact or provider mutation, and the admitted preread already done stays as it is
  (`tests/test_dashboard_update_mvp.py:804-808`: no plan, no task, `write_count == 0`,
  `best_practice_count == 0`, configuration unchanged). Superseded (historical): the first draft said
  compilation runs "before any … provider call", which wrongly treats no mutation as no read.
- **Error model.** `DashboardFoundationError` (`f3_dashboard/errors.py:11-66`)
  already carries optional `constraint` and `stage` tokens (`^[a-z][a-z0-9_]{0,63}$`)
  and non-negative `observed`/`limit` integers. Its `diagnostic_details()`
  returns only those fields.
  - `PatchValidationError` has code `invalid_dashboard_patch` (`errors.py:77-78`).
  - `PatchCompilationError` has code `dashboard_patch_compilation_failed`
    (`errors.py:81-82`).
- **Public projection.** `service.py:5602-5609` maps every
  `DashboardFoundationError` to `CONFIGURATION_VALIDATION_FAILED` with
  `{"reason": exc.code}`. The single exception is `semantic_leaf_changes` at stage
  `compilation`, which gets the full `diagnostic_details()`.
  `tests/test_dashboard_update_mvp.py:814-823` pins this legacy shape for any other
  compiler error. The contract doc says "Other compilation failures retain their
  prior error details" (`F3_DASHBOARD_WRITE_CONTRACT.md:134-135`).
- **Leaf accounting.** `_leaf_weight` (`patch.py:224-238`) and
  `semantic_leaf_difference` (`patch.py:241-280`) do the counting. An array
  insert or remove that is not at the end counts the whole displaced suffix
  (`patch.py:300-312`, `345-358`). That is why one insert can cost 40 leaves.
  The capacity check runs only after every operation applies (`patch.py:391-399`).
- **Failure exits with no sub-category today.** Each reaches the client only as a
  bare `reason`:

| Stage | Exits (patch.py unless noted) | Today's `reason` |
| --- | --- | --- |
| validation | list shape 142; op count 145; unknown/incomplete fields 103/105; op id 110; op kind 114; value presence 119/121; value bytes 126; value JSON `json_codec.py:32-56`; duplicate id/path 149/152; parent/child conflict 157; patch bytes 160; pointer 51/67/69/72/75/81/94/97 | `invalid_dashboard_patch` |
| compilation | list index 180/182/184/187; parent missing 198; parent not collection 204/221; append misuse 208/213; add-on-present 294; array add without list 314; replace/remove absent 329; traversal bound 229/254 | `dashboard_patch_compilation_failed` |
| compilation (as validation class) | equality traversal bound `json_codec.py:95/97`, raised from inside the compiler | `invalid_dashboard_patch` |
| compilation | config not object 380; input mutated 390; result bytes 404; growth bytes 406 | `dashboard_patch_compilation_failed` |
| compilation | semantic leaves 392 | full details (already shipped) |

- **What the client can and cannot do.** A malformed pointer looks the same as a
  path that no longer exists in the current dashboard, and the same as a
  growth-bound overrun. The client cannot tell which of its 16 operations failed.

## 3. Proposed change

Every change is additive and pre-dispatch. Exception classes, `code`, `reason`,
`ErrorCode`, check order and every bound (256 leaves, 16 operations and the rest)
stay exactly as they are.

**Narrowed contract (current scope).** Only the `capacity_exceeded` row of the table in
item 2 is in scope. Every compiler *limit* exit carries `category="capacity_exceeded"`
plus its `constraint`, `stage`, `observed` and `limit`, and where they are known
`operation_index` and `token_index`. `semantic_leaf_changes` keeps its shipped
five-field shape and gains only `category`. Every non-limit exit keeps the legacy
`{"reason": code}` projection. The other category rows, item 4, and path echo are
not in scope (Josh decision (in-session 2026-11-08), item 23; section 9).

1. **Error model** (`f3_dashboard/errors.py`). Add three optional fields to
   `DashboardFoundationError`, each emitted by `diagnostic_details()` only when set:
   - `category`: a token from a fixed enum.
   - `operation_index`: an int, 0-based, in caller order.
   - `token_index`: an int, a 0-based pointer segment.

   Validation reuses `_token`/`_count`. Add a closed allow-list for `category`
   and `constraint`; an unknown value raises at construction (fail closed). Under
   item 23 the `category` allow-list is exactly `{"capacity_exceeded"}` and the
   `constraint` allow-list is exactly that row's tokens.
2. **Category enum** (stable). Only the `capacity_exceeded` row is in the narrowed
   contract. The remaining rows are not in scope (item 23). They are kept only as
   historical vocabulary: no build emits them, and the allow-list in item 1 refuses
   them. Each compiler limit exit gets one fixed `constraint` token:

| `category` | Meaning / client action | `constraint` tokens (exits) |
| --- | --- | --- |
| `capacity_exceeded` | Input is well-formed but over a reviewed bound. Narrow the change. Do not silently split or reorder it (contract line 143-144). | `semantic_leaf_changes`, `operation_count`, `operation_value_bytes`, `patch_bytes`, `pointer_length`, `pointer_depth`, `value_json_nodes`, `value_json_depth`, `result_config_bytes`, `config_growth_bytes`, `traversal_bound` |
| `malformed_operation` | The operation is syntactically invalid. Fix that operation. | `operations_not_list`, `operation_fields_unknown`, `operation_fields_incomplete`, `operation_id_not_canonical`, `operation_value_presence`, `operation_id_duplicate`, `path_duplicate`, `path_parent_child_conflict`, `pointer_not_absolute`, `pointer_escape_malformed`, `pointer_empty_token`, `pointer_not_canonical`, `list_index_not_canonical` |
| `unsupported_operation` | Well-formed, but outside the reviewed subset. | `operation_kind_unsupported`, `pointer_selector_prohibited`, `pointer_append_misplaced`, `append_requires_add`, `value_not_json` |
| `target_mismatch` | Well-formed, but does not fit the current pre-read dashboard. Re-read and re-plan. | `target_parent_missing`, `target_parent_not_collection`, `append_requires_list`, `list_index_out_of_range`, `add_target_present`, `target_absent` |
| `internal_failure` | Invariant failure. Nothing the client can act on. | `configuration_not_object`, `input_mutated` |

3. **Attribution** (`patch.py`).
   - `canonicalize_patch` and the `_apply_one` loop catch a
     `DashboardFoundationError` for operation *i* and re-raise the same class with
     `operation_index=i`.
   - `parse_pointer`/`_resolve_parent` add `token_index` where a segment is known.
   - `path_parent_child_conflict` reports the later index.
   - Bound exits set `observed`/`limit`. The traversal and equality bounds set
     `limit` only, because their `observed` count is not truthful once the walk aborts.
   - The `json_codec.py:95/97` bound keeps its `PatchValidationError` class and adds
     `stage="compilation"`, so the stage tells the truth without changing the class.
4. **Capacity enrichment (not in scope: Josh decision (in-session 2026-11-08), item 23; kept as historical design)**
   (`patch.py:392`). Keep the existing five fields and add:
   - `operation_leaf_changes`: per-operation counts, length at most 16, taken from
     `effect.leaf_change_count`.
   - `shifted_array_operations`: an int count of effects that used suffix accounting.

   Together these tell the caller *why* the count is high without changing how it
   is counted. `diagnostic_details` is widened to allow this one bounded integer list.
5. **Projection** (`service.py:5602-5609`). For a `PatchValidationError` or
   `PatchCompilationError` whose `category` is `capacity_exceeded`, emit
   `{**exc.diagnostic_details(), "reason": exc.code}`. Every non-limit exit, and every
   other `DashboardFoundationError`, keeps `{"reason": exc.code}`. The contract sentence
   "Other compilation failures retain their prior error details" is narrowed to
   non-limit failures in the same PR. `test_unrelated_compiler_error_keeps_legacy_details`
   (a `target_absent` exit) stays unchanged. Superseded (historical): the first draft
   projected details for every patch error and changed that test.
6. **Safe-detail rule.** Only fixed tokens and non-negative integers are returned.
   Never returned: pointer strings, `operation_id` strings (caller tokens; their
   charset also differs from `_DIAGNOSTIC_TOKEN`), keys, values, list lengths or
   exception text. The caller maps `operation_index` and `token_index` back to its
   own request. `result_config_bytes` reveals only a byte count, which the contract
   already allows (`F3_DASHBOARD_WRITE_CONTRACT.md:98-100`). Path echo is not in
   scope (section 9, item 23).
7. **Observability.** `planning.patch_validation_failures` records
   `codes=(exc.code, category)`, bounded by `MAX_EVENT_CODES`
   (`constants.py:65`). The `broad_subtree_rejections` trigger stays the same
   (`planning.py:148-149`).
8. **Provider routing.** Unchanged. The compiler is Engineering-native. It runs after
   the existing admitted ha-mcp dashboard preread (`planning.py:108`) and before ha-mcp
   `ha_config_set_dashboard` admission (`planning.py:158-195`). No delegated call,
   extra read, fallback or Core command is added.

## 4. Public schema / tool-registration impact

- No tool is added or removed, and the tool count is unchanged.
- The `create_dashboard_update_plan` input schema (`tools/governance.py:197-212`,
  `459-498`) is unchanged.
- Output: the error `details` of `CONFIGURATION_VALIDATION_FAILED` gain additive
  keys on dashboard compiler *limit* failures only. Non-limit failures keep
  `{"reason": code}`. `reason` values are unchanged.
- This is a security-sensitive compatibility surface: the error output contract
  changes. It needs the contract-doc update and review.

## 5. Signing impact

None. Dashboard update planning is Engineering-native compilation followed by
dispatch through the attested ha-mcp provider. It uses no signed Core profile
(`docs/CORE_RELEASE_REGISTRY.md:23-25`). The only dashboard Core profiles,
`core.dashboard_configuration_read` and `core.dashboard_metadata_operation`
(`capabilities.py:504-505`), are not touched. No Core command, contract or
fingerprint changes, and nothing joins a signing batch.

## 6. Negative reachability and refusal tests

The change adds no write, service, reload or fallback path. Tests:
These were written as required implementation evidence before the build. The build is #412 (section 0); this
note has not reconciled them with the #412 tests and claims none of them as run.
- **One row per limit exit.** A table-driven test asserts the exact `details` for every
  compiler limit exit in section 2, including `category`, `constraint`, `stage`,
  `observed`/`limit` and indices. A second table asserts that every non-limit exit
  still returns exactly `{"reason": code}`.
  Each case uses a synthetic private marker in its path, key and value, and
  asserts that marker is absent from `details` and from `audit.jsonl` (the
  pattern at `test_dashboard_update_mvp.py:809-812`).
- **Same work refused, preread preserved (Codex F3).** For every limit exit, the test
  observes exactly one bounded admitted preread (`preread_count == 1`) and zero setter
  and guide calls (`write_count == 0`, `best_practice_count == 0`). It also asserts no
  plan, no task, no persisted work and an unchanged configuration
  (`test_dashboard_update_mvp.py:701`, `:804-808`). The test must not assume zero
  provider calls.
- **Order unchanged.** Validation runs before compilation, compilation stops at
  the first failing operation in caller order, and the capacity check comes before
  the size and growth checks. A patch with both a bad operation 3 and an
  over-capacity total reports operation 3.
- **Fail closed.** Building a diagnostic with an unknown `category`/`constraint`,
  a negative index or a bool raises an error.
- **Not expanded (item 23).** No `details` from any exit contains a `path` key, an
  `operation_leaf_changes` list or a `shifted_array_operations` count. No exit emits a
  `category` other than `capacity_exceeded`, and no non-limit exit carries `category`
  at all. Constructing a diagnostic with `category="malformed_operation"` (or any other
  non-capacity row) raises.
- **Other errors unchanged.** Non-patch `DashboardFoundationError` subclasses
  still project only `{"reason": code}`. `ProviderAuthorityUnavailableError` still
  maps to `PROVIDER_UNAVAILABLE`.

## 7. Acceptance

- **Success.** The 54-leaf Home fixture still plans, approves, applies once,
  verifies and restores (`test_dashboard_update_mvp.py:704`). Exactly 256 leaves
  still reaches `succeeded_verified` (`:762-779`). Leaf counts stay
  `[8, 2, 4, 40]` (`test_f3_dashboard_patch.py:401-403`).
- **Capacity refusal.** 257 and 300 leaves return the existing five fields plus
  `category="capacity_exceeded"`. `observed` is the actual total. A 17-operation
  patch, an over-bound value, patch, pointer, JSON depth or node count, and a result
  or growth overrun each return `category="capacity_exceeded"` with their own
  `constraint`, `stage` and `limit`. `observed` is included where it is truthful.
  Where it applies, `operation_index` is included too.
- **Other refusals.** Malformed, unsupported and target-mismatch inputs keep the legacy
  `{"reason": code}` under the narrowed scope.
- **Failure preservation.** Every `ErrorCode`, `reason`, exception class, bound
  and check order is unchanged. Stale-state refusal, approval-projection binding,
  one-dispatch and exact readback tests pass unmodified.
- **Recovery.** Nothing changes for recovery. Refusal happens after the read-only
  preread and before any plan, so the client corrects the request and plans again.

## 8. Build slice and dependencies

- One PR against `release/2.6.0`. It changes `f3_dashboard/errors.py`,
  `patch.py`, `json_codec.py` (stage only), `planning.py` (metric codes) and
  `governance/service.py` (projection), plus tests and
  `F3_DASHBOARD_WRITE_CONTRACT.md`. Its 2.6.0 notes and acceptance go in the
  release PR.
- These are protected runtime paths, so the PR must authorize them by name.
- Built: PR #412, merge `50e127e5` into `release/2.6.0`, not on `main` (section 0). The
  scope is the narrowed contract only (item 23). Superseded (historical): "Not built.
  Register status is Backlog, for a later 2.6 wave (wave 2 in the orchestrator's 2.6 plan)."
- No dependency on signing or on a live HA. As of 2026-11-07 the heads of
  #384-#387 do not touch these files. If a 2.5.0 change touches `service.py`,
  2.5.0 wins and this PR merges `main`.
- Rollback: revert the PR. It changes no persisted record or artifact schema,
  because diagnostics are never stored in plans.

## 9. Open questions

No question blocks the narrowed scope. Superseded (historical): the first draft's Q1
("proceed or close as delivered?") is answered by the reopened row and is removed.

**Decided: not expanded.** Josh decision (in-session 2026-11-08), item 23: HAMCP-143 is
not expanded beyond the limit diagnostics. All three former optional choices are closed
as "no" for this row:
1. **Path echo: no.** Indices only, matching the existing test that the path is never
   echoed.
2. **Capacity enrichment: no.** The shipped five fields plus `category` stay.
3. **Non-limit categorization: no.** Non-limit exits keep `{"reason": code}`.

The proving tests are in section 6 ("Not expanded (item 23)"), as required implementation
evidence (not reconciled with #412 here; section 0). Superseded (historical): the three were listed as "optional
future scope, pending Josh" with these same defaults.

## 10. Codex design review 2026-11-08 — disposition

| Finding | Severity | Correction in this note |
| --- | --- | --- |
| F2: settled closure question and stale scope | Low | Section 0 gives the dated disposition: reopened and narrowed per the HAMCP-143 row, 256/16 preserved, nothing built. Sections 1 and 3 (narrowed contract, items 2, 4 and 5) restrict the design to compiler-limit diagnostics. Section 9 removes the closure question and lists the remaining choices only as optional. Proving check: reconcile with `register get HAMCP-143`. |
| F3: compiler runs after a provider preread | Low | Section 2 "Pipeline" and section 3 item 8 now state preread (`planning.py:108`), then compile (`:143`), then setter admission (`:158-195`). A compilation failure creates no plan, task, artifact or provider mutation and keeps the admitted preread. Section 6 requires future evidence: `preread_count == 1`, `write_count == 0`, `best_practice_count == 0`, unchanged state, no persisted work. |

## 11. Josh decisions 2026-11-08 — disposition

| Item | Decision (Josh decision, in-session 2026-11-08) | Sections changed |
| --- | --- | --- |
| 23 | HAMCP-143 is not expanded beyond the narrowed compiler-limit diagnostics (no path echo, no capacity enrichment, no non-limit categorization). | 0 (decided entry), 1, 3 (narrowed-contract note; item 1 allow-list fixed to `capacity_exceeded`; item 2 other rows historical only; item 4 out of scope; item 6), 4 (output line narrowed to limit failures), 6 (new "Not expanded" negative tests), 8 (wave 2 scope), 9 (three choices closed). |

Effect on the Codex corrections in section 10: F2's "lists the remaining choices only as optional" is now "closes
them as not in scope (item 23)". F3 (preread, then compile, then setter admission) is unchanged.

## 12. Cursor delta review 2026-11-09 — disposition

| Finding | Severity | Correction in this note |
| --- | --- | --- |
| L1: section 0 said nothing is built | Low | Section 0 records the build: PR #412, merge `50e127e5` into `release/2.6.0` (2026-11-08 2:13 PM CST), not on `main`, ancestry checked. Sections 6, 8 and 9 no longer call it unbuilt; the section 6 tests are not claimed as run. The section 10 F2 text ("nothing built") is the 2026-11-08 record and is superseded by section 0. Item 23 is unchanged. |
