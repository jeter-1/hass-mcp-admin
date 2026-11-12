# S3: vendored code and dependencies

Identities, method and claim marks are in `README.md`. Upstream paths are
relative to `src/ha_mcp/` unless stated. "A" is v9.1.2, "B" is v9.2.0.

This file covers what is left of the dependency question after the
`error_handling.py` change was read earlier: the rest of the `_vendor` diff,
the locked dependency versions, and the Python base of the standalone image.
Packages that are not in the upstream repository (pydantic, attrs, the HTTP
client) and Home Assistant Core were not read, and no package source was
fetched. Those are settled by observation, as noted at each point.

## 1. The `_vendor` tree

`src/ha_mcp/_vendor/` changed in 33 files between the tags (+524 -315), from
two commits: `97b59d5d` (#3054, websockets) and `e3a069df` (#3047,
fastmcp-slim). **[source]**

`_vendor/requirements.txt` **[source]**:

| Package | A | B |
|---|---|---|
| `websockets` | 17.2 | 17.4 |
| `fastmcp-slim` | 4.1.4 | 4.1.6 |
| `mcp` | 2.3.0 | 2.3.0 |
| `mcp-types` | 2.3.0 | 2.3.0 |

`_vendor/mcp` (tree `744aa339`) and `_vendor/mcp_types` (tree `1639fd1b`) are
the same tree at both tags. The MCP protocol library ha-mcp serves with did not
change. **[source]**

### 1a. fastmcp-slim 4.1.4 to 4.1.6

Fifteen files changed: twelve code modules and three vendoring records
(`MANIFEST.sha256`, `VENDORED`, the version in `__init__.py`). The code
modules **[source]**:

| File | Change |
|---|---|
| `server/middleware/error_handling.py` | `ValidationError` maps to JSON-RPC code -32602 (read earlier) |
| `prompts/function_prompt.py` | missing prompt arguments raise `ValidationError` |
| `utilities/json_schema_type.py` | `_create_enum` uses strict equality |
| `client/mixins/tools.py` | new `_wrapped_result_schema` |
| `server/providers/fastmcp_provider.py` | one line |
| `server/providers/openapi/components.py`, `server/transforms/namespace.py` | provider and transform changes |
| `cli/client.py`, `cli/discovery.py`, `client/client.py`, `client/group.py`, `client/transports/stdio.py` | client and command-line side |

Whether ha-mcp uses any of these was checked by searching the non-vendored
source at B for imports **[source]**: no module outside `_vendor` imports
`json_schema_type`, the openapi provider, `fastmcp.client`, `fastmcp.cli`,
`Namespace`, `function_prompt`, `fastmcp_provider` or
`ErrorHandlingMiddleware`. ha-mcp's own middleware list is built at
`server.py:235-316` and `:682-800`.

Conclusions **[source, inference]**:

- No changed fastmcp module was found on ha-mcp's tool registration or tool
  call path by reading. The vendor bump by itself should not change a tool's
  input schema or the shape of a tool result.
- The -32602 mapping applies to `ErrorHandlingMiddleware`, which ha-mcp does
  not install. The earlier concern that argument-validation failures change
  their error code is not supported by the import search.

Both are to be confirmed from the captured descriptors and from a captured
validation failure on each image. **[capture]** (P2)

### 1b. websockets 17.2 to 17.4

Sixteen files changed. ha-mcp imports the asyncio client only
(`client/websocket_client.py:28-29`, `tools/tools_addons.py:23-25`,
`hacs_auto_refresh.py:39`). **[source]**

On that path **[source]**:

- New exception `TooManyFragments`.
- `asyncio/messages.py` adds
  `MAX_FRAGMENTS = int(os.environ.get("WEBSOCKETS_MAX_FRAGMENTS", "4096"))`.
- `asyncio/connection.py` fails a message with more fragments than that with
  `CloseCode.MESSAGE_TOO_BIG` (close code 1009, reason "too many fragments").
