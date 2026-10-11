"""Explicit marker for tests that deliberately measure real elapsed time.

Marked tests keep wall-clock waits because the waits are the behavior under
test (responsiveness, latency bounds, real timer delivery). The CI shard plan
places every unit containing a marked test (a whole module, or a single test of
a module the plan splits by test) in the isolated real-time shard, so these
waits never share a shard with ordinary unit tests.
"""

from __future__ import annotations

ATTRIBUTE = "__hamcp_real_time_measurement__"


def real_time_measurement(reason: str):
    """Mark a test method or class as an intentional real-time measurement."""

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("real_time_measurement requires a reason")

    def mark(target):
        setattr(target, ATTRIBUTE, reason)
        return target

    return mark
