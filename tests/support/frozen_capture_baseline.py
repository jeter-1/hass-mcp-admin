"""Frozen ha-mcp 9.1.2 baseline for the historical capture-lane tests (HAMCP-437).

Historical capture-lane coverage; shipped default refuses (HAMCP-437). The
HAMCP-351/371 capture lanes (``scripts/upstream_catalog_capture_local.py`` and
``scripts/core_read_replay_capture_local.py``) bind their 9.1.2 baseline from a
reviewed registry row. The shipped registry holds only 9.2.0, so on the shipped
default ``binding("9.1.2")`` refuses with ``baseline_registry_row_missing``.

ANSWER 20261122-1140-01 (b): the scripts are unchanged. These tests pass the
frozen two-row registry from ``tests/fixtures/ha_mcp_8_6_0_9_1_2_foreign``
(byte copies at ``0f1adcb5``, see ``frozen_foreign_releases``) through the
script's own ``binding(version, registry=...)`` parameter. ``patch_binding``
wraps the real function for the lanes' internal call sites, which pass no
registry, so the binding logic under test stays the script's. The fixture is
loaded by path and never replaces the shipped registry.
"""

from __future__ import annotations

from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]

import upstream_catalog_capture_local as s4  # noqa: E402
from support import frozen_foreign_releases as foreign  # noqa: E402

REGISTRY = foreign.REGISTRY
SHIPPED_BINDING = s4.binding


def binding(version, *, registry=REGISTRY, **kwargs):
    """The script's binding on the frozen registry."""

    return SHIPPED_BINDING(version, registry=registry, **kwargs)


def patch_binding():
    """Route the lane's own ``binding`` calls through the frozen registry."""

    return mock.patch.object(s4, "binding", binding)
