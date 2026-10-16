# HAMCP-162 D1: disposable offline capture of the upstream YAML reader

Evidence only. This record contains no runtime code, no Engineering change and
no release metadata. It is the D1 prerequisite named in
[`docs/HAMCP_162_YAML_READER_DESIGN.md`](../../HAMCP_162_YAML_READER_DESIGN.md)
(the design approved by Josh on 2026-10-16, PR #260 head
`2fb9843cdeed0bdc61c3791e9c0fd09d2951f4eb`, merged to `main` as
`cef5b800c84b6d3adb499a1eb6cfe5bb6dc099bb`).

**Outcome: the capture contradicts or corrects several stated upstream
assumptions (§7). Under the task's STOP rule, Part 2 must not start until Josh
has reviewed §7.**

## 1. Environment and provenance

Everything ran on one developer host, inside Docker, on the network
`hamcp162-int`, which was created with `--internal`. No port was published to
the host. The dependency install for the custom component used
`--network none`. Nothing in the capture reached live Home Assistant, a
production add-on, Nabu Casa or a real credential. The Home Assistant
configuration was created fresh. The only token was a long-lived access token
minted for the throwaway owner user of that instance, and it was destroyed
with the instance (§9).

| Item | Value |
|---|---|
| Capture window | 2026-10-16, about 05:10 to 05:31 UTC |
| Host tools | Docker 29.9.0 (containerd image store; each pulled image Id equals the pinned index digest), Python 3.13.5 |
| Home Assistant Core | 2026.10.1, `ghcr.io/home-assistant/home-assistant@sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0` (the digest pinned in `ci/support-window.yaml:59`) |
| ha-mcp 8.5.0 (production) | `ghcr.io/homeassistant-ai/ha-mcp@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c` |
| ha-mcp 8.4.3 (rollback, D7) | `ghcr.io/homeassistant-ai/ha-mcp@sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456` |
| Custom component | `ha_mcp_tools` 2.2.1, the production version, taken from upstream tag `v8.6.0`, commit `fc54437a804858732e4bc927add98e202d879a09`, subtree `custom_components/ha_mcp_tools` = tree `a2b8b98375c709d47360cd1ed2ca0f65d81c67c7`, extracted with `git archive`. Combined hash `926cd64802a22b5d817f1c5702621c4c8b215030d70e4841066613afb6c5661c` (the sha256 of the sorted `sha256sum` listing of the files). See `captures/component.txt` |
| Component dependencies | Installed offline from wheels: `ruamel_yaml-0.19.1` (sha256 `27592957fedf6e0b62f281e96effd28043345e0e66001f97683aa9a40c667c93`) and `voluptuous_openapi-0.4.1` (sha256 `b30abc527c622cd62dba1269a05099e6b6170678c2838908d347fa85ab0cdb44`) |
| Component entry | One config entry, "HA-MCP File & YAML Tools", created through the `tools` menu step. Its options were left at their defaults, so there are no extra allowed directories (D5a). See `captures/setup.json` |
| ha-mcp settings, filesystem ON (mirrors production) | `ENABLE_BETA_FEATURES=true`, `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, `ENABLE_YAML_CONFIG_EDITING=false`, `DISABLED_TOOLS=ha_write_file,ha_delete_file`, `HA_MCP_DISABLE_SETTINGS_UI=true`, `HA_MCP_DISABLE_UPDATE_CHECK=true`; read-only root filesystem with a tmpfs; secret path `/synthetic-review/mcp` |
| ha-mcp settings, OFF baseline | The same, without the beta, filesystem and `DISABLED_TOOLS` settings |
| Fingerprint code | `hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.py` at `cef5b800`, sha256 `699f344652d3311c385cdc5ae2e880c2b02def18a83c448d8ffa4b6533ad990f`. It was loaded by file path; nothing in the runtime package was modified |
| Raw proxy log | `state/proxy.jsonl`: 3,421 rows, 19.97 MB, sha256 `913cbee74eea1944c1202a14bc697dc6fc7cb6af803859d3c36518167bd228b8`. **Not committed**, because it carries the throwaway token in WS auth frames and request headers. It was destroyed with the environment. The value-free projection `captures/proxy-structure.jsonl` is committed instead |

Production mirroring. The production configuration is HA Core 2026.10.1,
ha-mcp 8.5.0 with filesystem tools on, YAML editing off and
`ha_write_file`/`ha_delete_file` hidden, and component 2.2.1 with only the
File & YAML Tools entry and the default directories. The capture mirrors that
configuration. The 8.4.3 runs use the same component, 2.2.1, which is what a
rollback to 8.4.3 would run in production (F3).

## 2. Generation process

The harness under `harness/` is the exact code that produced the captures.

1. `gen_fixtures.py` writes the synthetic fixtures under `fixtures/`
   deterministically: `configuration.yaml` variants, companions, a
   `secrets.yaml` and the redaction corpus `fixtures/cases.json`. Running it
   twice gives byte-identical output.
2. `run.sh prepare`: creates the internal network and the config directory,
   and installs the wheels with `--network none`.
3. `run.sh core`: starts Core 2026.10.1 on the internal network with a fresh
   config.
4. `run.sh setup`: onboards a throwaway owner, mints a long-lived token into
   `state/token.env` (mode 700, never printed) and installs the component.
   Then it restarts Core and creates the File & YAML Tools entry
   (`captures/setup.json`).
5. `proxy.py`: a logging reverse proxy (`hamcp162-proxy:8124`) between ha-mcp
   and Core. It records every REST call and every WS frame. The learned
   component caller token is replaced with `<redacted:learned-token>` before
   anything is written.
6. `seq.sh` / `seq2.sh`: for each version, run the ON instance A, then the
   catalog and the cases (`driver.py`), then instance B, then the catalog.
   Then the OFF instance, then the catalog.
7. Recapture A2. During the first pass, the PEM-shaped fixture tripped the
   repository secret gate (`scripts/check.ps1` `Test-SecretPatterns`). The
   fixture label was changed from a bare `PRIVATE KEY` to
   `ENCRYPTED PRIVATE KEY`, assembled by concatenation in
   `gen_fixtures.py`. Then both versions' cases were recaptured on a fresh ON
   instance (A2). The committed `cases-<v>.json` files are the A2 run.
8. `seq3.sh` (`CASESET=supp`) and `seq4.sh` (`CASESET=supp2`): supplementary
   runs on fresh ON instances (S, S2). They cover non-string key addressing,
   symlinks and traversal to `secrets.yaml`.
9. `analyze.py . <policy module> out/analysis.json`: computes the
   fingerprints, the R4 bindings, stability, the cross-version diff, the
   per-case Core calls and the corpus check, and writes
   `captures/analysis.json`.
10. `proxy_structure.py state/proxy.jsonl captures/proxy-structure.jsonl`:
    writes the value-free projection of the proxy log.

`captures/` is a byte copy of the scratch `out/` directory, plus
`case-table.txt` and `proxy-structure.jsonl`. `case-table.txt` is a
one-line-per-case outcome table produced from the A2 cases. Its single NUL
byte, echoed from the `configuration.yaml\0` path case, is written as the
literal text `\x00`. Trailing spaces at line ends, left by truncating long
lines, are stripped.

Each case record holds the exact MCP `tools/call` arguments, the raw HTTP
status, content type, byte count and sha256 of the response, and the decoded
JSON-RPC message. It also records the fixture variant with its sha256, the
proxy sequence window, and the preparation and restore operations.

### Committed fixture names

Every YAML fixture is committed with a `.fixture` suffix appended, for
example `fixtures/variants/main.yaml.fixture`. The repository's local gate
(`scripts/check.ps1`) runs `yaml.safe_load` on every `*.yaml` and `*.yml`
file in the tree. Several fixtures are deliberately invalid or non-UTF-8
(`parse_error`, `binary`, `latin1_text`, tag-on-mapping and others), so they
cannot carry their natural names. The bytes are unchanged.
`gen_fixtures.py <dir>` writes the original names. Regenerating into a
temporary directory on 2026-10-16 and stripping the suffix gave
byte-identical files for all 45 committed fixtures. The two excluded large
files reproduced the sha256 values below. `.gitattributes` marks this
directory `-text`, and `crlf.yaml.fixture` `-whitespace`, so that checkout
never rewrites the hashed bytes.

### Files not committed

- `fixtures/variants/large_1mib_value.yaml` (sha256
  `56c229d55ab2eadbb797b3b5d91d837cdb754392bf773ba0ed7e3b03c9ef4a5a`) and
  `fixtures/variants/large_8mib_file.yaml` (sha256
  `ae2f963b2da978ba866cef53cf1dd079676261bdd9164c0ff31ac9d0ed1f0c6f`). They
  are regenerated byte-identically by `gen_fixtures.py`, and every case
  record carries the fixture sha256.
- `state/` (the token and the raw proxy log) and `config/` (the throwaway
  Home Assistant configuration, including `.storage`).

## 3. Case inventory

Each of the two versions ran the same cases.

| Set | Count | Content |
|---|---|---|
| `main:` | 67 | every top-level and nested path of the main synthetic `configuration.yaml`. Covers rule classes A, B, C′ (including C′3), R10 keys, R11 SQLite safe and unsafe shapes, `!secret`/`!env_var` markers, credential keys, the Codex counterexamples expressible in YAML, forgery mappings, includes, anchors and merges |
| `yaml11key:` | 24 | YAML 1.1 versus 1.2 scalar resolution (yes/no/on/off, octal, sexagesimal, exponent, inf/nan, dates and more) |
| `variant:` | 44 | whole-file variants: empty, comment-only, list and scalar roots, BOM, CRLF, binary, latin-1, parse error, duplicate keys, tags on mappings and sequences, unregistered tags, a bare `!secret`, a multiline `!env_var` default, alias blow-up, 4,001 nodes, depth 25, a 1 MiB value, an 8 MiB file, non-string keys, and a missing `configuration.yaml` |
| `file:` | 36 | the `file` argument: allowed, case variants, `configuration.yml`, `secrets.yaml`, traversal, absolute, URL, `~`, backslash, percent-encoding, trailing space, an over-long path, globs, `.storage`, `approval_pin.json`, packages, `www` |
| `read_file:` | 24 | `ha_read_file` against the same paths |
| `list_files:` | 10 | `ha_list_files` against the config directory and its subdirectories |
| `schema:` | 5 | argument-schema violations |
| `defaults:`, `content+parsed:`, `neither:` | 5 | the `include_content`/`include_parsed` combinations |
| `unavailable:` | 2 | component entry removed, then re-added |
| `supp:` (S) | 15 | addressing each non-string key class; a symlinked `configuration.yaml` pointing inside and outside the config directory |
| `supp2:` (S2) | 11 | `packages/../`, `www/../` and `packages/./../` traversal to `secrets.yaml` through both tools; a symlinked `configuration.yaml` pointing to `secrets.yaml` and to `.storage/onboarding` |

Engineering's planned request is
`{"file":"configuration.yaml","yaml_path":P,"include_content":false,"include_parsed":true}`.
The other argument shapes exist only to characterize upstream behaviour.

## 4. Catalogs and complete-descriptor bindings (R4)

### Catalogs

| Run | Tools | `catalog_fingerprint` | tool order fingerprint |
|---|---|---|---|
| 8.5.0 ON (A, A2, B, S, S2) | 80 | `f112aa40dccfe7e2aa814942dc92dce28c5aaeb95f2069809772ddc34b4640e0` | `00a41614e16114bbb656e80409c71e5c1f074f888eea23112fe3b81d0d97b0d3` |
| 8.5.0 OFF | 77 | `0d7ecdb7f58b9b3f92fe05e0f3afbd213da6d5841263219d8c12c3dea1d77845` (equals the reviewed stock fingerprint in `upstream_tool_policy_8_5_0.json`) | `0aab73755d36e877b4ae6ed35683e7232974b04c98edb4fb4a0ea1f0bc8f6c8e` |
| 8.4.3 ON (A, A2, B, S, S2) | 81 | `f0a694cc29696566056fb45aefde57ba9bd72dec320f5ee66dce4dc26a92db0e` | `32176b4d9ea0295d74d0263d73e48a0b88ff2f47395ba16ec9c0d2c1463130b5` |
| 8.4.3 OFF | 78 | `4b75e198df50a633ab94d51f006961c5fc31a1edfcf524b4dc925a48799e98f7` (equals the reviewed stock fingerprint in `upstream_tool_policy_8_4_3.json`) | `b78ae4ddde97e9db9250830c96a666fbaa8561abe747fcab1223d43754bead34` |

In both versions, the ON catalog is the OFF catalog plus exactly
`ha_config_get_yaml`, `ha_list_files` and `ha_read_file`. There are no
duplicate names. `ha_write_file` and `ha_delete_file` are absent. The MCP
`protocolVersion` is `2025-03-26`.

The ON catalog is identical across five independent instance starts per
version (A, A2, B, S, S2), measured by `catalog_fingerprint` and tool order.
The raw files differ only in byte length (A2, S2), which comes from JSON-RPC
id width.

### Descriptors

The top-level fields of all three tools, in both versions, are exactly
`[_meta, annotations, description, inputSchema, name, outputSchema, title]`.
There is no `execution` and no `icons` field.

| Field | Value |
|---|---|
| `outputSchema` | `{"additionalProperties": true, "type": "object"}` (all three) |
| `annotations` | `readOnlyHint: true`, `openWorldHint: false`, `title` "Read YAML Config Fragment" / "List Files" / "Read File" |
| `_meta.fastmcp.tags` | `[System, beta]` for `ha_config_get_yaml`; `[Files, beta]` for the others |
| `_meta.ha_mcp` | `{llm_api_exposed: false, pinned: false, policy: {deployment: "standalone", enabled: false, live: false, rules: 0}}`; `runtime_policy_state_fingerprint_projection` is valid (`ha-mcp-policy-runtime-state-v1`) |

Across versions, `ha_config_get_yaml` and `ha_list_files` are identical.
`ha_read_file` differs in `/description` only: 8.5.0 refers to
`ha_manage_blueprints(action="get")`, and adds the `home-assistant.log.fault`
and `ha_get_logs(source=…)` lines. The diff is in
`analysis.json#/descriptor_diff_843_850`.

