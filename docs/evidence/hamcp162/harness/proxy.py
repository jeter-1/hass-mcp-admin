#!/usr/bin/env python3
"""HAMCP-162 D1: logging HTTP + WebSocket forwarder between ha-mcp and Core.

Runs inside the Core image (aiohttp) on the internal capture network only.
Every request/response and every WebSocket frame is appended to a JSONL log.
Credentials are scrubbed before anything is written: the Authorization
header, the component caller-token field, WebSocket auth access tokens and
every token value learned at runtime (the long-lived token supplied through
the local control endpoint and the caller token observed in the
get_caller_token response) are replaced everywhere in the logged text.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time

import aiohttp
from aiohttp import web

UPSTREAM = os.environ.get("PROXY_UPSTREAM", "http://hamcp162-core:8123")
LOG_PATH = os.environ.get("PROXY_LOG", "/state/proxy.jsonl")
INLINE_LIMIT = 262144
LEARNED: set[str] = set()
SEQ = 0
_lock = asyncio.Lock()
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailers", "transfer-encoding", "upgrade", "host", "content-length",
       "content-encoding", "accept-encoding"}


def scrub_text(text: str) -> str:
    for tok in sorted(LEARNED, key=len, reverse=True):
        if tok:
            text = text.replace(tok, "<redacted:learned-token>")
    return text


def scrub_obj(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("_ha_mcp_token", "access_token") and isinstance(v, str):
                out[k] = "<redacted:%s>" % k
            else:
                out[k] = scrub_obj(v)
        return out
    if isinstance(obj, list):
        return [scrub_obj(v) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def learn_from(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("token", "access_token", "_ha_mcp_token") and isinstance(v, str) and len(v) >= 16:
                LEARNED.add(v)
            learn_from(v)
    elif isinstance(obj, list):
        for v in obj:
            learn_from(v)


def body_record(raw: bytes):
    rec = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if not raw:
        return rec
    try:
        parsed = json.loads(raw)
    except Exception:
        if len(raw) <= INLINE_LIMIT:
            rec["text"] = scrub_text(raw.decode("utf-8", "replace"))
        return rec
    if len(raw) <= INLINE_LIMIT:
        rec["json"] = scrub_obj(parsed)
    else:
        rec["inline"] = False
        rec["json_top_level_keys"] = sorted(parsed) if isinstance(parsed, dict) else None
    return rec


async def write(event: dict):
    global SEQ
    async with _lock:
        SEQ += 1
        event["seq"] = SEQ
        event["t"] = round(time.time(), 3)
        line = json.dumps(event, ensure_ascii=True, sort_keys=True)
        line = scrub_text(line)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def safe_headers(headers):
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk == "authorization":
            out[k] = "<redacted:authorization>"
        elif lk in ("content-type", "accept", "user-agent", "mcp-session-id", "x-ha-access"):
            out[k] = scrub_text(v)
    return out


async def handle_ws(request: web.Request, session: aiohttp.ClientSession):
    ws_server = web.WebSocketResponse(max_msg_size=0)
    await ws_server.prepare(request)
    conn = f"ws-{id(ws_server):x}"
    await write({"kind": "ws_open", "conn": conn, "path": request.path_qs})
    async with session.ws_connect(UPSTREAM + request.path_qs, max_msg_size=0) as ws_client:
        async def pump(src, dst, direction):
            async for msg in src:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.data
                    try:
                        obj = json.loads(data)
                        learn_from(obj) if direction == "in" and isinstance(obj, dict) and obj.get("type") == "auth" else None
                        rec = {"json": scrub_obj(obj)} if len(data) <= INLINE_LIMIT else {
                            "bytes": len(data), "sha256": hashlib.sha256(data.encode()).hexdigest()}
                    except Exception:
                        rec = {"bytes": len(data)}
                    await write({"kind": "ws_" + direction, "conn": conn, **rec})
                    await dst.send_str(data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await dst.send_bytes(msg.data)
                else:
                    break
        t1 = asyncio.create_task(pump(ws_server, ws_client, "in"))
        t2 = asyncio.create_task(pump(ws_client, ws_server, "out"))
        done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    await write({"kind": "ws_close", "conn": conn})
    await ws_server.close()
    return ws_server


async def handle(request: web.Request):
    session: aiohttp.ClientSession = request.app["session"]
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await handle_ws(request, session)
    raw = await request.read()
    try:
        learn_from(json.loads(raw)) if raw and request.path.endswith("/auth/token") else None
    except Exception:
        pass
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    async with session.request(request.method, UPSTREAM + request.path_qs, data=raw,
                               headers=headers, allow_redirects=False) as resp:
        body = await resp.read()
        if request.path.endswith("/get_caller_token") or request.path.endswith("/auth/token"):
            try:
                learn_from(json.loads(body))
            except Exception:
                pass
        await write({"kind": "http", "method": request.method, "path": scrub_text(request.path_qs),
                     "req_headers": safe_headers(request.headers), "req_body": body_record(raw),
                     "status": resp.status, "resp_headers": safe_headers(resp.headers),
                     "resp_body": body_record(body)})
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
        return web.Response(status=resp.status, body=body, headers=out_headers)


async def control_learn(request: web.Request):
    data = await request.json()
    for tok in data.get("tokens", []):
        if isinstance(tok, str) and len(tok) >= 16:
            LEARNED.add(tok)
    return web.json_response({"learned": len(LEARNED)})


async def control_seq(request: web.Request):
    return web.json_response({"seq": SEQ})


async def control_marker(request: web.Request):
    data = await request.json()
    await write({"kind": "marker", "label": str(data.get("label"))[:200]})
    return web.json_response({"seq": SEQ})


async def main():
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300), auto_decompress=True)
    app = web.Application(client_max_size=1024 ** 3)
    app["session"] = session
    app.router.add_route("*", "/{tail:.*}", handle)
    ctl = web.Application()
    ctl.router.add_post("/learn", control_learn)
    ctl.router.add_get("/seq", control_seq)
    ctl.router.add_post("/marker", control_marker)
    r1, r2 = web.AppRunner(app), web.AppRunner(ctl)
    await r1.setup(); await r2.setup()
    await web.TCPSite(r1, "0.0.0.0", 8124).start()
    await web.TCPSite(r2, "0.0.0.0", 8125).start()
    print("proxy ready", file=sys.stderr, flush=True)
    await asyncio.Event().wait()


asyncio.run(main())
