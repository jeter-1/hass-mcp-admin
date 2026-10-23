"""Run the existing measured test body with a 400 ms non-CPU loop stall.

No source file is modified. Same probe runs against the base and target.
"""
import sys
import time
import unittest
from unittest.mock import patch

name = sys.argv[1]
if name == "alarmo":
    from test_alarmo_inspection_pagination import PaginationTests
    from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService
    cls, attr = IntegrationInspectionService, "inspect"
    case = PaginationTests("_maximum_report_fitting_is_responsive_and_preserves_counts")
elif name == "baseline":
    from test_automation_baseline_capture import CaptureTests, BaselineCaptureService
    cls, attr = BaselineCaptureService, "capture"
    case = CaptureTests("_maximum_capture_bounded_pages_responsive")
elif name == "continuation":
    from test_rc2dev4_release_hardening import DependencyBakeAcceptanceTests, EntityDependencyAnalysisService
    cls, attr = EntityDependencyAnalysisService, "analyze"
    case = DependencyBakeAcceptanceTests("test_cursor_continuation_is_under_100ms_and_never_rebuilds")
else:
    raise ValueError(name)

original = getattr(cls, attr)
injected = []
async def blocked(self, *args, **kwargs):
    if not injected and (name != "continuation" or kwargs.get("cursor")):
        started = time.perf_counter()
        time.sleep(.4)
        injected.append(time.perf_counter() - started)
    return await original(self, *args, **kwargs)

with patch.object(cls, attr, blocked):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
print("INJECTED_LOOP_BLOCK_SECONDS", injected, "MUTATION_SURVIVED", result.wasSuccessful(), flush=True)
assert len(injected) == 1 and injected[0] >= .4
raise SystemExit(not result.wasSuccessful())
