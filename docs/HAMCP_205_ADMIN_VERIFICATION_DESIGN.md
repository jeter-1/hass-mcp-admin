# HAMCP-205: shared administrator verification for Ingress approval pages

Status: implemented on `agent/HAMCP-205-admin-verification` (draft). Absorbs
HAMCP-199. Owner decision (Josh): Option A. On every review and every decision,
look up the requesting user's role in Home Assistant and require an
administrator, failing closed when the lookup fails or is ambiguous. One shared
check covers every approval page, and the anonymous default approver is
removed.

## Part 1: what the pinned upstream sources establish

Authority follows ADR-021. Every claim below was read from source at the exact
commits listed; nothing was executed against a live system.

| Component | Version | Commit |
| --- | --- | --- |
| Home Assistant Core | 2026.10.1 | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` |
| Home Assistant Core | 2026.10.0 | `6a811d3359c7b2076dc9e1cf900843a129c044af` (cited files identical to 2026.10.1) |
| Supervisor | 2026.10.0 | `5e948a2545319e7031400666c6910fc172ebc954` |
| Supervisor | 2026.10.1 | `01b32d6d31125425260638f768980d35659c4874` |
| Supervisor | 2026.10.2 | `c9e000812b7131fa1f1b72b483555083929d7f83` |

The installed Supervisor version is **unknown**: the repository records the
supported Core versions (2026.9.2, 2026.10.0, 2026.10.1) but not the installed
Supervisor version. The design therefore assumes the weakest of these
Supervisor versions.

### 1. Headers that Ingress forwards about the user

- Supervisor sends exactly three user headers: `X-Remote-User-Id`,
  `X-Remote-User-Name` and `X-Remote-User-Display-Name`
  (`supervisor/const.py:100-102`; set in `supervisor/api/ingress.py`
  `_init_header`, 2026.10.0 lines 320-331 and 2026.10.2 lines 370-381).
- **No administrator flag is forwarded.** `ATTR_ADMIN` appears only in the
  panel listing (`api/ingress.py:104`, which is `app.panel_admin`), not in
  proxied request headers.
- Client-supplied copies of these headers are stripped before Supervisor adds
  its own (`api/ingress.py:335-350`).
  - The strip compares each header name against `istr(...)` constants.
  - Whether a lowercase client header survives that comparison is **inferred
    from source and not executed**. If it did survive, the add-on would see two
    values, or only the forged one when Supervisor has no session user to add.
  - Mitigation: Engineering refuses any request carrying more than one
    `X-Remote-User-Id` value.
  - Residual risk, upstream and unconfirmed: a single forged value when
    Supervisor resolves no user. Even then the forged ID must name a real Home
    Assistant administrator to pass the role lookup below, and the request must
    already arrive through an authenticated Ingress session from the Supervisor
    peer.

### 2. Whether a non-administrator can reach the add-on's Ingress URL

- **Supervisor 2026.10.0 and 2026.10.1:** yes. `panel_admin` only controls
  whether the sidebar panel is shown (`api/ingress.py:104`). The proxy handler
  checks the Ingress session but not the user's role.
- **Supervisor 2026.10.2:** no. The handler refuses non-administrators when
  `panel_admin` is set (`api/ingress.py:150-167`:
  `if app.panel_admin and (user is None or not user.is_admin): raise
  HTTPForbidden`). The user record is cached per Ingress session, so a role
  change may not be seen until a new session starts.
- Conclusion: neither panel visibility nor the Supervisor version is an
  authorization boundary that Engineering can rely on.

### 3. The Home Assistant API that reports administrator status

- Core's WebSocket command `config/auth/list` (`components/config/auth.py:22-33`)
  is administrator-only (`@websocket_api.require_admin`; the decorator in
  `components/websocket_api/decorators.py:54-66` raises `Unauthorized` unless
  `connection.user.is_admin`).
- It returns every user, unpaginated, through `_user_info`
  (`config/auth.py:165-187`). Each row contains `id`, `username`, `name`,
  `is_owner`, `is_active`, `local_only`, `system_generated`, `group_ids` and
  `credentials`. There is **no `is_admin` field**.
- Core's rule is `User.is_admin` (`auth/models.py:92-96`):
  `is_owner or (is_active and any(group.id == GROUP_ID_ADMIN))`, where
  `GROUP_ID_ADMIN = "system-admin"` (`auth/const.py:9`). Engineering applies
  this rule to the row whose `id` exactly matches the forwarded user ID.
- No narrower read-only endpoint returns another user's role. `auth/current_user`
  describes only the caller, which here is Engineering's own principal.

### 4. Whether Engineering's token may call it

- Engineering connects with `SUPERVISOR_TOKEN` to
  `ws://supervisor/core/websocket`. Supervisor's proxy accepts the add-on token
  when the add-on has `homeassistant_api` access (`api/proxy.py:300-334`). It
  then relays messages over its own upstream connection to Core
  (`api/proxy.py:213-222`), which is authenticated with Supervisor's Core access
  token (`homeassistant/api.py:160-205`).
