# Engineering 2.4.1-beta.43 release notes

Engineering 2.4.1-beta.43 relaxes one rule of the host journal screen, as
proven by a live calibration (HAMCP-234 step 2). It follows 2.4.1-beta.42
and includes it. There is no dependency, add-on configuration, input schema
or tool change.

Acceptance: [`V2_4_1_BETA43_ACCEPTANCE.md`](V2_4_1_BETA43_ACCEPTANCE.md).

## Why

On 2026-10-27, a live beta.42 calibration of `addon:core_matter_server`
scanned 40 lines and withheld 37 of them. Every one was withheld as
`user_pass_host.squashed`.

These are ordinary matter.js lines. They start with a colon-rich timestamp
(`17:11:46.221`) and later name a peer or session with `@`
(`secure#2b71@peer@1:5a3c`). The screen's squashed form removes all
whitespace. That joins the timestamp's `:` and the later `@` into
`user:password@host`, a shape no single word of the line has.

## The change

Owner decision (Josh, 2026-10-27), **in the host journal path only**:

- **`user_pass_host` is read as one contiguous token.** It is checked in the
  normal form only, with no whitespace between the user, `:`, the password,
  `@` and the host. It is no longer checked in the squashed form there.
  A contiguous `user:pass@host` is still withheld (`user_pass_host`), and
  so is any `scheme://user:pass@host` (`credential_url`).
- **Decoding never splits the token** (owner clarification, Josh,
  2026-10-28, from the independent review). The token is also checked in
  the text as given and after each round of its percent-decode, so a
  `%20`, `%09` or `%0A` inside a password stays one token. Example: a
  logged proxy setting `{"proxy": "user:pass%20more@proxy:8080"}`, which a
  proxy client decodes and uses, is withheld.
- **Every other rule is unchanged, in both forms:**
  - credential URLs and connection URIs;
  - `password=` fields;
  - the labelled keywords (`password`, `token`, `secret`, `api_key`, …),
    `Bearer` and mosquitto `-P`/`--pw`;
  - known secrets, normal and split;
  - PEM private-key headers;
  - invisible and control characters;
  - the value shapes.
- **Same shared screen.** This is an option of the same
  `credentials.screen`, which the journal sets (`contiguous_user_pass_host`),
  not a separate screen. The configuration-definitions (HAMCP-132) and trace
  (HAMCP-225) paths do not set it and decide exactly as before. Their suites
  pass unchanged.
- **Rule ids.** The journal can no longer report `user_pass_host.squashed`,
  so a calibration's `rule_ids` lists 44 ids instead of 45.

### Before and after, on a constructed corpus

The corpus has 33 benign lines. They are shaped like the live sample, the
Supervisor and Python logging; none is captured live data, and none holds a
credential. See `docs/evidence/hamcp234-step2/corpus-report.json`, a
generated file that a test keeps equal to its computation:

| Corpus | beta.42 shown / withheld | beta.43 shown / withheld | Still withheld by |
|---|---|---|---|
| matter.js (19) | 2 / 17 | 18 / 1 | `credential_url` (1) |
| Supervisor (8) | 6 / 2 | 6 / 2 | `labelled_keyword:token` (1), `user_pass_host` (1) |
| Python logging (6) | 4 / 2 | 6 / 0 | none |

In beta.42, 18 lines were withheld by `user_pass_host.squashed` alone; all
18 are now shown. No other rule's count changes. The correction that reads
the token before decoding too changes no line of this corpus (the report is
byte-identical before and after it).

## Disclosed cost and residuals

- **Accepted cost.** In the journal, a scheme-less `user:pass@host` with
  literal whitespace inside it (`user: pass@host`) is now shown, unless
  another rule meets it. Some clients accept such a value: Python's
  `urllib.request` proxy parser takes a literal space inside a password
  (verified offline). Percent-encoded whitespace is withheld.
- **Still withheld (false positives remain):**
  - a contiguous `name:tag@sha256:…` image reference, which is
    `user_pass_host` in the normal form;
  - a line with `://` and a later `@`, which is `credential_url`;
  - a line with a labelled keyword such as `token`.

  Each of these needs its own owner decision.

## Calibration reachability

`calibration` was already in the served `get_incident_logs` schema in
beta.42, and no source defect was found. In process, the production
application's `tools/list` advertises it as an optional boolean (default
`false`, `additionalProperties: false`). The schema the beta.42 endpoint
served to an MCP client on 2026-10-27 also included it; that was a schema
read only, with no tool call. The missing parameter the live check saw was
most likely a client schema cached before the update. That cause is not
verified.

This release adds regression tests:
- one reads the served `tools/list` of the production application;
- one makes a `calibration: true` call through the authenticated MCP gateway
  and checks that it returns counts.

## Compatibility

- No input schema, tool, routing, provider, policy, Core gate, telemetry,
  signed profile, probe fingerprint or signed registry change.
- What the journal shows changes only for text that the squashed
  `user_pass_host` alone withheld.
- No persisted format, workflow, Dockerfile, dependency, Supervisor role,
  add-on option or stable-v1 change.

**Protected runtime files.** The HAMCP-234 step 2 task authorized this
scoped change:
- `configuration_definitions/credentials.py`, the option;
- `incident_logs/host_journal.py`, the journal's use of it and its rule ids;
- the version.

## Rollback

Reinstall the 2.4.1-beta.42 artifact. No stored format changes.
