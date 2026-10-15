# HAMCP-162 delegated read-only YAML configuration reader — design

Status: **Part 1 design report, awaiting Josh's decisions (§10).** No
production code, policy, registry, schema, version or workflow change is part
of this document. Nothing here authorizes a capture, deployment, live Home
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
- Expose **neither** `ha_list_files` nor `ha_read_file`. Both are still
  explicitly classified as reviewed-and-never-exposed, so that they stop
  counting as unreviewed (§1.4, D3).

| | Value |
|---|---|
| Public tool | `ha_config_get_yaml` (closed schema, §3) |
| Upstream call Engineering builds | `{"file": <validated>, "yaml_path": <validated>, "include_content": false, "include_parsed": true}` |
| Readable files | `configuration.yaml`, and `packages/…/*.yaml` (single files, or the one fixed glob `packages/*.yaml`) |
| Refused before dispatch, with zero upstream calls | everything else, including `secrets.yaml`, `.storage/`, `*.pem`/`*.key`/`*.crt`, `www/`, `custom_components/`, `ssl/`, absolute paths, traversal |
| Returned | the parsed subtree only. It is projected through a positive allowlist and then YAML-aware redaction (credential keys, PEM, JWT, bearer, URL credentials). `!secret`/`!env_var` are kept as reference names only. Raw text and comments are never returned. |
| Admission | an **optional reviewed tool**: compiled per-version contract bindings for 8.4.3 and 8.5.0, with an explicit per-version disposition for every registry version. It is outside the stock policy JSON, the stock count, the order fingerprint and the whole-catalog validator, so no existing digest, signed entry or fingerprint changes (§5). |
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
| `ha_list_files` | **Do not expose** in this task | Discloses directory layout, sizes and mtimes of `www/`, `themes/`, `blueprints/` and configured extra dirs, with no count bound. Its only use here is package discovery, which the fixed Engineering-built glob `packages/*.yaml` already covers (§3). |
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

Engineering must not rely on any upstream deny rule. Five layers apply, in
this order. Each layer works without the others.

**L1. Positive path allowlist** (before dispatch, zero upstream calls). The
`file` argument must be one of:
- the literal `configuration.yaml`;
- the literal glob `packages/*.yaml` (the only glob accepted);
- a package file matching
  `^packages(/[A-Za-z0-9_][A-Za-z0-9_-]{0,63}){0,3}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.ya?ml$`.
  That is, at most four path segments under `packages/`, no leading dot in any
  segment, and an extension of `.yaml` or `.yml`.

Everything else is refused with `yaml_file_not_allowed`.

**L2. Denylist** (redundant defense in depth; before dispatch; checked
case-insensitively on every segment). The request is refused when any of
these hold:
- a segment equals `.storage`, `.cloud`, `.git`, `ssl`, `www`,
  `custom_components`, `blueprints`, `backups`, `deps`, `tts` or
  `.ha_mcp_tools`;
- a segment starts with `.`;
- the basename is `secrets.yaml`, `secrets.yml` or `approval_pin.json`;
- the basename matches `*secret*`, `*credential*`, `*token*`, `*.pem`,
  `*.key`, `*.crt`, `*.csr`, `*.der`, `*.p12`, `*.pfx`, `*.jks`, `*.db`,
  `*.db-*`, `*.sqlite*`, `*.json`, `*.log*`, `id_rsa*` or `id_ed25519*`.

The denylist runs **after** the allowlist and must never admit anything. A
test asserts that every deny case is also outside the allowlist, so that
removing L1 alone still refuses.

**L3. Normalization.** Applied before L1/L2, with refusal on any of these:
- NUL or other control characters;
- backslash;
- a percent sign, so there is no encoded traversal;
- `//`;
- a leading `/` or `~`;
- a `.` or `..` segment;
- a URL scheme;
- more than 256 characters;
- characters outside `[A-Za-z0-9_.\-/*]`;
- `*` anywhere except the one literal glob.

Engineering never rewrites a path. A path is accepted exactly as written, or
refused.

**L4. Output projection and YAML-aware redaction** (after dispatch, before
the shared sanitizer):
- Only `parsed` is requested and returned. Engineering forces
  `include_content: false`, so comments and raw text never cross the
  boundary.
