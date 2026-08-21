"""Validated internal models for the isolated F3 execution core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from .contracts import F3_ADAPTER_CONTRACT_MODEL


LOCK_RECORD_SCHEMA_VERSION = 1
LOCK_STATE_SCHEMA_VERSION = 1
EXECUTION_RECORD_SCHEMA_VERSION = 1

MAX_IDENTIFIER_LENGTH = 128
MAX_LOCK_KEY_LENGTH = 320
MAX_EVIDENCE_ITEMS = 16
MAX_DIAGNOSTIC_ITEMS = 16
MAX_DIAGNOSTIC_LENGTH = 96
MAX_EXECUTION_EVENTS = 128

LOCK_KEY_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]{0,63}:[a-z0-9][a-z0-9_.-]{0,255}$"
)
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
EVIDENCE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


NORMALIZED_OUTCOME_TO_TASK_STATE: dict[str, str] = {
    "preflight_rejected": "failed_pre_dispatch",
    "lock_conflict": "failed_pre_dispatch",
    "provider_unavailable_pre_dispatch": "failed_pre_dispatch",
    "dispatch_failed_confirmed": "failed_post_dispatch",
    "dispatch_indeterminate": "observing",
    "observing": "observing",
    "verification_mismatch": "failed_post_dispatch",
    "succeeded_verified": "succeeded_verified",
    "failed_pre_dispatch": "failed_pre_dispatch",
    "failed_post_dispatch": "failed_post_dispatch",
    "manual_review_required": "manual_review_required",
    "cancelled_pre_dispatch": "cancelled_pre_dispatch",
}

TERMINAL_OUTCOMES = frozenset(
    {
        "preflight_rejected",
        "lock_conflict",
        "provider_unavailable_pre_dispatch",
        "dispatch_failed_confirmed",
        "verification_mismatch",
        "succeeded_verified",
        "failed_pre_dispatch",
        "failed_post_dispatch",
        "manual_review_required",
        "cancelled_pre_dispatch",
    }
)

PRE_DISPATCH_TERMINAL_OUTCOMES = frozenset(
    {
        "preflight_rejected",
        "lock_conflict",
        "provider_unavailable_pre_dispatch",
        "failed_pre_dispatch",
        "cancelled_pre_dispatch",
    }
)
POST_INTENT_TERMINAL_OUTCOMES = frozenset(
    {
        "dispatch_failed_confirmed",
        "verification_mismatch",
        "succeeded_verified",
        "failed_post_dispatch",
        "manual_review_required",
    }
)
EXECUTION_CLASS_PRE_INTENT = "pre_intent"
EXECUTION_CLASS_TERMINAL_PRE_DISPATCH = "terminal_pre_dispatch"
EXECUTION_CLASS_VERIFIED_NO_DISPATCH = "verified_no_dispatch"
EXECUTION_CLASS_POST_INTENT = "post_intent"
EXECUTION_CLASS_TERMINAL_POST_INTENT = "terminal_post_intent"

_PRE_INTENT_EVENT_TYPES = frozenset(
    {
        "execution_started",
        "execution_reclaimed",
        "locks_acquired",
        "preflight_completed",
        "pre_intent_retry_required",
    }
)
_PRE_DISPATCH_TERMINAL_EVENT_TYPES = frozenset(
    {"pre_dispatch_terminal", "execution_cancelled"}
)
_POST_INTENT_EVENT_TYPES = frozenset(
    {
        *_PRE_INTENT_EVENT_TYPES,
        "dispatch_intent_committed",
        "dispatch_result_recorded",
        "observation_recorded",
        "verification_recorded",
        "execution_claim_yielded",
        "recovery_claimed",
        "recovery_locks_transferred",
        "cancellation_rejected",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def bounded_codes(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError("diagnostic evidence must be a sequence")
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not EVIDENCE_PATTERN.fullmatch(value):
            raise ValueError("diagnostic evidence is not canonical")
        result.add(value)
    if not result or len(result) > MAX_EVIDENCE_ITEMS:
        raise ValueError("diagnostic evidence count is invalid")
    return tuple(sorted(result, key=lambda item: item.encode("utf-8")))


def bounded_diagnostics(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    result: list[str] = []
    for value in values[:MAX_DIAGNOSTIC_ITEMS]:
        if isinstance(value, str) and EVIDENCE_PATTERN.fullmatch(value):
            result.append(value[:MAX_DIAGNOSTIC_LENGTH])
    return tuple(sorted(set(result), key=lambda item: item.encode("utf-8")))


def enum_value(value: object) -> str:
    candidate = getattr(value, "value", value)
    if not isinstance(candidate, str):
        raise ValueError("enum value must be a string")
    return candidate


def validate_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
    return value


def validate_lock_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_LOCK_KEY_LENGTH
        or not LOCK_KEY_PATTERN.fullmatch(value)
        or value.count(":") != 1
    ):
        raise ValueError("lock key is not canonical")
    return value


def validate_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a lower-case SHA-256 digest")
    return value


def require_bool(value: object, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


def require_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return value


@dataclass(frozen=True)
class LockTiming:
    """Explicit bounded lease, renewal, and waiting constraints."""

    lease_seconds: float
    renewal_interval_seconds: float
    wait_timeout_seconds: float
    poll_interval_seconds: float = 0.05

    def validate(self) -> None:
        if not 30 <= self.lease_seconds <= 3600:
            raise ValueError("lease duration must be between 30 and 3600 seconds")
        if not 5 <= self.renewal_interval_seconds <= 300:
            raise ValueError("renewal interval must be between 5 and 300 seconds")
        if self.renewal_interval_seconds >= self.lease_seconds:
            raise ValueError("renewal interval must be shorter than lease duration")
        if not 0 <= self.wait_timeout_seconds <= 30:
            raise ValueError("wait timeout must be between 0 and 30 seconds")
        if not 0.01 <= self.poll_interval_seconds <= 1:
            raise ValueError("poll interval must be between 0.01 and 1 second")


@dataclass(frozen=True)
class LockOwner:
    owner_id: str
    task_id: str
    plan_id: str | None
    operation_id: str
    attempt_id: str

    def validate(self) -> None:
        validate_identifier(self.owner_id, field_name="owner_id")
        validate_identifier(self.task_id, field_name="task_id")
        if self.plan_id is not None:
            validate_identifier(self.plan_id, field_name="plan_id")
        validate_identifier(self.operation_id, field_name="operation_id")
        validate_identifier(self.attempt_id, field_name="attempt_id")


@dataclass(frozen=True)
class NormalizedLockRequest:
    key: str
    scopes: tuple[str, ...]
    mode: str
    reason_codes: tuple[str, ...]

    def validate(self) -> None:
        validate_lock_key(self.key)
        if not self.scopes or set(self.scopes) - {"resource", "provider"}:
            raise ValueError("lock scopes are invalid")
        if tuple(sorted(set(self.scopes))) != self.scopes:
            raise ValueError("lock scopes are not canonical")
        if self.mode not in {"shared", "exclusive"}:
            raise ValueError("lock mode is invalid")
        if bounded_codes(self.reason_codes) != self.reason_codes:
            raise ValueError("lock reasons are not canonical")


@dataclass(frozen=True)
class LockToken:
    key: str
    generation: int
    mode: str

    def validate(self) -> None:
        validate_lock_key(self.key)
        if self.generation < 1:
            raise ValueError("lock generation is invalid")
        if self.mode not in {"shared", "exclusive"}:
            raise ValueError("lock token mode is invalid")


@dataclass(frozen=True)
class LockHandle:
    owner: LockOwner
    tokens: tuple[LockToken, ...]
    acquired_at: str
    lease_expires_at: str
    timing: LockTiming

    def validate(self) -> None:
        self.owner.validate()
        self.timing.validate()
        if not self.tokens:
            raise ValueError("lock handle is empty")
        keys = tuple(token.key for token in self.tokens)
        if keys != tuple(sorted(keys, key=lambda item: item.encode("utf-8"))):
            raise ValueError("lock tokens are not bytewise sorted")
        if len(keys) != len(set(keys)):
            raise ValueError("lock handle contains duplicate keys")
        for token in self.tokens:
            token.validate()
        acquired = parse_timestamp(self.acquired_at, field_name="acquired_at")
        expires = parse_timestamp(
            self.lease_expires_at, field_name="lease_expires_at"
        )
        if expires <= acquired:
            raise ValueError("lock lease expiration is invalid")


@dataclass
class LockRecord:
    schema_version: int
    key: str
    scopes: tuple[str, ...]
    mode: str
    owner_id: str
    task_id: str
    plan_id: str | None
    operation_id: str
    attempt_id: str
    acquired_at: str
    lease_expires_at: str
    last_renewed_at: str
    generation: int
    evidence_references: tuple[str, ...]
    conflict_hold: bool = False

    def validate(self) -> None:
        if self.schema_version != LOCK_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported lock-record schema")
        NormalizedLockRequest(
            key=self.key,
            scopes=self.scopes,
            mode=self.mode,
            reason_codes=self.evidence_references,
        ).validate()
        LockOwner(
            owner_id=self.owner_id,
            task_id=self.task_id,
            plan_id=self.plan_id,
            operation_id=self.operation_id,
            attempt_id=self.attempt_id,
        ).validate()
        if self.generation < 1:
            raise ValueError("lock generation is invalid")
        if type(self.conflict_hold) is not bool:
            raise ValueError("lock conflict hold must be a boolean")
        acquired = parse_timestamp(self.acquired_at, field_name="acquired_at")
        renewed = parse_timestamp(self.last_renewed_at, field_name="last_renewed_at")
        expires = parse_timestamp(self.lease_expires_at, field_name="lease_expires_at")
        if renewed < acquired or expires <= renewed:
            raise ValueError("lock record timestamps are contradictory")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "scopes": list(self.scopes),
            "mode": self.mode,
            "owner_id": self.owner_id,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "operation_id": self.operation_id,
            "attempt_id": self.attempt_id,
            "acquired_at": self.acquired_at,
            "lease_expires_at": self.lease_expires_at,
            "last_renewed_at": self.last_renewed_at,
            "generation": self.generation,
            "evidence_references": list(self.evidence_references),
            "conflict_hold": self.conflict_hold,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LockRecord":
        if not isinstance(value, dict):
            raise ValueError("lock record must be an object")
        allowed = {
            "schema_version", "key", "scopes", "mode", "owner_id", "task_id",
            "plan_id", "operation_id", "attempt_id", "acquired_at",
            "lease_expires_at", "last_renewed_at", "generation",
            "evidence_references", "conflict_hold",
        }
        if set(value) != allowed:
            raise ValueError("lock record fields are invalid")
        record = cls(
            schema_version=require_int(
                value["schema_version"], field_name="schema_version"
            ),
            key=str(value["key"]),
            scopes=tuple(str(item) for item in value["scopes"]),
            mode=str(value["mode"]),
            owner_id=str(value["owner_id"]),
            task_id=str(value["task_id"]),
            plan_id=str(value["plan_id"]) if value["plan_id"] is not None else None,
            operation_id=str(value["operation_id"]),
            attempt_id=str(value["attempt_id"]),
            acquired_at=str(value["acquired_at"]),
            lease_expires_at=str(value["lease_expires_at"]),
            last_renewed_at=str(value["last_renewed_at"]),
            generation=require_int(value["generation"], field_name="generation"),
            evidence_references=tuple(str(item) for item in value["evidence_references"]),
            conflict_hold=require_bool(
                value["conflict_hold"], field_name="conflict_hold"
            ),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExecutionIdentity:
    task_id: str
    plan_id: str | None
    attempt_id: str
    request_id: str
    owner_id: str

    def validate(self) -> None:
        validate_identifier(self.task_id, field_name="task_id")
        if self.plan_id is not None:
            validate_identifier(self.plan_id, field_name="plan_id")
        validate_identifier(self.attempt_id, field_name="attempt_id")
        validate_identifier(self.request_id, field_name="request_id")
        validate_identifier(self.owner_id, field_name="owner_id")


@dataclass(frozen=True)
class ExecutorTiming:
    post_dispatch_evidence_seconds: float
    claim_lease_seconds: float
    max_observation_attempts: int
    max_verification_attempts: int

    def validate(self) -> None:
        if not 30 <= self.post_dispatch_evidence_seconds <= 86400:
            raise ValueError("post-dispatch evidence deadline is invalid")
        if not 30 <= self.claim_lease_seconds <= 3600:
            raise ValueError("execution claim lease is invalid")
        if not 1 <= self.max_observation_attempts <= 32:
            raise ValueError("observation attempt bound is invalid")
        if not 1 <= self.max_verification_attempts <= 32:
            raise ValueError("verification attempt bound is invalid")


@dataclass(frozen=True)
class ExecutorResult:
    task_id: str
    attempt_id: str
    outcome: str
    task_state: str
    terminal: bool
    dispatch_intent_recorded: bool
    dispatch_count: int
    provider_response_received: bool
    observation_required: bool
    redispatch_prohibited: bool
    lock_keys: tuple[str, ...]
    diagnostic_codes: tuple[str, ...] = ()
    duplicate_execution: bool = False

    def validate(self) -> None:
        validate_identifier(self.task_id, field_name="task_id")
        validate_identifier(self.attempt_id, field_name="attempt_id")
        if self.outcome not in NORMALIZED_OUTCOME_TO_TASK_STATE:
            raise ValueError("normalized outcome is invalid")
        if self.task_state != NORMALIZED_OUTCOME_TO_TASK_STATE[self.outcome]:
            raise ValueError("task-state projection is invalid")
        if self.dispatch_count not in {0, 1}:
            raise ValueError("dispatch count exceeds the F3 contract")
        for key in self.lock_keys:
            validate_lock_key(key)


@dataclass
class ExecutionRecord:
    schema_version: int
    identity: dict[str, Any]
    adapter_model: str
    adapter_id: str
    operation: str
    target: dict[str, str]
    prepared_operation_hash: str
    state: str
    normalized_outcome: str | None
    task_state: str
    terminal: bool
    created_at: str
    updated_at: str
    claim_generation: int
    claim_expires_at: str
    lock_tokens: list[dict[str, Any]] = field(default_factory=list)
    preflight_completed: bool = False
    dispatch_intent: dict[str, Any] | None = None
    dispatch_count: int = 0
    provider_response_received: bool = False
    observation_attempts: int = 0
    verification_attempts: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def execution_identity(self) -> ExecutionIdentity:
        return ExecutionIdentity(
            task_id=self.identity["task_id"],
            plan_id=self.identity["plan_id"],
            attempt_id=self.identity["attempt_id"],
            request_id=self.identity["request_id"],
            owner_id=self.identity["owner_id"],
        )

    def validate(self) -> None:
        if self.schema_version != EXECUTION_RECORD_SCHEMA_VERSION:
            raise ValueError("unsupported execution-record schema")
        identity = self.execution_identity()
        identity.validate()
        if self.adapter_model != F3_ADAPTER_CONTRACT_MODEL:
            raise ValueError("execution adapter model is invalid")
        validate_identifier(self.adapter_id, field_name="adapter_id")
        validate_identifier(self.operation, field_name="operation")
        if set(self.target) != {"target_type", "target_id"}:
            raise ValueError("execution target is invalid")
        validate_identifier(self.target["target_type"], field_name="target_type")
        validate_identifier(self.target["target_id"], field_name="target_id")
        validate_sha256(self.prepared_operation_hash, field_name="prepared_operation_hash")
        if set(self.identity) != {
            "task_id", "plan_id", "attempt_id", "request_id", "owner_id"
        }:
            raise ValueError("execution identity fields are invalid")
        allowed_states = {
            "planning", "preflight", "dispatching", "observation", "terminal"
        }
        if self.state not in allowed_states:
            raise ValueError("execution phase state is invalid")
        allowed_task_states = {
            "created",
            "preflight",
            "dispatching",
            "observing",
            "verifying",
            "succeeded_verified",
            "failed_pre_dispatch",
            "failed_post_dispatch",
            "manual_review_required",
            "cancelled_pre_dispatch",
        }
        if self.task_state not in allowed_task_states:
            raise ValueError("execution task state is invalid")
        if type(self.terminal) is not bool:
            raise ValueError("execution terminal flag must be a boolean")
        if type(self.preflight_completed) is not bool:
            raise ValueError("preflight flag must be a boolean")
        if type(self.provider_response_received) is not bool:
            raise ValueError("provider-response flag must be a boolean")
        if self.normalized_outcome is not None:
            if self.normalized_outcome not in NORMALIZED_OUTCOME_TO_TASK_STATE:
                raise ValueError("execution outcome is invalid")
            if self.terminal and self.task_state != NORMALIZED_OUTCOME_TO_TASK_STATE[
                self.normalized_outcome
            ]:
                raise ValueError("terminal task-state projection is invalid")
        elif self.terminal:
            raise ValueError("terminal execution record is missing an outcome")
        if type(self.dispatch_count) is not int or self.dispatch_count not in {0, 1}:
            raise ValueError("execution record has multiple dispatches")
        self._validate_lock_tokens(self.lock_tokens)
        if self.dispatch_intent is not None:
            required = {
                "committed_at", "evidence_deadline", "request_id", "provider_operation",
                "provider_arguments_hash", "lock_tokens", "possibly_dispatched",
            }
            if set(self.dispatch_intent) != required:
                raise ValueError("dispatch intent fields are invalid")
            parse_timestamp(self.dispatch_intent["committed_at"], field_name="committed_at")
            parse_timestamp(self.dispatch_intent["evidence_deadline"], field_name="evidence_deadline")
            validate_sha256(
                self.dispatch_intent["provider_arguments_hash"],
                field_name="provider_arguments_hash",
            )
            validate_identifier(
                self.dispatch_intent["request_id"], field_name="request_id"
            )
            validate_identifier(
                self.dispatch_intent["provider_operation"],
                field_name="provider_operation",
            )
            self._validate_lock_tokens(self.dispatch_intent["lock_tokens"])
            if self.dispatch_intent["lock_tokens"] != self.lock_tokens:
                raise ValueError("durable intent lock tokens are inconsistent")
            if self.dispatch_intent["possibly_dispatched"] is not True:
                raise ValueError("durable intent must be possibly dispatched")
        parse_timestamp(self.created_at, field_name="created_at")
        parse_timestamp(self.updated_at, field_name="updated_at")
        parse_timestamp(self.claim_expires_at, field_name="claim_expires_at")
        if type(self.claim_generation) is not int or self.claim_generation < 1:
            raise ValueError("execution claim generation is invalid")
        if type(self.observation_attempts) is not int or not 0 <= self.observation_attempts <= 32:
            raise ValueError("observation attempts are invalid")
        if type(self.verification_attempts) is not int or not 0 <= self.verification_attempts <= 32:
            raise ValueError("verification attempts are invalid")
        if not isinstance(self.evidence, dict):
            raise ValueError("execution evidence is invalid")
        allowed_evidence = {
            "evidence_hash", "resulting_state_fingerprint", "mismatch_fields",
            "manual_review_reason_code",
        }
        if set(self.evidence) - allowed_evidence:
            raise ValueError("execution evidence fields are invalid")
        for key, value in self.evidence.items():
            if key in {"evidence_hash", "resulting_state_fingerprint"}:
                validate_sha256(value, field_name=key)
            elif key == "mismatch_fields":
                if list(bounded_diagnostics(value)) != value:
                    raise ValueError("execution mismatch fields are invalid")
            elif bounded_diagnostics((value,)) != (value,):
                raise ValueError("manual-review evidence is invalid")
        if len(self.events) > MAX_EXECUTION_EVENTS:
            raise ValueError("execution event bound exceeded")
        for expected_sequence, event in enumerate(self.events, start=1):
            if not isinstance(event, dict) or set(event) != {
                "sequence", "event_type", "occurred_at", "diagnostic_codes"
            }:
                raise ValueError("execution event fields are invalid")
            if require_int(event["sequence"], field_name="event sequence") != expected_sequence:
                raise ValueError("execution event sequence is invalid")
            if not isinstance(event["event_type"], str) or not EVIDENCE_PATTERN.fullmatch(
                event["event_type"]
            ):
                raise ValueError("execution event type is invalid")
            parse_timestamp(event["occurred_at"], field_name="event occurred_at")
            if list(bounded_diagnostics(event["diagnostic_codes"])) != event[
                "diagnostic_codes"
            ]:
                raise ValueError("execution event diagnostics are invalid")
        self.execution_class()

    def execution_class(self) -> str:
        """Return the closed persisted dispatch class or fail on contradiction.

        The class is derived exclusively from fields written by the durable
        execution repository.  Callers may use it for scheduling and lock
        disposition, but it never grants recovery or dispatch authority.
        """

        event_types = tuple(item["event_type"] for item in self.events)
        event_type_set = set(event_types)
        if self.dispatch_intent is None:
            if (
                self.dispatch_count != 0
                or self.provider_response_received
                or self.observation_attempts != 0
                or self.verification_attempts != 0
            ):
                raise ValueError(
                    "no-intent execution contains post-dispatch evidence"
                )
            if self.terminal:
                if self.state != "terminal":
                    raise ValueError(
                        "terminal no-intent execution has invalid state"
                    )
                if self.normalized_outcome == "succeeded_verified":
                    if (
                        self.task_state != "succeeded_verified"
                        or not self.preflight_completed
                        or set(self.evidence)
                        != {
                            "evidence_hash",
                            "resulting_state_fingerprint",
                        }
                        or "preflight_noop_verified" not in event_type_set
                        or not event_type_set.issubset(
                            _PRE_INTENT_EVENT_TYPES
                            | {"preflight_noop_verified"}
                        )
                    ):
                        raise ValueError(
                            "verified no-dispatch execution lacks exact "
                            "preflight proof"
                        )
                    return EXECUTION_CLASS_VERIFIED_NO_DISPATCH
                terminal_events = (
                    event_type_set & _PRE_DISPATCH_TERMINAL_EVENT_TYPES
                )
                if (
                    self.normalized_outcome
                    not in PRE_DISPATCH_TERMINAL_OUTCOMES
                    or self.evidence
                    or not event_type_set.issubset(
                        _PRE_INTENT_EVENT_TYPES
                        | _PRE_DISPATCH_TERMINAL_EVENT_TYPES
                    )
                    or len(terminal_events) != 1
                    or (
                        self.normalized_outcome != "cancelled_pre_dispatch"
                        and "execution_cancelled" in event_type_set
                    )
                ):
                    raise ValueError(
                        "terminal no-intent execution class is invalid"
                    )
                return EXECUTION_CLASS_TERMINAL_PRE_DISPATCH
            if (
                self.normalized_outcome is not None
                or self.evidence
                or (self.state, self.task_state)
                not in {
                    ("planning", "created"),
                    ("planning", "preflight"),
                    ("preflight", "preflight"),
                }
                or not event_type_set.issubset(_PRE_INTENT_EVENT_TYPES)
            ):
                raise ValueError("nonterminal pre-intent execution is invalid")
            return EXECUTION_CLASS_PRE_INTENT

        if (
            self.dispatch_count != 1
            or not self.preflight_completed
            or "dispatch_intent_committed" not in event_type_set
            or not event_type_set.issubset(_POST_INTENT_EVENT_TYPES)
            or event_types.count("observation_recorded")
            != self.observation_attempts
            or event_types.count("verification_recorded")
            != self.verification_attempts
            or (
                self.provider_response_received
                and "dispatch_result_recorded" not in event_type_set
            )
        ):
            raise ValueError("post-intent execution evidence is invalid")
        if self.terminal:
            if (
                self.state != "terminal"
                or self.normalized_outcome
                not in POST_INTENT_TERMINAL_OUTCOMES
                or (
                    self.normalized_outcome == "dispatch_failed_confirmed"
                    and (
                        self.observation_attempts != 0
                        or self.verification_attempts != 0
                        or self.evidence
                    )
                )
                or (
                    self.normalized_outcome != "dispatch_failed_confirmed"
                    and self.verification_attempts == 0
                )
            ):
                raise ValueError("terminal post-intent execution is invalid")
            return EXECUTION_CLASS_TERMINAL_POST_INTENT
        expected_nonterminal = {
            None: ("dispatching", "dispatching"),
            "dispatch_indeterminate": ("observation", "observing"),
            "observing": ("observation", "observing"),
            "verification_mismatch": (
                "observation",
                "failed_post_dispatch",
            ),
            "manual_review_required": (
                "observation",
                "manual_review_required",
            ),
        }
        if expected_nonterminal.get(self.normalized_outcome) != (
            self.state,
            self.task_state,
        ):
            raise ValueError("nonterminal post-intent execution is invalid")
        return EXECUTION_CLASS_POST_INTENT

    @staticmethod
    def _validate_lock_tokens(value: object) -> None:
        if not isinstance(value, list) or len(value) > MAX_EVIDENCE_ITEMS:
            raise ValueError("execution lock tokens are invalid")
        keys: list[str] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "key", "generation", "mode", "owner_id"
            }:
                raise ValueError("execution lock-token fields are invalid")
            token = LockToken(
                key=item["key"],
                generation=require_int(
                    item["generation"], field_name="lock generation"
                ),
                mode=item["mode"],
            )
            token.validate()
            validate_identifier(item["owner_id"], field_name="owner_id")
            keys.append(token.key)
        if keys != sorted(keys, key=lambda item: item.encode("utf-8")):
            raise ValueError("execution lock tokens are not bytewise sorted")
        if len(keys) != len(set(keys)):
            raise ValueError("execution lock tokens are duplicated")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "identity": dict(self.identity),
            "adapter_model": self.adapter_model,
            "adapter_id": self.adapter_id,
            "operation": self.operation,
            "target": dict(self.target),
            "prepared_operation_hash": self.prepared_operation_hash,
            "state": self.state,
            "normalized_outcome": self.normalized_outcome,
            "task_state": self.task_state,
            "terminal": self.terminal,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "claim_generation": self.claim_generation,
            "claim_expires_at": self.claim_expires_at,
            "lock_tokens": [dict(item) for item in self.lock_tokens],
            "preflight_completed": self.preflight_completed,
            "dispatch_intent": dict(self.dispatch_intent) if self.dispatch_intent else None,
            "dispatch_count": self.dispatch_count,
            "provider_response_received": self.provider_response_received,
            "observation_attempts": self.observation_attempts,
            "verification_attempts": self.verification_attempts,
            "evidence": dict(self.evidence),
            "events": [dict(item) for item in self.events],
        }

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionRecord":
        if not isinstance(value, dict):
            raise ValueError("execution record must be an object")
        allowed = {
            "schema_version", "identity", "adapter_model", "adapter_id", "operation",
            "target", "prepared_operation_hash", "state", "normalized_outcome",
            "task_state", "terminal", "created_at", "updated_at", "claim_generation",
            "claim_expires_at", "lock_tokens", "preflight_completed", "dispatch_intent",
            "dispatch_count", "provider_response_received", "observation_attempts",
            "verification_attempts", "evidence", "events",
        }
        if set(value) != allowed:
            raise ValueError("execution record fields are invalid")
        record = cls(
            schema_version=require_int(
                value["schema_version"], field_name="schema_version"
            ),
            identity=dict(value["identity"]),
            adapter_model=value["adapter_model"],
            adapter_id=value["adapter_id"],
            operation=value["operation"],
            target=dict(value["target"]),
            prepared_operation_hash=value["prepared_operation_hash"],
            state=value["state"],
            normalized_outcome=(
                value["normalized_outcome"]
                if value["normalized_outcome"] is not None
                else None
            ),
            task_state=value["task_state"],
            terminal=require_bool(value["terminal"], field_name="terminal"),
            created_at=value["created_at"],
            updated_at=value["updated_at"],
            claim_generation=require_int(
                value["claim_generation"], field_name="claim_generation"
            ),
            claim_expires_at=value["claim_expires_at"],
            lock_tokens=[dict(item) for item in value["lock_tokens"]],
            preflight_completed=require_bool(
                value["preflight_completed"], field_name="preflight_completed"
            ),
            dispatch_intent=(dict(value["dispatch_intent"]) if isinstance(value["dispatch_intent"], dict) else None),
            dispatch_count=require_int(
                value["dispatch_count"], field_name="dispatch_count"
            ),
            provider_response_received=require_bool(
                value["provider_response_received"],
                field_name="provider_response_received",
            ),
            observation_attempts=require_int(
                value["observation_attempts"],
                field_name="observation_attempts",
            ),
            verification_attempts=require_int(
                value["verification_attempts"],
                field_name="verification_attempts",
            ),
            evidence=dict(value["evidence"]),
            events=[dict(item) for item in value["events"]],
        )
        record.validate()
        return record


def append_execution_event(
    record: ExecutionRecord,
    *,
    event_type: str,
    occurred_at: str,
    diagnostic_codes: tuple[str, ...] = (),
) -> None:
    if not EVIDENCE_PATTERN.fullmatch(event_type):
        raise ValueError("execution event type is invalid")
    parse_timestamp(occurred_at, field_name="occurred_at")
    if len(record.events) >= MAX_EXECUTION_EVENTS:
        raise ValueError("execution event bound exceeded")
    record.events.append(
        {
            "sequence": len(record.events) + 1,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "diagnostic_codes": list(bounded_diagnostics(diagnostic_codes)),
        }
    )
    record.updated_at = occurred_at
