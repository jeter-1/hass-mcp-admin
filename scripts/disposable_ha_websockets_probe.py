"""Probe the exact vendored websocket runtime inside disposable Home Assistant."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import sys
import types


def distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package_root = args.source_root.resolve() / "src/ha_mcp"
    if not package_root.is_dir():
        raise SystemExit("exact source package is unavailable")

    shared_before = distribution_version("websockets")
    shared_imported_before = "websockets" in sys.modules
    package = types.ModuleType("ha_mcp")
    package.__path__ = [str(package_root)]
    sys.modules["ha_mcp"] = package
    from ha_mcp._vendor import websockets as vendored

    shared_after = distribution_version("websockets")
    shared_imported_after = "websockets" in sys.modules
    vendor_path = Path(vendored.__file__).resolve()
    if vendored.__version__ != "17.0.1":
        raise SystemExit("vendored websocket version changed")
    if package_root not in vendor_path.parents:
        raise SystemExit("vendored import escaped exact source tree")
    if shared_before != shared_after:
        raise SystemExit("shared Home Assistant websocket distribution changed")
    if not shared_imported_before and shared_imported_after:
        raise SystemExit("vendored import loaded shared websockets")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "result": "PASS",
                "model": "disposable-ha-vendored-websockets-isolation-v1",
                "vendored_version": vendored.__version__,
                "vendored_path_scoped_to_exact_source": True,
                "shared_distribution_version_before": shared_before,
                "shared_distribution_version_after": shared_after,
                "shared_distribution_unchanged": True,
                "vendored_import_loaded_shared_package": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