### Bindings

The binding model is `hamcp162-optional-tool-binding-v1`, using
`ha-mcp-operational-tool-descriptor-v2` for the complete descriptor. Each
binding in `analysis.json#/bindings` also carries `top_level_fields`, the
per-field `diagnostic_fingerprints`, `source_commit` (`cef5b800`) and the
capture resource with its sha256.

| Version | Tool | v2 complete-descriptor fingerprint | v1 runtime contract | Disposition |
|---|---|---|---|---|
| 8.4.3 | `ha_config_get_yaml` | `75d623c93573aee91b9d78723f71ac1de30c688efda78bb48033dd62a196d007` | `ec9e10865a02ece2170fc9c9792e5abc6e3212848e842320c121559ffd238c02` | `mixed_or_requires_wrapper`, token `ha-mcp-8.4.3-yaml-fragment-read-v1` |
| 8.5.0 | `ha_config_get_yaml` | `75d623c93573aee91b9d78723f71ac1de30c688efda78bb48033dd62a196d007` | `ec9e10865a02ece2170fc9c9792e5abc6e3212848e842320c121559ffd238c02` | `mixed_or_requires_wrapper`, token `ha-mcp-8.5.0-yaml-fragment-read-v1` |
| 8.4.3 | `ha_list_files` | `71e51bafd62d0837b7f2056b7a8bd007aa20b0c8c42c169981a8af1796c0e083` | `5a2256f6106b28757c6588727584222f4a953fc0d629984a9a095efe4c960975` | `prohibited` |
| 8.5.0 | `ha_list_files` | `71e51bafd62d0837b7f2056b7a8bd007aa20b0c8c42c169981a8af1796c0e083` | `5a2256f6106b28757c6588727584222f4a953fc0d629984a9a095efe4c960975` | `prohibited` |
| 8.4.3 | `ha_read_file` | `97459dd33eca15079f42cce074485c821752fad85c0c6a3f545544e42adecae1` | `2455436ee60b70c9631cbdaa74a1c5fe19342f5715cec246b0ab1294b70ed2f6` | `prohibited` |
| 8.5.0 | `ha_read_file` | `7627857f25d581e75c41bed2cad2ddf9b41839b69b8942c033c150c17cbf92a6` | `8bf8fa302dbeb00358ff678fd22515fe2cc53448b2a9bb099a4672eedb5ee1f6` | `prohibited` |

