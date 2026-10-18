#!/usr/bin/env python3
"""HAMCP-162 D1: deterministic synthetic fixture generator.

Writes the configuration.yaml variants, the companion files and
``cases.json`` (the expected parsed value of each corpus entry) into an
output directory. Every credential-like value is synthetic and unique per
entry. Running it twice gives byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
_counter = 0


def syn() -> str:
    """A unique synthetic value with no credential word in it."""
    global _counter
    _counter += 1
    return f"Zq7v{_counter:04d}Xw"


def q(text: str) -> str:
    """YAML double-quoted scalar that round-trips the exact Python string."""
    out = ['"']
    for ch in text:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= o <= 0x7E:
            out.append(ch)
        elif o <= 0xFFFF:
            out.append(f"\\u{o:04X}")
        else:
            out.append(f"\\U{o:08X}")
    out.append('"')
    return "".join(out)


def enc(value: str, depth: int) -> str:
    for _ in range(depth):
        value = quote(value, safe="")
    return value


def depths(base: str, rng=range(7)) -> list[str]:
    return [enc(base, d) for d in rng]


# --------------------------------------------------------------------------
# String corpus (R2 / C' / C'3 / R9 / R11 / useful / over-redaction / D12)
# --------------------------------------------------------------------------
corpus: list[tuple[str, str]] = []  # (group, value)


def add(group: str, values):
    for v in values:
        corpus.append((group, v))


S = syn
add("r2_boundary_dsn", depths(f"Server=db;Uid=u;Pw⟦split⟧d={S()}"))
add("r2_boundary_uri", depths(f"my⟦split⟧sq⟦split⟧l:⟦split⟧//u:{S()}@db/ha"))
add("r2_c2_counterexample", depths(f"Server=db;Uid=u;Pw⟦split⟧d=={S()}", range(7)))
add("r2_c2_uri", [f"mysql://db/ha?pw⟦split⟧d=={S()}"])
for tmpl in ["Pw⟦split⟧d=={s}", "PWD :={s}", ";pwd;{s}", "P%57D={s}", "ｐｗｄ={s}",
             "pw⟦split⟧d={s}", "?x=PWD%3D{s}%3Bz", "?%2550WD={s}", '{{"pwd": "{s}"}}']:
    add("c2_pwd_variants", depths(tmpl.format(s=S())))
for tmpl in ["pass={s}", "PASS =={s}", "key: {s}", ";key={s}", "key=={s}", "pass:={s}",
             "key :={s}", "Key={s}", "x=1;pass={s}", "a&key={s}", "key\t={s}",
             "key ={s}", "ｋｅｙ＝{s}", "ｐａｓｓ：{s}",
             "pass%3D{s}", "%6Bey={s}", "k%45Y: {s}"]:
    add("c3_delimited", depths(tmpl.format(s=S())))
for tmpl in ["https://api.example.com/v1?key={s}", "mysql://db/ha?pass={s}",
             "https://api.example.com/v1?a=1&KEY={s}"]:
    add("c3_query", depths(tmpl.format(s=S()), range(5)))
add("c3_noncatch", ["keypad", "monkey", "bypass", "compass", "key fob", "monkey=1",
                    "keypad: 4", "bypass=on", "compass: north", "hotkey=F5", "passkey=1",
                    "turnkey: yes", "Kitchen keypad", "Hall pass", "pass the salt",
                    "key lime pie", "keys=3", "passes: 2"])
for tmpl in ["po⟦split⟧st⟦split⟧gr⟦split⟧es⟦split⟧ql⟦split⟧+ps⟦split⟧yc⟦split⟧op⟦split⟧g2⟦split⟧://u:{s}@db/ha", "ms⟦split⟧sq⟦split⟧l+⟦split⟧py⟦split⟧od⟦split⟧bc⟦split⟧://u:{s}@db/ha",
             "redis://:{s}@cache:6379/0", "ht⟦split⟧tp⟦split⟧s:⟦split⟧//u:{s}@example.com/x", "mysql://u@db/ha",
             "mysql://u:{s}＠db/ha", "mysql://db/ha?pa⟦split⟧ssword={s}"]:
    add("uri_userinfo", depths(tmpl.format(s=S())))
for tmpl in ["DRIVER={{X}};SERVER=db;UID=u;PW⟦split⟧D={s}", "Data Source=db;User ID=u;Pa⟦split⟧ssword={s}",
             "host=db dbname=ha user=u pa⟦split⟧ssword={s}",
             "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC%7D%3BSERVER%3Ddb%3BUID%3Du%3BPWD%3D{s}"]:
    add("standalone_cs", depths(tmpl.format(s=S())))
add("a_nonconverging", [enc("Living Room", 5), enc("Living Room", 6), "%41%", "%G1%41",
                        "%41 50% off", "%FF", "%C0%AE", "a%00b", "％７０wd=" + S()])
add("r9_bounds", [enc("Living Room", 4), enc("Living Room", 5), enc(f"Pw⟦split⟧d={S()}", 4),
                  "50% off", "100%", "%G1", "{{ states('sensor.x') }}"])
add("r11_sqlite_visible", ["sqlite:////config/home-assistant_v2.db",
                           "sqlite:///home-assistant_v2.db", "sqlite:///a..b.db"])
add("r11_sqlite_variants", [
    "sqlite:////config/home-assistant_v2.db\n", "sqlite:////config/home\n-assistant_v2.db",
    "sqlite:////config/x\r.db", "sqlite:////config/x\t.db", "sqlite:////config/x\x00.db",
    "sqlite:////config/x\x7f.db", "sqlite:////config/x .db", "sqlite:////config/x .db",
    "sqlite:///../x.db", "sqlite:////config/../x.db", "sqlite:////config/x.db/..",
    "sqlite:////config/x%2D.db", "sqlite:////config/x%.db", "SQLITE:///x.db", "sqlite:///X.db",
    "sqlite:////config/ｘ.db", "sqlite:////config/x.db?mode=ro", "sqlite:////config/u@x.db",
    "sqlite:////config/x;y.db", "sqlite:////config/x=y.db", "sqlite:////config/x y.db",
    "sqlite:///:memory:", "sqlite:////config/session.db"])
add("useful_visible", ["mysql://db.local:3306/ha?charset=utf8mb4",
                       "postgresql://db.local/ha?sslmode=require", "mysql://[fd00::5]:3306/ha",
                       "unknown://host/path", "https://api.example.com/v1?entity=sensor.x",
                       "http://api.local/{{ states('input_text.x') }}/v1",
                       "Server=db;Database=ha;Trusted_Connection=yes", "Living Room", "50% off"])
add("over_redaction", ["Refresh the access token daily", "Author: Synthetic", "Session started",
                       "private room", "cookie jar", "secretary",
                       "https://example.com/oauth/authorize", "{% if token == 1 %}",
                       "token://host/x", "mailto://someone@example.com", "Hall pass: yes",
                       "Press key: Enter", "{% set key = 1 %}", "Spare key=garage",
                       "key://host/x", "http://key:8080/x"])
for tmpl in ["login={s}", "pw={s}", "psk={s}", "p_w_d={s}", "Pсd={s}", "kеy={s}",
             "my_key={s}", "app_key={s}", "passkey={s}", "pass_word={s}", '"key": "{s}"',
             "{{'pass': '{s}'}}", "key→{s}"]:
    add("d12_residual", [tmpl.format(s=S())])
add("d12_residual", ["QUJDREVGR0g="])
add("other_secrets", [
    "Bearer " + "ey⟦split⟧JhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ." + S(),
    "ey⟦split⟧JhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMtMiJ9." + S(),
    # PKCS#8 encrypted label; the marker is assembled so no committed file carries the
    # repository credential-gate pattern (scripts/check.ps1 Test-SecretPatterns).
    "-----BEGIN ENCRYPTED " + "PRIVATE KEY-----\nU1lOVEhFVElDLU5PVC1BLUtFWS0" + S()
    + "\n-----END ENCRYPTED " + "PRIVATE KEY-----\n",
    "--⟦split⟧---BEGIN CERTIFICATE-----\nU1lOVEhFVElDLUNFUlQ" + S() + "\n--⟦split⟧---END CERTIFICATE-----\n",
    "U1lOVEhFVElD" * 12 + S(),
])
add("unicode_controls", ["a b", "﻿bom", "​zw", "line1\nline2", "tab\there",
                         "\U0001F600 emoji", "\u0085nel", "trailing   ", "  leading"])

CORPUS_GROUPS = sorted({g for g, _ in corpus})

# --------------------------------------------------------------------------
# R10 keys (canonical/encoded/fullwidth/non-identifier) and K1 keys
# --------------------------------------------------------------------------
key_corpus: list[tuple[str, str]] = []
for base in ["p%61ssword", "%70assword", "PASSWORD", "ｐａｓｓｗｏｒｄ",
             "MQTT-PassWord", "Pass Word", "db%5Furl", "db_urℓ", "pwd=" + S(),
             "mysql://u:" + S() + "@db", "token: " + S(), "key=" + S()]:
    for d in range(7):
        k = enc(base, d)
        if all(k != existing for existing, _ in key_corpus):
            key_corpus.append((k, S()))

# --------------------------------------------------------------------------
# Main configuration.yaml
# --------------------------------------------------------------------------
V = {}  # named synthetic values recorded in cases.json
for name in ["mqtt_password", "cred_value", "pin_numeric_note", "auth_password", "api_key",
             "influx_token", "comment_secret", "env_default", "env_default_multi",
             "rest_password", "header_bearer", "list_password", "nested_api_key",
             "recorder_url_pw"]:
    V[name] = S()

lines: list[str] = []
w = lines.append
w("# HAMCP-162 D1 synthetic configuration. Every credential below is synthetic.")
w("homeassistant:")
w("  name: Synthetic D1")
w("  unit_system: metric")
w("  packages: !include_dir_named packages")
w("  customize:")
w("    sensor.synthetic_temperature:")
w("      friendly_name: Synthetic temperature")
w("      icon: mdi:thermometer")
w("  auth_providers:")
w("    - type: homeassistant")
w("recorder:")
w(f"  # commented-out credential: password: {V['comment_secret']}")
w(f"  db_url: {q('mysql://recorder:' + V['recorder_url_pw'] + '@db.local:3306/ha?charset=utf8mb4')}")
w("  purge_keep_days: 10")
w("  commit_interval: 5")
w("  exclude:")
w("    domains:")
w("      - automation")
w("      - updater")
w("recorder_sqlite:")
w("  db_url: sqlite:////config/home-assistant_v2.db")
w("template:")
w("  - sensor:")
w("      - name: Synthetic template")
w("        state: \"{{ states('sensor.synthetic_temperature') | float(0) + 1 }}\"")
w("mqtt:")
w("  broker: mqtt.local")
w("  port: 1883")
w("  username: synthetic_user")
w(f"  password: {q(V['mqtt_password'])}")
w("  credentials:")
w(f"    value: {q(V['cred_value'])}")
w("    id: 7")
w("  certificate: auto")
w("x:")
w("  pin: 1234")
w("  code: 987654")
w("  pin_float: 12.5")
w("  token_bool: true")
w("  password_null:")
w("  auth:")
w(f"    password: {q(V['auth_password'])}")
w("http:")
w(f"  api_key: {q(V['api_key'])}")
w("  server_port: 8123")
w("influxdb:")
w(f"  token: {q(V['influx_token'])}")
w("  database:")
w("    name: ha")
w("rest:")
w("  - resource: https://api.example.com/v1/status")
w("    authentication: basic")
w("    username: synthetic_user")
w(f"    password: {q(V['rest_password'])}")
w("    headers:")
w(f"      Authorization: {q('Bearer ' + V['header_bearer'])}")
w("lists:")
w("  harmless:")
w("    - name: a")
w(f"      password: {q(V['list_password'])}")
w("    - name: b")
w(f"      nested: {{api_key: {q(V['nested_api_key'])}, port: 1}}")
w("  credentials:")
w("    - id: 1")
w("      value: " + q(S()))
w("refs:")
w("  password: !secret mqtt_password")
w("  api_key: !secret k")
w("  broker: !secret mqtt_host")
w("  token: !env_var TOK")
w(f"  pw_default: !env_var MQTT_PW {V['env_default']}")
w(f"  pw_multi: !env_var MQTT_PW multi word default {V['env_default_multi']}")
w("  quoted: \"!secret abc\"")
w("  single_quoted: '!secret abc'")
w("  db_url: !secret recorder_db_url")
w("  container:")
w("    password: !secret x")
w("  two_words: !secret two words")
w("  long_name: !secret " + "a" * 129)
w("  bad_env: !env_var 1BAD d")
w("  jwt_name: !secret ey⟦split⟧JhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aCJ9.c2ln")
w("  leading_space_quoted: \" !secret x\"")
w("forgery:")
w(f"  a: {{env_var_ref: {q(S())}}}")
w(f"  b: {{secret_ref: {q(S())}}}")
w("  c: {env_var_ref: x, default_redacted: true}")
w("includes:")
w("  sensor: !include sensors.yaml")
w("  automation: !include automations.yaml")
w("  dir_list: !include_dir_list lists_dir")
w("  dir_named: !include_dir_named named_dir")
w("  dir_merge_list: !include_dir_merge_list merge_list_dir")
w("  dir_merge_named: !include_dir_merge_named merge_named_dir")
w("sensor: !include sensors.yaml")
w("automation: !include automations.yaml")
w("script: !include scripts.yaml")
w("anchors:")
w("  base: &base {a: 1, b: two}")
w("  merged:")
w("    <<: *base")
w("    c: 3")
w("  alias: *base")
w("  list_alias: &l [1, 2]")
w("  list_ref: *l")
w("yaml11:")
for k, raw in [("yes_", "yes"), ("no_", "no"), ("on_", "on"), ("off_", "off"), ("y_", "y"),
               ("true_", "true"), ("True_", "True"), ("octal_0o", "0o17"), ("octal_0", "017"),
               ("hex", "0x1F"), ("underscore", "1_000"), ("sexagesimal", "12:30:00"),
               ("date", "2026-10-16"), ("datetime", "2026-10-16T10:00:00Z"),
               ("inf", ".inf"), ("ninf", "-.inf"), ("nan", ".nan"), ("tilde", "~"),
               ("null_", "null"), ("exp", "1e3"), ("plus", "+1"), ("bin", "0b101"),
               ("float_", "1.50"), ("bigint", "123456789012345678901234567890")]:
    w(f"  {k}: {raw}")
w("dotted:")
w("  \"a.b\": dotted_key_value")
w("  plain: plain_value")
w("empty_values:")
w("  null_value:")
w("  empty_map: {}")
w("  empty_list: []")
w("  empty_string: \"\"")
w("top_null:")
w("top_scalar: just a string")
w("top_list:")
w("  - one")
w("  - two")
w("corpus:")
corpus_expected = {}
for i, (group, value) in enumerate(corpus):
    key = f"c{i:04d}"
    corpus_expected[key] = {"group": group, "value": value}
    w(f"  {key}: {q(value)}")
w("keys:")
keys_expected = {}
for k, v in key_corpus:
    keys_expected[k] = v
    w(f"  {q(k)}: {q(v)}")
w("mqtt_keys:")
w("  broker: mqtt.local")
w(f"  \"MQTT-PassWord\": {q(S())}")
main_text = "\n".join(lines) + "\n"

# --------------------------------------------------------------------------
# Variants (each replaces configuration.yaml for its cases)
# --------------------------------------------------------------------------
variants: dict[str, bytes] = {"main": main_text.encode()}
bad_secret = S()
variants["parse_error"] = (
    "recorder:\n  purge_keep_days: 3\nmqtt:\n"
    f"  password: {bad_secret}: [unclosed\n  broker: x\n").encode()
variants["list_root"] = b"- one\n- two\n"
variants["scalar_root"] = b"just a scalar\n"
variants["empty"] = b""
variants["comment_only"] = b"# nothing here\n"
variants["duplicate_keys"] = b"recorder:\n  purge_keep_days: 1\nrecorder:\n  purge_keep_days: 2\n"
variants["dup_child_keys"] = b"recorder:\n  purge_keep_days: 1\n  purge_keep_days: 2\n"
for _nk, _line in [("int_str", b'  1: int_one\n  "1": str_one\n'), ("int", b"  1: int_one\n"),
                   ("bool", b"  true: bool_true\n"), ("bool_str", b'  true: bool_true\n  "True": str_true\n'),
                   ("null", b"  null: null_key\n"), ("float", b"  1.5: float_key\n"),
                   ("complex", b"  ? [a, b]\n  : complex_key\n"), ("date", b"  2026-10-16: date_key\n")]:
    variants["nk_" + _nk] = b"nk:\n" + _line + b"  plain: plain_value\n"
variants["tag_SECRET"] = b"refs:\n  upper: !SECRET x\n"
variants["tag_input"] = b"refs:\n  bp: !input motion_entity\n"
variants["tag_secret_bare"] = b"refs:\n  bare: !secret\n"
variants["tag_on_mapping"] = b"refs:\n  m: !secret {a: 1}\n"
variants["tag_on_sequence"] = b"refs:\n  s: !include [a, b]\n"
variants["tag_custom_local"] = b"refs:\n  c: !custom_tag value\n"
variants["tag_env_default_multiline"] = (
    "refs:\n  pw: !env_var MQTT_PW \"line1\\nline2 " + S() + "\"\n").encode()
variants["nonstring_keys"] = (
    b"nk:\n  1: int_one\n  \"1\": str_one\n  true: bool_true\n  \"True\": str_true\n"
    b"  null: null_key\n  1.5: float_key\n  ? [a, b]\n  : complex_key\n  2026-10-16: date_key\n")
variants["binary"] = b"recorder:\n  x: \xff\xfe\xfa\n"
variants["bom"] = b"\xef\xbb\xbfrecorder:\n  purge_keep_days: 4\n"
variants["crlf"] = b"recorder:\r\n  purge_keep_days: 5\r\n"
variants["latin1_text"] = "recorder:\n  name: café\n".encode("latin-1")
nodes = ["big:"] + [f"  k{i:04d}: {i}" for i in range(4001)]
variants["nodes_4001"] = ("\n".join(nodes) + "\n").encode()
deep = "deep:\n"
for i in range(25):
    deep += "  " * (i + 1) + f"l{i}:\n"
deep += "  " * 26 + "leaf: bottom\n"
variants["depth_25"] = deep.encode()
blow = ["bomb:", "  l0: &l0 [x, x, x, x, x, x, x, x]"]
for i in range(1, 6):
    refs = ", ".join([f"*l{i - 1}"] * 8)
    blow.append(f"  l{i}: &l{i} [{refs}]")
blow.append("  top: *l5")
variants["alias_blowup"] = ("\n".join(blow) + "\n").encode()
variants["large_1mib_value"] = ("big:\n  value: \"" + ("A" * (1024 * 1024)) + "\"\n").encode()
big8 = ["big:"] + [f"  k{i:06d}: \"{'B' * 100}\"" for i in range(80000)]
big8.append("small:\n  ok: 1")
variants["large_8mib_file"] = ("\n".join(big8) + "\n").encode()

# --------------------------------------------------------------------------
# Companion files
# --------------------------------------------------------------------------
SECRETS = {"mqtt_password": S(), "k": S(), "mqtt_host": "mqtt.local", "recorder_db_url":
           "mysql://u:" + S() + "@db/ha", "abc": S(), "x": S()}
companions: dict[str, bytes] = {
    "secrets.yaml": ("# synthetic secrets only\n" + "".join(
        f"{k}: {q(v)}\n" for k, v in SECRETS.items())).encode(),
    "secrets_list_variant.yaml": b"- a\n- b\n",
    "sensors.yaml": b"- platform: template\n  sensors: {}\n",
    "automations.yaml": b"- id: '1'\n  alias: Synthetic\n  triggers: []\n  actions: []\n",
    "scripts.yaml": b"synthetic_script:\n  sequence: []\n",
    "packages/x.yaml": ("x_pkg:\n  value: 1\n  password: " + q(S()) + "\n").encode(),
    "packages/y.yaml": b"y_pkg:\n  value: 2\n",
    "packages/[b].yaml": b"bracket_pkg:\n  value: 3\n",
    "approval_pin.json": ("{\"pin\": \"" + S() + "\"}\n").encode(),
    "www/readme.txt": b"synthetic www file\n",
    "www/data.yaml": b"www_yaml:\n  value: 1\n",
    "configuration.yml": b"yml_variant:\n  value: 1\n",
    "Configuration.yaml": b"case_variant:\n  value: 1\n",
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "variants").mkdir(exist_ok=True)
(OUT / "companions").mkdir(exist_ok=True)
manifest = {"variants": {}, "companions": {}}
for name, data in variants.items():
    (OUT / "variants" / f"{name}.yaml").write_bytes(data)
    manifest["variants"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
for name, data in companions.items():
    p = OUT / "companions" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    manifest["companions"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

cases = {
    "generator": "gen_fixtures.py",
    "synthetic_values": V,
    "synthetic_secrets": SECRETS,
    "parse_error_secret": bad_secret,
    "corpus_groups": CORPUS_GROUPS,
    "corpus": corpus_expected,
    "keys": keys_expected,
    "manifest": manifest,
}
(OUT / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=True, sort_keys=True) + "\n")
print(json.dumps({"corpus": len(corpus), "keys": len(key_corpus), "variants": len(variants),
                  "synthetic_count": _counter}))