- That upstream Core user is the Supervisor system user, which Core creates in
  the administrator group (`components/hassio/__init__.py:178-205`:
  `async_create_system_user(HASSIO_USER_NAME, group_ids=[GROUP_ID_ADMIN])`).
  `@require_admin` is therefore satisfied.
- The proxy blocks only `supervisor/` and `hassio/` message types
  (`api/proxy.py:45`, 55 and 357). `config/auth/list` is forwarded.
- The same authority could also reach `config/auth/create`, `update` and
  `delete`. Engineering's verifier can send only one fixed, data-free command
  (`AUTH_LIST_COMMAND = {"type": "config/auth/list"}`), and a source test pins
  that this is the module's only command type.

Result: a reliable source exists, so Part 1 did not stop.

## Design

### One verifier (`ha_mcp_engineering/approval_identity.py`)

- **`ingress_user_id(values)`** accepts exactly one `X-Remote-User-Id` value
  matching `^[A-Za-z0-9_-]{1,128}$`. Zero values, repeated values or a
  malformed value produce no identity.
- **`AdministratorVerifier.verify(user_id)`** performs one `config/auth/list`
  read per call:
  - The command is a fixed `MappingProxyType` constant; no request value can
    choose a different command.
  - The read is bounded: `SINGLE_READ` gives a 1 MB frame limit, the call has a
    5 s timeout, and at most 1,000 users are accepted.
  - It returns `administrator`, `not_administrator`, `lookup_failed` or
    `identity_missing`.
- **Failure handling:**
  - These results all return `lookup_failed`: any exception, a non-list result,
    a row with a non-string ID or wrong field types, and a user ID that matches
    zero rows or more than one.
  - System-generated accounts are never administrators for approval.
- **Nothing is cached** between requests, so a role change in Home Assistant
  applies to the next request, including between a review and its decision.
- **Data kept:** only the requester's ID and the verdict. The user list (names,
  usernames, credential types) is never stored, rendered or logged.

### One gate for every approval page (`approval_web.py`)

`_require_administrator(request, surface)` is the first statement of all ten
routes:

| Surface | Route |
| --- | --- |
| `plan_inbox` | `GET /` |
| `plan_review` | `GET /plans/{id}` |
| `plan_approve` / `plan_reject` | `POST /plans/{id}/approve` and `/reject` (via `_decide`) |
| `f3_inbox` | `GET /f3` |
| `f3_review` | `GET /f3/{child}` |
| `f3_reconcile` | `POST /f3/{child}/reconcile` |
| `typed_inbox` | `GET /typed` |
| `typed_review` | `GET /typed/{kind}/{task}` |
| `typed_release` | `POST /typed/{kind}/{task}/release` |

The gate works in this order:

1. **Ingress origin check (unchanged).** The request must come from Supervisor
   peer `172.30.32.2` with a well-formed `X-Ingress-Path`; otherwise the result
   is the existing 403 "Forbidden". This check does not count as an identity
   refusal and does not perform a lookup.