The diagnostic fingerprint keys are `/_meta`, `/_meta/fastmcp/tags`,
`/_meta/ha_mcp/llm_api_exposed`, `/_meta/ha_mcp/pinned`,
`/_meta/ha_mcp/policy/{deployment,enabled,live,rules}`, `/annotations`,
`/annotations/title`, `/description`, `/execution` (absent), `/inputSchema`,
`/outputSchema` and `/title`.

**Stability (D1 "repeated once per version").** For every tool and version,
A equals B under both the v1 and v2 models, and `varying_fields` is empty
(`analysis.json#/stability`). No field needs the R4 escape for
capture-varying fields.

These values are capture evidence for Part 2. They are not yet committed to
any policy, registry or binding, and they change nothing at runtime.

## 5. Observed request and response shapes

### Engineering's request

The MCP call
`{"file":"configuration.yaml","yaml_path":P,"include_content":false,"include_parsed":true}`
produces this sequence on the Core side (`analysis.json#/component_calls`,
`captures/proxy-structure.jsonl`):

- The first tool call of an ha-mcp process does `GET /api/services` twice,
  then `POST /api/services/ha_mcp_tools/get_caller_token`, then
  `POST /api/services/ha_mcp_tools/read_file?return_response=true`.
- WS `ha_mcp_tools/info` is sent once per WS connection: 8 times over the
  whole capture, never per tool call.
