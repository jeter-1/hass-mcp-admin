# Engineering 2.4.2-beta.7 release notes

Engineering 2.4.2-beta.7 is the 2.5.0 build (HAMCP-331). It builds on
2.4.2-beta.6 (HAMCP-304, #373). Every item landed through its own reviewed
pull request into the `release/2.5.0` integration branch, and the release
pull request (#383) carries the whole build to `main`.

Josh decided on 2026-11-07 to build all 2.5.0 items into one release. The
version string is 2.4.2-beta.7 because the release transition refuses
`2.4.2-beta.6 -> 2.5.0-beta.1`.

Acceptance: [`V2_4_2_BETA7_ACCEPTANCE.md`](V2_4_2_BETA7_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

**Read [Deploy order](#deploy-order) before installing.** This release
changes how Engineering connects to ha-mcp on every version, including
ha-mcp 8.5.

## Items

| Item | Pull requests |
| --- | --- |
| HAMCP-327 (folds in HAMCP-301): `control_helper` fails to ask | #386 |
| HAMCP-288: ha-mcp secret-path containment | #387, #403 |
| HAMCP-319 Phase 2: ha-mcp 9.1.0 admission side by side with 8.5.0 | #385 (design), #404, #405, #406 (with #407, #408) |
| HAMCP-290: Marketplace inventory read | #409 |
| HAMCP-309 Part 3: Core release order guard (unblocks HAMCP-279) | #384 |
| HAMCP-278 / HAMCP-279: status only | no code in this release |

## Deploy order

1. **Deploy Engineering 2.4.2-beta.7 with the ha-mcp app still on 8.5.**
   Leave `upstream_dashboard_mcp_url` unchanged.
2. **Verify on 8.5** with the post-deploy acceptance
   ([acceptance contract](V2_4_2_BETA7_ACCEPTANCE.md), section 4):
   `get_server_health` reports ha-mcp `8.5.0`, reachable, with no
   `upstream_redirect_refused` or `upstream_endpoint_rejected`; one delegated
   read and one governed dashboard list return useful results. If the URL
   relied on a redirect, set the exact endpoint URL (see
   [item 3](#3-redirects-are-refused-on-every-ha-mcp-connection)) and restart
   Engineering before going on.
3. **Only then update the ha-mcp app to 9.1,** append `/catalog/full` to
   `upstream_dashboard_mcp_url` ([item 1](#1-ha-mcp-91-and-the-catalogfull-option)),
   and verify again.
4. **Rotate the ha-mcp secret path** (HAMCP-288, Josh only) with the
   [rotation runbook](evidence/hamcp288/ROTATION_RUNBOOK.md), only after
   containment is deployed and verified in step 2.

Each step is a separate deployment or live change that Josh authorizes. Ready
on #383 authorizes none of them.

## HAMCP-319 Phase 2: ha-mcp 9.1.0 at parity with 8.5.0

Engineering admits the exact ha-mcp 9.1.0 release side by side with 8.5.0,
with the same delegated read surface, typed controls and governed dashboard
operations. The ten items are numbered as reviewed in #406.

### 1. ha-mcp 9.1 and the `/catalog/full` option

ha-mcp 9.1.0 is admitted only when `upstream_dashboard_mcp_url` ends with
exactly `/catalog/full`. After updating the app to 9.1.0, append
`/catalog/full` to the existing value, with no trailing slash, encoding or
case change. Engineering never repairs or canonicalizes the value.

A legacy-form value on 9.1.0 is refused after initialize and before any
`tools/list`, with `upstream_catalog_mode_unadmitted`, or
`upstream_catalog_selector_invalid` for a misspelled selector. The
remediation text is fixed and never echoes the URL. No partial 9.1 surface is
exposed.

On 9.1.0, runtime admission binds every tool Engineering uses exactly and
once. A missing, changed or duplicate used tool fails closed and is withheld.
Catalog order is not an admission condition on 9.1.0; 8.5.0 keeps its
existing order check unchanged.

### 2. Rollback of the option, in both orders

- **App back to 8.5.0:** also remove the `/catalog/full` suffix. If it stays,
  8.5.0 answers 404 (`endpoint_rejected`) and the delegated surface stays
  fail-closed.
- **Engineering back to 2.4.2-beta.6 or earlier while the app is 9.1.0:**
  beta.6 has no 9.1.0 entry and refuses it as an unreviewed upstream version
  (`admission_status: blocked_incompatible_upstream`, `version_status:
  rejected_unreviewed`, no delegated read exposed). Roll the app back to 8.5.0
  and remove the suffix **first**, then roll back Engineering.

The side-by-side exact-image lane runs this Engineering rollback against the
published 2.4.2-beta.6 image by index digest
(`sha256:9284290132d1d4f06195f5ee5bb27797238b86d8a250db13c6c78f2171059f6a`).
Its lane case is named `unsupported_version_rollback`; that is a lane label,
not a runtime reason code.

### 3. Redirects are refused on every ha-mcp connection

Josh decided on 2026-11-07 (DCL1, recorded in
[`HAMCP_319_PHASE2_DESIGN.md`](HAMCP_319_PHASE2_DESIGN.md)) that redirects
are refused on every ha-mcp MCP connection, **ha-mcp 8.5.0 included**, for
every request type and every Engineering client. Every 3xx is refused with
zero requests to the redirect destination and reported as
`upstream_redirect_refused` with a fixed operator message.

A URL that worked only through a redirect (for example one that differs only
by a trailing slash) now fails closed. Set `upstream_dashboard_mcp_url` to
the exact endpoint URL and restart Engineering. The post-deploy acceptance on
8.5 ([Deploy order](#deploy-order), step 2) is the only proof that the
production URL needs no redirect.

### 4. Typed write records across rollback

Typed write records created under 9.1.0 keep their 9.1.0 contract after an
app rollback to 8.5.0. They are never re-dispatched.

### 5. `ha_get_device` projection, reduced on 8.5.0

On 8.5.0 and 9.1.0, `ha_get_device` withholds Thread credentials and IP
addresses on non-IP networks. **On 8.5.0 the output is also reduced:**

- Thread `ip_addresses` are withheld;
- unknown nested keys are dropped;
- enrichment warnings become closed codes;
- registry pairs outside the reviewed allowlist, or that fail derived
  identity, are withheld with `registry_pair_withheld` or
  `registry_identity_withheld`.

### 6. Thread credential redaction everywhere

`thread_credential` keys, including `tlv` and `network_key`, are redacted on
every surface by the shared sanitizer.

### 7. 8.5.0 dashboard getter

A changed dashboard getter descriptor, for example title drift, is now
refused on 8.5.0 as on 9.1.0 (`upstream_runtime_contract_mismatch`).
Releases older than 8.5.0 keep their prior admission (HAMCP-336).

### 8. Disclosed 9.1 parity differences

- The skill guide is file-only on 9.1: its listing and file-list tiers are
  unavailable.
- Eval-template `request_id` and `evaluation_time` are unavailable.
- Groups `all` and `order` are unavailable when absent.
- Dashboard absence is unavailable when the registry list collapses.
- The optional `ha_config_get_yaml` is admitted only with the filesystem
  tools gated as reviewed.

### 9. HACS on 9.1.0

Superseded by [HAMCP-290](#hamcp-290-marketplace-inventory-read):
`ha_get_hacs_info` is retired on every release. With 9.1.0 as the newest
reviewed release the catalog is 82 + 27 = 109.

### 10. Pending approvals and supersession

Pending approvals are superseded when a newer plan for the same target is
created; a restart alone does not supersede them. This is existing behavior.
Corrected 2026-11-08: an earlier description, relayed by the architect and
noted as accepted by Josh on 2026-11-07, wrongly attributed supersession to
restart.

### Lane evidence

The exact-image admission lanes run the built Engineering image against the
exact ha-mcp images on amd64 and arm64 in three configurations: 8.5 only,
9.1 only, and side by side (with the beta.6 rollback stage). The lanes
compare the full 9.1 catalog with the reviewed capture as an
order-independent set with exact descriptors, refusing any duplicate,
missing or extra tool; order fingerprints are diagnostic only. The
acceptance gate requires `ACCEPTED` for every configuration.

## HAMCP-288: ha-mcp secret-path containment

- Engineering never returns the configured ha-mcp secret path from
  `upstream_dashboard_mcp_url` in any output; it is redacted wherever it could
  appear, including upstream error text.
- `get_incident_logs` withholds the ha-mcp app's own log source with
  `upstream_secret_path_source_withheld`.
- Configuration validation refuses, with a reason that names
  `upstream_dashboard_mcp_url` and carries no part of the value:
  - a secret path whose decoded form is empty or only separators (architect
    ruling, 2026-11-07);
  - a **percent-encoded** secret path whose decoded form is only separators
    and endpoint route words (Josh decision 2026-11-07, refined the same day,
    recorded in #403).

  Unencoded paths such as `/readonly/mcp` are accepted as before.
- [`LAN_LISTENER_FINDING.md`](evidence/hamcp288/LAN_LISTENER_FINDING.md)
  records that the ha-mcp listener port was reachable on the LAN (one TCP
  connect check, 2026-11-07; listener identity not verified).
- **Rotation (Josh, after deploy):** follow the
  [rotation runbook](evidence/hamcp288/ROTATION_RUNBOOK.md): deploy
  containment first, rotate the app's `secret_path`, update the Engineering
  option, confirm the new path works and the old one fails. Any copy of the
  path that left Engineering before containment stays valid until rotation.

## HAMCP-290: Marketplace inventory read

Recorded Josh decisions of 2026-11-08
([`HAMCP_290_MARKETPLACE_READ_DESIGN.md`](HAMCP_290_MARKETPLACE_READ_DESIGN.md)):

- Marketplace (HACS) inventory is the Engineering-owned read
  `get_marketplace_inventory`. It lists installed repositories only, paged
  and sanitized.
- The delegated ha-mcp `ha_get_hacs_info` read is retired on every reviewed
  ha-mcp release, with no alias and no fallback.
- **Unsigned until its own signing.** The read uses a new Core profile,
  `core.marketplace_read`, which ships unsigned and is signed separately after
  2.5. Until then it refuses with `pending_signed_registry_admission` and
  sends no command, so **no HACS or Marketplace inventory read is available
  on any Core.** This is a regression on Core 2026.7 through 2026.10 with
  HACS, where `ha_get_hacs_info` previously answered.
- A page reports `completeness: partial`, never `complete`, when more
  matching repositories remain (`more_pages`) or when Engineering withheld or
  shortened text (`sanitized_text`), listed in `partial_reasons`. A refusal's
  `fallback_occurred` is true exactly when the HACS command was sent.
- **Rollback before signing:** revert #409, or roll Engineering back to
  2.4.2-beta.6. Either restores `ha_get_hacs_info`.
- **Rollback after signing:**
  - source rollback is a reviewed Engineering release that no longer compiles
    the profile; the signed reference then has no effect and stays in the
    journal;
  - the only signed withdrawal is `revoke` of the whole Core release entry,
    which withdraws every signed Core capability for that release and needs
    its own owner-signed sequence and Josh's separate authorization;
  - never roll back the journal or delete a cache.

## HAMCP-327: `control_helper` fails to ask, never acts blind

Folds in HAMCP-301.

- A valid dependency index with partial coverage is established evidence;
  coverage gaps are disclosed in `coverage_reason_codes`.
- When evidence cannot be established, `control_helper` returns
  `confirmation_required` with `evidence_unavailable` and does not dispatch.
  The path that proceeded without a safety check is gone.
- A helper with a safety-critical consumer is never changed without the
  safety-critical confirmation, including a literal helper id inside a long
  configuration path (HAMCP-301 C-iv).
- `no_change` and reconciliation results disclose consumers as information
  read after the result is decided; the disclosure never asks, dispatches or
  changes the status.
- Opaque safety-critical consumers are disclosed only (HAMCP-311 rule).

## HAMCP-309 Part 3: Core release order guard

The Core release-evidence order guard now compares only official stable
records, so pre-releases no longer make scheduled discovery refuse.
Selection, the stop rule, the page bound and every refusal are otherwise
unchanged. Scripts and tests only; no Engineering runtime change.

## HAMCP-278 and HAMCP-279 status

- **HAMCP-278 (Core registry signing and publication): done.** Proven live
  with registry sequences 16 and 17. No HAMCP-278 code is in this release.
- **HAMCP-279 (automated Core release evidence): CI-only, pending live
  proof.** No code remains. Live proof needs this release (with HAMCP-309
  Part 3) on `main`, one passing scheduled run, and the first Core patch
  release after 2026.11.0.

## Tool catalog

| Catalog | Engineering | Delegated | Total |
| --- | --- | --- | --- |
| Reviewed newest upstream 9.1.0, stock policy (source baseline) | 82 | 27 | 109 |
| ha-mcp 8.5.0 component capture, optional YAML reader on | 82 | 28 | 110 |
| ha-mcp 8.6.0 or 8.4.3, stock policy | 82 | 27 | 109 |
| ha-mcp 8.4.1 or 8.2.x | 82 | 24 | 106 |

Engineering-owned tools rise from 81 to 82 with `get_marketplace_inventory`;
the delegated count falls by one with the retirement of `ha_get_hacs_info`.
The derivations are in
[`HAMCP_290_MARKETPLACE_READ_DESIGN.md`](HAMCP_290_MARKETPLACE_READ_DESIGN.md)
and `scripts/codex-context.py`.
Admission is per tool and fail-closed, so a running system may expose a
smaller compatible subset; read `tools/list` on the running system.

## Known limitations

- HAMCP-290: no Marketplace inventory on any Core until
  `core.marketplace_read` is signed.
- HAMCP-319: production has not run ha-mcp 9.1.0; the lanes are the evidence.
  Whether the production 8.5 URL relies on a redirect is unknown until the
  post-deploy acceptance runs.
- HAMCP-319: the `tools/call` 3xx case is covered at the transport level
  only; used-tool field classes are sampled on `ha_get_state` only.
- HAMCP-319: no 9.1.0 add-on capture with the filesystem flag off.
- HAMCP-319: after a restart, the approval notification for each still-active
  challenge is sent again (existing behavior; Josh chose option A on #407).
- HAMCP-319: the mutation survivors NR L1–L4 and A2a/A2b are disclosed
  in the #407 review record.
- Dashboard getter title drift on releases older than 8.5.0 is not refused
  (HAMCP-336).

## Unchanged by design

No governance, plan, approval or audit storage format changes. No new write,
service call, approval source or unreviewed provider fallback; every new or
changed read fails closed. The one reviewed fallback is HAMCP-290's
Marketplace-to-HACS protocol fallback in `get_marketplace_inventory`: only on
an exact `unknown_command` reply to `marketplace/repositories/list`, it sends
the constant `hacs/repositories/list` on the same connection and reports it
as `fallback_occurred`. It is dormant until `core.marketplace_read` is signed.
Stable v1 (`hass_mcp_admin/`) is unchanged.

## Rollback

Rolling back Engineering to 2.4.2-beta.6 is safe under these conditions:

1. **If the app is on 9.1.0, roll it back to 8.5.0 and remove
   `/catalog/full` first** (item 2); beta.6 refuses 9.1.0.
2. **Redirects:** beta.6 follows redirects again; no action needed.
3. **HAMCP-288:** beta.6 has no containment. After a rollback the secret path
   can again appear in the ha-mcp app's log source and in upstream error
   text. If the path was rotated, treat a rollback as exposing the new path
   until a containing release is redeployed.
4. **HAMCP-290:** beta.6 restores `ha_get_hacs_info`; after signing, follow
   the HAMCP-290 rollback rules above.
5. Storage formats are unchanged; typed write records keep their recorded
   upstream version.

Whether to roll back is Josh's decision.
