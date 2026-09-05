"""Canonical Engineering release-transition validation."""

from __future__ import annotations

from typing import NamedTuple
import re


_CORE_PATTERN = (
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
)
_RELEASE_VERSION = re.compile(
    rf"^{_CORE_PATTERN}"
    r"(?:-(?:beta\.(?P<beta>[1-9]\d*)|rc\.(?P<rc>[1-9]\d*)))?$"
)


class ReleaseTransitionError(ValueError):
    """A version or lifecycle transition is not canonical."""


class ReleaseVersion(NamedTuple):
    core: tuple[int, int, int]
    channel: str
    sequence: int | None


def parse_release_version(value: str) -> ReleaseVersion:
    """Parse one canonical stable, beta, or release-candidate version."""

    if not isinstance(value, str):
        raise ReleaseTransitionError("A release version must be a string")
    match = _RELEASE_VERSION.fullmatch(value)
    if match is None:
        raise ReleaseTransitionError(
            f"Unsupported or noncanonical release version: {value!r}"
        )
    core = tuple(int(match[name]) for name in ("major", "minor", "patch"))
    if match["beta"] is not None:
        return ReleaseVersion(core, "beta", int(match["beta"]))
    if match["rc"] is not None:
        return ReleaseVersion(core, "rc", int(match["rc"]))
    return ReleaseVersion(core, "stable", None)


def validate_release_transition(current: str, candidate: str) -> None:
    """Require the exact next transition in the Engineering lifecycle."""

    current_version = parse_release_version(current)
    candidate_version = parse_release_version(candidate)

    accepted = False
    if current_version.channel == "beta":
        current_sequence = current_version.sequence
        accepted = (
            current_sequence is not None
            and candidate_version.core == current_version.core
            and (
                (
                    candidate_version.channel == "beta"
                    and candidate_version.sequence
                    == current_sequence + 1
                )
                or (
                    candidate_version.channel == "rc"
                    and candidate_version.sequence == 1
                )
            )
        )
    elif current_version.channel == "rc":
        current_sequence = current_version.sequence
        accepted = (
            current_sequence is not None
            and candidate_version.core == current_version.core
            and (
                (
                    candidate_version.channel == "rc"
                    and candidate_version.sequence
                    == current_sequence + 1
                )
                or candidate_version.channel == "stable"
            )
        )
    else:
        accepted = (
            candidate_version.core > current_version.core
            and candidate_version.channel == "beta"
            and candidate_version.sequence == 1
        )

    if not accepted:
        raise ReleaseTransitionError(
            f"Unsupported Engineering release transition: {current} -> {candidate}"
        )