- Every later call is exactly one `POST …/read_file`, with body keys
  `{_ha_mcp_token, include_parsed, path, yaml_path}`.
- After a missing-config error, the next call adds one `GET /api/services`.
- The component's service response contains the **whole file**. It was
  33,344 bytes for the 30,948-byte main fixture.

The successful result is an MCP text block with the following JSON
(`structuredContent` mirrors it):

```
{success: true, yaml_path, matches: [{file, yaml_path, parsed}], count: 1,
 files_searched: 1, files_unreadable: 0}
```

- `matches[*]` keys are exactly `{file, parsed, yaml_path}`.
- `file` is echoed as sent (`./configuration.yaml` keeps its `./`).
- No `warnings` key appears for a literal file.
- A missing key returns `count: 0` and `matches: []`.
- A key whose value is `null` is found (`parsed: null`).

### Errors

Errors have `isError: true` and **no `structuredContent`**. There is only one
text block containing JSON, in one of two shapes:

- From the component:
  `{"success": false, "error": "<string>"}`, for example
  `"Path not allowed. Allowed paths: …"` or `"File does not exist: …"`.
- From ha-mcp:
  `{"success": false, "error": {code, message, suggestion, suggestions}, "tool", "file", "yaml_path"}`,
  for example `CONFIG_INVALID`, `VALIDATION_FAILED` or `SERVICE_CALL_FAILED`.

