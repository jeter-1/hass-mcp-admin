"""HAMCP-134: regenerate committed Core source-hash records from exact archives.

Offline and deterministic. Each archive is verified against its committed
release provenance before any member is read; members are read, never
extracted. A manifest path is hashed only when it is a regular file in the
archive; absence is recorded by omission, never by an invented digest.

Writes (or, with --check, compares byte-for-byte without writing):
  docs/evidence/hamcp315/core-sources/<version>.json
  docs/evidence/hamcp315/manifest-compatibility.json (manifest_paths, missing_paths)
  docs/evidence/hamcp134/lane/core-security-metadata-source-hashes.json (2026.11.0 only)
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[4]
MANIFEST = ROOT / "ci/core-contract-read-files.json"
RECORDS = ROOT / "docs/evidence/hamcp315/core-sources"
COMPATIBILITY = ROOT / "docs/evidence/hamcp315/manifest-compatibility.json"
SECURITY_METADATA = ROOT / "docs/evidence/hamcp134/lane/core-security-metadata-source-hashes.json"
CAPABILITY = "core.security_metadata_read"
TARGET = "2026.11.0"


def render(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def provenance(version):
    sys.path.insert(0, str(ROOT / "scripts"))
    from core_lane_bindings import REVIEWED_CORE_BINDINGS, provenance_name
    fixture = json.loads((ROOT / "tests/fixtures" / provenance_name(version)).read_text())
    if fixture["image_index_digest"] != REVIEWED_CORE_BINDINGS[version].image_index_digest:
        raise ValueError(version + ": provenance is not the reviewed binding")
    return fixture


def archive_hashes(version, archive, paths):
    fixture = provenance(version)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != fixture["source_archive_sha256"]:
        raise ValueError(version + ": archive SHA-256 does not match committed provenance")
    prefix = "core-" + fixture["source_commit"] + "/"
    wanted, hashes = set(paths), {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name == prefix[:-1] and member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError(version + ": unexpected archive top directory")
            name = member.name[len(prefix):]
            if name in wanted and member.isreg():
                if name in hashes:
                    raise ValueError(version + ": duplicate archive member " + name)
                hashes[name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    record = {"version": version, "commit": fixture["source_commit"], "tag_commit": fixture["source_commit"],
              "tree": fixture["source_tree"], "archive_sha256": fixture["source_archive_sha256"],
              "hashes": {p: hashes[p] for p in paths if p in hashes}}
    return fixture, record


def outputs(archives):
    manifest = json.loads(MANIFEST.read_text())
    paths = manifest["paths"]
    if paths != sorted(set(paths)):
        raise ValueError("manifest paths must be sorted and unique")
    files, records = {}, {}
    for version, archive in archives.items():
        fixture, record = archive_hashes(version, archive, paths)
        records[version] = (fixture, record)
        files[RECORDS / (version + ".json")] = render(record)
    compatibility = json.loads(COMPATIBILITY.read_text())
    compatibility["manifest_paths"] = len(paths)
    for item in compatibility["sources"]:
        fixture, record = records[item["version"]]
        if (item["source_commit"], item["source_archive_sha256"]) != (
                fixture["source_commit"], fixture["source_archive_sha256"]):
            raise ValueError(item["version"] + ": compatibility provenance mismatch")
        item["missing_paths"] = [p for p in paths if p not in record["hashes"]]
    files[COMPATIBILITY] = render(compatibility)
    profile = manifest["profiles"].get(CAPABILITY)
    if profile is not None and TARGET in records:
        fixture, record = records[TARGET]
        missing = [p for p in profile if p not in record["hashes"]]
        if missing:
            raise ValueError("security metadata profile paths absent at " + TARGET + ": " + ", ".join(missing))
        files[SECURITY_METADATA] = render({
            "core_version": TARGET, "source_commit": fixture["source_commit"],
            "source_archive_sha256": fixture["source_archive_sha256"],
            "files": [{"path": p, "sha256": record["hashes"][p]} for p in profile]})
    return files


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
    if {p.stem for p in RECORDS.glob("*.json")} != set(archives):
        raise ValueError("every committed source record needs its exact archive")
    files = outputs(archives)
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
