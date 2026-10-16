#!/usr/bin/env python3
"""HAMCP-162 D1: capture driver (runs in the Core image on the internal network).

Subcommands:
  setup                 onboard the throwaway Core, mint a long-lived token,
                        create the "File & YAML Tools" entry, write the env file
  catalog LABEL         initialize + tools/list against $MCP_URL, save in full
  cases LABEL           run the full case suite against $MCP_URL
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

import aiohttp

HA = os.environ.get("HA_URL", "http://hamcp162-core:8123")
PROXY_CTL = os.environ.get("PROXY_CTL", "http://hamcp162-proxy:8125")
MCP_URL = os.environ.get("MCP_URL", "")
STATE = Path("/state")
CONFIG = Path("/config")
FIX = Path("/fixtures")
OUT = Path("/out")
CLIENT_ID = "http://hamcp162-core:8123/"
INLINE = 262144


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


async def jreq(s, method, path, *, json_body=None, data=None, token="", ok=(200, 201)):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with s.request(method, HA + path, json=json_body, data=data, headers=headers) as r:
        body = await r.read()
        if r.status not in ok:
            raise RuntimeError(f"{method} {path} -> {r.status}: {body[:300]!r}")
        return json.loads(body) if body else None


async def setup():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        for _ in range(240):
            try:
                async with s.get(HA + "/api/onboarding") as r:
                    if r.status == 200:
                        break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Core not ready")
        onboarding = await jreq(s, "GET", "/api/onboarding")
        assert not any(i.get("done") for i in onboarding if i.get("step") == "user"), "not fresh"
        user = await jreq(s, "POST", "/api/onboarding/users", json_body={
            "client_id": CLIENT_ID, "name": "HAMCP-162 D1 synthetic admin",
            "username": "d1_" + secrets.token_hex(4), "password": secrets.token_urlsafe(24),
            "language": "en"})
        tok = await jreq(s, "POST", "/auth/token", data={
            "grant_type": "authorization_code", "code": user["auth_code"], "client_id": CLIENT_ID})
        access = tok["access_token"]
        await jreq(s, "POST", "/api/onboarding/core_config", json_body={}, token=access)
        await jreq(s, "POST", "/api/onboarding/integration",
                   json_body={"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID}, token=access)
        await jreq(s, "POST", "/api/onboarding/analytics", json_body={}, token=access)
        async with s.ws_connect(HA + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": access})
            assert (await ws.receive_json())["type"] == "auth_ok"
            await ws.send_json({"id": 1, "type": "auth/long_lived_access_token",
                                "client_name": "hamcp162-d1", "lifespan": 2})
            llat = (await ws.receive_json())["result"]
        env = STATE / "token.env"
        env.write_text(f"HOMEASSISTANT_TOKEN={llat}\n")
        os.chmod(env, 0o600)
        async with s.post(PROXY_CTL + "/learn", json={"tokens": [access, llat]}) as r:
            await r.read()
        flow = await jreq(s, "POST", "/api/config/config_entries/flow",
                          json_body={"handler": "ha_mcp_tools"}, token=llat)
        steps = [{"type": flow.get("type"), "step_id": flow.get("step_id"),
                  "menu_options": flow.get("menu_options")}]
        flow = await jreq(s, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json_body={"next_step_id": "tools"}, token=llat)
        steps.append({"type": flow.get("type"), "step_id": flow.get("step_id"),
                      "data_schema": flow.get("data_schema")})
        if flow.get("type") == "form":
            flow = await jreq(s, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                              json_body={}, token=llat)
        steps.append({"type": flow.get("type"), "title": flow.get("title"),
                      "result_keys": sorted((flow.get("result") or {}).keys())})
        entries = await jreq(s, "GET", "/api/config/config_entries/entry?domain=ha_mcp_tools", token=llat)
        cfg = await jreq(s, "GET", "/api/config", token=llat)
        services = await jreq(s, "GET", "/api/services", token=llat)
        comp_services = sorted(next((d["services"] for d in services if d["domain"] == "ha_mcp_tools"), {}).keys())
        info = {"core_version": cfg.get("version"), "components": sorted(cfg.get("components", [])),
                "config_flow_steps": steps,
                "entries": [{k: e.get(k) for k in ("domain", "title", "state", "source", "version")}
                            for e in entries],
                "ha_mcp_tools_services": comp_services}
        (OUT / "setup.json").write_text(json.dumps(info, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"core": info["core_version"], "entries": info["entries"],
                          "services": comp_services}))


class MCP:
    def __init__(self, s, url):
        self.s, self.url, self.sid, self.n = s, url, None, 0

    async def post(self, body):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.sid:
            headers["mcp-session-id"] = self.sid
        async with self.s.post(self.url, data=json.dumps(body).encode(), headers=headers) as r:
            if "mcp-session-id" in r.headers:
                self.sid = r.headers["mcp-session-id"]
            raw = await r.read()
            return r.status, r.headers.get("content-type", ""), raw

    async def rpc(self, method, params):
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}
        status, ctype, raw = await self.post(body)
        msg = None
        if ctype.startswith("text/event-stream"):
            for block in raw.decode("utf-8").split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
                if data:
                    obj = json.loads(data)
                    if obj.get("id") == self.n:
                        msg = obj
        elif raw:
            msg = json.loads(raw)
        return {"http_status": status, "content_type": ctype.split(";")[0], "raw_bytes": len(raw),
                "raw_sha256": sha(raw)}, msg

    async def notify(self, method, params=None):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        status, _, _ = await self.post(body)
        return status

    async def initialize(self):
        meta, msg = await self.rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "hamcp162-d1-capture", "version": "1"}})
        await self.notify("notifications/initialized")
        return meta, msg

    async def list_tools(self):
        tools, cursor, pages = [], None, 0
        while True:
            meta, msg = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            pages += 1
            tools.extend(msg["result"]["tools"])
            cursor = msg["result"].get("nextCursor")
            if not cursor:
                return tools, pages


async def mcp_session(s):
    for _ in range(120):
        try:
            m = MCP(s, MCP_URL)
            meta, init = await m.initialize()
            if init and "result" in init:
                return m, init
        except Exception:
            pass
        await asyncio.sleep(1)
    raise RuntimeError("MCP not ready")


async def catalog(label):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        m, init = await mcp_session(s)
        tools, pages = await m.list_tools()
        rec = {"label": label, "mcp_url_path": MCP_URL.split("8086", 1)[-1],
               "initialize_result": init["result"], "tools_list_pages": pages,
               "tool_count": len(tools), "tools": tools, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (OUT / f"catalog-{label}.json").write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=True) + "\n")
        print(json.dumps({"label": label, "server": init["result"].get("serverInfo"),
                          "tools": len(tools)}))


# ------------------------------------------------------------------ cases
def E(path, file="configuration.yaml"):
    return {"file": file, "yaml_path": path, "include_content": False, "include_parsed": True}


def build_cases():
    C = []

    def add(cid, tool, args, variant="main", note="", prep=None):
        C.append({"id": cid, "tool": tool, "arguments": args, "variant": variant, "note": note, "prep": prep})

    G = "ha_config_get_yaml"
    for p in ["recorder", "homeassistant", "homeassistant.customize", "template", "mqtt",
              "mqtt.password", "mqtt.credentials", "mqtt.credentials.value", "mqtt.port", "x",
              "x.pin", "x.password_null", "x.auth", "x.auth.password", "http", "http.api_key",
              "influxdb", "influxdb.database", "rest", "lists", "refs", "refs.quoted",
              "refs.single_quoted", "refs.pw_default", "refs.password", "forgery", "includes",
              "sensor", "automation", "anchors", "yaml11", "yaml11.yes_", "dotted", "dotted.a.b",
              "empty_values", "empty_values.null_value", "empty_values.empty_map",
              "empty_values.empty_list", "empty_values.empty_string", "top_null", "top_scalar",
              "top_list", "template.0", "rest.0", "rest.0.resource", "corpus", "keys",
              "mqtt_keys", "recorder_sqlite", "recorder.db_url", "missing_key",
              "recorder.missing", "", ".", "recorder.", ".recorder", "a..b",
              "recorder..exclude", " recorder", "Recorder", "recorder ", "*", "recorder.*",
              "r" * 1000, "homeassistant.auth_providers", "recorder.exclude.domains",
              "recorder.exclude.domains.0"]:
        add(f"main:{p[:40]}" + ("" if len(p) <= 40 else f"(len{len(p)})"), G, E(p))
    for k in ["yes_", "no_", "on_", "off_", "y_", "true_", "True_", "octal_0o", "octal_0", "hex",
              "underscore", "sexagesimal", "date", "datetime", "inf", "ninf", "nan", "tilde", "null_",
              "exp", "plus", "bin", "float_", "bigint"]:
        add(f"yaml11key:{k}", G, E(f"yaml11.{k}"))
    for v in ["int_str", "int", "bool", "bool_str", "null", "float", "complex", "date"]:
        add(f"variant:nk_{v}:nk", G, E("nk"), variant=f"nk_{v}")
    add("defaults:recorder", G, {"yaml_path": "recorder"}, note="upstream defaults")
    add("defaults:mqtt", G, {"yaml_path": "mqtt"}, note="upstream defaults")
    add("content+parsed:refs", G, {"yaml_path": "refs", "include_content": True, "include_parsed": True})
    add("content+parsed:anchors", G, {"yaml_path": "anchors", "include_content": True, "include_parsed": True})
    add("neither:recorder", G, {"yaml_path": "recorder", "include_content": False, "include_parsed": False})
    add("schema:yaml_path_int", G, {"yaml_path": 123, "include_content": False, "include_parsed": True})
    add("schema:missing_yaml_path", G, {"file": "configuration.yaml"})
    add("schema:extra_property", G, {**E("recorder"), "path": "secrets.yaml"})
    add("schema:include_parsed_str", G, {"yaml_path": "recorder", "include_parsed": "yes"})
    add("schema:file_null", G, {"yaml_path": "recorder", "file": None})
    files = ["configuration.yml", "Configuration.yaml", "CONFIGURATION.YAML", "./configuration.yaml",
             "/config/configuration.yaml", "configuration.yaml ", "packages/x.yaml",
             "packages/*.yaml", "packages/[b].yaml", "*.yaml", "secrets.yaml", "Secrets.YAML",
             "PACKAGES/../secrets.yaml", "packages/../secrets.yaml", ".storage/core.config",
             "scripts.yaml", "automations.yaml", "configuration.yaml\u0000", "packages\\x.yaml",
             "%2e%2e/secrets.yaml", "//configuration.yaml", "~/configuration.yaml",
             "http://example.invalid/configuration.yaml", "c" * 300 + ".yaml", "approval_pin.json",
             "www/data.yaml", "www/secret_link.yaml", "www/escape.yaml", "../etc/hostname",
             "/etc/hostname", "sensors.yaml", "scenes.yaml", "packages/missing.yaml",
             "home-assistant.log", "www/*.yaml", "packages/link.yaml"]
    ypath = {"scripts.yaml": "synthetic_script", "packages/x.yaml": "x_pkg",
             "packages/*.yaml": "x_pkg", "packages/[b].yaml": "bracket_pkg", "secrets.yaml": "mqtt_password",
             "www/data.yaml": "www_yaml", "www/*.yaml": "www_yaml", "automations.yaml": "0",
             "configuration.yml": "yml_variant", "Configuration.yaml": "case_variant"}
    for f in files:
        add(f"file:{f[:48]}" + ("" if len(f) <= 48 else f"(len{len(f)})"), G, E(ypath.get(f, "recorder"), f))
    for v, p in [("parse_error", "recorder"), ("parse_error", "mqtt"), ("list_root", "0"),
                 ("list_root", "x"), ("scalar_root", "x"), ("empty", "recorder"),
                 ("comment_only", "recorder"), ("duplicate_keys", "recorder"),
                 ("dup_child_keys", "recorder"), ("tag_SECRET", "refs"), ("tag_input", "refs"),
                 ("tag_secret_bare", "refs"), ("tag_on_mapping", "refs"),
                 ("tag_on_sequence", "refs"), ("tag_custom_local", "refs"),
                 ("tag_env_default_multiline", "refs"), ("nonstring_keys", "nk"),
                 ("nonstring_keys", "nk.1"), ("nonstring_keys", "nk.true"),
                 ("nonstring_keys", "nk.True"), ("binary", "recorder"), ("bom", "recorder"),
                 ("crlf", "recorder"), ("latin1_text", "recorder"), ("nodes_4001", "big"),
                 ("depth_25", "deep"), ("alias_blowup", "bomb"), ("large_1mib_value", "big"),
                 ("large_8mib_file", "small"), ("large_8mib_file", "big")]:
        add(f"variant:{v}:{p}", G, E(p), variant=v)
    add("variant:missing_config:recorder", G, E("recorder"), variant=None, prep="remove_config")
    add("variant:packages_custom_dir:pkgdir", G, E("p_pkg", "mypkgs/p.yaml"), variant="main",
        prep="custom_packages_dir")
    add("variant:secrets_list:get_yaml", G, E("0", "secrets.yaml"), prep="secrets_list")
    add("variant:secrets_list:read_file", "ha_read_file", {"path": "secrets.yaml"}, prep="secrets_list")
    add("variant:secrets_symlinked", "ha_read_file", {"path": "secrets.yaml"}, prep="secrets_symlink")
    add("variant:secrets_symlinked:get_yaml", G, E("mqtt_password", "secrets.yaml"), prep="secrets_symlink")
    R = "ha_read_file"
    for p in ["secrets.yaml", "configuration.yaml", "approval_pin.json", ".storage/core.config",
              "www/secret_link.yaml", "www/escape.yaml", "sensors.yaml", "packages/x.yaml",
              "Secrets.yaml", "../etc/hostname", "/etc/hostname", "www/readme.txt", "www/bin.dat",
              "www/data.yaml", "scripts.yaml", "custom_components/ha_mcp_tools/manifest.json",
              "configuration.yml", "packages/link.yaml"]:
        add(f"read_file:{p}", R, {"path": p})
    add("read_file:configuration.yaml#mqtt", R, {"path": "configuration.yaml", "yaml_path": "mqtt"})
    add("read_file:configuration.yaml#empty_path", R, {"path": "configuration.yaml", "yaml_path": ""})
    add("read_file:secrets.yaml#mqtt_password", R, {"path": "secrets.yaml", "yaml_path": "mqtt_password"})
    add("read_file:home-assistant.log#tail3", R, {"path": "home-assistant.log", "tail_lines": 3})
    add("read_file:home-assistant.log#yaml_path", R, {"path": "home-assistant.log", "tail_lines": 2,
                                                      "yaml_path": "x"})
    add("read_file:tail_lines_0", R, {"path": "home-assistant.log", "tail_lines": 0})
    L = "ha_list_files"
    for p, pat in [(".", None), ("www", None), ("www", "*.yaml"), ("packages", None),
                   ("packages", "[b]*"), ("custom_components", None), (".storage", None),
                   ("blueprints", None), ("../", None), ("themes", None)]:
        args = {"path": p} if pat is None else {"path": p, "pattern": pat}
        add(f"list_files:{p}:{pat}", L, args)
    # Component unavailable, then recovered (run last).
    add("unavailable:entry_removed", G, E("recorder"), prep="remove_entry")
    add("unavailable:entry_readded", G, E("recorder"), prep="readd_entry")
    return C


def build_supplemental_cases():
    """Supplement (run after the main set): non-string-key addressing and a symlinked
    configuration.yaml, which the main set did not exercise."""
    C = []

    def add(cid, tool, args, variant="main", note="", prep=None):
        C.append({"id": cid, "tool": tool, "arguments": args, "variant": variant, "note": note, "prep": prep})

    G = "ha_config_get_yaml"
    for v, paths in [("nk_int", ["nk.1", "nk.plain"]), ("nk_int_str", ["nk.1"]),
                     ("nk_bool", ["nk.True", "nk.true"]), ("nk_bool_str", ["nk.True"]),
                     ("nk_null", ["nk.None", "nk.null", "nk.~"]), ("nk_float", ["nk.1.5"]),
                     ("nk_complex", ["nk.('a', 'b')"]), ("nk_date", ["nk.2026-10-16"])]:
        for path in paths:
            add(f"supp:{v}:{path}", G, E(path), variant=v)
    add("supp:config_symlink_inside:recorder", G, E("recorder"), prep="config_symlink_inside")
    add("supp:config_symlink_escape:x", G, E("x"), prep="config_symlink_escape")
    add("supp:config_symlink_inside:read_file", "ha_read_file", {"path": "configuration.yaml"},
        prep="config_symlink_inside")
    return C


def build_supplemental2_cases():
    """Supplement 2: traversal-normalized secrets.yaml reads and configuration.yaml
    symlinks into the deny floor."""
    C = []

    def add(cid, tool, args, variant="main", note="", prep=None):
        C.append({"id": cid, "tool": tool, "arguments": args, "variant": variant, "note": note, "prep": prep})

    G = "ha_config_get_yaml"
    for f in ["packages/../secrets.yaml", "www/../secrets.yaml", "packages/./../secrets.yaml",
              "secrets.yaml"]:
        add(f"supp2:get_yaml:{f}:mqtt_password", G, E("mqtt_password", f))
        add(f"supp2:read_file:{f}", "ha_read_file", {"path": f})
    add("supp2:get_yaml:packages/../secrets.yaml:mqtt_password:content", G,
        {"file": "packages/../secrets.yaml", "yaml_path": "mqtt_password", "include_content": True,
         "include_parsed": True})
    add("supp2:config_symlink_secrets:mqtt_password", G, E("mqtt_password"), prep="config_symlink_secrets")
    add("supp2:config_symlink_storage:data", G, E("data"), prep="config_symlink_storage")
    return C


def install_companions():
    comp = FIX / "companions"
    for p in sorted(comp.rglob("*")):
        if p.is_file():
            dst = CONFIG / p.relative_to(comp)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
    (CONFIG / "www" / "bin.dat").write_bytes(bytes(range(256)) * 4)
    for link, target in [("www/secret_link.yaml", "../secrets.yaml"),
                         ("www/escape.yaml", "/etc/hostname"),
                         ("packages/link.yaml", "../secrets.yaml")]:
        lp = CONFIG / link
        if lp.is_symlink() or lp.exists():
            lp.unlink()
        lp.symlink_to(target)


def set_variant(name):
    data = (FIX / "variants" / f"{name}.yaml").read_bytes()
    (CONFIG / "configuration.yaml").write_bytes(data)
    return {"variant": name, "bytes": len(data), "sha256": sha(data)}


async def prep_op(s, op, llat, state):
    secrets_src = FIX / "companions" / "secrets.yaml"
    if op == "remove_config":
        (CONFIG / "configuration.yaml").unlink()
    elif op == "custom_packages_dir":
        base = (FIX / "variants" / "main.yaml").read_text().replace(
            "packages: !include_dir_named packages", "packages: !include_dir_named mypkgs")
        (CONFIG / "configuration.yaml").write_text(base)
        (CONFIG / "mypkgs").mkdir(exist_ok=True)
        (CONFIG / "mypkgs" / "p.yaml").write_text("p_pkg:\n  value: 9\n")
    elif op == "secrets_list":
        shutil.copyfile(FIX / "companions" / "secrets_list_variant.yaml", CONFIG / "secrets.yaml")
    elif op == "secrets_symlink":
        shutil.copyfile(secrets_src, CONFIG / "real_secrets_store.yaml")
        (CONFIG / "secrets.yaml").unlink()
        (CONFIG / "secrets.yaml").symlink_to("real_secrets_store.yaml")
    elif op in ("config_symlink_inside", "config_symlink_escape", "config_symlink_secrets",
                "config_symlink_storage"):
        cfg = CONFIG / "configuration.yaml"
        cfg.unlink()
        if op == "config_symlink_secrets":
            cfg.symlink_to("secrets.yaml")
        elif op == "config_symlink_storage":
            cfg.symlink_to(".storage/onboarding")
        elif op == "config_symlink_inside":
            (CONFIG / "real_config_store.yaml").write_bytes((FIX / "variants" / "main.yaml").read_bytes())
            cfg.symlink_to("real_config_store.yaml")
        else:
            cfg.symlink_to("/etc/hostname")
    elif op == "remove_entry":
        entries = await jreq(s, "GET", "/api/config/config_entries/entry?domain=ha_mcp_tools", token=llat)
        for e in entries:
            await jreq(s, "DELETE", f"/api/config/config_entries/entry/{e['entry_id']}", token=llat)
        await asyncio.sleep(2)
    elif op == "readd_entry":
        flow = await jreq(s, "POST", "/api/config/config_entries/flow", json_body={"handler": "ha_mcp_tools"}, token=llat)
        flow = await jreq(s, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json_body={"next_step_id": "tools"}, token=llat)
        if flow.get("type") == "form":
            await jreq(s, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", json_body={}, token=llat)
        await asyncio.sleep(2)
    return op


def restore_after(op):
    if op in ("secrets_list", "secrets_symlink"):
        p = CONFIG / "secrets.yaml"
        if p.is_symlink() or p.exists():
            p.unlink()
        shutil.copyfile(FIX / "companions" / "secrets.yaml", p)
        rs = CONFIG / "real_secrets_store.yaml"
        if rs.exists():
            rs.unlink()
    if op in ("config_symlink_inside", "config_symlink_escape", "config_symlink_secrets",
              "config_symlink_storage"):
        (CONFIG / "configuration.yaml").unlink()
        rc = CONFIG / "real_config_store.yaml"
        if rc.exists():
            rc.unlink()
        set_variant("main")
    if op == "custom_packages_dir":
        shutil.rmtree(CONFIG / "mypkgs", ignore_errors=True)


async def proxy_seq(s, label=None):
    if label:
        async with s.post(PROXY_CTL + "/marker", json={"label": label}) as r:
            return (await r.json())["seq"]
    async with s.get(PROXY_CTL + "/seq") as r:
        return (await r.json())["seq"]


def bounded(msg):
    raw = json.dumps(msg, ensure_ascii=True, sort_keys=True).encode()
    if len(raw) <= INLINE:
        return {"inline": True, "message": msg}
    res = msg.get("result", {}) if isinstance(msg, dict) else {}
    sc = res.get("structuredContent")
    summary = {"inline": False, "bytes": len(raw), "sha256": sha(raw),
               "result_keys": sorted(res.keys()) if isinstance(res, dict) else None,
               "isError": res.get("isError") if isinstance(res, dict) else None}
    if isinstance(sc, dict):
        summary["structured_top_level"] = {k: (v if not isinstance(v, (list, dict, str)) or len(json.dumps(v)) < 2000
                                               else {"type": type(v).__name__, "json_bytes": len(json.dumps(v))})
                                           for k, v in sc.items()}
        matches = sc.get("matches")
        if isinstance(matches, list):
            summary["match_shapes"] = [{"keys": sorted(mm.keys()),
                                        "parsed_json_bytes": len(json.dumps(mm.get("parsed"))),
                                        "parsed_sha256": sha(json.dumps(mm.get("parsed"), sort_keys=True, ensure_ascii=True).encode())}
                                       for mm in matches if isinstance(mm, dict)]
    return summary


async def ha_yaml11_view():
    """How HA's own loader reads the yaml11 section (runtime interpretation)."""
    try:
        from homeassistant.util.yaml.loader import parse_yaml
        text = (FIX / "variants" / "main.yaml").read_text()
        start = text.index("yaml11:\n")
        end = text.index("dotted:\n")
        val = parse_yaml(text[start:end])
        return {"ok": True, "yaml11": json.loads(json.dumps(val, default=lambda o: f"<{type(o).__name__}:{o}>"))}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def cases(label):
    llat = (STATE / "token.env").read_text().strip().split("=", 1)[1]
    install_companions()
    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
        m, init = await mcp_session(s)
        current = None
        caseset = {"supp": build_supplemental_cases, "supp2": build_supplemental2_cases}.get(
            os.environ.get("CASESET", ""), build_cases)
        for case in caseset():
            op = None
            if case["variant"] and case["variant"] != current:
                vinfo = set_variant(case["variant"])
                current = case["variant"]
            elif case["variant"] is None:
                vinfo = {"variant": None}
            else:
                vinfo = {"variant": current, "sha256": sha((CONFIG / "configuration.yaml").read_bytes())}
            if case["prep"]:
                op = await prep_op(s, case["prep"], llat, None)
                current = None
            a = await proxy_seq(s, f"{label}:{case['id']}:begin")
            t0 = time.monotonic()
            try:
                meta, msg = await m.rpc("tools/call", {"name": case["tool"], "arguments": case["arguments"]})
                err = None
            except Exception as exc:
                meta, msg, err = None, None, f"{type(exc).__name__}: {exc}"
            dt = round(time.monotonic() - t0, 3)
            b = await proxy_seq(s, f"{label}:{case['id']}:end")
            if op:
                restore_after(op)
            results.append({"id": case["id"], "tool": case["tool"], "arguments": case["arguments"],
                            "note": case["note"], "prep": case["prep"], "fixture": vinfo,
                            "http": meta, "response": bounded(msg) if msg is not None else None,
                            "client_error": err, "elapsed_s": dt, "proxy_seq": [a, b]})
            print(f"{case['id']}: {dt}s", file=sys.stderr, flush=True)
        set_variant("main")
        out = {"label": label, "server": init["result"].get("serverInfo"),
               "protocolVersion": init["result"].get("protocolVersion"),
               "ha_yaml11_runtime_view": await ha_yaml11_view(), "cases": results}
        (OUT / f"cases-{label}.json").write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=True) + "\n")
        print(json.dumps({"label": label, "cases": len(results)}))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "setup":
        asyncio.run(setup())
    elif cmd == "catalog":
        asyncio.run(catalog(sys.argv[2]))
    elif cmd == "cases":
        asyncio.run(cases(sys.argv[2]))
    elif cmd == "list":
        print(json.dumps([c["id"] for c in build_cases()], indent=0))