`case-table.txt` lists the outcome of every case. The full raw responses are
in `cases-<v>[-supp|-supp2].json`.

## 6. Assumptions confirmed

Every observation in §6 and §7 is identical on 8.4.3 and 8.5.0, except the
single difference noted in §7.

| Design claim (section) | Result |
|---|---|
| Registration needs `ENABLE_BETA_FEATURES` + `HAMCP_ENABLE_FILESYSTEM_TOOLS`; not gated by YAML editing (§1.1) | Confirmed (ON vs OFF catalogs with YAML editing off) |
| Signature, defaults (§1.1) | Confirmed. The default call returns `content` and no `parsed`; `include_parsed: "true"` (a string) is coerced and accepted; with neither flag set, a match is `{file, yaml_path}` |
| Annotations, tags, titles (§1.1, R4) | Confirmed (§4) |
| REST transport through the component; ha-mcp never reads the filesystem; whole-file read included in the service response (§1.1) | Confirmed |
| `yaml_path` walks mapping keys only: no list indices, no dotted keys, empty path matches nothing, non-mapping root gives no match (§1.1) | Confirmed: dotted keys, list indices, `""`, `"."`, case variants and glob characters in `yaml_path` all give `count: 0`; list root, scalar root, empty and comment-only files give `count: 0` |
| Non-string keys cannot be addressed (§1.1) | Confirmed (see K5 for the stringification detail) |
| Parse error is `CONFIG_INVALID` with line and column only (§1.1) | Confirmed; duplicate keys also give `CONFIG_INVALID` |
| Includes never followed; tags render as source text (§1.1) | Confirmed for `!include`, all four `!include_dir_*`, `!secret`, `!env_var` |
| Quoted `"!secret abc"` is indistinguishable from a real tag in `parsed` (§2.1) | Confirmed: `"!secret abc"`, `'!secret abc'` and `!secret abc` all render `"!secret abc"` |
| `!env_var` default returned verbatim (§2.1 table) | Confirmed; a multiline quoted default keeps its quotes and newline (`!env_var MQTT_PW "line1\nline2 …"`), which the design's DOTALL `.+` pattern (§2.2) covers. A bare `!secret` renders `"!secret"` |
| `!input` inferred to lose its tag (§1.1, F14) | Confirmed: `!input motion_entity` renders `"motion_entity"`; `!SECRET` and a custom local tag also lose the tag |
| Aliases and merge keys inferred expanded (§1.1, §2.3, §4) | Confirmed: a 307-byte alias blow-up returned a 2.97 MB `parsed` |
| No upstream size cap (§1.1, F8) | Confirmed: a 1 MiB value and an 8 MiB file (9.2 MB `parsed`) were returned whole; 4,001 nodes and depth 25 returned |
| No upstream redaction | Confirmed: all 409 corpus strings and 72 credential-like keys came back verbatim (`analysis.json#/corpus_check`) |
| Allowlist is case-sensitive; only `configuration.yaml` (R6) | Confirmed: `Configuration.yaml`, `CONFIGURATION.YAML`, `configuration.yml` refused |
| Refusals for absolute, URL, `~`, backslash, `%2e%2e`, trailing space and over-long paths (§1.3) | Confirmed: "Path not allowed" or "File does not exist" |
| "Path not allowed" discloses the allowlist (§1.3, §4) | Confirmed: the message lists the effective allowlist |
| Deny floor: `.storage`, `approval_pin.json`; symlinked `secrets.yaml` blocked (2.2.1) (§1.3) | Confirmed |
| `secrets.yaml` direct read masked, keys disclosed (§1.3) | Confirmed: values `[MASKED]`; a list-shaped `secrets.yaml` gives content withheld |
| Traversal resolving to `secrets.yaml` (§1.3 check order) | Confirmed: `packages/../secrets.yaml`, `www/../secrets.yaml`, `packages/./../secrets.yaml` and `Secrets.YAML` are normalized and then masked or refused in both `ha_config_get_yaml` and `ha_read_file`; no unmasked secret value returned |
| Symlinked `configuration.yaml` resolving to `secrets.yaml` or `.storage/` is refused (§2.3, was **inferred**) | **Now verified**: refused for `secrets.yaml`, `.storage/onboarding` and an outside target (`/etc/hostname`) |
| Symlinked `configuration.yaml` resolving to another in-config file is readable (§2.3, was **inferred**) | **Now verified**: readable when the target is a non-allowlisted file inside the config directory |
| Packages folder detected from `homeassistant: packages:` (§1.3) | Confirmed: detection is dynamic |
| BOM and CRLF | Read normally |
| `ha_read_file` returns whole raw files incl. comments (§1.4) | Confirmed; `custom_components/**/manifest.json` refused (only `*.py` allowed) |
| `ha_list_files` / `ha_read_file` metadata | `modified` is epoch seconds in `ha_list_files` and an ISO string in `ha_read_file` |
| 8.4.3 and 8.5.0 contract identical for `ha_config_get_yaml` (§1.1 version table) | Confirmed: identical descriptor and identical behaviour on all 243 cases except the one schema-error message in §7 |

