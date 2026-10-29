# Engineering 2.4.1-beta.48 release notes

Engineering 2.4.1-beta.48 lets a full automation baseline capture complete
its final fence (HAMCP-248). It follows 2.4.1-beta.47 and includes it. It
adds no Home Assistant write, tool, input schema, route, provider, Core
command or signed registry change. The 45 s collection budget, the
concurrency limit, the reserve, resume semantics, the D4 refusals and the
stored format are unchanged.

Acceptance: [`V2_4_1_BETA48_ACCEPTANCE.md`](V2_4_1_BETA48_ACCEPTANCE.md).

## Why

On beta.45 (2026-10-29, the owner's installation, 116 automations) every
full `capture_automation_baseline` ended with `final_fence: "unavailable"`,
`final_fence_reason: "malformed_response"` and the installation
`unestablished`. That included a capture with no timed-out record and
`timeout_occurred: false`. A resume that re-read only 13 records completed its
final fence and established the installation.

## Root cause

The capture opens one websocket and authenticates it before the first
fence. After the first fence's last command, the websocket is idle for the
whole configuration collection (about 35 to 40 s on 116 automations). In
the add-on, that websocket is the Supervisor's Core proxy, which runs with a
30 s heartbeat: after 30 s without a client frame it sends a PING. The
capture websocket does not answer pings automatically, so the PING waited
in the queue. The final fence's first read (`auth/current_user`) received
the PING instead of its result. The capture refused every frame that was not
TEXT as `malformed_response`, so the fence failed after a single request.
That is why capture 2 made 123 requests: the authentication attempt, 5
first-fence reads, 116 configuration reads and 1 final-fence read. A
successful full capture makes 127. A 13-record resume never idles for 30 s,
so it never saw a PING.

An offline loopback reproduction reproduced capture 2 exactly. It used the
real capture client and provider against a fake Home Assistant whose
websocket has the proxy's 30 s heartbeat, with 116 configurations at
1.0 to 1.5 s each, 4 concurrent reads, 3.8 MB of states, 3,116 registry
rows and the real 45 s budget. The result was 36.7 s, 123 requests, nothing
incomplete, no timeout, `unavailable` / `malformed_response` and
`unestablished`. Without the heartbeat, or with 13 records, the fence
completed.

## The change

**Keepalive is answered.** A capture websocket read now handles keepalive
frames:
- A PING is answered with its own payload.
- An unsolicited PONG is skipped.
- At most four keepalive frames are handled per read.
- The handling stays within that read's deadline, and each frame's bytes are
  charged to the capture's byte budget.

No Home Assistant command is sent. This narrowly reverses the HAMCP-228
choice "control frames are refused, not automatically ponged". Answering a
PING is a websocket control reply on the same authenticated connection.
Once the PING is answered, the proxy no longer closes the connection within
a 45 s capture.

**Each failure keeps its own reason.** A frame that is not TEXT and not
keepalive is refused with the reason of what it is:

| Frame | Reason |
| --- | --- |
| A close, or a transport error | `source_unavailable` |
| A frame over the 4 MiB frame bound | `response_limit` |
| A binary frame, a websocket protocol error, or more keepalive frames than the bound | `malformed_response` |

A timeout stays `timeout`, and a cancellation propagates.
`malformed_response` now covers only a response that is really malformed.

**Compare rows name only their true reasons.** An `UNKNOWN` row whose
digest is missing on either side no longer also claims
`record_fingerprint_model_mismatch`, because a missing digest has no
fingerprint model. In a recorded-fingerprint compare, the same applies to
the per-row `fingerprint_model_mismatch`. A caller-supplied model that
differs from the contract model still marks every row. Two digests of
different models remain a mismatch.

**Documentation.** The capture contract now describes the keepalive
handling. It also explains that a resumed capture inherits its base's start
time. A capture taken between the base and its resume therefore overlaps
the resume, and comparing the two correctly yields
`capture_intervals_not_ordered`. Compare a resume with a capture that starts
after the resume ends. The published beta.45 acceptance is not rewritten.

## Not changed

- The 45 s collection budget, `CONCURRENCY`, the read deadline, the reserve
  formula and the window.
- Resume semantics and the D4 refusals.
- The stored format and every other tool.
- Identity drift, authority loss, access denial and an unverifiable identity
  still refuse the capture with nothing stored.
- Short-lived websockets elsewhere in the add-on keep their existing frame
  handling. They do not idle for 30 s.

## Rollback

Reinstall 2.4.1-beta.47. No stored record changes shape. Captures stored by
beta.48 read the same on beta.47.
