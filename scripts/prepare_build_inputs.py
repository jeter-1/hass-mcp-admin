#!/usr/bin/env python3
"""Render hash locks from reviewed, wheel-only pip reports; performs no network IO."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "engineering_build_inputs", ROOT / "hass_mcp_engineering_beta/build_inputs.py")
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


def prepare(reports: list[Path], runtime_lock: Path | None = None) -> bytes:
    if not 1 <= len(reports) <= 4:
        raise ValueError("REPORT_COUNT_INVALID")
    packages = {}
    for path in reports:
        report = json.loads(inputs.read_bytes(path, 8_388_608),
                            object_pairs_hook=inputs.unique_object)
        if report.get("version") != "1" or report.get("pip_version") != "25.0.1":
            raise ValueError("REPORT_VERSION_INVALID")
        items = report.get("install")
        if not isinstance(items, list) or not 1 <= len(items) <= 512:
            raise ValueError("REPORT_COLLECTION_INVALID")
        seen = set()
        for item in items:
            metadata = item["metadata"]
            name = inputs.normalized(metadata["name"])
            version = metadata["version"]
            download = item["download_info"]
            url = urlsplit(download["url"])
            digest = download["archive_info"]["hashes"]["sha256"]
            if (not inputs.NAME.fullmatch(name) or not inputs.VERSION.fullmatch(version)
                    or name in seen or item.get("is_yanked") is not False
                    or item.get("is_direct") is not False
                    or url.scheme != "https" or url.hostname != "files.pythonhosted.org"
                    or url.username or url.password or url.port not in (None, 443)
                    or url.query or url.fragment or not url.path.endswith(".whl")
                    or not inputs.HASH.fullmatch(digest)):
                raise ValueError("REPORT_WHEEL_INVALID")
            seen.add(name)
            prior_version, hashes = packages.setdefault(name, (version, set()))
            if prior_version != version:
                raise ValueError("REPORT_VERSION_CONFLICT")
            hashes.add(digest)
    if runtime_lock is not None:
        runtime = inputs.parse_lock(inputs.read_bytes(runtime_lock))
        for name, (version, hashes) in runtime.items():
            actual = packages.get(name)
            if actual is None or actual[0] != version or not actual[1].issubset(hashes):
                raise ValueError("REPORT_RUNTIME_DRIFT")
            packages[name] = (version, set(hashes))
    lines = ["# Exact wheel inputs; generated from reviewed pip 25.0.1 reports.",
             "# See docs/BUILD_INPUTS.md for the bounded update procedure."]
    for name, (version, hashes) in sorted(packages.items()):
        lines.append(f"{name}=={version} \\")
        fields = [f"    --hash=sha256:{digest}" for digest in sorted(hashes)]
        lines.append(" \\\n".join(fields))
    result = ("\n".join(lines) + "\n").encode()
    inputs.parse_lock(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.report, args.runtime_lock)
        with args.output.open("xb") as stream:
            stream.write(result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print('{"status":"FAIL","category":"BUILD_LOCK_PREPARATION_FAILED"}')
        return 1
    print('{"status":"PASS"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