- `recv_exc` is renamed `connection_exc`, and a new `close_exc` is added.
- `http11.py` validates the status line, `Content-Length` and chunk sizes more
  strictly during the opening handshake.
- `headers.py` and `asyncio/server.py` change basic-auth handling (server
  side; ha-mcp is the client on this socket).

`legacy/`, `sync/` and `trio/` also changed. They are not on the imported path.
**[source]**

What this means for the lane **[source, inference]**:

- There is one new failure mode on the ha-mcp-to-Core socket: a single message
  that Core sends in more than 4096 fragments now closes the connection. At
  17.2 there was no such limit.
- Whether Core fragments large replies at all was not read; Core source is not
  in the clone. If Core sends each reply as one frame, the limit is never
  reached.
- The stricter handshake parsing only matters if Core's upgrade response is
  malformed in a way 17.2 tolerated.

To be settled by observation: one large socket reply (for example a full
entity-registry list or a long history window) on the 9.2.0 image, with the
socket's close code recorded, and a normal handshake on both images.
**[capture]** (P9)

## 2. Locked dependency versions

`uv.lock` at each tag was read as data with `tomllib` (`python3 -I`); nothing
was resolved or installed. **[source]**

- `requires-python` is `>=3.13, <3.15` at both tags.
- The lock grows from 129 to 223 packages; 118 entries are changed or added.
  Most additions are Home Assistant test dependencies (`homeassistant
  2026.11.1`, `pytest-homeassistant 2026.11.1` and what they pull in) and are
  not installed into the runtime image (see section 3).

Runtime-relevant changes **[source]**:

| Package | A | B | Direction |
|---|---|---|---|
| `pydantic` | 2.13.5 | 2.14.1 | up |
| `pydantic-core` | 2.46.5 | 2.50.1 | up |
| `pydantic-settings` | 2.16.1 | 2.16.2 | up |
| `attrs` | 25.3.0 | 26.1.0 | up (major) |
| `httpx2` | 2.16.0 | 2.14.0 | down |
| `httpcore2` | 2.16.0 | 2.14.0 | down |
| `truststore` | 0.10.5 | 0.10.4 | down |
| `certifi` | 2026.10.19 | 2026.10.23 | up |
| `authlib` | 1.9.1 | 1.9.2 | up |
| `cyclopts` | 5.3.0 | 5.3.3 | up |
| `pyjwt` | 2.15.0 | 2.15.1 | up |
| `python-multipart` | 0.0.32 | 0.0.33 | up |
| `opentelemetry-api` | 1.45.1 | 1.45.2 | up |
| `platformdirs` | 4.13.5 | 4.14.3 | up |
| `propcache` | 0.5.2 | 0.5.4 | up |
| `tzdata` | 2026.5 | 2026.6 | up |
| `websockets` (lock entry) | 17.3 | 17.4 | up |

Notes **[source]**:

- The `websockets` lock entry is not the copy ha-mcp imports. A comment in
  `pyproject.toml` says websockets is deliberately absent from the runtime
  dependencies, and ha-mcp imports only `_vendor/websockets` (section 1b).
- Three packages move down between the tags (`httpx2`, `httpcore2`,
  `truststore`). That is what the two lock files say; the reason was not
  looked for.
- `pyproject.toml` at B gives the runtime ranges `httpx[socks]>=0.28.1,<0.29`,
  `pydantic[email]>=2.13.4,<3`, `python-dotenv>=1.2.4,<2`,
  `truststore>=0.10.4,<0.11`, `cryptography>=48.0.0,<51`,
  `pydantic-monty==0.0.18` and `httpx2>=2.10.0`. The mypy `python_version`
  moves from 3.13 to 3.14.

What this means **[source, inference]**:

