"""Derive the CI lane matrices exactly as ``prepare_exact_image_matrix`` does.

CI computes the real-HA, strict-full and admission matrices from
``ci/support-window.yaml`` with ``scripts/support_window.py`` (strict-full and
admission rows are cross-checked against ``review_upstream_read_release.py
ci-matrix``; HAMCP-437 retired the gateway and add-on runtime lanes). Tests use
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
    """Return {'real_ha', 'strict_full', 'admission'} matrices for ``tier``.

    ``admission`` (HAMCP-319 section 5.1 lanes) does not depend on the tier and
    is empty unless the window declares the optional ``admission`` key.
    """

    module = support_window()
    window = module.load(ROOT)
    return {
        "real_ha": module.real_ha_matrix(window, tier),
        "strict_full": module.strict_full_matrix(window, tier, registry_matrix()),
        "admission": module.admission_matrix(window, registry_matrix()),
    }


def rows(kind, tier):
    return matrices(tier)[kind]["include"]


def tier_for(event):
    return support_window().tier_for(event)
