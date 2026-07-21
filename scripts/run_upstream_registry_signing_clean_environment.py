"""Exercise the real protected signer in a clean lock-only virtual environment."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_upstream_registry_inspection import (  # noqa: E402
    prepare_inspection_artifact,
)
from scripts.upstream_registry_signing_core import (  # noqa: E402
    CONTRACT_FAMILY,
    REGISTRY_PATH,
    allowed_output_paths,
    canonical_file,
    canonical_json,
    normalize_runtime_contract,
    reviewed_security_projection,
)
from scripts.verify_upstream_registry_signing_wheels import (  # noqa: E402
    WheelhouseVerificationError,
    verify_wheelhouse,
)


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
    )
    return result


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _python(venv_root: Path) -> Path:
    windows = venv_root / "Scripts" / "python.exe"
    return windows if windows.exists() else venv_root / "bin" / "python"


def _fixture_evidence(directory: Path, version: str) -> tuple[Path, Path]:
    tool_path = (
        ROOT
        / "hass_mcp_engineering_beta"
        / "ha_mcp_engineering"
        / "providers"
        / "contracts"
        / "ha_mcp_7_14_dashboard_read_v2.json"
    )
    tool = json.loads(tool_path.read_text(encoding="utf-8"))
    fingerprints = normalize_runtime_contract(tool, "2025-03-26")
    descriptor = hashlib.sha256(canonical_json(tool)).hexdigest()
    runtime = {
        "server_name": "ha-mcp",
        "server_version": version,
        "protocol_version": "2025-03-26",
        "required_tool": tool,
        "contract_fingerprints": fingerprints,
        "informational_fingerprints": {
            "raw_input_schema": hashlib.sha256(
                canonical_json(tool["inputSchema"])
            ).hexdigest(),
            "reviewed_security_descriptor": hashlib.sha256(
                canonical_json(reviewed_security_projection(tool))
            ).hexdigest(),
            "fixture_runtime_descriptor": descriptor,
            "published_runtime_descriptor": descriptor,
        },
        "catalog_fingerprint": "5" * 64,
        "write_dispatches": 0,
        "negative_reachability": {
            "rejected_before_dispatch": [
                "ha_set_entity",
                "ha_set_device",
                "ha_call_service",
                "ha_bulk_control",
                "ha_config_set_dashboard",
                "ha_config_delete_dashboard",
            ],
            "include_screenshot_true_rejected": True,
            "generic_forwarder_present": False,
        },
    }
    release = {
        "version": version,
        "source_tag": f"v{version}",
        "source_commit": "a" * 40,
        "image_index_digest": "sha256:" + "b" * 64,
        "image_revision": "c" * 40,
        "image_created": "2026-07-20T00:00:00Z",
        "image_source": "https://github.com/homeassistant-ai/ha-mcp",
        "dirty_label": "false",
        "platform_digests": {
            "linux/amd64": "sha256:" + "d" * 64,
            "linux/arm64": "sha256:" + "e" * 64,
            "linux/arm/v7": "sha256:" + "f" * 64,
        },
        "slsa_provenance": "present_per_platform",
        "sbom": "present",
        "official_repository": "homeassistant-ai/ha-mcp",
        "official_image": "ghcr.io/homeassistant-ai/ha-mcp",
    }
    directory.mkdir(parents=True)
    runtime_path = directory / "runtime-evidence.json"
    release_path = directory / "release-evidence.json"
    runtime_path.write_bytes(canonical_file(runtime))
    release_path.write_bytes(canonical_file(release))
    return runtime_path, release_path


def _common_arguments(
    *,
    base_sha: str,
    version: str,
    key_id: str,
    operation: str = "bootstrap",
    expiry_days: int = 90,
    reason: str = "clean-environment-review",
) -> list[str]:
    result = [
        "--expected-operation",
        operation,
        "--expected-upstream-version",
        version,
        "--expected-current-sequence",
        "0",
        "--expected-expiry-days",
        str(expiry_days),
        "--expected-operator-reason",
        reason,
        "--expected-workflow-base-sha",
        base_sha,
        "--expected-dispatch-sha",
        base_sha,
        "--expected-contract-family",
        CONTRACT_FAMILY,
        "--expected-key-id",
        key_id,
    ]
    for path in allowed_output_paths(1):
        result.extend(["--expected-output-path", path])
    return result


def _require_failure(result: subprocess.CompletedProcess[str], category: str | None = None) -> None:
    if result.returncode == 0:
        raise RuntimeError("negative_case_unexpected_success")
    if category and category not in result.stderr:
        raise RuntimeError("negative_case_category_mismatch")


def run_acceptance(wheelhouse: Path, lock: Path) -> dict[str, object]:
    verify_wheelhouse(wheelhouse, lock)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repository = root / "repository"
        repository.mkdir()
        _git(repository, "init", "-b", "main")
        _git(repository, "config", "user.name", "Clean Environment")
        _git(repository, "config", "user.email", "clean@example.invalid")
        (repository / "README.md").write_text("test\n", encoding="utf-8")
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-m", "base")
        base_sha = _git(repository, "rev-parse", "HEAD")
        _git(repository, "update-ref", "refs/remotes/origin/main", base_sha)
        refs_before = _git(repository, "show-ref")

        private = Ed25519PrivateKey.generate()
        private_text = base64.b64encode(private.private_bytes_raw()).decode()
        public_text = base64.b64encode(private.public_key().public_bytes_raw()).decode()
        key_id = "clean-environment-test-key"
        public_environment = {
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": public_text,
            "UPSTREAM_TRUST_REGISTRY_KEY_ID": key_id,
        }
        raw_runtime, raw_release = _fixture_evidence(root / "raw", "7.14.2")
        inspection = root / "registry-inspection"
        prepare_inspection_artifact(
            output_directory=inspection,
            operation="bootstrap",
            upstream_version="7.14.2",
            expected_current_sequence=0,
            expiry_days=90,
            operator_reason="clean-environment-review",
            workflow_base_sha=base_sha,
            dispatch_sha=base_sha,
            contract_family=CONTRACT_FAMILY,
            runtime_evidence=raw_runtime,
            release_evidence=raw_release,
        )

        environment_root = root / "clean-venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment_root)
        python = _python(environment_root)
        clean_environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(ROOT),
        }
        install = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--require-hashes",
                "-r",
                str(lock),
            ],
            cwd=ROOT,
            environment=clean_environment,
        )
        if install.returncode != 0:
            raise RuntimeError("clean_environment_install_failed")
        forbidden = _run(
            [
                str(python),
                "-c",
                (
                    "import importlib.util\n"
                    "failed=[]\n"
                    "for name in ('aiohttp','mcp','ha_mcp_engineering.application'):\n"
                    "    try:\n"
                    "        present=importlib.util.find_spec(name) is not None\n"
                    "    except ModuleNotFoundError:\n"
                    "        present=False\n"
                    "    if present: failed.append(name)\n"
                    "raise SystemExit(1 if failed else 0)\n"
                ),
            ],
            cwd=ROOT,
            environment=clean_environment,
        )
        if forbidden.returncode != 0:
            raise RuntimeError("clean_environment_forbidden_dependency_present")
        freeze = _run(
            [str(python), "-m", "pip", "freeze", "--all"],
            cwd=ROOT,
            environment=clean_environment,
        )
        installed = {
            line.split("==", 1)[0].lower()
            for line in freeze.stdout.splitlines()
            if "==" in line
        }
        if installed - {"cryptography", "cffi", "pycparser", "pip", "setuptools"}:
            raise RuntimeError("clean_environment_dependency_surface_expanded")

        cli = ROOT / "scripts" / "protected_sign_upstream_registry.py"
        common = _common_arguments(base_sha=base_sha, version="7.14.2", key_id=key_id)
        prepared = root / "prepared-signing"
        signatures = root / "signature-fragments"
        signed = root / "signed-registry"
        outputs: list[str] = []
        prepare = _run(
            [
                str(python),
                str(cli),
                "--phase",
                "prepare-signing",
                "--inspection-directory",
                str(inspection),
                "--prepared-directory",
                str(prepared),
                "--repository",
                str(repository),
                *common,
            ],
            cwd=ROOT,
            environment={**clean_environment, **public_environment},
        )
        if prepare.returncode != 0:
            raise RuntimeError("clean_environment_prepare_failed")
        outputs.extend([prepare.stdout, prepare.stderr])
        sign = _run(
            [
                str(python),
                str(cli),
                "--phase",
                "sign",
                "--prepared-directory",
                str(prepared),
                "--signature-directory",
                str(signatures),
                *common,
            ],
            cwd=ROOT,
            environment={
                **clean_environment,
                **public_environment,
                "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY": private_text,
            },
        )
        if sign.returncode != 0:
            raise RuntimeError("clean_environment_sign_failed")
        outputs.extend([sign.stdout, sign.stderr])
        verify = _run(
            [
                str(python),
                str(cli),
                "--phase",
                "verify-artifacts",
                "--prepared-directory",
                str(prepared),
                "--signature-directory",
                str(signatures),
                "--output-directory",
                str(signed),
                "--repository",
                str(repository),
                *common,
            ],
            cwd=ROOT,
            environment={**clean_environment, **public_environment},
        )
        if verify.returncode != 0:
            raise RuntimeError("clean_environment_verify_failed")
        outputs.extend([verify.stdout, verify.stderr])

        negative_count = 0
        wrong_root = _run(
            [
                str(python),
                str(cli),
                "--phase",
                "prepare-signing",
                "--inspection-directory",
                str(inspection.parent),
                "--prepared-directory",
                str(root / "wrong-prepared"),
                "--repository",
                str(repository),
                *common,
            ],
            cwd=ROOT,
            environment={**clean_environment, **public_environment},
        )
        _require_failure(wrong_root)
        negative_count += 1
        for artifact_mutation in ("missing", "extra"):
            altered_inspection = root / f"inspection-{artifact_mutation}"
            shutil.copytree(inspection, altered_inspection)
            if artifact_mutation == "missing":
                (altered_inspection / "runtime-evidence.json").unlink()
            else:
                (altered_inspection / "unexpected.json").write_text("{}\n")
            result = _run(
                [
                    str(python),
                    str(cli),
                    "--phase",
                    "prepare-signing",
                    "--inspection-directory",
                    str(altered_inspection),
                    "--prepared-directory",
                    str(root / f"negative-inspection-{artifact_mutation}"),
                    "--repository",
                    str(repository),
                    *common,
                ],
                cwd=ROOT,
                environment={**clean_environment, **public_environment},
            )
            _require_failure(result)
            negative_count += 1
        for name, value in (
            ("operation", "add"),
            ("version", "7.14.3"),
            ("expiry", "91"),
            ("reason", "different-review"),
        ):
            altered = list(common)
            option = {
                "operation": "--expected-operation",
                "version": "--expected-upstream-version",
                "expiry": "--expected-expiry-days",
                "reason": "--expected-operator-reason",
            }[name]
            altered[altered.index(option) + 1] = value
            result = _run(
                [
                    str(python),
                    str(cli),
                    "--phase",
                    "prepare-signing",
                    "--inspection-directory",
                    str(inspection),
                    "--prepared-directory",
                    str(root / f"negative-{name}"),
                    "--repository",
                    str(repository),
                    *altered,
                ],
                cwd=ROOT,
                environment={**clean_environment, **public_environment},
            )
            _require_failure(result)
            negative_count += 1
        preview = inspection / "unsigned-candidate.json"
        preview.write_text("{}\n", encoding="utf-8")
        preview_result = _run(
            [
                str(python),
                str(cli),
                "--phase",
                "prepare-signing",
                "--inspection-directory",
                str(inspection),
                "--prepared-directory",
                str(root / "negative-preview"),
                "--repository",
                str(repository),
                *common,
            ],
            cwd=ROOT,
            environment={**clean_environment, **public_environment},
        )
        _require_failure(preview_result, "artifact_file_set_mismatch")
        preview.unlink()
        negative_count += 1

        for wheel_mutation in ("missing", "altered", "extra"):
            copied = root / f"wheelhouse-{wheel_mutation}"
            shutil.copytree(wheelhouse, copied)
            first = next(copied.glob("*.whl"))
            if wheel_mutation == "missing":
                first.unlink()
            elif wheel_mutation == "altered":
                first.write_bytes(first.read_bytes() + b"changed")
            else:
                (copied / "extra.whl").write_bytes(b"extra")
            try:
                verify_wheelhouse(copied, lock)
            except WheelhouseVerificationError:
                negative_count += 1
            else:
                raise RuntimeError("negative_wheelhouse_case_unexpected_success")
        incomplete_lock = root / "incomplete-signing.lock"
        lock_lines = lock.read_text(encoding="utf-8").splitlines()
        incomplete_lock.write_text(
            "\n".join(line for line in lock_lines if not line.startswith("pycparser=="))
            + "\n",
            encoding="utf-8",
        )
        try:
            verify_wheelhouse(wheelhouse, incomplete_lock)
        except WheelhouseVerificationError:
            negative_count += 1
        else:
            raise RuntimeError("incomplete_dependency_lock_unexpected_success")

        combined = "".join(outputs)
        if private_text in combined:
            raise RuntimeError("private_key_exposed_in_output")
        for path in (inspection, prepared, signatures, signed):
            for file in path.rglob("*"):
                if file.is_file() and private_text.encode() in file.read_bytes():
                    raise RuntimeError("private_key_exposed_in_artifact")
        if _git(repository, "show-ref") != refs_before:
            raise RuntimeError("clean_environment_git_ref_changed")
        if not (signed / "tree" / REGISTRY_PATH).is_file():
            raise RuntimeError("clean_environment_signed_set_missing")
        return {
            "schema_version": 1,
            "clean_environment": True,
            "artifact_paths_exact": True,
            "protected_phases": ["prepare-signing", "sign", "verify-artifacts"],
            "installed_signing_distributions": sorted(
                installed & {"cryptography", "cffi", "pycparser"}
            ),
            "negative_cases": negative_count,
            "private_key_exposed": False,
            "repository_ref_changed": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_acceptance(args.wheelhouse, args.lock)
    except Exception as exc:
        category = str(exc)
        if len(category) > 128 or not category.replace("_", "").isalnum():
            category = "clean_environment_acceptance_failed"
        print(category, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
