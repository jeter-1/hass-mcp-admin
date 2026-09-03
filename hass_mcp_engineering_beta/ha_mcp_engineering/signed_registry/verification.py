"""Ed25519 verification and monotonic registry-chain validation."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from .models import (
    RegistryEnvelope,
    RegistryErrorCode,
    RegistryValidationError,
    parse_utc_timestamp,
)


MAX_CLOCK_SKEW = timedelta(minutes=5)


class ValidationStatus(str, Enum):
    ACCEPTED = "accepted"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ValidationIssue:
    code: RegistryErrorCode


@dataclass(frozen=True)
class AcceptedRegistryState:
    """Minimal persisted state required for rollback and replay protection."""

    registry_id: str
    sequence: int
    content_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.registry_id, str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}",
                self.registry_id,
            )
            or isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
            or not isinstance(self.content_digest, str)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                self.content_digest,
            )
        ):
            raise RegistryValidationError(
                RegistryErrorCode.ACCEPTED_STATE_INVALID
            )

    @classmethod
    def from_envelope(
        cls,
        envelope: RegistryEnvelope,
    ) -> "AcceptedRegistryState":
        return cls(
            registry_id=envelope.registry_id,
            sequence=envelope.sequence,
            content_digest=envelope.content_digest,
        )


@dataclass(frozen=True)
class RegistryValidationResult:
    status: ValidationStatus
    content_digest: str | None
    envelope: RegistryEnvelope | None
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status in {
            ValidationStatus.ACCEPTED,
            ValidationStatus.IDEMPOTENT_REPLAY,
        }


class TrustAnchorStore:
    """Configured public Ed25519 trust anchors keyed by stable key_id."""

    def __init__(
        self,
        anchors: Mapping[str, Ed25519PublicKey],
    ) -> None:
        if not isinstance(anchors, Mapping):
            raise RegistryValidationError(
                RegistryErrorCode.TRUST_ANCHOR_INVALID
            )
        normalized: dict[str, Ed25519PublicKey] = {}
        for key_id, public_key in anchors.items():
            if (
                not isinstance(key_id, str)
                or not key_id
                or len(key_id) > 80
                or any(
                    not (
                        char.isalnum()
                        or char in "._-"
                    )
                    for char in key_id
                )
                or not isinstance(public_key, Ed25519PublicKey)
            ):
                raise RegistryValidationError(
                    RegistryErrorCode.TRUST_ANCHOR_INVALID
                )
            normalized[key_id] = public_key
        self._anchors = normalized

    @classmethod
    def from_base64(
        cls,
        anchors: Mapping[str, str],
    ) -> "TrustAnchorStore":
        parsed: dict[str, Ed25519PublicKey] = {}
        try:
            items = anchors.items()
        except AttributeError:
            raise RegistryValidationError(
                RegistryErrorCode.TRUST_ANCHOR_INVALID
            ) from None
        for key_id, encoded in items:
            if not isinstance(encoded, str):
                raise RegistryValidationError(
                    RegistryErrorCode.TRUST_ANCHOR_INVALID
                )
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise RegistryValidationError(
                    RegistryErrorCode.TRUST_ANCHOR_INVALID
                ) from None
            if (
                len(raw) != 32
                or base64.b64encode(raw).decode("ascii") != encoded
            ):
                raise RegistryValidationError(
                    RegistryErrorCode.TRUST_ANCHOR_INVALID
                )
            try:
                parsed[key_id] = Ed25519PublicKey.from_public_bytes(
                    raw
                )
            except (TypeError, ValueError):
                raise RegistryValidationError(
                    RegistryErrorCode.TRUST_ANCHOR_INVALID
                ) from None
        return cls(parsed)

    def lookup(self, key_id: str) -> Ed25519PublicKey | None:
        return self._anchors.get(key_id)


def parse_verified_registry_envelope(
    raw: bytes,
    *,
    trust_anchors: TrustAnchorStore,
) -> RegistryEnvelope:
    """Parse and authenticate an envelope without granting positive authority.

    Operational cache loading uses this boundary to retain signed revocations
    after positive authority expires. Callers must separately validate time,
    registry identity, sequence, and chain before using an envelope to admit
    any capability.
    """

    envelope = RegistryEnvelope.from_bytes(raw)
    _verify_signature(envelope, trust_anchors)
    return envelope


def validate_registry_envelope(
    raw: bytes,
    *,
    trust_anchors: TrustAnchorStore,
    now: datetime,
    accepted_state: AcceptedRegistryState | None = None,
    max_clock_skew: timedelta = MAX_CLOCK_SKEW,
) -> RegistryValidationResult:
    """Verify one registry and return deterministic bounded validation data."""

    envelope: RegistryEnvelope | None = None
    digest: str | None = None
    try:
        envelope = RegistryEnvelope.from_bytes(raw)
        digest = envelope.content_digest
        _verify_signature(envelope, trust_anchors)
        _validate_time(
            envelope,
            now=now,
            max_clock_skew=max_clock_skew,
        )
        status = _validate_chain(
            envelope,
            content_digest=digest,
            accepted_state=accepted_state,
        )
        return RegistryValidationResult(
            status=status,
            content_digest=digest,
            envelope=envelope,
        )
    except RegistryValidationError as exc:
        return RegistryValidationResult(
            status=ValidationStatus.REJECTED,
            content_digest=None,
            envelope=None,
            issues=(ValidationIssue(exc.code),),
        )


def _verify_signature(
    envelope: RegistryEnvelope,
    trust_anchors: TrustAnchorStore,
) -> None:
    public_key = trust_anchors.lookup(envelope.key_id)
    if public_key is None:
        raise RegistryValidationError(RegistryErrorCode.UNKNOWN_KEY)
    try:
        public_key.verify(
            envelope.signature_bytes,
            envelope.canonical_unsigned,
        )
    except InvalidSignature:
        raise RegistryValidationError(
            RegistryErrorCode.INVALID_SIGNATURE
        ) from None


def _validate_time(
    envelope: RegistryEnvelope,
    *,
    now: datetime,
    max_clock_skew: timedelta,
) -> None:
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or not isinstance(max_clock_skew, timedelta)
        or max_clock_skew < timedelta(0)
    ):
        raise RegistryValidationError(RegistryErrorCode.CLOCK_INVALID)
    now_utc = now.astimezone(timezone.utc)
    generated = parse_utc_timestamp(envelope.generated_at)
    expires = parse_utc_timestamp(envelope.expires_at)
    if generated - now_utc > max_clock_skew:
        raise RegistryValidationError(
            RegistryErrorCode.GENERATED_IN_FUTURE
        )
    if now_utc >= expires:
        raise RegistryValidationError(RegistryErrorCode.EXPIRED)


def _validate_chain(
    envelope: RegistryEnvelope,
    *,
    content_digest: str,
    accepted_state: AcceptedRegistryState | None,
) -> ValidationStatus:
    if accepted_state is None:
        if (
            envelope.sequence != 1
            or envelope.previous_registry_sha256 is not None
        ):
            raise RegistryValidationError(
                RegistryErrorCode.INITIAL_CHAIN_INVALID
            )
        return ValidationStatus.ACCEPTED
    if envelope.registry_id != accepted_state.registry_id:
        raise RegistryValidationError(
            RegistryErrorCode.REGISTRY_ID_MISMATCH
        )
    if envelope.sequence < accepted_state.sequence:
        raise RegistryValidationError(RegistryErrorCode.ROLLBACK)
    if envelope.sequence == accepted_state.sequence:
        if content_digest == accepted_state.content_digest:
            return ValidationStatus.IDEMPOTENT_REPLAY
        raise RegistryValidationError(
            RegistryErrorCode.REPLAY_CONFLICT
        )
    if (
        envelope.previous_registry_sha256
        != accepted_state.content_digest
    ):
        raise RegistryValidationError(
            RegistryErrorCode.PREVIOUS_DIGEST_MISMATCH
        )
    return ValidationStatus.ACCEPTED
