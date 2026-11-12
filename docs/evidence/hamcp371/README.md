# HAMCP-371 ha-mcp 9.2.0 admission: source-read evidence

Read-only source findings for the ha-mcp 9.1.2 to 9.2.0 admission lane, written
2026-11-11 (Central). This directory holds the reading half only. Nothing here
admits a release, binds a provider or changes a reviewed value.

## What was compared

| Item | Identity |
|---|---|
| Upstream repository | `https://github.com/homeassistant-ai/ha-mcp.git` (existing local clone; no fetch in this step) |
| v9.1.2 ("A") | commit `bab615dfc90cc510f5e19a9118707d0fae03a37c`, tree `85d0ea3c9377fb8d71cc51e7fe45e5052be455f6` |
| v9.2.0 ("B") | commit `51e3aac788482a081807591cd5d58d07dc4e540b`, tree `377909bd56f80aea959c0eca00014c489febd4c8` |
| Commits A..B | 81 |
| Engineering source | `origin/release/2.6.0` at `aeb3b314dd180592874243c9ae370a63f0622c2b` |

Upstream paths below are relative to `src/ha_mcp/` unless they start with
`custom_components/` or name a repository-root file. `P` stands for
`hass_mcp_engineering_beta/ha_mcp_engineering` in the Engineering source.
"A `:n`" and "B `:n`" are line numbers in the file at that tag.

## Method

- Upstream source was treated as untrusted data. It was read with git plumbing
  only (`git show`, `git diff`, `git rev-parse`, `git grep`, `git log`) from the
  object database. Nothing was checked out, imported, installed or executed
  from the clone.
- "Unchanged" for a file means the blob identity is equal at both tags
  (`git rev-parse A:path B:path`). "Unchanged" for a region means the lines
  were compared and are byte-identical.
- The transport table in `s2-source-findings.md` comes from a static tracer
  (`trace/trace_reads.py`) that parses source text with the Python `ast`
  module, run with `python3 -I`. Its raw output for each tag is in `trace/`.
- No image was pulled, no container was started, no network request was made,
  and no Home Assistant, deployed MCP endpoint, credential or secret was used.
- No protected path was edited. The Engineering source was read only.

## Claim marking

Every finding carries one of two marks:

- **[source]** read from source at the cited file and line. It states what the
  code says, not what a running peer does.
- **[capture]** to be confirmed by capture or observation in a later step. The
  step is named: P2 (descriptor and catalog fingerprints from the pinned
  images), P6 (write payloads against a disposable Core), P9 (socket and
  transport observations), or P1 and P5 (dependency behaviour).

A statement that follows from source by reasoning rather than by direct reading
is marked **[source, inference]**.

## Files

| File | Step | Content |
|---|---|---|
| `sw-write-providers.md` | SW, source half | #3050 first, then one finding per governed write provider (fan, power, helper value, dashboard) |
| `s2-source-findings.md` | S2 | transports per admitted read, component manifest, zone rows and `friendly_name`, redaction default and the `secret` rule |
| `s3-vendored-and-dependencies.md` | S3 | the `_vendor` diff, locked dependency versions, what was not read |
| `trace/trace_reads.py` | S2 | the static tracer |
| `trace/trace-v9.1.2.txt`, `trace/trace-v9.2.0.txt` | S2 | raw tracer output for the 27 admitted reads at each tag |
| `trace/trace-mixins-v9.1.2.txt`, `trace/trace-mixins-v9.2.0.txt` | S2 | raw tracer output for the methods reached through mixins and thin wrappers |

## Tracer limits

The tracer follows calls to functions defined in the same module, functions
imported with `from ... import`, and `client.<method>` calls into
`client/rest_client.py`. It records a REST call where a literal method and path
reach `_request`, `_raw_request` or `guarded_request`, and a websocket call
where a literal message type reaches `send_websocket_message` or
`send_command`. It does not follow:

- methods reached through `self.` on a mixin (listed as `unresolved` in the
  raw output). For `ha_get_overview`, `ha_get_state` and `ha_search` the mixin
  methods were traced separately from `tools/search/overview.py`,
  `tools/search/state.py`, `tools/search/component.py` and
  `tools/search/legacy.py`, and the table includes those results;
- calls on other objects (for example `self._smart_tools.get_system_overview`);
- message types or paths built at run time (shown as `<dynamic>`);
- thin registration wrappers whose body calls a method on another object
  (`ha_eval_template`, `ha_get_app`); the wrapped methods were traced
  separately.

A multi-mode tool is traced as a whole, so the table shows what the tool's code
can reach, not what one admitted call does. The per-call set is settled by
observation in P9.

## Not done here

- No descriptor, catalog or error-shape fingerprint was computed. Those need
  the pinned images (S4, P2).
- Skills content was not read. It is hashed from the two pinned images in S4.
- Pydantic, attrs, the HTTP client and Home Assistant Core source are not in
  the clone and were not read. No package source was fetched.
- S4, S5 and the later groups were not started.
