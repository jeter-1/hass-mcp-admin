# HAMCP-371 S12: the P9 socket record

S12 writes observation P9 of the plan (research G ruling 4): the ha-mcp
peer's WebSocket to the disposable Core on 9.2.0, with the same sequence on
9.1.2 beside it. It continues the S10 and S11 commits under the architect's
2026-11-15 14:45 CST instruction, relayed by Josh: "CONTINUE: D5-B, T129 and
E1-R; then finish S10; then S11 onward, stopping after SR."

This step writes documents only. No lane was run for it, and no Home
Assistant instance was accessed. Everything below comes from the S6 runs
already committed under `../s6/`, plus the S6 peer logs. The peer logs were
not committed in S6; S12 checks each one against the hash in the committed
run `SHA256SUMS` before reading it.

The optional part of the S12 plan row (a 9.2.0 row in
`scripts/disposable_ha_websockets_probe.py` and its test) was not done.

## Files

| File | What it is |
| --- | --- |
| `p9_record.py` | read-only builder; reads the committed S6 receipts and per-call files and, with `--peer-logs`, the S6 peer logs, refusing any log whose sha256 differs from the committed run `SHA256SUMS` |
| `p9-record.json` | its output for the four S6 runs that P9 uses, with only the socket lines of each peer log (see (f)) |

To rebuild, from the worktree root, with the S6 peer logs in their scratch
location:

```
python3 -I docs/evidence/hamcp371/s12/p9_record.py docs/evidence/hamcp371/s6 \
  --peer-logs .artifacts/hamcp371-s6
```

The S6 runs used:

| Key in `p9-record.json` | Run | Profile | Versions |
| --- | --- | --- | --- |
| `A 9.1.2`, `A 9.2.0` | `s6/run-a-1` | A | 9.1.2 then 9.2.0 against one disposable Core |
| `B-9.1.2 9.1.2` | `s6/run-b-9.1.2-1` | B, component 9.1.2, D1 flags | 9.1.2 |
| `B-9.2.0 9.2.0` | `s6/run-b-9.2.0-2` | B, component 9.2.0, D1 flags | 9.2.0 |

`run-b-9.2.0-1` was the run before the D1 flags and is not used.

## (a) Python and websockets, read from the images

| | 9.1.2 | 9.2.0 |
| --- | --- | --- |
| `PYTHON_VERSION` (image default environment, `upstream_images.<v>.image_defaults.environment`) | 3.13.18 | 3.14.10 |
| ha-mcp `dist-info` in the image | `/app/.venv/lib/python3.13/site-packages/ha_mcp-9.1.2.dist-info` | `/app/.venv/lib/python3.14/site-packages/ha_mcp-9.2.0.dist-info` |
| image `ha_mcp/_vendor/websockets/VENDORED` sha256 | `a6c00001…a404` | `85472f7f…58c1` |
| image `ha_mcp/_vendor/requirements.txt` sha256 | `c0daa762…1327` | `9af7f688…62b1` |
| the same two files at the tag | v9.1.2: equal hashes; `VENDORED` reads `websockets==17.2` | `51e3aac7` (v9.2.0): equal hashes; `VENDORED` reads `websockets==17.4` |

The vendored hashes come from the profile B package copy, which is taken from
the pinned image export (`profile_b.package_copy.records` in each B receipt).
The tag files were read from the disposable ha-mcp clone with
`GIT_NO_LAZY_FETCH=1 git show <tag>:src/ha_mcp/_vendor/...` and hashed. In
both images websockets is a vendored copy, not an installed distribution
(S5 README, inventory-2). So the websockets version is read from the image
through file identity with the tag: the image file has the same sha256 as
the tagged file, which carries the version text.

## (b) Socket-borne reads served with the socket up

The S2 source table (`../s2-source-findings.md`, section (a)) lists 24
admitted reads that can reach the socket at v9.2.0: 9 websocket only and 15
websocket and REST. In every run below, each of these 24 reads completed with
no exchange failure, with the socket connected and authenticated earlier in
the same peer log:

| Run | Reads | `isError` | Exchange failures |
| --- | --- | --- | --- |
| A 9.1.2 | 24 | 1 (`r27-apps`) | 0 |
| A 9.2.0 | 24 | 1 (`r27-apps`) | 0 |
| B 9.1.2 | 24 | 1 (`r27-apps`) | 0 |
| B 9.2.0 | 24 | 1 (`r27-apps`) | 0 |

