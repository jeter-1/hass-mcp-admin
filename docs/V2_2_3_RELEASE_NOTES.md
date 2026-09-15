# Engineering 2.2.3 release notes

Engineering 2.2.3 adds explicit received Host and Origin enforcement before MCP
processing. It retains the existing **HA MCP Engineering Server Beta** installation
and public connector path. Header admission supplements secret-path authentication;
it grants no tool, provider or approval authority.

## Configure intended MCP clients before updating

The two new options default to:

```yaml
mcp_allowed_hosts:
  - "127.0.0.1:8100"
  - "[::1]:8100"
  - "localhost:8100"
mcp_allowed_origins: []
```

An explicit host list replaces these defaults. Add only the exact authorities
actually received by Engineering for intended clients, including the port where
present. LAN names, internal aliases and tunnel authorities outside these defaults
need explicit configuration. A public URL does not establish the Host forwarded
to Engineering. Forwarding headers, DNS suffixes and network location do not
grant admission.

Native clients may omit Origin. The empty origin list rejects every present
Origin; browser clients need their exact HTTP(S) origin configured. An absent
Origin is different from an empty or `null` value. This is not a CORS feature.
The authenticated approval listener on port 8110 remains a separate boundary.

Malformed options or an empty host list refuse startup. Reconcile the exact
configuration before an authorized update and retain independent Home Assistant
management access: invalid startup configuration can prevent both listeners from
starting. See [the inbound policy](INBOUND_SECURITY.md) for normalization, bounds,
option examples and recovery requirements.

## Enforcement and preserved boundaries

The gateway checks received headers before health/readiness paths, secret-path
authentication, request bodies, Core authority and the SDK. Malformed, missing or
duplicate Host fields are refused with 400; a valid unlisted Host with 421;
unacceptable Origin with 403; an excessive header-entry envelope with 431.
Parser precedence applies when several fields are invalid.

Policy is immutable and bounded. It permits no wildcards, CIDRs, suffix matching,
DNS lookup or inferred forwarding trust. Diagnostics contain fixed safe categories,
not rejected header values. The MCP listener explicitly disables Uvicorn
proxy-header rewriting, preserving the immediate peer used by existing policies.
The optional Cloudflare direct-peer restriction remains separate.

Requests with admitted headers still require existing authentication, rate limits,
provider admission and governed approval. Tool schemas, registration, routing to
providers, single-dispatch ownership, verification, cleanup and zero fallback are
preserved. The private topology observer remains dormant by default with its
existing separate arming contract; installation does not start a capture.

## Installation and compatibility

The directory/slug `hass_mcp_engineering_beta`, add-on name, image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, ports 8100/8110, ingress identity,
existing options and persistent formats remain unchanged. The two header-policy
options are the configuration additions. No second installation or data migration
is required.

The controlled build inputs and supported `linux/amd64` and `linux/arm64` platforms
are unchanged. Historical three-platform release verification remains available;
this release does not restore arm/v7 image production. Frozen stable-v1 1.1.2 is
unchanged and is not an Engineering rollback target.

The healthy reviewed ha-mcp 8.4.3 pairing retains 51 static plus 25 delegated tools
and 17 Core capabilities. `ha_get_operation_status` remains absent from ordinary
registration. Core 2026.9.2 requires separately configured, valid signed Core
authority referencing the reviewed compiled contracts. Installing 2.2.3 does not
configure that trust or establish compatibility with future Core or ha-mcp versions.

## Evidence and acceptance limits

Independent source review covered implementation commit
`6ee5bf81301343b6e7c1a2f823128a4c27df19b4`, tree
`74d52513a152b2abbea46defb992d00ef949bea0`, with no actionable findings.
The review ran 455 focused tests: 454 passed and one expected staging-related
test was skipped. Nine additional independent probes passed. Disposable tests
reproduced both defects on unchanged 2.2.2 and passed on the candidate.

Those results establish source behavior, not final release-candidate CI, published
image identity or installed enforcement. Publication records the actual release
source, build time and digests; [2.2.3 acceptance](V2_2_3_ACCEPTANCE.md) separately
requires installed-image binding, complete public catalog, useful reads, intended
and hostile-header path checks, natural dependency refresh and settled resources.
Historical acceptance remains attributed to its original version and observation.

Receiver enforcement cannot reconstruct fields discarded or replaced upstream.
A successful native catalog capture does not prove hostile browser-Origin
rejection through a hosted proxy. Issue #62 remains open pending appropriate
installed-path evidence. Complete Android navigation, cache-only startup during
an outage and independent verification of backup contents remain outside the
established historical acceptance evidence.

Recovery must reconcile compatible artifact, options, Core, configuration and
database/backup state through independent management access. Returning to 2.2.2
restores the known missing enforcement. Do not widen policy with wildcards,
disable authentication, or blindly retry an uncertain operation. Publication does
not deploy the release or authorize configuration changes or recovery.
