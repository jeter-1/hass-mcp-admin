"""Verify the exact offline wheel set used by the protected signing job."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


EXPECTED_WHEELS = {
    "cryptography-45.0.7-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl":
        "577470e39e60a6cd7780793202e63536026d9b8641de011ed9d8174da9ca5339",
    "cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl":
        "1e9f50d192a3e525b15a75ab5114e442d83d657b7ec29182a991bc9a88fd3a66",
    "pycparser-3.0-py3-none-any.whl":
        "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
}
LOCK_PACKAGES = {"cryptography": "45.0.7", "cffi": "2.1.0", "pycparser": "3.0"}


class WheelhouseVerificationError(ValueError):
    """Bounded wheel-set integrity failure."""


def verify_wheelhouse(directory: Path, lock_path: Path) -> dict[str, object]:
    files = {path.name: path for path in directory.glob("*.whl") if path.is_file()}
    if set(files) != set(EXPECTED_WHEELS):
        raise WheelhouseVerificationError("signing_wheel_set_mismatch")
    lock = lock_path.read_text(encoding="utf-8")
    declarations = {
        match.group(1).lower(): match.group(2)
        for match in re.finditer(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock, re.MULTILINE)
    }
    if declarations != LOCK_PACKAGES:
        raise WheelhouseVerificationError("signing_dependency_lock_incomplete")
    for filename, expected in EXPECTED_WHEELS.items():
        actual = hashlib.sha256(files[filename].read_bytes()).hexdigest()
        if actual != expected or f"--hash=sha256:{expected}" not in lock:
            raise WheelhouseVerificationError("signing_wheel_hash_mismatch")
    return {
        "schema_version": 1,
        "wheel_count": len(files),
        "lock_complete": True,
        "all_hashes_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel_directory", type=Path)
    parser.add_argument("lock_file", type=Path)
    args = parser.parse_args()
    try:
        result = verify_wheelhouse(args.wheel_directory, args.lock_file)
    except (OSError, ValueError) as exc:
        message = str(exc)
        if len(message) > 128:
            message = "signing_wheel_verification_failed"
        print(message)
        return 2
    print(f"verified {result['wheel_count']} hash-locked signing wheels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
