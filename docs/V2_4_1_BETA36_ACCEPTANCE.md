# Engineering 2.4.1-beta.36 acceptance

This contract covers the HAMCP-155 correction to the governed delete's
dispatch-time drift read, shipped **inactive** like beta.35. The signed
registry (sequence 12) still does not reference
`core.configuration_delete_operation`. The release activates nothing new. It
follows 2.4.1-beta.35 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA36_RELEASE_NOTES.md`](V2_4_1_BETA36_RELEASE_NOTES.md).
- The beta.35 deletion contract this release keeps:
  [`V2_4_1_BETA35_ACCEPTANCE.md`](V2_4_1_BETA35_ACCEPTANCE.md).

**Release target.** `2.4.1-beta.36` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.35. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's 2026-10-24 decision (Option 1: a scoped protected-runtime fix of
  the delete's pre-dispatch drift re-read, then beta.36, then the HAMCP-222
  batch evidence rerun);
- `2.4.1-beta.36` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No new reachability.** No new write, read, endpoint, provider, fallback
  or tool. The only DELETE remains beta.35's, sent once by the delete
  gateway after `before_dispatch` commits the dispatch authority.
- **The drift read's own authority window**
  (`f3_runtime/runtime.py` `_predispatch_read_window`). It:
  - is bound only inside `_CoreVerificationAdapter.dispatch`, through
    `f3_configuration.adapter.PREDISPATCH_READ_WINDOW`, and is used only by
    an adapter with `PREDISPATCH_READS_HOME_ASSISTANT` (the delete adapter);
  - refuses with no guard, a second use, or a guard whose authority is
    already committed;
  - acquires the bound operation's lease with the plan's `plan_created_at`,
    consumes it, releases it if it cannot be consumed, and finishes it on
    every exit;
  - admits client calls only while its own lease revalidates **and** the
    authorizer outside the dispatch window admits them; it never admits on
    the uncommitted dispatch authority;
  - restores the dispatch window's authorizer on exit, so the drift read
    cannot leak authority to anything after it;
  - re-checks its lease after the read; missing or lost authority is
    `dispatch_authority_unavailable` with nothing sent.
- **Unchanged.** Drift is `stale_target_state`; a delete dispatches exactly
  once; verification proves absence. The dispatch guard's acquire, consume,
  commit and revalidate are untouched. Every other adapter's pre-intent
  check is the base no-op and gets no window.
- **Reconciliation.** The window does not re-run `reconcile_once`; the
  dispatch guard ran it immediately before the same dispatch.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every descriptor and input schema; the catalog is **96 tools, 97 with
  the optional YAML reader admitted**;
- probe fingerprints, the 17 signed Core capability profiles, the
  owner-signed Core registry (sequence 12), the signed ha-mcp registry and
  the delete profile's contract fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`;
- the HOLD and the support window;
- add-on permissions, dependencies and every workflow.

### Offline proof

No test reaches a live system. Every fixture is synthetic.

- **Enforcing fakes.** `tests/core_authority_fakes.py` applies the real
  clients' Core-authority check (`clients/rest.py`, `clients/websocket.py`)
  in the governance and F3 fake Home Assistant clients and gateways, so an
  authority-less call fails in unit tests as it does against real Core.
- `tests/test_hamcp155_predispatch_authority_window.py` (14 tests):
  - executor level, the real service, F3 runtime and delete gateway against
    the enforcing store: a delete verifies with exactly one DELETE, its
    drift read authorized and no call refused; drift is
    `stale_target_state`; authority withdrawn during the read, or missing
    when the window opens, is `dispatch_authority_unavailable` with no
    DELETE; the re-read moved back outside any window fails closed and the
    enforcing client records the refused read;
  - the real `CoreRuntime` with a TEST-signed entry: the window's second
    lease is issued while the guard holds its own and the delete verifies
    once;
  - the window alone: admits only while its lease is current and the outer
    authorizer admits; refuses with no guard, a committed guard or a second
    use; releases an unconsumable lease; finishes and restores the
    dispatch authorizer on every exit.
- The HAMCP-155, 069A, 069B, 165, dev-14 and F3 runtime integration suites
  pass under the enforcing fakes; the PR lists every test the enforcement
  exposed.
- Mutations: `docs/evidence/hamcp155-predispatch-window-mutations/`.
  `results.json` records each mutant and the tests that killed it.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.35 artifacts and the known-good earlier
artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.35 artifact.

## 3. Installed Engineering

All installed checks in this section are read-only.

1. **Installed-image binding.** Separately authorize installation of the
   published artifact. Bind the running add-on to the published image index
   digest, the release tag and the protected `main` merge commit.
2. **Catalog.** `tools/list` shows 96 tools (97 with the YAML reader
   admitted).
3. **Inactive.** `list_capabilities` and server health show the
   configuration delete capability as `pending_signed_registry_admission`;
   one `create_configuration_delete_plan` call is refused with
   `CoreCapabilityUnavailable` and `plan_created: false`.

## 4. Offline real-Core evidence and live canary

The HAMCP-222 batch evidence run (#306) reruns against beta.36 on a
disposable Core container: one clean run must produce receipts for all four
dormant profiles. That run is offline-disposable evidence, not a live
change. The live canary of beta.35 section 4 still waits for an owner-signed
sequence 13 and Josh's separate approval; this document does not authorize
it.