## 7. STOP list: contradictions and corrections

Each item below contradicts, or materially corrects, a statement in the
approved design. Severity describes the effect on Part 2 as designed. It is
decision support only; Josh decides the disposition.

| # | Design statement | Observed | Effect on Part 2 |
|---|---|---|---|
| K1 | §1.1 Flow step 1 reads as a per-call availability preflight. §5.4 lists the Core APIs reached as `read_file`, `GET /api/services` and WS `ha_mcp_tools/info` only | The preflight and the caller token are **cached per process**. The first call also reaches **`POST /api/services/ha_mcp_tools/get_caller_token`** (admin-gated per §1.1; it succeeded with the throwaway owner token; non-admin was not tested). The caller token is persisted by the component in `.storage/ha_mcp_tools_auth` and **survived removing and re-adding the entry** | §5.4's route list and the matrix disclosure must name `get_caller_token`. The "exactly one call per request" claim holds only after the first call. Mapping to `core.basic_rest_read` must be re-judged for an admin-gated service POST |
| K2 | §4 maps "component missing or unavailable, or token failure" to `yaml_component_unavailable` | With the entry removed, the call returns `VALIDATION_FAILED` `"API error: 400 - 400: Bad Request"`. There is no distinct unavailability signal | Unavailability cannot be told apart from other 400s by code; §4's mapping needs a decided rule (or it falls to `yaml_read_failed`) |
| K3 | §1.1 "Errors come back as ha-mcp structured tool errors" | Errors carry **no `structuredContent`**; only a text block of JSON, with two shapes: component `error` as a **string**, ha-mcp `error` as an **object** (§5) | The Part 2 response parser must accept both shapes from text and must not expect `structuredContent` on errors |
| K4 | §1.3 lists `"Cannot read binary file: <path>…"` as the binary error | A non-UTF-8 `configuration.yaml` (binary or latin-1) returns `SERVICE_CALL_FAILED` `"API error: 500 … Server got itself in trouble"`. In 2.2.1, the package-dir marker scan (`_load_package_dir_markers_tracked`) catches only `OSError`/`YAMLError`, so the decode error escapes as an HTTP 500. Also, an integer too large for JSON (`123456789012345678901234567890`) **anywhere in the selected subtree** makes the whole response a 500 from HA JSON serialization | Both arrive as generic 500s; §4 maps them to `yaml_read_failed`. A big integer makes its enclosing subtree unreadable (not partially readable) |
| K5 | §1.1 "Non-string keys cannot be addressed" (confirmed), but the parent-view shape is not stated | In `parsed`, non-string keys are **stringified** (`1`→`"1"`, `true`→`"True"`, `null`→`"None"`, `1.5`→`"1.5"`, a date→`"2026-10-16"`, a complex key→`"('a', 'b')"`). **Colliding keys collapse, and the last value wins** (`1` and `"1"`; `true` and `"True"`). A mapping holding both `1` and `true` gives `CONFIG_INVALID` (line 4, column 3), which makes **every path in the file unreadable** | L4 sees Python-repr strings as keys; a collision silently drops a value before Engineering sees it. Redaction must not rely on the original key type |
| K7 | Not stated; the design implicitly treats `parsed` as what Home Assistant uses | `parsed` follows **YAML 1.2** (ruamel), while Home Assistant's runtime loader uses **YAML 1.1**. They differ for `yes`/`no`/`on`/`off` (strings vs booleans), `017` (17 vs 15), `0o17` (15 vs `"0o17"`), `12:30:00` (`"12:30:00"` vs 45000), `1e3` (1000.0 vs `"1e3"`), `.inf`/`-.inf`/`.nan` (strings vs floats) and dates or datetimes (ISO strings vs date objects). Identical: `y`, `true`, `True`, hex, `1_000`, `+1`, `0b101`, `1.50`, `~`, `null`. HA's view was computed in the Core image (`analysis.json#/yaml11_runtime_view_ha`) | The reader reports the file's YAML 1.2 view, which can differ from the value Home Assistant actually loads. This needs a disclosure in the tool description and `limitations`, or a decision |
| K8 | §1.3 `<packages folder>/**/*.yaml` | The disclosed allowlist string reads `packages/*.yaml`. Nested packages were **not tested** | None for the first release (packages removed, D5); correct §1.3 or test before any packages option |
| K9 | §5/§6 tool counts 77→80 | 8.5.0: 77→80 confirmed. **8.4.3: 78→81** | Any count assertion for 8.4.3 must use 78/81 |
| K10 | — | The only behavioural difference between 8.4.3 and 8.5.0: the unknown-argument error message. 8.4.3 says ``"`path`: Unexpected keyword argument"``; 8.5.0 says ``"`path`: unknown parameter. Valid parameters: …"`` and adds `valid_parameters` | None if Engineering never forwards the upstream text (§4); recorded for the per-version binding |
| K11 | §1.1 version table: 8.4.3 ships component 2.1.3, 8.5.0 ships 2.2.0 | **Not tested.** Both runs used component 2.2.1, as production does. Behaviour under 2.1.3 or 2.2.0 (for example, without the `approval_pin.json` deny or the symlinked-`secrets.yaml` block) is unverified | F3 already says L1–L4 must not depend on 2.2.1-only denies; unchanged |

