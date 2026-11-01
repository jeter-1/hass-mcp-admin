# HAMCP-269: owner-signed Core sequence 15

This data update appends the five compiled profiles below to **2026.10.3 only**.
It preserves the exact ordered 26-reference prefix, source/image/architecture/
probe identity, all sibling entries and denial history. Core .0 stays at 22
references, .1 stays at 26, and .3 becomes 31. Runtime, workflows, stable v1,
Engineering 2.4.1-beta.53 and release metadata do not change; no version bump.
The journal is owner-signed; deployment/installed acceptance is not claimed.

## Owner decisions and review

PR [#336](https://github.com/jeter-1/hass-mcp-admin/pull/336) supplied disposable
evidence on both .1 and .3. The architect approved Part 2 at
`d7945775cfdb5f396720cabdb0da0c99759adcd6`; Josh merged it as
`e09ce94add65ac885cacf43231548c8673c60bbd`. Josh then explicitly selected .3
only for this signing sequence. Both evidence lanes remain useful; .1 does
not receive the five production admissions.

HAMCP-269 Notes records the architect's independent candidate approval on
2026-11-01 for the exact candidate/evidence hashes below. The architect
independently checked the predecessor, ordered transition, siblings, profiles,
canonical evidence and candidate shape. Their note discloses that CI artifact
contents were not independently fetchable from that review session; the exact
hash-bound receipt bytes are retained here for publication review. The Part 2
review note is itself one of the ten bound artifacts, captured byte-exactly
from the register Notes cell. Its older status statements are historical.

Josh signed on his protected host using the existing reviewed PEM loader
`docs/evidence/hamcp182/sign_step.py` (SHA256
`43491477e88f6bdd0fad5dd1e4862bf32dda9e003282a60ba00cb9e0c3681381`), checkout
`e09ce94add65ac885cacf43231548c8673c60bbd`, Python 3.12.14. The loader delegates
to the unchanged `prepare_core_release_registry.py` signing validator. No key,
PEM path, passphrase, seed conversion or new signer is included here. The
initial environment-variable invocation refused because that variable was
missing; the existing PEM interface succeeded. The assistant only verified
public signatures and copied the returned signed bytes.

## Immutable bindings

| Material | SHA256 |
|---|---|
| Published-path signed journal (`upstream-trust/ha-core-release-registry.json`) | a4dfbb143ce6cd932b91bf49c4b3d0fe9b889f690a27188c547c4700e8b62dc4 |
| Approved unsigned candidate (`candidate-seq15-2026.10.3.json`) | 224305a5cb41d53a58311246bcf5a31ee9e0e6678efbc310464f119014adb168 |
| Canonical evidence (`evidence-extend-2026.10.3.json`) | eb68cf9285c2f5e8db355eef21a2da822a091834f55030c169a3519154bcb1af |
| Exact predecessor journal (`previous-seq14-journal.json`) | 80d91919ebbd57e4ca1b7a49bc2bb6a9b2996b3b14bef3070916263e82c53817 |
| Canonical complete predecessor .3 entry | 0c29c8d769344fc45dcdbd6b2e9050e79c91e44d6528a54b441cec358b801159 |
| Raw committed Ed25519 public key | 7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac |

Sequence 15 content digest:
`sha256:4ef81c9cfc9f40ed5cf0bc5fb35b08dd8e2fe8e6a7e18c9c822f5427de1bff7e`.
Predecessor content digest:
`sha256:734210cf33344c62886377e6abea5d4df776585d5ba687f22087778fa3e93199`.
These unsigned-envelope content digests differ from the full journal byte hashes.
Generated `2026-11-01T02:25:50Z`; expires `2027-01-30T02:25:50Z`.

The previous journal is an exact byte-copy of
`upstream-trust/ha-core-release-registry.json` at the merged e09ce94 commit,
already signed by Josh on 2026-10-25. It is not reconstructed or re-signed.
The new journal retains every prior signed envelope and denial source exactly
and appends the approved successor. The canonical evidence has sorted keys,
compact separators and no trailing newline. Do not reserialize signed or
hash-bound material.

All added references have profile version 1 and the corresponding compiled
profile/adapter IDs recorded in the canonical evidence, in this exact order:

| Capability | Contract fingerprint |
|---|---|
| core.config_entry_helper_options_operation | sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8 |
| core.typed_input_select_option_operation | sha256:e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d |
| core.script_trace_read | sha256:749904fe40b13db4289301d24b958836fc72c810829611729d8c26080f06ca03 |
| core.dashboard_metadata_operation | sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa |
| core.scene_configuration_operation | sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb |

## Contract receipts and limitations

Run [41262807034](https://github.com/jeter-1/hass-mcp-admin/actions/runs/41262807034),
attempt 1, pull_request head d7945775: all 22 jobs green. Its checkout
`576d17d4513fa28f9fcd2e3419a501290f8f6dfe`, PR head and merged e09ce94 all
have tree `1c2a68c74b572e8f82e9b5403843e0e68a16a6f6`. Engineering runtime
tree remains `731c5251121f7503115699e708517bbec66e6fc9`.

Retained artifacts were `seq15-contracts-core-2026.10.3-candidate-ha-2026-10-3`
(ID13066280504) and `alarmo-inspection-core-2026.10.3-candidate-amd64`
(ID13066021379). `review-artifacts/provenance.json` records the archive digests,
per-file hashes, source identities and review-note origin. Archive digests are
GitHub-reported; individual receipt hashes were recomputed from fresh downloads.

| File in review-artifacts | Scenarios | SHA256 |
|---|---:|---|
| seq15-helper-options-ha-2026-10-3.json | 9 | d7b55fd4afc5c3896977dd915e39823f2d8b8df4e88751ec11875fbcffb0aa96 |
| seq15-input-select-option-ha-2026-10-3.json | 5 | 4be5ea7f099f3c9747bf398c8f58d8d28ab61529de011c49f3e2ce371f152f88 |
| seq15-script-traces-ha-2026-10-3.json | 4 | 91bca525820a88082b9b74fddf7ce031255cafd525fe2abd675c5a611d616192 |
| seq15-dashboard-metadata-ha-2026-10-3.json | 14 | 8f05fef96201dbc6818205e345ddb25b2ffef88525b6aa29303003b790eb0712 |
| seq15-scene-configuration-ha-2026-10-3.json | 12 | 921132b0bd4b4518867b09ec21cb279ff2f68e14612eb8296811fc096b73be82 |
| alarmo-image-ha-2026-10-3.json | — | 1cabbf7320a44a49d64db115d4345748b9a8ccd0728a6ded0f74b1f62577431c |
| alarmo-image-ha-2026-10-3.index.json | — | 407b89cd11a8cb67d388d21739a226c74acbbebe1ac59e3b83b11b43ce76b98f |
| alarmo-image-ha-2026-10-3.manifest.json | — | c32ab33f0b0589182b2f40324826acb02a104d1707c91573b6420fd622b82127 |
| architect-register-notes.txt | — | 0d30a68d84ddae09b29b0857fb4469c15526e8c2229a19c9e7d7e52ddc1ccc80 |
| provenance.json | — | 675bf6ffc78c4792405596854436679f8979a86fd63be2acfc08e36706910a85 |

The 44 scenarios cover positive behavior, refusals, mismatches, rollback and
the deferred dashboard/scene stored-readback controls listed in the Part 2
document. They execute Engineering source under the CI interpreter on exact
amd64 disposable Core; they do not prove installed Engineering-image or arm64
execution. The architect assigned installed acceptance to post-activation review.
Scene loaded state remains explicitly `not_verified`; no live household access
or canary was performed by this implementation session.

## Publication validation and activation

Public verification of Josh's returned bytes passed through the production
journal parser and separately through direct Ed25519 verification of all 16
signed objects. The successor envelope exactly equals the reviewed candidate.
The new offline test module `tests/test_hamcp269_signed_core_extension.py`
covers the production signature, exact candidate/predecessor, unchanged ordered
prefix/siblings, all ten artifact hashes and runtime selector/cache admission.
It also adds the architect's Low regression: the identical ephemeral-signed
record verifies with its own test key and fails with `registry_invalid_signature`
against the committed production public key. No production private key is used.
Executed publication-PR tests and CI are recorded in the PR and durable handoff;
the earlier green run alone does not validate these new tests.

Historical sequence-14 fixtures explicitly select the retained sequence-14
envelope. The CI harness continues to prove 26-to-27 individual admission and
31/0 batch admission on both releases using ephemeral authority. Existing
current-journal assertions now include sequence 15 without weakening historical
fingerprint or carrier checks. These changes affect tests/harness only.

Merging the separately reviewed data PR publishes the new journal at the fixed
main URL (`ha_core_readmission/registry.py:14`, under the Engineering package).
An already-enabled runtime refreshes every 21,600 seconds, checked by 300-second
Core reconciliation (`ha_mcp_readmission/registry.py:48`, `:458`;
`ha_core_readmission/runtime.py:38`, `:565`). Allow up to the next six-hour poll
plus one reconciliation interval and probe time under healthy conditions.
Startup loads the verified durable cache and makes a fetch due; a restart is
optional, not required for this data update. No restart is performed here.

Verification checks signatures, closed schemas, expiry, chain and monotonic
successors; durable cache persistence precedes new positive authority
(`ha_mcp_readmission/registry.py:558`, `:609`, `:837`). Changed selection
fingerprints retire the old Core generation and may invalidate outstanding
plans/leases (`ha_core_readmission/runtime.py:1458`). Reconcile and replan when
needed. Expected settled health on .3 is **compatible31 / withheld0**; installed
acceptance remains for the architect after separately authorized publication.

Keep this publication PR draft and unmerged until independent review and Josh's
decision. No Ready, merge, deployment, release or image publication is authorized
by preparation. Before merge, rejecting this draft leaves main unchanged. After
adoption, do not revert the journal or clear its cache: withdrawal requires a
separately authorized newer signed sequence, not rollback to sequence 14.
