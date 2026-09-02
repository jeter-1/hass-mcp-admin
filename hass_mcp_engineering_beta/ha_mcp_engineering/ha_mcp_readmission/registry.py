"""Fixed-location ADR-009 registry retrieval and durable accepted-state cache."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Awaitable, Callable

import aiohttp
from cryptography.exceptions import InvalidSignature

from ..signed_registry import (
    AcceptedRegistryState,
    RegistryEnvelope,
    RegistryErrorCode,
    RegistryValidationError,
    ReleaseRevocation,
    ReviewedReleaseEntry,
    TrustAnchorStore,
    canonical_json,
    parse_verified_registry_envelope,
    sha256_digest,
    validate_registry_envelope,
)
from ..signed_registry.models import MAX_REVOCATIONS, parse_utc_timestamp
from .models import MAX_SAFE_INTEGER


REGISTRY_ID = "ha-mcp-reviewed-releases"
TRUST_ANCHOR_KEY_ID = "ha-mcp-release-registry-v1"
REGISTRY_URL = (
    "https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/"
    "main/upstream-trust/ha-mcp-release-registry.json"
)
CACHE_PATH = Path("/data/ha-mcp-release-registry-cache.json")
CONNECT_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 15.0
REFRESH_INTERVAL_SECONDS = 21_600.0
MISSING_RELEASE_REFRESH_INTERVAL_SECONDS = 60.0
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_REVOCATION_SOURCE_ENVELOPES = 8
MAX_AUTHORITY_CHAIN_ENVELOPES = 64
MAX_DENIAL_JOURNALS = 64
MAX_MISSING_RELEASE_TRACKED = 16
MAX_FAILURE_REASONS = 32
JOURNAL_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 3
PENDING_SCHEMA_VERSION = 3
LIFECYCLE_WITNESS_SCHEMA_VERSION = 1
LIFECYCLE_WITNESS_MAX_BYTES = 512

_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NO_SIGNED_AUTHORITY_SHA256 = sha256_digest(
    {"state": "no_signed_authority"}
)


class ReleaseRegistryOperationalError(RuntimeError):
    """Bounded operational registry failure without raw remote content."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ReleaseRegistryAuthority:
    """Authenticated registry evidence available to the ha-mcp selector."""

    envelope: RegistryEnvelope | None
    positive_authority_current: bool
    revocations: tuple[ReleaseRevocation, ...]
    sequence: int | None
    content_digest: str | None
    surface_denied: bool = False

    def entry_for(
        self,
        server_name: str,
        version: str,
    ) -> ReviewedReleaseEntry | None:
        if not self.positive_authority_current or self.envelope is None:
            return None
        return next(
            (
                entry
                for entry in self.envelope.entries
                if entry.server_name == server_name
                and entry.version == version
            ),
            None,
        )

    def revoked(self, server_name: str, version: str) -> bool:
        return any(
            item.server_name == server_name and item.version == version
            for item in self.revocations
        )


Fetcher = Callable[[str, int], Awaitable[bytes]]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SignedRegistryJournal:
    """Authenticated bounded chain plus explicit compaction checkpoint."""

    registry_id: str
    key_id: str
    checkpoint_sequence: int
    checkpoint_previous_registry_sha256: str | None
    envelopes: tuple[RegistryEnvelope, ...]
    revocation_sources: tuple[RegistryEnvelope, ...]
    signature: str

    @property
    def accepted(self) -> RegistryEnvelope:
        return self.envelopes[-1]

    def unsigned_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "registry_id": self.registry_id,
            "key_id": self.key_id,
            "checkpoint_sequence": self.checkpoint_sequence,
            "checkpoint_previous_registry_sha256": (
                self.checkpoint_previous_registry_sha256
            ),
            "envelopes": [item.to_mapping() for item in self.envelopes],
            "revocation_sources": [
                item.to_mapping() for item in self.revocation_sources
            ],
        }

    def to_mapping(self) -> dict[str, Any]:
        return {**self.unsigned_mapping(), "signature": self.signature}

    @property
    def content_digest(self) -> str:
        return sha256_digest(self.unsigned_mapping())


