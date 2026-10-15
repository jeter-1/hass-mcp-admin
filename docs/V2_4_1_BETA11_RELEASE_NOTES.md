# Engineering 2.4.1-beta.11 release notes

Engineering 2.4.1-beta.11 is an audit-hardening release (HAMCP-184). It closes
four findings from the 2026-10-14 main audit on top of beta.10 (HAMCP-181
PR1). No tool, input schema, description, routing, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included.
No new write, dispatch, service-call or fallback path is reachable. The
design note is [`HAMCP_184_AUDIT_HARDENING_DESIGN.md`](HAMCP_184_AUDIT_HARDENING_DESIGN.md).

**Provider dispositions fail closed (F-05).** A reviewed ha-mcp release used
to admit a provider surface (read gateway, dashboard, backup, lifecycle) when
its registry entry declared no disposition for it. Admission is no longer
inferred from omission:
- The disposition accessor returns `held` for an absent surface or an unknown
  value.
- The registry loader refuses a reviewed entry without an explicit
  `provider_dispositions` object naming exactly the four surfaces with
  `admitted`, `partial` or `held`.
- The five legacy entries (7.14.1, 7.14.2, 8.0.0, 8.1.0 and 8.2.0) now declare
  the dispositions they effectively had, each backed by recorded review
  evidence. No surface moved to `held`.

Effective behavior is unchanged for all ten compiled entries. Production
ha-mcp 8.5.0 keeps read gateway and dashboard `admitted` and backup and
lifecycle `held`, and its entry is byte-identical.

**F3 re-apply checks the approved hash (F-01).** Re-applying a plan whose F3
task already exists now validates the caller's `expected_plan_hash` exactly as
the first apply and the legacy path do. An empty or wrong hash is refused with
`approval_hash_mismatch` before any dispatch, task event or plan change. A
matching replay is unchanged: a finished task returns without redispatch, and
an initialized task that never dispatched resumes and dispatches once.

**Truthful principal-separation reporting (F-02).** The Ingress approval route
falls back to the anonymous principal `home_assistant_admin_ingress` when Home
Assistant supplies no user identity. Decisions made that way used to report
`principal_separation_enforced: true`, but they prove only that an
administrator decided through the separate channel, not which administrator.
The decisions now report:
- `principal_separation_enforced: false`;
- a new `principal_separation_status` object,
  `{evaluated: true, enforced: false, reason: anonymous_fallback_principal}`.

An identified principal still reports `true` with
`external_administrator_distinct`. An undecided plan reports `null` with
`no_external_approver_exists`.

Reporting only (owner decision):
- who may approve, the approval flow, standard-class eligibility of the
  anonymous fallback and elevated-class refusal of it are unchanged;
- existing records are never rewritten. A pre-beta.11 anonymous decision
  stored as `true` now displays `false` and stays exactly as eligible as
  before.

**Runner guard covers matrix runners (F-06).** The CI runner guard now checks
every runner label the committed add-on matrix generator emits for every tier,
rather than a declared constant. An unpinned generated label fails the guard.
`ci.yml` is unchanged; its matrix runners resolve to `ubuntu-24.04` and
`ubuntu-24.04-arm`.

The healthy catalog remains **87 tools: 59 native and 28 delegated reads**.

Downgrade: beta.10 reads every beta.11 record. A downgraded server returns to
displaying `principal_separation_enforced` from the stored value and loses
the status object. A beta.11 anonymous-fallback approval is stored as `false`,
and beta.10 refuses to apply that approval, so decide pending approvals again
after a downgrade. A downgraded server also returns to defaulting absent
dispositions to `admitted` and to the unchecked F3 re-apply hash.

Follow the [beta.11 acceptance contract](V2_4_1_BETA11_ACCEPTANCE.md).
