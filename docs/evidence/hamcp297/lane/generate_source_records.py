"""HAMCP-297: regenerate committed Core source-hash records from exact archives.

Offline and deterministic. It reuses the HAMCP-290 generator unchanged for the
shared O3 records (docs/evidence/hamcp315/...) and the Marketplace record, and
adds only the device configuration entity record for 2026.11.0:

  docs/evidence/hamcp297/lane/core-device-configuration-source-hashes.json

Archives are verified against committed release provenance before any member is
read; members are read, never extracted. A profile path absent from the exact
archive refuses; no digest is invented. --check compares byte-for-byte and
writes nothing.
"""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "docs/evidence/hamcp290/lane/generate_source_records.py"
DEVICE_CONFIGURATION = ROOT / "docs/evidence/hamcp297/lane/core-device-configuration-source-hashes.json"
CAPABILITY = "core.device_configuration_entity_operation"
TARGET = "2026.11.0"


def base_module():
    spec = importlib.util.spec_from_file_location("hamcp290_generate_source_records", BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outputs(archives):
    base = base_module()
    files = base.outputs(archives)
    manifest = json.loads(base.MANIFEST.read_text())
    profile = manifest["profiles"].get(CAPABILITY)
    if profile is None:
        raise ValueError(CAPABILITY + " profile missing from the manifest")
    if TARGET not in archives:
        raise ValueError("the " + TARGET + " archive is required")
    fixture, record = base.archive_hashes(TARGET, archives[TARGET], manifest["paths"])
    missing = [p for p in profile if p not in record["hashes"]]
    if missing:
        raise ValueError("device configuration profile paths absent at " + TARGET + ": " + ", ".join(missing))
    files[DEVICE_CONFIGURATION] = base.render({
        "core_version": TARGET, "source_commit": fixture["source_commit"],
        "source_archive_sha256": fixture["source_archive_sha256"],
        "files": [{"path": p, "sha256": record["hashes"][p]} for p in profile]})
    return files, base


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", action="append", required=True, metavar="VERSION=PATH",
                        help="exact Core source archive; repeat for each committed record")
    parser.add_argument("--check", action="store_true", help="compare committed bytes; write nothing")
    args = parser.parse_args()
    archives = {}
    for item in args.archive:
        version, _, path = item.partition("=")
        archives[version] = Path(path)
    base = base_module()
    if {p.stem for p in base.RECORDS.glob("*.json")} != set(archives):
        raise ValueError("every committed source record needs its exact archive")
    files, _ = outputs(archives)
    stale = [str(p.relative_to(ROOT)) for p, data in files.items() if not p.exists() or p.read_bytes() != data]
    if args.check:
        print("stale: " + ", ".join(stale) if stale else "all records reproduce byte-for-byte")
        return 1 if stale else 0
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print("wrote: " + ", ".join(stale) if stale else "no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
