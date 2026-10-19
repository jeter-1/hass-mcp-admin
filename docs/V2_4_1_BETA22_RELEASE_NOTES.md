# Engineering 2.4.1-beta.22 release notes

Engineering 2.4.1-beta.22 is a dependency security release (HAMCP-211). It
upgrades `aiohttp` from 3.14.3 to 3.14.4, which fixes three advisories that
`pip-audit --strict` reports against 3.14.3:
- GHSA-2g87-pp6c-29x5;
- CVE-2026-111063;
- CVE-2026-111064.

aiohttp 3.14.4 requires `yarl>=1.25.1`, so `yarl` moves from 1.24.5 to 1.25.1.
No other package changes.

**beta.21 was superseded before publication.** 2.4.1-beta.21 (HAMCP-205) was
merged to `main` but never published: its publish run failed the
`pip-audit --strict` gate on these advisories. Josh decided that it is not
re-tagged or re-published. beta.22 is the next published beta and includes:
- HAMCP-205's administrator verification for every Ingress approval page and
  the removal of the anonymous default approver, exactly as described in the
  [2.4.1-beta.21 release notes](V2_4_1_BETA21_RELEASE_NOTES.md);
- the aiohttp and yarl fix described here.

An installation moving from beta.20 to beta.22 receives both. Everything the
beta.21 notes and acceptance contract say about administrator verification
applies to beta.22.

No Engineering runtime code, tool, input schema, description, provider route,
signed registry, HOLD, support window, workflow or stable-v1 change is included
beyond HAMCP-205's. Acceptance:
[`V2_4_1_BETA22_ACCEPTANCE.md`](V2_4_1_BETA22_ACCEPTANCE.md).

## Advisories

All three affect aiohttp 3.14.3 and earlier and are fixed in 3.14.4.

| Advisory | Severity | Summary | Engineering exposure |
| --- | --- | --- | --- |
| GHSA-2g87-pp6c-29x5 | High | Multiple cases of excessive or unbounded memory or CPU use on attacker-controlled requests | Partly applicable. The advisory does not list its cases. The 3.14.4 changelog's resource fixes include client paths Engineering uses: the client WebSocket buffer after a protocol error, small-message and many-small-read WebSocket overhead, the post-upgrade tail limit, and many concatenated compressed members. Engineering's own byte, frame and time bounds already limited these; 3.14.4 removes the remaining library-internal growth. The aiohttp server cases do not apply. |
| CVE-2026-111064 (GHSA-5wr8-gcqf-gqf3) | High | The aiohttp web **server** did not apply its keepalive timeout before the first request data, so an idle connection could stay open indefinitely | Not affected. Engineering's MCP and Ingress servers run on uvicorn and Starlette, not the aiohttp server. Only test-only loopback peers use it. |
| CVE-2026-111063 (GHSA-494j-54xm-rwp3) | Low | The `Secure` attribute was not enforced for shared cookies (no `Domain` attribute), which could be sent over plain HTTP | Not affected in practice. Engineering authenticates with a bearer header, never adds cookies, and closes each short-lived session (and its default jar) when its bounded read ends. |

## Dependency change

Only `aiohttp` and `yarl` change.
- `aiohttp` 3.14.3 → 3.14.4, wheel hashes:
  - amd64: `sha256:c5e93ab6e6330dce40f5b43996f1fc5f2543296d263a5e4b59ff09e73870b7d8`
  - arm64: `sha256:322e3d741b3133bcbb981a41d4f9b8ac3545c18de49c662c2b1f4e1c770b98ad`
- `yarl` 1.24.5 → 1.25.1, wheel hashes:
  - amd64: `sha256:c6f117789d22dce188e5754e8bc65b7e6ebf8cb73963b9fa761f672a5883769d`
  - arm64: `sha256:4d781294bb815ecb5ea57ff6bbf8038e0a31a95fdf3e1788f66e0dc100d64b58`
- The 3.14.3 hashes, both 1.24.5 platform hashes and the 1.24.5 py3-none-any
  hash are removed. yarl 1.25.1 is resolved from its platform wheels.
- `requirements.txt`, both locks and `build-inputs.json` change. Every lock
  entry was rendered by `scripts/prepare_build_inputs.py` from pip reports for
  both architectures, and `build_inputs.py source` passes with 39
  distributions.
