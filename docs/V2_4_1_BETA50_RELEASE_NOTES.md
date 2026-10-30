# Engineering 2.4.1-beta.50 release notes

Engineering 2.4.1-beta.50 builds governed dashboard metadata **inactive**
(HAMCP-106). It also makes the Core-authority reconciliation audit record fit
whole (HAMCP-255). It follows 2.4.1-beta.49 and includes it. It activates
nothing new. It changes no signed registry, signed profile, existing
fingerprint, existing input schema or existing plan family.

Acceptance: [`V2_4_1_BETA50_ACCEPTANCE.md`](V2_4_1_BETA50_ACCEPTANCE.md).
Design and owner decisions:
[`HAMCP_106_DASHBOARD_METADATA_DESIGN.md`](HAMCP_106_DASHBOARD_METADATA_DESIGN.md).

## Governed dashboard metadata (HAMCP-106)

New tool: `create_dashboard_metadata_plan`. It proposes a change to the
`title`, `icon`, `show_in_sidebar` or `require_admin` of one existing storage
dashboard. The plan is approved externally and applied with
`apply_change_plan`.

- **Route (Josh, 2026-10-30).** Engineering-native, direct to Core. The one
  write is `lovelace/dashboards/update` by `dashboard_id`, carrying only the
  requested fields. The readback is `lovelace/dashboards/list`. The
  request does not go through ha-mcp and has no fallback.
- **Inactive.** It needs the new runtime-only Core profile
  `core.dashboard_metadata_operation`, which signed sequence 14 does not
  name. Every entry point therefore refuses
  `pending_signed_registry_admission` before any Home Assistant request or
  persistence. Health under sequence 14 lists 26 compatible and 4 withheld
  profiles, including this one. The profile joins the sequence-15 signing
  batch.
- **Planning.** Planning refuses, before any dispatch:
  - an unknown `url_path`;
  - a YAML dashboard;
  - `"lovelace"`;
  - an ambiguous target;
  - an invalid value.

  The title is non-empty, at most 255 characters, and stored exactly
  (`"Map "` keeps its trailing space). The icon is `prefix:name`, or `null`
  to clear it. A request that changes nothing returns a no-change result
  and creates no plan. Each changed field is disclosed before and after.
  `require_admin` and `show_in_sidebar` changes carry fixed visibility
  texts. Disclosure is generic; it does not count users.
- **Policy.** `standard_admin` for every field, physical consequence none.
- **Fingerprint.** The plan binds the metadata row and the dashboard content
  fingerprint. The content fingerprint comes from the admitted
  `get_dashboard_config` read. If that read is unavailable, the request
  fails closed.
- **Apply.**
  - The fingerprint is checked immediately before dispatch, and a stale
    state is refused as `stale_target_state`.
  - Dispatch happens at most once, with durable intent, no redispatch and
    truthful attribution.
  - An ambiguous Core answer is decided by the readback.
- **Verification.** The requested fields must be exact, the unrequested
  fields unchanged and the content unchanged. Any mismatch is a
  verification failure.
- **Rollback.** `rollback_change` creates a separate governed plan that
  restores the changed fields.
- **Not in scope:** dashboard content, creating or deleting dashboards,
  `url_path` or `mode` changes, YAML dashboards, resources and themes. The
  existing dashboard update plan and its schema are unchanged.

The dormant seq-15 `dashboard_metadata` evidence lane is defined. Its
`ci.yml` wiring is deferred to the November batch, because `ci.yml` is
protected (design section 7).

## Whole Core-authority audit record (HAMCP-255)

On beta.48, the `home_assistant_core_authority_reconciled` audit event was
8972 characters. `AuditLogger` (bound 8192) therefore stored a
`payload_truncated` stub at every startup. No per-profile disposition
survived.

The event's `analysis_summary` now uses model `ha-core-authority-audit-v2`:
- Profiles are grouped by disposition, reason code and authority source.
  Each group maps a capability id to its profile id.
- `adapter_id` is dropped.
- Counts and the other fields are kept.

Each profile's disposition and reason code can be reconstructed from the
record. Twice today's profile count fits in 75% of the bound. The logger's
bound and its assertion are unchanged. The failure event is unchanged.

## Counts

Static tools 77 → 78. The advertised catalog is 106, or 107 with the
optional YAML reader admitted. Runtime Core profiles 29 → 30.

## Rollback

Reinstall 2.4.1-beta.49. Under sequence 14, beta.50 writes no new record
shape: the new family refuses before persistence. No code parses the audit
summary model, so an older version returns stored v2 audit records as they
are; new records revert to v1, which is truncated again.
