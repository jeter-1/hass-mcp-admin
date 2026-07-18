"""Reproducible RC2dev4/RC2dev5 transport bake harness.

Fixture mode is the default and cannot contact Home Assistant. Network mode
accepts only an explicitly configured local/test MCP URL and performs MCP
initialize requests; it contains no state-changing tool call.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parents[1]
INITIALIZE = json.dumps({
    "jsonrpc": "2.0",
    "id": "rc2dev5-bake",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "rc2dev5-bake-harness", "version": "1"},
    },
}).encode("utf-8")

FIXTURE_TESTS = {
    "auth": (
        "tests.test_rc2dev4_release_hardening.TransportBakeHarnessTests.test_auth_failure_throttles_without_disabling_valid_client",
    ),
    "rate-limit": (
        "tests.test_rc2dev4_release_hardening.TransportBakeHarnessTests.test_rate_limit_is_structured_audited_and_refills",
    ),
    "dependency": (
        "tests.test_rc2dev4_release_hardening.DependencyBakeAcceptanceTests",
        "tests.test_rc2dev5_live_acceptance.DependencyFreshnessTests",
        "tests.test_rc2dev5_live_acceptance.PrewarmRuntimeTests",
    ),
    "upstream": (
        "tests.test_rc2dev4_release_hardening.DashboardOutcomeAndFreshnessTests",
        "tests.test_rc2dev5_live_acceptance.DashboardDomainOutcomeTests",
    ),
    "cursor": (
        "tests.test_rc2dev4_release_hardening.DependencyBakeAcceptanceTests.test_cursor_continuation_is_under_100ms_and_never_rebuilds",
        "tests.test_change_impact_analysis.ServiceTests.test_pagination_signed_binding_snapshot_reuse_and_no_counter_inflation",
        "tests.test_change_impact_analysis.ServiceTests.test_cursor_stales_only_after_index_invalidation_or_replacement",
    ),
    "large-response": (
        "tests.test_rc3a_dashboard_provider.PublicDashboardToolTests.test_large_configuration_returns_structured_omission",
        "tests.test_entity_dependency_analysis.IndexAndAnalysisTests.test_summary_and_evidence_outputs_are_bounded",
        "tests.test_beta_observability.RedactionAndAuditTests.test_audit_payload_is_bounded",
        "tests.test_rc2dev4_release_hardening.GovernanceLifecycleAcceptanceTests.test_plan_list_is_summary_and_full_plan_remains_retrievable",
    ),
    "governance": (
        "tests.test_rc2dev4_release_hardening.GovernanceLifecycleAcceptanceTests",
        "tests.test_rc2dev5_live_acceptance.GovernanceCompatibilityTests",
    ),
    "security": (
        "tests.test_rc2dev4_release_hardening.SanitizationAcceptanceTests",
        "tests.test_rc2dev5_live_acceptance.WebhookSanitizationTests",
    ),
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("all", *FIXTURE_TESTS), default="all")
    parser.add_argument("--network", action="store_true", help="Use an explicit local/test endpoint.")
    parser.add_argument(
        "--test-mcp-url",
        default=os.environ.get("RC2DEV5_TEST_MCP_URL", os.environ.get("RC2DEV4_TEST_MCP_URL", "")),
        help="Secret-bearing test URL; never printed or persisted.",
    )
    parser.add_argument("--allow-nonlocal-test-target", action="store_true")
    parser.add_argument("--acknowledge-test-system", action="store_true")
    parser.add_argument("--burst", type=int, default=30)
    parser.add_argument(
        "--allow-state-change",
        action="store_true",
        help="Reserved safety acknowledgement; this harness has no write scenario.",
    )
    return parser.parse_args(argv)


def _post_status(url: str) -> int:
    req = request.Request(
        url,
        data=INITIALIZE,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            response.read(4096)
            return response.status
    except error.HTTPError as exc:
        exc.read(4096)
        return exc.code
    except Exception as exc:
        # Never serialize the raw exception: it can contain the configured URL.
        raise RuntimeError("The configured test endpoint could not be reached safely.") from None


def run_network(args) -> int:
    if not args.test_mcp_url:
        raise SystemExit("Network mode requires RC2DEV5_TEST_MCP_URL or --test-mcp-url.")
    parsed = parse.urlsplit(args.test_mcp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("The test MCP URL is malformed.")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local and not (args.allow_nonlocal_test_target and args.acknowledge_test_system):
        raise SystemExit("Non-local targets require both explicit test-system acknowledgements.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if args.scenario in {"all", "auth"}:
        missing = _post_status(origin + "/mcp")
        invalid = [_post_status(origin + "/rc2dev5-invalid-path/mcp") for _ in range(6)]
        valid = _post_status(args.test_mcp_url)
        print(json.dumps({"scenario": "auth", "missing_status": missing, "invalid_statuses": invalid, "valid_status": valid}))
    if args.scenario in {"all", "rate-limit"}:
        count = max(2, min(args.burst, 100))
        statuses = [_post_status(args.test_mcp_url) for _ in range(count)]
        print(json.dumps({"scenario": "rate-limit", "request_count": count, "status_counts": {str(code): statuses.count(code) for code in sorted(set(statuses))}}))
    if args.scenario not in {"all", "auth", "rate-limit"}:
        raise SystemExit("That scenario is fixture-only; network dispatch was refused.")
    return 0


def run_fixtures(scenario: str) -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
    groups = FIXTURE_TESTS.values() if scenario == "all" else (FIXTURE_TESTS[scenario],)
    names = [name for group in groups for name in group]
    suite = unittest.TestLoader().loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.allow_state_change:
        print("State-change acknowledgement recorded; no harness scenario performs writes.")
    return run_network(args) if args.network else run_fixtures(args.scenario)


if __name__ == "__main__":
    raise SystemExit(main())
