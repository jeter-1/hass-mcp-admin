# Engineering MCP Host and Origin policy

Engineering 2.2.3 adds received-header enforcement after the published 2.2.2
baseline. A source version does not establish publication or installed acceptance.
Existing 2.2.2 image/catalog and topology evidence retain their original source
and time. Use the [2.2.3 acceptance contract](V2_2_3_ACCEPTANCE.md) for the new
artifact and separately authorized installed-path checks.

## Scope and defaults

The Engineering gateway checks Host and Origin on its MCP listener before
health/readiness responses, authentication-path processing, body reads, Core
authority acquisition and SDK dispatch. This is the sole Host/Origin enforcer;
FastMCP's optional inner policy remains unused. Header admission is not caller
authentication, provider admission or approval authority.

Two add-on options configure exact authorities and origins:

```yaml
mcp_allowed_hosts:
  - "127.0.0.1:8100"
  - "[::1]:8100"
  - "localhost:8100"
mcp_allowed_origins: []
```

These are conventional loopback authorities, not a restriction on the socket
peer. A proxy may legitimately send a loopback Host from a different immediate
peer. The listener still binds `0.0.0.0`; no address, port mapping or connector
path is changed. In standalone use, absent options select loopback authorities
with the configured MCP port. The add-on defaults use its port 8100. If using a
different port with explicit options, configure the matching authorities.

The host list **replaces** defaults. It must be nonempty. Add each actually used
internal hostname, LAN authority or proxy target explicitly, including its port
where received. For example, an isolated test installation might add
`engineering.example.invalid:8100`, `192.0.2.10:8100` or
`[2001:db8::10]:8100`. These reserved examples are not deployment defaults.
No CIDR, wildcard/suffix, automatic discovery, DNS lookup, outbound Core URL or
trusted-forwarder inference supplies an allowed Host.

Absent Origin is permitted for native clients. Empty `mcp_allowed_origins` means
every **present** Origin is refused. For a browser MCP client, configure its
exact HTTP(S) origin, for example `https://client.example.invalid`; this does not
add CORS permissions. The independently authenticated approval panel on ingress
port 8110 does not use this MCP origin list. Its existing peer, ingress path,
user, CSRF and approval controls remain unchanged.

## Parsing and refusal

Each option is a JSON list of at most 32 strings, each at most 512 ASCII bytes,
with a combined selected-value bound of 32,768 bytes. The compiled policy is
immutable. Invalid types, nested values, malformed entries, empty host lists
and semantic duplicates refuse startup. Values are never coerced or truncated.
Absent options choose strict defaults, not unrestricted access.

Host must occur exactly once. Names are ASCII DNS labels (including explicitly
encoded punycode), strict dotted-decimal IPv4 or bracketed IPv6, with an optional
decimal port in 1..65535. DNS case and one final DNS dot normalize consistently;
IPv6 text normalizes to its address. Numeric shorthand, octal/hex-like IP forms,
zone identifiers, userinfo, wildcards, whitespace, controls and URL components
are refused. A missing Host port is distinct from an explicit port: configure
both forms only if both are required. No scheme is inferred from proxy headers.

Origin may be absent, but a present Origin must occur exactly once and match a
configured serialized HTTP(S) origin. Scheme and host are case-insensitive;
default ports 80/443 have their usual origin equivalence. Empty/`null`, opaque,
duplicate, multi-origin, non-ASCII, credential-bearing and path/query/fragment
values are refused. A trailing slash is a path and is not an origin entry.

The application inspects at most 256 header entries with names at most 256 bytes.
Host/Origin values have the 512-byte bound. Duplicate values are refused even
when identical. The HTTP parser can reject malformed requests before ASGI;
application tests and actual parser tests establish different boundaries.

| Received request | HTTP result |
|---|---|
| Invalid or unapproved present Origin | 403 |
| Missing, duplicate or malformed Host | 400 |
| Well-formed unlisted Host | 421 |
| Header-entry bound exceeded | 431 |
| Malformed ASGI header envelope | 400 |

An invalid present Origin has precedence over Host errors after a valid bounded
header envelope is scanned. Replies are fixed small transport errors, with
existing bounded request correlation; no request body is read to find an RPC id.
They do not claim a tool ran or an action failed after execution. For allowed
headers, wrong-secret refusal/throttling and normal authenticated rate limits
continue unchanged. A valid secret cannot bypass the header policy.

## Forwarders and diagnostics

The MCP listener explicitly sets Uvicorn `proxy_headers=False`, for both dormant
and separately armed diagnostic compositions. `FORWARDED_ALLOW_IPS` cannot make
Uvicorn rewrite that listener's peer or scheme. Forwarded Host, scheme and address
headers never substitute for received Host/Origin. Existing optional CF
client-address accounting keeps its separate direct-peer/CIDR contract; this
change enables no trust setting or new proxy route. Approval-listener proxy
handling remains unchanged.

The startup record reports enabled/valid status, host-policy source and counts,
without option values. Rejections use a fixed reason category, request id,
status and generic invalid-request classification. They produce one bounded
audit record and log event, without raw headers, paths, URLs, credentials,
arbitrary exception text or a new per-client state store. Existing audit
configuration and general retention remain in effect.

A webhook relay may replace Host with its configured destination and may merge
or remove headers. Therefore the public connector hostname is not necessarily
the Host Engineering receives. Do not guess a Nabu Casa suffix allowlist.
Receiver validation cannot reconstruct upstream Host or duplicates already
discarded. A successful native no-Origin capture does not prove rejection of a
hostile browser Origin through the hosted chain. Keep that limitation explicit
until separately authorized deployed-path evidence establishes it.

The private observer is retained dormant for troubleshooting, with its existing
source-bound arm, expiry, export and replay controls. It grants no request
authority and cannot bypass this policy. This source change does not arm it or
delete any diagnostic history. See [INBOUND_TOPOLOGY_CAPTURE.md](INBOUND_TOPOLOGY_CAPTURE.md).

## Migration, validation and recovery

Before later release/deployment, inventory every actually used MCP destination
and browser origin. Strict defaults can refuse previously accepted LAN/tunnel
aliases; prepare exact option values before that change. Validate current-state
configuration and independently reachable HA management access. Invalid options
can prevent both add-on listeners from starting, so the approval panel is not
the recovery interface for a startup failure. Correct only the reviewed options
through independent management, under separate live authorization.

Source acceptance requires positive native/browser/alias controls, hostile and
malformed refusals, real production factory/HTTP-parser tests, unchanged
authentication/approval/provider boundaries, cancellation and settled resources.
Exact-image tests must exercise both supported architectures and existing CI
lanes. Later installed acceptance separately binds source/image and proves the
actual connector/catalog/reads, ingress continuity and authorized negative
requests through the relevant path. No household mutation canary is necessary
to test header enforcement. Do not close issue #62 from source-only evidence.

Local recovery is an ordinary reviewed source revert. Installed recovery needs
its own compatible artifact/configuration decision; returning to 2.2.2 restores
its known missing Host/Origin enforcement. Do not use wildcard trust, disable
checks, switch endpoints/providers or delete caches to manufacture a pass.
