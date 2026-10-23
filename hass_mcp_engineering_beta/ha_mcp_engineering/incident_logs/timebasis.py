"""Journal entry time basis (HAMCP-220). Pure: no I/O beyond the zone database.

Supervisor renders each entry's ``__REALTIME_TIMESTAMP`` as text in the
verbose prefix. Pinned 2026.09.1 renders it in UTC
(``utils/systemd_journal.py`` L56-60, 40e3ee76); 2026.10.3 renders it naive
in Supervisor's time zone (``journal_verbose_formatter(..., tz)``, #7372,
9493d3a7, passed ``sys_timezone_tzinfo`` by ``api/host.py`` L399); a later
release may append ``+HH:MM``. The log API returns text only, but
``X-First-Cursor`` is the first returned entry's ``__CURSOR`` and systemd
v257.9 (``sd-journal.c`` L1252-1258) writes its ``t=`` field as that
entry's realtime in hex microseconds.

So the first entry of every page has its exact UTC realtime, and that
entry calibrates how the page's text was rendered: each rendering
hypothesis (Home Assistant's zone, UTC) that maps the first entry's text to
its realtime survives, and none surviving fails the page closed. Every other
entry is read from its text under the surviving renderings. A local time
that occurs twice (DST fall-back) or never (spring-forward), or on which the
surviving renderings disagree, is uncertain: every candidate is kept and
none is chosen.

The zone is Home Assistant's configured time zone, read per call from
Core ``/api/config`` (``zone.py``); Supervisor renders the log text in the
zone Core pushes to it. The add-on container's ``TZ`` is set once, at
container creation (``docker/app.py`` L237, 2026.10.3), so it is compared
with that zone over the requested window (``first_offset_difference``) and
never used to read text. A missing or unknown zone is never replaced by UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
import re
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .journal import RawEntry


BASIS_REALTIME = "journal_realtime_utc"
BASIS_TEXT_LOCAL = "text_local_tz"
BASIS_TEXT_OFFSET = "text_explicit_offset"
BASIS_UNCERTAIN = "uncertain"

AMBIGUOUS = "ambiguous_local_time"
NONEXISTENT = "nonexistent_local_time"
DISAGREEMENT = "rendering_disagreement"

UTC_NAME = "UTC"
# Text is truncated to milliseconds from microseconds via a float; one
# millisecond either way still separates renderings at least 15 minutes apart.
CALIBRATION_TOLERANCE_MS = 1
MAX_ZONE_NAME = 64
_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,3}\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


class TimeZoneUnavailable(ValueError):
    """Home Assistant's time zone could not be established."""


class TimeBasisMismatch(ValueError):
    """No rendering maps the page's first entry to its journal realtime."""


@dataclass(frozen=True)
class Zone:
    name: str
    tz: tzinfo


@dataclass(frozen=True)
class PageBasis:
    """How one page's text was read."""

    verified: bool
    renderings: tuple[str, ...]


def load_zone(name: object) -> Zone:
    """An IANA zone by name; anything else is unavailable, never UTC."""

    if (
        type(name) is not str or not 0 < len(name) <= MAX_ZONE_NAME
        or not _ZONE_NAME.fullmatch(name) or ".." in name
    ):
        raise TimeZoneUnavailable("time_zone_invalid")
    try:
        return Zone(name, ZoneInfo(name))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise TimeZoneUnavailable("time_zone_unknown") from None


def _offset(zone: Zone, at_ms: int) -> timedelta:
    return (_EPOCH + at_ms * _MS).astimezone(zone.tz).utcoffset()


def _change(zone: Zone, low: int, high: int) -> int:
    """The first millisecond in ``(low, high]`` with ``high``'s offset."""

    before = _offset(zone, low)
    while high - low > 1:
        middle = (low + high) // 2
        if _offset(zone, middle) == before:
            low = middle
        else:
            high = middle
    return high


def first_offset_difference(first: Zone, second: Zone, start_ms: int, end_ms: int,
                            sample_ms: int) -> int | None:
    """The first millisecond in ``[start, end)`` where the zones' UTC offsets differ.

    Offsets are compared every ``sample_ms`` and at ``end - 1``; an offset
    change of either zone between two samples is located to the millisecond
    and compared there too. A zone changing offset twice within one sample
    interval is not represented in the zone database and is not searched.
    """

    if end_ms <= start_ms:
        return None
    points = list(range(start_ms, end_ms, max(1, sample_ms)))
    if points[-1] != end_ms - 1:
        points.append(end_ms - 1)
    previous = None
    for point in points:
        changes = []
        if previous is not None:
            changes = sorted(_change(zone, previous, point) for zone in (first, second)
                             if _offset(zone, previous) != _offset(zone, point))
        for moment in (*changes, point):
            if _offset(first, moment) != _offset(second, moment):
                return moment
        previous = point
    return None


def realtime_us(cursor: str | None) -> int | None:
    """The ``t=`` realtime (UTC microseconds) of a systemd cursor, if present."""

    if not cursor:
        return None
    for part in cursor.split(";"):
        key, _, value = part.partition("=")
        if key == "t" and 0 < len(value) <= 16:
            try:
                return int(value, 16)
            except ValueError:
                return None
    return None


