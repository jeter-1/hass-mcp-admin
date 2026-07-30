"""Deterministic version-scope parsing and applicability evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping


_VERSION_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$",
    re.ASCII,
)
_INTEGRATION_ID_PATTERN = re.compile(
    r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    re.ASCII,
)
_MAX_VERSION_LENGTH = 96


class VersionScopeError(ValueError):
    """A stable validation failure for a version or scope."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class Applicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class VersionScopeKind(str, Enum):
    ALL = "all"
    EXACT = "exact"
    RANGE = "range"
    UNKNOWN = "unknown"


class IntegrationScopeKind(str, Enum):
    ALL = "all"
    INTEGRATION = "integration"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _ParsedVersion:
    core: tuple[int, int, int]
    prerelease: tuple[str, ...] | None


def _parse_version(value: str, *, code: str) -> _ParsedVersion:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_LENGTH:
        raise VersionScopeError(code, "version must be a bounded string")
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise VersionScopeError(
            code,
            "version must use MAJOR.MINOR.PATCH with an optional prerelease",
        )
    prerelease = match.group(4)
    identifiers = tuple(prerelease.split(".")) if prerelease else None
    if identifiers is not None:
        for identifier in identifiers:
            if identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0":
                raise VersionScopeError(
                    code,
                    "numeric prerelease identifiers cannot contain leading zeroes",
                )
    return _ParsedVersion(
        core=(int(match.group(1)), int(match.group(2)), int(match.group(3))),
        prerelease=identifiers,
    )


def _compare_prerelease(
    left: tuple[str, ...] | None,
    right: tuple[str, ...] | None,
) -> int:
    if left is None and right is None:
        return 0
    if left is None:
        return 1
    if right is None:
        return -1
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_item) < int(right_item) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_item < right_item else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare_versions(left: str, right: str) -> int:
    """Compare two strict versions without consulting environment state."""

    parsed_left = _parse_version(left, code="invalid_version")
    parsed_right = _parse_version(right, code="invalid_version")
    if parsed_left.core != parsed_right.core:
        return -1 if parsed_left.core < parsed_right.core else 1
    return _compare_prerelease(parsed_left.prerelease, parsed_right.prerelease)


def _strict_keys(
    value: Mapping[str, Any],
    *,
    expected: set[str],
    code: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(f"missing keys {missing}")
        if unknown:
            detail.append(f"unknown keys {unknown}")
        raise VersionScopeError(code, "; ".join(detail))


@dataclass(frozen=True)
class VersionScope:
    """An explicit all, exact, bounded, or unknown version scope."""

    kind: VersionScopeKind
    version: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    include_minimum: bool | None = None
    include_maximum: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VersionScopeKind):
            raise VersionScopeError(
                "invalid_version_scope",
                "kind must be a VersionScopeKind",
            )
        if self.kind in (VersionScopeKind.ALL, VersionScopeKind.UNKNOWN):
            if any(
                item is not None
                for item in (
                    self.version,
                    self.minimum,
                    self.maximum,
                    self.include_minimum,
                    self.include_maximum,
                )
            ):
                raise VersionScopeError(
                    "invalid_version_scope",
                    f"{self.kind.value} scope cannot contain version bounds",
                )
            return
        if self.kind is VersionScopeKind.EXACT:
            if any(
                item is not None
                for item in (
                    self.minimum,
                    self.maximum,
                    self.include_minimum,
                    self.include_maximum,
                )
            ):
                raise VersionScopeError(
                    "invalid_version_scope",
                    "exact scope cannot contain range bounds",
                )
            _parse_version(self.version, code="invalid_version_scope")
            return
        if self.version is not None:
            raise VersionScopeError(
                "invalid_version_range",
                "range scope cannot contain an exact version",
            )
        if type(self.include_minimum) is not bool or type(
            self.include_maximum
        ) is not bool:
            raise VersionScopeError(
                "invalid_version_range",
                "range inclusivity values must be booleans",
            )
        _parse_version(self.minimum, code="invalid_version_range")
        _parse_version(self.maximum, code="invalid_version_range")
        if compare_versions(self.minimum, self.maximum) >= 0:
            raise VersionScopeError(
                "invalid_version_range",
                "range minimum must be lower than maximum",
            )

    @classmethod
    def from_dict(cls, value: Any) -> "VersionScope":
        if not isinstance(value, dict):
            raise VersionScopeError(
                "invalid_version_scope",
                "version scope must be an object",
            )
        raw_kind = value.get("kind")
        try:
            kind = VersionScopeKind(raw_kind)
        except (TypeError, ValueError) as exc:
            raise VersionScopeError(
                "invalid_version_scope",
                "unknown version scope kind",
            ) from exc
        if kind in (VersionScopeKind.ALL, VersionScopeKind.UNKNOWN):
            _strict_keys(
                value,
                expected={"kind"},
                code="invalid_version_scope",
            )
            return cls(kind=kind)
        if kind is VersionScopeKind.EXACT:
            _strict_keys(
                value,
                expected={"kind", "version"},
                code="invalid_version_scope",
            )
            _parse_version(value["version"], code="invalid_version_scope")
            return cls(kind=kind, version=value["version"])

        _strict_keys(
            value,
            expected={
                "kind",
                "minimum",
                "maximum",
                "include_minimum",
                "include_maximum",
            },
            code="invalid_version_range",
        )
        minimum = value["minimum"]
        maximum = value["maximum"]
        if type(value["include_minimum"]) is not bool or type(
            value["include_maximum"]
        ) is not bool:
            raise VersionScopeError(
                "invalid_version_range",
                "range inclusivity values must be booleans",
            )
        _parse_version(minimum, code="invalid_version_range")
        _parse_version(maximum, code="invalid_version_range")
        if compare_versions(minimum, maximum) >= 0:
            raise VersionScopeError(
                "invalid_version_range",
                "range minimum must be lower than maximum",
            )
        return cls(
            kind=kind,
            minimum=minimum,
            maximum=maximum,
            include_minimum=value["include_minimum"],
            include_maximum=value["include_maximum"],
        )

    def evaluate(self, version: str | None) -> Applicability:
        parsed_target = (
            _parse_version(version, code="invalid_target_version")
            if version is not None
            else None
        )
        if self.kind is VersionScopeKind.ALL:
            return Applicability.APPLICABLE
        if self.kind is VersionScopeKind.UNKNOWN or parsed_target is None:
            return Applicability.UNKNOWN
        if self.kind is VersionScopeKind.EXACT:
            assert self.version is not None
            return (
                Applicability.APPLICABLE
                if compare_versions(version, self.version) == 0
                else Applicability.NOT_APPLICABLE
            )
        assert self.minimum is not None
        assert self.maximum is not None
        lower = compare_versions(version, self.minimum)
        upper = compare_versions(version, self.maximum)
        lower_matches = lower > 0 or (lower == 0 and self.include_minimum is True)
        upper_matches = upper < 0 or (upper == 0 and self.include_maximum is True)
        return (
            Applicability.APPLICABLE
            if lower_matches and upper_matches
            else Applicability.NOT_APPLICABLE
        )

    def canonical_dict(self) -> dict[str, Any]:
        if self.kind in (VersionScopeKind.ALL, VersionScopeKind.UNKNOWN):
            return {"kind": self.kind.value}
        if self.kind is VersionScopeKind.EXACT:
            return {"kind": self.kind.value, "version": self.version}
        return {
            "kind": self.kind.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "include_minimum": self.include_minimum,
            "include_maximum": self.include_maximum,
        }


