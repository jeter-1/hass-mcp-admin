# Engineering 2.4.1-beta.14 release notes

Engineering 2.4.1-beta.14 adds one read-only delegated tool,
**`ha_config_get_yaml`**, which reads one mapping-key path of
`configuration.yaml` through ha-mcp (HAMCP-162). It builds on beta.13
(HAMCP-164).

The approved design is
[`HAMCP_162_YAML_READER_DESIGN.md`](HAMCP_162_YAML_READER_DESIGN.md). The
D1 capture evidence is in [`evidence/hamcp162/`](evidence/hamcp162/README.md).
Josh's 2026-10-16 decisions K1–K11 apply.

No signed registry, HOLD, support window, Core profile, probe fingerprint,
persisted format, workflow or stable-v1 change is included. No write,
reload, restart, deletion or fallback path is reachable, and Engineering
makes no service call. The one accepted exception is the K1 caller-token
bootstrap: the first call may make the ha-mcp component call
`POST ha_mcp_tools/get_caller_token` and persist the admin caller token (see
**K1 dependency** below). Only `ha_config_get_yaml` is exposed. `ha_list_files` and `ha_read_file` stay
reviewed and never exposed.

The healthy catalog grows from 87 to **88 tools: 59 native and 29 delegated
reads**.

**What it reads.**
- One dotted mapping-key path (at most eight segments) of
  `configuration.yaml` only.
- The `file` argument may be omitted or be exactly `configuration.yaml`.
  Engineering always sends the fixed upstream shape
  `{yaml_path, file: "configuration.yaml", include_content: false,
  include_parsed: true}`. Caller keys are never forwarded.
- Every other file, including `secrets.yaml`, `packages/`, `.storage/`,
  case variants and `configuration.yml`, is refused before route
  acquisition, Core acquisition and any upstream call (zero upstream calls).
- A path naming a credential-shaped key is refused the same way. Read the
  parent instead.

**What it returns.** The result is a bounded envelope with the following
fields:
- `file`, `yaml_path`, `source: "disk"`, `found` and `value`;
- `completeness`;
- `limitations`;
- `redaction` (count and categories).

Every one of these fields passes the redaction pipeline (H1): `value`
through L4 and L5, and every other field, including include and path
descriptions, limitations, warnings, metadata and error details, through a
final shared-sanitizer pass with the configured known secrets. Key paths in
`limitations` and the `yaml_path` echo use the key pipeline and end in `*` at
the first segment the value tree would redact.

**Truncation runs before redaction (H2, owner direction 2026-10-16).** The
4,000-node cap applies to the decoded upstream tree first; redaction then
judges exactly the kept shapes. This supersedes design section 6.3 ("redaction
runs before truncation"). Nodes beyond the cap are never returned, so they are
neither examined nor counted. Reference markers are trusted only by the
identity of a marker the same read minted. A mapping shaped like a rendered
marker, from upstream data or from truncation, is withheld as
`reference_marker_collision`.

**How values are protected.**
- `!secret NAME` and `!env_var NAME` become typed reference-name markers,
  `{"secret_ref": NAME}` and `{"env_var_ref": NAME}`. Secret values and
  `!env_var` defaults are never returned; a default is recorded only as
  `"default_redacted": true`.
- Values are redacted whole under credential-shaped keys (absolute canonical
  path, including the requested prefix). This covers containers, not only
  leaves.
- Values under connection-shaped keys are redacted whole, except one strict
  safe SQLite shape (rules B and R11).
- Credential text anywhere is redacted whole: userinfo URLs, the 13
  credential words, and the `pass`/`key` delimited-token rule C′3.
- Keys are canonicalized before they are judged: percent-decoding up to
  four rounds, NFKC and casefold (rules A and R10). Values that cannot be
  canonicalized are redacted as `encoding`.
- PEM blocks, opaque blobs, JWTs and known secrets are redacted.
- `!include` tags are reported as limitations and never followed.
- Redaction is best effort and over-redacts. For example, most URLs, and any
  text mentioning a token, auth or session, are redacted.

**Disclosures on every successful read.**
- **K7:** the warning that values are ha-mcp's YAML 1.2 parse and may
  differ from Home Assistant's YAML 1.1 interpretation (for example
  `on`/`off`/`yes`/`no` and numbers with leading zeros).
- **K5:** non-string keys are converted upstream, and keys with the same
  text collapse to one.
- **D12:** a credential under an innocent key, with no credential word in
  its text, is not recognized.

The quoted-reference residual is accepted and disclosed: a quoted string that
looks exactly like `!secret name` is shown as a reference.

**K1 dependency.** The first call may make the ha-mcp component fetch an
admin caller token (`POST ha_mcp_tools/get_caller_token`) and persist it.
This is an accepted dependency, disclosed in the tool description.

**Fixed failure codes.** Upstream text is never forwarded.

| Code | Meaning |
| --- | --- |
| `yaml_read_arguments_invalid` | Pre-dispatch refusal: arguments |
| `yaml_file_not_allowed` | Pre-dispatch refusal: file |
| `yaml_path_invalid` | Pre-dispatch refusal: path syntax |
| `yaml_path_sensitive` | Pre-dispatch refusal: credential-shaped path |
| `yaml_file_not_allowed_upstream` | The component refused the path |
| `yaml_file_not_found` | `configuration.yaml` was not found |
| `yaml_parse_error` | Parse error, with integer line and column only |
| `yaml_upstream_validation_or_component_unavailable` | K2: "upstream validation failed or component unavailable". It never claims the component is definitely unavailable. |
| `yaml_read_failed` | "upstream read failed": non-UTF-8 files, oversized integers and other generic 500s (K4) |
| `yaml_read_response_identity_invalid` | The response did not match the reviewed shape |

**Admission.** The tool is optional and reviewed:
- It is admitted only when its complete descriptor (top-level fields plus
  the runtime-contract fingerprint) exactly matches the explicit binding for
  ha-mcp **8.4.3** or **8.5.0**.
- Every registry version has an explicit disposition. 8.6.0 and all older
  versions are `held`, and nothing is admitted by default.
- It is withheld under a signed-registry selection, under `read_gateway:
  held`, and when its Core requirements are unavailable.
- It is excluded from the stock order fingerprint, from stock admission and
  aggregate health, and from signed selections.
- Drift, removal or a disposition change between admission and dispatch
  causes zero dispatch.
- Its Core mapping is `core.basic_rest_read` plus `core.basic_websocket_read`
  (HAMCP-164's gate applies).
- `server_info` reports the following fields:
  - `optional_tools`;
  - `optional_reviewed_accounting_valid`;
  - `optional_sibling_drift`.

**Upstream catalog accounting (K9).** The exact catalog grows from 78 to 81
tools on ha-mcp 8.4.3, and from 77 to 80 on 8.5.0. The three added tools are
`ha_config_get_yaml`, `ha_list_files` and `ha_read_file`. A stock catalog
without them is unchanged.

**Audit.** Refusals and reads use the existing generic delegated audit:
error code plus argument field names, never values. The design's per-path
audit detail is not included (`routing.py` was not in scope). This is more
conservative than the design.

Downgrade: beta.13 reads every beta.14 record, since no persisted format
changes. A downgraded server no longer exposes `ha_config_get_yaml`, and the
catalog returns to 87.

Follow the [beta.14 acceptance contract](V2_4_1_BETA14_ACCEPTANCE.md).
