# Engineering 2.4.2-beta.2 release notes

Engineering 2.4.2-beta.2 (HAMCP-306) fixes `get_dashboard_config` refusing a
dashboard whose response fits the response limit. It builds on 2.4.2-beta.1
(HAMCP-301, #362), and its runtime is otherwise identical to it.

Acceptance: [`V2_4_2_BETA2_ACCEPTANCE.md`](V2_4_2_BETA2_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## The defect

On 2.4.1-rc.1, the architect read `home-admin-dashboard` with
`get_dashboard_config` and got `upstream_dashboard_response_too_large`. The
refusal reported `estimated_serialized_size` 29,907 against a
`response_limit` of 60,000. The defect is not new in rc.1: 2.4.1-beta.63 has
the same runtime, and 2.4.2-beta.1 has the same dashboard provider.

The provider (`providers/upstream_dashboard.py`, at 2.4.1-rc.1) used two
different measures:

- **What it checked** was the sanitized configuration as indented JSON
  (`indent=2`) plus a fixed 16,000-character reserve. This effectively capped
  the indented configuration at 44,000 characters.
- **What it reported** was the size of the raw configuration as compact JSON.

Indentation inflates deeply nested dashboards. A sections dashboard with
stacked cards is about 30 KB compact and about 86 KB indented, so it was
refused, and the refusal reported a number half the limit.

## The change

`get_dashboard_config` now checks with the same measure that the general
response limit uses to admit a response, and reports that same number. The
general limit (`dump_json_with_truncation`) returns a response whole if its
compact, ASCII-escaped JSON fits.

- The estimate is the compact JSON length of the whole tool response,
  including its own `estimated_serialized_size` value. The provider
  serializes the tool's real wrapper around its `data`, `warnings` and
  `metadata`: operation, summary, timing, this request's ID and success.
  Every timing member is final when the size is checked except four that
  are still growing: `total_ms`, `tool_ms`, `upstream_ms` and
  `upstream_wall_clock_span_ms` (the upstream attempt is closed after the
  check). Those four are counted at the widest a JSON float can be,
  24 characters.
- **Bound.** The estimate is never below the real response's compact JSON.
  It exceeds it by at most 84 characters: four members, each at most
  24 − 3 characters wider than their narrowest real form (`0.0`). Offline
  measurements across small, ~30 KB, non-ASCII-heavy and near-limit
  fixtures gave 68–78 characters with real timing in one run, exactly 84
  with every growing member at `0.0`, and 0 with every growing member at
  full width.
- The configuration is returned when the estimate is at most the limit. It
  is refused when the estimate is above the limit. So every response of at
  most 59,916 compact characters (60,000 − 84) is returned, every response
  above 60,000 is refused, and between the two the timing digits decide.
- The success response and the refusal report the same
  `estimated_serialized_size`. A refused read reports exactly the number the
  limit was compared with.
- A returned configuration is never truncated and never exceeds the limit.
  The returned text is the indented JSON when that also fits the limit,
  otherwise the compact JSON. The indented text can be longer than the
  estimate, which bounds the compact form only.

With the default 60,000-character limit, a configuration of about 57,400
compact characters fits in the offline fixtures. The exact figure depends
on warnings, redaction metadata and the request ID's length. A 30 KB nested
dashboard is now returned with `config_hash` and `engineering_config_hash`.

The response shape is unchanged. Only the meaning of
`estimated_serialized_size` changed: it is now the estimated compact size of
the whole response, not of the raw configuration. Its key, type, position and
the refusal's detail keys are the same.

## Unchanged by design

- The dashboard write tools, their 40,000-byte configuration cap and
  16,384-byte patch cap, plan and readback hashes, and approval bindings.
- The dependency index's dashboard scan (its own read path) and global
  response truncation.
- Tool and input schemas, providers, signed profiles, contract fingerprints,
  persisted formats and add-on options.

The plan preread, the post-apply readback and the metadata fingerprint reader
call the same provider read. They inherit the corrected threshold: a
dashboard that fits the response is now readable, and the unchanged write
caps still apply after it.

Static tools stay at 80. The advertised catalog stays at 108, or 109 with the
optional YAML reader.

## Known limitations

- A dashboard whose whole response exceeds the limit is still refused. There
  is no paging or partial read (RCG-045, HAMCP-273).
- The known issues carried by 2.4.2-beta.1 are not addressed here, apart
  from this defect.

## Rollback

Reinstall 2.4.2-beta.1. It has the same storage, plan and task formats. Dashboards
that fit only under the new measure are refused again, as before. An image
rollback does not restore household configuration or physical state.
