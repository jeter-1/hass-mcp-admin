#!/usr/bin/env python3
"""HAMCP-371 P3 (amended, ruling 2026-11-16 06:05 CST): Core 2026.11.0 authority from the seed.

The committed seed files are delivered exactly as the SR lane delivers them: the
harness's ``core_seed()`` and ``options_archive()`` build the tar that ``docker cp``
streams into Engineering's ``/data``, and it is extracted here into a temporary
``data/`` under the same final names, with nothing renamed or rewritten. Engineering's
``CoreReleaseRegistry`` at the branch head then loads them with the lane's key (the
committed ``docs/evidence/hamcp222/core-public-key.txt``) and a fetcher that always
fails the way a lane with no egress does (``aiohttp.ClientConnectionError``).

The authority for Core 2026.11.0 is read once at load and once more after one
``refresh()`` through that fetcher. PASS needs both readings to be: sequence 18, cache
``valid``, positive authority current, not surface-denied, the entry
``ha-core-2026.11.0-reviewed-v1`` with image index ``sha256:c6c1ca99...`` and source
commit ``74cf5a49...`` equal to the registry blob's sequence-18 entry, a probe profile,
and at least one selection, every one ``positive``. The refresh must return False, the
fetcher must be called once, and ``data/`` must still hold exactly the delivered files
with unchanged bytes. A witness that does not survive the copy shows as an invalid
cache or a surface denial and fails P3; it is never adjusted.

Prints the JSON result; ``--write`` also writes ``p3-core-authority.json``. Exit 0 on
PASS, 1 on FAIL, 2 when P3 cannot be evaluated. Uses the real clock.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "scripts"))

import aiohttp  # noqa: E402

import hamcp371_live_replay_local as harness  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import (  # noqa: E402
    CORE_IDENTITY, CoreAuthorityStatus)
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_CACHE_PATH, CoreReleaseRegistry)

SCHEMA = "hamcp371.sr.p3_core_authority.v1"
OUTPUT = PurePosixPath("docs/evidence/hamcp371/sr/p3-core-authority.json")
VERSION = "2026.11.0"
ENTRY_ID = "ha-core-2026.11.0-reviewed-v1"
IMAGE_PREFIX = "sha256:c6c1ca99"
SOURCE_PREFIX = "74cf5a49"
SEQUENCE = 18
EXPIRES_AT = "2027-02-12T12:09:02Z"
OPTIONS = "options.json"
ENTRY_FIELDS = ("entry_id", "version", "source_commit", "image_index_digest",
                "probe_profile_id")


class P3Error(Exception):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NoTransport:
    """A fetcher that fails like a lane with no egress."""

    def __init__(self):
        self.calls = []

    async def __call__(self, url: str, maximum: int) -> bytes:
        self.calls.append({"url": url, "maximum": maximum})
        raise aiohttp.ClientConnectionError("P3: no transport")


def blob_entry(root: Path = ROOT) -> dict:
    """The sequence-18 2026.11.0 entry read straight from the registry blob."""
    raw = subprocess.run(["git", "-C", str(root), "cat-file", "blob", harness.CORE_SEED_BLOB],
                         capture_output=True, check=True).stdout
    envelopes = [item for item in json.loads(raw)["envelopes"] if item["sequence"] == SEQUENCE]
    if len(envelopes) != 1:
        raise P3Error(f"the registry blob has {len(envelopes)} sequence-{SEQUENCE} envelopes")
    entries = [item for item in envelopes[0]["entries"]
               if item["server_name"] == CORE_IDENTITY and item["version"] == VERSION]
    if len(entries) != 1:
        raise P3Error(f"sequence {SEQUENCE} has {len(entries)} entries for {VERSION}")
    entry = entries[0]
    return {**{key: entry[key] for key in ENTRY_FIELDS},
            "capability_count": len(entry["capabilities"])}


def lane_data(workdir: Path, seed: dict) -> Path:
    """Extract the lane's options archive into ``data/``; the seed files must be unchanged."""
    data = workdir / "data"
    data.mkdir()
    with tarfile.open(fileobj=io.BytesIO(harness.options_archive(seed)), mode="r:") as archive:
        archive.extractall(data, filter="data")
    names = sorted(path.name for path in data.iterdir())
    if names != sorted((OPTIONS, *harness.CORE_SEED_FILES)):
        raise P3Error(f"the lane archive delivered {names}")
    for name in harness.CORE_SEED_FILES:
        if (data / name).read_bytes() != seed["data"][name]:
            raise P3Error(f"{name} changed in delivery")
    if CORE_REGISTRY_CACHE_PATH.name != harness.CORE_SEED_FILES[0]:
        raise P3Error("the seed's cache name is not Engineering's")
    return data


def files_state(data: Path) -> dict:
    return {path.name: {"sha256": _sha256(path.read_bytes()),
                        "mode": oct(path.stat().st_mode & 0o777)}
            for path in sorted(data.iterdir())}


