# Engineering 2.4.1-beta.27 acceptance

This contract covers HAMCP-132 slice 1, definition visibility. It adds two
read-only tools, `get_recorder_settings` and `get_unregistered_entities`, and
follows 2.4.1-beta.26 (HAMCP-217, the certifi 2026.10.19 dependency
security release), which builds on 2.4.1-beta.25 (HAMCP-212).

See also:
- Release notes:
  [`V2_4_1_BETA27_RELEASE_NOTES.md`](V2_4_1_BETA27_RELEASE_NOTES.md).
- Design and Part 1 verification against Core 2026.10.1:
  [`HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`](HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md)
  (draft PR #291).
- Implementation notes and deviations:
  [`HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md`](HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md).

**Release target.** `2.4.1-beta.27`. It is staged by one owner-authorized run
of `scripts/promote_next_release.py --apply` on a base that already carries
2.4.1-beta.26. The run consumes the declaration. HAMCP-217 took beta.26
first, so this PR merged that base and took the next free beta. If another
release takes beta.27 first, this PR merges that base again and takes the
next free beta.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's HAMCP-132 objective and owner decisions (i)–(vii) of 2026-10-20
  (design section 8);
- `2.4.1-beta.27` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **New sources.** Core WebSocket `recorder/info`,
  `recorder/entity_options/get` (admin, read-only),
  `config/entity_registry/list`, `entity/source` and `search/related` with
  `item_type: entity`, plus REST `GET /api/states`. All run under the
  existing administrative token, with no new permission (design section 2).
- **YAML reader.** The admitted YAML reader is used through the new gateway
  `yaml_config_reader`. It reads exactly three fixed paths, with its L1–L4
  pipeline, Core gate and response seal unchanged, and with no fallback.
- **Batch allowlist.** Exactly two more fixed shapes, with a strict entity-ID
  pattern for each. There is zero new write, purge, service call, reload,
  flow, fallback or forwarding reachability.
- **`db_url` and references.** `db_url` and every `!secret` or `!env_var`
  reference are withheld before sanitization. The projection is a positive
  allowlist, and unrecognized keys are counted only.
- **One sanitized exit point.** Each tool assembles its complete envelope
  first: report, omissions, warnings, limitations and every identifier. It
  then runs the strict sanitizer once on it, with the known secrets. Nothing
  is appended afterwards. A redaction makes the report partial (HAMCP-191).
  This is the correction for review finding H1 on `de1433f`.
- **YAML 1.1 readings.** A value whose YAML 1.1 readings change Core's
  verdict is `parser_dependent`. `fails_core_schema` is reported only when
  every reading fails. This is the correction for review finding M1 and
  deviation 6. Under the owner decisions of 2026-10-20:
  - a plain integer the reader returns as a number is definite, with the
    standing `coverage.yaml_numeric_spelling` disclosure that the spelling
    is not observed (`010` would be octal in YAML 1.1);
  - a returned string that YAML 1.2 would read as another type when plain
    (`"010"`, `"true"`, `"1.5"`) was quoted, and is a definite string;
  - `y` and `n` are plain strings, because PyYAML's bool resolver excludes
    them;
  - unquoted `on`, `off`, `yes` and `no`, sexagesimal values and exponent
    floats stay `parser_dependent`;
  - a `0o`-prefixed integer stays disclosed as undetectable.
- **Truthfulness.** Defaults are labelled `core_default` only when every
  source that could set the key is complete. `filter_drift` is never
  resolved. `unknown_command` means the recorder is not set up.
- **Arguments.** Arguments are validated before the SDK, and a string is never
  split.
- **Shared refactor.** The `_bounded_route_current` refactor of the HAMCP-212
  script reader.

Preserve the following:
- stable v1.1.2;
- persisted formats;
- every existing descriptor and input schema. The catalog is **90 tools,
  91 with the optional YAML reader admitted**: 62 static plus 28 delegated.
- probe fingerprints, Core capability profiles, owner-signed Core registry
  entries and the signed ha-mcp registry;
- the HOLD and the support window;
- workflows, add-on permissions and dependencies.

### Offline proof

No test reaches a live system. Every fixture is synthetic, and any
credential-shaped string is assembled at test time.

- `tests/test_hamcp132_recorder_settings.py` (79 tests) covers:
  - setting origins and every `unknown` reason;
  - `db_url`, `secret_ref` and non-HTTP userinfo withheld, checked by
    serialized-output needles;
  - per-source partial results;
  - the filter port against Core's truth table, regenerated from the pinned
    source when cached;
  - drift;
  - `unknown_command`;
  - the authority recheck at every read;
  - the AST no-write test and the exact batch allowlist;
  - the Core gate;
  - registration, gateway pre-SDK refusal and the audit;
  - the YAML reader route;
  - YAML 1.1 versus 1.2: the reviewer's examples, paired spellings, genuine
    invalid values, parser-dependent filter lists, and the port checked
    against PyYAML;
  - the owner decisions: common integers (10, 14, 30, 365) definite, the
    integer spellings disclosed, quoted strings definite, `y`/`n` checked
    against the installed PyYAML resolver, and the YAML 1.2 quoting port
    checked against recorded ruamel.yaml 0.19.1 readings (and the installed
    ruamel.yaml when importable);
  - the reviewer's final-envelope reproduction through the authenticated
    gateway, and a planted-secret sweep across every response field.
- `tests/test_hamcp132_unregistered_entities.py` (30 tests) covers:
  - reconciliation counts and platform status;
  - `config_entry_status`, including `ambiguous`;
  - search failure as partial;
  - truncation as partial;
  - inventory failure as `sources_unavailable`;
  - the authority recheck;
  - the no-write AST test;
  - registration;
  - the reviewer's final-envelope reproduction through the authenticated
    gateway, and a planted-secret sweep across every response field.
- The 41 touched existing test modules and the new suites: 1,134 tests OK
  (9 skipped), run before the final post-YAML authority test was added. The
  Evidence tier on the PR head is the complete final run.
- Filter truth table: `docs/evidence/hamcp132/`. It covers 13 configurations ×
  12 entities, generated by Core's own functions from the SHA-256-verified
  pinned source.
- Mutations: 80 of 80 killed, none by an import or syntax error
  (`docs/evidence/hamcp132-mutations/`). That is the original 62, plus the
  reviewer's reversed entity-options association, plus 12 for the review
  corrections, of which the owner decisions of 2026-10-20 replace one, plus
  five more for those decisions:
  - omissions appended after sanitization, in each tool;
  - a redaction not making the report partial, in each tool;
  - a parser-dependent value reported as `fails_core_schema`, or as
    definite;
  - a parser-dependent filter list reported as `fails_core_schema`;
  - the YAML 1.1 boolean, sexagesimal or exponent forms ignored;
  - a plain integer reported as spelling-dependent (replaces the base-8
    mutation);
  - the `setting_parser_dependent` omission dropped;
  - a quoted string treated as unquoted;
  - `y`/`n` read as YAML 1.1 booleans;
  - the numeric-spelling disclosure dropped;
  - the YAML 1.2 quoting signal lost for booleans, or for floats.

  Every row of design section 7 is covered:
  - a default reported as configured, or applied with packages present;
  - `db_url` leaked;
  - a failed source reported as complete;
  - 4a and 4b swapped, and exclude domains honoured in 4a;
  - drift resolved;
  - `unknown_command` treated as not recording;
  - a registry-less entity dropped, or a location asserted;
  - a command reachable outside the allowlist.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.27;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.26 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.26 artifact.

## 3. Installed Engineering

All installed checks below are read-only. None of them writes, purges,
reloads or calls a service.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.27 tag;
   - the protected `main` merge commit that contains this PR.
2. **Catalog.** Using fresh protocol enumeration, confirm:
   - 90 tools, or 91 with the optional YAML reader admitted;
   - `get_recorder_settings` and `get_unregistered_entities` present, with
     read-only annotations;
   - every other descriptor unchanged from beta.26;
   - Core signed selection, storage and audit health, and zero fallback.
3. **Recorder settings (live).** Call `get_recorder_settings` with no
   arguments, then with one entity the owner expects to be excluded and one
   the owner expects to be recorded. Record:
   - every `sources[]` status and reason;
   - each setting's `origin`. With `homeassistant.packages` configured,
     unwritten settings must be `unknown` (`packages_present`), not
     `core_default`;
   - `filters.filter_case` and `exclude_domains_ignored`;
   - `runtime.recording` and `db_in_default_location`;
   - each entity's `core_recorded`, `computed_recorded` and `agreement`. Any
     `filter_drift` needs a true cause, for example an unrestarted
     configuration change;
   - that the serialized response contains no database URL, credential or
     secret name.
4. **Unregistered entities (live).** Call `get_unregistered_entities` with
   the default limit. Record:
   - `counts`;
   - `truncated`;
   - the platform and config-entry statuses of a sample of rows;
   - that `definition_location` is `not_determinable` for every row.

   Compare a known registry-less YAML entity, such as a legacy template
   sensor, with its expected platform.
5. **No change.** No configuration, registry entry or recorder state is
   modified by steps 3 and 4.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
