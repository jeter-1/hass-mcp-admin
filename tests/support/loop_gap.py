"""Same-child baselines for event-loop responsiveness bounds (HAMCP-219).

A responsiveness body schedules a heartbeat on the event loop and bounds the
longest wall gap between its turns, and the loop-thread CPU between them,
while the work under test runs. Each gap is bounded by a fixed allowance plus
a baseline measured in the same child, never by a retry:

- wall: the unstalled heartbeat gap measured idle just before the window.
- thread CPU, where collection stays enabled: the loop-thread CPU of the
  natural major collections that ran in that same gap.

The heartbeat observes only clocks and in-memory counters. It reads no
``/proc`` or other file on the loop thread: an earlier per-heartbeat
scheduler run-delay credit read ``/proc/self/task/*/schedstat`` there, and
those reads themselves delayed the heartbeat by up to 153 ms on a loaded
host, while the credit measured 0.000 s median and 0.009 s max at four busy
processes (PR #321 review 1, M1).

The baseline's contribution is capped. ``gap_bound`` refuses an allowance and
cap whose sum exceeds ``CEILING_SECONDS``, so every bound stays at least 50 ms
below a 400 ms loop stall: a stall that long produces a wall gap of at least
400 ms on any runner, so it is rejected however noisy the runner reports.

Per body (wall = allowance + min(baseline, cap); ceiling = allowance + cap):

==============  ===============  ====  =======  ==========================
body            wall allowance   cap   ceiling  thread CPU bound
==============  ===============  ====  =======  ==========================
alarmo          .30              .05   .35      .10 fixed per gap (GC off)
baseline        .30              .05   .35      .10 fixed per gap (GC off)
continuation    .10              .10   .20      .10 total, fixed (GC off)
catalog         .25              .10   .35      .10 + min(gen-2 CPU, .10)
native          .25              .10   .35      .10 + min(gen-2 CPU, .10)
upstream        .25              .10   .35      .10 + min(gen-2 CPU, .10)
==============  ===============  ====  =======  ==========================

The idle detection floor is the allowance plus the measured idle baseline,
the bound a gap gets when the runner is quiet. Measured 2026-10-28 on a
10-core host with no added load, 25 isolated runs per body, median / max:
alarmo .3109 / .3112 s (10 ms heartbeat), baseline .3059 / .3061 s,
continuation .1056 / .1058 s, catalog, native and upstream .2556 / .2559 s;
the CPU floor in catalog, native and upstream is .1000 / .1000 s (no major
collection fell in the binding gap). On a loaded runner the wall bound rises
above that floor only as the pre-window idle baseline rises, and the CPU
bound only with the collection CPU measured in the same gap; neither passes
its ceiling.
"""

from __future__ import annotations

import asyncio
import math
import time

STALL_SECONDS = .4
CEILING_SECONDS = .35
BASELINE_SECONDS = .3
BASELINE_CAP_SECONDS = .1


async def unstalled_gap(interval: float, seconds: float = BASELINE_SECONDS) -> float:
    """Longest gap of the same heartbeat with no work under test."""

    gaps = []
    last = time.perf_counter()
    deadline = last + seconds
    while last < deadline:
        await asyncio.sleep(interval)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
    return max(gaps)


def gap_bound(baseline: float, allowance: float, cap: float = BASELINE_CAP_SECONDS) -> float:
    """The allowance plus the capped baseline, refusing a bound near a stall."""

    if not (math.isfinite(allowance) and allowance > 0 and math.isfinite(cap) and cap >= 0):
        raise ValueError("loop-gap allowance and cap must be finite, the allowance positive")
    if allowance + cap > CEILING_SECONDS:
        raise ValueError("loop-gap bound could come within 50 ms of a 400 ms stall")
    if math.isnan(baseline) or baseline < 0:
        raise ValueError("loop-gap baseline must be a non-negative measurement")
    return allowance + min(baseline, cap)


def gap_report(gaps, baselines, allowance: float, cap: float = BASELINE_CAP_SECONDS) -> dict:
    """The gap closest to (or furthest past) its own bound, with that bound."""

    if not gaps or len(gaps) != len(baselines):
        raise ValueError("every measured gap needs its own baseline")
    gap, baseline = max(zip(gaps, baselines), key=lambda pair: pair[0] - gap_bound(pair[1], allowance, cap))
    return {"gap_seconds": gap, "baseline_seconds": baseline,
            "bound_seconds": gap_bound(baseline, allowance, cap),
            "allowance_seconds": allowance, "cap_seconds": cap,
            "maximum_gap_seconds": max(gaps), "maximum_baseline_seconds": max(baselines)}


def gap_message(kind: str, report: dict) -> str:
    """Child assertion message: the measured gap, its baseline and its bound."""

    return (f"{kind} gap {report['gap_seconds']:.4f}s is not below its bound {report['bound_seconds']:.4f}s"
            f" = allowance {report['allowance_seconds']:.3f}s + min(same-child baseline"
            f" {report['baseline_seconds']:.4f}s, cap {report['cap_seconds']:.3f}s); longest {kind.lower()}"
            f" gap {report['maximum_gap_seconds']:.4f}s, largest baseline {report['maximum_baseline_seconds']:.4f}s.")


def child_failure(summary: str, result) -> str:
    """Outer failure message carrying the child's measurements and assertion."""

    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    lines += [line for line in result.stderr.splitlines() if line.startswith("AssertionError")]
    return "\n".join([summary, *(line[:2000] for line in lines[-6:])])