- **Key-based redaction**, applied at any depth, including inside lists:
  - first, the existing `sanitize_untrusted_data` key sets (`E/sanitization.py`
    :19-75; exact normalized-key match, :146-172);
  - then a new YAML credential set:
    - exact keys `api_password`, `apikey`, `app_key`, `app_secret`,
      `auth_token`, `bearer_token`, `consumer_key`, `consumer_secret`,
      `access_key`, `secret_key`, `private_key`, `encryption_key`, `psk`,
      `pin`, `code`, `passcode`, `username_password`, `key`;
    - normalized-suffix rules `*_password`, `*_passwd`, `*_token`,
      `*_secret`, `*_api_key`, `*_apikey`, `*_private_key`, `*_psk`,
      `*_pin`, `*_code`.
  - Suffix matches can redact harmless fields such as `country_code`. This is
    accepted and disclosed as fail-closed.
- **Value scanners on every string:**
  - the existing JWT, bearer, `Authorization` header, URL userinfo, URL
    credential-parameter, webhook-path and Matter patterns (`E/sanitization.py`
    :76-92);
  - **new PEM detection**: any `-----BEGIN [A-Z0-9 ]*(PRIVATE KEY|CERTIFICATE|ENCRYPTED PRIVATE KEY|OPENSSH PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----`
    marker redacts the whole string, not just the block (the #2714 style);
  - a bare base64 run of 200 or more characters is redacted as
    `opaque_blob`.
- **Tags:**
  - A string matching `^!secret [A-Za-z0-9_.-]{1,128}$` is kept. The secret
    *name* is a reference, not a value.
  - `^!env_var ` keeps only the variable name (`!env_var NAME`). Any default
    is replaced with `[REDACTED:env_var_default]`.
  - `!include*` strings are kept only if the target matches
    `[A-Za-z0-9_./-]{1,256}`.
  - Any other string that starts with `!` passes through the normal value
    scanners.
- Redaction is recorded per category (`redaction.categories`,
  `redacted_field_count`). `completeness` stays `complete`, because a
  redacted value is not missing data.

**L5. The existing gateway sanitizer.** It runs last, with its usual
`max_string` and its `response_too_large` refusal.

**`!secret` values are never resolved by Engineering**: Engineering has no
code path that reads `secrets.yaml` and will not gain one.

### 2.3 Verified versus inferred

| Claim | Status |
|---|---|
| Upstream never resolves `!secret`/`!env_var`/`!include` | Verified (CC:2361-2392, `yaml_rt.py`) |
| `secrets.yaml` and `.storage` are refused by the component | Verified for 2.2.1; Engineering refuses them anyway (L1/L2) |
| A symlink `packages/x.yaml → ../secrets.yaml` or `→ ../.storage/…` is refused | **Inferred.** Component 2.2.1 checks the resolved path (CC:684-685, :747-749). Engineering cannot see the filesystem, so it relies on the component for symlinks and on L4 for content. Disclosed. |
| A symlink `packages/x.yaml → <config>/custom_components/…/*.py` is readable | **Inferred** possible. The allow rule is evaluated on the requested path. The content would arrive as a parse error or a scalar; L4 still applies. |
| Inline credentials are returned by upstream | Verified (no redaction in the read path) |
| L4 catches every secret | **Not claimed.** Key and pattern redaction is best-effort over free-form configuration. An unlabeled secret value (for example `foo: s3cr3t`) cannot be recognized. Disclosed in the tool description and in `limitations`. |
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
      "description": "Dotted mapping-key path, e.g. 'recorder' or 'homeassistant.customize'. List indices are not supported."
    },
    "file": {
      "type": "string",
      "default": "configuration.yaml",
      "maxLength": 256,
      "description": "configuration.yaml, a file under packages/, or the literal glob 'packages/*.yaml' (one directory level)."
    }
  }
}
```

**Engineering-built upstream arguments**

```
{"yaml_path": <validated>, "file": <validated>,
 "include_content": false, "include_parsed": true}
