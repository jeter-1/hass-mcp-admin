# Engineering 2.4.1-beta.44 release notes

Engineering 2.4.1-beta.44 makes automation baseline compare and resume usable
on the owner's installation (HAMCP-235). It follows 2.4.1-beta.43 and
includes it. There is no dependency, add-on configuration, input schema, tool,
routing, provider, Core command or signed registry change.

Acceptance: [`V2_4_1_BETA44_ACCEPTANCE.md`](V2_4_1_BETA44_ACCEPTANCE.md).

## Why

On 2026-10-27 the owner ran beta.40 on HAOS (Core 2026.10.3, signed registry
seq 14) with 115 automations:

- two captures 4 s apart with identical digests compared as **UNKNOWN 115 /
  UNCHANGED 0**; every row carried
  `earlier_/later_authority_continuity_unestablished` and
  `installation_identity_unestablished`;
- each header said installation `unestablished`, `final_fence:
  "unavailable"`, drift `unknown`; the second capture also had two
  incomplete configurations;
- a resume two minutes later was refused as `resume_inventory_changed`,
  with no detail.

Diagnosis (recorded in HAMCP-235):

- **Identity.** The HAOS identity anchor (the Core device's hassio config
  entry) was valid. "Unestablished" came only from the final fence failing:
  a failed final fence discarded the identity the first fence had verified.
- **Final fence.** It shared the 45 s collection deadline, configuration
  scheduling kept only a fixed 10 s for it, and a failure was swallowed
  without a reason.
- **Resume.** A base whose final fence failed stored `installation_id: null`,
  so every resume mismatched. The fence also compared the Core authority
  generation and the declared count, and the capture fences compared each
  automation's on/off state, all of which are volatile.

No new Core command was needed: every fix uses the reads already admitted
under signed registry seq 14.

## The change

**Compare (owner decision, Josh, option a).**
- Rows are classified by digest: `UNCHANGED`, `CHANGED` or `PREFIX_MATCH`
  (its own bucket); `ADDED` / `REMOVED` only where inventory authority
  allows.
- What the pair cannot verify is stated once, in a new `caveats` list of
  `{code, statement, sides}`: `installation_identity_unverified`,
  `authority_continuity_unestablished`, `non_atomic_capture`. It is identical
  on every page and never repeated as a row reason.
- `UNKNOWN` is kept for genuinely incomparable rows, each with its reason: a
  timed-out read (`timed_out`, now from either capture's stored outcomes), a
  missing digest, an unreadable configuration, incomplete coverage, an
  unverified mapping, a fingerprint contract or model mismatch, a scope
  mismatch, or two established, different installations
  (`installation_identity_mismatch`).

**Resume.**
- Only stable identity is compared: the installation anchor, the set of
  configuration ids and each id's entity. On/off state, the authority
  generation and the unmapped count no longer refuse a resume; a new
  generation is recorded as `authority_drift: "detected"`.
- A refusal names its fixed categories in `details.resume_changes`:
  `automation_added`, `automation_removed`, `mapping_changed`,
  `installation_changed`, `installation_anchor_missing`, or
  `core_version_changed` (for `resume_core_changed`). No identifier, count or
  Home Assistant text.
- A base whose own final fence failed is anchored by the identity its first
  fence observed (`diagnostics.first_fence_installation_id`, an opaque
  digest), and the resumed baseline is established by the resume's verified
  fences. Inherited rows keep the provenance of the capture they came from;
  the current fences' anchor is never evidence for them.
- Owner decision D4 (Josh, 2026-10-28): a base with no installation anchor,
  such as a beta.43 capture whose final fence failed, is refused as
  `resume_inventory_changed` with `resume_changes:
  ["installation_anchor_missing"]`, on any installation, before any Home
  Assistant read and with nothing stored. Take a fresh capture instead.

**Final fence.**
- It reads the identity first (principal, config entries, devices), then the
  inventory. If only the inventory reads fail, the re-verified identity still
  establishes the capture (`final_fence: "identity_only"`). Identity drift
  still refuses the capture, with nothing stored.
- `diagnostics.final_fence_reason` keeps the failed read's fixed reason.
- The final fence's reserve covers its identity reads: twice their measured
  first-fence time, clamped to 5–15 s
  (`diagnostics.final_fence_reserve_seconds`). Configuration reads end at the
  collection deadline less the reserve (in-flight reads are cut there); the
  inventory reads use what remains and may give `identity_only`. With 115
  automations at 1 s per read: beta.43 read 115 in 39.3 s (fixed 10 s
  reserve), and beta.44 reads 115 in 39.3 s (6.0 s reserve) with the final
  fence complete, within the 45 s deadline.
- On/off state is no longer inventory drift; entity, configuration id and
  the unmapped and withheld counts still are.

### Before and after, live-shaped (offline)

| Owner case | beta.43 | beta.44 |
|---|---|---|
| Two captures, identical digests, final fence failed | UNKNOWN 115, three reasons per row | UNCHANGED 115, three caveats once |
| Same pair, header | installation `unestablished`, `final_fence: "unavailable"` | unchanged when the identity reads fail too; `identity_only` and established when only inventory reads fail; reason recorded |
| A timed-out row | UNKNOWN (`later_configuration_digest_missing`) | UNKNOWN (`timed_out`, `later_configuration_digest_missing`) |
| Resume minutes later, same automations | `resume_inventory_changed`, no detail | succeeds; established; `authority_drift: "detected"` if the generation changed |
| Resume after an automation was added | `resume_inventory_changed` | `resume_inventory_changed`, `resume_changes: ["automation_added"]` |

## Compatibility

- **Output (additive).** `compare_automation_baselines` gains `caveats`;
  rows that were `UNKNOWN` only for the comparison-level conditions are now
  classified by digest. The row reasons `installation_identity_unestablished`,
  `earlier_/later_authority_continuity_unestablished`,
  `capture_installation_unestablished` and
  `capture_authority_continuity_unestablished` are no longer emitted.
  `capture_automation_baseline` diagnostics gain `final_fence_reason`,
  `first_fence_installation_id`, `final_fence_reserve_seconds` and the
  `final_fence` value `identity_only`. Refusals gain `details.resume_changes`.
- **Stored format.** The baseline schema is unchanged; the new values are in
  the free-form diagnostics. Baselines written by beta.43 compare and resume
  on beta.44 (a beta.43 base whose final fence failed has no anchor and its
  resume stays unestablished). Baselines written by beta.44 load on beta.43.
- No input schema, tool count, routing, provider, policy, Core gate,
  telemetry, signed profile, probe fingerprint, signed registry, workflow,
  Dockerfile, dependency, add-on option or stable-v1 change. Tool
  descriptors are unchanged.

**Protected runtime files.** The HAMCP-235 task authorized this scoped
change under `audit_baseline/`: `capture_contracts.py`,
`capture_provider.py`, `capture_service.py`, `comparison.py`, `models.py`;
and the version.

## Rollback

Reinstall the 2.4.1-beta.43 artifact. No stored format changes; baselines
written by beta.44 stay readable there (compared with its per-row reasons).
