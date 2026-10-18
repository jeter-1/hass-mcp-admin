"""HAMCP-171 D1/D5: in-memory health safety verdicts and the segment memo.

D5: one health job may memoize the pure segment scan; results are identical.
D1: a verdict that a record's exact bytes passed the persisted-plan safety
detector is kept in process memory between health checks. It is reused only
when canonical path, ``(st_ino, st_size, st_mtime_ns)``, the SHA-256 of the
bytes parsed by this call and the detector context all match. Anything else
validates fresh. ``deep_storage_audit`` bypasses the cache. Fail-closed
detection of tampered, corrupt and partial records is unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import service as service_module  # noqa: E402
from ha_mcp_engineering.governance import storage as storage_module  # noqa: E402
from ha_mcp_engineering.governance.runtime import GovernanceRuntime  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.sanitization import (  # noqa: E402
    SegmentMemo,
    rescan_stored_plan,
    sanitize_untrusted_data,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)

# Same length (19 bytes) as the configured synthetic sensitive value below.
BENIGN = b"Exercise exact post"
BENIGN_EDIT = b"Exercise EXACT post"
UNSAFE_EDIT = b"dev14-test-ha-token"
UNSAFE_REASON = {"reason": "unsafe_persisted_configuration_plan"}


class _FstatFails:
    """Module proxy for ``storage.os`` whose ``fstat`` always fails."""

    def __getattr__(self, name):
        return getattr(os, name)

    @staticmethod
    def fstat(_descriptor):
        raise OSError("synthetic fstat failure")


class _FstatWrongSize(_FstatFails):
    @staticmethod
    def fstat(descriptor):
        real = os.fstat(descriptor)
        fields = list(real)
        fields[6] = real.st_size + 1  # st_size
        return os.stat_result(fields, {
            "st_mtime_ns": real.st_mtime_ns, "st_atime_ns": real.st_atime_ns,
            "st_ctime_ns": real.st_ctime_ns,
        })


class VerdictCacheTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.plan_ids = [
            (await self.create_automation_plan())["plan_id"] for _ in range(3)
        ]
        self.validated: list[list[str]] = []
        original = service_module._validate_health_records

        def counting(plans, sensitive_values):
            self.validated.append(sorted(plan.plan_id for plan in plans))
            return original(plans, sensitive_values)

        patcher = patch.object(
            service_module, "_validate_health_records", side_effect=counting
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def health(self, service=None, **kwargs):
        result = await (service or self.service).async_health_summary(**kwargs)
        self.last_result = result
        return result["plan_store_scaling"]["safety_verdict_cache"]

    def path(self, plan_id):
        return self.repository._path(plan_id)

    def stat_key(self, path):
        status = os.stat(path)
        return status.st_ino, status.st_size, status.st_mtime_ns

    def overwrite_same_stat(self, path, old, new):
        """Change bytes in place keeping inode, size and mtime identical."""
        self.assertEqual(len(old), len(new))
        before = os.stat(path)
        offset = path.read_bytes().index(old)
        with open(path, "r+b") as handle:
            handle.seek(offset)
            handle.write(new)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(
            (before.st_ino, before.st_size, before.st_mtime_ns),
            self.stat_key(path),
        )

    async def warm(self):
        cold = await self.health()
        self.assertEqual(cold["last_run"]["misses"], 3)
        self.assertEqual(cold["last_run"]["hits"], 0)
        self.assertEqual(cold["entries"], 3)

    def fresh_service(self):
        return ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=lambda: datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            sensitive_values=("dev14-test-access-secret", "dev14-test-ha-token"),
        )

    # ----- hits ------------------------------------------------------------

    async def test_unchanged_records_hit_and_counters_are_truthful(self):
        hits_before = self.service._health_cache_hit_count
        await self.warm()
        self.assertEqual(self.service._health_cache_hit_count, hits_before)
        warm = await self.health()
        self.assertEqual(warm["last_run"], {
            "mode": "cached", "hits": 3, "misses": 0,
            "not_applicable": 0, "pruned": 0,
        })
        self.assertEqual(self.validated[-1], [])
        self.assertEqual(self.service._health_cache_hit_count, hits_before + 1)
        self.assertEqual(
            self.last_result["plan_store_scaling"]["health_cache_hit_count"],
            hits_before + 1,
        )
        self.assertEqual(warm["totals"]["hits"], 3)
        self.assertEqual(warm["totals"]["misses"], 3)
        self.assertEqual(warm["scope"], "process_memory_only")
        self.assertFalse(warm["persisted"])
        self.assertFalse(warm["authoritative_reads_use_cache"])
        # Every record is still collected and parsed on a cached call.
        self.assertEqual(self.last_result["total_plans"], 3)

    # ----- misses ----------------------------------------------------------

    async def test_same_size_same_mtime_content_change_misses(self):
        await self.warm()
        target = self.plan_ids[1]
        self.overwrite_same_stat(self.path(target), BENIGN, BENIGN_EDIT)
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (2, 1))
        self.assertEqual(self.validated[-1], [target])

    async def test_same_stat_tamper_is_detected_with_warm_cache(self):
        await self.warm()
        self.overwrite_same_stat(self.path(self.plan_ids[0]), BENIGN, UNSAFE_EDIT)
        with self.assertRaises(GovernanceError) as error:
            await self.health()
        self.assertEqual(error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR)
        self.assertEqual(error.exception.details, UNSAFE_REASON)
        # A failed scan discards every cached verdict.
        self.assertEqual(self.service._safety_verdict_cache, {})
        self.assertEqual(
            self.service._safety_verdict_totals["invalidations"], 1
        )
        # The refusal repeats; nothing was cached as safe.
        with self.assertRaises(GovernanceError):
            await self.health()
        # Authoritative reads never consult the health cache.
        with self.assertRaises(GovernanceError):
            self.service._load(self.plan_ids[0])

    async def test_tamper_is_detected_without_cache_and_on_deep_rescan(self):
        await self.warm()
        self.overwrite_same_stat(self.path(self.plan_ids[2]), BENIGN, UNSAFE_EDIT)
        with self.assertRaises(GovernanceError) as deep:
            await self.health(deep_storage_audit=True)
        self.assertEqual(deep.exception.details, UNSAFE_REASON)
        with self.assertRaises(GovernanceError) as cold:
            await self.health(self.fresh_service())
        self.assertEqual(cold.exception.details, UNSAFE_REASON)

    async def test_inode_change_misses(self):
        await self.warm()
        target = self.plan_ids[0]
        path = self.path(target)
        before = os.stat(path)
        body = path.read_bytes()
        staged = path.with_name(path.name + ".staged")
        staged.write_bytes(body)
        os.replace(staged, path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertNotEqual(before.st_ino, after.st_ino)
        self.assertEqual(
            (before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns)
        )
        self.assertEqual(path.read_bytes(), body)
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (2, 1))
        self.assertEqual(self.validated[-1], [target])

    async def test_size_change_misses(self):
        await self.warm()
        target = self.plan_ids[2]
        path = self.path(target)
        before = os.stat(path)
        with open(path, "ab") as handle:
            handle.write(b"\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        self.assertEqual(
            (before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns)
        )
        self.assertNotEqual(before.st_size, after.st_size)
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (2, 1))
        self.assertEqual(self.validated[-1], [target])

    async def test_mtime_change_misses(self):
        await self.warm()
        target = self.plan_ids[1]
        path = self.path(target)
        before = os.stat(path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (2, 1))
        self.assertEqual(self.validated[-1], [target])

    async def test_new_record_misses(self):
        await self.warm()
        before = {pid: self.stat_key(self.path(pid)) for pid in self.plan_ids}
        created = await self.create_automation_plan()
        # Creating a plan for the same target may rewrite an older record
        # (supersession); a rewritten record must miss as well.
        rewritten = [
            pid for pid in self.plan_ids
            if self.stat_key(self.path(pid)) != before[pid]
        ]
        run = (await self.health())["last_run"]
        self.assertEqual(
            (run["hits"], run["misses"]), (3 - len(rewritten), 1 + len(rewritten))
        )
        self.assertEqual(
            self.validated[-1], sorted([created["plan_id"], *rewritten])
        )

    async def test_deleted_record_is_pruned(self):
        await self.warm()
        self.path(self.plan_ids[0]).unlink()
        state = await self.health()
        self.assertEqual(state["last_run"]["pruned"], 1)
        self.assertEqual((state["last_run"]["hits"], state["last_run"]["misses"]), (2, 0))
        self.assertEqual(state["entries"], 2)
        self.assertNotIn(self.plan_ids[0], self.service._safety_verdict_cache)

    async def test_stat_failure_validates_fresh_and_stores_nothing(self):
        await self.warm()
        for proxy in (_FstatFails(), _FstatWrongSize()):
            with self.subTest(proxy=type(proxy).__name__):
                with patch.object(storage_module, "os", proxy):
                    state = await self.health()
                self.assertEqual(
                    (state["last_run"]["hits"], state["last_run"]["misses"]), (0, 3)
                )
                self.assertEqual(self.validated[-1], sorted(self.plan_ids))
                self.assertEqual(state["entries"], 0)
                # Nothing unproven was cached: the next call validates again.
                state = await self.health()
                self.assertEqual(state["last_run"]["misses"], 3)

    async def test_detector_context_change_misses(self):
        await self.warm()
        self.service.sensitive_values = (
            *self.service.sensitive_values, "dev14-test-extra-secret"
        )
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (0, 3))

    # ----- deep re-scan ----------------------------------------------------

    async def test_deep_rescan_bypasses_then_repopulates(self):
        await self.warm()
        deep = await self.health(deep_storage_audit=True)
        self.assertEqual(deep["last_run"], {
            "mode": "deep_rescan", "hits": 0, "misses": 3,
            "not_applicable": 0, "pruned": 0,
        })
        self.assertEqual(self.validated[-1], sorted(self.plan_ids))
        self.assertEqual(deep["totals"]["deep_rescans"], 1)
        hits_before = self.service._health_cache_hit_count
        warm = await self.health()
        self.assertEqual(warm["last_run"]["hits"], 3)
        self.assertEqual(self.service._health_cache_hit_count, hits_before + 1)

    async def test_internal_deep_audit_discards_every_verdict(self):
        await self.warm()
        self.service.deep_audit_plan_store()
        self.assertEqual(self.service._safety_verdict_cache, {})
        after = await self.health()
        self.assertEqual(after["last_run"]["hits"], 0)
        self.assertEqual(after["last_run"]["misses"], 3)
        self.assertEqual(self.validated[-1], sorted(self.plan_ids))

    async def test_public_tool_deep_storage_audit_reaches_deep_path(self):
        from ha_mcp_engineering.tools import compatibility
        runtime = GovernanceRuntime()
        runtime.service = self.service
        registry = HealthRegistry(governance=runtime)
        with (
            patch.object(compatibility, "HEALTH", registry),
            patch.object(compatibility, "rest", AsyncMock()) as rest,
            patch.object(compatibility, "ws_command", AsyncMock()) as websocket,
        ):
            modes = []
            for kwargs in ({}, {}, {"deep_storage_audit": True}, {}):
                response = json.loads(
                    await compatibility.get_server_health(check_ha=False, **kwargs)
                )
                self.assertTrue(response["success"])
                cache = response["data"]["governance"]["plan_store_scaling"][
                    "safety_verdict_cache"
                ]
                modes.append((cache["last_run"]["mode"], cache["last_run"]["hits"]))
        self.assertEqual(
            modes, [("cached", 0), ("cached", 3), ("deep_rescan", 0), ("cached", 3)]
        )
        rest.assert_not_awaited()
        websocket.assert_not_awaited()
        rendered = json.dumps(response)
        for plan_id in self.plan_ids:
            digest = hashlib.sha256(self.path(plan_id).read_bytes()).hexdigest()
            self.assertNotIn(digest, rendered)

    # ----- corrupt / partial -----------------------------------------------

    async def test_corrupt_and_partial_records_are_detected_with_and_without_cache(self):
        await self.warm()
        cases = (
            ("partial", lambda body: body[: len(body) // 2], {}),
            ("corrupt", lambda body: b"{", {"deep_storage_audit": True}),
        )
        for index, (name, damage, kwargs) in enumerate(cases):
            with self.subTest(case=name):
                path = self.path(self.plan_ids[index])
                path.write_bytes(damage(path.read_bytes()))
                before = self.repository.corruption_count
                with self.assertRaises(GovernanceError) as error:
                    await self.health(**kwargs)
                self.assertEqual(
                    error.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR
                )
                self.assertNotEqual(
                    error.exception.details.get("reason"), "health_snapshot_superseded"
                )
                self.assertEqual(self.repository.corruption_count, before + 1)
                self.assertFalse(path.exists())
                self.assertEqual(self.service._safety_verdict_cache, {})
        # The surviving record validates fresh after the invalidation.
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (0, 1))

    # ----- lifetime --------------------------------------------------------

    async def test_restart_starts_empty_and_nothing_is_persisted(self):
        await self.warm()
        await self.health()
        digests = {
            hashlib.sha256(self.path(plan_id).read_bytes()).hexdigest()
            for plan_id in self.plan_ids
        }
        for path in self.root.rglob("*"):
            if path.is_file():
                body = path.read_bytes()
                for digest in digests:
                    self.assertNotIn(digest.encode(), body, str(path))
        restarted = self.fresh_service()
        self.assertEqual(restarted._safety_verdict_cache, {})
        self.assertIsNone(restarted._safety_verdict_health()["last_run"])
        run = (await self.health(restarted))["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (0, 3))

    async def test_unexpected_worker_failure_discards_every_verdict(self):
        await self.warm()
        path = self.path(self.plan_ids[0])
        before = os.stat(path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        with patch.object(
            service_module, "_validate_health_records",
            side_effect=RuntimeError("synthetic worker failure"),
        ):
            with self.assertRaises(RuntimeError):
                await self.service.async_health_summary()
        self.assertEqual(self.service._safety_verdict_cache, {})
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (0, 3))

    async def test_abandoned_job_failure_discards_every_verdict(self):
        await self.warm()
        path = self.path(self.plan_ids[0])
        before = os.stat(path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        release = __import__("threading").Event()

        def failing(_plans, _sensitive_values):
            loop.call_soon_threadsafe(entered.set)
            release.wait(3)
            raise RuntimeError("synthetic abandoned failure")

        with patch.object(
            service_module, "_validate_health_records", side_effect=failing
        ):
            pending = asyncio.create_task(self.service.async_health_summary())
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        release.set()
        # The next reader drains the abandoned failure before validating.
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (0, 3))

    async def test_cancel_before_worker_completion_publishes_no_verdicts(self):
        original = service_module._validate_health_records
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        release = __import__("threading").Event()

        def slow(plans, sensitive_values):
            loop.call_soon_threadsafe(entered.set)
            release.wait(3)
            return original(plans, sensitive_values)

        with patch.object(service_module, "_validate_health_records", side_effect=slow):
            pending = asyncio.create_task(self.service.async_health_summary())
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            release.set()
            job = self.service._health_validation_job
            if job is not None:
                await job
        self.assertEqual(self.service._safety_verdict_cache, {})
        run = (await self.health())["last_run"]
        self.assertEqual(run["misses"], 3)

    async def test_cancel_after_validation_retains_only_verified_verdicts(self):
        original = self.service._safety_verdict_store
        stored = {}

        def store_then_cancel(plans, pending, run):
            # Cancel at the real fence await after the successful worker;
            # do not add an artificial yield to production code.
            original(plans, pending, run)
            stored.update(self.service._safety_verdict_cache)
            asyncio.current_task().cancel()

        with patch.object(
            self.service, "_safety_verdict_store", side_effect=store_then_cancel
        ), patch.object(
            self.service, "_resolve_lifecycle", wraps=self.service._resolve_lifecycle
        ) as lifecycle:
            pending = asyncio.create_task(self.service.async_health_summary())
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(pending.cancelled())
            lifecycle.assert_not_called()

        self.assertEqual(self.validated, [sorted(self.plan_ids)])
        self.assertEqual(set(stored), set(self.plan_ids))
        self.assertEqual(self.service._safety_verdict_cache, stored)
        run = (await self.health())["last_run"]
        self.assertEqual((run["hits"], run["misses"]), (3, 0))
        self.assertEqual(self.validated[-1], [])


class SegmentMemoTests(unittest.TestCase):
    SECRETS = ("synthetic-known-secret-value", "another-synthetic-secret")
    CORPUS = (
        "plain text",
        "token: synthetic-known-secret-value and more",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        "https://user:pass@example.invalid/path?access_token=abcdef0123456789",
        "https://example.invalid/api/webhook/abcdefabcdefabcdef",
        '{"password": "hunter2-synthetic"}',
        "[REDACTED:token] already marked",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c2lnbmF0dXJlc3ludGhldGlj",
        "",
        "x" * 5000,
    )

    def value(self):
        return {
            "repeat": [self.CORPUS[index % len(self.CORPUS)] for index in range(60)],
            "nested": {text[:12] or "empty": {"value": text} for text in self.CORPUS},
            "password": "a-synthetic-password",
        }

    def test_memo_result_is_identical_and_reused(self):
        plain = sanitize_untrusted_data(self.value(), known_secrets=self.SECRETS)
        memo = SegmentMemo(self.SECRETS)
        memoized = sanitize_untrusted_data(
            self.value(), known_secrets=self.SECRETS, segment_memo=memo
        )
        again = sanitize_untrusted_data(
            self.value(), known_secrets=self.SECRETS, segment_memo=memo
        )
        self.assertEqual(plain, memoized)
        self.assertEqual(plain, again)
        self.assertGreater(memo.hits, 0)
        # Over-limit text is computed every time and never retained.
        self.assertNotIn("x" * 5000, memo._results)

    def test_memo_bound_to_other_secrets_or_another_pass_is_refused(self):
        with self.assertRaises(TypeError):
            sanitize_untrusted_data(
                "text", known_secrets=self.SECRETS,
                segment_memo=SegmentMemo(("different-secret",)),
            )
        # HAMCP-191: the strict pass is the only general pass, so it takes a
        # memo; a memo bound to it never serves the D11 stored-plan re-scan,
        # and the reverse.
        strict_memo = SegmentMemo(self.SECRETS)
        sanitize_untrusted_data(
            "text", known_secrets=self.SECRETS, identity_markers_only=True,
            segment_memo=strict_memo,
        )
        with self.assertRaises(TypeError):
            rescan_stored_plan(
                "text", known_secrets=self.SECRETS, segment_memo=strict_memo,
            )
        stored_memo = SegmentMemo(self.SECRETS)
        rescan_stored_plan(
            "text", known_secrets=self.SECRETS, segment_memo=stored_memo,
        )
        with self.assertRaises(TypeError):
            sanitize_untrusted_data(
                "text", known_secrets=self.SECRETS, segment_memo=stored_memo,
            )
        with self.assertRaises(TypeError):
            sanitize_untrusted_data(
                "text", known_secrets=self.SECRETS, segment_memo=object(),
            )

    def test_validation_job_owns_a_fresh_memo(self):
        created = []
        original = service_module.SegmentMemo

        def tracking(secrets):
            memo = original(secrets)
            created.append(memo)
            return memo

        with patch.object(service_module, "SegmentMemo", side_effect=tracking):
            service_module._validate_health_records([], ("a-secret",))
            service_module._validate_health_records([], ("a-secret",))
        self.assertEqual(len(created), 2)
        self.assertIsNot(created[0], created[1])


if __name__ == "__main__":
    unittest.main()
