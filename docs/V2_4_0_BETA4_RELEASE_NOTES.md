# Engineering 2.4.0-beta.4 release notes

Release candidate for bounded script-call dependency analysis. These
notes assert no publication, deployment or installed acceptance.

The existing read-only `entity_dependency_analysis` now recognizes literal script
invocations using canonical registry storage keys and current entity IDs. Optional
indirect analysis follows automation → script → entity and script → script paths
to depth three, with page-specific evidence and provider attribution. Dynamic or
unknown calls, cycles, work limits and partial discovery remain explicit.

No script executes. Tool inputs and count remain 79 (54 static + 25 delegated).
Provider admission, fallback, execution authority, persisted formats, options,
permissions, dependencies and stable-v1 are unchanged. Existing helper/impact
bindings do not consume the new diagnostic evidence. No new Core version pin or
signed registry entry is introduced.

This is the first bounded HAFA-G001 slice, not complete all-family inventory.
See [source contract](SCRIPT_CALL_DEPENDENCIES.md) and
[acceptance](V2_4_0_BETA4_ACCEPTANCE.md). Prior beta evidence retains its original
scope, including the separate beta.3 dashboard canary decision.