- `r27-apps` (`ha_get_app`, websocket only, `supervisor/api`) returns
  `SERVICE_CALL_FAILED` "Command failed: Unknown command." on both versions.
  The peer logs the matching line `WebSocket message failed (supervisor/api)`.
  This is the expected result with no Supervisor (05:35 ruling: "not
  replayable in the local lane (no Supervisor)"). It shows that the socket
  carried the command and Core answered it.
- The other 23 return `success: true`, except `r16-history` and `r19-state`.
  Those two return their `data`/`metadata` envelope, which has no `success`
  field, with `isError` false.
- Per read rows are under `runs.<key>.socket_reads` in `p9-record.json`.

The lane sees the peer only from outside, through its MCP replies and its log.
So this is evidence that the reads succeeded while the socket was up. It is
not a packet-level proof that each one used the socket (plan P9 notes;
inference).

## (c) One large socket result

The plan names an unfiltered registry or services read. Sizes are MCP response
bodies as the lane received them, not socket frame sizes. The socket frames
inside the peer are not visible to the lane.

| Read | A 9.1.2 | A 9.2.0 | B 9.1.2 | B 9.2.0 |
| --- | --- | --- | --- | --- |
| `t-large-services` (`ha_list_services`, full detail; `GET /services` plus `frontend/get_translations` over the socket) | 36,672 | 42,952 | 34,042 | 70,046 |
| `r26-integrations` (`ha_get_integration`; largest of the 24 reads in every run) | 11,834 | 11,834 | 13,518 | 13,520 |
| `r13-devices` (`ha_get_device`, websocket only, device and entity registry) | 2,010 | 2,010 | 2,614 | 2,614 |

All answered with no exchange failure and `isError` false. The seeded Core is
small, so no registry read was large. The largest websocket-only body is under
3 KB. A large registry result over the socket is unobserved until SR or live
acceptance. Why `t-large-services` on B 9.2.0 is about twice the B 9.1.2 size
was not investigated here; it is a response-content difference, recorded for
SR.

## (d) One read after an idle wait

`t-after-idle` (`ha_get_entity`, websocket only) ran on the same MCP session
after `idle_seconds_before` 75.0. It answered normally in every run (`success`
true, `isError` false, 0.005 to 0.008 s). The keepalive interval at both tags
is `ping_interval=30`, `ping_timeout=10`
(`src/ha_mcp/client/websocket_client.py:238-239` at v9.1.2 and `51e3aac7`).
So the wait covered at least two ping intervals. No socket line appears in
any peer log between the read before the wait and the read after it.

## (e) Core restart and the first read after it

For each version the lane restarted the disposable Core once, then made two
reads: `a-entity-after-core-restart` (`ha_get_entity`) and
`a-floors-areas-after-core-restart` (`ha_list_floors_areas`), both websocket
only. Times are the HTTP `Date` headers of the replies (one-second
resolution) and the receipt's post-restart network check, all in UTC:

| Run | `t-after-idle` reply | Network check after restart | First read after restart | Its time | Second read |
| --- | --- | --- | --- | --- | --- |
| A 9.1.2 | 2026-11-12 06:23:27 | 06:23:43 | 06:23:48, `success` true | 0.028 s | `success` true, 0.008 s |
| A 9.2.0 | 2026-11-12 06:25:12 | 06:25:26 | 06:25:32, `success` true | 0.029 s | `success` true, 0.006 s |
| B 9.1.2 | 2026-11-15 05:08:53 | 05:09:07 | 05:09:12, `success` true | 0.028 s | `success` true, 0.004 s |
| B 9.2.0 | 2026-11-15 05:04:36 | 05:04:51 | 05:04:56, `success` true | 0.027 s | `success` true, 0.004 s |

The restart falls between the last capture call and the network check. The
last capture call is the latest `Date` header under `calls/`: `t-new-session`
on A (06:23:28 and 06:25:12), `y-group` on B (05:08:53 and 05:04:36). The
receipts do not record when the restart began or when Core was ready again.

The peer reconnects. In every log the next socket lines after the restart are
"WebSocket disconnected", "Connecting to Home Assistant WebSocket" and
"WebSocket connected and authenticated successfully", all stamped with the
second of the first read's `Date` header (for example A 9.2.0: 06:25:32 in
both). Between the last capture call and that second, the peer logged no socket
line, so it logged no disconnect when Core stopped.

Inference: the peer found the stale socket when the first read needed it and
reconnected inside that read. That read took 0.027 to 0.029 s, against 0.004
to 0.008 s for the second, so seen from the lane the reconnect costs about
20 ms. How long after Core was ready this happened is not known; it is at most
the 5 to 6 s between the network check and the first read. 9.1.2 and 9.2.0
behave alike.

## (f) Peer log lines for connect, close and reconnect

`runs.<key>.peer_log.socket_lines` in `p9-record.json` keeps 16 lines per run.
They are the lines whose message starts with one of the `LOG_MESSAGES` in
`p9_record.py`:
- connecting;
- connected and authenticated;
- disconnected;
- reconnected or reconnection failed;
- `WebSocket message failed (`.

Each line is reduced to its timestamp, logger, level and message. The peer log
timestamps are UTC.

Per run: 5 connects, 5 authenticated, 4 disconnected, and 2 message-failure
lines. The message failures are `supervisor/api` for `r27-apps` and
`config/entity_registry/get` "Entity not found" for the P4 absent-entity call.
No run has a "reconnected successfully" or "reconnection failed" line: the
reconnect after the restart is logged as a plain disconnect and connect.

The full logs stay uncommitted in scratch; their sha256 values are in the
committed run `SHA256SUMS` and are repeated in `p9-record.json`.

## Not produced, and why

- Reconnect delay from Core ready: unknown, at most 5 to 6 s (see (e)); the
  receipts have no restart or Core-ready timestamp.
- A large registry result over the socket: not produced, because the seeded
  Core is small (see (c)).
- Per-read transport inside the peer: not observable from the lane (see (b)).
- The optional probe row: not done.
- P10 (the same sequence with Engineering in front) is step SR, not S12.

## Validation

Run 2026-11-15 on local HEAD `e6474aad` (base `origin/main` `e741f2ed`):

- the rebuild command above, from this directory: exit 0; the four peer logs
  hashed to their committed `SHA256SUMS` values; the output is byte-identical
  to the scratch draft it replaces;
- the spot checks in (b), (e) and (f) were read back from `p9-record.json`
  (24 reads, 1 `isError`, 0 exchange failures, 16 socket lines per run; the
  restart reads' `Date` headers and times);
- `s6/comparison/scan.py` on the three files: 3 scanned, 0 hits; the leak
  check passed on each file.

No test module covers `p9_record.py`; it is an evidence builder, not product
code. No lane, Home Assistant instance or heavy job was run for S12.