def _parse_signed_journal(
    raw: bytes,
    *,
    trust_anchors: TrustAnchorStore,
) -> SignedRegistryJournal:
    if not isinstance(raw, bytes) or len(raw) > MAX_CACHE_BYTES:
        raise ReleaseRegistryOperationalError(
            "registry_journal_oversized"
        )
    value = _strict_json_document(raw, "registry_journal_invalid")
    fields = {
        "schema_version",
        "registry_id",
        "key_id",
        "checkpoint_sequence",
        "checkpoint_previous_registry_sha256",
        "envelopes",
        "revocation_sources",
        "signature",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ReleaseRegistryOperationalError("registry_journal_invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != JOURNAL_SCHEMA_VERSION
    ):
        raise ReleaseRegistryOperationalError(
            "registry_journal_schema_unsupported"
        )
    if value["registry_id"] != REGISTRY_ID:
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.REGISTRY_ID_MISMATCH.value
        )
    if value["key_id"] != TRUST_ANCHOR_KEY_ID:
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.UNKNOWN_KEY.value
        )
    raw_signature = value["signature"]
    if not isinstance(raw_signature, str):
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.SIGNATURE_ENCODING_INVALID.value
        )
    try:
        signature = base64.b64decode(raw_signature, validate=True)
    except (binascii.Error, ValueError):
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.SIGNATURE_ENCODING_INVALID.value
        ) from None
    if (
        len(signature) != 64
        or base64.b64encode(signature).decode("ascii") != raw_signature
    ):
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.SIGNATURE_LENGTH_INVALID.value
        )
    public_key = trust_anchors.lookup(value["key_id"])
    if public_key is None:
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.UNKNOWN_KEY.value
        )
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    try:
        public_key.verify(signature, canonical_json(unsigned))
    except InvalidSignature:
        raise ReleaseRegistryOperationalError(
            RegistryErrorCode.INVALID_SIGNATURE.value
        ) from None
    except Exception:
        raise ReleaseRegistryOperationalError(
            "registry_journal_invalid"
        ) from None

    checkpoint_sequence = value["checkpoint_sequence"]
    checkpoint_previous = value[
        "checkpoint_previous_registry_sha256"
    ]
    if (
        isinstance(checkpoint_sequence, bool)
        or not isinstance(checkpoint_sequence, int)
        or not 1 <= checkpoint_sequence <= MAX_SAFE_INTEGER
        or (
            checkpoint_previous is not None
            and (
                not isinstance(checkpoint_previous, str)
                or not _SHA256_DIGEST.fullmatch(checkpoint_previous)
            )
        )
        or (checkpoint_sequence == 1) != (checkpoint_previous is None)
    ):
        raise ReleaseRegistryOperationalError("registry_checkpoint_invalid")

    raw_envelopes = value["envelopes"]
    if (
        not isinstance(raw_envelopes, list)
        or not raw_envelopes
        or len(raw_envelopes) > MAX_AUTHORITY_CHAIN_ENVELOPES
    ):
        raise ReleaseRegistryOperationalError(
            "registry_journal_capacity_exhausted"
        )
    envelopes = tuple(
        parse_verified_registry_envelope(
            canonical_json(item), trust_anchors=trust_anchors
        )
        for item in raw_envelopes
    )
    _validate_journal_envelopes(
        envelopes,
        checkpoint_sequence=checkpoint_sequence,
        checkpoint_previous=checkpoint_previous,
    )

    raw_sources = value["revocation_sources"]
    if (
        not isinstance(raw_sources, list)
        or len(raw_sources) > MAX_REVOCATION_SOURCE_ENVELOPES
    ):
        raise ReleaseRegistryOperationalError(
            "registry_revocation_history_capacity_exhausted"
        )
    sources = tuple(
        parse_verified_registry_envelope(
            canonical_json(item), trust_anchors=trust_anchors
        )
        for item in raw_sources
    )
    source_digests: set[str] = set()
    envelope_by_sequence = {
        item.sequence: item.content_digest for item in envelopes
    }
    source_by_sequence: dict[int, str] = {}
    envelope_digests = set(envelope_by_sequence.values())
    for source in sources:
        _require_journal_envelope(source)
        if (
            not source.revocations
            or source.sequence > envelopes[-1].sequence
            or source.content_digest in source_digests
            or source.content_digest in envelope_digests
            or (
                source.sequence in envelope_by_sequence
                and envelope_by_sequence[source.sequence]
                != source.content_digest
            )
            or (
                source.sequence in source_by_sequence
                and source_by_sequence[source.sequence]
                != source.content_digest
            )
        ):
            raise ReleaseRegistryOperationalError(
                "registry_journal_invalid"
            )
        source_digests.add(source.content_digest)
        source_by_sequence[source.sequence] = source.content_digest
    return SignedRegistryJournal(
        registry_id=REGISTRY_ID,
        key_id=TRUST_ANCHOR_KEY_ID,
        checkpoint_sequence=checkpoint_sequence,
        checkpoint_previous_registry_sha256=checkpoint_previous,
        envelopes=envelopes,
        revocation_sources=sources,
        signature=raw_signature,
    )


def _require_journal_envelope(envelope: RegistryEnvelope) -> None:
    if (
        envelope.registry_id != REGISTRY_ID
        or envelope.key_id != TRUST_ANCHOR_KEY_ID
    ):
        raise ReleaseRegistryOperationalError("registry_journal_invalid")


def _validate_journal_envelopes(
    envelopes: tuple[RegistryEnvelope, ...],
    *,
    checkpoint_sequence: int,
    checkpoint_previous: str | None,
) -> None:
    first = envelopes[0]
    if (
        first.sequence != checkpoint_sequence
        or first.previous_registry_sha256 != checkpoint_previous
    ):
        raise ReleaseRegistryOperationalError("registry_checkpoint_invalid")
    sequences: set[int] = set()
    digests: set[str] = set()
    previous: RegistryEnvelope | None = None
    for envelope in envelopes:
        _require_journal_envelope(envelope)
        if (
            envelope.sequence in sequences
            or envelope.content_digest in digests
        ):
            raise ReleaseRegistryOperationalError(
                "registry_journal_duplicate_envelope"
            )
        sequences.add(envelope.sequence)
        digests.add(envelope.content_digest)
        if previous is not None and (
            envelope.sequence != previous.sequence + 1
            or envelope.previous_registry_sha256
            != previous.content_digest
            or parse_utc_timestamp(envelope.generated_at)
            < parse_utc_timestamp(previous.generated_at)
        ):
            raise ReleaseRegistryOperationalError(
                "registry_journal_disconnected"
            )
        previous = envelope


