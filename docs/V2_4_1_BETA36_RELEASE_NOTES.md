# Engineering 2.4.1-beta.36 release notes

Engineering 2.4.1-beta.36 corrects the governed delete's dispatch-time drift
read (HAMCP-155). It follows 2.4.1-beta.35 and includes it, plus three
documentation-only merges since (#302 HAMCP-222 signed coverage audit, #299
HAMCP-195 design note, #291 HAMCP-132 design note). It adds no dependency
change; both hashed locks are beta.35's. **The release activates nothing
new.** `core.configuration_delete_operation` stays
`pending_signed_registry_admission` under the shipped signed sequence 12.

Acceptance: [`V2_4_1_BETA36_ACCEPTANCE.md`](V2_4_1_BETA36_ACCEPTANCE.md).
Design: [`HAMCP_155_GOVERNED_DELETION_DESIGN.md`](HAMCP_155_GOVERNED_DELETION_DESIGN.md).

## The defect

Immediately before the DELETE, the delete adapter re-reads the live config
and compares it with the bound pre-image (drift is `stale_target_state`).
That read ran inside the dispatch call window but **before**
`before_dispatch` consumed the dispatch authority. The dispatch window admits
a Home Assistant call only once that authority is committed, and the real
`RestClient` refuses any call the window does not admit. Against a real Core
(a disposable 2026.10.1 container, HAMCP-222 evidence run) every admitted
delete therefore failed pre-dispatch with `resource_read_unavailable` and
zero writes. It failed closed; nothing was deleted. No live install was
affected: the profile is not admitted by any signed registry.

The unit-test fakes never applied the real clients' authority check, so the
HAMCP-155 suites passed.

## The correction

- The drift read now runs in its **own short Core-authority window**, the
  same pattern as the F3 preflight step: acquire a lease for the bound
  operation (the plan's own `plan_created_at`), consume it, admit client
  calls only while that lease revalidates (and while the authorizer outside
  the dispatch window still admits them), re-check it after the read, and
  finish it.
- The window is entered inside the dispatch window, is single use, and is
  refused once the dispatch authority has been committed. It never touches
  the dispatch guard's lease: `before_dispatch` still consumes and commits
  that one exactly as before. Nothing else in the dispatch or authority
  model changes.
- Authority missing when the window opens, or withdrawn or retired during
  the read, refuses with `dispatch_authority_unavailable`; nothing is sent.
  Drift is still `stale_target_state`; a delete still dispatches exactly
  once and verification still proves absence.
- Only an adapter that declares its pre-intent check reads Home Assistant
  (`PREDISPATCH_READS_HOME_ASSISTANT`, set only on the delete adapter) gets
  the window. Every other adapter's pre-intent check is the base no-op and
  runs unchanged. Without a Core runtime the read runs exactly as before.
- The window does not re-run Core reconciliation: the dispatch guard ran it
  immediately before this dispatch began.

## Test fakes now enforce the real authority check

`tests/core_authority_fakes.py` applies the same check as
`RestClient.request` and `WebSocketClient.command`: a call the request's Core
authorizer does not admit raises `HomeAssistantUnavailableError`
(`PROVIDER_UNAVAILABLE`) before the fake answers, and is recorded. The
governance and F3 fakes call it: the 069A registry WebSocket and REST, the
069B exposure and 165 label WebSockets (through the 069A base), the 155
configuration REST store, the dev-14 configuration gateway, and the F3
runtime integration state reader.

## Compatibility

- No tool, descriptor or input schema change. The catalog is still **96
  tools, 97 with the optional YAML reader admitted**.
- The 17 signed Core capability profiles, the probe fingerprints, the
  owner-signed Core registry (sequence 12) and the delete profile's contract
  fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`
  are unchanged, so a sequence-13 entry prepared against beta.35 still
  references the right value.
- No persisted format, workflow, permission, add-on configuration,
  dependency or stable-v1 change.

**Protected runtime files.** Josh authorized this scoped HAMCP-155 fix:
`f3_configuration/adapter.py`, `f3_configuration/delete.py`,
`f3_runtime/runtime.py` and the version.

## Rollback

Reinstall the 2.4.1-beta.35 artifact. No stored format changes.