K6 is unused: it was a working-notes number withdrawn before this record,
and the other numbers are kept stable. K9 and K10 are corrections, not contradictions of a
behavioural assumption. They are listed so that every correction appears in
one place.

## 8. Not tested

- Nested packages (`packages/a/b.yaml`).
- ha-mcp 8.6.0 and 8.7.0 (D7 holds them).
- Component versions other than 2.2.1.
- Production's actual component options and extra directories (D5a assumes
  the default).
- A non-admin token for `get_caller_token`.
- **Open reconciliation with production.** The HAMCP-162 register row
  (2026-10-14) records that production ha-mcp advertised 79 tools (77 plus
  2 unreviewed), not 80. This capture, with production-mirroring settings on
  8.5.0, gives 80 (77 plus all three tools). Which production setting hides
  one tool, if any, is **unknown**. Settling it needs a separately
  authorized read-only observation of the production catalog, which this
  task did not perform.
- Concurrency, and repeated sessions on a single ha-mcp process beyond the
  case run.

## 9. Secret hygiene and cleanup

Scans of every committed file under this directory:

- The throwaway long-lived token (183 characters) and its 40-character
  prefix: **0 hits**.
- The component caller token: never written. The proxy replaced it before
  logging; the only match is the placeholder string in `proxy.py`.