def observe(registry: CoreReleaseRegistry) -> dict:
    snapshot, authority = registry.snapshot(), registry.authority()
    entry = authority.entry_for(CORE_IDENTITY, VERSION)
    probe = registry.probe_profile_for(VERSION)
    expires = str(authority.envelope.expires_at) if authority.envelope is not None else None
    return {
        "snapshot": {key: snapshot[key] for key in (
            "registry_id_status", "sequence", "freshness_status", "refresh_status",
            "cache_status", "last_failure_reason", "failure_reason_counts",
            "surface_denied")},
        "sequence": authority.sequence,
        "content_digest": authority.content_digest,
        "expires_at": expires,
        "positive_authority_current": authority.positive_authority_current,
        "surface_denied": authority.surface_denied,
        "revoked": authority.revoked(CORE_IDENTITY, VERSION),
        "entry": None if entry is None else {
            **{key: getattr(entry, key) for key in ENTRY_FIELDS},
            "capability_count": len(entry.capabilities)},
        "probe_profile_id": None if probe is None else getattr(probe, "profile_id", None),
        "probe_profile_present": probe is not None,
        "selections": [{"source": item.source.value, "status": item.status.value,
                        "profile_id": item.profile_id, "adapter_id": item.adapter_id,
                        "capability_ids": list(item.capability_ids),
                        "reason_code": item.reason_code}
                       for item in registry.selections(VERSION)],
    }


def failures_of(reading: dict, expected_entry: dict) -> list[str]:
    found = []
    if reading["sequence"] != SEQUENCE:
        found.append(f"sequence is {reading['sequence']}, not {SEQUENCE}")
    if reading["snapshot"]["cache_status"] != "valid":
        found.append(f"cache_status is {reading['snapshot']['cache_status']}")
    if reading["surface_denied"]:
        found.append("surface denied")
    if not reading["positive_authority_current"]:
        found.append("positive authority is not current")
    if reading["expires_at"] != EXPIRES_AT:
        found.append(f"expires_at is {reading['expires_at']}")
    if reading["revoked"]:
        found.append(f"{VERSION} is revoked")
    entry = reading["entry"]
    if entry is None:
        found.append(f"no entry for {VERSION}")
    else:
        if entry != expected_entry:
            found.append("the entry differs from the registry blob's sequence-18 entry")
        if (entry["entry_id"] != ENTRY_ID or not entry["image_index_digest"].startswith(IMAGE_PREFIX)
                or not entry["source_commit"].startswith(SOURCE_PREFIX)):
            found.append("the entry is not the ruled entry, image index or source commit")
    if not reading["probe_profile_present"]:
        found.append("no probe profile")
    if not reading["selections"]:
        found.append("no selections")
    if any(item["status"] != CoreAuthorityStatus.POSITIVE.value for item in reading["selections"]):
        found.append("a selection is not positive")
    return found


def p3(root: Path, workdir: Path) -> dict:
    seed = harness.core_seed()
    key = harness.engineering_options()["ha_core_release_registry_public_key"]
    expected = blob_entry(root)
    data = lane_data(workdir, seed)
    delivered = files_state(data)
    fetcher = NoTransport()
    registry = CoreReleaseRegistry(enabled=True, public_key=key,
                                   cache_path=data / CORE_REGISTRY_CACHE_PATH.name,
                                   fetcher=fetcher)
    at_load = observe(registry)
    refreshed = asyncio.run(registry.refresh())
    after = observe(registry)
    remaining = files_state(data)
    failures = ([f"at load: {item}" for item in failures_of(at_load, expected)]
                + [f"after the failed refresh: {item}" for item in failures_of(after, expected)])
    if refreshed is not False:
        failures.append(f"refresh() returned {refreshed!r} with no transport")
    if len(fetcher.calls) != 1:
        failures.append(f"the fetcher was called {len(fetcher.calls)} times")
    if remaining != delivered:
        failures.append("data/ changed across the failed refresh")
    return {
        "schema": SCHEMA,
        "ruling": "architect ruling 2026-11-16 06:05 CST, P3 (amended)",
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engineering": {"head": harness.git("rev-parse", "HEAD").decode("ascii").strip(),
                        "tree": harness.git("rev-parse", "HEAD:hass_mcp_engineering_beta").decode("ascii").strip()},
        "seed": harness.seed_summary(seed),
        "public_key": {"file": harness.CORE_KEY_FILE.relative_to(ROOT).as_posix(),
                       "sha256": _sha256(harness.CORE_KEY_FILE.read_bytes())},
        "delivery": "harness options_archive() extracted into data/ (tarfile filter=data)",
        "fetcher": {"kind": "always fails: aiohttp.ClientConnectionError", "calls": fetcher.calls},
        "files_delivered": delivered,
        "files_after_refresh": remaining,
        "expected_entry_from_blob": expected,
        "at_load": at_load,
        "refresh_returned": refreshed,
        "after_failed_refresh": after,
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"write {OUTPUT}")
    args = parser.parse_args(argv)
    scratch = ROOT / ".artifacts/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=scratch) as name:
            result = p3(ROOT, Path(name))
    except (P3Error, harness.Refusal) as exc:
        print(json.dumps({"p3_error": str(exc)}, indent=1))
        return 2
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    if args.write:
        (ROOT / OUTPUT).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
