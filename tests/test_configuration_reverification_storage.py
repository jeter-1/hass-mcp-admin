"""Integrity, single-reader ownership and crash boundaries for optional receipts."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.governance import reverification_storage as storage


def start_body():
    return {"binding": {"task_id": "a" * 32, "expected_plan_hash": "b" * 64,
                        "request_id": "00000000-0000-4000-8000-000000000001"},
            "plan_id": "c" * 32, "history_hash": "d" * 64,
            "started_at": "2026-09-01T00:00:00+00:00",
            "lock_requests": [{"key": "automation:example", "mode": "exclusive",
                               "scopes": ["resource"], "reason_codes": ["target"]}]}


def finish_body():
    return {"binding": start_body()["binding"], "plan_id": "c" * 32,
            "checked_at": "2026-09-01T00:00:01+00:00", "status": "interrupted_read",
            "review_resolved": False, "audit_event_id": "e" * 64,
            "configuration_mutations": 0, "device_commands": 0, "approval_consumptions": 0,
            "redispatches": 0, "fallback": "none", "receipt_persisted": False, "replayed": False}


class ReverificationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = storage.ReverificationStore(self.root)

    def populate(self):
        with self.store.transaction() as tx:
            tx.append("started", start_body())
            tx.append("finished", finish_body())

    def test_optional_namespace_and_restart_readback(self):
        self.assertEqual(self.store.read(), [])
        self.assertFalse(self.store.root.exists())
        self.populate()
        events = storage.ReverificationStore(self.root).read()
        self.assertEqual([e["kind"] for e in events], ["started", "finished"])
        self.assertEqual(events[1]["previous"], events[0]["hash"])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_nonblocking_ownership_works_across_store_instances(self):
        with self.store.transaction():
            with self.assertRaises(storage.ReceiptBusy):
                with storage.ReverificationStore(self.root).transaction():
                    self.fail("competing reader acquired receipt ownership")
        with storage.ReverificationStore(self.root).transaction():
            pass

    def test_failed_replace_preserves_pending_declaration_and_no_temp_file(self):
        with self.store.transaction() as tx:
            tx.append("started", start_body())
            before = self.store.path.read_bytes()
            self.store.fault_hook = lambda stage: (_ for _ in ()).throw(OSError("synthetic")) if stage == "before_replace" else None
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append("finished", finish_body())
            self.assertEqual(self.store.path.read_bytes(), before)
            self.assertEqual(list(self.store.root.glob(".receipt-*")), [])
            self.assertFalse(self.store.commit_unknown)
        self.assertEqual(self.store.read()[-1]["kind"], "started")

    def test_post_replace_uncertainty_blocks_in_process_read_and_replay(self):
        with self.store.transaction() as tx:
            tx.append("started", start_body())
            self.store.fault_hook = lambda stage: (_ for _ in ()).throw(OSError("synthetic")) if stage == "after_replace" else None
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append("finished", finish_body())
        self.assertTrue(self.store.commit_unknown)
        with self.assertRaises(storage.ReceiptStorageError):
            self.store.read()
        # A new process must independently validate whatever actually survived;
        # the interrupted caller does not receive a successful receipt.
        self.assertEqual(storage.ReverificationStore(self.root).read()[-1]["kind"], "finished")

    def test_corruption_wrong_model_sequence_or_binding_is_not_projected(self):
        self.populate()
        original = json.loads(self.store.path.read_text())
        for mutate in (
                lambda d: d.update(model="future-unreviewed"),
                lambda d: d["events"][0].update(sequence=True),
                lambda d: d["events"][0]["body"].update(history_hash="0" * 64),
                lambda d: d["events"][1]["body"].update(review_resolved=True),
                lambda d: d["events"][1]["body"]["binding"].update(task_id="0" * 32),
                lambda d: d["events"].reverse()):
            value = deepcopy(original)
            mutate(value)
            self.store.path.write_text(json.dumps(value))
            with self.assertRaises(storage.ReceiptStorageError):
                self.store.read()

    def test_symlink_and_file_event_byte_limits(self):
        target = self.root / "unrelated"
        target.write_text("synthetic private sentinel")
        self.store.root.mkdir()
        self.store.path.symlink_to(target)
        with self.assertRaises(storage.ReceiptStorageError):
            self.store.read()
        self.store.path.unlink()
        self.populate()
        with patch.object(storage, "MAX_STORE_BYTES", 16):
            with self.assertRaises(storage.ReceiptStorageError):
                self.store.read()
        with self.store.transaction() as tx, patch.object(storage, "MAX_EVENTS", 2):
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append("started", start_body())
        self.assertEqual(target.read_text(), "synthetic private sentinel")

    def test_malformed_body_refuses_before_persistence(self):
        with self.store.transaction() as tx:
            for key, value in (("plan_id", "private/path"), ("lock_requests", []),
                               ("started_at", "2026-09-01T00:00:00")):
                body = start_body()
                body[key] = value
                with self.assertRaises(storage.ReceiptStorageError):
                    tx.append("started", body)
            self.assertFalse(self.store.path.exists())

    def test_bound_refusal_is_durable_replayable_and_never_positive(self):
        self.populate()
        body = finish_body()
        body['binding'] = {**body['binding'], 'request_id': '00000000-0000-4000-8000-000000000002'}
        body['status'] = 'read_authority_or_audit_unavailable'
        body['audit_event_id'] = None
        with self.store.transaction() as tx:
            tx.append('refused', body)
        restored = storage.ReverificationStore(self.root).read()
        self.assertEqual(restored[-1]['kind'], 'refused')
        self.assertFalse(restored[-1]['body']['review_resolved'])
        self.assertIsNone(restored[-1]['body']['audit_event_id'])
        with self.store.transaction() as tx:
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append('refused', body)
            changed = deepcopy(body)
            changed['binding']['request_id'] = '00000000-0000-4000-8000-000000000003'
            changed['binding']['expected_plan_hash'] = 'f' * 64
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append('refused', changed)
            changed['binding']['expected_plan_hash'] = body['binding']['expected_plan_hash']
            changed['review_resolved'] = True
            with self.assertRaises(storage.ReceiptStorageError):
                tx.append('refused', changed)

    def test_read_io_is_distinct_from_damaged_serialization(self):
        self.populate()
        with patch.object(storage.os, 'open', side_effect=PermissionError('SYNTHETIC_PRIVATE_IO')):
            with self.assertRaisesRegex(storage.ReceiptStorageError, '^receipt_storage_unavailable$'):
                self.store.read()
        self.store.path.write_text('invalid JSON SYNTHETIC_PRIVATE_BODY')
        with self.assertRaisesRegex(storage.ReceiptStorageError, '^receipt_integrity_failure$'):
            self.store.read()
