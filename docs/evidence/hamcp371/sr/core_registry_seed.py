#!/usr/bin/env python3
"""HAMCP-371 seeding ruling (a), 2026-11-16 06:05 CST: the lane's Core registry cache.

Engineering's own ``CoreReleaseRegistry.refresh()`` runs once, offline, in a
temporary directory whose ``data/`` stands for the lane's ``/data``. Its fetcher is
local: it accepts only Engineering's Core registry URL and returns the exact blob
``upstream-trust/ha-core-release-registry.json`` at registry commit ``917bfa2a``
(``git cat-file``; git blob id and sha256 both checked). The key is the committed
``docs/evidence/hamcp222/core-public-key.txt``. ``refresh()`` writes the cache and
its ``.lifecycle`` witness under the lane's names; both are copied unchanged into
``core-registry-seed/`` and their sha256s recorded. Nothing here writes or edits
cache or witness content.

Known difference from production: same verification and write path, local
transport instead of GitHub.

``--write`` produces the committed files and the record; the default only
regenerates in a temporary directory and compares with the committed files.
Needs Engineering's dependencies (the worktree venv); exit 2 on any difference.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_CACHE_PATH, CORE_REGISTRY_URL, CoreReleaseRegistry)

REGISTRY_COMMIT = "917bfa2a59da5f996fb3d46a73760353547d2700"
REGISTRY_PATH = "upstream-trust/ha-core-release-registry.json"
BLOB_ID = "6d91c9b243cd7c0414d134e6620718903ef8fc33"
BLOB_SHA256 = "e792670dd22586cfa3d2fbbfaacf6165feb85bcb435e33f69bc712f7db27b092"
KEY_FILE = "docs/evidence/hamcp222/core-public-key.txt"
SEED_DIR = PurePosixPath("docs/evidence/hamcp371/sr/core-registry-seed")
RECORD = "seed-record.json"
RECORD_SCHEMA = "hamcp371.sr.core_registry_seed.v1"
LANE_DATA = PurePosixPath("/data")
CACHE_NAME = CORE_REGISTRY_CACHE_PATH.name
WITNESS_NAME = CACHE_NAME + ".lifecycle"
SEED_FILES = (CACHE_NAME, WITNESS_NAME)
KNOWN_DIFFERENCE = "same verification and write path, local transport instead of GitHub"


class SeedError(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def registry_blob(root: Path = ROOT) -> bytes:
    """The exact registry bytes at the cited commit, checked by git id and sha256."""
    spec = f"{REGISTRY_COMMIT}:{REGISTRY_PATH}"
    found = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", spec],
                           capture_output=True, text=True)
    if found.returncode != 0 or found.stdout.strip() != BLOB_ID:
        raise SeedError(f"{spec} is not blob {BLOB_ID}")
    blob = subprocess.run(["git", "-C", str(root), "cat-file", "blob", BLOB_ID],
                          capture_output=True, check=True).stdout
    if _sha256(blob) != BLOB_SHA256:
        raise SeedError("registry blob sha256 differs from the ruling")
    return blob


def public_key(root: Path = ROOT) -> str:
    return (root / KEY_FILE).read_text(encoding="utf-8").strip()


class LocalFetcher:
    """Serves the registry blob for Engineering's Core registry URL only."""

    def __init__(self, blob: bytes):
        self.blob, self.calls = blob, []

    async def __call__(self, url: str, maximum: int) -> bytes:
        self.calls.append({"url": url, "maximum": maximum})
        if url != CORE_REGISTRY_URL:
            raise SeedError(f"fetcher asked for an unexpected URL: {url}")
        if len(self.blob) > maximum:
            raise SeedError("registry blob exceeds the fetch limit")
        return self.blob


def authority_summary(registry: CoreReleaseRegistry) -> dict:
    snapshot = registry.snapshot()
    authority = registry.authority()
    return {"sequence": authority.sequence, "content_digest": authority.content_digest,
            "positive_authority_current": authority.positive_authority_current,
            "surface_denied": authority.surface_denied,
            "expires_at": (str(authority.envelope.expires_at)
                           if authority.envelope is not None else None),
            "cache_status": snapshot["cache_status"],
            "refresh_status": snapshot["refresh_status"],
            "freshness_status": snapshot["freshness_status"]}