class SignedReleaseRegistry:
    """Verify and cache one fixed repository-owned signed release registry."""

    def __init__(
        self,
        *,
        enabled: bool,
        public_key: str,
        cache_path: Path = CACHE_PATH,
        fetcher: Fetcher | None = None,
        now: Clock | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._anchors = (
            TrustAnchorStore.from_base64(
                {TRUST_ANCHOR_KEY_ID: public_key}
            )
            if self._enabled
            else TrustAnchorStore({})
        )
        self._cache_path = cache_path
        self._fetcher = fetcher or self._fetch_bytes
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._accepted: RegistryEnvelope | None = None
        self._authority_journal: SignedRegistryJournal | None = None
        self._authority_chain: tuple[RegistryEnvelope, ...] = ()
        self._revocation_sources: tuple[RegistryEnvelope, ...] = ()
        self._volatile_accepted: RegistryEnvelope | None = None
        self._volatile_journal: SignedRegistryJournal | None = None
        self._volatile_denial_journals: tuple[
            SignedRegistryJournal, ...
        ] = ()
        self._volatile_revocations: tuple[ReleaseRevocation, ...] = ()
        self._volatile_revocation_overflow = False
        self._retired_cache_incomplete = False
        self._surface_denied = False
        self._last_refresh_monotonic: float | None = None
        self._missing_release_refreshes: dict[
            tuple[str, str], float
        ] = {}
        self._last_refresh_status = (
            "not_attempted" if self._enabled else "disabled"
        )
        self._last_failure_reason: str | None = None
        self._failure_counts: Counter[str] = Counter()
        self._cache_status = "not_loaded"
        if self._enabled:
            self._load_cache()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def evaluated_at(self) -> datetime:
        """Return the validated clock used for registry freshness decisions."""

        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ReleaseRegistryOperationalError("registry_clock_invalid")
        return value.astimezone(timezone.utc)

    def refresh_due(self) -> bool:
        if not self._enabled:
            return False
        if self._last_refresh_monotonic is None:
            return True
        return (
            time.monotonic() - self._last_refresh_monotonic
            >= REFRESH_INTERVAL_SECONDS
        )

    async def refresh_if_due(self, *, force: bool = False) -> bool:
        if not self._enabled or (not force and not self.refresh_due()):
            return False
        return await self.refresh()

    async def refresh_for_missing_release(
        self,
        *,
        server_name: str,
        version: str,
    ) -> bool:
        """Rate-limit one authenticated refresh for a newly observed release."""

        if not self._enabled:
            return False
        authority = self.authority()
        if (
            authority.entry_for(server_name, version) is not None
            or authority.revoked(server_name, version)
        ):
            return False
        key = self._missing_release_key(server_name, version)
        if key is None:
            return False
        now = time.monotonic()
        previous = self._missing_release_refreshes.get(key)
        if previous is not None and (
            now - previous < MISSING_RELEASE_REFRESH_INTERVAL_SECONDS
        ):
            return False
        if (
            key not in self._missing_release_refreshes
            and len(self._missing_release_refreshes)
            >= MAX_MISSING_RELEASE_TRACKED
        ):
            oldest = min(
                self._missing_release_refreshes,
                key=lambda item: self._missing_release_refreshes[item],
            )
            self._missing_release_refreshes.pop(oldest, None)
        self._missing_release_refreshes[key] = now
        return await self.refresh()

    def missing_release_retry_delay(
        self,
        *,
        server_name: str,
        version: str,
    ) -> float | None:
        """Return the bounded delay until this observed release may refetch."""

        key = self._missing_release_key(server_name, version)
        if key is None:
            return None
        previous = self._missing_release_refreshes.get(key)
        if previous is None:
            return 0.0
        return max(
            0.0,
            MISSING_RELEASE_REFRESH_INTERVAL_SECONDS
            - (time.monotonic() - previous),
        )

    async def refresh(self) -> bool:
        if not self._enabled:
            return False
        async with self._lock:
            self._last_refresh_monotonic = time.monotonic()
            self._last_refresh_status = "refreshing"
            witness_started = False
            candidate_validated = False
            try:
                if not self._surface_denied:
                    authority_digest = (
                        self._authority_journal.content_digest
                        if self._authority_journal is not None
                        else _NO_SIGNED_AUTHORITY_SHA256
                    )
                    try:
                        self._write_lifecycle_witness(
                            state="refreshing",
                            authority_journal_sha256=authority_digest,
                        )
                    except ReleaseRegistryOperationalError:
                        # No signed response may be observed unless restart
                        # can distinguish this refresh from a clean first
                        # start.  Existing compiled authority is denied for
                        # this process when even that intent is not durable.
                        self._surface_denied = True
                        raise
                    witness_started = True
                raw = await self._fetcher(REGISTRY_URL, MAX_CACHE_BYTES)
                journal = _parse_signed_journal(
                    raw,
                    trust_anchors=self._anchors,
                )
                self._require_current_tip(journal.accepted)
                status = self._validate_candidate_journal(journal)
                candidate_validated = True
                if (
                    status == "idempotent"
                    and not self._surface_denied
                ):
                    try:
                        self._write_lifecycle_witness(
                            state="committed",
                            authority_journal_sha256=(
                                self._authority_journal.content_digest
                            ),
                        )
                    except ReleaseRegistryOperationalError:
                        self._surface_denied = True
                        raise
                    self._last_refresh_status = "idempotent"
                    self._last_failure_reason = None
                    return True
                try:
                    sources = self._normalized_revocation_sources(journal)
                except ReleaseRegistryOperationalError:
                    # The candidate is already signature-, time-, and
                    # chain-verified.  A bounded retention failure must never
                    # leave older positive authority active, especially when
                    # the verified tip revokes it.  Retain the monotonic
                    # sequence barrier and independently retain older signed
                    # denial journals when the new candidate omits them.
                    overflow_was_active = (
                        self._volatile_revocation_overflow
                    )
                    self._retain_verified_denial(journal)
                    if not overflow_was_active:
                        try:
                            self._persist_denial_barrier(
                                journal,
                                denial_journals=(
                                    self._volatile_denial_journals
                                ),
                            )
                        except ReleaseRegistryOperationalError:
                            pass
                    raise
                try:
                    self._write_cache(journal)
                except ReleaseRegistryOperationalError:
                    overflow_was_active = (
                        self._volatile_revocation_overflow
                    )
                    self._retain_volatile_candidate(journal, sources)
                    if not overflow_was_active:
                        try:
                            self._persist_denial_barrier(
                                journal,
                                denial_journals=(
                                    self._volatile_denial_journals
                                ),
                            )
                        except ReleaseRegistryOperationalError:
                            pass
                    raise
                self._accepted = journal.accepted
                self._authority_journal = journal
                self._authority_chain = journal.envelopes
                self._revocation_sources = sources
                self._volatile_accepted = None
                self._volatile_journal = None
                self._volatile_denial_journals = ()
                self._volatile_revocations = ()
                self._volatile_revocation_overflow = False
                self._retired_cache_incomplete = False
                self._surface_denied = False
                self._cache_status = "valid"
                self._last_refresh_status = "accepted"
                self._last_failure_reason = None
                return True
            except ReleaseRegistryOperationalError as exc:
                self._record_failure(
                    self._restore_refresh_witness(
                        witness_started=witness_started,
                        candidate_validated=candidate_validated,
                        reason_code=exc.reason_code,
                    )
                )
                return False
            except RegistryValidationError as exc:
                self._record_failure(
                    self._restore_refresh_witness(
                        witness_started=witness_started,
                        candidate_validated=candidate_validated,
                        reason_code=exc.code.value,
                    )
                )
                return False
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                self._record_failure(
                    self._restore_refresh_witness(
                        witness_started=witness_started,
                        candidate_validated=candidate_validated,
                        reason_code="registry_unavailable",
                    )
                )
                return False
            except Exception:
                self._record_failure(
                    self._restore_refresh_witness(
                        witness_started=witness_started,
                        candidate_validated=candidate_validated,
                        reason_code="registry_invalid",
                    )
                )
                return False

    def _restore_refresh_witness(
        self,
        *,
        witness_started: bool,
        candidate_validated: bool,
        reason_code: str,
    ) -> str:
        """Restore old cache authority only before a candidate is accepted."""

        if not witness_started or candidate_validated:
            return reason_code
        try:
            self._write_lifecycle_witness(
                state="committed",
                authority_journal_sha256=(
                    self._authority_journal.content_digest
                    if self._authority_journal is not None
                    else _NO_SIGNED_AUTHORITY_SHA256
                ),
            )
        except ReleaseRegistryOperationalError:
            self._surface_denied = True
            return "registry_cache_write_failed"
        return reason_code

    def authority(self) -> ReleaseRegistryAuthority:
        envelope = self._accepted
        barrier = self._volatile_accepted or envelope
        current = bool(
            envelope
            and not self._surface_denied
            and self._positive_is_current(envelope)
        )
        revocations: dict[
            tuple[str, str, str, str], ReleaseRevocation
        ] = {}
        for source in self._revocation_sources:
            for item in source.revocations:
                revocations[item.revocation_identity] = item
        for item in self._volatile_revocations:
            revocations[item.revocation_identity] = item
        if envelope is not None:
            for item in envelope.revocations:
                revocations[item.revocation_identity] = item
        return ReleaseRegistryAuthority(
            envelope=envelope,
            positive_authority_current=current,
            revocations=tuple(
                revocations[key] for key in sorted(revocations)
            ),
            sequence=barrier.sequence if barrier else None,
            content_digest=(barrier.content_digest if barrier else None),
            surface_denied=self._surface_denied,
        )

    def snapshot(self) -> dict[str, Any]:
        authority = self.authority()
        return {
            "enabled": self._enabled,
            "registry_id_status": (
                "accepted" if authority.envelope is not None else "unavailable"
            ),
            "sequence": authority.sequence,
            "freshness_status": (
                "current"
                if authority.positive_authority_current
                else "denial_only"
                if authority.revocations or authority.surface_denied
                else "unavailable"
            ),
            "refresh_status": self._last_refresh_status,
            "cache_status": self._cache_status,
            "last_failure_reason": self._last_failure_reason,
            "failure_reason_counts": [
                {"reason_code": reason, "count": count}
                for reason, count in sorted(self._failure_counts.items())[
                    :MAX_FAILURE_REASONS
                ]
            ],
            "retained_revocation_count": len(authority.revocations),
            "retained_revocation_source_count": len(
                self._revocation_sources
            ),
            "registry_location": "fixed_repository_https",
            "surface_denied": authority.surface_denied,
        }

    async def _fetch_bytes(self, url: str, maximum: int) -> bytes:
        if url != REGISTRY_URL or not url.startswith("https://"):
            raise ReleaseRegistryOperationalError(
                "registry_location_rejected"
            )
        timeout = aiohttp.ClientTimeout(
            total=TOTAL_TIMEOUT_SECONDS,
            connect=CONNECT_TIMEOUT_SECONDS,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ReleaseRegistryOperationalError(
                        "registry_unavailable"
                    )
                if (
                    response.content_length is not None
                    and response.content_length > maximum
                ):
                    raise ReleaseRegistryOperationalError(
                        RegistryErrorCode.ENVELOPE_OVERSIZED.value
                    )
                chunks: list[bytes] = []
                size = 0
                while size <= maximum:
                    chunk = await response.content.read(
                        min(65_536, maximum + 1 - size)
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                raw = b"".join(chunks)
                if len(raw) > maximum:
                    raise ReleaseRegistryOperationalError(
                        RegistryErrorCode.ENVELOPE_OVERSIZED.value
                    )
                return raw

    def _positive_is_current(self, envelope: RegistryEnvelope) -> bool:
        try:
            now = self.evaluated_at()
        except ReleaseRegistryOperationalError:
            return False
        generated = parse_utc_timestamp(envelope.generated_at)
        expires = parse_utc_timestamp(envelope.expires_at)
        return generated.timestamp() - now.timestamp() <= 300 and now < expires

    def _require_current_tip(self, tip: RegistryEnvelope) -> None:
        accepted_state = None
        if tip.sequence > 1:
            assert tip.previous_registry_sha256 is not None
            accepted_state = AcceptedRegistryState(
                registry_id=tip.registry_id,
                sequence=tip.sequence - 1,
                content_digest=tip.previous_registry_sha256,
            )
        result = validate_registry_envelope(
            canonical_json(tip.to_mapping()),
            trust_anchors=self._anchors,
            now=self.evaluated_at(),
            accepted_state=accepted_state,
        )
        if not result.accepted:
            code = (
                result.issues[0].code.value
                if len(result.issues) == 1
                else "registry_validation_failed"
            )
            raise ReleaseRegistryOperationalError(code)

    def _validate_candidate_journal(
        self,
        journal: SignedRegistryJournal,
    ) -> str:
        candidate = journal.accepted
        barrier = self._volatile_accepted or self._accepted
        if barrier is None:
            return "accepted_checkpoint"
        if candidate.sequence < barrier.sequence:
            raise ReleaseRegistryOperationalError(
                RegistryErrorCode.ROLLBACK.value
            )
        if candidate.sequence == barrier.sequence:
            if candidate.content_digest != barrier.content_digest:
                raise ReleaseRegistryOperationalError(
                    RegistryErrorCode.REPLAY_CONFLICT.value
                )
            if (
                self._volatile_accepted is None
                and self._authority_journal is not None
                and journal.content_digest
                != self._authority_journal.content_digest
            ):
                raise ReleaseRegistryOperationalError(
                    RegistryErrorCode.REPLAY_CONFLICT.value
                )
            if self._retired_cache_incomplete:
                raise ReleaseRegistryOperationalError(
                    "registry_journal_disconnected"
                )
            return "idempotent"

        first = journal.envelopes[0]
        matching = next(
            (
                index
                for index, item in enumerate(journal.envelopes)
                if item.sequence == barrier.sequence
            ),
            None,
        )
        if matching is not None:
            if (
                journal.envelopes[matching].content_digest
                != barrier.content_digest
            ):
                raise ReleaseRegistryOperationalError(
                    RegistryErrorCode.REPLAY_CONFLICT.value
                )
            if matching == len(journal.envelopes) - 1:
                raise ReleaseRegistryOperationalError(
                    "registry_journal_disconnected"
                )
        elif barrier.sequence >= first.sequence:
            raise ReleaseRegistryOperationalError(
                "registry_journal_disconnected"
            )
        elif (
            first.sequence == barrier.sequence + 1
            and first.previous_registry_sha256
            != barrier.content_digest
        ):
            raise ReleaseRegistryOperationalError(
                RegistryErrorCode.PREVIOUS_DIGEST_MISMATCH.value
            )
        # A larger gap is an explicit checkpoint/compaction authorized by the
        # journal signature. Retained denial evidence is checked separately.
        if self._retired_cache_incomplete:
            forward = (
                journal.envelopes[matching + 1 :]
                if matching is not None
                else journal.envelopes
            )
            expected_sequence = barrier.sequence + 1
            if not forward or any(
                item.sequence != expected_sequence + offset
                for offset, item in enumerate(forward)
            ):
                raise ReleaseRegistryOperationalError(
                    "registry_journal_disconnected"
                )
        return (
            "accepted_compaction"
            if first.sequence > barrier.sequence + 1
            else "accepted"
        )

    def _normalized_revocation_sources(
        self,
        journal: SignedRegistryJournal,
    ) -> tuple[RegistryEnvelope, ...]:
        if self._volatile_revocation_overflow:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        candidates = self._sources_in_journal(journal)
        candidate_revocations = {
            item.revocation_identity
            for source in candidates
            for item in source.revocations
        }
        retained = {
            item.revocation_identity
            for source in self._revocation_sources
            for item in source.revocations
        }
        if self._accepted is not None:
            retained.update(
                item.revocation_identity
                for item in self._accepted.revocations
            )
        retained.update(
            item.revocation_identity
            for item in self._volatile_revocations
        )
        if not retained <= candidate_revocations:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_missing"
            )
        return self._minimal_revocation_sources(candidates)

    @staticmethod
    def _sources_in_journal(
        journal: SignedRegistryJournal,
    ) -> tuple[RegistryEnvelope, ...]:
        return tuple(
            item
            for item in journal.revocation_sources + journal.envelopes
            if item.revocations
        )

    @staticmethod
    def _minimal_revocation_sources(
        candidates: tuple[RegistryEnvelope, ...],
    ) -> tuple[RegistryEnvelope, ...]:
        latest_by_identity: dict[
            tuple[str, str, str, str], RegistryEnvelope
        ] = {}
        for source in sorted(
            candidates,
            key=lambda item: (item.sequence, item.content_digest),
        ):
            for item in source.revocations:
                latest_by_identity[item.revocation_identity] = source
        selected = {
            item.content_digest: item
            for item in latest_by_identity.values()
        }
        result = tuple(
            selected[key] for key in sorted(selected)
        )
        if len(result) > MAX_REVOCATION_SOURCE_ENVELOPES:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        return result

    def _retain_volatile_candidate(
        self,
        journal: SignedRegistryJournal,
        sources: tuple[RegistryEnvelope, ...],
    ) -> None:
        del sources
        self._volatile_accepted = journal.accepted
        self._volatile_journal = journal
        if self._volatile_revocation_overflow:
            self._surface_denied = True
            return
        try:
            self._volatile_denial_journals = self._denial_journals_with(
                journal
            )
            self._volatile_revocations = self._bounded_denial_revocations(
                self._volatile_denial_journals
            )
            self._volatile_revocation_overflow = False
        except ReleaseRegistryOperationalError:
            self._volatile_revocations = ()
            self._volatile_revocation_overflow = True
        self._surface_denied = True

    def _retain_verified_denial(
        self,
        journal: SignedRegistryJournal,
    ) -> None:
        """Retain a verified candidate as a bounded denial-only barrier."""

        self._volatile_accepted = journal.accepted
        self._volatile_journal = journal
        if self._volatile_revocation_overflow:
            self._surface_denied = True
            return
        try:
            self._volatile_denial_journals = self._denial_journals_with(
                journal
            )
            self._volatile_revocations = self._bounded_denial_revocations(
                self._volatile_denial_journals
            )
            self._volatile_revocation_overflow = False
        except ReleaseRegistryOperationalError:
            self._volatile_revocations = ()
            self._volatile_revocation_overflow = True
            self._surface_denied = True
            return
        self._surface_denied = True

    def _denial_journals_with(
        self,
        journal: SignedRegistryJournal,
    ) -> tuple[SignedRegistryJournal, ...]:
        candidates = list(self._volatile_denial_journals)
        if self._authority_journal is not None:
            candidates.append(self._authority_journal)
        candidates.append(journal)
        return self._minimal_denial_journals(tuple(candidates))

    @classmethod
    def _minimal_denial_journals(
        cls,
        candidates: tuple[SignedRegistryJournal, ...],
    ) -> tuple[SignedRegistryJournal, ...]:
        latest_by_identity: dict[
            tuple[str, str, str, str], SignedRegistryJournal
        ] = {}
        for journal in sorted(
            candidates,
            key=lambda item: (
                item.accepted.sequence,
                item.accepted.content_digest,
            ),
        ):
            for source in cls._sources_in_journal(journal):
                for revocation in source.revocations:
                    latest_by_identity[
                        revocation.revocation_identity
                    ] = journal
        selected = {
            item.content_digest: item
            for item in latest_by_identity.values()
        }
        result = tuple(selected[key] for key in sorted(selected))
        if len(result) > MAX_DENIAL_JOURNALS:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        return result

    @staticmethod
    def _missing_release_key(
        server_name: str,
        version: str,
    ) -> tuple[str, str] | None:
        if (
            not isinstance(server_name, str)
            or not isinstance(version, str)
            or not 1 <= len(server_name) <= 64
            or not 1 <= len(version) <= 64
        ):
            return None
        return server_name, version

    def _load_cache(self) -> None:
        try:
            if self._pending_cache_path().exists():
                if self._load_committed_main_cache():
                    return
                self._load_interrupted_cache_transaction()
                return
            journal, sources = self._parse_cache_document(
                self._cache_path.read_bytes()
            )
            self._install_cache_journal(journal, sources)
            try:
                state, authority_digest = self._parse_lifecycle_witness()
            except Exception:
                self._surface_denied = True
                self._retired_cache_incomplete = True
                self._cache_status = "invalid"
                self._record_failure(
                    "registry_cache_incomplete_transaction"
                )
                return
            if (
                state != "committed"
                or authority_digest != journal.content_digest
            ):
                self._surface_denied = True
                self._retired_cache_incomplete = True
                self._cache_status = "invalid"
                self._record_failure(
                    "registry_cache_incomplete_transaction"
                )
                return
            self._cache_status = (
                "valid"
                if self._positive_is_current(journal.accepted)
                else "denial_only"
            )
        except FileNotFoundError:
            if self._previous_cache_path().exists():
                self._load_retired_cache()
            elif self._lifecycle_witness_path().exists():
                try:
                    state, authority_digest = (
                        self._parse_lifecycle_witness()
                    )
                except Exception:
                    state = "invalid"
                    authority_digest = ""
                if (
                    state == "committed"
                    and authority_digest
                    == _NO_SIGNED_AUTHORITY_SHA256
                ):
                    self._cache_status = "missing"
                else:
                    # A witness for a signed journal without its cache, or
                    # any interrupted/invalid bootstrap witness, is durable
                    # evidence that this is not a clean first start.
                    self._surface_denied = True
                    self._retired_cache_incomplete = True
                    self._cache_status = "invalid"
                    self._record_failure(
                        "registry_cache_incomplete_transaction"
                    )
            else:
                self._cache_status = "missing"
        except Exception:
            self._accepted = None
            self._authority_journal = None
            self._authority_chain = ()
            self._revocation_sources = ()
            self._surface_denied = True
            self._cache_status = "invalid"
            self._record_failure("registry_cache_invalid")

    def _load_committed_main_cache(self) -> bool:
        """Accept only an exact committed pending cleanup residue."""

        try:
            journal, sources = self._parse_cache_document(
                self._cache_path.read_bytes()
            )
            state, authority_digest = self._parse_lifecycle_witness()
            pending, retained = self._parse_pending_document(
                self._pending_cache_path().read_bytes()
            )
        except Exception:
            return False
        if (
            state != "committed"
            or authority_digest != journal.content_digest
            or pending.to_mapping() != journal.to_mapping()
            or not self._retained_denials_match_committed_journal(
                journal,
                retained,
            )
        ):
            return False
        self._install_cache_journal(journal, sources)
        self._cache_status = (
            "valid"
            if self._positive_is_current(journal.accepted)
            else "denial_only"
        )
        return True

    @classmethod
    def _retained_denials_match_committed_journal(
        cls,
        committed: SignedRegistryJournal,
        retained: tuple[SignedRegistryJournal, ...],
    ) -> bool:
        """Require cleanup residue to add no uncommitted denial evidence."""

        committed_revocations = {
            item.revocation_identity: item
            for source in cls._sources_in_journal(committed)
            for item in source.revocations
        }
        seen: dict[
            tuple[str, str, str, str], ReleaseRevocation
        ] = {}
        for journal in retained:
            sources = cls._sources_in_journal(journal)
            if not sources:
                return False
            for source in sources:
                for item in source.revocations:
                    identity = item.revocation_identity
                    if (
                        identity in seen
                        and seen[identity] != item
                    ):
                        return False
                    seen[identity] = item
                    if committed_revocations.get(identity) != item:
                        return False
        return True

    def _install_cache_journal(
        self,
        journal: SignedRegistryJournal,
        sources: tuple[RegistryEnvelope, ...],
    ) -> None:
        self._accepted = journal.accepted
        self._authority_journal = journal
        self._authority_chain = journal.envelopes
        self._revocation_sources = sources

    def _parse_lifecycle_witness(self) -> tuple[str, str]:
        raw = self._lifecycle_witness_path().read_bytes()
        if len(raw) > LIFECYCLE_WITNESS_MAX_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_invalid"
            )
        value = _strict_json_document(raw, "registry_cache_invalid")
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "state",
            "authority_journal_sha256",
        }:
            raise ReleaseRegistryOperationalError(
                "registry_cache_invalid"
            )
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"]
            != LIFECYCLE_WITNESS_SCHEMA_VERSION
            or value["state"] not in {"committed", "refreshing"}
            or not isinstance(value["authority_journal_sha256"], str)
            or not _SHA256_DIGEST.fullmatch(
                value["authority_journal_sha256"]
            )
        ):
            raise ReleaseRegistryOperationalError(
                "registry_cache_invalid"
            )
        return value["state"], value["authority_journal_sha256"]

    def _parse_cache_document(
        self,
        raw: bytes,
    ) -> tuple[
        SignedRegistryJournal,
        tuple[RegistryEnvelope, ...],
    ]:
        if len(raw) > MAX_CACHE_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_oversized"
            )
        value = _strict_json_document(raw, "registry_cache_invalid")
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "authority_journal",
        }:
            raise ReleaseRegistryOperationalError("registry_cache_invalid")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != CACHE_SCHEMA_VERSION
        ):
            raise ReleaseRegistryOperationalError(
                "registry_cache_schema_unsupported"
            )
        journal = _parse_signed_journal(
            canonical_json(value["authority_journal"]),
            trust_anchors=self._anchors,
        )
        sources = self._minimal_revocation_sources(
            self._sources_in_journal(journal)
        )
        return journal, sources

    def _load_interrupted_cache_transaction(self) -> None:
        self._surface_denied = True
        self._retired_cache_incomplete = True
        candidate_journal: SignedRegistryJournal | None = None
        retained_denial_journals: tuple[
            SignedRegistryJournal, ...
        ] = ()
        candidate_revocations: tuple[ReleaseRevocation, ...] = ()
        try:
            (
                candidate_journal,
                retained_denial_journals,
            ) = self._parse_pending_document(
                self._pending_cache_path().read_bytes()
            )
        except Exception:
            pass

        base_journal: SignedRegistryJournal | None = None
        base_sources: tuple[RegistryEnvelope, ...] = ()
        for path in (
            self._previous_cache_path(),
            self._cache_path,
        ):
            try:
                base_journal, base_sources = self._parse_cache_document(
                    path.read_bytes()
                )
                break
            except Exception:
                continue
        if base_journal is None:
            self._accepted = None
            self._authority_journal = None
            self._authority_chain = ()
            self._revocation_sources = ()
        else:
            self._install_cache_journal(base_journal, base_sources)

        topology_ambiguous = candidate_journal is None
        if candidate_journal is not None:
            self._volatile_accepted = candidate_journal.accepted
            self._volatile_journal = candidate_journal
            try:
                self._volatile_denial_journals = (
                    self._minimal_denial_journals(
                        retained_denial_journals
                        + (candidate_journal,)
                        + ((base_journal,) if base_journal else ())
                    )
                )
                candidate_revocations = self._bounded_denial_revocations(
                    self._volatile_denial_journals
                )
            except ReleaseRegistryOperationalError:
                topology_ambiguous = True
            if base_journal is not None and not (
                self._pending_advances_base(
                    candidate_journal,
                    base_journal,
                )
            ):
                topology_ambiguous = True
                if (
                    base_journal.accepted.sequence
                    >= candidate_journal.accepted.sequence
                ):
                    self._volatile_accepted = base_journal.accepted
                    self._volatile_journal = base_journal
            if not topology_ambiguous:
                self._retired_cache_incomplete = False

        retained = {
            item.revocation_identity: item
            for item in candidate_revocations
        }
        self._volatile_revocations = tuple(
            retained[key] for key in sorted(retained)
        )
        if self._retired_cache_incomplete:
            self._volatile_revocation_overflow = True
        self._cache_status = "invalid"
        self._record_failure("registry_cache_incomplete_transaction")

    @staticmethod
    def _pending_advances_base(
        candidate: SignedRegistryJournal,
        base: SignedRegistryJournal,
    ) -> bool:
        """Require a pending tip to be newer and linked to its base."""

        candidate_tip = candidate.accepted
        base_tip = base.accepted
        if candidate_tip.sequence <= base_tip.sequence:
            return False
        matching = next(
            (
                index
                for index, envelope in enumerate(candidate.envelopes)
                if envelope.sequence == base_tip.sequence
            ),
            None,
        )
        if matching is not None:
            return bool(
                candidate.envelopes[matching].content_digest
                == base_tip.content_digest
                and matching < len(candidate.envelopes) - 1
            )
        first = candidate.envelopes[0]
        if first.sequence <= base_tip.sequence:
            return False
        if first.sequence == base_tip.sequence + 1:
            return (
                first.previous_registry_sha256
                == base_tip.content_digest
            )
        return True

    def _load_retired_cache(self) -> None:
        """Treat a retirement-first cache as denial-only after restart."""

        self._surface_denied = True
        self._retired_cache_incomplete = True
        self._volatile_revocation_overflow = True
        try:
            journal, sources = self._parse_cache_document(
                self._previous_cache_path().read_bytes()
            )
            self._accepted = journal.accepted
            self._authority_journal = journal
            self._authority_chain = journal.envelopes
            self._revocation_sources = sources
        except Exception:
            self._accepted = None
            self._authority_journal = None
            self._authority_chain = ()
            self._revocation_sources = ()
        self._cache_status = "invalid"
        self._record_failure("registry_cache_incomplete_transaction")

    @classmethod
    def _bounded_denial_revocations(
        cls,
        journals: tuple[SignedRegistryJournal, ...],
    ) -> tuple[ReleaseRevocation, ...]:
        """Flatten signed denial evidence without the positive-cache bound."""

        retained = {
            item.revocation_identity: item
            for journal in journals
            for source in cls._sources_in_journal(journal)
            for item in source.revocations
        }
        if len(retained) > MAX_REVOCATIONS:
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        return tuple(retained[key] for key in sorted(retained))

    def _parse_pending_document(
        self,
        raw: bytes,
    ) -> tuple[
        SignedRegistryJournal,
        tuple[SignedRegistryJournal, ...],
    ]:
        if len(raw) > MAX_CACHE_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_oversized"
            )
        value = _strict_json_document(raw, "registry_cache_invalid")
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "candidate_journal",
            "retained_denial_journals",
        }:
            raise ReleaseRegistryOperationalError("registry_cache_invalid")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != PENDING_SCHEMA_VERSION
        ):
            raise ReleaseRegistryOperationalError(
                "registry_cache_schema_unsupported"
            )
        candidate = _parse_signed_journal(
            canonical_json(value["candidate_journal"]),
            trust_anchors=self._anchors,
        )
        raw_denial_journals = value["retained_denial_journals"]
        if (
            not isinstance(raw_denial_journals, list)
            or len(raw_denial_journals) > MAX_DENIAL_JOURNALS
        ):
            raise ReleaseRegistryOperationalError(
                "registry_revocation_history_capacity_exhausted"
            )
        retained = tuple(
            _parse_signed_journal(
                canonical_json(item),
                trust_anchors=self._anchors,
            )
            for item in raw_denial_journals
        )
        seen_digests = {candidate.content_digest}
        accepted_by_sequence = {
            candidate.accepted.sequence: candidate.accepted.content_digest
        }
        for journal in retained:
            accepted = journal.accepted
            if (
                accepted.sequence > candidate.accepted.sequence
                or journal.content_digest in seen_digests
                or (
                    accepted.sequence in accepted_by_sequence
                    and accepted_by_sequence[accepted.sequence]
                    != accepted.content_digest
                )
            ):
                raise ReleaseRegistryOperationalError(
                    "registry_cache_invalid"
                )
            seen_digests.add(journal.content_digest)
            accepted_by_sequence[
                accepted.sequence
            ] = accepted.content_digest
        return candidate, retained

    def _write_pending_barrier(
        self,
        journal: SignedRegistryJournal,
        *,
        parent: Path,
        denial_journals: tuple[SignedRegistryJournal, ...],
    ) -> Path:
        """Durably retain a verified candidate as restart denial evidence."""

        marker_encoded = canonical_json(
            {
                "schema_version": PENDING_SCHEMA_VERSION,
                "candidate_journal": journal.to_mapping(),
                "retained_denial_journals": [
                    item.to_mapping()
                    for item in denial_journals
                    if item.content_digest != journal.content_digest
                ],
            }
        )
        if len(marker_encoded) > MAX_CACHE_BYTES:
            raise ReleaseRegistryOperationalError("registry_cache_oversized")
        marker_temporary: Path | None = None
        pending = self._pending_cache_path()
        try:
            marker_handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self._cache_path.name}.pending.",
                dir=parent,
                delete=False,
            )
            marker_temporary = Path(marker_handle.name)
            with marker_handle:
                marker_handle.write(marker_encoded)
                marker_handle.flush()
                os.fsync(marker_handle.fileno())
            os.replace(marker_temporary, pending)
            self._fsync_directory(parent)
        except OSError as exc:
            raise ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ) from exc
        finally:
            if marker_temporary is not None:
                try:
                    marker_temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return parent

    def _retire_cached_positive_authority(self) -> Path:
        """Durably remove the active cache before writing a denial marker."""

        parent = self._cache_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if self._cache_path.exists():
                os.replace(
                    self._cache_path,
                    self._previous_cache_path(),
                )
                self._fsync_directory(parent)
        except OSError as exc:
            raise ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ) from exc
        return parent

    def _persist_denial_barrier(
        self,
        journal: SignedRegistryJournal,
        *,
        denial_journals: tuple[SignedRegistryJournal, ...],
    ) -> None:
        """Persist denial even when retiring the positive cache fails."""

        try:
            parent = self._retire_cached_positive_authority()
        except ReleaseRegistryOperationalError as retirement_error:
            try:
                self._write_pending_barrier(
                    journal,
                    parent=self._cache_path.parent,
                    denial_journals=denial_journals,
                )
            except ReleaseRegistryOperationalError:
                try:
                    self._retire_cached_positive_authority()
                except ReleaseRegistryOperationalError:
                    raise retirement_error
            return
        self._write_pending_barrier(
            journal,
            parent=parent,
            denial_journals=denial_journals,
        )

    def _write_cache(
        self,
        journal: SignedRegistryJournal,
    ) -> None:
        encoded = canonical_json(
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "authority_journal": journal.to_mapping(),
            }
        )
        if len(encoded) > MAX_CACHE_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_oversized"
            )
        denial_journals = self._denial_journals_with(journal)
        parent = self._retire_cached_positive_authority()
        self._write_pending_barrier(
            journal,
            parent=parent,
            denial_journals=denial_journals,
        )
        handle = None
        temporary: Path | None = None
        pending = self._pending_cache_path()
        previous_path = self._previous_cache_path()
        try:
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self._cache_path.name}.",
                dir=parent,
                delete=False,
            )
            temporary = Path(handle.name)
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._cache_path)
            self._fsync_directory(parent)
            self._write_lifecycle_witness(
                state="committed",
                authority_journal_sha256=journal.content_digest,
            )
        except OSError as exc:
            raise ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        # The candidate and its directory entry are durable at this point.
        # Cleanup is deliberately best effort: a retained pending marker makes
        # restart deny positive authority rather than guessing that a failed
        # transaction committed.
        try:
            pending.unlink(missing_ok=True)
            previous_path.unlink(missing_ok=True)
            self._fsync_directory(parent)
        except OSError:
            pass

    def _write_lifecycle_witness(
        self,
        *,
        state: str,
        authority_journal_sha256: str,
    ) -> None:
        """Persist refresh intent before a newer registry can be observed."""

        if (
            state not in {"committed", "refreshing"}
            or not isinstance(authority_journal_sha256, str)
            or not _SHA256_DIGEST.fullmatch(authority_journal_sha256)
        ):
            raise ReleaseRegistryOperationalError(
                "registry_cache_invalid"
            )
        encoded = canonical_json(
            {
                "schema_version": LIFECYCLE_WITNESS_SCHEMA_VERSION,
                "state": state,
                "authority_journal_sha256": authority_journal_sha256,
            }
        )
        if len(encoded) > LIFECYCLE_WITNESS_MAX_BYTES:
            raise ReleaseRegistryOperationalError(
                "registry_cache_oversized"
            )
        path = self._lifecycle_witness_path()
        parent = path.parent
        temporary: Path | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(encoded)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                return
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self._cache_path.name}.lifecycle.",
                dir=parent,
                delete=False,
            )
            temporary = Path(handle.name)
            with handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(parent)
        except OSError as exc:
            raise ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _pending_cache_path(self) -> Path:
        return self._cache_path.with_name(self._cache_path.name + ".pending")

    def _previous_cache_path(self) -> Path:
        return self._cache_path.with_name(self._cache_path.name + ".previous")

    def _lifecycle_witness_path(self) -> Path:
        return self._cache_path.with_name(
            self._cache_path.name + ".lifecycle"
        )

    @staticmethod
    def _fsync_directory(parent: Path) -> None:
        directory = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _record_failure(self, reason: str) -> None:
        safe = (
            reason
            if isinstance(reason, str)
            and 1 <= len(reason) <= 96
            and all(
                character.islower()
                or character.isdigit()
                or character == "_"
                for character in reason
            )
            else "registry_invalid"
        )
        self._last_refresh_status = "failed"
        self._last_failure_reason = safe
        if safe in self._failure_counts or len(
            self._failure_counts
        ) < MAX_FAILURE_REASONS:
            self._failure_counts[safe] = min(
                MAX_SAFE_INTEGER,
                self._failure_counts[safe] + 1,
            )


def _strict_json_document(raw: bytes, reason_code: str) -> Any:
    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseRegistryOperationalError(
                    reason_code
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ReleaseRegistryOperationalError(reason_code)

    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ReleaseRegistryOperationalError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ReleaseRegistryOperationalError(
            reason_code
        ) from None