- aiohttp 3.14.4's only `Requires-Dist` change is the yarl floor. yarl 1.25.1
  declares the same `Requires-Dist` as 1.24.5. `multidict`, `propcache` and
  every other runtime or test-only package are unchanged.

aiohttp 3.14.5 is also available; it only widens the `multidict` upper bound.
Josh chose 3.14.4.

## Library behaviour changes Engineering inherits

Each change in aiohttp 3.14.4 and yarl 1.25.1 was compared against
Engineering's use. Engineering uses aiohttp only as a client, always with
`allow_redirects=False`, a redirect-refusing trace or middleware, no automatic
decompression and its own byte bounds. Engineering adapts none of these.

1. **Header values lose trailing whitespace (C parser).** aiohttp's compiled
   HTTP parser now strips trailing whitespace from header values, matching
   its pure-Python parser and RFC 9110 §5.5.
   - Engineering's identity check (`Content-Encoding` must be absent or
     `identity`, ignoring case) now reads a response labelled `identity `
     (trailing whitespace) as identity. 3.14.3 refused it as an encoded body.
   - Every real coding (`gzip`, `br`, a list) is still refused, with or
     without whitespace.
   - Pinned by `ContentEncodingWhitespaceTests`.
2. **yarl `URL.query` drops empty fields and decodes invalid UTF-8 escapes to
   U+FFFD.** `?&a=1&` now parses as `[("a", "1")]`, and `?a=%ff` as
   `[("a", "�")]`.
   - Engineering never emits either form. Its query keys are fixed source text
     and its values are percent-encoded.
   - HAMCP-200's `request_url` compares the route's exact encoded text before
     the parsed query, so a raw empty field or invalid escape is still refused
     as `route_mismatch`.
   - A differential run of 9,440 Engineering route cases (4 bases, 6
     templates, 47 values, 10 queries) gave identical accept and refuse
     results and URLs on 1.24.5 and 1.25.1.
   - Pinned by `RouteQueryTests`.
3. **WebSocket reader.** A new data frame between the fragments of a message
   is a protocol error (1002), and the message-size check is rewritten to be
   overflow-safe.
   - The size threshold is unchanged.
   - Engineering's bounded inspection read still maps a message over its byte
     limit to `response_bytes`, and an error or control frame to
     `malformed_response`. Both 3.14.3 and 3.14.4 behave this way for the
     tested sequences.
   - Pinned by `WebSocketFrameBoundTests`.
4. **Internal retry on a reused connection was rewritten.** aiohttp retries
   only an idempotent request whose reused keep-alive connection drops. It now
   reuses the request body, and refuses to retry a consumed one.
   - The REST client's single-read and logbook paths, the Alarmo, audit
     baseline and dashboard analysis transports, and the core-log reader
     convert a disconnect before that loop. A dropped request still reaches
     the peer exactly once. Pinned by `SingleAttemptTests`.
   - Other bounded GET probes keep 3.14.3's single retry of an idempotent
     GET on a reused connection.
   - POST is never retried, and no Engineering request sends a file-object
     body.
5. **Not reachable from Engineering:**
   - redirect `Location` resolution (redirects are refused);
   - `Set-Cookie` parsing and the cookie limits (no cookies are used);
   - `UploadTracker` and the deprecated upload attributes;
   - `readuntil`;
   - multipart;
   - every aiohttp web-server fix (no aiohttp server in the runtime).
   - Mixed-case `Content-Encoding` decompression is not reachable because
     automatic decompression is off.

Unchanged:
- REST, Supervisor and WebSocket timeouts;
- redirect refusal;
- streaming body bounds;
- the yarl host, port and path parsing of Engineering's base URLs.

## Preserved

All of the following are unchanged from beta.21:
- the public tool catalog and every descriptor;
- the Core profiles, probe and signed fingerprints;
- the signed ha-mcp registry, the 8.6.0 HOLD and the support window;
- storage formats, workflows and pinned runners;
- stable v1.1.2.

Stable v1 (`hass_mcp_admin/`) pins `aiohttp==3.9.5`, which is inside all three
advisories' affected range. It is frozen and retired, so it is not changed
here. Josh needs to decide separately what to do about it.

## Compatibility and rollback

- **Stored formats** are unchanged from beta.21 (and therefore from beta.20).
- **Rollback** is reinstalling the beta.20 artifact, the last published beta.
  That restores aiohttp 3.14.3 and yarl 1.24.5 with their advisories, and
  removes HAMCP-205's administrator verification with the consequences the
  beta.21 notes describe.
