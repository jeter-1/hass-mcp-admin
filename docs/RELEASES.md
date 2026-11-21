# Release index

Every Engineering release record in `docs/`, newest first. Release notes describe
what a release changed; the acceptance contract binds what it had to prove. Both
are immutable historical records once a later release exists. Published images
and release bodies are on
[GitHub Releases](https://github.com/jeter-1/hass-mcp-admin/releases).

The version in each row is taken from the document's own first heading, not
from its filename. Records whose headings carry no version string are listed
under [Legacy naming](#legacy-naming) with their headings verbatim.

The per-release narration that the root README carried until 2.4.1-beta.63 is
preserved in
[the README at `34b8538b`](https://github.com/jeter-1/hass-mcp-admin/blob/34b8538ba704ded6032f13600ec5b185590100eb/README.md).
The stable v1 line is retired; see [`hass_mcp_admin/README.md`](../hass_mcp_admin/README.md).
Release records deleted by HAMCP-437 remain in git history: [`docs/` at `9af0d7dc`](https://github.com/jeter-1/hass-mcp-admin/tree/9af0d7dcce7f17c240917f0bdce576068a565458/docs).

## Adding a release

A release PR adds its `docs/<STEM>_RELEASE_NOTES.md` and `docs/<STEM>_ACCEPTANCE.md`
flat in `docs/` (the stem rules are in `scripts/codex-context.py`), adds one row at
the top of the table below, and replaces the **Current release** section of the
root README. It does not move earlier records or add narration to the README.
`tests/test_docs_index.py` fails if a release document is missing from this index.

## Releases

| Version | Release notes | Acceptance |
| --- | --- | --- |
| 2.4.2-beta.11 | [`V2_4_2_BETA11_RELEASE_NOTES.md`](V2_4_2_BETA11_RELEASE_NOTES.md) | [`V2_4_2_BETA11_ACCEPTANCE.md`](V2_4_2_BETA11_ACCEPTANCE.md) |
| 2.4.2-beta.10 | [`V2_4_2_BETA10_RELEASE_NOTES.md`](V2_4_2_BETA10_RELEASE_NOTES.md) | [`V2_4_2_BETA10_ACCEPTANCE.md`](V2_4_2_BETA10_ACCEPTANCE.md) |
| 2.4.2-beta.9 | [`V2_4_2_BETA9_RELEASE_NOTES.md`](V2_4_2_BETA9_RELEASE_NOTES.md) | [`V2_4_2_BETA9_ACCEPTANCE.md`](V2_4_2_BETA9_ACCEPTANCE.md) |
| 2.4.2-beta.8 | [`V2_4_2_BETA8_RELEASE_NOTES.md`](V2_4_2_BETA8_RELEASE_NOTES.md) | [`V2_4_2_BETA8_ACCEPTANCE.md`](V2_4_2_BETA8_ACCEPTANCE.md) |
| 2.4.2-beta.3 | [`V2_4_2_BETA3_RELEASE_NOTES.md`](V2_4_2_BETA3_RELEASE_NOTES.md) | [`V2_4_2_BETA3_ACCEPTANCE.md`](V2_4_2_BETA3_ACCEPTANCE.md) |
| 2.4.1-beta.58 | [`V2_4_1_BETA58_RELEASE_NOTES.md`](V2_4_1_BETA58_RELEASE_NOTES.md) | [`V2_4_1_BETA58_ACCEPTANCE.md`](V2_4_1_BETA58_ACCEPTANCE.md) |
| 2.4.1-beta.53 | [`V2_4_1_BETA53_RELEASE_NOTES.md`](V2_4_1_BETA53_RELEASE_NOTES.md) | [`V2_4_1_BETA53_ACCEPTANCE.md`](V2_4_1_BETA53_ACCEPTANCE.md) |
| 2.4.1-beta.20 | [`V2_4_1_BETA20_RELEASE_NOTES.md`](V2_4_1_BETA20_RELEASE_NOTES.md) | [`V2_4_1_BETA20_ACCEPTANCE.md`](V2_4_1_BETA20_ACCEPTANCE.md) |
| 2.4.1-beta.1 | [`V2_4_1_BETA1_RELEASE_NOTES.md`](V2_4_1_BETA1_RELEASE_NOTES.md) | [`V2_4_1_BETA1_ACCEPTANCE.md`](V2_4_1_BETA1_ACCEPTANCE.md) |
| 2.4.0-beta.15 | — | [`V2_4_0_BETA15_ACCEPTANCE.md`](V2_4_0_BETA15_ACCEPTANCE.md) |
| 2.3.0-beta.4 | — | [`V2_3_0_BETA4_ACCEPTANCE.md`](V2_3_0_BETA4_ACCEPTANCE.md) |
| 2.2.0-rc.9 | — | [`V2_2_0_RC9_ACCEPTANCE.md`](V2_2_0_RC9_ACCEPTANCE.md) |
| 2.2.0-rc.1 | [`V2_2_0_RC1_RELEASE_NOTES.md`](V2_2_0_RC1_RELEASE_NOTES.md) | [`V2_2_0_RC1_ACCEPTANCE.md`](V2_2_0_RC1_ACCEPTANCE.md) |
| 2.2.0-beta.54 | [`V2_2_0_BETA54_RELEASE_NOTES.md`](V2_2_0_BETA54_RELEASE_NOTES.md) | [`V2_2_0_BETA54_ACCEPTANCE.md`](V2_2_0_BETA54_ACCEPTANCE.md) |
| 2.2.0-beta.51 | — | [`V2_2_0_BETA51_ACCEPTANCE.md`](V2_2_0_BETA51_ACCEPTANCE.md) |
| 2.2.0-beta.35 | — | [`V2_2_0_BETA35_ACCEPTANCE.md`](V2_2_0_BETA35_ACCEPTANCE.md) |
| 2.2.0-beta.33 | [`V2_2_0_BETA33_RELEASE_NOTES.md`](V2_2_0_BETA33_RELEASE_NOTES.md) | [`V2_2_0_BETA33_ACCEPTANCE.md`](V2_2_0_BETA33_ACCEPTANCE.md) |
| 2.2.0-beta.30 | [`V2_2_0_BETA30_RELEASE_NOTES.md`](V2_2_0_BETA30_RELEASE_NOTES.md) | [`V2_2_0_BETA30_ACCEPTANCE.md`](V2_2_0_BETA30_ACCEPTANCE.md) |
| 2.2.0-beta.22 | [`V2_2_0_BETA22_RELEASE_NOTES.md`](V2_2_0_BETA22_RELEASE_NOTES.md) | — |
| 2.1.0-beta.1 | [`V2_1_0_BETA1_RELEASE_NOTES.md`](V2_1_0_BETA1_RELEASE_NOTES.md) | [`V2_1_0_BETA1_ACCEPTANCE.md`](V2_1_0_BETA1_ACCEPTANCE.md) |
| 2.0.1 | [`V2_0_1_RELEASE_NOTES.md`](V2_0_1_RELEASE_NOTES.md) | [`V2_0_1_ACCEPTANCE.md`](V2_0_1_ACCEPTANCE.md) |
| 2.0.1-rc1-dev2 | [`RC1DEV2_RELEASE_NOTES.md`](RC1DEV2_RELEASE_NOTES.md) | [`RC1DEV2_ACCEPTANCE.md`](RC1DEV2_ACCEPTANCE.md) |
| 2.0.1-rc1-dev1 | [`RC1DEV1_RELEASE_NOTES.md`](RC1DEV1_RELEASE_NOTES.md) | [`RC1DEV1_ACCEPTANCE.md`](RC1DEV1_ACCEPTANCE.md) |
| 2.0.0 | [`V2_0_0_RELEASE_NOTES.md`](V2_0_0_RELEASE_NOTES.md) | [`V2_0_0_ACCEPTANCE.md`](V2_0_0_ACCEPTANCE.md) |
| 2.0.0-rc.2 | [`RC2_RELEASE_NOTES.md`](RC2_RELEASE_NOTES.md) | [`RC2_ACCEPTANCE.md`](RC2_ACCEPTANCE.md) |

## Legacy naming

These records predate version-bearing headings. Their headings are quoted
verbatim; no version is inferred.

| Record | Release notes | Acceptance | Headings |
| --- | --- | --- | --- |
| RC2DEV8 | [`RC2DEV8_RELEASE_NOTES.md`](RC2DEV8_RELEASE_NOTES.md) | [`RC2DEV8_ACCEPTANCE.md`](RC2DEV8_ACCEPTANCE.md) | RC2DEV8_ACCEPTANCE.md: “RC2dev8 acceptance checklist”<br>RC2DEV8_RELEASE_NOTES.md: “RC2dev8 release notes” |
| RC2DEV12 | [`RC2DEV12_RELEASE_NOTES.md`](RC2DEV12_RELEASE_NOTES.md) | [`RC2DEV12_ACCEPTANCE.md`](RC2DEV12_ACCEPTANCE.md) | RC2DEV12_ACCEPTANCE.md: “RC2dev12 rejected acceptance record”<br>RC2DEV12_RELEASE_NOTES.md: “RC2dev12 immutable historical release record” |
| RC2DEV13 | [`RC2DEV13_RELEASE_NOTES.md`](RC2DEV13_RELEASE_NOTES.md) | [`RC2DEV13_ACCEPTANCE.md`](RC2DEV13_ACCEPTANCE.md) | RC2DEV13_ACCEPTANCE.md: “RC2dev13 acceptance”<br>RC2DEV13_RELEASE_NOTES.md: “RC2dev13 release notes” |
| RC2DEV14 | [`RC2DEV14_RELEASE_NOTES.md`](RC2DEV14_RELEASE_NOTES.md) | [`RC2DEV14_ACCEPTANCE.md`](RC2DEV14_ACCEPTANCE.md) | RC2DEV14_ACCEPTANCE.md: “RC2dev14 pre-deployment acceptance contract”<br>RC2DEV14_RELEASE_NOTES.md: “RC2dev14 development notes” |
| RC2DEV15 | [`RC2DEV15_RELEASE_NOTES.md`](RC2DEV15_RELEASE_NOTES.md) | [`RC2DEV15_ACCEPTANCE.md`](RC2DEV15_ACCEPTANCE.md) | RC2DEV15_ACCEPTANCE.md: “RC2dev15 pre-deployment acceptance contract”<br>RC2DEV15_RELEASE_NOTES.md: “RC2dev15 development notes” |
| RC2DEV16 | [`RC2DEV16_RELEASE_NOTES.md`](RC2DEV16_RELEASE_NOTES.md) | [`RC2DEV16_ACCEPTANCE.md`](RC2DEV16_ACCEPTANCE.md) | RC2DEV16_ACCEPTANCE.md: “RC2dev16 pre-deployment acceptance contract”<br>RC2DEV16_RELEASE_NOTES.md: “RC2dev16 development notes” |
| RC3A | [`RC3A_RELEASE_NOTES.md`](RC3A_RELEASE_NOTES.md) | [`RC3A_ACCEPTANCE.md`](RC3A_ACCEPTANCE.md) | RC3A_ACCEPTANCE.md: “HA MCP Engineering Server RC3A acceptance”<br>RC3A_RELEASE_NOTES.md: “HA MCP Engineering Server RC3A release notes” |

## Release companions

Supplementary records for a specific release, listed with their headings.

- [`BETA35_MOBILE_APPROVAL_NAVIGATION.md`](BETA35_MOBILE_APPROVAL_NAVIGATION.md): “Beta 35 mobile approval navigation”

## Feature acceptance contracts

Acceptance contracts for a feature rather than a release.

- [`ALARMO_INSPECTION_ACCEPTANCE.md`](ALARMO_INSPECTION_ACCEPTANCE.md): “Alarmo inspector acceptance”
- [`AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md`](AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md): “Native automation-baseline capture acceptance”
- [`F3_COMPLETION_ACCEPTANCE.md`](F3_COMPLETION_ACCEPTANCE.md): “F3 completion acceptance contract”
- [`HA_MCP_CAPABILITY_AUTO_READMISSION_ACCEPTANCE.md`](HA_MCP_CAPABILITY_AUTO_READMISSION_ACCEPTANCE.md): “ha-mcp capability-scoped automatic-readmission acceptance”
