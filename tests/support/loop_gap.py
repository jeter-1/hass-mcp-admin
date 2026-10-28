"""Same-child baselines for event-loop responsiveness bounds (HAMCP-219).

A responsiveness body schedules a heartbeat on the event loop and bounds the
longest wall gap between its turns, and the loop-thread CPU between them,
while the work under test runs. Each gap is bounded by a fixed allowance plus
a baseline measured in the same child at the same moment, never by a retry:

- wall: the unstalled heartbeat gap measured idle just before the window,
  plus the time this process's threads spent runnable but waiting for a CPU
  during that same gap (Linux ``schedstat`` run delay; 0 elsewhere). On a
  busy runner the loop thread, or the worker holding the GIL it needs, waits
  for the scheduler; the run delay measures exactly that wait. A thread
  sleeping or blocked is not runnable, so a blocking stall adds nothing.
- thread CPU, where collection stays enabled: the loop-thread CPU of the
  natural major collections that ran in that same gap.

The baseline's contribution is capped. ``gap_bound`` refuses an allowance and
cap whose sum exceeds ``CEILING_SECONDS``, so every bound stays at least 50 ms
below a 400 ms loop stall: a stall that long produces a wall gap of at least
400 ms on any runner, so it is rejected however noisy the runner reports.
"""

from __future__ import annotations

import asyncio
import math
import os
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


def runner_delay() -> float:
    """Seconds this process's threads have waited runnable for a CPU (0 if unknown)."""

    total = 0
    try:
        tasks = os.listdir("/proc/self/task")
    except OSError:
        return 0.0
    for task in tasks:
        try:
            with open(f"/proc/self/task/{task}/schedstat", "rb") as stats:
                total += int(stats.read().split()[1])
        except (OSError, IndexError, ValueError):
            pass
    return total / 1e9


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