- pydantic and pydantic-core generate the tool input schemas. A minor-version
  step in both is the most likely source of a descriptor change that has no
  matching change in ha-mcp's own source. A changed input-schema fingerprint
  at B for a tool whose source blob is unchanged should be read against this
  first.
- The HTTP client stack that carries every REST call to Core is a different
  version at B. Timeouts, connection reuse and error text can differ without
  any ha-mcp source change.
- `attrs` crosses a major version. Where it is used at run time was not
  traced.

None of this was read in the packages themselves. It is settled by observation:
installed versions read back from each image, descriptors compared, and REST
and error behaviour compared in replay. **[capture]** (P1, P2, P5, P9)

## 3. The standalone image build

`Dockerfile` at the repository root **[source]**:

| Item | A | B |
|---|---|---|
| Runtime base | `public.ecr.aws/docker/library/python:3.13-slim@sha256:ae14d4ce...` | `python:3.14-slim@sha256:9aaa4740...` (same registry path) |
| Builder | `ghcr.io/astral-sh/uv:0.13.6-python3.13-trixie-slim@sha256:e586c1c6...` | `uv:0.13.7-python3.14-trixie-slim@sha256:e80c05a3...` |

Dependencies are installed with
`uv sync --locked --no-install-project --no-dev` (B `:24`) and
`uv sync --locked --no-dev` (B `:30`). **[source]**

So **[source, inference]**:

- The standalone 9.2.0 image runs Python 3.14 where the 9.1.2 image runs
  Python 3.13. This is an interpreter change under an otherwise small release
  and is not visible in the ha-mcp source diff.
- With `--locked --no-dev`, the image carries the locked versions in section 2
  and not the test dependencies.

Both are to be read back from the pulled images (Python version, installed
package versions, base digest in the image history). **[capture]** (P1)

Six Dockerfiles changed between the tags: the root `Dockerfile` (6 lines),
`homeassistant-addon/Dockerfile` (6), `homeassistant-addon-dev/Dockerfile` (4),
`homeassistant-addon-webhook-proxy/Dockerfile` (5),
`homeassistant-addon-webhook-proxy-dev/Dockerfile` (2) and
`tests/haos_image_build/screenshot_engine_mock/Dockerfile` (4). Only the root
`Dockerfile` was read; the other five are known by their change counts only.
**[source]** Which Dockerfile produced each pinned image is part of the
provenance check in the capture step. **[capture]** (S4)

For the component path the interpreter and the shared packages are Core's, not
the image's (see `s2-source-findings.md`, section (b)). **[capture]** (P1, P5)

## 4. Not read, and how each is settled

| Item | Why not read | Settled by |
|---|---|---|
| pydantic, pydantic-core, attrs source | not in the upstream clone; no package source fetched | P1 (installed versions), P2 (descriptors) |
| HTTP client stack source | same | P5, P9 (REST behaviour in replay) |
| Core's websocket framing | Core source not in the clone | P9 (large reply, close code) |
| websockets `sync/`, `trio/`, `legacy/` | changed, not on the imported path | not needed unless P1 shows an import |
| five other Dockerfiles | change counts only | S4 provenance check |
| where `attrs` is used at run time | not traced | P1, P5 |

## Summary

| Finding | Mark |
|---|---|
| `_vendor/mcp` and `_vendor/mcp_types` unchanged | source |
| no changed fastmcp module found on the tool registration or call path | source, inference; confirm by P2 |
| the -32602 mapping is in a middleware ha-mcp does not install | source; confirm by P2 |
| websockets 17.4 adds a 4096-fragment limit that closes the socket with code 1009 | source; reach by P9 |
| pydantic 2.13.5 to 2.14.1, pydantic-core 2.46.5 to 2.50.1, attrs 25.3.0 to 26.1.0 | source; effect by P1, P2 |
| `httpx2`, `httpcore2` and `truststore` move down | source; effect by P5, P9 |
| standalone image moves from Python 3.13 to 3.14 | source; confirm by P1 |
