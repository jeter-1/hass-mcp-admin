"""Run one measured responsiveness body with a 400 ms loop stall (HAMCP-219).

Not collected by discovery. Usage: ``loop_stall_probe.py NAME STALL NOISY``.
NAME selects the measured body. STALL ``1`` wraps its measured call with one
``time.sleep(.4)`` on the loop thread (no CPU time), as the HAMCP-208 review 1
probe does. NOISY ``1`` makes the same-child idle baseline report an
arbitrarily noisy runner (a huge unstalled gap), so each wall bound sits at
its cap. The probe prints one verdict line and exits 1
when the body fails.
"""
import importlib
import sys
import time
import unittest
from unittest.mock import patch

BODIES = {
    "alarmo": ("test_alarmo_inspection_pagination", "PaginationTests",
               "_maximum_report_fitting_is_responsive_and_preserves_counts",
               "ha_mcp_engineering.integration_inspection.service", "IntegrationInspectionService", "inspect"),
    "baseline": ("test_automation_baseline_capture", "CaptureTests", "_maximum_capture_bounded_pages_responsive",
                 "ha_mcp_engineering.audit_baseline.capture_service", "BaselineCaptureService", "capture"),
    "continuation": ("test_rc2dev4_release_hardening", "DependencyBakeAcceptanceTests",
                     "test_cursor_continuation_is_under_100ms_and_never_rebuilds",
                     "ha_mcp_engineering.dependency.service", "EntityDependencyAnalysisService", "analyze"),
    "catalog": ("test_dashboard_analysis_catalog", "AssembledTests", "_full_assembled_path",
                "ha_mcp_engineering.tools.dashboard", None, "dashboard_integrity_analysis"),
    "native": ("test_dashboard_analysis_transport", "NativeTransportTests", "_maximum_native_path",
               "ha_mcp_engineering.clients.dashboard_analysis", "InventoryPeer", "read"),
    "upstream": ("test_dashboard_analysis_transport", "UpstreamTransportTests", "_maximum_upstream_path",
                 "ha_mcp_engineering.clients.mcp", "McpDashboardTransport", "execute_analysis_read"),
}


def main(name, stall, noisy):
    module_name, case_name, method, target_module, target_class, attr = BODIES[name]
    module = importlib.import_module(module_name)
    case = getattr(module, case_name)(method)
    owner = importlib.import_module(target_module)
    target = getattr(owner, target_class) if target_class else owner
    original = getattr(target, attr)
    injected = []

    async def stalled(*args, **kwargs):
        # The continuation body's first analyze builds the index; stall only
        # the measured cursor continuation, as the review 1 probe does.
        if stall and not injected and (name != "continuation" or kwargs.get("cursor")):
            started = time.perf_counter()
            time.sleep(.4)
            injected.append(time.perf_counter() - started)
        return await original(*args, **kwargs)

    async def noisy_gap(*args, **kwargs):
        return 10.0

    patches = [patch.object(target, attr, stalled)]
    if noisy:
        # The bodies import the helper module as support.loop_gap or
        # tests.support.loop_gap; patch it under either name.
        for loaded in {id(m): m for m in (sys.modules.get("support.loop_gap"),
                                          sys.modules.get("tests.support.loop_gap")) if m}.values():
            patches.append(patch.object(loaded, "unstalled_gap", noisy_gap))
    for active in patches:
        active.start()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
    finally:
        for active in reversed(patches):
            active.stop()
    print("LOOP_STALL_PROBE", name, "STALL", injected, "NOISY", bool(noisy),
          "BODY_PASSED", result.wasSuccessful(), flush=True)
    if stall and not (len(injected) == 1 and injected[0] >= .4):
        raise SystemExit(2)
    raise SystemExit(not result.wasSuccessful())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1")
