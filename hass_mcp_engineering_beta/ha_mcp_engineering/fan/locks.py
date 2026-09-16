"""Fan-specific selective holds over the unchanged shared F3 lock format."""
from ..f3.locks import DurableLockStore, LockOwnershipError
from ..f3.models import LockHandle, LockOwner, LockTiming, LockToken


class FanLockStore(DurableLockStore):
    def __init__(self, root, declaration):
        super().__init__(root)
        self.declaration = declaration

    def promote_to_conflict_hold(self, handle, *, reason_code):
        prepared = self.declaration(handle.owner.task_id)
        if prepared is None or handle.owner.operation_id != prepared.operation:
            raise LockOwnershipError("fan hold declaration is unavailable")
        return self.promote_selective_conflict_hold(
            handle, retained_keys=("entity:" + prepared.request.entity_id,),
            reason_code=reason_code,
        )

    def reconcile_terminal_hold(self, record):
        """Retry only fenced local settlement after an interrupted promotion.

        Never release a target or acquire another owner's keys. A failed atomic
        transaction leaves the previous complete lock set intact and visible.
        """
        if record.normalized_outcome != "manual_review_required":
            return
        identity = record.execution_identity()
        prepared = self.declaration(identity.task_id)
        if prepared is None or record.prepared_operation_hash != prepared.prepared_operation_hash:
            raise LockOwnershipError("fan hold declaration changed")
        owned = [item for item in self.records() if item.task_id == identity.task_id]
        expected = {(item["key"], item["generation"], item["mode"]) for item in record.lock_tokens}
        target = "entity:" + prepared.request.entity_id
        owner = LockOwner(identity.owner_id, identity.task_id, None,
                          prepared.operation, identity.attempt_id)
        if any(not self._same_owner(item, owner) for item in owned):
            raise LockOwnershipError("fan hold owner changed")
        if (len(owned) == 1 and owned[0].key == target and owned[0].conflict_hold
                and (owned[0].key, owned[0].generation, owned[0].mode) in expected):
            return
        if not owned or {(item.key, item.generation, item.mode) for item in owned} != expected:
            raise LockOwnershipError("fan hold settlement was fenced")
        self.promote_to_conflict_hold(
            LockHandle(owner, tuple(LockToken(item.key, item.generation, item.mode)
                                    for item in owned),
                       min(item.acquired_at for item in owned),
                       min(item.lease_expires_at for item in owned), LockTiming(120, 10, 0)),
            reason_code="manual_review_unresolved_dispatch",
        )

    def hold_settled(self, record, owned):
        prepared = self.declaration(record.execution_identity().task_id)
        if prepared is None or prepared.prepared_operation_hash != record.prepared_operation_hash:
            return False
        identity = record.execution_identity()
        owner = LockOwner(identity.owner_id, identity.task_id, None,
                          prepared.operation, identity.attempt_id)
        return (len(owned) == 1 and owned[0].key == "entity:" + prepared.request.entity_id
                and owned[0].conflict_hold and self._same_owner(owned[0], owner)
                and {"key": owned[0].key, "generation": owned[0].generation,
                     "mode": owned[0].mode, "owner_id": owned[0].owner_id} in record.lock_tokens)