```

All four keys are always sent; nothing is defaulted upstream.

**Rejected:**
- any other property, including `include_content`, `include_parsed`, `path`,
  `tail_lines`, `pattern` and `action`;
- non-string values;
- a `yaml_path` that is empty, has more than eight segments, a segment over
  64 characters, a segment that starts with `-`, or any character outside the
  pattern, such as spaces, `*`, `[` or quotes;
- a `file` that fails L1/L2/L3.

Every refusal happens before admission and dispatch: zero upstream calls, and
an audit record with a fixed reason code
(`yaml_read_arguments_invalid`, `yaml_path_invalid`, `yaml_file_not_allowed`).

**Allowed path set**
- `configuration.yaml`.
- `packages/**` up to depth 4, `.yaml`/`.yml`.
- The literal glob `packages/*.yaml`.

**The packages folder name is fixed to `packages`.** A custom folder is not
followed: the component would allow it, but Engineering would not. Owner
decision D5.

Two related exclusions:
- `automations.yaml` and `scenes.yaml` are lists at the root. They are not
  addressable by `yaml_path`, and native tools already cover them.
- `scripts.yaml` is covered by `ha_config_get_script`.

All three are excluded (D4).

## 4. Output

**Positive allowlist** (anything else is dropped):

```
{
  "file": <requested file, echoed>,
  "yaml_path": <requested path, echoed>,
  "source": "disk",                              # not the loaded runtime config
  "matches": [                                   # ≤ 32
    {"file": str, "yaml_path": str, "value": <redacted parsed>}
  ],
  "count": int,
  "files_searched": int,
  "files_unreadable": int,
  "warnings": [str],                             # fixed-category strings only, ≤ 16
  "completeness": "complete" | "partial",
  "limitations": [str],
  "redaction": {"redacted_field_count": int, "categories": [str]}
}
```

**Identity validation** (refuse `yaml_read_response_identity_invalid` on any
failure):
- Each `match.yaml_path` equals the request.
- Each `match.file` equals the requested file. For the glob, it matches
  `^packages/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.yaml$`, and it is re-checked
  against L2.
- `count == len(matches)`.
- `count ≤ files_searched` and `files_unreadable ≤ files_searched`.
- `success is True`.
- The types are exact.

A match whose `file` fails L2 is **dropped, not returned**. It is counted in a
`withheld_file_count` limitation and makes the result partial. This is a
backstop in case upstream expansion ever returns a denied name.

**Warnings.** Upstream warning text is never passed through, because it
embeds upstream failure text. Engineering emits fixed categories:
- `expanded_file_unreadable`;
- `expanded_file_parse_error` (with line and column only);
- `literal_absent`.

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
- At most 4,000 nodes in total across matches.
- At most 32 matches.
- Strings are capped by the gateway `max_string`.
- The whole response is capped by the gateway response limit, with the
  existing `encoded + 8000 > limit` refusal.
- When the node or match cap is reached, Engineering truncates
  deterministically (matches in upstream order, then breadth-first within a
  value). It sets `completeness: partial` and adds
  `limitations: ["value_truncated:<n> nodes omitted"]`.
- If the response is still over the limit, the result is
  `response_too_large` and no partial data is returned.

**Upstream size is not bounded** (F8). The component reads the whole file, and
ha-mcp fans out reads over every expanded package. Engineering cannot bound
that work. It bounds only what it accepts and returns. Disclosed.

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
component allowlist (F6).

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

A new adapter module, `providers/upstream_yaml_reader.py`, owns:
- **`OPTIONAL_TOOL_BINDINGS`**: per (upstream version, upstream name), the
  exact reviewed contract from the D1 capture, plus the source commit.
  Each contract has these fields:
  - `input_schema_fingerprint`;
  - `description_fingerprint`;
  - `annotations_fingerprint`;
  - `output_schema_fingerprint`;
  - `title`;
  - `classification`.

  Classifications: `ha_config_get_yaml` → `mixed_or_requires_wrapper` with
  adapter token `ha-mcp-<v>-yaml-fragment-read-v1`; `ha_list_files` and
  `ha_read_file` → `prohibited`.
- **`OPTIONAL_TOOL_DISPOSITIONS`**: an explicit disposition for **every**
  version in `upstream_release_registry.json`:
  - `admitted` for 8.4.3 and 8.5.0;
  - `held` for every other version, including 8.6.0 until its HOLD lifts
    (D7).

  A loader assertion requires the key set to equal the registry version set
  exactly. A missing version is a load error, never a default. This is the
  per-tool form of HAMCP-184's rule.

**Admission rule.** The YAML reader route is admitted only when all of these
hold:
1. the release's `read_gateway` provider disposition is explicitly `admitted`
   (HAMCP-184 semantics: absent means held);
2. `OPTIONAL_TOOL_DISPOSITIONS[version] == "admitted"`;
3. a binding exists for (version, `ha_config_get_yaml`), and its source
   commit equals the release's source commit;
4. the observed descriptor appears exactly once and matches every
   fingerprinted component;
5. server identity, release, protocol, revocation and HOLD checks pass as for
   every delegated read;
6. the Core gate passes (§5.4).

**What the mechanism leaves unchanged**
- **Not counted** in the stock count, the stock catalog fingerprint, the
  exact order fingerprint input, or the whole-catalog validator. The 77-tool
  and 80-tool order fingerprints are therefore unchanged, and so is every
  existing signed entry.
- **No policy JSON edit.** No digest moves, no legacy pairs are needed, and
  `_legacy_release` stays as it is.
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
- `prohibited_withheld`.

The existing fields are unchanged in meaning. `reviewed_tool_accounting_valid`
(`providers/upstream_read_gateway.py:1738`) compares the policy against the
catalog; the 80-tool catalog makes it false today. Whether optional names
count as accounted is decision D3. The recommendation is a new
`optional_reviewed_accounting_valid` field, leaving the stock field's meaning
unchanged.

### 5.3 Drift quarantines only this tool

**Drift in `ha_config_get_yaml`.** A change in any fingerprinted component,
a duplicate, or a source-commit mismatch quarantines only the YAML reader. The
tool is not exposed (HAMCP-129 §4.4 precedent) and is never exposed with the
upstream schema. All 28 other delegated reads, dashboard writes and power/fan
are unaffected: none of them name it, and HAMCP-175/177 dependency-scoped
admission already ignores extras.

**Drift in a `prohibited` optional name** is reported and changes nothing.

**Absence** with the flag off is `optional_absent`. The tool is not exposed,
the catalog stays at 87, and nothing is quarantined.

**Signed selection.** Under signed-profile selection
(`_select_signed_profile`, `ha_mcp.py:457-487`), the optional route is
admitted only when the signed entry's version equals the binding version.
This is the D8 rule that `withhold_unbound_platform_reads` (`:178`) applies.

The capability profile derived from the policy (`_profile_for_release`) must
stay **byte-identical**, so that existing signed entries keep their profile
ids. The optional route is therefore attached after profile derivation, not
inside it. A test pins every existing profile id.

### 5.4 Core route mapping

**Which Core APIs the upstream tool reaches**
- REST `POST /api/services/ha_mcp_tools/read_file` and `/list_files` (the
  latter for the glob);
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
  - disk, not loaded runtime config;
  - mapping-key paths only;
  - includes are not followed;
  - `!secret` names only, never values;
  - redaction is best-effort;
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
- **Audit F002.** Recorder settings (`yaml_path=recorder`, giving `purge_keep_days`,
  `include`, `exclude`, `db_url` with userinfo redacted). Database-size and
  free-space sensor definitions, when they are inline in
  `configuration.yaml` or in packages.
- **Package audits.** Automations, scripts, template, sensor and MQTT
  entities defined in `packages/*.yaml`. This uses the one-level glob plus
  named deeper files.
- **YAML-defined entities.** The audit's YAML-defined entity origin can be
  confirmed by key, for example `template`, `sensor`, `mqtt`, `command_line`
  or `rest`.

**Gaps that remain** (disclosed):
- `!include` targets outside `packages/` (for example `sensors.yaml`, or
  `templates/` via `!include_dir_merge_list`) are unreadable upstream (F6).
- List elements cannot be addressed directly.
- Nested packages need explicit names.
- Disk is not the loaded config.

**Analysis consumers.**
`entity_dependency_analysis` (docs/ENTITY_DEPENDENCY_ANALYSIS.md:34, :52-57,
:306-310) and `configuration_integrity_analysis`
(docs/CONFIGURATION_INTEGRITY_ANALYSIS.md:67, :183) declare static YAML and
packages "outside coverage until a later reviewed provider supplies it".

**Recommendation: consume later**, in a separate scoped task, after this tool
has been accepted live. Wiring it in now would:
1. change two analysis output contracts and their completeness semantics;
2. multiply upstream fan-out per analysis run (every package file, unbounded
   upstream);
3. mix a new, best-effort-redacted source into evidence that is currently
   fully structured.

The tool itself lets a client gather the evidence manually in the meantime.

## 8. Tests, mutations, files, size, PR split and release

### 8.1 Test groups (`tests/test_hamcp162_yaml_reader.py`)

These tests use real MCP SDK sessions against a synthetic ha-mcp peer
replaying the D1-captured descriptors, at 8.4.3 and 8.5.0.

1. **Useful success:**
   - `recorder` from `configuration.yaml`;
   - a key across `packages/*.yaml` (three files, two matches);
   - a named nested package;
   - exactly one upstream call per request, with the exact Engineering-built
     arguments.
2. **Pre-dispatch refusal, with zero upstream calls asserted** for each case:
   - every L1 miss;
   - every L2 deny entry (case variants: `Secrets.YAML`, `PACKAGES/../secrets.yaml`, `.Storage`);
   - every L3 normalization case (NUL, backslash, `%2e%2e`, `//`, `/config/…`,
     `~`, a URL, an over-length path);
   - any extra property;
   - every `yaml_path` violation.
3. **Secrets in output:**
   - inline `password`/`api_key`/`token`/suffix keys at depth inside lists;
   - URL userinfo and credential parameters;
   - an `Authorization` header;
   - a JWT;
   - a PEM private key and a certificate inside a string;
   - a long base64 blob;
   - `!env_var NAME default` loses the default;
   - `!secret name` is kept as the name only;
   - no `content` field ever, even if the peer sends one.
4. **Identity and shape:** a mismatched `file` or `yaml_path`, `count`
   inconsistency, a denied name in the expanded matches (dropped and
   partial), extra fields dropped, wrong types refused.
5. **Errors:** each upstream error maps to its fixed code. Upstream text,
   including the allowlist listing, never appears in output or audit.
6. **Bounds:** a 33-match glob, a 4,001-node value, depth 25, an
   alias-expansion blowup, `response_too_large`, and deterministic
   truncation with `partial`.
7. **Includes:** a tag string is returned and `include_not_followed` is set.
8. **Admission:**
   - optional-absent with the stock 77 catalog (87 public tools, nothing
     quarantined);
   - exact 80 catalog gives 88;
   - drift in each fingerprinted component, a duplicate and a source-commit
     mismatch each quarantine only this tool;
   - a `held` disposition, an unlisted registry version (load error), and
     `read_gateway` held each refuse;
   - a revoked release refuses;
   - `ha_list_files` and `ha_read_file` are never exposed or dispatched in
     any state.
9. **Invariants unchanged:**
   - stock count;
   - the 77- and 80-tool order fingerprints;
   - every policy and registry digest;
   - every signed profile id;
   - legacy binding behaviour;
   - the whole-catalog validator still refuses 80;
   - power/fan and dashboard admission are identical with the reader admitted
     or quarantined.
10. **Core:** the mapping exists; Core retirement refuses with zero upstream
    calls; both supported Cores pass.
11. **Signed selection:** a version mismatch withholds the route; a matching
    version admits it.
12. **Audit:** attribution, fixed reason codes, no path content beyond the
    validated request.

### 8.2 Mutation checks

Each of the following must fail at least one test:
- dropping L1, L2 or L3 individually;
- making the deny check case-sensitive;
- forwarding `include_content: true`;
- passing upstream error text;
- removing PEM detection;
- keeping `!env_var` defaults;
- counting optional tools in the order fingerprint;
- a default disposition for an unlisted version;
- exposing on `read_gateway: held`;
- attaching the route inside profile derivation;
- removing the Core mapping;
- skipping identity validation;
- returning partial data on `response_too_large`.

### 8.3 Files and size (estimate)

| File | Kind | Lines (est.) |
|---|---|---|
| `E/providers/upstream_yaml_reader.py` (new) | runtime: schema, L1-L3, args, identity, projection, YAML redaction, bounds, bindings, dispositions | 450-550 |
| `E/sanitization.py` | runtime: PEM and opaque-blob scanners (shared, opt-in parameter) | 30-50 |
| `E/providers/upstream_read_gateway.py` | runtime: optional-entry evaluation, routing, `optional_tools` reporting | 180-250 |
| `E/upstream_tool_policy.py` | runtime: optional binding load and version-set assertion | 50-80 |
| `E/ha_mcp_readmission/ha_mcp.py` | runtime: signed-selection version rule, post-profile attach | 30-50 |
| `E/ha_core_readmission/routes.py`, `E/capabilities.py`, tool registration | runtime | 40-60 |
| **Runtime total** | | **~800-1,050** |
| `tests/test_hamcp162_yaml_reader.py` + fixtures generator | tests | 1,300-1,700 |
| Count-assertion updates (§6) | tests and scripts | ~60 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-<v>-filesystem.json` (D1 capture, with provenance) | evidence | generated |
| Release notes, acceptance, README, TOOL_AUDIT, matrix docs | docs | ~300 |

Runtime is below about 1,500 lines, so **one PR is recommended** (D9). If
the redaction or gateway work grows past the threshold, split as follows:
- **PR-A:** the optional reviewed tool mechanism, plus classification of all
  three names (nothing exposed; reports only);
- **PR-B:** the wrapper and its exposure.

**Protected paths Part 2 needs** (D10):
- `E/` runtime files listed above;
- `hass_mcp_engineering_beta/config.yaml`;
- release metadata through `scripts/promote_next_release.py --apply`.

No policy JSON, registry JSON, signed registry, workflow, `.release/`
declaration edit beyond the staged release, or stable-v1 change is needed.

### 8.4 Release

The release is the next beta after HAMCP-184. If 184 ships as
`2.4.1-beta.11`, this is **`2.4.1-beta.12`**, materialized in the PR before
Ready. The acceptance contract follows the beta.8 shape:
1. source and CI;
2. publication;
3. installed acceptance. This step:
   - keeps the stock 77-tool check;
   - has Josh enable the flag (already on in production);
   - verifies 88 tools;
   - runs one read canary per use case (`recorder`, a package key);
   - runs one refusal canary (`file=secrets.yaml`), which must report zero
     upstream calls in audit;
   - repeats the dashboard and power canaries from beta.8 step 5.

## 9. Findings that contradict the brief's assumptions

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | The three tools are in no policy, capture or registry contract; no exact descriptor is committed anywhere | policy JSONs and `docs/evidence/upstream-read-compatibility/ha-mcp-8.4.3/8.5.0.json` searched; HAMCP-187 §10.2 committed the doc only | D1 capture is a prerequisite |
| F2 | Ordinary policy entries break stock count, registry sha, advertised count and order fingerprint (whole-read quarantine), and invalidate signed entries | §5.1 | optional reviewed tool mechanism (§5.2) |
| F3 | The component version is independent of the ha-mcp version: production 2.2.1 ships in the 8.6.0 tree, while 8.5.0 bundles 2.2.0 and 8.4.3 bundles 2.1.3 | manifests | Engineering cannot attest the component; L1-L4 must not depend on 2.2.1-only denies |
| F4 | The filesystem flag is a beta sub-flag; production needs `ENABLE_BETA_FEATURES` | HAMCP-187 §3d | beta-tagged surface; disclosed |
| F5 | `secrets.yaml` **is** on the upstream allowlist (masked, but key names, size and mtime disclosed) | CC:267-279, :1248 | Engineering refuses it (L1/L2) |
| F6 | `!include` targets other than the four root files and allowlisted dirs are unreadable upstream, for example `sensors.yaml` or `templates/` | §1.3 | F002 coverage depends on the production layout (D5) |
| F7 | `yaml_path` addresses mapping keys only, with no list indices; list-root files are not addressable | CC:2386-2390 | automations/scenes excluded (D4) |
| F8 | No upstream size cap; whole-file read; unbounded glob fan-out | CC:1366; YR:456 | Engineering bounds only its output; disclosed |
| F9 | ha-mcp `redact_secrets` does not cover file content | HAMCP-187 :464-466 | Engineering L4 is the only content redaction |
| F10 | No PEM, key or certificate detection exists upstream **or in Engineering**; `*.pem`/`*.key`/`ssl/` have no deny rule upstream | CONST:75, :95; `E/sanitization.py` | new PEM scanner (L4) |
| F11 | Admin-configured extra directories grant read (and write) on arbitrary volumes, for example `/ssl` | §1.3 | Engineering never addresses them (L1); production configuration unknown |
| F12 | `!env_var` defaults are returned verbatim | `yaml_rt.py` | L4 redacts defaults |
| F13 | The upstream "Path not allowed" error discloses the effective allowlist | CC:2919 | fixed error codes (§4) |
| F14 | `!input` is not a registered tag (inferred tag loss) | `yaml_rt.py:66-77` | out of scope (blueprints excluded) |
| F15 | Adding a provider surface would change the family descriptors and every `family_admission` record | `E/compatibility_family.py:59-62, :411, :849` | per-tool explicit dispositions instead (D2) |
| F16 | With the flag on, backup and lifecycle whole-catalog validation already refuses (both surfaces `held` for 8.4.3 and 8.5.0, so no loss today) | `E/providers/operational_backup.py:326` | unchanged; noted for when those holds lift |

## 10. Owner decisions requested

| # | Decision | Recommendation |
|---|---|---|
| D1 | Authorize a disposable offline capture (HAMCP-187 §10.1 method, internal network, no live HA) of 8.4.3 and 8.5.0 with `ENABLE_BETA_FEATURES=true`, `HAMCP_ENABLE_FILESYSTEM_TOOLS=true`, YAML editing off, and the committed artifact plus provenance | Authorize; prerequisite for Part 2 |
| D2 | Admission mechanism: (a) optional reviewed tools with per-version explicit dispositions in the adapter module (no policy, registry or surface change), or (b) a new `yaml_reader` provider surface (every registry entry, family descriptor and family_admission record changes), or (c) ordinary policy entries (stock invariants, legacy pairs, signed-entry churn) | (a) |
| D3 | Classify `ha_list_files`/`ha_read_file` as reviewed `prohibited` optional names, and report optional accounting in a new field (stock `reviewed_tool_accounting_valid` meaning unchanged) | Accept |
| D4 | Readable set: `configuration.yaml` + `packages/` only (exclude `automations.yaml`, `scripts.yaml`, `scenes.yaml`) | Accept |
| D5 | Packages folder fixed to `packages/` (confirm the production layout; a custom folder needs an explicit literal) | Accept, and Josh confirms the layout |
| D6 | Core mapping: (a) basic REST+WS profiles with component routes disclosed as unattested, or (b) a new dormant profile needing signed sequence 13 | (a) |
| D7 | Bindings for 8.4.3 and 8.5.0 only; 8.6.0 `held` (HOLD), 8.7.0 future | Accept |
| D8 | Public name: keep `ha_config_get_yaml` or rename to `get_yaml_config` | Keep the upstream name |
| D9 | One PR, or the A/B split | One PR |
| D10 | Authorize Part 2 protected paths (§8.3) and `2.4.1-beta.12` release metadata after HAMCP-184 merges | Authorize after 184 merges |
| D11 | Never return raw `content` or comments (parsed only) in this release | Accept |
| D12 | Accept best-effort redaction of free-form configuration (unlabeled secret values cannot be recognized), disclosed in the description | Accept with disclosure |
| D13 | Analysis consumers (dependency, integrity) adopt the reader later, in a separate task | Later |

## 11. Non-actions in Part 1

- No production code, schema, policy, registry, signed-registry, HOLD,
  support-window, version, `.release/`, workflow or stable-v1 change.
- No upstream capture executed. No live Home Assistant, Supervisor, ha-mcp or
  Engineering access. No credentials or secrets read.
- HAMCP-184's branch and files are untouched.
- Draft PR only; not marked Ready, not merged, not released or deployed.
