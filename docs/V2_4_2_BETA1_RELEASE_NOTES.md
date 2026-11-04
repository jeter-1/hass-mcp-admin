# Engineering 2.4.2-beta.1 release notes

Engineering 2.4.2-beta.1 (HAMCP-306) fixes `get_dashboard_config` refusing a
dashboard whose response fits the response limit. It builds on 2.4.1-rc.1
(HAMCP-302, #359), and its runtime is otherwise identical to it.

Acceptance: [`V2_4_2_BETA1_ACCEPTANCE.md`](V2_4_2_BETA1_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## The defect

On 2.4.1-rc.1, the architect read `home-admin-dashboard` with
`get_dashboard_config` and got `upstream_dashboard_response_too_large`. The
refusal reported `estimated_serialized_size` 29,907 against a
`response_limit` of 60,000. The defect is not new in rc.1: 2.4.1-beta.63 has
the same runtime.

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

- The estimate is the compact JSON length of the provider's `data`,
  `warnings` and `metadata`. It includes its own `estimated_serialized_size`
  value. A fixed 1,024-character reserve
  (`CONFIGURATION_TOOL_ENVELOPE_RESERVE`) is added for the members the tool
  adds around them: operation, summary, timing, request ID and success. The
  largest possible envelope measures 825 characters.
- The configuration is returned when the estimate is at most the limit. It
  is refused when the estimate is above the limit.
- The success response and the refusal report the same
  `estimated_serialized_size`. A refused read reports exactly the number the
  limit was compared with.
- The estimate is never below the size of the real response, so a returned
  configuration is never truncated.

With the default 60,000-character limit, a configuration of about 57,000
compact characters now fits. The exact figure depends on warnings and
redaction metadata. A 30 KB nested dashboard is now returned with
`config_hash` and `engineering_config_hash`.

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
- The known issues carried by 2.4.1-rc.1 are not addressed here, apart from
  this defect.

## Rollback

Reinstall 2.4.1-rc.1. It has the same storage, plan and task formats. Dashboards
that fit only under the new measure are refused again, as before. An image
rollback does not restore household configuration or physical state.
