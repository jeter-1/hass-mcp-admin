"""Port of Home Assistant Core's recorder entity filter (2026.10.1).

Source: ``homeassistant/helpers/entityfilter.py`` (``_convert_globs_to_pattern``
and ``_generate_filter_from_sets_and_pattern_lists``), ``homeassistant/core.py``
(``VALID_ENTITY_ID``, ``split_entity_id``) and the ``boolean``, ``string``,
``entity_id`` and ``_entity_ids`` validators of
``homeassistant/helpers/config_validation.py`` at Core commit
``abe1fdd7cd6b4de1715c87b6fd87d92699514ef9``.

The port is a cross-check only. Core's ``recorder/entity_options/get`` answer
from the loaded filter is primary; a disagreement is reported as
``filter_drift`` and never resolved here. The evaluation is pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
import fnmatch
from numbers import Number
import re
from typing import Any


CORE_PIN = "2026.10.1"

_OBJECT_ID = r"(?!_)[\da-z_]+(?<!_)"
_DOMAIN = r"(?!.+__)" + _OBJECT_ID
VALID_ENTITY_ID = re.compile(r"^" + _DOMAIN + r"\." + _OBJECT_ID + r"$")

# The six evaluation branches of ``_generate_filter_from_sets_and_pattern_lists``.
NO_FILTER = "no_filter"            # case 1
INCLUDE_ONLY = "include_only"      # case 2
EXCLUDE_ONLY = "exclude_only"      # case 3
INCLUDE_DOMAIN_OR_GLOB = "4a"      # case 4: exclude.domains ignored
EXCLUDE_DOMAIN_OR_GLOB = "4b"      # case 5
INCLUDE_ENTITIES_ONLY = "include_entities_only"  # case 6


class FilterValueInvalid(ValueError):
    """The configured value fails Core's schema; Core would refuse it."""


def split_entity_id(entity_id: str) -> tuple[str, str]:
    domain, _, object_id = entity_id.partition(".")
    if not domain or not object_id:
        raise ValueError("Invalid entity ID")
    return domain, object_id


