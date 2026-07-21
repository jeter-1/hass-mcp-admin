"""Run the minimal protected registry signing phases."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.upstream_registry_signing_core import (  # noqa: E402
    SigningCoreError,
    TrustedInputs,
    canonical_file,
    prepare_signing,
    sign_prepared,
    verify_and_assemble_artifacts,
)


def _trusted(args: argparse.Namespace) -> TrustedInputs:
    return TrustedInputs(
        operation=args.expected_operation,
        upstream_version=args.expected_upstream_version,
        expected_current_sequence=args.expected_current_sequence,
        expiry_days=args.expected_expiry_days,
        operator_reason=args.expected_operator_reason,
        workflow_base_sha=args.expected_workflow_base_sha,
        dispatch_sha=args.expected_dispatch_sha,
        contract_family=args.expected_contract_family,
        output_paths=tuple(sorted(args.expected_output_path)),
        key_id=args.expected_key_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("prepare-signing", "sign", "verify-artifacts"),
        required=True,
    )
    parser.add_argument("--expected-operation", required=True)
    parser.add_argument("--expected-upstream-version", default="")
    parser.add_argument("--expected-current-sequence", type=int, required=True)
    parser.add_argument("--expected-expiry-days", type=int, required=True)
    parser.add_argument("--expected-operator-reason", default="")
    parser.add_argument("--expected-workflow-base-sha", required=True)
    parser.add_argument("--expected-dispatch-sha", required=True)
    parser.add_argument("--expected-contract-family", required=True)
    parser.add_argument("--expected-output-path", action="append", default=[])
    parser.add_argument("--expected-key-id", required=True)
    parser.add_argument("--inspection-directory", type=Path)
    parser.add_argument("--prepared-directory", type=Path, required=True)
    parser.add_argument("--signature-directory", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--repository", type=Path)
    args = parser.parse_args(argv)
    trusted = _trusted(args)
    try:
        if args.phase == "prepare-signing":
            if args.inspection_directory is None or args.repository is None:
                raise SigningCoreError("prepare_signing_arguments_invalid")
            result = prepare_signing(
                inspection_directory=args.inspection_directory,
                prepared_directory=args.prepared_directory,
                repository=args.repository,
                trusted=trusted,
                environment=os.environ,
            )
        elif args.phase == "sign":
            if args.signature_directory is None:
                raise SigningCoreError("sign_arguments_invalid")
            result = sign_prepared(
                prepared_directory=args.prepared_directory,
                signature_directory=args.signature_directory,
                trusted=trusted,
                environment=os.environ,
            )
        else:
            if (
                args.signature_directory is None
                or args.output_directory is None
                or args.repository is None
            ):
                raise SigningCoreError("verify_artifacts_arguments_invalid")
            result = verify_and_assemble_artifacts(
                prepared_directory=args.prepared_directory,
                signature_directory=args.signature_directory,
                output_directory=args.output_directory,
                repository=args.repository,
                trusted=trusted,
                environment=os.environ,
            )
    except (OSError, ValueError) as exc:
        print(getattr(exc, "category", "protected_signing_failed"), file=sys.stderr)
        return 2
    print(canonical_file(result).decode("utf-8").rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
