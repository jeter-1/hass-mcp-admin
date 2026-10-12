"""Core expectations derived from the one reviewed binding list (HAMCP-159 R2).

Qualification checks take "the set of qualified Cores" from
``scripts/core_lane_bindings.py`` (the reviewed authority) through these
helpers. They never hand-maintain a version list and never derive it from
``ci/support-window.yaml``. Adding one reviewed binding therefore needs no test
edit, and unknown-version controls use versions generated to be absent from the
reviewed list, so a later real binding can never turn a control into a
qualified Core. Assertions about a named historical Core stay version-named in
the tests that make them.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import core_lane_bindings  # noqa: E402


def reviewed_versions():
    """Every Core with a reviewed binding, read at call time."""

    return frozenset(core_lane_bindings.REVIEWED_CORE_BINDINGS)


def lane_versions():
    """Every reviewed Core that also has a disposable lane, read at call time."""

    return frozenset(version for version in core_lane_bindings.REVIEWED_CORE_BINDINGS
                     if core_lane_bindings.reviewed_lane_binding(version) is not None)


def _parts(version):
    return tuple(int(part) for part in version.split("."))


def unreviewed_version(start="2026.10.2"):
    """``start``, or the next patch release after it, that has no reviewed binding."""

    year, month, patch = _parts(start)
    while f"{year}.{month}.{patch}" in core_lane_bindings.REVIEWED_CORE_BINDINGS:
        patch += 1
    return f"{year}.{month}.{patch}"


def unreviewed_versions():
    """Well-formed Core versions guaranteed absent from the reviewed list.

    The next patch and the next minor after the newest reviewed Core, and the
    next unreviewed patch after the oldest one, all computed at call time.
    """

    ordered = sorted(core_lane_bindings.REVIEWED_CORE_BINDINGS, key=_parts)
    year, month, patch = _parts(ordered[-1])
    oldest = _parts(ordered[0])
    found = (unreviewed_version(f"{year}.{month}.{patch + 1}"),
             unreviewed_version(f"{year}.{month + 1}.0"),
             unreviewed_version(f"{oldest[0]}.{oldest[1]}.{oldest[2] + 1}"))
    assert not set(found) & reviewed_versions()
    return tuple(dict.fromkeys(found))
