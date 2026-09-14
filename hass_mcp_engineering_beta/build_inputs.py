#!/usr/bin/env python3
"""Offline build/inventory checks. Never imported by Engineering startup."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys


PLATFORMS = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]*\Z")
FIELDS = {"schema_version", "python_version", "installer_version", "base_image",
          "base_platforms", "requirements_sha256", "runtime_lock_sha256", "runtime"}


def fail(reason: str):
    raise ValueError(reason)


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_bytes(path: Path, limit: int = 262_144) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if not value or len(value) > limit:
        fail("BUILD_INPUT_BOUND_INVALID")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("BUILD_INPUT_DUPLICATE_FIELD")
        result[key] = value
    return result


def parse_lock(raw: bytes) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Accept exact pins and hashes only; no URLs, indexes, includes or markers."""
    result = {}
    for line in raw.decode("utf-8").replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        name, separator, version = parts[0].partition("==")
        if (not separator or not NAME.fullmatch(name) or not VERSION.fullmatch(version)
                or name in result or len(parts) < 2 or len(parts) > 129):
            fail("BUILD_LOCK_PIN_INVALID")
        hashes = []
        for field in parts[1:]:
            if not field.startswith("--hash=sha256:") or not HASH.fullmatch(field[14:]):
                fail("BUILD_LOCK_HASH_INVALID")
            hashes.append(field[14:])
        if len(set(hashes)) != len(hashes):
            fail("BUILD_LOCK_DUPLICATE_HASH")
        result[name] = (version, tuple(hashes))
    if not 1 <= len(result) <= 512:
        fail("BUILD_LOCK_COUNT_INVALID")
    return result


def load_inputs(root: Path) -> dict:
    raw = read_bytes(root / "build-inputs.json")
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_constant=lambda _: fail("BUILD_INPUT_NONFINITE"))
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail("BUILD_INPUT_SCHEMA_INVALID")
    if not re.fullmatch(r"3\.12\.[0-9]+", str(value["python_version"])):
        fail("BUILD_PYTHON_INVALID")
    if not isinstance(value["installer_version"], str) or not VERSION.fullmatch(value["installer_version"]):
        fail("BUILD_INSTALLER_INVALID")
    if not re.fullmatch(r"python:3\.12-slim@sha256:[0-9a-f]{64}", str(value["base_image"])):
        fail("BUILD_BASE_INVALID")
    bases = value["base_platforms"]
    if not isinstance(bases, dict) or set(bases) != set(PLATFORMS):
        fail("BUILD_PLATFORM_SET_INVALID")
    for item in bases.values():
        if not isinstance(item, dict) or set(item) != {"manifest_digest", "configuration_digest"}:
            fail("BUILD_BASE_DESCRIPTOR_INVALID")
        if any(not isinstance(x, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", x) for x in item.values()):
            fail("BUILD_BASE_DIGEST_INVALID")
    lock = read_bytes(root / "requirements.lock")
    requirements = read_bytes(root / "requirements.txt")
    for raw_file, field in ((lock, "runtime_lock_sha256"), (requirements, "requirements_sha256")):
        if hashlib.sha256(raw_file).hexdigest() != value[field]:
            fail("BUILD_INPUT_HASH_MISMATCH")
    expected = {name: version for name, (version, _) in parse_lock(lock).items()}
    if value["runtime"] != expected or "pip" in expected:
        fail("BUILD_RUNTIME_LOCK_MISMATCH")
    for line in requirements.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator or expected.get(normalized(name)) != version:
            fail("BUILD_DIRECT_REQUIREMENT_MISMATCH")
    return value


def verify_source(root: Path) -> dict:
    value = load_inputs(root)
    dockerfile = read_bytes(root / "Dockerfile").decode()
    bases = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ")]
    if bases != [value["base_image"], value["base_image"]]:
        fail("BUILD_DOCKER_BASE_MISMATCH")
    return {"status": "PASS", "base_image": value["base_image"],
            "runtime_lock_sha256": value["runtime_lock_sha256"],
            "runtime_distribution_count": len(value["runtime"])}


def installed_inventory() -> dict[str, str]:
    result = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not NAME.fullmatch(normalized(name)):
            fail("BUILD_INSTALLED_NAME_INVALID")
        name = normalized(name)
        if name in result:
            fail("BUILD_INSTALLED_DUPLICATE")
        result[name] = distribution.version
    return dict(sorted(result.items()))


def verify_installed(root: Path, expected_platform: str | None = None) -> dict:
    value = load_inputs(root)
    if platform.python_version() != value["python_version"]:
        fail("BUILD_INSTALLED_PYTHON_MISMATCH")
    actual_platform = next((name for name, machine in PLATFORMS.items()
                            if platform.machine() == machine), None)
    if actual_platform is None or (expected_platform is not None and actual_platform != expected_platform):
        fail("BUILD_INSTALLED_PLATFORM_MISMATCH")
    actual = installed_inventory()
    expected = {**value["runtime"], "pip": value["installer_version"]}
    if actual != expected:
        fail("BUILD_INSTALLED_INVENTORY_MISMATCH")
    return {"status": "PASS", "platform": actual_platform,
            "python_version": platform.python_version(),
            "runtime_lock_sha256": value["runtime_lock_sha256"],
            "distributions": actual}


def smoke(root: Path, expected_platform: str | None = None) -> dict:
    result = verify_installed(root, expected_platform)
    import aiohttp
    import cffi
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import httpx
    import jinja2
    import jsonschema
    import mcp
    import pydantic
    import starlette
    import uvicorn
    import yaml
    from ha_mcp_engineering import version

    # Pure local behavior, including native dependency loading; no app startup,
    # HTTP client/session, HA configuration, service or provider dispatch.
    payload = yaml.safe_load("value: 7\n")
    jsonschema.validate(payload, {"type": "object", "required": ["value"]})
    if jinja2.Template("{{ value + 1 }}").render(payload) != "8":
        fail("BUILD_TEMPLATE_SMOKE_FAILED")
    cipher = AESGCM(bytes(range(32)))  # Public synthetic test material only.
    nonce = bytes(range(12))
    encrypted = cipher.encrypt(nonce, b"build smoke", b"synthetic")
    if cipher.decrypt(nonce, encrypted, b"synthetic") != b"build smoke":
        fail("BUILD_CRYPTO_SMOKE_FAILED")
    if cffi.FFI().sizeof("int") <= 0 or pydantic.TypeAdapter(dict[str, int]).validate_python(payload) != payload:
        fail("BUILD_NATIVE_SMOKE_FAILED")
    result.update(engineering_version=version.SERVER_VERSION,
                  yaml_accelerator_available=bool(getattr(yaml, "__with_libyaml__", False)),
                  smoke="PASS")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("source", "inventory", "smoke"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--expected-platform", choices=tuple(PLATFORMS))
    args = parser.parse_args(argv)
    try:
        result = verify_source(args.root) if args.command == "source" else (
            smoke(args.root, args.expected_platform) if args.command == "smoke"
            else verify_installed(args.root, args.expected_platform))
    except (OSError, ValueError, UnicodeError, ImportError, RuntimeError):
        print(json.dumps({"status": "FAIL", "category": "BUILD_INPUT_VERIFICATION_FAILED"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