@dataclass(frozen=True)
class IntegrationScope:
    """An explicit all, one-integration, or unknown integration scope."""

    kind: IntegrationScopeKind
    integration_id: str | None = None
    version_scope: VersionScope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IntegrationScopeKind):
            raise VersionScopeError(
                "invalid_integration_scope",
                "kind must be an IntegrationScopeKind",
            )
        if self.kind in (IntegrationScopeKind.ALL, IntegrationScopeKind.UNKNOWN):
            if self.integration_id is not None or self.version_scope is not None:
                raise VersionScopeError(
                    "invalid_integration_scope",
                    f"{self.kind.value} scope cannot identify one integration",
                )
            return
        if (
            not isinstance(self.integration_id, str)
            or len(self.integration_id) > 64
            or _INTEGRATION_ID_PATTERN.fullmatch(self.integration_id) is None
        ):
            raise VersionScopeError(
                "invalid_integration_scope",
                "integration_id must be a canonical lowercase identifier",
            )
        if not isinstance(self.version_scope, VersionScope):
            raise VersionScopeError(
                "invalid_integration_scope",
                "integration scope requires a strict version scope",
            )

    @classmethod
    def from_dict(cls, value: Any) -> "IntegrationScope":
        if not isinstance(value, dict):
            raise VersionScopeError(
                "invalid_integration_scope",
                "integration scope must be an object",
            )
        raw_kind = value.get("kind")
        try:
            kind = IntegrationScopeKind(raw_kind)
        except (TypeError, ValueError) as exc:
            raise VersionScopeError(
                "invalid_integration_scope",
                "unknown integration scope kind",
            ) from exc
        if kind in (IntegrationScopeKind.ALL, IntegrationScopeKind.UNKNOWN):
            _strict_keys(
                value,
                expected={"kind"},
                code="invalid_integration_scope",
            )
            return cls(kind=kind)
        _strict_keys(
            value,
            expected={"kind", "integration_id", "version_scope"},
            code="invalid_integration_scope",
        )
        integration_id = value["integration_id"]
        if (
            not isinstance(integration_id, str)
            or len(integration_id) > 64
            or _INTEGRATION_ID_PATTERN.fullmatch(integration_id) is None
        ):
            raise VersionScopeError(
                "invalid_integration_scope",
                "integration_id must be a canonical lowercase identifier",
            )
        return cls(
            kind=kind,
            integration_id=integration_id,
            version_scope=VersionScope.from_dict(value["version_scope"]),
        )

    def evaluate(
        self,
        integration_id: str | None,
        version: str | None,
    ) -> Applicability:
        if version is not None and integration_id is None:
            raise VersionScopeError(
                "invalid_target_integration",
                "integration version requires an integration_id",
            )
        if integration_id is not None and (
            len(integration_id) > 64
            or _INTEGRATION_ID_PATTERN.fullmatch(integration_id) is None
        ):
            raise VersionScopeError(
                "invalid_target_integration",
                "integration_id must be a canonical lowercase identifier",
            )
        if self.kind is IntegrationScopeKind.ALL:
            return Applicability.APPLICABLE
        if self.kind is IntegrationScopeKind.UNKNOWN or integration_id is None:
            return Applicability.UNKNOWN
        if integration_id != self.integration_id:
            return Applicability.NOT_APPLICABLE
        assert self.version_scope is not None
        return self.version_scope.evaluate(version)

    def canonical_dict(self) -> dict[str, Any]:
        if self.kind in (IntegrationScopeKind.ALL, IntegrationScopeKind.UNKNOWN):
            return {"kind": self.kind.value}
        assert self.version_scope is not None
        return {
            "kind": self.kind.value,
            "integration_id": self.integration_id,
            "version_scope": self.version_scope.canonical_dict(),
        }
