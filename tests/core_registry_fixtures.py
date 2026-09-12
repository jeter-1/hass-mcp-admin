"""Synthetic Core release data and ephemeral signatures; no release authority."""

from copy import deepcopy
import json
from pathlib import Path

from ha_mcp_engineering.ha_core_readmission.models import CORE_IDENTITY
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_CAPABILITY_PROFILES
from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
from ha_mcp_engineering.ha_core_readmission.registry_models import CORE_REGISTRY_ID, CORE_REGISTRY_KEY_ID
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes
from signed_registry_fixtures import RegistrySigner, NOW, utc


def core_entry(version="2026.9.2"):
    return {
        "entry_id": f"synthetic-core-{version}", "server_name": CORE_IDENTITY,
        "version": version, "source_commit": "1" * 40, "source_tree": "2" * 40,
        "source_archive_sha256": "3" * 64, "image_index_digest": "sha256:" + "4" * 64,
        "architecture_manifests": {"linux/amd64": "sha256:" + "5" * 64,
                                   "linux/arm64": "sha256:" + "6" * 64},
        "evidence_sha256": "7" * 64,
        "probe_profile_id": CHILD_DEVICE_PROBE_PROFILE.profile_id,
        "probe_profile_sha256": CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint,
        "capabilities": [{k: p.to_mapping()[k] for k in (
            "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint"
        )} for p in CORE_CAPABILITY_PROFILES],
    }


def core_revocation(version="2026.9.2"):
    entry = core_entry(version)
    return {**{k: entry[k] for k in ("entry_id", "server_name", "version", "image_index_digest")},
            "revoked_at": utc(NOW), "reason": "Synthetic test-only revocation."}


class CoreSigner(RegistrySigner):
    def __init__(self, **kwargs):
        super().__init__(key_id=kwargs.pop("key_id", CORE_REGISTRY_KEY_ID), **kwargs)

    def unsigned(self, **kwargs):
        kwargs.setdefault("registry_id", CORE_REGISTRY_ID)
        kwargs.setdefault("entries", [core_entry()])
        return super().unsigned(**kwargs)

    def journal_unsigned(self, **kwargs):
        value = super().journal_unsigned(**kwargs)
        value["registry_id"] = CORE_REGISTRY_ID
        return value


class ProjectedCoreSource:
    """Synthetic I/O feeding the actual production device/semantic projection."""

    def __init__(self, registry, version="2026.9.2"):
        self.registry, self.version = registry, version
        self.fixture = json.loads((Path(__file__).parent / "fixtures" /
                                   "ha_core_2026_9_device_registry.json").read_text())
        self.calls = 0

    async def capture_core_snapshot(self):
        self.calls += 1
        version = self.version
        evidence = capability_evidence_for_probes(
            version=version, probe_profile=self.registry.probe_profile_for(version),
            rest_config={"version": version}, websocket_config={"version": version},
            states=[{"entity_id": "sensor.synthetic", "state": "ready", "attributes": {}}],
            services=[{"domain": "input_boolean", "services": {}}],
            configuration_validation={"result": "valid", "errors": None, "warnings": None},
            websocket_results={
                "areas": [{"area_id": "garage"}], "floors": [{"floor_id": "ground"}],
                "labels": [{"label_id": "synthetic"}], "entities": self.fixture["entities"],
                "devices": self.fixture["devices"], "dashboards": [{"url_path": "synthetic"}],
                "automation": {"id": "synthetic"}, "dashboard": {"views": []},
                "trace_list": [], "trace_get": {"_ha_mcp_engineering_expected_probe_error": "not_found"},
            },
        )
        return deepcopy({
            "identity": CORE_IDENTITY, "connected": True, "authenticated": True,
            "session_id": "synthetic-core-registry-session",
            "rest_config": {"version": version},
            "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
            "websocket_get_config": {"version": version}, "capability_evidence": evidence,
        })