def epoch_ms(moment: datetime) -> int:
    return (moment - _EPOCH) // _MS


def local_candidates(naive: datetime, tz: tzinfo) -> tuple[tuple[int, ...], str | None]:
    """UTC milliseconds a naive wall time in ``tz`` can denote, and why if not one.

    ``fold`` 0 and 1 give the two readings. Both round-trip for a repeated
    (fall-back) time; neither reading round-trips for a skipped
    (spring-forward) time. Both readings are kept in either case.
    """

    readings = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        utc = aware.astimezone(timezone.utc)
        readings.append((epoch_ms(utc), utc.astimezone(tz).replace(tzinfo=None) == naive))
    (first, first_ok), (second, second_ok) = readings
    if first == second and first_ok:
        return (first,), None
    candidates = tuple(sorted({first, second}))
    if first_ok and second_ok:
        return candidates, AMBIGUOUS
    return candidates, NONEXISTENT


def _renderings(zone: Zone) -> tuple[Zone, ...]:
    if zone.name == UTC_NAME:
        return (zone,)
    return (zone, Zone(UTC_NAME, timezone.utc))


def resolve_page(entries: Sequence[RawEntry], first_cursor: str | None,
                 zone: Zone) -> tuple[tuple[RawEntry, ...], PageBasis]:
    """Give every parsed entry its UTC time, basis and any uncertainty."""

    if not entries:
        return (), PageBasis(True, ())
    offsets = {entry.stamp_offset is not None for entry in entries}
    if len(offsets) != 1:
        raise TimeBasisMismatch("timestamp_form_mixed")
    realtime = realtime_us(first_cursor)
    realtime_ms = None if realtime is None else realtime // 1000
    if offsets == {True}:
        resolved = [_with_offset(entry) for entry in entries]
        if realtime_ms is not None:
            if abs(resolved[0].ts_ms - realtime_ms) > CALIBRATION_TOLERANCE_MS:
                raise TimeBasisMismatch("timestamp_basis_mismatch")
            resolved[0] = _exact(resolved[0], realtime_ms)
        return tuple(resolved), PageBasis(realtime_ms is not None, ("explicit_offset",))
    if realtime_ms is None:
        # No realtime to verify against: the naive text is read in Home
        # Assistant's zone only, and the page says it is unverified.
        surviving = (zone,)
    else:
        first = entries[0].stamp
        surviving = tuple(
            rendering for rendering in _renderings(zone)
            if any(abs(candidate - realtime_ms) <= CALIBRATION_TOLERANCE_MS
                   for candidate in local_candidates(first, rendering.tz)[0])
        )
        if not surviving:
            raise TimeBasisMismatch("timestamp_basis_mismatch")
    resolved = [_from_text(entry, surviving) for entry in entries]
    if realtime_ms is not None:
        resolved[0] = _exact(resolved[0], realtime_ms)
    return tuple(resolved), PageBasis(realtime_ms is not None,
                                      tuple(rendering.name for rendering in surviving))


def _exact(entry: RawEntry, ts_ms: int) -> RawEntry:
    return replace(entry, ts_ms=ts_ms, ts_hi=ts_ms, basis=BASIS_REALTIME, zone=UTC_NAME,
                   candidates=(), uncertain=None)


def _with_offset(entry: RawEntry) -> RawEntry:
    sign, hours, minutes = entry.stamp_offset[0], int(entry.stamp_offset[1:3]), int(entry.stamp_offset[4:6])
    if hours > 23 or minutes > 59:
        raise TimeBasisMismatch("timestamp_invalid")
    delta = timedelta(hours=hours, minutes=minutes) * (1 if sign == "+" else -1)
    ts_ms = epoch_ms(entry.stamp.replace(tzinfo=timezone(delta)))
    return replace(entry, ts_ms=ts_ms, ts_hi=ts_ms, basis=BASIS_TEXT_OFFSET, zone=entry.stamp_offset,
                   candidates=(), uncertain=None)


def _from_text(entry: RawEntry, renderings: tuple[Zone, ...]) -> RawEntry:
    candidates: set[int] = set()
    reasons: set[str] = set()
    for rendering in renderings:
        values, reason = local_candidates(entry.stamp, rendering.tz)
        candidates.update(values)
        if reason:
            reasons.add(reason)
    ordered = tuple(sorted(candidates))
    if len(ordered) == 1 and not reasons:
        return replace(entry, ts_ms=ordered[0], ts_hi=ordered[0], basis=BASIS_TEXT_LOCAL,
                       zone=renderings[0].name, candidates=(), uncertain=None)
    reason = NONEXISTENT if NONEXISTENT in reasons else AMBIGUOUS if AMBIGUOUS in reasons else DISAGREEMENT
    return replace(entry, ts_ms=ordered[0], ts_hi=ordered[-1], basis=BASIS_UNCERTAIN,
                   zone="|".join(rendering.name for rendering in renderings),
                   candidates=ordered, uncertain=reason)
