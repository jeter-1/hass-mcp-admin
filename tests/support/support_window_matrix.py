"""Derive the CI lane matrices exactly as ``prepare_exact_image_matrix`` does.

CI computes the real-HA, exact-image gateway and add-on runtime matrices from
``ci/support-window.yaml`` with ``scripts/support_window.py`` (gateway rows are
cross-checked against ``review_upstream_read_release.py ci-matrix``). Tests use
these helpers instead of hard-coding rows in ``.github/workflows/ci.yml``.
Offline only: the reviewed registry is read from committed source.
"""

from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
MATRIX_JOB = "prepare_exact_image_matrix"
# Events and the tier GitHub delivers for them (workflow_call inherits the
# caller's event, so publication callers arrive as push/workflow_run).
PR_EVENT = "pull_request"
FULL_EVENTS = ("schedule", "workflow_dispatch", "push", "workflow_run")


@lru_cache(maxsize=None)
def support_window():
    spec = importlib.util.spec_from_file_location(
        "support_window_under_test", ROOT / "scripts/support_window.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _registry_matrix_text():
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/review_upstream_read_release.py"), "ci-matrix"],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=120,
    ).stdout


def registry_matrix():
    return json.loads(_registry_matrix_text())


def matrices(tier):
    """Return {'real_ha', 'addon', 'gateway'} matrices for ``tier``."""

    module = support_window()
    window = module.load(ROOT)
    return {
        "real_ha": module.real_ha_matrix(window, tier),
        "addon": module.addon_matrix(window, tier),
        "gateway": module.gateway_matrix(window, tier, registry_matrix()),
    }


def rows(kind, tier):
    return matrices(tier)[kind]["include"]


def tier_for(event):
    return support_window().tier_for(event)
