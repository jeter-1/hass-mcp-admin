# Engineering 2.4.1-beta.7 release notes

Engineering 2.4.1-beta.7 adds three delegated platform reads (HAMCP-129) on
top of beta.6's typed power and fan admission repair (HAMCP-175). Each one is
an Engineering-owned wrapper over a ha-mcp tool that was previously withheld
as `mixed_or_requires_wrapper`. The wrapper fixes the upstream arguments,
closes the public schema and projects the upstream result through a positive
output allowlist before the existing sanitizer runs.

- **`ha_get_system_health`** returns the platform baseline (Home Assistant,
  Supervisor and Cloud health fields) plus any of the optional sections:
  `repairs`, `zha_network`, `zwave_network`, `thread_network`,
  `matter_network`, `dead_entities` and `themes`. Typed Core health values are
  reduced to `ok`, `pending` or `failed`, and their error text is never
  returned. `diagnostics`, `config_check` and `zha_network_full` cannot be
  requested. Repair `translation_placeholders`, `learn_more_url` and issue
  `data` are dropped.
- **`ha_get_app`** lists installed add-ons, optionally filtered to one `slug`.
  The slug is filtered by Engineering and never sent upstream. The tool cannot
  reach the add-on store, `options`, `schema`, `network` or ingress
  data. `include_stats` is accepted only when at most 32 add-ons are started.
  A stats-free listing checks that bound before Engineering dispatches the
  stats call. The check is a sampled bound: upstream re-lists before its stats
  fan-out, so an add-on started in between is still fanned out before the
  over-cap response is refused. A started add-on whose stats are unavailable
  makes the result `partial` (`stats_unavailable`, with the add-on's slug in
  a warning).
- **`ha_get_integration`** lists config entries, with an optional `query`,
  `domain`, `entry_id` or `state` filter and pagination. Engineering sends one
  fixed list request: at most 200 entries, `include_options=false`, and
  `domain` or `query` only as an upstream `query` prefilter. Upstream
  `domain` and `entry_id` modes are never used, so no options flow is started.
  Engineering then applies every exact filter and the pagination. Entry
  titles are capped at 120 characters, and e-mail-like substrings are masked
  as `[email]` before output and filtering. Hostnames in titles are not
  masked.

All lists and strings are capped. A capped, missing-section or
stats-unavailable result is reported through `completeness` (`partial` with
fixed reason codes). Masking alone does not make a result partial.
Upstream error text and warnings are never passed through.

**Disclosed boundaries.**
- The three reads use the existing basic Core WebSocket and REST profiles. No
  new signed Core profile is added. Those profiles do not check these
  specific commands or the Supervisor API.
- `ha_get_system_health` and `ha_get_app` stay `openWorldHint: true`, as
  upstream declares. The health baseline always runs Core's integration health
  callbacks (Cloud and Supervisor reachability) and ha-mcp's own update check.
- `ha_get_integration` stays `openWorldHint: false`, as upstream declares.
- Host CPU and RAM are not part of the platform projection.
- The platform adapters are bound to exact ha-mcp releases. A future ha-mcp
  release admitted only through an owner-signed registry entry keeps the same
  25 delegated reads as beta.6. The three new tools are withheld there with
  reason `platform_read_binding_absent`, reported in the
  `readmission_withheld_*` health fields, until a compiled release binds
  them.
- The regenerated 8.4.3 policy has a new digest. A signed entry that binds
  the earlier 8.4.3 policy digest
  (`sha256:dc61eeb287de20a30c78504efb21352d7a4d4cf9f248dcb4e6c823b64deb0cbe`),
  as an entry accepted by beta.6 does, still selects that exact earlier policy
  and profile. That entry keeps the same 25 reads, with the three new tools
  withheld as above, through one enumerated, digest-verified legacy binding.
  No other digest is treated as an alias. Drift, expiry and revocation
  refuse exactly as before. An entry prepared from this release binds the new
  digest, which beta.6 and earlier do not recognize.

**Admission.**
- Each tool binds to its exact ha-mcp 8.4.3, 8.5.0 or 8.6.0 input-schema
  fingerprint and source commit through a compiled adapter token.
- Upstream drift quarantines only the affected tool.
- An unknown Core withholds all three tools without dispatch.

The healthy catalog is **87 tools: 59 native and 28 delegated reads**. The
compiled (unsigned) ha-mcp release registry evidence identities are
regenerated for the new classification counts (automatic reads 25 to 28).
The owner-signed Core registry, the 8.6.0 HOLD, the support window,
workflows, persisted formats and stable v1 are unchanged. No write, service
call, fallback or arbitrary forwarding becomes reachable.

Downgrade: beta.6 reads every beta.7 record, and nothing new is persisted. A
downgraded server withholds the three tools again.

Follow the [beta.7 acceptance contract](V2_4_1_BETA7_ACCEPTANCE.md) and the
[HAMCP-129 design](HAMCP_129_PLATFORM_READS_DESIGN.md).