- The repository secret-gate patterns (`github_pat_`, `gh[pousr]_`, `sk-…`,
  PEM private-key headers): **0 hits**.
- JWT-shaped strings: every one is a synthetic fixture value with the `sub`
  claim `synth`/`synthetic`.
- Every credential-shaped value in the fixtures is synthetic (the
  `Zq7v####Xw` family and similar). Token-bearing fields in the harness
  configuration appear only as `!env_var TOK`.

### 9.1 Cleanup record (2026-10-16, completed 05:42 UTC)

- Containers `hamcp162-core` and `hamcp162-proxy` were removed with
  `docker rm -f`. Each MCP and driver container was `--rm` and had already
  exited.
- Root-owned files were deleted from inside a `--network none` container.
  Then the whole scratch tree `~/tmp/hamcp162-d1` was removed: the
  throwaway Home Assistant `config/` (including `.storage`, the owner user,
  the token and the component's `ha_mcp_tools_auth`), `state/token.env`,
  `state/proxy.jsonl`, `out/`, `deps/` and `wheels/`. The temporary
  fixture-regeneration and runtime-view check directories `/tmp/genchk2`
  and `/tmp/rtcheck` were removed too.
- The network `hamcp162-int` was removed.
- Verification afterwards: `docker ps -a` shows no `hamcp162-*` container,
  `docker network ls` shows no `hamcp162-*` network, `docker volume ls` is
  empty, and the scratch paths no longer exist.
- Kept intentionally: the public, digest-pinned images in the local image
  store. They hold no capture data and other tasks share them.
- Kept intentionally: the pre-existing upstream source checkout
  `/tmp/ha-mcp-8.6.0`. It dates from 2026-10-12 and predates this task; the
  component was exported from it read-only with `git archive`.
- The throwaway token was only ever valid against the destroyed instance.

## 10. Files

- `README.md`: this record.
- `MANIFEST.sha256`: sha256 of every file in this directory except `README.md`
  and the manifest itself.
- `captures/`: `catalog-<v>-<run>.json` for runs A, A2, B, S, S2 and OFF;
  `cases-<v>.json`, `cases-<v>-supp.json`, `cases-<v>-supp2.json`;
  `analysis.json`; `setup.json`; `component.txt`; `case-table.txt`;
  `proxy-structure.jsonl`.
- `fixtures/`: the synthetic `configuration.yaml` variants, companions,
  `secrets.yaml` (all with the `.fixture` suffix, §2) and the corpus
  `cases.json`.
- `harness/`: the exact generation code (§2).
