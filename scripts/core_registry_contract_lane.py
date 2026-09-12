"""Disposable-lane authority only; never imported by the Engineering runtime.

An ephemeral key allows CI to exercise the actual signature verifier and Core
source probes. This is candidate contract evidence, not publication authority.
"""

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_CAPABILITY_PROFILES, compiled_exact_authority
from ha_mcp_engineering.signed_registry import canonical_json


async def configure_with_test_authority(runtime, configured, *, cache_path, expected_image):
    """Same runtime instance: unsigned/absent .2 withheld, signed .2 admitted."""
    entry = json.loads((ROOT / "tests/fixtures/core_2026_9_2_lane_provenance.json").read_text())
    assert expected_image == (
        "ghcr.io/home-assistant/home-assistant:" + entry["version"] + "@" + entry["image_index_digest"]
    )
    assert compiled_exact_authority(entry["version"]) == ()
    evidence = b"Disposable test authority only; actual contract result follows."
    entry.update({
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "probe_profile_id": CHILD_DEVICE_PROBE_PROFILE.profile_id,
        "probe_profile_sha256": CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint,
        "capabilities": [{name: profile.to_mapping()[name] for name in (
            "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint"
        )} for profile in CORE_CAPABILITY_PROFILES],
    })
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    candidate = canonical_json(prepare_candidate(
        entry=entry, evidence=evidence, previous=None,
        public_key=key.public_key(), now=now,
    ))
    signed = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                            previous=None, key=key, now=now)
    payload = None

    async def fetch(_url, _maximum):
        if payload is None:
            raise OSError("No disposable test authority yet")
        return payload

    registry = CoreReleaseRegistry(
        enabled=True, public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        fetcher=fetch, cache_path=cache_path,
    )
    # Neither code nor compiled authority changes between these observations.
    runtime.configure(configured, release_registry=registry)
    await runtime.reconcile_once("startup")
    before = runtime.health_snapshot()
    assert before["compatible_count"] == 0
    assert runtime.current_observation.version == entry["version"]
    payload = signed
    assert await registry.refresh()
    await runtime.reconcile_once("core_test_signed_data_available")
    after = runtime.health_snapshot()
    assert after["compatible_count"] == 17
    assert all(p["authority_source"] == "verified_compatibility" for p in after["authority_profiles"])
    assert after["fallback_count"] == 0
    print("Core data-only authority contract: " + json.dumps({
        "version": entry["version"], "before": 0, "after": 17,
        "same_runtime_instance": True, "compiled_exact_authority": False,
        "ephemeral_test_signature": True, "production_authority": False,
    }, sort_keys=True))
