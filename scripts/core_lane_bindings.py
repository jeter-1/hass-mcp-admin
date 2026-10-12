"""The one reviewed list of exact disposable Core bindings (CI-only, HAMCP-159).

Every executable consumer reads this list: support-window validation
(``scripts/support_window.py``), the disposable lane helpers
(``scripts/core_registry_contract_lane.py``) and the real-HA contract dispatcher
(``scripts/real_ha_contract_tests.py``). There is no second version list.

Rotating Cores that are already listed (candidate -> current -> rollback) edits
only ``ci/support-window.yaml``. A brand-new Core is added here, as one deliberate
review step, with its exact image index digest. The window never derives or
supplies a binding: a window Core without a lane binding here is refused before
any matrix is generated. Never imported by the Engineering runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

CHILD_DEVICES = "child_devices"
CHILD_DEVICE_NAME_PARTS = "child_device_name_parts"
DEVICE_REGISTRIES = frozenset({CHILD_DEVICES, CHILD_DEVICE_NAME_PARTS})
CHILD_CONSUMERS = frozenset({"8.4.3", "8.5.0"})


@dataclass(frozen=True)
class CoreBinding:
    """Reviewed contract semantics for one exact Core release."""

    # Exact multi-architecture image index digest of the disposable lane, or None
    # when the release has the child-device contract but no disposable lane (and
    # therefore can never occupy a support-window slot).
    image_index_digest: str | None
    device_registry: str
    typed_operations: bool
    child_consumer: str

    @property
    def lane(self) -> bool:
        return self.image_index_digest is not None


REVIEWED_CORE_BINDINGS = {
    "2026.9.0": CoreBinding(None, CHILD_DEVICES, False, "8.4.3"),
    "2026.9.1": CoreBinding(None, CHILD_DEVICES, False, "8.4.3"),
    "2026.9.2": CoreBinding("sha256:a1bc133af84ee6505fe2c266d9805b7c75b780dfdc188edfee3b11e8f3cd8efe",
                            CHILD_DEVICES, False, "8.4.3"),
    "2026.9.3": CoreBinding("sha256:d8922685169707fd91e8b9729902d975f06157d005e422874d201e0261dda196",
                            CHILD_DEVICES, True, "8.5.0"),
    "2026.9.4": CoreBinding("sha256:3e6710a7ab2a61311d9d899b719f6c3657791c63e8f4942cec4ebc42401d6b76",
                            CHILD_DEVICES, True, "8.5.0"),
    "2026.10.0": CoreBinding("sha256:1b64d38f38d922bf9d59336451fd6453e1d614f934456af4ee3d2a51061be3a4",
                             CHILD_DEVICE_NAME_PARTS, True, "8.5.0"),
    "2026.10.1": CoreBinding("sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0",
                             CHILD_DEVICE_NAME_PARTS, True, "8.5.0"),
}


def reviewed_core_binding(version):
    """The exact reviewed binding for ``version``, or None (no prefix or family match)."""

    if not isinstance(version, str):
        return None
    binding = REVIEWED_CORE_BINDINGS.get(version)
    if binding is None:
        return None
    if (binding.device_registry not in DEVICE_REGISTRIES or binding.child_consumer not in CHILD_CONSUMERS
            or type(binding.typed_operations) is not bool):
        raise ValueError("malformed reviewed Core binding")
    return binding


def reviewed_lane_binding(version):
    """The binding of an exact Core with a disposable lane, or None."""

    binding = reviewed_core_binding(version)
    return binding if binding is not None and binding.lane else None


def provenance_name(version):
    return "core_" + version.replace(".", "_") + "_lane_provenance.json"
