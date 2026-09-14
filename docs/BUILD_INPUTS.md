# Engineering build inputs and architecture policy

This is the delivery-audit F3 correction. It does not change the Engineering
execution subsystem also named F3. The next release supports `linux/amd64` and
`linux/arm64`; published 2.2.0 retains its original three-platform artifact.
Frozen stable-v1 packaging and external provider artifact contracts are unchanged.
An armv7 installation needs a separately planned migration to supported 64-bit
hardware/OS. Removing an architecture here does not perform that migration.

## Controlled inputs

Both Docker stages use the Python 3.12.14 multi-platform base index recorded for
the accepted 2.2.0 image:
`sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`.
Child manifest and configuration digests are in
[`build-inputs.json`](../hass_mcp_engineering_beta/build-inputs.json).
The pinned base fixes inherited OS packages and pip 25.0.1. There is no
additional apt resolution. Future vulnerability fixes require deliberate updates.

[`requirements.txt`](../hass_mcp_engineering_beta/requirements.txt) retains eight
direct requirements. [`requirements.lock`](../hass_mcp_engineering_beta/requirements.lock)
pins the complete 39-package runtime closure to the versions recorded for 2.2.0,
including transitive httpx. Only reviewed wheel hashes for the two architectures
are admitted. Binary-only download refuses source distributions; hash-enforced,
index-free installation uses those downloaded wheels. No source build, apt
compiler toolchain or floating setuptools/Cython/wheel build environment remains.

[`tests/requirements.lock`](../tests/requirements.lock) fixes the complete Linux
amd64 Python 3.12 test closure and includes identical runtime pins and hashes.
The existing direct test pins remain in
[`tests/requirements.txt`](../tests/requirements.txt). The test environment also
contains audit/test tools and is not the final runtime inventory. CI selects
Python 3.12.14, installs locked wheels and audits the complete runtime lock.
The publisher obtains its validation lock from the protected workflow commit,
including during guarded recovery of older artifacts that predate locks.
The ordinary GitHub runner and Actions environment is not an immutable replica
of the container.

## Build and publication verification

`hass_mcp_engineering_beta/build_inputs.py source` checks input hashes, the
complete pin set and both Docker base references. The final Docker layer runs
`build_inputs.py smoke` with networking disabled. It checks Python, architecture
and the exact installed Python distribution set (runtime plus base pip), then
exercises local YAML, templates, JSON schema, cryptography, CFFI and Pydantic.
It imports dependencies and immutable version metadata without starting
Engineering or contacting a provider.

The required packaging job builds and loads both images. It repeats bounded
smoke under each requested architecture with no network, a read-only root,
dropped capabilities and no new privileges. Each JSON inventory and the checkout
SHA are retained in `engineering-build-inputs`. Failure or timeout fails packaging
and the required aggregate `validate`. Validation images are not pushed. Existing
Core, exact-provider, add-on and full unit-test gates remain required.

Before final release tags are written, the publisher verifies the digest-addressed
image and attestation chain. For a source declaring controlled build inputs,
`verify_publication_source_image.py` reads committed contract/lock bytes, compares
every architecture's SBOM Python inventory including pip, and requires the
expected Python base index or corresponding child digest in provenance. Missing,
extra, duplicate or different runtime packages, absent base evidence and
source/lock mismatches refuse publication. Expectations come from immutable
release source, not the received inventory or edited checkout.

Base configuration digests record the reviewed index-to-manifest chain.
Publication checks the resolved base digest; it does not independently download
those Docker Hub configuration blobs on every run. Historical sources without
this declaration retain their original contract and report
`historical_not_declared`, with no retroactive input-verification credit.
Historical three-platform manifests still require all three declared platforms.

The final build runs local smoke and provenance binds its source. Digest-bound
SBOM/provenance inspection is separate from independently running the published
digest. This change adds no post-publication container run and preserves the
no-rebuild digest-resume path. BuildKit attestations and digests provide
traceability, not independent signer authentication or byte-for-byte
reproducibility. Timestamps, scanners, attestations and CI host inputs remain
relevant differences.

## Reviewed updates

Use disposable Python 3.12.14 with pip 25.0.1; keep preparation reports outside
the checkout. Never update locks as a side effect of building or publication.

1. Identify the exact proposed base index, both manifests/configurations, Python
   and pip versions. Retrieve and hash raw public artifacts. Review OS changes,
   vulnerabilities, supported wheel tags and both architectures.
2. Choose exact direct and transitive runtime versions. Obtain wheel-only pip
   reports for both target architectures with an explicit PyPI index. Record
   interpreter, platform tags, commands, wheel URLs/hashes and outcomes. Render
   a new lock to an absent path using the offline tool:

   ```text
   python scripts/prepare_build_inputs.py --report <amd64-pip-report.json> --report <arm64-pip-report.json> --output <new-runtime.lock>
   ```

   The tool accepts pip 25.0.1 reports, refuses yanked/direct/source-archive
   entries, restricts wheel URLs to PyPI's file host, rejects version conflicts
   and merges hashes. Inspect every changed pin/artifact before adopting output.
   Python/installer policy changes require matching tooling review.
3. Resolve a Linux amd64 test report with all runtime versions constrained and
   the existing test requirements. Generate its complete lock:

   ```text
   python scripts/prepare_build_inputs.py --report <test-pip-report.json> --runtime-lock <reviewed-runtime.lock> --output <new-tests.lock>
   ```

   Every runtime package must appear with the same version and an admitted
   hash. Both architecture hashes remain in the test lock. Review test-only
   changes too; report generation alone is not artifact approval.
4. Update the declaration's direct-requirements/runtime-lock SHA256 values,
   distribution map and base identities with both Docker references. Download
   and hash-check both wheel sets, install from locks, run dependency checks,
   focused tests, full Evidence and both architecture builds. Retain actual image
   inventories. Missing compatible wheels fail; do not silently permit source
   builds, add a compiler or remove an architecture gate.
5. Obtain independent review and an authorized new release. Bind CI/publication
   evidence to the exact candidate/digest. Preserve published 2.2.0 and history.

Local test setup and focused validation:

```text
python -m pip --isolated install --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r tests/requirements.lock
python hass_mcp_engineering_beta/build_inputs.py source
python -m unittest -v tests.test_build_inputs tests.test_publication_source_verifier tests.test_rc1_publication tests.test_ci_efficiency
```

Uncached wheels require network retrieval. Regression tests use synthetic files
and local pip operations without network or Docker. Source tests cannot establish
architecture image execution. Report local Docker access gaps; do not change host
access controls to bypass them.

Local recovery is an ordinary revert of the build-input commit and, if required
and authorized, the separate retirement commit. A source revert neither rewrites
published artifacts nor restores upstream armv7 support or authorizes a live
downgrade. Packaging changes require a new authorized version transition;
unchanged 2.2.0 metadata must not be used to republish.
