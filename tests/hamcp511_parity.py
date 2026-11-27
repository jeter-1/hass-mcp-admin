"""HAMCP-511 parity fixture: one synthetic recorder, the read cases, the compared keys.

Shared by ``generate_hamcp511_parity_goldens.py`` (run against the delegated
service at the source commit) and ``test_hamcp511_native_history_slices``
(run against the native service). It imports no Engineering code, so the
generator can load it next to the historical package. Synthetic data only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ENTITY = "sensor.synthetic_parity_511"
START, END = "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z"
CODEC_KEY = b"p" * 32
CASES = {"limit_1": 1, "limit_7": 7, "limit_100": 100, "default_limit": None}
# Reply keys that may differ between the delegated and the native route
# (disclosed in the PR): provider.*, upstream_calls, verification_calls,
# bounds, limitations and the cursor bytes. Everything else must match.
DIFFERING_KEYS = frozenset({"provider", "upstream_calls", "verification_calls", "bounds", "limitations",
                            "next_cursor"})
MS = 1000
LONG_STATE = "synthetic-long-state-" + "z" * 280


def us(text: str) -> int:
    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return (moment - EPOCH) // timedelta(microseconds=1)


def at(day: int, clock: str) -> int:
    return us(f"2026-10-{day:02d}T{clock}")


def rows() -> list[tuple[int, str]]:
    """The recorder rows: edges, repeats, a busy slice, an empty slice, a long state."""

    s0, e0 = at(19, "00:00:00"), at(20, "00:00:00")
    out = [
        # Before the window: the prevailing state, and a row exactly at the
        # slice-0 request start (start - 1 ms), which Core reports in neither set.
        (s0 - 2 * 3600 * 10**6, "alpha"), (s0 - MS, "beta"),
        # At the window start, an equal repeat 1 us later, then a change.
        (s0, "gamma"), (s0 + 1, "gamma"), (s0 + 10**6, "delta"),
    ]
    # Slice 0: a change every 10 minutes with periodic equal repeats.
    for minute in range(10, 360, 10):
        out.append((s0 + minute * 60 * 10**6, "on" if (minute // 10) % 3 else "off"))
    # Slice 1: more than 100 changes after collapsing, starting exactly at
    # the slice boundary, with an equal repeat every 7th row.
    s1 = at(19, "06:00:00")
    for index in range(150):
        state = f"level_{index % 4}"
        out.append((s1 + index * 120 * 10**6, state))
        if index % 7 == 3:
            out.append((s1 + index * 120 * 10**6 + 30 * 10**6, state))
    # Slice 2 (12:00-18:00): empty.
    # Slice 3: a state over the 255-character bound, ties at the end edge.
    out += [
        (at(19, "19:00:00"), LONG_STATE), (at(19, "19:00:00.000001"), "after_long"),
        (at(19, "21:30:00"), "late"), (e0 - 1, "last_inside"), (e0, "at_end_excluded"),
    ]
    return out


def seed(recorder) -> None:
    for ts_us, state in rows():
        recorder.add(ts_us, state)


NOW_MS = us("2026-10-20T01:00:00") // 1000


def arguments(case: str) -> dict:
    args = {"entity_id": ENTITY, "start": START, "end": END}
    if CASES[case] is not None:
        args["limit"] = CASES[case]
    return args


def normalize(page: dict) -> dict:
    """The page without the keys allowed to differ; the cursor as presence."""

    out = {key: value for key, value in page.items() if key not in DIFFERING_KEYS}
    out["has_cursor"] = page["next_cursor"] is not None
    return out
