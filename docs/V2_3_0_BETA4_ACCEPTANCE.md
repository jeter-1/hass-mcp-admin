# Engineering 2.3.0-beta.4 acceptance

This release removes the need for new compiled Core version pairs for existing
typed fan/light/switch semantics. Keep source preparation, production signing,
publication, deployment, Core update and live tests as separate authorities.

## Reviewed implementation and release delta

Complete-PR base: `349e0e49607e44a31eececd9ec457a5e124dbf98`.
Independently reviewed implementation: `64af6ec213ddb3c6d0203ba534307d6e4ffa8e25`,
tree `5d0e9bed1677ad1e7239aacca06c0fd86e3fe47c`.

Carry forward the no-actionable-findings review, 274 focused passes with zero
skips, and exact shipped-writer fixture reproduction. The earlier 3,924-test
full run had one corrected assertion, ten loopback permission errors and 34
skips; focused correction/transport reruns are not a clean full invocation.
Do not relabel those historical results as final candidate validation.

Review the release/integration delta. Preserve reviewed runtime contracts except
the version constant. Require beta.3 to beta.4 sequencing, exact staged notes and
acceptance resolution, promotion preview and local materialization. All three
version authorities must become beta.4 and the staging declaration must be
consumed in the same branch before Ready. This does not publish or deploy.

Run affected context/release and disposable-lane regressions, then clean-head
Evidence against the complete-PR base with every changed protected surface
declared. Require passing metadata and complete unittest discovery; identify
skips, interpreter, environment, base/head/tree, counts and failures explicitly.
Preserve stable-v1, previous descriptors, production trust data, original 17
Core fingerprints, immutable .2 authority, dependencies and installation identity.

## Disposable exact-version contracts

Core 2026.9.3 source is `6de5eb18cd4502f94af44cfff3a02250d88716ed`;
`tests/fixtures/core_2026_9_3_lane_provenance.json` records its exact source tree,
source archive hash, immutable image index and both architecture manifests.
The source/tag and container labels are distinct evidence; labels alone are not
cryptographic source-build provenance. Never invent a missing attestation.

The existing real-Core suite must run on exact .3 with reviewed ha-mcp 8.5.0,
retaining configuration/helper/dependency/child-device/read contracts. The same
runtime instance first withholds absent authority and then admits all 19
capabilities after receiving ephemeral test-signed references. Do not add .3 to
production compiled release tables or modify the published .2 entry.

Retain every historical Core/provider lane. In addition, execute .3/8.5.0 with
both standalone and add-on images on native amd64 and arm64. Image inputs,
initialized identity, admitted protocol, complete catalog and selected provider
contracts must match reviewed evidence. Add-on relay tests do not establish
Supervisor lifecycle or the hosted Nabu Casa path.

For each new combination require:

1. Three fan actions (ON at 50%, 75%, OFF) and light/switch ON/OFF, each with one
   intent and attempt, exact Core/semantic/provider binding, authoritative state
   readback, unchanged duplicate receipts and no additional dispatch. Verify
   independent power service counters and exact relay accounting.
2. Controlled stale state between preparation and preflight refuses fan and
   power dispatch. Disclose direct REST state interference as a disposable fault;
   it is not a device operation. Restore the fixture state and prove zero calls.
3. A real provider action followed by injected response/readback loss retains an
   uncertain nonterminal receipt. A replacement reader respects the active owner,
   then recovers read-only after explicitly simulated 121-second owner expiry.
   Core identity and transport remain real. Require one original dispatch,
   exact independent readback, a durable recovery event, duplicate suppression
   and a separately identified OFF restoration. Do not claim an actual process
   crash, elapsed 121 seconds or Core version transition from this fault.
4. Useful admitted reads after errors, truthful blueprint completeness,
   governed dashboard apply/readback/restoration and provider attribution.
5. No unexplained active tasks, locks, conflict holds, Core leases/commits,
   audit projection failures or fallback; exact final OFF states. Assert owned
   container/network cleanup and preserve failure evidence. Never upload tokens,
   private Core storage or arbitrary container logs.

Retain independent unit coverage for changed Core version, revoked/expired/bad
authority, provider drift, invalid arguments, tampering, owner loss and no replay.
Synthetic transport tests do not substitute for the container results above.
The candidate's interpreter runs providers/executors against real containers;
Engineering image execution and installed acceptance remain separate checks.

Require both controlled-input architecture builds and every job behind the
exact-head required `validate` aggregate. Preserve workflow permissions,
triggers, timeouts, immutable actions, historical coverage and always-run cleanup.
Bind results to the exact source or verify both parents and tree relationship
for GitHub's synthetic merge. Complete a bounded independent delta review before
Josh's Ready decision; do not repeat the full implementation campaign.

## Publication and deployment preparation

Prepare the exact approved merge/source identity, version `2.3.0-beta.4`, 78-tool
expectation, 19-profile inventory, reviewed provider identity, registry state,
image/tag/architecture provenance and storage-compatible recovery decision.
Publication requires the existing owner-authorized protected workflow; require
tag `v2.3.0-beta.4`, prerelease notes and matching immutable image/build identities.
Publication does not authorize trust activation, deployment or a Core update.

Prepare a separately reviewed production Core .3 reference covering the original
17 and two typed profiles only after actual compatibility evidence exists.
Preserve the immutable .2 entry. Test-key signatures are not production authority.

## Separately authorized installed acceptance

Deploy Engineering first while Core remains 2026.9.2. Verify exact installed
source/build/image identity, REST/WebSocket agreement, ha-mcp admission, storage,
audit, retained declarations, useful native/delegated reads and settled F3 state.
With the unchanged .2 journal, expect 17 admitted of 19 profiles and the historical
typed route; do not claim the two new references are signed for .2.

After reviewed .3 authority and a distinct Core-update decision, establish the
specific backup/recovery evidence and exact pre-state. Update only the approved
Core target. Read-only validation must show Core 2026.9.3 agreement, all 19
compatible capabilities including exact typed references, provider admission,
useful reads, ordinary dependency refresh and execution settlement. Report
missing or partial evidence truthfully. A cached client tool inventory is not
a raw protocol capture; document any required client refresh.

Any household write acceptance needs exact approved fixture targets, disclosed
effects, pre-state, bounded actions and separate restoration/readback. Reconcile
uncertainty through original IDs; never redispatch or substitute providers to
recover missing evidence. Do not repeat completed beta.3 canaries without a
specific compatibility question and authorization. Preserve historical catalog
qualifications and receipt continuity.

## Recovery

Before deployment, ordinary source revert has no household effect. After new
Core-bound declarations exist, beta.3 cannot read them: binary downgrade alone
is insufficient. Preserve dispatch identities, holds, original receipts and
uncertain work. Use a compatible reader or an independently reviewed restoration
procedure; never delete IDs or restore an execution store that loses a possible
dispatch. Core rollback, application/storage restoration and physical restoration
are different actions with separate verification and owner decisions.
