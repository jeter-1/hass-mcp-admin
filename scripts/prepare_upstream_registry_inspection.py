"""Package raw, unsigned registry inspection evidence for protected signing."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.upstream_registry_signing_core import (  # noqa: E402
    CONTRACT_FAMILY,
    INSPECTION_ARTIFACT_NAME,
    MUTATING_OPERATIONS,
    REGISTRY_PATH,
    REGISTRY_SIGNATURE_PATH,
    STABLE_VERSION,
    WHEELHOUSE_ARTIFACT_NAME,
    SigningCoreError,
    canonical_file,
    load_canonical_file,
    sha256_digest,
)


def prepare_inspection_artifact(
    *,
    output_directory: Path,
    operation: str,
    upstream_version: str,
    expected_current_sequence: int,
    expiry_days: int,
    operator_reason: str,
    workflow_base_sha: str,
    dispatch_sha: str,
    contract_family: str,
    runtime_evidence: Path | None = None,
    release_evidence: Path | None = None,
    registry: Path | None = None,
    registry_signature: Path | None = None,
) -> dict[str, object]:
    if operation not in {*MUTATING_OPERATIONS, "verify"}:
        raise SigningCoreError("inspection_operation_invalid")
    if operation in {"bootstrap", "add"}:
        if not STABLE_VERSION.fullmatch(upstream_version):
            raise SigningCoreError("inspection_selector_invalid")
        sources = {
            "runtime-evidence.json": runtime_evidence,
            "release-evidence.json": release_evidence,
        }
    else:
        if operation in {"revoke", "restore"}:
            if not STABLE_VERSION.fullmatch(upstream_version):
                raise SigningCoreError("inspection_selector_invalid")
        elif upstream_version:
            raise SigningCoreError("inspection_selector_invalid")
        sources = {
            "current-registry.json": registry,
            "current-registry-signature.json": registry_signature,
        }
    if any(path is None for path in sources.values()):
        raise SigningCoreError("inspection_source_missing")
    if output_directory.exists():
        shutil.rmtree(output_directory)
    output_directory.mkdir(parents=True)
    evidence_digests: dict[str, str] = {}
    for name, source in sources.items():
        assert source is not None
        value = load_canonical_file(source)
        raw = canonical_file(value)
        (output_directory / name).write_bytes(raw)
        evidence_digests[name] = sha256_digest(raw)
    root_files = sorted({"inspection-manifest.json", *sources})
    manifest = {
        "schema_version": 1,
        "artifact_name": INSPECTION_ARTIFACT_NAME,
        "wheelhouse_artifact_name": WHEELHOUSE_ARTIFACT_NAME,
        "root_files": root_files,
        "operation": operation,
        "upstream_version": upstream_version,
        "expected_current_sequence": expected_current_sequence,
        "expiry_days": expiry_days,
        "operator_reason": operator_reason,
        "workflow_base_sha": workflow_base_sha,
        "dispatch_sha": dispatch_sha,
        "contract_family": contract_family,
        "evidence_digests": evidence_digests,
    }
    (output_directory / "inspection-manifest.json").write_bytes(canonical_file(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--upstream-version", default="")
    parser.add_argument("--expected-current-sequence", type=int, required=True)
    parser.add_argument("--expiry-days", type=int, required=True)
    parser.add_argument("--operator-reason", default="")
    parser.add_argument("--workflow-base-sha", required=True)
    parser.add_argument("--dispatch-sha", required=True)
    parser.add_argument("--contract-family", default=CONTRACT_FAMILY)
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--release-evidence", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--registry-signature", type=Path)
    args = parser.parse_args()
    try:
        result = prepare_inspection_artifact(
            output_directory=args.output_directory,
            operation=args.operation,
            upstream_version=args.upstream_version,
            expected_current_sequence=args.expected_current_sequence,
            expiry_days=args.expiry_days,
            operator_reason=args.operator_reason,
            workflow_base_sha=args.workflow_base_sha,
            dispatch_sha=args.dispatch_sha,
            contract_family=args.contract_family,
            runtime_evidence=args.runtime_evidence,
            release_evidence=args.release_evidence,
            registry=args.registry,
            registry_signature=args.registry_signature,
        )
    except (OSError, ValueError) as exc:
        print(getattr(exc, "category", "inspection_packaging_failed"), file=sys.stderr)
        return 2
    print(canonical_file(result).decode("utf-8").rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
