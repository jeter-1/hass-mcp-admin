# Core 2026.10.0 local compatibility evidence

This summary binds the local October lane to the reviewed implementation and
preserved original receipts. It is not a production registry entry or installed
acceptance. The release delta must demonstrate that the relevant implementation
and test inputs remain identical before reusing it.

## Source and immutable inputs

| Input | Exact identity |
| --- | --- |
| Engineering base | `c4168a61e919996c59d8763b77fcf78ffd99b6f7` |
| Reviewed implementation | `badbc08f61237dfeec934c852a3d9e77c953f9aa` |
| Core 2026.10.0 source | `6a811d3359c7b2076dc9e1cf900843a129c044af` |
| Core archive SHA-256 | `96778e8c130c16e86619e82741d308e41c76d2963da649f0748f7b51fb9703b9` |
| Core OCI index | `sha256:1b64d38f38d922bf9d59336451fd6453e1d614f934456af4ee3d2a51061be3a4` |
| Executed amd64 manifest | `sha256:956a5d9effc091888cd34cb0b6fb4f66c448d257d20519e72302557682734fd0` |
| Executed configuration | `sha256:e5ee66f11f90f86c50c1b7641e558b707ccc3d2bb354daf05c296e58382945e2` |
| ha-mcp 8.5.0 source | `311d6dc273fb4e9a5b8cde0de15f69472a64fe44` |
| Alarmo 1.10.19 source | `169e134f4b70d87aae36ba54a72a398ecc960afd` |

Both standalone/add-on provider index, architecture-manifest, configuration and
actual container descriptor bindings are retained with the original typed
receipts. Docker 29 containerd identity is checked through that complete chain;
image labels or an assumed meaning of `.Id` were not substituted for it.

## Actual executed outcomes

| Lane | Result |
| --- | --- |
| Script semantics | 11 unittest methods passed, no failures/errors/skips; exact installed Core and seven source hashes checked. |
| General/migration | PASS; exact Core 2026.7.2 generated historical records, October contract process completed with exit 0. No invented aggregate unittest count. |
| Alarmo | PASS; 16 synthetic records/two pages, explicit 19-to-20 test-authority transition, withheld-authority/no-write observer controls, zero continuation reads. |
| Native baseline | PASS; separate ephemeral 20-to-21 selection; 123 records before/after. Comparator: ADDED 1, CHANGED 1, REMOVED 1, UNCHANGED 120, UNKNOWN 1; union 124. Zero continuation reads and negative identity/admin/authority controls. |
| Garage actual Core | 326 executed checks passed / 319 distinct labels across six harnesses; seven labels deliberately repeat. Actual 30-second and five-minute controls completed. |
| Each typed provider mode | PASS; two matching 77-descriptor captures, 25 admitted reads, three fan/four power/two governed dashboard operations plus stale, uncertain, recovery and no-redispatch controls. |
| Settlement | Every successful lane's containers exited 0 without OOM and were removed; owned integration networks removed; exact-label final check found no retained resources. |

The 21 capability IDs and contract fingerprints were independently derived
from source and matched to the executed contracts. They are not one aggregate
test count or one long-lived production authority. The 77 upstream descriptors,
25 admitted reads, 83 public Engineering tools and 21 Core profiles are distinct
inventories.

Original failures remain preserved: Docker identity preflight refused before
execution; the first general writer lacked an exact locked pure dependency;
the second general fixture omitted the October baseline selector. Reviewed
test-side corrections led to a fresh complete third run. No failure was relabeled
PASS, and no production implementation assertion was relaxed to fix the harness.

## Review and receipt bindings

The private retained root is the isolated compatibility checkout's ignored
`.artifacts/`. Relative artifact names below identify the immutable originals;
they are receipt locators, not instructions to upload private configuration stores.

| Evidence | SHA-256 |
| --- | --- |
| `core-october/independent-review/REVIEW.md` | `72b0009354cce5d4e22cc85df8c23d30989f850211056d914aab90e11d80f571` |
| `core-october-disposable-20261009T195216Z/review-execution/REVIEW.md` | `588d4218bdbf653b7c8136aa6c1cffcb8bac732e7be2ae04a301660095e6a742` |
| Same execution root: `REPORT.md` | `4b245ac378fde8550350c0f0c5cfbe80e90d5ee1b7a4c7de4c5552840d757bd8` |
| Same execution root: `MANIFEST.json` | `fa8809cae93a39093d39daf6339d4e8c79cfbc0d517ecaef8b30413ed5e56a7e` |
| Same execution root: `general3/INPUTS.json` | `6de18e61486ada463e028231cbaa0444dac7ab5983c12691606e0d9a14bc54f0` |
| Same execution root: `garage-run/INPUTS.json` | `b5cf8074f5ef6b97a4a78f468feb37fc7f78329a2fe851bc2e41e625e397af7e` |
| Same execution root: `typed/INPUTS.json` | `0ef8bf827aef94b21e6f13162b162e87a42922d7ae408b99c9dd87f5b1fcb000` |

The source reviewer independently ran 77 focused tests with no findings. Its
then-pending assembled lane is resolved by the later independent execution
review, which reconciled original receipts and hashes without rerunning campaigns
and found no new findings across the 21 profiles. The final seal verifies 398
selected files. Original private mutable stores are excluded; immutable source
and dependency trees remain bound by the retained input manifests.

Implementation Full/Evidence ran 4,790 tests: 4,766 passed / 24 skipped, no test
failures/errors. It was 14/15 solely because release-version materialization was
outside that task. Those results belong to `badbc08`, not a future release head;
beta.2 must receive its own Full/Evidence, release review and exact-head CI.

## Limits

Execution was native amd64, synthetic Core services/configurations and ephemeral
test authority. Arm64 runtime, household physical feedback, actual Supervisor
lifecycle and all custom integrations were not exercised. The add-on used its
default startup against a fixed `/core` relay; baseline used synthetic Hass.io
metadata and non-atomic intervals. Network-none Script/garage cases differ from
the owned loopback integration bridges, which are not complete egress isolation.
The lane makes no promise about later Core patches, ha-mcp 8.6.x or every
capability/provider/principal combination. No production signing, admission,
publication, installation or household change occurred.
