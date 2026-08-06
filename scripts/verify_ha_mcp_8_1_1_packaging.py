"""Verify exact ha-mcp 8.1.1 vendoring and Home Assistant isolation contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


EXPECTED_VERSION = "8.1.1"
EXPECTED_VENDOR_VERSION = "17.0.1"
EXPECTED_SOURCE_COMMIT = "ae84694b50bfbd8d507042381fdee5e529bf73c5"
EXPECTED_SOURCE_ARCHIVE_SHA256 = (
    "bc631f50d3efd22234430891bbf66f55bbcd1cdea775e2eb7ae1b41b5feabe79"
)
_SHARED_IMPORT = re.compile(
    r"^\s*(?:import websockets\b|from websockets(?:\.|\s+import\b))",
    re.MULTILINE,
)


class PackagingAcceptanceFailure(RuntimeError):
    """The exact reviewed packaging contract changed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackagingAcceptanceFailure(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(vendor_root: Path) -> int:
    manifest = vendor_root / "MANIFEST.sha256"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    ordered_paths: list[str] = []
    for line in lines:
        matched = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(matched is not None, "vendored manifest record malformed")
        digest, relative = matched.groups()
        require(relative not in seen, "vendored manifest path duplicated")
        require(relative != "MANIFEST.sha256", "manifest hashes itself")
        seen.add(relative)
        ordered_paths.append(relative)
        path = vendor_root / relative
        require(path.is_file(), "vendored manifest file missing")
        require(file_sha256(path) == digest, "vendored manifest digest mismatch")
    require(ordered_paths == sorted(ordered_paths), "vendored manifest order changed")
    actual = {
        path.relative_to(vendor_root).as_posix()
        for path in vendor_root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.sha256"
    }
    require(actual == seen, "vendored manifest file accounting mismatch")
    return len(seen)


def verify_source(source_root: Path) -> dict[str, object]:
    pyproject_path = source_root / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    require(pyproject["project"]["version"] == EXPECTED_VERSION, "version changed")
    dependency_names = {
        canonicalize_name(Requirement(item).name)
        for item in pyproject["project"]["dependencies"]
    }
    require("websockets" not in dependency_names, "shared dependency declared")

    vendor_root = source_root / "src/ha_mcp/_vendor/websockets"
    marker = (vendor_root / "VENDORED").read_text(encoding="utf-8")
    require(
        f"websockets=={EXPECTED_VENDOR_VERSION}" in marker,
        "vendored version marker changed",
    )
    manifest_files = verify_manifest(vendor_root)

    scan_roots = (
        source_root / "src/ha_mcp",
        source_root / "custom_components/ha_mcp_tools",
    )
    vendor_parent = source_root / "src/ha_mcp/_vendor"
    offenders = []
    scanned = 0
    for root in scan_roots:
        for path in root.rglob("*.py"):
            if vendor_parent in path.parents:
                continue
            scanned += 1
            if _SHARED_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(source_root).as_posix())
    require(not offenders, "first-party source imports shared websockets")

    embedded = (
        source_root
        / "custom_components/ha_mcp_tools/embedded_server.py"
    ).read_text(encoding="utf-8")
    ws_settings = re.findall(r"\bws\s*=\s*[\"']([^\"']+)[\"']", embedded)
    require(ws_settings and set(ws_settings) == {"none"}, "listener loads websocket protocol")
    require(
        "--upgrade\"" not in embedded and "'--upgrade'" not in embedded,
        "unscoped dependency upgrade returned",
    )
    require(
        "--upgrade-package" in embedded and "--reinstall-package" in embedded,
        "scoped package replacement contract missing",
    )
    return {
        "result": "PASS",
        "model": "ha-mcp-8.1.1-vendored-websockets-isolation-v1",
        "upstream_version": EXPECTED_VERSION,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_archive_sha256": EXPECTED_SOURCE_ARCHIVE_SHA256,
        "vendored_websockets_version": EXPECTED_VENDOR_VERSION,
        "vendored_manifest_file_count": manifest_files,
        "first_party_python_files_scanned": scanned,
        "shared_websockets_dependency_declared": False,
        "shared_websockets_import_count": 0,
        "embedded_uvicorn_websocket_protocol": "none",
        "package_replacement_scope": "ha-mcp-distribution-only",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_source(args.source_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