2. **Identity check.** No single well-formed user ID gives
   **"Approver identity missing"** (403), without a lookup. This absorbs
   HAMCP-199.
3. **Role lookup.**
   - Not an administrator gives **"Administrator required"** (403).
   - A failed, raising, unknown or unconfigured lookup gives **"Could not
     verify administrator role"** (503).
4. **Success.** Only an administrator verdict continues, with principal
   `home_assistant_admin_ingress:<user id>`. The approval, F3 reconciliation
   and typed hold-release records store that principal.

Each refusal:

- happens before any governance, F3, typed-service or device access;
- issues no CSRF token;
- writes one audit entry `ingress_approval_identity_refused`, carrying the
  surface, method and reason. `caller_id` is set only for a well-formed ID,
  and the Ingress path is never recorded;
- increments the health counter.

An audit write failure does not turn a refusal into an acceptance.

### Health

`get_server_health` gains `approval_identity`, which contains:

- `administrator_role_source`;
- `refused_attempts`;
- `refused_by_reason` (`identity_missing`, `not_administrator`,
  `lookup_failed`);
- `last_refusal_reason`;
- `counter_semantics: cumulative_process_events`.

The counters are counts only, with no user IDs.

### Wiring

- `application.create_approval_application(settings)` installs the real
  verifier on a `HomeAssistantWebSocketClient(settings)`.
- The Ingress web factory defaults to no verifier, which fails closed with
  "Could not verify administrator role" on every page.

## Deviations and open items for the reviewer and Josh

1. **No HAMCP-200 Route.** The brief asked for the lookup to use a registered
   HAMCP-200 Route. That registry (`clients/paths.py`) covers REST paths only,
   and the role exists only over WebSocket. The lookup therefore uses one fixed,
   data-free WebSocket command constant, pinned by a source test. Adding a
   WebSocket command registry is out of scope here.
2. **Governance still has the anonymous fallback, but it is unreachable.**
   - `governance/service.py` still contains
     `approver_principal or DEFAULT_APPROVER_PRINCIPAL` in
     `decide_external_approval`.
   - That method is called only from the Ingress app, which now always passes a
     verified principal, so the fallback cannot be reached from any approval
     page.
   - Removing it inside governance was deferred for three reasons:
     - the file is protected and outside this brief's named files;
     - HAMCP-191 (#280) has open changes to the same file, in other regions;
     - HAMCP-184's tests create historical anonymous records through that path,
       and replacing them must respect the persisted-fixture provenance rules.
   - Historical anonymous standard-class approvals keep their HAMCP-184
     eligibility (`_external_principal_authority_valid`). Josh decides whether
     they should be made ineligible.
3. **Lowercase header forgery.** This upstream residual risk is inferred, not
   executed (see Part 1, section 1).
4. **Role lookup cost.** Every page view and decision makes one Core WebSocket
   round trip (connect, authenticate, list). This is acceptable for a
   human-paced approval panel.

## Tests and mutations

`tests/test_hamcp205_admin_verification.py` (34 tests) covers:

- an administrator allowed on every page, with the verified principal stored;
- a non-administrator, missing identity, malformed identity, repeated identity
  header, lookup failure, verifier exception or unknown verdict, and an
  unconfigured verifier each refused on all ten routes, with no governance,
  typed-service, CSRF or device side effects;
- the role revoked between review and decision, refused at decision for plan,
  F3 and typed release, while the restored administrator's untouched form
  still works;
- non-Ingress peers and paths refused before any lookup;
- the real verifier against a synthetic transport: Core's rule, fail-closed
  parsing, timeout, the exact command and `SINGLE_READ`, and no caching;
- one audit entry without secrets;
- the health counter;
- production wiring.

Existing approval suites now inject an administrator verifier from
`tests/approval_identity_support.py`. The beta25 HAMCP-199 test now expects a
refusal.

Mutations (all killed; the runner copies the worktree per mutant):

- the role check skipped on each of the ten pages;
- trusting the identity header instead of the lookup;
- accepting the default principal again;
- caching the role in the verifier;
- caching the role in the gate.
