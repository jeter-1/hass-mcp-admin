"""HAMCP-161 Part 2: verify the owner-signed sequence-9 journal against sequence 6.

PUBLIC key only; never reads a private key. Uses this checkout's registry code
(CoreReleaseRegistry with a synthetic fetcher; no network, no Home Assistant).

Proves, in one jump from the predecessor that main serves:
  * the final journal authenticates with the production public key and the
    predecessor's envelopes are an unchanged prefix of it;
  * 2026.10.1 is admitted with exactly the cleared candidate-1 entry
    (21 references) and the candidate SHA256 is the reviewed one;
  * 2026.9.2 and 2026.9.3 are revoked with the directed reason, nothing else is;
  * the 2026.10.0 and 2026.9.4 entries are byte-for-byte unchanged;
  * no envelope references core.entity_registry_metadata_operation.
Exit 0 and a final "OK" line only when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta")]
from ha_mcp_engineering.ha_core_readmission import CORE_IDENTITY  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import (  # noqa: E402
    CORE_REGISTRY_URL, CoreReleaseRegistry)
from ha_mcp_engineering.signed_registry import canonical_json  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
PREDECESSOR_SHA256 = "1d3530345c0d923bda17951c28ddf913e5c8e07d6fa69ca2d258302b418bbf4a"
PUBLIC_KEY_SHA256 = "7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac"
CANDIDATE_1_SHA256 = "faa529e8957178882b532731f154e1d7952755012e2a8ba30450d5dc68027d8b"
REASON = "outside the CI support window; retired by owner direction 2026-10-12 (HAMCP-161)"
ENTITY = "core.entity_registry_metadata_operation"

parser = argparse.ArgumentParser()
parser.add_argument("--predecessor", type=Path, required=True, help="sequence-6 journal main serves")
parser.add_argument("--final", type=Path, required=True, help="owner-signed sequence-9 journal")
parser.add_argument("--candidate-1", type=Path, required=True)
parser.add_argument("--public-key", type=Path, default=HERE / "core-public-key.txt")
parser.add_argument("--now", required=True, help="explicit UTC time, e.g. 2026-10-12T22:00:00Z")
args = parser.parse_args()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("FAILED: " + message)


def entries(envelope: dict) -> dict[str, dict]:
    return {e["version"]: e for e in envelope["entries"]}


async def authenticate(raw: bytes, public: str, now: datetime) -> int:
    async def fetch(url, maximum):
        assert url == CORE_REGISTRY_URL
        return raw

    with tempfile.TemporaryDirectory() as tmp:
        registry = CoreReleaseRegistry(enabled=True, public_key=public, cache_path=Path(tmp) / "c.json",
                                       fetcher=fetch, now=lambda: now)
        require(await registry.refresh(), "registry code refused the journal")
        authority = registry.authority()
        require(authority.positive_authority_current and not authority.surface_denied,
                "authority not current")
        return authority.sequence


def main() -> None:
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(timezone.utc)
    public = args.public_key.read_text().strip()
    require(sha(base64.b64decode(public)) == PUBLIC_KEY_SHA256, "public key fingerprint")
    old_raw, new_raw, cand_raw = (p.read_bytes() for p in (args.predecessor, args.final, args.candidate_1))
    require(sha(old_raw) == PREDECESSOR_SHA256, "predecessor is not the sequence-6 journal main serves")
    require(sha(cand_raw) == CANDIDATE_1_SHA256, "candidate 1 is not the cleared candidate")
    require(asyncio.run(authenticate(old_raw, public, now)) == 6, "predecessor sequence")
    require(asyncio.run(authenticate(new_raw, public, now)) == 9, "final sequence")
    old, new, cand = (json.loads(r) for r in (old_raw, new_raw, cand_raw))
    require(len(new["envelopes"]) == 9, "envelope count")
    for a, b in zip(old["envelopes"], new["envelopes"][:6]):
        require(canonical_json(a) == canonical_json(b), f"envelope {a['sequence']} changed")
    tip6, tip9 = old["envelopes"][-1], new["envelopes"][-1]
    e6, e9 = entries(tip6), entries(tip9)
    require(sorted(e9) == ["2026.10.0", "2026.10.1", "2026.9.4"], f"final entries {sorted(e9)}")
    for version in ("2026.10.0", "2026.9.4"):
        require(canonical_json(e6[version]) == canonical_json(e9[version]), f"{version} entry changed")
    require(cand["operation"] == "add", "candidate 1 is not an add")
    added = entries(cand["envelope"])["2026.10.1"]
    cleared = json.loads((HERE / "inputs/entry-2026.10.1.json").read_text())
    require(canonical_json(added) == canonical_json(cleared), "candidate-1 entry differs from inputs")
    require(canonical_json(e9["2026.10.1"]) == canonical_json(added), "2026.10.1 entry is not the cleared entry")
    require(len(e9["2026.10.1"]["capabilities"]) == 21, "2026.10.1 reference count")
    require(e9["2026.10.1"]["evidence_sha256"]
            == "a431916502b68089834e7e152947b3f32e26c31840acd65cc4ce94364cc923d7", "2026.10.1 evidence hash")
    revoked = {r["version"]: r for r in tip9["revocations"]}
    require(sorted(revoked) == ["2026.9.2", "2026.9.3"], f"revocations {sorted(revoked)}")
    for version, record in revoked.items():
        require(record["reason"] == REASON, f"{version} reason")
        require(record["entry_id"] == e6[version]["entry_id"]
                and record["image_index_digest"] == e6[version]["image_index_digest"],
                f"{version} revocation does not name the seq-6 entry")
    for envelope in new["envelopes"]:
        for entry in envelope["entries"]:
            require(all(c.get("capability_id") != ENTITY for c in entry["capabilities"]),
                    f"entity-metadata reference in seq {envelope['sequence']} {entry['version']}")
    require(ENTITY.encode() not in new_raw, "entity-metadata capability id appears in the journal bytes")
    require(tip9["expires_at"] > args.now, "final journal expired")
    print(f"Predecessor sequence 6 SHA256 {sha(old_raw)}; final sequence 9 SHA256 {sha(new_raw)}")
    print(f"Final entries {sorted(e9)}; revoked {sorted(revoked)}; 2026.10.1 references "
          f"{len(e9['2026.10.1']['capabilities'])}; expires_at {tip9['expires_at']}")
    print(f"Registry code: CORE_IDENTITY={CORE_IDENTITY!r}; root {ROOT}")
    print("OK")


main()