def refresh_once(root: Path, workdir: Path, now=None) -> dict:
    """One offline refresh(); returns the bytes it wrote and the authority after it.

    ``now`` is for tests only; the seed itself uses Engineering's default clock.
    """
    if CORE_REGISTRY_CACHE_PATH.parent != Path(str(LANE_DATA)):
        raise SeedError("Engineering's Core cache path is not under /data")
    blob, key = registry_blob(root), public_key(root)
    data = workdir / "data"
    data.mkdir()
    fetcher = LocalFetcher(blob)
    registry = CoreReleaseRegistry(enabled=True, public_key=key, cache_path=data / CACHE_NAME,
                                   fetcher=fetcher, now=now)
    if asyncio.run(registry.refresh()) is not True:
        raise SeedError(f"refresh() did not accept the registry: {registry.snapshot()}")
    if len(fetcher.calls) != 1:
        raise SeedError(f"refresh() fetched {len(fetcher.calls)} times")
    written = sorted(path.name for path in data.iterdir())
    if written != sorted(SEED_FILES):
        raise SeedError(f"refresh() left {written}, not {sorted(SEED_FILES)}")
    return {"data": data, "blob": blob, "key": key, "fetch": fetcher.calls[0],
            "files": {name: (data / name).read_bytes() for name in SEED_FILES},
            "modes": {name: oct((data / name).stat().st_mode & 0o777) for name in SEED_FILES},
            "authority": authority_summary(registry)}


def record_for(root: Path, result: dict) -> dict:
    return {
        "schema": RECORD_SCHEMA,
        "ruling": "architect ruling 2026-11-16 06:05 CST, seeding (a)",
        "registry": {"commit": REGISTRY_COMMIT, "path": REGISTRY_PATH, "blob": BLOB_ID,
                     "sha256": _sha256(result["blob"]), "bytes": len(result["blob"])},
        "public_key": {"file": KEY_FILE, "sha256": _sha256((root / KEY_FILE).read_bytes())},
        "fetch": {"transport": "local, in-process", "url": result["fetch"]["url"],
                  "calls": 1},
        "writer": "ha_mcp_engineering CoreReleaseRegistry.refresh()",
        "lane_paths": {name: str(LANE_DATA / name) for name in SEED_FILES},
        "files": {name: {"sha256": _sha256(data), "bytes": len(data),
                         "mode_written": result["modes"][name]}
                  for name, data in result["files"].items()},
        "authority_after_refresh": result["authority"],
        "known_difference": KNOWN_DIFFERENCE,
    }


def committed(root: Path = ROOT) -> dict:
    """The committed seed files and record; the record's sha256s must hold."""
    directory = root / SEED_DIR
    record = json.loads((directory / RECORD).read_text(encoding="utf-8"))
    files = {name: (directory / name).read_bytes() for name in SEED_FILES}
    for name, data in files.items():
        if _sha256(data) != record["files"][name]["sha256"]:
            raise SeedError(f"{SEED_DIR / name} differs from its recorded sha256")
    return {"record": record, "files": files}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="copy refresh()'s files into core-registry-seed/ with the record")
    args = parser.parse_args(argv)
    scratch = ROOT / ".artifacts/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            result = refresh_once(ROOT, Path(name))
            record = record_for(ROOT, result)
            target = ROOT / SEED_DIR
            if args.write:
                target.mkdir(parents=False, exist_ok=False)
                for seed in SEED_FILES:
                    shutil.copyfile(result["data"] / seed, target / seed)
                (target / RECORD).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
            seed = committed(ROOT)
            if seed["files"] != result["files"]:
                raise SeedError("a fresh refresh() wrote bytes that differ from the committed seed")
            if seed["record"]["files"] != record["files"]:
                raise SeedError("the committed record differs from a fresh refresh()")
    except SeedError as exc:
        print(json.dumps({"seed_error": str(exc)}, indent=1))
        return 2
    print(json.dumps(record, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
