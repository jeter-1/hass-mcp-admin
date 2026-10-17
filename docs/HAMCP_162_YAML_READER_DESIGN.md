# HAMCP-162 delegated read-only YAML configuration reader — design

Status: **Part 1 design report, revision 5.** Revision 2 was the
correction batch after the independent Codex design review (R1–R7).
Revision 3 corrected the delta review's R2 residual and R8 (§13).
Revision 4 replaced the R2 recognizer with decode-then-judge rules and
aligned R9 (§14). Revision 5 replaces revision 4's assignment parsing with
a substring deny on canonical text, canonicalizes keys (R10) and tightens
the SQLite exception (R11) (§15). Josh's decisions on revision 5 add the
delimited-token rule C′3 (§15.1). Owner decisions are recorded in §10;
final owner approval follows the focused delta review of revision 5. No production code,
policy, registry, schema, version or workflow change is part of this
document. Nothing here authorizes a capture, deployment, live Home
Assistant or Engineering access, registry signing, a HOLD or support-window
change, or a release.

- Register: HAMCP-162.
- Base: `main` at `38ce701` (Engineering `2.4.1-beta.10`, after HAMCP-187
  #259).
- Upstream source was reviewed at exact commits. Each tree is the checked-out
  tag:
  - ha-mcp 8.4.3 `eac7a3aa7063432e9af17e7d7726040e909c7b8f`;
  - ha-mcp 8.5.0 `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`;
  - ha-mcp 8.6.0 `fc54437a804858732e4bc927add98e202d879a09`, which ships
    custom component **2.2.1**, the production component;
  - ha-mcp 8.7.0 (parked; future reference only).
- References use these abbreviations:
  - `YR` = `src/ha_mcp/tools/tools_yaml_read.py`;
  - `FS` = `src/ha_mcp/tools/tools_filesystem.py`;
  - `CC` = `custom_components/ha_mcp_tools/__init__.py`, from the 8.6.0
    tree (component 2.2.1);
  - `CONST` = that component's `const.py`;
  - `E/` = `hass_mcp_engineering_beta/ha_mcp_engineering/`.
- Home Assistant Core source for the root configuration file name: tag
  `2026.10.1`, commit `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`
  (`homeassistant/config.py`, abbreviated `HAC`).
- Production: ha-mcp 8.5.0 with the HA-MCP Custom Component 2.2.1 (File & YAML
  Tools entry only). Filesystem tools ON; YAML editing OFF; `ha_write_file`
  and `ha_delete_file` hidden.
- **Verified** means read in the pinned source or in current Engineering
  source. **Inferred** means reasoned from source but not executed. No upstream
  code was executed for this report.
- HAMCP-184 (beta.11, unmerged; commits `7d3c56b`, `a43dc5c`) is assumed. It
  makes absent or unknown provider dispositions fail closed to `held`. It also
  requires every registry entry to declare exactly the `PROVIDER_SURFACES` set.
  This design depends on that rule and does not touch 184's branch.

## 0. Summary

**Recommendation:**
- Expose **one** new delegated read, `ha_config_get_yaml`. It is an
  Engineering-owned, argument-constrained wrapper over upstream
  `ha_config_get_yaml`, following the `providers/upstream_blueprint.py` and
  HAMCP-129 patterns.
- **The first release reads `configuration.yaml` only** (owner decision D4/D5,
  2026-10-15). There is no `packages/` support, no glob and no other file.
  Every upstream call names the one concrete allowlisted file.
- Expose **neither** `ha_list_files` nor `ha_read_file`. Both are still
  explicitly classified as reviewed-and-never-exposed, so that they stop
  counting as unreviewed (§1.4, D3).

| | Value |
|---|---|
| Public tool | `ha_config_get_yaml` (closed schema, §3) |
| Upstream call Engineering builds | `{"file": "configuration.yaml", "yaml_path": <validated>, "include_content": false, "include_parsed": true}`; the `file` value is a binary constant |
| Readable files | `configuration.yaml` only |
| Refused before dispatch, with zero upstream calls | any other `file` value (byte-exact comparison), and any `yaml_path` that names a credential-shaped key at any level (§3, R1) |
| Returned | the parsed subtree only. It is projected through a positive allowlist and then YAML-aware redaction that carries the **full key path**, including the requested `yaml_path` (credential keys at any level, PEM, JWT, bearer, URL credentials). Connection strings and credential text use **decode, then deny by substring**: every string and key is canonicalized (percent-decoded in at most four rounds, otherwise redacted; then NFKC and casefold); values under connection-shaped keys (`*url*`, `*uri*`, `*dsn*`, `*connect*`, `*database*`) are redacted whole except an exact plain SQLite path; every other string is redacted whole when its canonical text holds userinfo or **contains** a credential word (`pwd`, `passw`, `secret`, `token`, `apikey`, `api_key`, `api-key`, `auth`, `credential`, `private`, `bearer`, `cookie`, `session`), or a delimited `pass` or `key` label (`\b(pass\|key)\s*[=:]`, owner addition C′3). At most the scheme survives. `!secret`/`!env_var` become typed reference markers (`{"secret_ref": name}`, `{"env_var_ref": name}`) that carry the name only, never a value or an `!env_var` default. Raw text and comments are never returned. |
| Admission | an **optional reviewed tool**: compiled per-version bindings for 8.4.3 and 8.5.0 over the **complete runtime descriptor** under an explicit fingerprint model, with an explicit per-version disposition for every registry version. It is a separate optional capability in the same admission generation and dispatch-lease path as stock reads. It is outside the stock policy JSON, the stock count, the order fingerprint and the whole-catalog validator, so no existing digest, signed entry, profile id or fingerprint changes (§5). |
| Core | the existing `core.basic_rest_read` + `core.basic_websocket_read` profiles; no signed-registry change (§5.4) |
| Catalog | 87 public tools while the filesystem flag is off (unchanged). **88** (59 native + 29 delegated) when the flag is on and the contract is exact (§6). |

**The single most important finding:**
- The three filesystem tools are in **no** reviewed policy, capture or
  registry contract (§9 F1).
- Adding them as ordinary policy entries would break four stock-catalog
  invariants:
  - the stock count;
  - the registry `policy_sha256`;
  - the advertised count;
  - the exact order fingerprint. A mismatch there quarantines **all**
    delegated reads.
- It would also invalidate every signed ha-mcp entry bound to the current
  policy digests.
- The design therefore introduces a narrowly scoped *optional reviewed tool*
  mechanism, rather than editing the stock policies.

A **disposable offline capture** is a prerequisite (D1). It must record the
exact runtime descriptor of the three tools for 8.4.3 and 8.5.0 with the
filesystem flag on. HAMCP-187 captured the 80-tool 8.5.0 catalog but did not
commit the artifact.

## 1. Upstream facts

### 1.1 `ha_config_get_yaml` (verified, 8.5.0)

**Registration**
- Decorator `@tool(name="ha_config_get_yaml", tags={"System","beta"},
  annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Read
  YAML Config Fragment"})` (YR:365-374).
- It is registered only when `is_filesystem_tools_enabled()` (YR:530). That
  requires `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, which is a beta sub-flag that
  also needs `ENABLE_BETA_FEATURES` (HAMCP-187 §3d).
- It is **not** gated by the YAML-editing flag.

**Signature (YR:375-420)**

```
ha_config_get_yaml(
    yaml_path: str,                       # required
    file: str = "configuration.yaml",
    include_content: bool = True,
    include_parsed: bool = False,
) -> dict
```

**Flow**
1. `_assert_mcp_tools_available`: the component must answer WS
   `ha_mcp_tools/info` and be registered in `GET /api/services`. The minimum
   component version is 1.2.0.
2. `_resolve_target_files(client, file)` (YR:284):
   - A basename without glob metacharacters is one target, passed through
     as-is.
   - A glob basename (`*`, `?`, `[`) is expanded through the component service
     `list_files` with `{"path": <dir or ".">, "pattern": <basename>}`. That
     service is non-recursive, so `packages/*.yaml` covers one directory
     level.
   - A bracketed basename runs a second, literal lookup.
   - **Engineering never uses this expansion.** The first release sends only
     the literal `configuration.yaml`, which is the no-glob, single-target
     branch, so `list_files` is never called (§2.2).
3. For each target, it calls the component service `read_file` with
   `{"path": target, "yaml_path": yaml_path, ["include_parsed": True]}`. The
   reads run concurrently through `asyncio.gather` (YR:456). **There is no
   upstream cap on the number of expanded targets.**
4. `_evaluate_read` (YR:174) turns each response into a match, a warning, or
   neither:
   - An explicitly named target that fails, or fails to parse, **raises**.
   - An expanded target degrades to a warning.
   - A missing key is neither.

**Transport (verified)**
- Calls go to the component over REST
  `POST /api/services/ha_mcp_tools/<service>?return_response=true`. The
  services are `SupportsResponse.ONLY`.
- ha-mcp never reads the filesystem itself. **All file access happens in the
  custom component inside Home Assistant.**
- Each call carries a caller token. ha-mcp obtains it from the admin-gated
  `get_caller_token` bootstrap and caches it. The component checks it with
  `_caller_token_ok` (CC:585) before `read_file` and `list_files` (CC:2748,
  CC:2894).
- `read_file` and `list_files` are token-gated, not admin-gated.

**Output (YR:493-503)**

```
{success: true, yaml_path, matches: [{file, yaml_path, content?, parsed?}],
 count, files_searched, files_unreadable, warnings?}
```

- `content` is the round-trip YAML text of the subtree, **including
  comments**. It is present when `include_content` (default true).
- `parsed` is JSON-safe structured data. It is present only when
  `include_parsed`.
- Errors come back as ha-mcp structured tool errors. A parse error carries
  `CONFIG_INVALID "… could not be parsed as YAML: not valid YAML at line L,
  column C"`. Only the position is given, never the ruamel message
  (CC:2394-2406).

**What it can read**
- Exactly what the component's read allowlist admits (§1.3).
- `yaml_path` is required and is a dotted walk over **mapping keys only**
  (CC:2386-2390):
  - There are no list indices and no keys that contain dots.
  - Non-string keys cannot be addressed.
  - A root that is not a mapping, for example `automations.yaml`, yields no
    match.
  - An empty `yaml_path` (`"".split(".") == [""]`) matches nothing, so the
    whole document is never returned through this tool (verified by reading
    the code; not executed).

**Includes and tags (verified, CC:2361-2392; YAML loader in `yaml_rt.py`)**
- The component parses with ruamel round-trip. The tags `!include`,
  `!include_dir_list`, `!include_dir_named`, `!include_dir_merge_list`,
  `!include_dir_merge_named`, `!secret` and `!env_var` are constructed as
  `_TaggedScalar` (`yaml_rt.py:17-77`).
- Includes are **never followed**, and secrets and environment variables are
  **never resolved**.
- In `parsed`, a tag renders as the source string, for example
  `"!secret api_key"`.
- `!input` is not in the registered tag set. It is **inferred** that a
  blueprint `!input` either fails to parse or loses its tag. This does not
  matter here because blueprints are out of scope.
- YAML aliases and merge keys (`<<: *x`) are **inferred** to be expanded in
  `parsed` by `yaml_jsonify`.

**Size:** none.
- The component calls `target_file.read_text()` on the whole file (CC:1366)
  with no byte cap. Only log files are tail-streamed.
- The whole file content is also part of the service response from Home
  Assistant to ha-mcp. ha-mcp forwards only `subtree`/`parsed`.
- **No upstream bound exists on the subtree size.**

**Version notes (verified by diff)**

| Version | Tool contract | Component shipped in tree | Notes |
|---|---|---|---|
| 8.4.3 (rollback) | identical signature and output to 8.5.0 | 2.1.3 | no `.log.fault`, no streaming tail (affects `ha_read_file` only) |
| 8.5.0 (production) | as above | 2.2.0 | — |
| 8.6.0 (candidate, HOLD) | description-only changes | **2.2.1** | 2.2.1: `approval_pin.json` denied, symlinked `secrets.yaml` blocked, denied names hidden from listings |
| 8.7.0 (parked) | description changes, and reads add `destructiveHint: false`, `idempotentHint: true` | 2.2.2 | new annotation fingerprints would be required |

The component version is independent of the ha-mcp version. Production runs
component 2.2.1 under ha-mcp 8.5.0, and a rollback to 8.4.3 would still run
2.2.1 (F3).

### 1.2 `ha_list_files` and `ha_read_file` (verified, 8.5.0)

**`ha_list_files(path: str, pattern: str | None = None)` (FS:552)**
- Tags `{"Files","beta"}`; `readOnlyHint: True`, `openWorldHint: False`,
  title "List Files".
- It calls the component's `list_files`, which does a non-recursive `iterdir`
  with an `fnmatch` on the name.
- Entries are `{name, path, is_dir, size, modified}`. `modified` is in epoch
  seconds. **There is no entry-count limit.**
- The root `"."` is not listable, and neither is `custom_components`.
- In 2.2.1, denied names are hidden.

**`ha_read_file(path: str, tail_lines: int | None (1-10000), yaml_path: str | None)` (FS:665)**
- Same tags and annotations.
- It returns `{success, path, content, size, modified, [subtree, parsed,
  parse_error]}`:
  - `content` is the **whole raw file**, comments included;
  - for logs it is the tail (default 1000 lines), and `yaml_path` is ignored.
- There is no byte cap and no offset parameter.
- It can read everything on the allowlist (§1.3), including `www/**`,
  `custom_components/**/*.py`, `home-assistant.log`, and admin-configured
  extra directories.

Both tools are gated by the same filesystem flag as `ha_config_get_yaml`.

### 1.3 Component read allowlist and deny floor (verified, 2.2.1)

**Positive read allowlist** (`_is_path_allowed_for_read`, CC:1169)
- Root files: `configuration.yaml`, `automations.yaml`, `scripts.yaml`,
  `scenes.yaml`, **`secrets.yaml`**, `home-assistant.log`,
  `home-assistant.log.fault` (CC:267-279).
- Directories, matched on the first segment at any depth with any extension:
  `www`, `themes`, `custom_templates`, `dashboards`, `blueprints`
  (CONST:60).
- `<packages folder>/**/*.yaml`. The folder is detected from
  `homeassistant: packages: !include_dir_*named <dir>`, and the default is
  `packages`.
- `custom_components/**/*.py`.
- **Admin-configured extra directories or exact files.** These grant both read
  and write, and can include the `/share`, `/media`, `/ssl` and `/backup`
  volumes.
  - They are runtime component options, so **the production configuration is
    unknown**.
  - An extra directory can make arbitrary paths readable, including `/ssl`
    key material (F11).
- Allowlist matching is **case-sensitive**.
- **Root configuration file name (R6).** The only root configuration file in
  the component allowlist is `configuration.yaml` (CC:267-279); there is no
  `configuration.yml` entry. Home Assistant Core itself loads only
  `YAML_CONFIG_FILE = "configuration.yaml"` (HAC:40, used at HAC:143 and
  :166); `configuration.yml` is not a Home Assistant configuration file.
  Verified at Core 2026.10.1; inferred unchanged for the other supported
  Core release. Engineering therefore accepts exactly `configuration.yaml`
  and refuses `configuration.yml` before dispatch (§2.2).

**Deny floor** (case-insensitive; checked on both the requested and the
resolved path, CC:641-749)
- `DENY_PATH_SEGMENTS = {".storage"}` (CONST:75).
- `DENY_READ_BASENAMES = {"secrets.yaml", "approval_pin.json"}` (CONST:95;
  `approval_pin.json` is new in 2.2.1).
- A path that resolves outside the config directory, or that fails to
  resolve, is denied (`_is_within_config_dir` CC:619, `_resolves_within`
  CC:705).
- `secrets.yaml` at the root is the one exception to the basename deny. It is
  allowed only for a direct read, and only while the resolved name is still
  `secrets.yaml`, and it is then **masked**:
  - every value is replaced with `key: "[MASKED]"` (CC:1248);
  - **key names, size and mtime are still disclosed**;
  - masking fails closed to withheld content.
- **There is no deny rule for** `*.pem`, `*.key`, `*.crt`,
  `home-assistant_v2.db`, `.cloud/` or `ssl/`. They are unreadable only
  because the default allowlist omits them (F10).

**Order of checks:**
1. `normpath`;
2. reject a leading `..` or `/`;
3. within the config directory (resolve + `is_relative_to`);
4. the deny floor;
5. the raw path resolves within;
6. the allow rules.

Absolute paths are allowed only under configured volume roots.

**Error strings** (CC:2919-2945):
- `"Path not allowed. Allowed paths: <list>"`. This **discloses the effective
  allowlist, including configured extra directories.**
- `"File does not exist: <path>"`, `"Path is not a file: <path>"`,
  `"Permission denied"`, `"Cannot read binary file: <path>…"`.

### 1.4 Recommendation on the two other tools

| Tool | Recommendation | Reason |
|---|---|---|
| `ha_list_files` | **Do not expose** in this task | Discloses directory layout, sizes and mtimes of `www/`, `themes/`, `blueprints/` and configured extra dirs, with no count bound. Its only possible use here was package discovery, and packages are out of scope for the first release (D4/D5). |
| `ha_read_file` | **Do not expose** | Whole raw files including comments, logs, `custom_components/**/*.py`, `www/**` and extra dirs, with no size cap. The YAML-fragment tool covers the configuration need with structure, and logs are already covered by native log tools. |

Both must still be **explicitly classified**. They become optional reviewed
names with disposition `prohibited`, are never exposed or dispatched, and their
exact descriptors are fingerprinted from the D1 capture. Then a catalog that
advertises them reports them as reviewed and withheld, not unreviewed. If
either drifts, it is reported, but nothing is exposed either way.

## 2. Secrets

### 2.1 What upstream does (verified unless marked)

| Surface | Behaviour |
|---|---|
| `!secret name` | Never resolved. In `parsed` it is the string `"!secret name"`; in `content` it is the raw tag. |
| `!env_var NAME [default]` | Never resolved, but **the default value is returned verbatim**. A default can itself be a credential (inferred risk). |
| `!include*` | Never followed; returned as a tag string |
| `secrets.yaml` | Readable by a direct read (`ha_read_file`, `ha_config_get_yaml file=secrets.yaml`), masked; key names disclosed |
| `.storage/**`, `approval_pin.json` | Denied on the requested and resolved path |
| Inline credentials in config (`password: hunter2`, `api_key: …`, a URL with `user:pass@`, a `headers: Authorization: Bearer …`) | **Returned verbatim.** No upstream redaction of file content. ha-mcp's `redact_secrets` is off by default and covers only add-on option passwords and options-flow passwords, not file content (HAMCP-187 :464-466). |
| Comments | Returned in `content` (round-trip text). They can hold commented-out credentials. |
| PEM or private-key blocks | No detection anywhere upstream; returned if inside a readable file (HAMCP-187 :568, :975-977) |
| `*.pem`, `*.key`, `*.crt`, `ssl/` | Not readable through the default allowlist; **readable** if an admin configured an extra directory that contains them |

### 2.2 Engineering-side protection (independent of upstream)

Engineering must not rely on any upstream deny rule. The layers below apply in
this order. They are **complementary, not independently sufficient**
(corrected in revision 2, R3):
- **L1** is the only path barrier that is sufficient on its own.
- **L2** is a floor that still refuses known-sensitive names if L1 were ever
  broadened in error. It does not refuse arbitrary non-sensitive names, so
  removing L1 is not covered by L2.
- **L3** and **L4** protect content: L3 refuses credential selections before
  dispatch, and L4 redacts what comes back.
- **L5** is the existing shared sanitizer.

**L1. Single-file allowlist, byte-exact** (before dispatch, zero upstream
calls).
- `file` must be absent, or a JSON string byte-equal to `configuration.yaml`.
- The comparison is made on the value exactly as received. Engineering never
  trims, case-folds, percent-decodes, normalizes, resolves or rewrites it.
  This subsumes revision 1's separate normalization layer: every traversal,
  encoding, case, separator or absolute-path variant is simply not equal.
- Everything else is refused with `yaml_file_not_allowed`. That includes
  `configuration.yml` (§1.3, R6), `Configuration.yaml`, `./configuration.yaml`,
  `/config/configuration.yaml`, `configuration.yaml ` (trailing space),
  `secrets.yaml`, any `packages/` path and any glob.
- The upstream `file` argument is a **binary constant**, never the caller's
  string. There is no glob, no expansion and no second file: every upstream
  read names the one concrete allowlisted file (R3 is resolved by this scope
  removal; there is no Engineering-denied name that upstream could read on
  Engineering's behalf).

**L2. Deny floor** (defense in depth; before dispatch; case-insensitive on
every `/`-separated segment of the caller's value). The request is refused
when any of these hold:
- a segment equals `.storage`, `.cloud`, `.git`, `ssl`, `www`,
  `custom_components`, `blueprints`, `backups`, `deps`, `tts`, `packages` or
  `.ha_mcp_tools`;
- a segment starts with `.`;
- the basename is `secrets.yaml`, `secrets.yml` or `approval_pin.json`;
- the basename matches `*secret*`, `*credential*`, `*token*`, `*.pem`,
  `*.key`, `*.crt`, `*.csr`, `*.der`, `*.p12`, `*.pfx`, `*.jks`, `*.db`,
  `*.db-*`, `*.sqlite*`, `*.json`, `*.log*`, `id_rsa*` or `id_ed25519*`.

Tests state the true relationship (replacing revision 1's false independence
test):
- every L2 deny example is also refused by L1 alone;
- the single L1-admitted value is not denied by L2 (asserted at module load
  as well as in tests);
- with L1 removed, a non-sensitive other name such as `scripts.yaml` is
  **not** refused by L2, which is why the L1-removal mutation must fail a
  test on its own.

**L3. Sensitive-selection refusal** (new in revision 2, R1; before dispatch,
zero upstream calls).
- Every segment of `yaml_path`, the leaf **and** each ancestor, is tested
  with the shared predicate `is_credential_key` (defined under L4).
- If any segment matches, the request is refused with `yaml_path_sensitive`.
  Examples: `mqtt.password`, `mqtt.credentials`, `mqtt.credentials.username`,
  `http.api_key`, `influxdb.token`, `notify_service.auth.password`,
  `some_integration.pin`.
- The caller can still read the parent, for example `mqtt`; the credential
  is then redacted by L4 and the harmless siblings, such as `broker` and
  `port`, stay visible.
- Rationale for refusing rather than only redacting: selecting a credential
  leaf returns a bare scalar whose key name is gone (the review's R1
  counterexample). Refusing before dispatch means the credential is never
  requested, never crosses the transport and cannot reach an error, warning
  or audit path.

**L4. Output projection and path-context redaction** (after dispatch, before
the shared sanitizer).

*Projection.* Only `parsed` is requested and returned. Engineering forces
`include_content: false`, so comments and raw text never cross the boundary.
A `content` field in the response is dropped and counted as a shape
violation (§4).

*Order* (made explicit in revision 3; key canonicalization moved first in
revision 5, R10). L4 evaluates `parsed` in one walk over absolute paths. At
each node it applies these steps in order:
1. the reference-marker collision check;
2. **key canonicalization** (R10): every mapping key gets its canonical form
   (rule A, then NFKC and casefold) and the key-text check K1. A key that
   fails either withholds its containing mapping;
3. the path-context rule, on the **canonical** keys of the absolute path (a
   container withheld whole is never descended);
4. reference minting;
5. the value rules on the canonical text: C′ (userinfo, credential
   words and the delimited-token rule C′3), then the key-scoped rule B
   with its SQLite exception (R11);
6. the other value scanners, on the canonical text from rule A;
7. the node and depth caps with truncation.

A minted marker is final, and later L4 steps skip it. L5 then runs on the
result, and only after L5 are markers rendered.

*`is_credential_key(key)`*, the single shared predicate used by L3 and L4:
- **Input is the canonical key** (revision 5, R10): `str(key)` canonicalized
  by rule A (percent-decoding, at most four rounds), then NFKC-normalized
  and casefolded. The raw key is never classified. Under L3 the `yaml_path`
  grammar admits only ASCII `[A-Za-z0-9_-]`, so canonicalization there only
  lowercases. In L4 it decodes `%70assword`, `p%61ssword` and
  `%2570assword` to `password`, and maps fullwidth `ｐａｓｓｗｏｒｄ` to
  `password`.
- Normalization of the canonical key: every run of characters outside
  `[a-z0-9]` replaced with `_`, then leading and trailing `_` stripped. This
  is the same normalization as `E/sanitization.py` `_normalize_key` (:146).
- A key matches if its **compact** form (the canonical key with every
  character outside `[a-z0-9]` removed) contains any **credential word** of
  rule C′ (`pwd`, `passw`, `secret`, `token`, `apikey`, `auth`,
  `credential`, `private`, `bearer`, `cookie`, `session`; the separator
  variants `api_key` and `api-key` compact to `apikey`). This clause is new
  in revision 5 and makes "a key containing a credential word redacts its
  value" part of the one predicate, so L3 refuses such a selection and the
  path-context rule withholds the value. Key compaction means `p_w_d`,
  `Pass Word` and `API-Key` match too.
- A key also matches if its normalized form is:
  - in any existing `sanitize_untrusted_data` key set (`E/sanitization.py`
    :19-75), for example `password`, `passphrase`, `token`, `api_key`,
    `secret`, `credential`, `credentials`, `client_secret`;
  - in the YAML exact set: `api_password`, `apikey`, `app_key`, `app_secret`,
    `auth`, `auth_token`, `bearer_token`, `consumer_key`, `consumer_secret`,
    `access_key`, `secret_key`, `private_key`, `encryption_key`, `psk`,
    `pin`, `code`, `passcode`, `key`, `pwd`, `pass`;
  - or it **contains** any of `password`, `passwd`, `passphrase`, `secret`,
    `token`, `credential`, `api_key`, `apikey`, `private_key`;
  - or it **ends with** `_pin`, `_code`, `_psk`, `_key`, `_pass` or `_pwd`.
- The rules deliberately over-match. Harmless fields such as `country_code`,
  `token_url` or `entity_key` are redacted, and selecting them is refused.
  Since revision 5 this includes every key mentioning a credential word,
  for example `homeassistant.auth_providers`, `auth_mfa_modules`,
  `private_ble_device`, `session_timeout` and a `logger.logs` entry named
  `homeassistant.components.auth`. This is fail-closed and disclosed in the
  description.

*Key-text check K1* (revision 5, R10). A key's own text is emitted, so it
must not carry a credential either. On the canonical key:
- if it holds userinfo (`://` followed later by `@`), **or**
- if its compact form contains a credential word, or its canonical text
  matches the delimited-token pattern of C′3, **and** the canonical key is
  not a plain identifier (it does not fully match `[a-z0-9_.-]+`),

then the **containing mapping** is withheld whole as
`[REDACTED:connection_string]`. A key that matches C′3 holds `=` or `:`,
so it is never a plain identifier. So `{"pwd=S": 1}`, `{"key=S": 1}`,
`{"pass: S": 1}`, `{"Pass Word": …}` and
a URI with userinfo used as a key withhold their mapping, while an
identifier key such as `mqtt_password`, `%70assword` or `PASSWORD` is shown
(raw) with its value redacted under the path-context rule. A key that rule
A refuses (invalid, ambiguous or non-converging) withholds its mapping as
`[REDACTED:encoding]`.

*Path-context rule (R1).* Redaction is defined on the **absolute key path**,
not on the returned node alone:
- For every node of the returned value, its absolute path is the requested
  `yaml_path` segments followed by the mapping keys from the selected root to
  that node. List elements are transparent: an element inherits the path of
  the key that holds the list.
- Every key on the path is judged in its **canonical** form (R10); the
  output keeps the raw key text of visible nodes.
- If any key on a node's absolute path matches `is_credential_key`, the node
  at the **first** matching key is replaced as a whole:
  - a scalar of any type (string, integer, float, boolean or null) becomes
    `"[REDACTED:credential_key]"`;
  - a mapping or list is replaced whole by the same marker. Its children are
    not walked, so innocuous-looking child names below a sensitive ancestor
    (for example `credentials: {user: …, value: …}`) never appear.
- **Reference tags under a sensitive key** (revision 3, R8): a scalar whose
  own key is the first matching sensitive key and which is minted as a
  **reference marker** under the *Reference markers* rule below is kept as
  that marker. The reference name stays visible; a resolved value never
  appears, and an `!env_var` default never appears. Every other value under
  a sensitive key gets the credential marker. This includes a malformed
  reference, an `!include*` tag string, and a plain string that merely
  resembles a reference. A reference inside a container that is withheld
  whole (for example `credentials: {password: !secret x}`) is withheld with
  the container, because its children are not walked.
- With L3 in place, the requested prefix never contains a credential-shaped
  segment. The rule still includes the prefix so that it is correct on its
  own: if L3 were removed, a direct `mqtt.password` selection would return
  the marker as the whole `value`. Tests run both ways (L3 mutation, §8.2).
- The envelope's generic key `value` carries no key context. Redaction is
  therefore applied **before** the value is placed in the envelope, using the
  absolute path, and never inferred from envelope keys.

*Connection and credential-text rules A, B, C′, D (R2; **decode, then
deny by substring** since revision 5)*, scoped to this reader (no change
to the global sanitizer's patterns).
- Revisions 2 and 3 **recognized** a connection string, then validated it.
  Recognition could be bypassed by encoding.
- Revision 4 decoded first and then **parsed** `name=value` assignments
  (C2). The parse was itself bypassable: its `==` exemption missed
  `Pwd==S`, which is a valid assignment of the value `=S`.
- Revision 5 stops parsing. Every string is canonicalized, and any
  canonical text that **contains** a credential word is redacted whole,
  whatever surrounds the word. Josh's addition C′3 matches the two words
  left out of that list, `pass` and `key`, only as a delimited label
  (§15.1).
- The revision-2/3 recognizer, strict grammar, decoded charsets,
  query-name allowlist and web-URI regime, and the revision-4 C2
  assignment predicate and its credential-like name list, are
  **withdrawn**.

- **A. Canonicalize first.** This applies to every string value and every
  mapping key in `parsed` that is not a minted reference marker (keys:
  step 2 of the order above; values: step 5).
  - One **decoding round** replaces each valid escape `%HH` (two hex
    digits) with its byte. Other characters are kept, and `+` is not
    special. The resulting bytes must be strict UTF-8.
  - A round is **invalid** if its result is not valid UTF-8, or if it adds
    a control character (C0 other than tab, LF or CR, DEL or C1). It is
    **ambiguous** if its input holds a valid escape and also a `%` that does
    not start one. Decoders disagree on such mixed input.
  - **The bound (one number):** at most **four** decoding rounds. Decoding
    stops at the first round whose input holds no valid escape, which may
    be after zero, one, two, three or four rounds.
  - **Normalization:** the decoded text is then Unicode **NFKC**-normalized
    and **casefolded** (`str.casefold()`). The result is the **canonical
    text**.
  - **One final check:** the canonical text must hold no valid escape.
    This one check covers two cases. One is a value still escaped after
    the fourth round, so a value that needs a fifth round is
    non-converging even if that round would end it. The other is an
    escape that NFKC introduced, such as fullwidth `％７０`, which becomes
    `%70`.
  - An invalid, ambiguous or non-converging value is redacted whole as
    `[REDACTED:encoding]`. For a mapping key, the mapping that contains the
    key is withheld whole, because a key cannot be rewritten without
    changing the structure.
  - A string with no valid escape, such as `50% off`, needs zero rounds.
  - **Every check below runs on the canonical text, never on the raw
    form.** When a value is kept visible, the **raw** string is emitted
    unchanged. Engineering never rewrites a value to its canonical form.
- **C′. Substring deny** on the canonical text of **every** string value,
  under any key. The value is redacted whole when any of these holds:
  - **C′1. Userinfo.** The text contains `://` and, anywhere after the
    first `://`, an `@` (unchanged from revision 4). This includes
    user-only userinfo and passwords that hold `/`, `?`, `#` or spaces once
    decoded. It deliberately over-matches an `@` later in a path, query or
    sentence.
  - **C′2. Credential word.** The text **contains**, anywhere, any of
    these 13 words: `pwd`, `passw`, `secret`, `token`, `apikey`, `api_key`,
    `api-key`, `auth`, `credential`, `private`, `bearer`, `cookie`,
    `session`. This is a plain substring test. There is no word boundary,
    no delimiter, no `=` and no parsing. `Pwd==S`, `PWD :=S`, `;pwd;S`,
    `P%57D=S` (canonical `pwd=s`), fullwidth `ｐｗｄ=S`, `?x=PWD%3DS` and a
    JSON payload `{"pwd": "S"}` are all caught by the same rule.
  - **C′3. Delimited token** (owner addition, 2026-10-15). C′2 leaves out
    `pass` and `key` because, as plain substrings, they would redact
    `keypad`, `monkey`, `bypass` and `compass`. C′3 matches those two words
    only as a delimited label: the value is redacted whole if
    `re.search(r"\b(pass|key)\s*[=:]", canonical)` matches anywhere. The
    pattern is a Python `str` pattern, so `\b` and `\s` are
    Unicode-aware, and it runs on the same canonical text as C′1 and C′2
    (after decoding, NFKC and casefold).
    - Caught: `pass=S`, `PASS ==S`, `key: S`, `;key=S`, `key==S`,
      `pass:=S`, `key :=S`, `x=1;pass=S`, `?a=1&KEY=S`, a tab or no-break
      space before the delimiter, fullwidth `ｋｅｙ＝S` and `ｐａｓｓ：S`
      (NFKC gives `key=s` and `pass:s`), and every percent-encoded form
      within the bound (`pass%3DS`, `%6Bey=S`, `k%45Y: S`).
    - Not caught: words that merely contain `pass` or `key` (`keypad`,
      `monkey`, `bypass`, `compass`, `hotkey=F5`, `passkey=1`, `keys=3`,
      `passes: 2`), and either word without a following `=` or `:` (`key
      fob`, `Hall pass`, `pass the salt`).
    - `_` is a word character, so `my_key=S` and `app_key=S` are not
      matched (a D12 residual; `api_key` is a C′2 word).
  - A credential word in a **key** is handled by `is_credential_key` (on
    the compact canonical key) and K1, which also applies C′3 to
    non-identifier keys. C′ is the value-side rule.
  - **Redaction form (keeps at most the scheme).** It is
    `<scheme>://[REDACTED:connection_uri]` when the canonical text begins,
    at position 0, with a scheme matching `[a-z][a-z0-9+.-]{0,31}`
    followed by `://`, **and** the scheme itself contains no credential
    word. The scheme is emitted in its canonical (lower-case) form.
    Otherwise (no scheme, leading whitespace, a scheme such as `token://`,
    a non-string or a container) it is `[REDACTED:connection_string]`.
    C′3 never fires on a scheme alone, because the scheme grammar admits
    no `=` or `:`. A `key://host` value is redacted by C′3 (its text holds
    `key:`) and keeps `key://`.
  - A value that passes C′ and is not under a connection-shaped key is
    visible as its raw string. This holds for credential-free database URLs
    under non-connection keys (for example `sensor.value:
    mysql://db.local:3306/ha?charset=utf8mb4`), for web URLs and templated
    REST URLs without a credential word, for unknown schemes and for
    credential-free DSNs such as `Server=db;Trusted_Connection=yes`.
- **B. Key-scoped always-redact** (unchanged in substance; R10 makes the key
  canonical). A key is **connection-shaped** if its compact canonical name
  (rule A, NFKC, casefold, then every character outside `[a-z0-9]`
  removed) **contains** `url`, `uri`, `dsn`, `connect`, `connstr` or
  `database`. Examples: `db_url`, `DB_URL`, `dbUrl`, `database_url`, `url`,
  `base_url`, `external_url`, `uri`, `redirect_uri`, `dsn`, `connection`,
  `Connection_String`, `conn_str`, `connect`, `connect_args`, `database`,
  `odbc_connect`, `sqlalchemy_url`, `Db-Url`, the encoded `db%5Furl`, and
  `db_urℓ` (NFKC maps `ℓ` to `l`).
  - It is judged on the **absolute path** (the requested `yaml_path`
    segments plus the returned keys), like the path-context rule, at any
    nesting level. A direct `recorder.db_url` selection is in scope through
    its last `yaml_path` segment and never through the envelope key `value`.
  - The node at the **first** connection-shaped key is redacted whole, in
    the C′ redaction form. This covers strings, numbers, booleans, null,
    mappings and lists. Children of a container are not walked.
  - A minted reference marker (`db_url: !secret recorder_db_url`) stays a
    marker (R8, unchanged). It carries a name, not a value.
  - **R11. One exact safe shape stays visible.** A string under a
    connection-shaped key is visible only if **all** of these hold:
    - it passed C′ (no userinfo and no credential word, so
      `sqlite:////config/session.db` is redacted);
    - `re.fullmatch(r"sqlite:///[a-z0-9_./-]+", canonical)` succeeds. The
      contract is a **full-string** match. `re.match`, `re.search` or a
      pattern anchored with `^…$` does not satisfy it, because Python's `$`
      also matches before a final newline;
    - the canonical text holds no control or line-separator character:
      none of `\x00`–`\x1f`, `\x7f`, ` ` or ` `. The character
      class already excludes them. They are also checked explicitly, so
      that a later widening of the class cannot admit them;
    - no `/`-separated segment of the canonical text is exactly `..`.
      `sqlite:///../x.db` and `sqlite:////config/../x.db` are redacted;
      `sqlite:///a..b.db` is a file name, not a segment, and stays visible;
    - the raw string is identical to its canonical text: zero decoding
      rounds, and NFKC and casefold change nothing. Any `%`, upper case
      (`SQLITE:///x`, `X.db`) and fullwidth characters are therefore
      redacted.

    Example: `sqlite:////config/home-assistant_v2.db`. Showing the text
    never opens, resolves or reads the path; the `..` refusal keeps the
    one visible shape simple rather than guarding a filesystem.
- **Minted references are outside C′.** Reference minting (step 4) runs
  before C′ (step 5), and a minted marker is final. A `!secret` or
  `!env_var` name such as `mqtt_password` is therefore kept as a marker
  even though it contains a credential word. The name grammar admits no
  `%`, `=`, `:`, `@` or space. This is the R8 reference behaviour and the
  quoted-literal residual that Josh has accepted, not a new exemption.
- **D. Residual (D12, best-effort).** C′ recognizes a credential only when
  its canonical text holds userinfo or one of the 13 words, or when it sits
  under a credential or connection-shaped key. It does **not** recognize:
  - a raw secret under an innocent key, where neither the key nor the value
    holds a credential word (`foo: s3cr3t`, `code_word: …`);
  - a credential whose label holds none of the 13 words and is not a
    delimited `pass` or `key` (C′3), such as `pw=`, `psk=`, `pin=`,
    `code=`, `login=` or `sig=`;
  - a `pass` or `key` label that C′3 does not see as delimited: joined to
    a preceding letter, digit or `_` (`my_key=`, `app_key=`, `passkey=`),
    separated from its `=` or `:` by a non-whitespace character (a quoted
    JSON or Python label such as `"key": "S"` or `{'pass': 'S'}`, and
    `pass_word=`), or followed by another delimiter (`key→S`, `key S`).
    Keys are not affected, because `is_credential_key` still matches
    `pass`, `key`, `pin`, `code` and the `_pass`/`_key` suffixes;
  - a credential word interrupted inside a **value** by separators or other
    characters (`p_w_d=`, `to-ken=`). Value matching is a plain substring
    and does not compact; keys are compacted;
  - a credential word written with confusables that NFKC does not map (the
    Cyrillic `с` in `Pсd=` or `е` in `kеy=`), or hidden by a non-percent
    encoding (base64
    shorter than the 200-character blob scanner, `&#112;wd`,
    `pwd`);
  - a secret split across list items or sibling values.

  These leak if present. The tool description and `limitations` say so.
  Rules A and C′ remove the bypasses that the delta reviews found. No amount
  of percent-encoding up to the bound hides a credential word or userinfo.
  More encoding than the bound is redacted. For the 13 C′2 words no
  assignment syntax, such as `==`, `:=` or a missing `=`, matters any more.
  C′3 needs only an `=` or `:` after optional whitespace, so `==` and `:=`
  are caught there too.
- **Examples** (synthetic values; `S` is a synthetic credential with no
  credential word, and `U` is the URL userinfo `u` + `:` + `S`):

  | Key and input | Output |
  |---|---|
  | `db_url: sqlite:////config/home-assistant_v2.db` | unchanged |
  | `db_url: sqlite:////config/home%2Dassistant_v2.db` (any `%`) | `sqlite://[REDACTED:connection_uri]` |
  | `db_url: sqlite:///x.db\n`, `sqlite:///../x.db`, `SQLITE:///x.db` | `sqlite://[REDACTED:connection_uri]` (R11) |
  | `db_url: sqlite:///:memory:` or `…/x.db?mode=ro` | `sqlite://[REDACTED:connection_uri]` |
  | `db_url: mysql://db.local:3306/ha?charset=utf8mb4` (no credential) | `mysql://[REDACTED:connection_uri]` |
  | `db_url: 42`, `database: home_assistant` | `[REDACTED:connection_string]` |
  | `db_url: !secret recorder_db_url` | reference marker `{"secret_ref": "recorder_db_url"}` (R8) |
  | `external_url: https://example.ui.nabu.casa` | `https://[REDACTED:connection_uri]` (disclosed cost of B) |
  | `value: mysql://db.local:3306/ha?charset=utf8mb4` | unchanged (passes C′) |
  | `value: unknown://host/path` | unchanged (passes C′) |
  | `value: mysql://U@db/ha`, `mysql://u@db/ha` | `mysql://[REDACTED:connection_uri]` (C′1) |
  | `value:` the same URI percent-encoded 1–4 times | `mysql://[REDACTED:connection_uri]` (A, then C′1) |
  | `value:` the same URI percent-encoded 5 or 6 times | `[REDACTED:encoding]` (A) |
  | `value: Server=db;Uid=u;Pwd=S`, encoded 0–4 times | `[REDACTED:connection_string]` (C′2) |
  | `value: Server=db;Uid=u;Pwd==S` (the revision-4 counterexample), `PWD :=S`, `;pwd;S`, `P%57D=S`, `ｐｗｄ=S` | `[REDACTED:connection_string]` (C′2) |
  | `value: mysql://db/ha?pwd==S` | `mysql://[REDACTED:connection_uri]` (C′2) |
  | `value:` the same DSN encoded 5 or 6 times | `[REDACTED:encoding]` (A) |
  | `value: mssql+pyodbc:///?odbc_connect=` + encoded `…;UID=u;PWD=S` (the revision-2 probe) | `mssql+pyodbc://[REDACTED:connection_uri]` (C′2) |
  | `value: host=db dbname=ha user=u password=S` | `[REDACTED:connection_string]` (C′2) |
  | `value: pass=S`, `PASS ==S`, `key: S`, `;key=S`, `pass:=S`, `ｋｅｙ＝S`, `pass%3DS` | `[REDACTED:connection_string]` (C′3) |
  | `value: https://api.example.com/v1?key=S` | `https://[REDACTED:connection_uri]` (C′3) |
  | `value: keypad`, `monkey`, `bypass`, `compass`, `key fob`, `passkey=1` | unchanged (C′3 needs a delimited label) |
  | `value: Press key: Enter`, `Hall pass: yes` | `[REDACTED:connection_string]` (C′3, disclosed over-redaction) |
  | `value: Refresh the access token daily` | `[REDACTED:connection_string]` (C′2, disclosed over-redaction) |
  | `value: https://example.com/oauth/authorize` | `https://[REDACTED:connection_uri]` (C′2, disclosed over-redaction) |
  | `value: token://host/x` | `[REDACTED:connection_string]` (the scheme holds a word) |
  | `value: Server=db;Database=ha;Trusted_Connection=yes` | unchanged (no credential word) |
  | `value: 50% off` / `value: %41%` | unchanged / `[REDACTED:encoding]` (ambiguous) |
  | `value: ％７０wd=S` (fullwidth `%`) | `[REDACTED:encoding]` (NFKC introduced an escape) |
  | `mqtt: {broker: core-mosquitto, "%70assword": S}` | `broker` visible; `%70assword` shown with the credential marker (R10) |
  | `x: {"%63redentials": [{value: S}]}` | `%63redentials` shown with the credential marker; the list is withheld (R10) |
  | `x: {"pwd=S": 1}` | `x` withheld as `[REDACTED:connection_string]` (K1) |
  | `x: {"key=S": 1}` | `x` withheld as `[REDACTED:connection_string]` (K1 with C′3) |
- **Credential-free recorder settings stay visible**, for example
  `purge_keep_days`, `commit_interval`, `auto_purge`, `auto_repack`,
  `db_max_retries`, `db_retry_wait`, `include` and `exclude`. `db_url` is
  visible only in the exact safe SQLite shape.
- **Disclosed over-redaction (the cost of B and C′).**
  - Every non-SQLite `db_url` is redacted to its scheme, including
    credential-free MySQL and PostgreSQL URLs.
  - Every URL under a `*url*`/`*uri*` key is redacted to its scheme,
    including `homeassistant.external_url`, `internal_url`, REST and
    `rest_command` `url`, and camera `still_image_url`.
  - Plain values under `database` (for example InfluxDB's database name)
    become `[REDACTED:connection_string]`.
  - Substring matching catches innocent connection keys:
    `connect_timeout`, `reconnect_interval` (numbers too), `security`,
    `during`.
  - **C′2 redacts any harmless value that mentions a credential word**
    (new in revision 5). Examples: "Refresh the access token daily",
    "Author: …", "Session started", "private room", "cookie jar",
    "secretary", any `oauth`/`authorize` URL, a template mentioning
    `token`, `session` or `auth` (for example `{% if token == 1 %}`), and a
    `compass password hint`. The scheme is kept where present.
  - Keys mentioning a credential word withhold their value (see
    `is_credential_key`), for example `auth_providers` and
    `private_ble_device`.
  - C′1 redacts any value with an `@` after `://`, such as an email in a
    query.
  - Rule A redacts the rare benign string that mixes valid escapes with a
    stray `%`.
  - **C′3 redacts harmless text with a delimited `pass` or `key` label**
    (owner addition): `Hall pass: yes`, `Press key: Enter`, `Spare
    key=garage`, a template `{% set key = 1 %}`, a `key://` or `pass://`
    URI (scheme kept), and a URL whose host is `key` or `pass` followed by
    a port (`http://key:8080/`).
  - Revision 4's C2 over-matches on names such as `monkey=1` stay gone,
    because C′3 needs a word boundary before `pass` or `key`.
  - Josh has accepted this over-redaction (§15.1 decision 3).

*Other value scanners*, on the canonical text (rule A) of every string that
survives the steps above; a hit redacts the raw value:
- the existing JWT, bearer, `Authorization` header, URL userinfo, URL
  credential-parameter, webhook-path and Matter patterns (`E/sanitization.py`
  :76-92). The existing URL-userinfo patterns cover only `http(s)` (:84-85),
  which is why the connection-string rules above are required;
- **new PEM detection**: any `-----BEGIN [A-Z0-9 ]*(PRIVATE KEY|CERTIFICATE|ENCRYPTED PRIVATE KEY|OPENSSH PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----`
  marker redacts the whole string, not just the block (the #2714 style);
- a bare base64 run of 200 or more characters is redacted as
  `opaque_blob`.

*Reference markers* (revision 3, R8). One rule applies at every depth and
under every key, sensitive or not:
- **What upstream gives Engineering.** Upstream renders a tagged scalar as
  its source text (`"!secret mqtt_password"`, `"!env_var MQTT_PW fallback"`,
  `yaml_jsonify`, §1.1). Engineering never sees the YAML tag itself.
- **Minting.** L4, and only L4, mints a reference marker. It does so from a
  whole scalar string value (never a mapping key, and never a substring)
  that matches one of these exactly:
  - `^!secret ([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})$`, which becomes
    `SecretRef(name)`;
  - `^!env_var ([A-Za-z_][A-Za-z0-9_]{0,127})$`, which becomes
    `EnvVarRef(name)`;
  - `^!env_var ([A-Za-z_][A-Za-z0-9_]{0,127}) .+$` (dot matches newline),
    which becomes `EnvVarRef(name, default_redacted=True)`. The default is
    discarded at once. It is never inspected, logged, counted by value or
    forwarded, and `env_var_default` is recorded.

  A name that trips any value scanner (JWT, bearer, opaque blob or a known
  secret) is not minted.
- **Not minted.** The following are a *malformed reference*:
  - any other string whose first non-whitespace characters are
    case-insensitively `!secret` or `!env_var` (for example `!secret`
    alone, `!secret two words`, `!SECRET x`, a leading space, a name over
    128 characters, or `!env_var 1BAD`);
  - a matching string that trips a value scanner.

  A malformed reference becomes the credential marker under a sensitive key
  and `[REDACTED:reference_malformed]` elsewhere.
- **Marker identity.** A marker is an instance of an Engineering-internal
  frozen type, `ReferenceMarker` (kind `secret` or `env_var`, the name, and
  `default_redacted`), defined in `E/sanitization.py`. No upstream data can
  construct it: upstream output is JSON, and the only constructor call is
  in L4's minting rule. A JSON mapping that looks like a rendered marker is
  ordinary data. An upstream mapping whose key set is exactly `{secret_ref}`,
  `{env_var_ref}` or `{env_var_ref, default_redacted}` is replaced whole
  with `[REDACTED:reference_marker_collision]`, so the client can never be
  shown a forged reference.
- **Rendering.** After L5, and only then, the reader's envelope builder
  serializes each marker to `{"secret_ref": "<name>"}`, `{"env_var_ref":
  "<name>"}` or `{"env_var_ref": "<name>", "default_redacted": true}`.
  No sanitizer runs after rendering. The node cap counts a marker as one
  node. The response size check runs on the rendered form.
- **Residual (disclosed; verified, `yaml_rt.py`:208-209).** A YAML string
  literal written exactly like a tag (for example `password: "!secret
  abc"`, quoted) renders byte-identically to a real `!secret abc` tag.
  Engineering cannot tell them apart, so it is shown as
  `{"secret_ref": "abc"}`. The exposure is bounded to text that matches the
  strict name grammar and passes the value scanners. Josh has accepted
  this residual (§12, revision 3; recorded in §15).

*Other tags* (outside sensitive keys):
- `!include*` strings are kept only if the target matches
  `[A-Za-z0-9_./-]{1,256}`.
- Any other string that starts with `!` passes through the normal value
  scanners.

*Accounting.* Redaction is recorded per category (`redaction.categories`:
`credential_key`, `connection_uri`, `connection_string`, `encoding`, `env_var_default`,
`reference_malformed`, `reference_marker_collision`, `pem`, `opaque_blob`,
and the existing sanitizer categories) and as `redacted_field_count`. A
preserved reference marker is not a redaction. `completeness` stays
`complete`, because a redacted value is not missing data.

**L5. The existing gateway sanitizer.** It runs last, with its usual key
sets, `max_string` and its `response_too_large` refusal. It has one
narrow, reader-scoped addition (R8):
- **Opt-in exemption.** `sanitize_untrusted_data(...,
  preserve_reference_markers=False)` is a new keyword, default off. With it
  on, the walker keeps a value only if `type(value) is ReferenceMarker` (an
  exact type, never a subclass, and never decided by JSON shape or string
  content) **and** its name still matches the minting grammar. This holds
  under a sensitive key too: the check runs before the key-category
  replacement for that item. A marker whose name fails revalidation becomes
  `[REDACTED:reference_malformed]`.
- **Nothing else is exempt.** A plain string such as `"!secret x"` is
  sanitized as usual, and under a sensitive key it is replaced by the key
  marker. So is a mapping shaped like a rendered marker, and so is any
  other value.
- **Default off.** With the opt-in off, which is every other tool, a
  `ReferenceMarker` that ever reached the sanitizer is replaced by
  `[REDACTED:reference_marker]`. It never reaches `repr()`, so a name
  cannot leak through another tool. Output for every value that is not a
  `ReferenceMarker` is byte-identical with the opt-in on or off.
- Only `ha_config_get_yaml` passes the opt-in.

**`!secret` values are never resolved by Engineering**: Engineering has no
code path that reads `secrets.yaml` and will not gain one.

**No credential in errors, warnings or audit.**
- Errors and warnings use fixed codes and categories only (§4); no upstream
  text and no value is forwarded.
- Audit records the accepted `yaml_path` verbatim (it matched the key-path
  pattern and has no credential-shaped segment) and the constant file name.
- For a refused argument, audit records only the reason code and the
  argument's length, never the refused text.
- Audit never records returned values.

### 2.3 Verified versus inferred

| Claim | Status |
|---|---|
| Upstream never resolves `!secret`/`!env_var`/`!include` | Verified (CC:2361-2392, `yaml_rt.py`) |
| Root `secrets.yaml` (R7) | Verified for 2.2.1: a **direct root read is allowed and masked**, not refused (values replaced, key names, size and mtime disclosed; CC:1248, §1.3). Nested or resolved `secrets.yaml` paths are denied by the basename deny floor (CONST:95). Engineering refuses root `secrets.yaml` itself before dispatch (L1, L2), with a zero-upstream-call test. |
| `.storage` is refused by the component | Verified for 2.2.1 (CONST:75, requested and resolved path); Engineering never addresses it (L1) |
| A symlinked `configuration.yaml` that resolves to `secrets.yaml` or into `.storage/` is refused | **Inferred.** Component 2.2.1 checks the resolved path (CC:684-685, :747-749), and 2.2.1 blocks a symlinked `secrets.yaml`. Engineering cannot see the filesystem, so it relies on the component for symlinks and on L4 for content. Disclosed. |
| A symlinked `configuration.yaml` that resolves to another allowed file is readable | **Inferred** possible. The allow rule is evaluated on the requested path. L4 still applies to whatever is returned. |
| Inline credentials are returned by upstream | Verified (no redaction in the read path) |
| A selected credential leaf loses its key name upstream (R1) | Verified by the review's offline probe (CC:2386-2394 returns only the selected node). Engineering refuses such selections (L3) and redacts by absolute path (L4). |
| Database URIs with userinfo pass the existing scanner (R2) | Verified by the review's offline probe (`E/sanitization.py`:84-85 match `http(s)` only). Engineering adds the connection and credential-text rules A, B and C′ (L4). |
| A percent-encoded ODBC DSN in an `odbc_connect` query value passes revision 2's name-based URI rule and real L5 (R2 residual) | Verified by the delta review's probe at `e6fb813`. Revision 3's default-deny fixed that probe, but its recognition step was bypassable (next row). |
| Fully percent-encoded URIs (1–6 rounds) and keyword DSNs encoded past the bound (5–6 rounds) under ordinary keys bypass revision 3's recognition and survive real L5 (R2, second residual) | Verified by the second delta review's 12 boundary cases at `042f254` (8 counterexamples). Revision 4 replaced recognition with decode-then-judge rules (L4). The author's throwaway rule model, run with the real L5 sanitizer and the reviewer's verbatim fixtures, redacts all 12 cases at both ordinary and connection paths (§14, and again under revision 5's rules in §15). That model is a self-check, not a review, and not an implementation; Part 2 tests prove the implementation. |
| Revision 4's assignment parse misses `Pwd==S` (its `==` exemption), raw-key checks miss encoded or fullwidth credential keys, and `^…$` admits a SQLite value with a trailing newline (R2 third residual, R10, R11) | Verified by the revision-4 delta review's 438 probe assertions at `3391172`. Revision 5 replaces parsing with the C′ substring deny, canonicalizes keys and uses `re.fullmatch` with explicit control, `..` and raw-equals-canonical checks. The author's revision-5 self-check ports all 438 assertions (425 unchanged, 13 deliberate flips) and adds 278 (§15). It is a self-check, not a review. |
| Revision 5's value-side word list misses `pass=S` and `key=S` under innocent keys (the narrowing disclosed in §15) | Closed by the owner-added delimited-token rule C′3 (§2.2, §15.1). The author's re-run self-check passes 1028/1028 with real L5, and dropping C′3 fails all 85 ordinary-path token catches (§15.1). Undelimited forms (`my_key=`, quoted `"key":`) remain D12 residuals. It is a self-check, not a review. |
| L5 unconditionally replaces values under sensitive keys, so an L4-preserved reference cannot survive the unchanged sanitizer (R8) | Verified (`E/sanitization.py`:341-350; delta review probe). Revision 3 adds an opt-in exemption for the internal `ReferenceMarker` type only (L5). |
| Upstream renders a tagged scalar and a quoted string with the same text identically in `parsed` | Verified (`yaml_rt.py`:208-209, component 2.2.1 tree): `f"{node.tag} {node.value}".strip()`. A quoted literal that looks exactly like a reference is therefore shown as a reference (disclosed residual, L4 *Reference markers*). |
| L4 catches every secret | **Not claimed.** Key and pattern redaction is best-effort over free-form configuration. An unlabeled secret value (for example `foo: s3cr3t`) cannot be recognized (D12). R1 and R2 are labeled or structurally recognizable credentials and are **not** covered by the D12 residual. Rules A and C′ do not recognize a credential whose label holds none of the 13 credential words and is not a delimited `pass` or `key` (for example `pw=`, `psk=`, `my_key=`, a quoted `"key": S`), a word interrupted by separators inside a value, confusables that NFKC does not map, a non-percent encoding, or a secret split across values (§2.2 rule D). The cost is disclosed over-redaction of harmless text that mentions a credential word. Disclosed in the tool description and in `limitations`. |
| Upstream alias expansion cannot exceed Engineering caps | Inferred; bounded by §4 caps and `response_too_large` |

## 3. Argument model

**Public input schema** (closed, binary-owned, `additionalProperties: false`):

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["yaml_path"],
  "properties": {
    "yaml_path": {
      "type": "string",
      "pattern": "^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}(\\.[A-Za-z0-9_][A-Za-z0-9_-]{0,63}){0,7}$",
      "description": "Dotted mapping-key path in configuration.yaml, e.g. 'recorder' or 'homeassistant.customize'. List indices are not supported. Paths naming a credential key (password, token, api_key, pin, code, ...) are refused; read the parent instead."
    },
    "file": {
      "type": "string",
      "enum": ["configuration.yaml"],
      "default": "configuration.yaml",
      "description": "Only configuration.yaml is readable in this release."
    }
  }
}
```

The `file` property is kept, restricted to one value, so that a caller can
be explicit and so that a later release could widen the enum additively
(§12, deferred item). Engineering validates it itself (L1); it does not rely
on the schema enum.

**Engineering-built upstream arguments**

```
{"yaml_path": <validated>, "file": "configuration.yaml",
 "include_content": false, "include_parsed": true}
```

All four keys are always sent; nothing is defaulted upstream. The `file`
value is a binary constant, not the caller's string.

**Rejected:**
- any other property, including `include_content`, `include_parsed`, `path`,
  `tail_lines`, `pattern` and `action`;
- non-string values;
- a `yaml_path` that is empty, has more than eight segments, a segment over
  64 characters, a segment that starts with `-`, or any character outside the
  pattern, such as spaces, `*`, `[` or quotes;
- a `yaml_path` with any credential-shaped segment (L3);
- a `file` that fails L1 or L2.

Every refusal happens before admission and dispatch: zero upstream calls, and
an audit record with a fixed reason code (`yaml_read_arguments_invalid`,
`yaml_path_invalid`, `yaml_path_sensitive`, `yaml_file_not_allowed`).

**Allowed path set:** `configuration.yaml` only.

**Excluded in the first release** (D4/D5, owner decision 2026-10-15):
- **`packages/` entirely**: no `packages/*.yaml` glob and no single package
  files. Production does not use Home Assistant packages; they are not
  enabled by default, and Josh is unaware of any. Adding packages later is a
  deferred item (§12), limited to explicit named-file reads, never upstream
  glob expansion.
- `automations.yaml` and `scenes.yaml` are lists at the root. They are not
  addressable by `yaml_path`, and native tools already cover them.
- `scripts.yaml` is covered by `ha_config_get_script`.
- Component extra directories are assumed to be at their default (empty)
  configuration; Engineering never addresses them regardless (F11).

## 4. Output

**Positive allowlist** (anything else is dropped):

```
{
  "file": "configuration.yaml",
  "yaml_path": <requested path, echoed>,
  "source": "disk",                              # not the loaded runtime config
  "found": bool,
  "value": <redacted parsed> | null,             # null when not found; may contain
                                                 # reference markers {"secret_ref": ..} /
                                                 # {"env_var_ref": .., ["default_redacted": true]}
  "completeness": "complete" | "partial",
  "limitations": [str],
  "redaction": {"redacted_field_count": int, "categories": [str]}
}
```

With a single concrete file there is at most one match, so the upstream
`matches` list is flattened into `found`/`value`.

**Identity validation** (refuse `yaml_read_response_identity_invalid` on any
failure):
- `success is True`.
- `files_searched == 1` and `files_unreadable == 0`.
- `count == len(matches)` and `count` is 0 or 1.
- The single match, if present, has `file == "configuration.yaml"` and
  `yaml_path` equal to the request.
- `warnings` is absent or empty. Upstream emits warnings only for
  glob-expanded targets, which Engineering never requests; a warning is a
  shape violation.
- The types are exact. A `content` field, or any unknown field, is a shape
  violation.

**Errors.** Upstream errors map to fixed codes, and no upstream text is
forwarded:

| Upstream condition | Engineering code |
|---|---|
| "Path not allowed…" | `yaml_file_not_allowed_upstream` |
| "File does not exist" | `yaml_file_not_found` |
| `CONFIG_INVALID` parse error | `yaml_parse_error` (line and column only) |
| component missing or unavailable, or token failure | `yaml_component_unavailable` |
| anything else | `yaml_read_failed` |

The "Path not allowed" text is not forwarded because it would disclose the
component's effective allowlist and any configured extra directories.

**Bounds**
- The `value` depth is at most 24.
- At most 4,000 nodes in the value.
- Strings are capped by the gateway `max_string`.
- The whole response is capped by the gateway response limit, with the
  existing `encoded + 8000 > limit` refusal.
- When the node cap is reached, Engineering truncates deterministically
  (breadth-first within the value). It sets `completeness: partial` and adds
  `limitations: ["value_truncated:<n> nodes omitted"]`. Redaction runs
  before truncation, so truncation never exposes an unredacted node.
- If the response is still over the limit, the result is
  `response_too_large` and no partial data is returned.

**Upstream size is not bounded** (F8). The component reads the whole
`configuration.yaml`. Engineering cannot bound that work; it bounds only what
it accepts and returns. With no glob, each request causes exactly one
upstream `read_file` service call (plus upstream's component-availability
checks). Disclosed.

**Anchors, aliases and merges.** They are represented as upstream expands
them (inferred: fully expanded in `parsed`). The caps above bound the
expansion. Nothing anchor-specific is returned.

**Includes.** They are returned as tag strings, for example
`"!include_dir_merge_list templates"`, and never followed. For each tag
string found, Engineering adds
`limitations: ["include_not_followed:<yaml_path>"]` (up to 16, then
`include_not_followed_truncated`), and the result is `partial`. Example: a
caller asking for `sensor` when `sensor: !include sensors.yaml` gets the tag,
not the sensors. Also, `sensors.yaml` is **not readable** through the
component allowlist (F6), and Engineering does not read it either.

## 5. Admission

### 5.1 Why not a stock policy entry (verified)

**Invariants that block an ordinary policy entry**
- `len(entries) != stock_tool_count` raises
  `policy_stock_catalog_count_invalid` (`E/upstream_tool_policy.py:1371`).
  The 8.5.0 policy is 77 entries; the flag-on catalog is 80.
- The registry pins `policy_sha256`, and requires
  `reviewed_stock_catalog_tool_count == advertised_tool_count`.
- `EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS` (`:71-76`) covers policy-named tools
  in observed order (`providers/upstream_read_gateway.py:2375-2420`):
  - A policy entry absent with the flag off changes nothing in the order.
  - A policy entry present with the flag on changes the fingerprint.
  - A mismatch raises `catalog_order_mismatch`, which quarantines **every**
    delegated read.
- `tool_contracts` and the description, annotation and output-schema
  fingerprint maps must equal the read-route set.

**Effect on signed entries**
- Signed ha-mcp entries bind `(policy_resource, policy_sha256)`
  (`E/ha_mcp_readmission/ha_mcp.py:465`).
- Any policy edit moves the digest. That forces new
  `_LEGACY_SIGNED_POLICY_BINDINGS` pairs (`:56-61`) with frozen copies for
  8.4.3 **and** 8.5.0.
- It also forces a generalization of `_legacy_release`, which today refuses a
  legacy copy that contains platform adapters (`:520-526`), so it cannot
  accept 8.5.0's.

**Whole-catalog validator.** `validate_reviewed_release_catalog` (`:926`)
still refuses the 80-tool catalog. It gates backup (`providers/operational_backup.py:326`),
lifecycle (`operational_lifecycle.py:808`) and release evidence. Both
surfaces are `held` for 8.4.3 and 8.5.0, so no capability is lost today.

### 5.2 Proposed mechanism: optional reviewed tools

A new adapter module, `providers/upstream_yaml_reader.py`, owns the compiled
optional-tool authority. It has three parts.

**`OPTIONAL_TOOL_BINDINGS`** (R4): per (upstream version, upstream name), the
exact reviewed contract from the D1 capture. Each binding has these fields:
- `binding_model`: `"hamcp162-optional-tool-binding-v1"`.
- `runtime_contract_fingerprint_model`: the release's declared model. For
  8.4.3 and 8.5.0 this is `ha-mcp-operational-tool-descriptor-v2`
  (`upstream_release_registry.json`). A binding whose model differs from
  its release's model is a load error.
- `runtime_contract_fingerprint`: `runtime_contract_fingerprint(descriptor,
  model=…)` (`E/upstream_tool_policy.py:203-224`) over the **complete**
  runtime descriptor exactly as observed in `tools/list`. That means every
  top-level field: `name`, `title`, `description`, `inputSchema`,
  `outputSchema`, `annotations`, `_meta`, `execution`, `icons`, and any other
  field present.
  - The only projection is the one model v2 already applies:
    `_meta.ha_mcp.policy` is reduced to the validity of the exact reviewed
    dynamic policy-state shape (`runtime_policy_state_fingerprint_projection`,
    :228-246). Nothing else is excluded.
  - If the D1 capture shows any other field varying between two captures of
    the same version, that field is brought back to review as a model change.
    It is never silently excluded.
- `top_level_fields`: the exact sorted set of top-level keys in the captured
  descriptor, with presence and absence both significant. This duplicates
  part of the full fingerprint, but gives a precise reason code.
- `diagnostic_fingerprints`: per-component hashes (input schema, output
  schema, description, annotations, title, `_meta`, `execution`, and the
  existing `RUNTIME_CONTRACT_DIAGNOSTIC_PATHS`, :116-126). These are for
  reason codes and `server_info` diagnostics only. **They never decide
  admission.**
- `source_commit`, the capture resource path and its SHA-256, so the binding
  is tied to committed provenance.
- `classification`: `ha_config_get_yaml` → `mixed_or_requires_wrapper` with
  adapter token `ha-mcp-<v>-yaml-fragment-read-v1`; `ha_list_files` and
  `ha_read_file` → `prohibited`.

**Admission comparison (R4).** An observed optional descriptor matches its
binding only when **both** hold:
- its complete runtime-contract fingerprint, computed under the binding's
  model, equals `runtime_contract_fingerprint`;
- its top-level key set equals `top_level_fields`.

Any other outcome is drift, with a reason code that names the first
differing component when the diagnostics identify one, otherwise
`optional_descriptor_contract_changed`. That includes an added, removed or
changed `_meta`, `execution` or `icons` field, or any field not named here.

**`OPTIONAL_TOOL_DISPOSITIONS`**: an explicit disposition for **every**
version in `upstream_release_registry.json`:
- `admitted` for 8.4.3 and 8.5.0;
- `held` for every other version, including 8.6.0 until its HOLD lifts
  (D7).

A loader assertion requires the key set to equal the registry version set
exactly. A missing version is a load error, never a default. This is the
per-tool form of HAMCP-184's fail-closed rule: no default disposition
exists, and a disposition is never inferred from the release's provider
dispositions.

**`OptionalCapabilityAuthority`** (R5): the compiled authority object built
from the three tables above for one release. It holds:
- the release version and source commit;
- the binding;
- the disposition;
- a `CapabilityContract` for `ha_config_get_yaml`, described next.

**The optional capability contract.**
- `capability_id = "ha_config_get_yaml"`, `kind = ORDINARY_READ`.
- `contract_fingerprint = evidence_fingerprint({model:
  "hamcp162-optional-tool-binding-v1", tool_name, classification,
  adapter_token, argument_constraints_id, runtime_contract_fingerprint,
  runtime_contract_fingerprint_model, top_level_fields, version,
  source_commit, disposition})`.
- `observation_for_catalog` computes the same fingerprint from the observed
  descriptor (`observation_for_catalog`, `E/ha_mcp_readmission/ha_mcp.py:542-633`,
  with `_observed_contract_fingerprint` at :696). Today an observed filesystem tool is undeclared and
  becomes a `capability_not_compiled` quarantine decision that does not
  affect the aggregate (`coordinator.py:755-773`).

**Admission rule.** The YAML reader route is admitted only when all of these
hold:
1. the selection's authority source is `compiled_exact` (see *Signed
   selection* below);
2. the release's `read_gateway` provider disposition is explicitly `admitted`
   (HAMCP-184 semantics: absent means held);
3. `OPTIONAL_TOOL_DISPOSITIONS[version] == "admitted"`;
4. a binding exists for (version, `ha_config_get_yaml`), and its source
   commit equals the release's source commit;
5. the observed descriptor appears exactly once and matches the binding
   under the R4 comparison above;
6. server identity, release, protocol, revocation and HOLD checks pass as for
   every delegated read;
7. the published coordinator generation holds an admitted decision for the
   optional capability, and the dispatch lease protocol below succeeds;
8. the Core gate passes (§5.4).

**Admission generation and dispatch lease (R5).** The route uses the
**existing** coordinator generation and single-use lease path. No bypass
exists, and none is added.

1. **Selection.** `HaMcpAuthoritySelector.select` keeps returning the
   unchanged stock profile (`_profile_for_release`, `ha_mcp.py:635-670`;
   byte-identical, same `profile_id`). For a `compiled_exact` selection whose
   version meets admission rules 2-4, the selection also carries an
   **optional extension profile**:
   - a second `CapabilityProfile` on the `HA_MCP` surface;
   - `profile_id = "ha-mcp-optional-yaml-<version>-<binding fp16>"`;
   - `adapter_id` = the adapter token;
   - `capabilities = (optional contract,)`.

   Otherwise the selection carries no extension, and records a withheld
   reason (`optional_disposition_held`, `optional_binding_absent`,
   `optional_source_commit_mismatch`, `optional_signed_selection`).
2. **Authority binding.** The selection's authority material and
   `authority_token` (`ha_mcp.py:201-207`) include the extension's
   identity, or its explicit absence. Attaching, detaching or changing the
   extension therefore changes the authority fingerprint. The gateway's
   existing live revalidation, which compares the live generation's
   authority fingerprint with the live selection's
   (`upstream_read_gateway.py:2894-2928`), detects it. The stock profile id
   is unchanged, so no existing signature is reinterpreted.
3. **Coordinator profiles.** `replace_surface_profile` (`coordinator.py:147`)
   and the gateway's coordinator construction
   (`upstream_read_gateway.py:1066-1077`) today install exactly one profile
   per surface.
   It is generalized to install the stock profile plus at most one optional
   extension for the surface, as one atomic binary configuration event. Any
   change retires that surface's outstanding leases, which is the existing
   replacement semantics. The constructor's duplicate-capability check
   (`coordinator.py:84-89`) still applies, so the extension can never shadow
   a stock capability.
4. **Aggregate isolation.** `_evaluate` (`coordinator.py:723-802`) derives
   the surface's aggregate disposition from all declared capabilities. If
   the optional capability counted, its drift would turn the stock
   aggregate from `admitted_exact` into `partial`. The optional contract is
   therefore marked `optional=True` in the model and is **excluded from the
   aggregate**, while still receiving its own per-capability decision in the
   same published generation. An optional decision that is not admitted
   never changes the aggregate or any stock decision.
5. **Observation and reconciliation.** The optional capability is
   reconciled in the same `begin_reconciliation`/`complete_reconciliation`
   pass as the stock reads. It is published in the same `DecisionGeneration`
   and bound to the same session fingerprint, observation fingerprint and
   authority fingerprint. There is no separate generation counter and no
   out-of-band admission.
6. **Request path.** The YAML wrapper dispatches only through the gateway's
   delegated-read path, in this order:
   1. argument validation (L1-L3; refusals here make zero calls);
   2. the admitted generation decision for `ha_config_get_yaml`;
   3. same-session live revalidation: generation, observation fingerprint,
      authority fingerprint and decision admitted (`:2894-2928`);
   4. `acquire_route("ha_config_get_yaml", session_id=…)` (`coordinator.py:452-500`,
      reached from `upstream_read_gateway.py:3034-3048`);
   5. Core authority acquisition (§5.4);
   6. `commit_route` immediately before `tools/call`, at the existing
      dispatch linearization point (`:3065-3099`), which re-runs
      `validate_pre_dispatch` (`coordinator.py:502-528`).
7. **Retirement and quarantine mid-request.** Retirement or quarantine
   between admission and dispatch refuses before dispatch with zero upstream
   calls, because the lease no longer validates. The triggers are:
   - a new generation (catalog refresh, optional drift, extension change);
   - a session change;
   - authority revocation or retirement (`retire_surface_authority`);
   - a disposition change;
   - Core retirement.

   A retired, replayed or foreign-session lease fails `commit_route`. A
   result that arrives after retirement is returned only under the existing
   committed-dispatch rules for stock reads. It never re-admits the route:
   only a new reconciliation can publish a new admitted decision.
8. **Consequence for stock reads, stated precisely.** Optional drift
   quarantines only the optional decision. Like any reconciliation that
   publishes a new generation, it invalidates leases already issued on the
   surface under the previous generation, so an in-flight stock read between
   acquire and commit is refused and must be retried. Stock decisions in the
   new generation remain admitted. This is the existing generation semantics,
   not new behaviour, and it is disclosed.

**Signed selection** (corrected in revision 2). Revision 1 attributed the
signed-selection rule to D8; that attribution was wrong, and the rule is
replaced. The optional extension is attached **only under `compiled_exact`
authority** (`AuthoritySource`, `models.py:81-84`):
- A `signed_registry` selection (`_select_signed_profile`,
  `ha_mcp.py:457-487`), including the legacy signed policy bindings, never
  carries the extension. It reports `optional_signed_selection`.
- `select` prefers a compiled release whenever one exists and its
  `read_gateway` is not held (`ha_mcp.py:281-303`). Both bound versions are
  compiled releases, so a signed selection of 8.4.3 or 8.5.0 occurs only when
  `read_gateway` is held, which admission rule 2 refuses anyway.
- The relationship to the signed release selection is therefore explicit.
  An existing signature authorizes exactly the stock profile it names, and
  never the optional capability. Josh's approval of the compiled optional
  authority (D2) is what authorizes the optional capability, for compiled
  releases only. No new signature is required, and none is reinterpreted.

**What the mechanism leaves unchanged**
- **Not counted** in the stock count, the stock catalog fingerprint, the
  exact order fingerprint input, or the whole-catalog validator. The 77-tool
  and 80-tool order fingerprints are therefore unchanged, and so is every
  existing signed entry.
- **No policy JSON edit.** No digest moves, no legacy pairs are needed, and
  `_legacy_release` stays as it is.
- **Stock profiles are byte-identical**, and every existing profile id is
  pinned by a test.
- **No new provider surface.** Adding one to `PROVIDER_SURFACES`
  (`E/compatibility_family.py:59-62`) would require every registry entry, the
  family descriptor `provider_surface_tools` (`:411`) and every
  `family_admission` record (`:849`) to change. That alternative is listed as
  D2(b).

**Accounting.** `server_info` gains one optional section, `optional_tools`,
with these values per name:
- `admitted`;
- `optional_absent`: the flag is off, which is normal and not quarantine;
- `quarantined:<reason>`;
- `held`;
- `withheld:<reason>` (for example `optional_signed_selection`);
- `prohibited_withheld`.

The existing fields are unchanged in meaning. `reviewed_tool_accounting_valid`
(`providers/upstream_read_gateway.py:1738`) compares the policy against the
catalog; the 80-tool catalog makes it false today. Optional names are
accounted in a new `optional_reviewed_accounting_valid` field, leaving the
stock field's meaning unchanged (D3).

### 5.3 Drift quarantines only this tool

**Drift in `ha_config_get_yaml`.** Any of the following quarantines only the
YAML reader's decision:
- a change in the complete runtime-contract fingerprint;
- a change in the top-level field set;
- a duplicate;
- a source-commit mismatch.

The tool is not exposed (HAMCP-129 §4.4 precedent) and is never exposed with
the upstream schema. All 28 other delegated reads, dashboard writes and
power/fan keep their admitted decisions: none of them name it, the optional
decision is excluded from the aggregate (§5.2 step 4), and HAMCP-175/177
dependency-scoped admission already ignores extras. In-flight leases follow
§5.2 step 8.

**Drift in a `prohibited` optional name** is reported and changes nothing.

**Absence** with the flag off is `optional_absent`. The tool is not exposed,
the catalog stays at 87, and nothing is quarantined.

### 5.4 Core route mapping

**Which Core APIs the upstream tool reaches** in the first release:
- REST `POST /api/services/ha_mcp_tools/read_file` (exactly one call per
  request; `list_files` is never reached, because Engineering never sends a
  glob);
- REST `GET /api/services`;
- WS `ha_mcp_tools/info`.

**Recommended mapping** in `DELEGATED_CORE_REQUIREMENTS`
(`E/ha_core_readmission/routes.py:74`):

```
ha_config_get_yaml → (core.basic_rest_read, core.basic_websocket_read)
```

These are the existing profiles (`ha_core_readmission/profiles.py:146-171`).
This follows the precedent for component-routed tools: the component service
routes are **unattested** by the Core profile, and that is disclosed in the
matrix row and the tool description.

**No signed Core registry change.** The registry stays at sequence 12. A
dedicated `core.ha_mcp_tools_file_read` profile would need owner-signed
sequence 13, and the tool would then ship dormant until it is signed. That is
alternative D6(b).

An unmapped tool silently skips the Core gate, so a test asserts that
`ha_config_get_yaml` is mapped. The `len(DELEGATED_CORE_REQUIREMENTS)`
assertion moves from 30 to 31.

## 6. Catalog

| | Flag off (stock 77) | Flag on, exact (80) |
|---|---|---|
| Public tools | 87 (59 native + 28 delegated), unchanged | **88** (59 native + 29 delegated) |
| `DELEGATED_CORE_REQUIREMENTS` entries | 31 (mapping is static) | 31 |
| Upstream unreviewed count | 0 | 0 if D3 is accepted (otherwise 3, as today) |

**Public tool**
- Name: `ha_config_get_yaml`, the upstream name, as precedent for delegated
  reads (D8: keep it, or rename to `get_yaml_config`).
- Annotations: `readOnlyHint: true`, `openWorldHint: false`,
  `destructiveHint: false`, `idempotentHint: true`.
- The description states:
  - `configuration.yaml` only, in this release;
  - disk, not loaded runtime config;
  - mapping-key paths only; paths naming a credential key are refused;
  - includes are not followed;
  - `!secret`/`!env_var` appear as typed reference markers carrying the
    name only, never values or `!env_var` defaults;
  - redaction is best-effort and deliberately over-matches credential-like
    keys; values under URL, URI, DSN, connection and database keys are
    redacted whole except a plain SQLite path; other strings are redacted
    whole when, after percent-decoding and Unicode normalization, they
    contain userinfo or a credential word such as `pwd`, `token`, `auth` or
    `session` (scheme kept at most), so harmless text mentioning such a
    word or a delimited `pass` or `key` label (such as `key: …`) is also
    redacted; a raw secret under an innocent key, or under a label without
    a credential word (such as `pw=` or `my_key=`), is not recognized;
  - it requires ha-mcp filesystem tools and the HA-MCP custom component.

**`CAPABILITY_PROVIDER_MATRIX` row** (`E/capabilities.py:437`):

| Field | Value |
|---|---|
| capability | `yaml_configuration_read` |
| provider | `ha_mcp_delegated` |
| tool | `ha_config_get_yaml` |
| mode | `reviewed_argument_constrained` |
| fallback | `none` |
| dependencies | `ha_mcp_tools` custom component; `ha_mcp_filesystem_tools_flag` |
| core | `core.basic_rest_read` + `core.basic_websocket_read`, component routes unattested |
| writes | none |

**Count assertions to update.** Only tests that build the 80-tool synthetic
catalog, or that pin the matrix and Core-requirement counts, change. Known
sites:
- `test_ha_core_2026_9_integration.py:2930-2931` (`len(DELEGATED)==30`);
- `test_hamcp175_power_fan_admission.py:56, :359, :467` (EXTRAS);
- `test_hamcp177_dashboard_write_admission.py:54`;
- `test_hamcp129_platform_reads.py:551, :1246, :1381`;
- `test_upstream_release_registry.py:282-284, :930-932`.

Stock-catalog assertions (87) stay. A full grep sweep is part of Part 2, along
with README.md:15/:153 and TOOL_AUDIT.md.

## 7. Use cases and analysis consumers

**What this unlocks** (read-only):
- **Audit F002.** Recorder settings (`yaml_path=recorder`, giving
  `purge_keep_days`, `commit_interval`, `include`, `exclude`, and `db_url`
  shown only in the exact plain SQLite shape, such as the default path;
  any other `db_url` is redacted whole to its scheme, for example
  `mysql+pymysql://[REDACTED:connection_uri]`, or shown as a
  `{"secret_ref": …}` marker). The backend type remains visible through
  the scheme. Database-size and free-space
  sensor definitions, when they are inline in `configuration.yaml`.
- **YAML-defined entities.** The audit's YAML-defined entity origin can be
  confirmed by key, for example `template`, `sensor`, `mqtt`, `command_line`
  or `rest`.

**Gaps that remain** (disclosed):
- `!include` targets (for example `sensors.yaml`, or `templates/` via
  `!include_dir_merge_list`) are not followed; most are unreadable upstream
  as well (F6).
- `packages/` is not readable in this release (D4/D5; production does not
  use packages). A later release may add explicit named package files (§12).
- List elements cannot be addressed directly.
- Credential-shaped keys cannot be selected directly; read the parent.
- Disk is not the loaded config.

**Analysis consumers.**
`entity_dependency_analysis` (docs/ENTITY_DEPENDENCY_ANALYSIS.md:34, :52-57,
:306-310) and `configuration_integrity_analysis`
(docs/CONFIGURATION_INTEGRITY_ANALYSIS.md:67, :183) declare static YAML and
packages "outside coverage until a later reviewed provider supplies it".

**Recommendation: consume later**, in a separate scoped task, after this tool
has been accepted live. Wiring it in now would:
1. change two analysis output contracts and their completeness semantics;
2. multiply upstream reads per analysis run (each one a whole-file read
   upstream);
3. mix a new, best-effort-redacted source into evidence that is currently
   fully structured.

The tool itself lets a client gather the evidence manually in the meantime.

## 8. Tests, mutations, files, size, PR split and release

### 8.1 Test groups (`tests/test_hamcp162_yaml_reader.py`)

These tests use real MCP SDK sessions against a synthetic ha-mcp peer
replaying the D1-captured descriptors, at 8.4.3 and 8.5.0. **Secret and
redaction tests assert on the final wrapper output** (tool result, error,
warning, `limitations` and the audit record), not only on the shared
sanitizer. They use synthetic credentials that are unique per test, and they
search every output channel for each one.

1. **Useful success:**
   - `recorder` from `configuration.yaml`, with credential-free settings
     visible;
   - `homeassistant.customize` and a YAML-defined `template` key;
   - a missing key gives `found: false`;
   - exactly one upstream `tools/call` per request, with the exact
     Engineering-built arguments (`file` is the constant), and exactly one
     upstream component `read_file` and zero `list_files` in the synthetic
     transport.
2. **Pre-dispatch refusal, with zero upstream calls asserted** for each case:
   - every `file` other than the byte-exact `configuration.yaml` (L1):
     `configuration.yml`, `Configuration.yaml`, `CONFIGURATION.YAML`,
     `./configuration.yaml`, `/config/configuration.yaml`, a trailing space,
     `packages/x.yaml`, `packages/*.yaml`, `*.yaml`, `secrets.yaml`,
     `Secrets.YAML`, `PACKAGES/../secrets.yaml`, `.storage/core.config`,
     `scripts.yaml`, NUL, backslash, `%2e%2e/secrets.yaml`, `//`, `~`, a URL
     and an over-length string;
   - **R3 scope removal:** for every one of those paths the synthetic
     transport records zero calls of any kind (no `tools/call`, no
     component `read_file`, no `list_files`);
   - the L1/L2 relationship tests of §2.2;
   - any extra property;
   - every `yaml_path` pattern violation;
   - **L3 (R1):** leaf and ancestor selections such as `mqtt.password`,
     `mqtt.credentials`, `mqtt.credentials.username`, `http.api_key`,
     `influxdb.token`, `x.pin`, `x.code`, `x.auth.password`, plus
     case/separator variants (`mqtt.Password`, `mqtt.api-key`) and, since
     revision 5, any segment whose compact form contains a credential word
     (`homeassistant.auth_providers`, `mqtt.MQTT-PassWord`). Each is
     refused with `yaml_path_sensitive`, and audit records only the reason
     code and length.
3. **R1 path-context redaction** (final wrapper):
   - parent versus leaf for `password`, `api_key`, `token`, `pin` (numeric)
     and nested `credentials`: the parent returns the marker in place of the
     credential and keeps harmless siblings (`broker`, `port`, `username`
     when not under a sensitive ancestor);
   - a list of mappings beneath a sensitive ancestor (`credentials: [{…}]`)
     and a list beneath a harmless key holding sensitive children (each
     element's sensitive child redacted, harmless children kept);
   - a mapping beneath a sensitive ancestor whose child names are innocuous
     (`credentials: {value: …, id: …}`), withheld whole;
   - integer, float, boolean and null values under sensitive keys;
   - reference behaviour is tested in group 3a (final pipeline);
   - **L3 disabled by mutation:** direct `mqtt.password` and
     `mqtt.credentials` selections return only the marker as `value` (L4
     alone is correct);
   - no synthetic credential appears in the response, an error, a warning,
     `limitations` or the audit record.
3a. **R8 reference markers** (final pipeline, L4 then real L5 with the
    opt-in, then rendering; never L4 alone):
    - `password: !secret mqtt_password` gives `{"secret_ref":
      "mqtt_password"}`, and so does `api_key: !secret k`. A non-sensitive
      key (`broker: !secret mqtt_host`) gives a marker too;
    - `token: !env_var TOK` gives `{"env_var_ref": "TOK"}`;
    - `password: !env_var MQTT_PW <synthetic default>` gives
      `{"env_var_ref": "MQTT_PW", "default_redacted": true}`. The default,
      including a multi-word or multi-line one, appears in no channel;
    - an ordinary sensitive string (`password: <synthetic>`) gives the
      credential marker;
    - malformed tags (`!secret` alone, `!secret two words`, `!SECRET x`, a
      leading space, a 129-character name, `!env_var 1BAD d`, and a name
      that is a JWT): the credential marker under a sensitive key and
      `[REDACTED:reference_malformed]` elsewhere. No marker is minted;
    - a sensitive container holding a reference (`credentials: {password:
      !secret x}`) is withheld whole;
    - forgery: upstream mappings `{env_var_ref: <synthetic>}`, `{secret_ref:
      <synthetic>}` and `{env_var_ref: x, default_redacted: true}` give
      `[REDACTED:reference_marker_collision]` (or the credential marker
      where the key is sensitive). The synthetic value appears nowhere;
    - L5 directly: with the opt-in on, a plain string `"!secret x"` under
      `password` is replaced by the key marker. A `ReferenceMarker`
      subclass instance and a marker whose name was altered to fail the
      grammar are not preserved. With the opt-in off, a `ReferenceMarker`
      becomes `[REDACTED:reference_marker]`, and the name appears nowhere;
    - **other tools unchanged:** the existing sanitizer corpus gives
      byte-identical output with the opt-in on and off, and every existing
      sanitizer test passes unmodified;
    - the quoted-literal residual (`password: "!secret abc"` rendered by
      upstream exactly like a tag) is pinned as shown as a marker, which
      documents the disclosed behaviour.
4. **R2 connection and credential text, decode then deny by substring**
   (revision 5; final wrapper, real L5; exact expected outputs). Every case
   runs at an ordinary path (`sensor.value`) **and** at a connection path
   (the `recorder` mapping and a direct `recorder.db_url` selection),
   unless stated otherwise. `S` is a synthetic credential with no
   credential word, and `U` is the URL userinfo `u` + `:` + `S`. "Encoded n times" means `urllib.parse.quote(value,
   safe="")` applied n times. **Every assertion of the revision-4 delta
   review's probe set (`pr260-3391172/probes.py`, 438 assertions) is a
   required test**, with the 13 expectations that revision 5 deliberately
   flips (§15) asserted in their revision-5 form.
   - **The second delta review's 12 boundary cases, verbatim, including its
     8 counterexamples.** `Server=db;Uid=u;Pwd=S` and `mysql://U@db/ha`,
     each encoded 1–6 times:
     - DSN encoded 1–4 times gives `[REDACTED:connection_string]`;
     - URI encoded 1–4 times gives `mysql://[REDACTED:connection_uri]`;
     - either encoded 5 or 6 times gives `[REDACTED:encoding]`.

     The outputs are identical at both paths.
   - **The revision-4 counterexample (R2, C′2):** `Server=db;Uid=u;Pwd==S`
     encoded 0–4 times gives `[REDACTED:connection_string]`, and 5–6 times
     `[REDACTED:encoding]`. `mysql://db/ha?pwd==S` gives
     `mysql://[REDACTED:connection_uri]`.
   - **Pwd variants, depths 0–6** (C′2): `Pwd==S`, `PWD :=S`, `;pwd;S`,
     `P%57D=S`, fullwidth `ｐｗｄ=S`, `pwd=S`, `?x=PWD%3DS%3Bz`, the
     double-encoded name `?%2550WD=S`, and a JSON payload `{"pwd": "S"}`.
     Each gives `[REDACTED:connection_string]` (or the scheme form where the
     text starts with a scheme) while its total decoding depth is at most
     four, and `[REDACTED:encoding]` beyond.
   - **Delimited token (C′3), depths 0–6, at both paths:** `pass=S`,
     `PASS ==S`, `key: S`, `;key=S`, `key==S`, `pass:=S`, `key :=S`,
     `Key=S`, `x=1;pass=S`, `a&key=S`, a tab and a no-break space before
     the delimiter, fullwidth `ｋｅｙ＝S` and `ｐａｓｓ：S`, `pass%3DS`,
     `%6Bey=S` and `k%45Y: S`. Each gives `[REDACTED:connection_string]`
     while its total decoding depth is at most four, and
     `[REDACTED:encoding]` beyond. Query forms
     (`https://api.example.com/v1?key=S`, `mysql://db/ha?pass=S`,
     `…&KEY=S`, encoded 0–4 times) give the scheme form. K1: `{"key=S":
     1}` and a twice-encoded `pass: S` key withhold their mapping.
     **Non-catches stay visible (raw)** at two ordinary paths: `keypad`,
     `monkey`, `bypass`, `compass`, `key fob`, `monkey=1`, `keypad: 4`,
     `bypass=on`, `compass: north`, `hotkey=F5`, `passkey=1`, `turnkey:
     yes`, `Kitchen keypad`, `Hall pass`, `pass the salt`, `key lime pie`,
     `keys=3` and `passes: 2`.
   - **Fully encoded URIs, depths 0–6:**
     - userinfo in `postgresql+psycopg2://`, `mssql+pyodbc://`,
       `redis://:S@…` and `https://U@…`, user-only `mysql://u@db/ha`, and
       a fullwidth `＠` (NFKC gives `@`);
     - a credential query item (`mysql://db/ha?password=S`);
     - also at `rest.resource`.

     Each gives `<scheme>://[REDACTED:connection_uri]` at depths 0–4 and
     `[REDACTED:encoding]` at 5–6.
   - **Standalone connection strings, depths 0–6:** ODBC
     `DRIVER={X};SERVER=db;UID=u;PWD=S`, ADO `Data Source=db;User
     ID=u;Password=S`, libpq `host=db dbname=ha user=u password=S`, and the
     revision-2 `odbc_connect` probe verbatim. Each is redacted whole at
     depths 0–4 and gives `[REDACTED:encoding]` at 5–6.
   - **Canonical keys (R10)**, each at depths 0–6 of key encoding:
     - `p%61ssword`, `%70assword`, `PASSWORD`, fullwidth `ｐａｓｓｗｏｒｄ`,
       and `MQTT-PassWord` under `mqtt` (with a visible `broker` sibling).
       Each is an identifier key, shown raw with its value replaced by a
       credential marker, and `broker` stays visible; `Pass Word` is not an
       identifier and K1 withholds the `mqtt` mapping; a key encoded past
       the bound withholds its mapping as `[REDACTED:encoding]`;
     - encoded ancestors `%63redentials`, `p%61ssword`, `api%5Fkey`,
       `%74oken`, `session%5Fid` and `%41uth` holding `{value: S}` or a list
       of mappings: withheld whole;
     - the encoded connection key `db%5Furl` and the NFKC key `db_urℓ`:
       rule B applies;
     - **K1:** a key whose canonical text holds userinfo or a credential
       word and is not a plain identifier (`pwd=S`, `Pwd=S` encoded twice,
       `mysql://U@db`, `token: S`) withholds its mapping as `[REDACTED:connection_string]`;
     - **L3:** a `yaml_path` segment whose compact canonical form contains a
       credential word (`homeassistant.auth_providers`,
       `x.session_timeout`, `mqtt.MQTT-PassWord`) is refused with zero
       upstream calls.
   - **Non-converging, ambiguous and invalid** (A), each giving
     `[REDACTED:encoding]`: a benign `Living Room` encoded 5 and 6 times;
     mixed escapes `%41%`, `%G1%41` and `%41 50% off`; invalid UTF-8 `%FF`,
     overlong `%C0%AE`, and an added control character `a%00b`; an escape
     introduced by NFKC (fullwidth `％７０wd=S`).
   - **R9 bound discriminators** (exact outputs): `Living Room` encoded
     **4** times is visible as its raw string; encoded **5** times it gives
     `[REDACTED:encoding]`; `Pwd=S` encoded **4** times gives
     `[REDACTED:connection_string]`, not `encoding`. Strings with no valid
     escape and no credential word (`50% off`, `100%`, `%G1`, a
     `{{ states('sensor.x') }}` template) stay visible.
   - **Each key-scoped name variant** (B), at `x.<key>`: `db_url`,
     `DB_URL`, `dbUrl`, `database_url`, `url`, `base_url`, `external_url`,
     `uri`, `redirect_uri`, `dsn`, `DSN`, `connection`,
     `Connection_String`, `conn_str`, `connect`, `connect_args`,
     `database`, `Database`, `odbc_connect`, `sqlalchemy_url`, `Db-Url`,
     `db%5Furl`, `db_urℓ`, and the disclosed over-matches
     `reconnect_interval` and `security`. The credential-free
     `mysql://db.local:3306/ha?charset=utf8mb4` gives
     `mysql://[REDACTED:connection_uri]`; a plain string and `42` give
     `[REDACTED:connection_string]`; a container under `influxdb.database`
     is withheld whole.
   - **R11 safe SQLite versus variants:**
     - visible: `sqlite:////config/home-assistant_v2.db`,
       `sqlite:///home-assistant_v2.db` and `sqlite:///a..b.db`;
     - redacted to `sqlite://[REDACTED:connection_uri]`: a trailing `\n`,
       an embedded `\n`, `\r`, `\t`, `\x00`, `\x7f`, ` ` and
       ` `; `sqlite:///../x.db`, `sqlite:////config/../x.db` and a
       trailing `/..`; `%2D` and any literal `%`; `SQLITE:///x.db` and
       `X.db`; fullwidth characters; `?mode=ro`, `@`, `;`, `=`, a space
       and `sqlite:///:memory:`;
     - redacted by C′ first: `sqlite:////config/session.db`;
     - `db_url: !secret recorder_db_url` gives the reference marker.
   - **Useful values stay visible (raw)** at a non-connection key, with no
     credential word: `mysql://db.local:3306/ha?charset=utf8mb4`,
     `postgresql://db.local/ha?sslmode=require`,
     `mysql://[fd00::5]:3306/ha`, `unknown://host/path`,
     `https://api.example.com/v1?entity=sensor.x`, a templated REST URL,
     `Server=db;Database=ha;Trusted_Connection=yes`, `Living Room`, `50%
     off`, and recorder settings. The same `unknown://host/path` at
     `recorder.db_url` gives `unknown://[REDACTED:connection_uri]`.
   - **Disclosed over-redaction, pinned with exact outputs:** "Refresh the
     access token daily", "Author: …", "Session started", "private room",
     "cookie jar", "secretary", `https://example.com/oauth/authorize`
     (scheme kept), `{% if token == 1 %}`, `token://host/x` (no scheme
     kept), an email after `://`, and the keys `auth_providers` and
     `private_ble_device` (value withheld); for C′3, `Hall pass: yes`,
     `Press key: Enter`, `{% set key = 1 %}`, `Spare key=garage`,
     `key://host/x` (scheme kept) and `http://key:8080/x` (scheme kept).
   - **Pinned residuals (D12)**, asserted as **not** redacted so that a
     change is noticed: `login=S`, `pw=S`, `psk=S`, `p_w_d=S`, a Cyrillic
     `Pсd=S` and `kеy=S`, base64 shorter than the blob scanner, and the
     undelimited C′3 forms `my_key=S`, `app_key=S`, `passkey=S`,
     `pass_word=S`, `"key": "S"`, `{'pass': 'S'}` and `key→S`. (`pass=S`
     and `key=S` moved from this list to the C′3 catches.)
   - **Raw emitted, never canonical:** every visible encoded control (for
     example `Living Room` encoded 4 times) is byte-identical to the input,
     and visible keys keep their raw text.
   - **Every channel is searched** for the synthetic credential in raw
     form and in its single-, double- and triple-percent-encoded forms:
     the response, the error, the partial/truncation case (credential URI
     both inside and beyond the node cap), warnings, `limitations` and the
     audit record.
5. **Other secrets in output:** inline suffix keys at depth inside lists; an
   `Authorization` header; a JWT; a PEM private key and a certificate inside
   a string; a long base64 blob; `!secret name` rendered as a
   `{"secret_ref": name}` marker only; no `content` field ever, even if the
   peer sends one.
6. **Identity and shape:** a mismatched `file` or `yaml_path`; `count` above
   1 or inconsistent; `files_searched` other than 1; non-empty upstream
   `warnings`; extra fields; wrong types. Each is refused.
7. **Errors:** each upstream error maps to its fixed code. Upstream text,
   including the allowlist listing, never appears in output or audit.
8. **Bounds:** a 4,001-node value, depth 25, an alias-expansion blowup,
   `response_too_large`, and deterministic truncation with `partial`, with
   redaction proven to run before truncation.
9. **Includes:** a tag string is returned and `include_not_followed` is set.
10. **Admission and descriptor binding (R4):**
    - optional-absent with the stock 77 catalog (87 public tools, nothing
      quarantined);
    - the exact 80 catalog gives 88;
    - mutate, add or remove **every** top-level descriptor field (`name`,
      `title`, `description`, `inputSchema`, `outputSchema`, `annotations`,
      `_meta`, `execution`, `icons`, and an unknown new field), plus nested
      changes in `_meta.fastmcp`, `_meta.ha_mcp` (other than the projected
      policy state) and `execution.taskSupport`. Each unreviewed mutation
      quarantines only the YAML reader with zero upstream calls, and leaves
      stock reads unchanged;
    - a model-v2 policy-state change within the reviewed shape does not
      quarantine, and an out-of-shape one does (same projection as stock);
    - a duplicate and a source-commit mismatch each quarantine only this
      tool;
    - a `held` disposition, an unlisted registry version (load error), a
      binding with the wrong fingerprint model (load error) and
      `read_gateway` held each refuse;
    - a revoked release refuses;
    - both captures are pinned with provenance (resource SHA-256 and source
      commit), and a repeated capture of the same version reproduces the
      fingerprint;
    - `ha_list_files` and `ha_read_file` are never exposed or dispatched in
      any state.
11. **Generation and lease (R5):**
    - useful admitted reads for each supported compiled selection;
    - absent extension, held, unknown version and signed selection each
      refuse, with the withheld reason reported;
    - zero dispatch after each of these between admission and dispatch:
      session change, catalog refresh, optional drift, authority revocation
      or retirement, disposition change, extension change, Core retirement;
    - retired, replayed and foreign-session leases fail `commit_route`;
    - a result arriving after retirement does not re-admit or re-expose the
      route;
    - the stock aggregate disposition stays `admitted_exact` while the
      optional decision is quarantined;
    - an in-flight stock lease across an optional-drift generation change is
      refused (existing semantics), and the next stock read succeeds.
12. **Invariants unchanged:**
    - stock count;
    - the 77- and 80-tool order fingerprints;
    - every policy and registry digest;
    - every stock profile id and byte-identical stock profiles;
    - legacy binding behaviour;
    - the whole-catalog validator still refuses 80;
    - power/fan and dashboard admission are identical with the reader admitted
      or quarantined.
13. **Core:** the mapping exists; Core retirement refuses with zero upstream
    calls; both supported Cores pass.
14. **Audit:** attribution, fixed reason codes, accepted `yaml_path` and the
    constant file only; refused arguments recorded as reason code and length.

### 8.2 Mutation checks

Each of the following must fail at least one test:
- dropping L1 (the `scripts.yaml` case proves L2 does not cover it);
- dropping L2 against an L1 deliberately widened in the test fixture;
- forwarding the caller's `file` string instead of the constant;
- accepting `configuration.yml` or any case variant;
- dropping L3;
- computing L4 key context from the returned node only (without the
  requested prefix);
- walking into, rather than replacing, a container under a sensitive key;
- **rule A mutations** (R9, extended in revision 5), each with the case
  that kills it:
  - decoding only once: the DSN and URI encoded 2–4 times leak;
  - a bound of 3: the 4-times-encoded benign control is redacted, and the
    4-times-encoded `Pwd=S` gives `encoding` instead of the exact
    `connection_string`;
  - a bound of 5: the 5-times-encoded benign control becomes visible;
  - no final escape check: the DSN encoded 5 or 6 times, and the fullwidth
    `％７０wd=S`, leak;
  - dropping NFKC: fullwidth `ｐｗｄ=S` and the key `ｐａｓｓｗｏｒｄ` leak;
  - dropping casefold: `PWD :=S` and `P%57D=S` leak;
  - judging the raw form instead of the canonical text: the encoded URI
    and DSN leak;
  - emitting the canonical text instead of the raw string: the visible
    encoded controls change;
- **rule B mutations:**
  - dropping B, or limiting it to exact key names: credential-free MySQL
    under `recorder.db_url` or `dbUrl` becomes visible;
  - matching raw instead of canonical key names: `db%5Fur%6C` and
    `db_urℓ` escape scope;
  - deriving the key context from the envelope key instead of the
    absolute path: the direct `recorder.db_url` case fails;
  - walking into, rather than replacing, a container under a
    connection-shaped key;
- **R11 mutations:**
  - `re.match` with `$` instead of `re.fullmatch`: `sqlite:///x.db\n`
    becomes visible;
  - dropping the `..` segment check: `sqlite:///../x.db` becomes visible;
  - dropping raw-equals-canonical: `home%2Dassistant_v2.db` and
    `SQLITE:///x.db` become visible;
- **rule C′ mutations:**
  - dropping C′1: `mysql://U@db/ha` leaks;
  - dropping C′2 or any one of the 13 words: the case for that word leaks;
  - restoring revision 4's assignment parse (its `==` exemption):
    `Pwd==S` leaks;
  - redacting only the matching part instead of the whole value;
  - keeping a scheme that holds a credential word: `token://host/x` keeps
    `token://`;
  - **dropping C′3**: `pass=S`, `key: S` and `{"key=S": 1}` leak, and every
    C′3 catch at the ordinary path (depths 0–4) fails its exact output;
  - C′3 without `\b`: `monkey=1` and `bypass=on` are redacted;
  - C′3 as a plain substring (`pass`/`key` added to the C′2 list):
    `keypad`, `compass` and `key fob` are redacted;
  - NFKC or casefold removed: fullwidth `ｋｅｙ＝S` and `PASS ==S` leak;
- **R10 mutations:**
  - classifying raw instead of canonical keys: `%70assword` and
    `%63redentials` leak;
  - dropping K1: `{"pwd=S": 1}` shows `S` in a key;
- exempting references at L5 by JSON shape or string content instead of
  `type(value) is ReferenceMarker`;
- minting a marker for a malformed reference, or skipping the collision
  check;
- passing the L5 opt-in from any other tool, or changing default-off
  output;
- making key matching case- or separator-sensitive;
- forwarding `include_content: true`;
- passing upstream error text;
- removing PEM detection;
- keeping `!env_var` defaults, or keeping them inside the marker;
- admitting on listed component hashes without the complete runtime-contract
  fingerprint, or ignoring the top-level field set;
- counting optional tools in the order fingerprint;
- counting the optional decision in the aggregate disposition;
- a default disposition for an unlisted version;
- exposing on `read_gateway: held`;
- attaching the extension under a signed selection;
- omitting the extension from the authority fingerprint;
- dispatching without `acquire_route`/`commit_route`;
- attaching the route inside stock profile derivation (changes the stock
  profile id);
- removing the Core mapping;
- skipping identity validation;
- returning partial data on `response_too_large`.

### 8.3 Files and size (estimate)

| File | Kind | Lines (est.) |
|---|---|---|
| `E/providers/upstream_yaml_reader.py` (new) | runtime: schema, L1-L3, args, identity, projection, path-context redaction with canonical keys (R10, K1), connection and credential-text rules A, B, C′ and R11, reference minting and rendering, bounds, bindings, dispositions, optional authority | 560-680 |
| `E/sanitization.py` | runtime: PEM and opaque-blob scanners (shared, opt-in parameter); the `ReferenceMarker` type, the default-off `preserve_reference_markers` exemption and the default-off marker branch (R8); the connection-string rules and reference minting stay in the reader module | 60-90 |
| `E/providers/upstream_read_gateway.py` | runtime: optional-entry evaluation, extension-aware coordinator installation, routing through the existing lease path, `optional_tools` reporting | 180-250 |
| `E/upstream_tool_policy.py` | runtime: optional binding load, model check, version-set assertion | 50-80 |
| `E/ha_mcp_readmission/ha_mcp.py` | runtime: extension on `compiled_exact` selection, authority material, observation of the optional contract | 60-100 |
| `E/ha_mcp_readmission/coordinator.py`, `models.py` | runtime: stock-plus-extension surface profiles, `optional` flag, aggregate exclusion | 60-100 |
| `E/ha_core_readmission/routes.py`, `E/capabilities.py`, tool registration | runtime | 40-60 |
| **Runtime total** | | **~1,010-1,360** |
| `tests/test_hamcp162_yaml_reader.py` + fixtures generator | tests | 1,500-1,900 |
| Coordinator and selector regression tests (extension, aggregate isolation, leases) | tests | 250-350 |
| Count-assertion updates (§6) | tests and scripts | ~60 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-<v>-filesystem.json` (D1 capture, with provenance) | evidence | generated |
| Release notes, acceptance, README, TOOL_AUDIT, matrix docs | docs | ~300 |

Runtime is below about 1,500 lines, so **one PR** (D9, accepted). Removing
packages and the glob reduced the path-validation and fan-out work, while
R1, R2, R4 and R5 added redaction, binding and coordinator work. If the
coordinator or redaction work grows past the threshold, split as follows:
- **PR-A:** the optional reviewed tool mechanism (bindings, dispositions,
  extension profile, aggregate isolation, lease path), plus classification
  of all three names (nothing exposed; reports only);
- **PR-B:** the wrapper and its exposure.

**Protected paths Part 2 needs** (D10):
- `E/` runtime files listed above, now including
  `E/ha_mcp_readmission/coordinator.py` and `models.py`;
- `hass_mcp_engineering_beta/config.yaml`;
- release metadata through `scripts/promote_next_release.py --apply`.

No policy JSON, registry JSON, signed registry, workflow, `.release/`
declaration edit beyond the staged release, or stable-v1 change is needed.

### 8.4 Release

The release is the next beta after HAMCP-184. If 184 ships as
`2.4.1-beta.11`, this is **`2.4.1-beta.12`**, materialized in the PR before
Ready. The number is revalidated when implementation starts. The
acceptance contract follows the beta.8 shape:
1. source and CI;
2. publication;
3. installed acceptance. This step:
   - keeps the stock 77-tool check;
   - has Josh enable the flag (already on in production);
   - verifies 88 tools;
   - runs one read canary (`recorder`), confirming credential-free settings
     are visible and any `db_url` credential is redacted;
   - runs refusal canaries (`file=secrets.yaml`, `file=packages/x.yaml` and
     `yaml_path=mqtt.password`), each of which must report zero upstream
     calls in audit;
   - repeats the dashboard and power canaries from beta.8 step 5.

## 9. Findings that contradict the brief's assumptions

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | The three tools are in no policy, capture or registry contract; no exact descriptor is committed anywhere | policy JSONs and `docs/evidence/upstream-read-compatibility/ha-mcp-8.4.3/8.5.0.json` searched; HAMCP-187 §10.2 committed the doc only | D1 capture is a prerequisite |
| F2 | Ordinary policy entries break stock count, registry sha, advertised count and order fingerprint (whole-read quarantine), and invalidate signed entries | §5.1 | optional reviewed tool mechanism (§5.2) |
| F3 | The component version is independent of the ha-mcp version: production 2.2.1 ships in the 8.6.0 tree, while 8.5.0 bundles 2.2.0 and 8.4.3 bundles 2.1.3 | manifests | Engineering cannot attest the component; L1-L4 must not depend on 2.2.1-only denies |
| F4 | The filesystem flag is a beta sub-flag; production needs `ENABLE_BETA_FEATURES` | HAMCP-187 §3d | beta-tagged surface; disclosed |
| F5 | Root `secrets.yaml` **is** on the upstream allowlist: a direct root read is allowed and masked (key names, size and mtime disclosed), not refused; nested or resolved `secrets.yaml` paths are denied | CC:267-279, :1248; CONST:95 | Engineering refuses root `secrets.yaml` itself before dispatch (L1/L2), zero upstream calls (§2.3, R7) |
| F6 | `!include` targets other than the four root files and allowlisted dirs are unreadable upstream, for example `sensors.yaml` or `templates/` | §1.3 | Engineering never follows includes; F002 recorder coverage needs `recorder` inline in `configuration.yaml` (otherwise the include tag is returned and the result is `partial`) |
| F7 | `yaml_path` addresses mapping keys only, with no list indices; list-root files are not addressable | CC:2386-2390 | automations/scenes excluded (D4) |
| F8 | No upstream size cap; whole-file read; unbounded glob fan-out | CC:1366; YR:456 | Engineering never sends a glob, so each request is one whole-file read of `configuration.yaml`; Engineering bounds only its output; disclosed |
| F9 | ha-mcp `redact_secrets` does not cover file content | HAMCP-187 :464-466 | Engineering L4 is the only content redaction |
| F10 | No PEM, key or certificate detection exists upstream **or in Engineering**; `*.pem`/`*.key`/`ssl/` have no deny rule upstream | CONST:75, :95; `E/sanitization.py` | new PEM scanner (L4) |
| F11 | Admin-configured extra directories grant read (and write) on arbitrary volumes, for example `/ssl` | §1.3 | Engineering never addresses them (L1 admits one file). Owner assumption 2026-10-15: default (empty), never changed; not independently observed |
| F12 | `!env_var` defaults are returned verbatim | `yaml_rt.py` | L4 redacts defaults |
| F13 | The upstream "Path not allowed" error discloses the effective allowlist | CC:2919 | fixed error codes (§4) |
| F14 | `!input` is not a registered tag (inferred tag loss) | `yaml_rt.py:66-77` | out of scope (blueprints excluded) |
| F15 | Adding a provider surface would change the family descriptors and every `family_admission` record | `E/compatibility_family.py:59-62, :411, :849` | per-tool explicit dispositions instead (D2) |
| F16 | With the flag on, backup and lifecycle whole-catalog validation already refuses (both surfaces `held` for 8.4.3 and 8.5.0, so no loss today) | `E/providers/operational_backup.py:326` | unchanged; noted for when those holds lift |
| F17 | A selected credential leaf or sensitive ancestor loses its key name in the returned node (review R1) | CC:2386-2394; review probe | L3 refusal and L4 absolute-path redaction (§2.2) |
| F18 | The existing URL-userinfo scanner covers `http(s)` only, so database URIs pass unredacted (review R2). A name-based URI rule still misses a percent-encoded ODBC DSN in `odbc_connect` (first delta review), and recognize-then-validate misses fully encoded URIs and over-bound DSNs under ordinary keys (second delta review) | `E/sanitization.py`:84-85; review and delta-review probes | reader-scoped rules A, B, C′ (**decode, then deny by substring**), R10 canonical keys and R11 (§2.2, revision 5) |
| F19 | Home Assistant loads only `configuration.yaml`; `configuration.yml` is neither a Core configuration file nor on the component allowlist (review R6) | HAC:40; CC:267-279 | byte-exact single-file L1 refuses `.yml` |
| F21 | Upstream renders a tagged scalar as its source text, which is identical to a quoted string with the same text, and the unchanged final sanitizer replaces any value under a sensitive key (delta review R8) | `yaml_rt.py`:208-209; `E/sanitization.py`:341-350 | L4-minted internal `ReferenceMarker` type with a default-off L5 exemption by exact type; quoted-literal residual disclosed (§2.2, revision 3) |
| F20 | The current coordinator installs one profile per surface and aggregates all declared capabilities, so an attached-only route can neither pass `acquire_route` nor be added without affecting the stock aggregate (review R5) | `coordinator.py:147`, :452-500, :723-802 | extension profile, aggregate isolation, existing lease path (§5.2) |

## 10. Owner decisions (recorded 2026-10-15)

Josh's decisions of 2026-10-15 are recorded below. Final owner approval of
the corrected design follows the focused delta review (§12); nothing below
authorizes implementation, capture, release or deployment before that.

| # | Decision | Recorded owner decision |
|---|---|---|
| D1 | Disposable offline capture (HAMCP-187 §10.1 method, internal network, no live HA) of 8.4.3 and 8.5.0 with `ENABLE_BETA_FEATURES=true`, `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, YAML editing off, and the committed artifact plus provenance. The capture records the **complete** runtime descriptor of all three tools (R4) and is repeated once per version to prove the fingerprint is stable | **Authorized.** Runs after final approval of this design; not run in this batch |
| D2 | Admission mechanism: (a) optional reviewed tools with per-version explicit dispositions in the adapter module, or (b) a new `yaml_reader` provider surface, or (c) ordinary policy entries | **(a)**, which must follow HAMCP-184's fail-closed rule with an explicit disposition for every registry version and no default. Revision 2 adds the complete-descriptor binding (R4) and the generation and lease contract (R5) |
| D3 | Classify `ha_list_files`/`ha_read_file` as reviewed `prohibited` optional names, with optional accounting in a new field (stock `reviewed_tool_accounting_valid` meaning unchanged) | **Accepted** |
| D4 | Readable set | **`configuration.yaml` only** (narrowed from revision 1's `configuration.yaml` + `packages/`) |
| D5 | Packages | **Removed from the first release entirely**: no `packages/*.yaml` glob and no single package files. Production does not use HA packages (not enabled by default; Josh is unaware of any). This resolves R3 by scope removal. A future option is recorded in §12 and not designed now |
| D5a | Component extra directories (F11) | **Assumed default (empty), never changed.** Engineering never addresses them regardless |
| D6 | Core mapping: (a) basic REST+WS profiles with component routes disclosed as unattested, or (b) a new dormant profile needing signed sequence 13 | **(a)** |
| D7 | Bindings for 8.4.3 and 8.5.0 only; 8.6.0 `held` (HOLD), 8.7.0 future | **Accepted** |
| D8 | Public name | **Keep `ha_config_get_yaml`** |
| D9 | One PR, or the A/B split | **One PR** (split only if the §8.3 threshold is exceeded) |
| D10 | Part 2 protected paths (§8.3, now including the coordinator and models) and `2.4.1-beta.12` release metadata | **Authorized after HAMCP-184 merges**; release number revalidated then |
| D11 | Never return raw `content` or comments (parsed only) in this release | **Accepted** |
| D12 | Best-effort redaction of free-form configuration (unlabeled secret values cannot be recognized) | **Accepted with disclosure.** Labeled credentials (R1), connection strings under connection-shaped keys (rule B) and any text containing userinfo or a credential word (rule C′, on canonical text and canonical keys) are handled explicitly. `pass` and `key` labels are caught when delimited (`pass=`, `key:`; rule C′3, owner addition §15.1). Still not recognized (revision 5): a raw secret under an innocent key; a label holding none of the 13 credential words and not a delimited `pass`/`key` (`pw=`, `psk=`, `pin=`, `code=`, `login=`); an undelimited `pass`/`key` label (`my_key=`, `passkey=`, a quoted `"key": S`); a word interrupted by separators inside a value (`p_w_d=`); confusables NFKC does not map; a non-percent encoding; a secret split across values (§2.2 rule D). **Over-redaction now includes harmless values and keys mentioning a credential word** (for example "Refresh the access token", an `oauth` URL, `auth_providers`) and text with a delimited `pass`/`key` label (`Press key: Enter`); owner-accepted (§15.1) |
| D13 | Analysis consumers (dependency, integrity) adopt the reader | **Later**, in a separate task |

Josh's decisions on revision 5 (2026-10-15: the C′3 token rule, keeping
the `..` refusal and accepting the wider over-redaction) are recorded in
§15.1.

## 11. Non-actions in Part 1

These apply to revision 1, to the revision 2, 3, 4 and 5 correction
batches, and to the revision-5 owner addition (§15.1).

- No production code, schema, policy, registry, signed-registry, HOLD,
  support-window, version, `.release/`, workflow or stable-v1 change.
- No upstream capture executed. No live Home Assistant, Supervisor, ha-mcp or
  Engineering access. No credentials or secrets read.
- HAMCP-184's branch and files are untouched.
- Draft PR only; not marked Ready, not merged, not released or deployed.

## 12. Revision 2: correction batch and deferred items

**Source of the corrections.** The independent Codex design review of PR #260
at `29b7b33` (verdict *changes required*: 2 High, 3 Medium, 2 Low, no
Critical) and Josh's scope decision of 2026-10-15. One correction batch;
a focused delta review follows.

| Finding | Severity | Resolution in revision 2 | Where |
|---|---|---|---|
| R1: selecting a credential leaf or ancestor strips the key context | High | Both: pre-dispatch refusal of any credential-shaped `yaml_path` segment (L3), **and** absolute-path redaction that includes the requested prefix and replaces whole values and containers (L4). Reference-tag behaviour made explicit. Final-wrapper tests across leaf, parent, nested, list, numeric and every output channel | §2.2 L3/L4, §3, §8.1 groups 2-3, §8.2 |
| R2: database URIs bypass the `http(s)`-only scanner | High | Reader-scoped connection-URI redactor: any scheme including driver-qualified forms; userinfo and credential query parameters redacted; the whole value redacted when parsing is not exact; keyword DSNs; connection keys; credential-free recorder settings kept. **Superseded by revision 3 (§13), then by revision 4's decode-then-judge rules (§14)** | §2.2 L4, §8.1 group 4 |
| R3: the package glob reads Engineering-denied names upstream; independence claim false | Medium | **Resolved by scope removal** (D4/D5): no glob and no `packages/`; the upstream `file` is a binary constant naming the one allowlisted file; zero upstream calls for any other path. Independence claim replaced with the true L1/L2 relationship and matching tests | §0, §2.2 L1/L2, §3, §8.1 group 2 |
| R4: optional bindings omit complete runtime-descriptor coverage | Medium | Binding on the complete runtime-contract fingerprint under the release's explicit model (v2 projection only), plus an exact top-level field set; component hashes are diagnostic only; mutation tests for every top-level field including `_meta` and `execution` | §5.2, §8.1 group 10 |
| R5: post-profile attachment lacks a generation, authority and lease contract | Medium | Optional extension profile on `compiled_exact` selections only, in the authority fingerprint; stock-plus-extension coordinator profiles; aggregate isolation; same generation, live revalidation, `acquire_route` and `commit_route` path; retirement refuses before dispatch; signed selections never carry it | §5.2, §5.3, §8.1 group 11, §8.3 |
| R6: `.yml` exceeds the component's package rule | Low | `configuration.yaml` only, byte-exact; `configuration.yml` refused before dispatch (HA Core loads only `configuration.yaml`) | §1.3, §2.2 L1, F19 |
| R7: the table said the component refuses `secrets.yaml` | Low | Corrected: a direct root read is allowed and masked; nested or resolved paths are denied; Engineering refuses root `secrets.yaml` itself with a zero-call test | §2.3, F5 |

Also updated for the narrowed scope: §0 summary, §1.4, §4 output (single
match flattened, no glob warnings), §5.4 (no `list_files`), §6 description,
§7 use cases, §8 tests, sizes and release canaries, and §10.

**Deferred items** (recorded, not designed now):
- **Packages.** If Josh later wants package files, add them **only through
  explicit named-file reads** (option a): each concrete filename validated
  by Engineering before its own read, one upstream read per named file.
  Never use upstream glob expansion. This would widen the `file` enum
  additively and needs its own review.
- **8.6.0 and later** bindings follow their HOLD decisions (D7).
- **Analysis consumers** adopt the reader in a separate task (D13).

## 13. Revision 3: R2 residual and R8 correction

**Source.** The focused Codex design delta review of PR #260 at `e6fb813`
returned *needs corrections*. R1 and R3–R7 are resolved. The R2 residual
remains High, and R8 is a new Low finding. There are no Critical findings.
This is a docs-only correction batch, and a focused delta review of
revision 3 follows.

| Finding | Severity | Resolution in revision 3 | Where |
|---|---|---|---|
| R2 residual: a percent-encoded ODBC DSN in an `odbc_connect` query value passes the name-based URI rule and real L5 | High | **Default-deny** for connection strings. These are values under connection keys (judged by the absolute path, so a direct `recorder.db_url` selection is included), every non-web `scheme://` URI, and keyword connection strings. A value is visible only if it passes a strict raw grammar (no userinfo, no `;`, no fragment, exact query items) **and**, after decoding every component until stable (at most four rounds), stays inside decoded charsets that exclude `=`, `;`, `@`, `:`, `/`, `{`, `}` and `%`. It must also have no credential-like or non-allowlisted query name and no duplicate name. Otherwise the whole value is redacted, keeping at most the scheme. Web URIs stay useful but are redacted whole on any userinfo, credential name, nested connection string or nested URI, or unstable encoding. Credential-free SQLite, MySQL and PostgreSQL URIs stay visible. **Withdrawn in revision 4 (§14):** recognition before validation was bypassable | (withdrawn) |
| R8: L4-preserved references conflict with the unchanged L5 | Low | One rule: L4 alone mints an internal frozen `ReferenceMarker` from a whole scalar that matches a strict `!secret`/`!env_var` grammar. The `!env_var` default is discarded at minting. L5 gains a **default-off** `preserve_reference_markers` opt-in that keeps only `type(value) is ReferenceMarker` with a revalidated name, including under sensitive keys. Plain strings and mappings shaped like a marker get no exemption, and forged marker shapes are redacted. Markers render after L5 as `{"secret_ref": name}` / `{"env_var_ref": name[, "default_redacted": true]}`. Other tools are byte-identical, and a marker reaching a default-off sanitizer is redacted | §2.2 L4 *Reference markers*, L5, §4, §8.1 group 3a, §8.2 |

Also updated: the status line, the §0 summary, §2.3, the §4 `value` note,
the §6 description, the §7 F002 example, the §8.3 sizes (still one PR,
under the threshold), F18, the new F21, and §11.

**For Josh's final approval** (a disclosed residual, not a new product
decision): a YAML string literal written exactly like a tag (`"!secret
abc"`, quoted) renders identically upstream (`yaml_rt.py`:208-209), so it
is shown as a reference marker. The exposure is limited to text matching
the strict name grammar that passes the value scanners. The alternative is
to drop reference preservation under sensitive keys entirely. Revision 3
keeps preservation, as the brief directs. *Josh has since accepted this
residual (recorded in §15).*

## 14. Revision 4: R2 by simplification and R9

**Source.** The second focused Codex delta review of PR #260 at `042f254`
returned *needs corrections*. R8 is resolved. R2 remains High: recognition
ran before validation, so a fully percent-encoded URI (1–6 rounds) or a
keyword DSN encoded past the bound (5–6 rounds) under an ordinary key was
never recognized. The review's 12 boundary cases include 8
counterexamples. R9 is a new Low finding. The architect directed a
replacement rule set rather than a fourth recognizer patch. This is a
docs-only correction batch, and a focused delta review of revision 4
follows.

| Finding | Severity | Resolution in revision 4 | Where |
|---|---|---|---|
| R2 (second residual): encoded candidates bypass recognition under ordinary keys | High | **Recognition removed.** **A:** every string and mapping key is percent-decoded to a canonical form in at most four rounds. A value that is invalid, ambiguous, or still holds a valid escape after round four is redacted whole (`[REDACTED:encoding]`). Every later check uses the canonical text, and visible values are emitted raw. **B:** every value at or below a connection-shaped key (canonical name contains `url`, `uri`, `dsn`, `connect`, `connstr` or `database`, on the absolute path, at any depth) is redacted whole, keeping at most the scheme. The one exception is a zero-round canonical `^sqlite:///[A-Za-z0-9_./-]+$`. **C:** every other string is redacted whole when its canonical text holds userinfo (`@` after `://`) or a credential-like `name=` assignment (one predicate for URI queries, `;` pairs, libpq pairs and nested values). **D:** the D12 residual is reworded to name what A–C do not recognize. The revision-2/3 recognizer, strict grammar, decoded charsets, query-name allowlist and web-URI regime are withdrawn | §2.2 L4 *Connection-string rules A–D*, §2.3, §8.1 group 4, §8.2 |
| R9: inconsistent unknown-scheme and decoding-depth expectations; non-discriminating decoder mutation | Low | **One number, one rule:** at most four decoding rounds, and a value needing a fifth is non-converging. Unknown schemes follow the same rule as every scheme: B redacts at a connection-shaped key, and C judges elsewhere (`unknown://host/path` is visible at `sensor.value` and redacted at `recorder.db_url`). Four-times-encoded values are canonical, not unstable. Exact-output discriminators: benign 4× visible, benign 5× `encoding`, `Pwd=S` 4× `connection_string`. Each decoder mutation has a named killing case | §2.2 rule A, §8.1 group 4, §8.2 |

**Changed behaviour versus revision 3** (deliberate, and the cost of
simplicity):
- credential-free MySQL and PostgreSQL `db_url` values are now redacted to
  their scheme. Only the plain SQLite path stays visible;
- URLs under any `*url*`/`*uri*` key, such as `external_url`, are redacted
  to their scheme;
- under ordinary keys, credential-free URIs with any query name, and
  credential-free DSNs, are now **visible**. Revision 3 redacted them as
  non-allowlisted or nested.

The over-match is listed in §2.2.

**Self-check, not a review.** A throwaway author rule model of rules A–C
(`~/tmp/hamcp162-r4/rule_model.py`, sha256 `e3bba45b…540d`) runs every
case through the real, unchanged `sanitize_untrusted_data` as L5. It
passed **687/687** checks, which covered:
- the reviewer's 12 boundary fixtures, built verbatim with the reviewer's
  own `encode` and synthetic value. The revision-3 model still shows exactly
  8 counterexamples, and all 12 are redacted under rules A–C at
  `sensor.value` and `recorder.db_url`;
- the §8.1 group 4 cases;
- ten mutations. Each one fails the discriminating subset.

The model also showed that the unchanged L5 alters a `{% if token == 1 %}`
template through its existing token pattern. That is pre-existing L5
behaviour and not part of rules A–C. The model is not independent review,
not an implementation and not a capture. Part 2 tests prove the
implementation.

**Unchanged:** R1 and R3–R8, reference markers and the quoted-literal
residual (§13; accepted by Josh, recorded in §15), admission, catalog
and release.

## 15. Revision 5: substring deny, canonical keys and strict SQLite

**Source.** The focused Codex delta review of revision 4 at `3391172`
returned *needs corrections*. R9 is resolved. R2 remains High: revision 4's
C2 assignment parse exempted `==`, so `Server=db;Uid=u;Pwd==S` (a valid
assignment of `=S`) stayed visible. R10 (Medium) found that key-based
checks used raw key text, so encoded or fullwidth credential keys escaped
them. R11 (Low) found that the SQLite exception's `^…$` admits a trailing
newline. The review's probe set (438 assertions) is authoritative and
becomes required tests (§8.1 group 4). The architect directed parsing to
stop in favour of a substring deny. This is a docs-only correction batch,
and a focused delta review of revision 5 follows.

| Finding | Severity | Resolution in revision 5 | Where |
|---|---|---|---|
| R2 (third residual): `Pwd==S` passes C2's `==` exemption | High | **Parsing removed (C′).** Every string value is canonicalized (rule A, now ending with NFKC and casefold) and redacted whole when the canonical text holds userinfo or **contains** any of `pwd`, `passw`, `secret`, `token`, `apikey`, `api_key`, `api-key`, `auth`, `credential`, `private`, `bearer`, `cookie`, `session`. At most a leading scheme is kept, and only if the scheme holds no credential word. The revision-4 C1/C2 text and its credential-like name list are removed | §2.2 rule C′ |
| R10: key checks run on raw key text | Medium | **Canonical keys.** Every key at every level is canonicalized (rule A, NFKC, casefold) before credential-key refusal (L3), path-context redaction, rule B scope and the new key-text check **K1**. `is_credential_key` also matches any compact canonical key containing a credential word. A key that is not a plain identifier and holds userinfo or a credential word withholds its mapping; a key that rule A refuses withholds its mapping as `encoding`. Visible keys are emitted raw | §2.2 L4 order, `is_credential_key`, K1, path-context rule, rule B |
| R11: `^…$` admits `sqlite:///x\n` | Low | `re.fullmatch(r"sqlite:///[a-z0-9_./-]+", canonical)`, with explicit refusal of `\x00`–`\x1f`, `\x7f`, ` ` and ` `, no `..` segment, the value passing C′, and the raw string equal to its canonical text | §2.2 rule B (R11) |

**Changed behaviour versus revision 4** (deliberate):
- **Over-redaction widens.** Any harmless value or key that mentions a
  credential word is redacted ("Refresh the access token daily", "Author",
  "Session started", `oauth` URLs, templates mentioning `token`,
  `auth_providers`, `private_ble_device`, `session_timeout`). D12 and the
  tool description say so (§2.2 rule D, §6, §10).
- **Narrower value-side names (flag for Josh and the reviewer).** Revision
  4's C2 list included `pass` and `key`; the architect's word list has
  `passw` and `apikey`/`api_key`/`api-key` instead. A value such as
  `pass=S` or `key=S` under an innocent key was a disclosed D12
  residual. Keys are unaffected: `is_credential_key` still matches `pass`,
  `key`, `pin`, `code` and their suffixes. The revision-4 over-match on
  names such as `monkey=1` is gone. **Closed by Josh's C′3 addition**
  (§15.1).
- **`..` refusal diverges from the reviewer's recommendation.** The review
  suggested that `sqlite:///../…` could be shown literally because showing
  text opens nothing. The architect's R11 decision refuses any `..`
  segment, so such values are redacted to the scheme. `sqlite:///a..b.db`
  (not a segment) stays visible. **Kept by Josh** (§15.1 decision 2).
- **SQLite is stricter.** The value must equal its canonical text, so any
  upper case (`SQLITE:///`, `X.db`), fullwidth character or `%` is
  redacted, and a path containing a credential word (`session.db`) is
  redacted by C′ first.
- **Scheme is emitted canonical.** A kept scheme appears in lower case
  (`MYSQL://…` gives `mysql://[REDACTED:connection_uri]`).
- **L3 refuses more selections**: any `yaml_path` segment whose compact
  form contains a credential word (`homeassistant.auth_providers`). Read
  the parent instead; the value is withheld there too.
- Minted reference names are not judged by C′ (minting precedes it); this
  is the accepted R8 behaviour, not a new exemption.

**Self-check, not a review.** A throwaway author rule model of rules A, B,
C′, R10/K1 and R11 (`~/tmp/hamcp162-r5/rule_model.py`, sha256
`77286b97…d6edb422`) runs every case through the real, unchanged
`sanitize_untrusted_data` as L5 and reuses the reviewers' fixtures
verbatim (the second and third delta reviews' probe files). It passed
**716/716** assertions:
- **all 438** assertions of the revision-4 delta review's probe set,
  ported: 425 unchanged and **13 deliberate flips**:
  - R2 (3): `Server=db;Uid=u;Pwd==S`, `mysql://db/ha?pwd==S`, and `Pwd==S`
    encoded three times are now redacted;
  - R10 (3): the encoded keys `%70assword`, `%63redentials` and
    `%2570assword` are now classified as credential keys;
  - R11 (2): `sqlite:///../../secrets.yaml` and
    `sqlite:////config/../secrets.yaml` are redacted (the `..` decision;
    both also contain `secret`), not shown literally;
  - D12 (5): residuals the review pinned as leaking are now caught by C′
    or canonical keys: HTML `Pwd&#61;S`, `Pwd=S`, the fullwidth `＝`
    delimiter, the NFKC key `db_urℓ`, and the JSON payload `{"pwd": …}`;
- **278 new** assertions for batch 4: Pwd variants and key variants at
  depths 0–6 at both paths, encoded ancestors, K1, R11 visible and
  redacted lists, useful visible controls, exact over-redaction outputs,
  the NFKC-introduced escape, the fullwidth `＠`, and pinned residuals;
- the revision-4 rule A mutations (once, bound 3, bound 5, no stability,
  unbounded, raw, emit canonical, lenient) and 13 revision-5 mutations
  (restore C2's `==` exemption, no NFKC, no casefold, raw keys, no K1,
  `match` with `$`, no `..` check, no raw-equals-canonical, no final
  escape check after NFKC, no word list, no userinfo, raw text without
  casefold, no B). Each is killed by at least one exact expected output.

The model is not independent review, not an implementation and not a
capture. Part 2 tests prove the implementation.

**Owner acceptance recorded.** Josh has accepted the key over-redaction
(credential-shaped keys withheld even when harmless) and the quoted-literal
reference residual (§2.2 *Reference markers*, §13). His decisions on the
value-side over-redaction, the `pass`/`key` narrowing and the `..`
divergence are recorded in §15.1.

**Unchanged:** rules A (apart from NFKC and casefold) and B (apart from
canonical keys and R11), R1 and R3–R9, reference markers, admission,
catalog and release.

### 15.1 Owner decisions on revision 5 (2026-10-15)

Josh decided the three items that revision 5 flagged. This is still a
docs-only change and part of revision 5. The focused delta review of
revision 5 covers it (C′ including C′3, R10 and R11).

| # | Decision | Recorded in |
|---|---|---|
| 1 | **Add a delimited-token rule to C′** for the two words left out of the substring list. On the canonical text (after decoding, NFKC and casefold), if `\b(pass\|key)\s*[=:]` matches anywhere, the whole value is redacted. This catches `pass=S`, `PASS ==S`, `key: S`, `;key=S` and their encoded forms, and does not redact `keypad`, `monkey`, `bypass` or `compass`. Tests cover each catch (including `==`, `:=`, leading separators and fullwidth forms after NFKC) and the non-catches (`keypad`, `monkey`, `bypass`, `compass`, `key fob`). A mutation that drops the rule must fail the tests | §2.2 rule C′3, K1, rule D, examples, over-redaction; §6; §8.1 group 4; §8.2; D12 |
| 2 | **Keep refusing `..` in SQLite paths** (architect's R11 instruction). The reviewer's alternative, showing `sqlite:///../…` literally because displaying text opens nothing, was considered and not adopted | §2.2 R11, §15 |
| 3 | **Accept the additional over-redaction**: credential words in harmless text, `oauth` URLs, `auth_providers` and similar keys. C′3's own over-redaction (`Press key: Enter`, `{% set key = 1 %}`, `key://` URIs) is of the same kind and is disclosed with it | §2.2 *Disclosed over-redaction*, D12 |

**Decision 1, applied to keys.** K1 already withholds a mapping whose
non-identifier key text holds a credential word. It now applies C′3 too, so
a key `key=S` or `pass: S` withholds its mapping instead of showing `S` in
the key. This only affects keys containing `=` or `:`, which are never
plain identifiers. It is the same text test applied to the other emitted
text, and it is called out here for the delta review.

**Leak closed.** The revision-5 narrowing (`pass=S` and `key=S` under an
innocent key) is closed. What C′3 still does not see is listed in rule D
and D12 under the honest wording:
- a label joined to a preceding letter, digit or `_` (`my_key=`,
  `app_key=`, `passkey=`);
- a label separated from its `=` or `:` by a non-whitespace character
  (`"key": "S"`, `{'pass': 'S'}`, `pass_word=`);
- another delimiter (`key→S`, `key S`);
- confusables NFKC does not map (Cyrillic `е` in `kеy=`).

**Self-check re-run, not a review.** The revision-5 author model was copied
and extended with C′3 (`~/tmp/hamcp162-r5b/rule_model.py`, sha256
`47f650cf7d3af6918d2a0af7702765e46ebf12804b674c38ef7982916f5b26a7`;
Python 3.12.14, real unchanged `sanitize_untrusted_data` as L5). It
passed **1028/1028** assertions:
- the 438 ported revision-4 probe assertions are unchanged by C′3. 425
  hold, and the same 13 deliberate flips as in §15 apply, with no new flip;
- the 278 revision-5 assertions hold, apart from the two pinned residuals
  `pass=S` and `key=S`, which moved to the catch set;
- **314 new** assertions:
  - 17 catch forms at depths 0–6 at both paths;
  - 3 query forms at depths 0–4;
  - K1 keys;
  - 18 non-catches at two ordinary paths, plus one in a mapping;
  - 6 over-redaction pins;
  - 14 pinned residuals (10 new);
  - 6 mutation checks (the drop sweep, NFKC/casefold on token cases, and the 3 new mutations);
- mutations: the 8 revision-4 rule A mutations, the 13 revision-5
  mutations and 3 new ones are all killed:
  - **dropping C′3** is killed by 3 exact outputs, and a sweep confirms
    that all **85/85** ordinary-path token catches (17 forms × depths 0–4)
    fail without it;
  - C′3 without `\b` is killed by `monkey=1` and `bypass=on`;
  - C′3 as a plain substring is killed by `keypad`, `compass` and `key
    fob`;
  - NFKC and casefold removal are killed by the fullwidth and upper-case
    token cases.

The model is not independent review, not an implementation and not a
capture. Part 2 tests prove the implementation.