def boolean(value: Any) -> bool:
    """``cv.boolean``."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.lower().strip()
        if value in ("1", "true", "yes", "on", "enable"):
            return True
        if value in ("0", "false", "no", "off", "disable"):
            return False
    elif isinstance(value, Number):
        return value != 0
    raise FilterValueInvalid("boolean")


def coerce_int(value: Any, minimum: int) -> int:
    """``probatio.All(probatio.Coerce(int), probatio.Range(min=minimum))``."""

    try:
        coerced = int(value)
    except (TypeError, ValueError, OverflowError):
        raise FilterValueInvalid("integer") from None
    if coerced < minimum:
        raise FilterValueInvalid("range")
    return coerced


def string(value: Any) -> str:
    """``cv.string`` for JSON-decoded values."""

    if value is None or isinstance(value, (list, dict)):
        raise FilterValueInvalid("string")
    if isinstance(value, str):
        return value
    return str(value)


def ensure_list(value: Any) -> list:
    """``probatio.EnsureList``."""

    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def string_list(value: Any) -> list[str]:
    """``probatio.All(probatio.EnsureList(), [cv.string])``."""

    return [string(item) for item in ensure_list(value)]


def entity_ids(value: Any) -> list[str]:
    """``cv.entity_ids``: comma split for strings, lower-cased, validated."""

    if value is None:
        raise FilterValueInvalid("entity_ids")
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        # probatio iterates the value; a non-list scalar is not iterable here.
        raise FilterValueInvalid("entity_ids")
    result = []
    for item in value:
        lowered = string(item).lower()
        if VALID_ENTITY_ID.match(lowered) is None:
            raise FilterValueInvalid("entity_id")
        result.append(lowered)
    return result


def convert_globs_to_pattern(globs: list[str] | None) -> re.Pattern[str] | None:
    if globs is None:
        return None
    translated = [pattern for glob in set(globs) if (pattern := fnmatch.translate(glob))]
    if not translated:
        return None
    return re.compile("(?:" + "|".join(translated) + ")")


def filter_case(include_d, include_e, exclude_d, exclude_e, include_eg, exclude_eg) -> str:
    have_exclude = bool(exclude_e or exclude_d or exclude_eg)
    have_include = bool(include_e or include_d or include_eg)
    if not have_include and not have_exclude:
        return NO_FILTER
    if have_include and not have_exclude:
        return INCLUDE_ONLY
    if not have_include and have_exclude:
        return EXCLUDE_ONLY
    if include_d or include_eg:
        return INCLUDE_DOMAIN_OR_GLOB
    if exclude_d or exclude_eg:
        return EXCLUDE_DOMAIN_OR_GLOB
    return INCLUDE_ENTITIES_ONLY


def generate_filter(
    include_d: set[str],
    include_e: set[str],
    exclude_d: set[str],
    exclude_e: set[str],
    include_eg: re.Pattern[str] | None,
    exclude_eg: re.Pattern[str] | None,
) -> Callable[[str], bool]:
    """``_generate_filter_from_sets_and_pattern_lists``, branch for branch."""

    case = filter_case(include_d, include_e, exclude_d, exclude_e, include_eg, exclude_eg)
    if case == NO_FILTER:
        return bool
    if case == INCLUDE_ONLY:
        def entity_included(entity_id: str) -> bool:
            return (
                entity_id in include_e
                or split_entity_id(entity_id)[0] in include_d
                or bool(include_eg and include_eg.match(entity_id))
            )
        return entity_included
    if case == EXCLUDE_ONLY:
        def entity_not_excluded(entity_id: str) -> bool:
            return not (
                entity_id in exclude_e
                or split_entity_id(entity_id)[0] in exclude_d
                or (exclude_eg and exclude_eg.match(entity_id))
            )
        return entity_not_excluded
    if case == INCLUDE_DOMAIN_OR_GLOB:
        def entity_filter_4a(entity_id: str) -> bool:
            return entity_id in include_e or (
                entity_id not in exclude_e
                and (
                    bool(include_eg and include_eg.match(entity_id))
                    or (
                        split_entity_id(entity_id)[0] in include_d
                        and not (exclude_eg and exclude_eg.match(entity_id))
                    )
                )
            )
        return entity_filter_4a
    if case == EXCLUDE_DOMAIN_OR_GLOB:
        def entity_filter_4b(entity_id: str) -> bool:
            domain = split_entity_id(entity_id)[0]
            if domain in exclude_d or bool(exclude_eg and exclude_eg.match(entity_id)):
                return entity_id in include_e
            return entity_id not in exclude_e
        return entity_filter_4b
    return include_e.__contains__


class RecorderFilter:
    """Core's ``EntityFilter`` over a schema-validated include/exclude pair."""

    def __init__(self, include: dict[str, list[str]], exclude: dict[str, list[str]]):
        self.include_d = set(include["domains"])
        self.include_e = set(include["entities"])
        self.exclude_d = set(exclude["domains"])
        self.exclude_e = set(exclude["entities"])
        self.include_eg = convert_globs_to_pattern(include["entity_globs"])
        self.exclude_eg = convert_globs_to_pattern(exclude["entity_globs"])
        args = (self.include_d, self.include_e, self.exclude_d, self.exclude_e,
                self.include_eg, self.exclude_eg)
        self.case = filter_case(*args)
        self._filter = generate_filter(*args)
        # ``entity_filter = None if _filter.empty_filter else ...``: an empty
        # configuration records every entity (recorder/__init__.py).
        self.empty = not any((include["domains"], include["entities"], include["entity_globs"],
                              exclude["domains"], exclude["entities"], exclude["entity_globs"]))

    def recorded(self, entity_id: str) -> bool:
        return True if self.empty else bool(self._filter(entity_id))
