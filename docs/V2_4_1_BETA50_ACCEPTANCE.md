# Engineering 2.4.1-beta.50 acceptance

This contract covers two items:
- HAMCP-106: governed storage-dashboard metadata, built **inactive**;
- HAMCP-255: the whole Core-authority reconciliation audit record.

It adds one tool, `create_dashboard_metadata_plan`, and one runtime-only
Core capability profile, `core.dashboard_metadata_operation`. The signed
registry (sequence 14) does not reference that profile. The release
activates nothing new; the tool switches on with the sequence-15 signing.
It follows 2.4.1-beta.49 (HAMCP-198, HAMCP-120).

See also:
- Release notes:
  [`V2_4_1_BETA50_RELEASE_NOTES.md`](V2_4_1_BETA50_RELEASE_NOTES.md).
- Design and owner decisions (Josh, 2026-10-30):
  [`HAMCP_106_DASHBOARD_METADATA_DESIGN.md`](HAMCP_106_DASHBOARD_METADATA_DESIGN.md).

**Release target.** `2.4.1-beta.50` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.49 (#329). The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-106 decisions of 2026-10-30, design section 0;
- Josh's HAMCP-255 decision (option 2 with conditions);
- `2.4.1-beta.50` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR;
- the runtime-only profile fingerprint, recomputed at the review head:

  | Profile | sha256 |
  |---|---|
  | `core.dashboard_metadata_operation` | `62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa` |

The review needs a security section covering the following items.

**HAMCP-106:**
- **New write path, unreachable at release.** `lovelace/dashboards/update`,
  with `dashboard_id` and only the requested fields. It is reachable only
  through an approved plan dispatched by the F3 runtime, and only for a
  profile admitted by a signed registry. Every entry point refuses first,
  before any read, validation read or storage:
  - the tool;
  - the generic `create_configuration_plan`;
  - the resource-write route.

  F3 dispatch admission is checked again.
- **Closed vocabulary.** The gateway sends only `lovelace/dashboards/list`
  and `update`. Dashboard content is only fingerprinted, through the
  admitted `get_dashboard_config` read.
- **Target.** Exactly one existing storage dashboard. `"lovelace"`, YAML
  dashboards, and unknown or ambiguous targets are refused while planning.
- **Approval.**
  - `standard_admin` for all four fields, with the fixed disclosure texts.
  - A multi-operation or incomplete dashboard-metadata plan is `PROHIBITED`.
  - The planning caller cannot approve.
- **Verification.**
  - The fingerprint is checked immediately before the write.
  - Exact readback of the requested fields, the unrequested fields and the
    content fingerprint.
  - An indeterminate send is decided by readback and never redispatched.
- **Rollback.** A separately approved restore plan.
- **Fallback.** There is no delegated provider and no fallback.

**HAMCP-255, as a distinct item:**
- **What changes.** The `home_assistant_core_authority_reconciled` audit
  record changes shape to `ha-core-authority-audit-v2` (grouped profiles,
  `adapter_id` dropped).
- **What it must preserve.** Every profile's disposition and reason code
  must stay reconstructable. The `AuditLogger` bound (8192) and its
  assertion must be unchanged.
- **Capacity.** Twice today's profile count must fit within 75% of the
  bound.
- **Reason (live, beta.48).** The v1 record was truncated at startup
  (`original_size` 8972, `payload_truncated` true).

Preserve the following:
- stable v1.1.2;
- persisted formats of every existing family; the new resource type is
  written only after signed admission;
- every existing descriptor and input schema, including the existing
  dashboard update plan;
- static tools **78** (77 + 1). The catalog is **106 tools, 107 with the
  optional YAML reader admitted**;
- probe fingerprints;
- every signed Core capability profile and the owner-signed Core registry
  (sequence 14, unchanged);
- the signed ha-mcp registry and the ha-mcp contracts;
- the HOLD and the support window;
- add-on permissions and dependencies;
- `ci.yml` and every other workflow, all unchanged.

### 1.1 Evidence lane (defined, not wired)

`scripts/seq15_contract_acceptance.py` defines the `dashboard_metadata`
lane, and `WORKFLOW_PENDING_LANES` lists it. `ci.yml` does not stage or
upload its evidence yet, so an enabled run fails closed. The `ci.yml` steps
remaining for November are listed in design section 7.

### 1.2 Offline proof

No test reaches a live system. Every fixture is synthetic. The dashboard
collection is shaped from the six live storage dashboards, including
`"Map "` with its trailing space.

- `tests/test_hamcp106_dashboard_metadata.py` with
  `tests/hamcp106_fixtures.py`. These use the real service, the F3 runtime
  and the dashboard metadata gateway, and cover:
  - positive paths for each field and for icon clearing;
  - per-field disclosure;
  - every planning refusal, and the no-op;
  - inactive refusal at the tool and at the generic builder, with no Core
    call and no plan;
  - generic routes that cannot carry the type;
  - stale state and expiry at apply;
  - Core 404, unauthorized, error and wrong-identity responses, decided
    truthfully;
  - readback mismatches (requested, unrequested and content);
  - rollback and its refusals.
- `tests/test_hamcp255_reconciliation_audit.py` covers:
  - reconstruction against health for every profile;
  - deterministic grouping;
  - the record stored whole by `AuditLogger`;
  - capacity at twice today's profile count;
  - the v1 regression anchor.
- `tests/test_seq15_contract_acceptance.py` (lane, fingerprint pin) and
  `tests/test_alarmo_interval_observer.py`. The second pins the wired lanes
  against `ci.yml` and the pending lane as absent from it.
- `tests/test_hamcp242_core_authority_itemization.py` pins sequence 14 at
  26 compatible and 4 withheld, including
  `core.dashboard_metadata_operation`, and 30 after sequence 15.
- Pin updates across the suite: tool counts, F3 identities and registry
  size, capability metadata, and dormant lane sets.
- Mutations: `docs/evidence/hamcp106-mutations/`.

### Downgrade rule

Rollback is reinstalling **2.4.1-beta.49**. Under sequence 14, beta.50
writes no new record shape, because the new family refuses before
persistence. Audit records already written in v2 stay as written. After a
future signed sequence admits the profile, a downgrade below beta.50 must be
re-evaluated before it is supported; that is not covered here. Never clear
F3 journals, task files or locks.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- the release tag;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.49 artifacts for
recovery.

## 3. Installed Engineering

Installation needs separate authorization.

### 3.1 After deploy (read-only)

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
2. **Catalog.** `tools/list` shows 106 tools, or 107 with the YAML reader
   admitted, including `create_dashboard_metadata_plan`.
3. **Inactive refusal.** Call `create_dashboard_metadata_plan` once with
   `{"url_path": "engineering-acceptance", "title": "x"}`. It must refuse
   with `pending_signed_registry_admission`, `plan_created` false and
   `provider_dispatch_occurred` false, and `list_change_plans` must show no
   new plan. This is a refusal, so no Home Assistant request is made.
4. **Health.** Core authority shows 26 compatible and 4 withheld, and the
   withheld itemization includes `core.dashboard_metadata_operation`
   (`positive_authority_missing`). `list_capabilities` lists the tool with
   that profile and a `core_admission` other than `admitted`.
5. **Audit.** The startup `home_assistant_core_authority_reconciled` record
   carries `ha-core-authority-audit-v2` and has no `payload_truncated`.
6. **Smoke and regression.** The standard read-only smoke and regression
   checks.

### 3.2 After the sequence-15 signing (defined here, not run)

These steps require all of the following:
- the signed sequence 15 that names the profile in section 1;
- an add-on restart;
- health showing 30 compatible and 0 withheld;
- Josh's approval at the time.

The target is `engineering-acceptance` only.

1. Change the title to `"Engineering Acceptance 106"` and the icon from
   `mdi:storage-tank` to `mdi:test-tube`. Approve, apply, and verify:
   - both fields are exact;
   - `show_in_sidebar` and `require_admin` are unchanged;
   - the content fingerprint (`get_dashboard_config`
     `engineering_config_hash`) is unchanged.
2. `rollback_change` creates a separate plan. Approve, apply, and verify
   that the title and icon are both restored.
3. Repeat a request for the current values. It is refused as no-change,
   with no plan.
4. Run the smoke and regression checks.

## 4. Out of scope

Not authorized by this document:
- registry signing (sequence 15);
- the `ci.yml` lane wiring;
- the post-signing steps;
- dashboard content, dashboard create or delete, `url_path` or `mode`
  changes, YAML dashboards, resources and themes;
- HAMCP-071, HAMCP-143 and HAMCP-144;
- any downgrade.
